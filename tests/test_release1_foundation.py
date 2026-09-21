"""IQVIA Release 1 foundation — matching rules, source authority, snapshot
approval gate, maker/checker, licensed-data access, log redaction.

No IQVIA content exists in the repository; every value here is synthetic.
The licensed observation tables are a proposal (ADR-006) and are NOT
exercised — nothing to exercise them with.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.logging_config import redact
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import source_matching as sx
from rce_traceability_support import (  # noqa: F401
    NPI_BAD_CHECKSUM, NPI_REGISTERED, SYN, rolled_back_db, seed_entity,
)


class _User:
    def __init__(self, role, email):
        self.role, self.email, self.id = role, email, None


# ── source authority ─────────────────────────────────────────────────────────

def test_observation_sources_cannot_assert_tefca_facts():
    for src in ("IQVIA_HCO", "IQVIA_HCP", "IQVIA_AFFILIATION", "NPPES", "CMS_CCN"):
        with pytest.raises(sx.SourceAuthorityViolation):
            sx.assert_not_tefca_fact(src, ["part_of"])
        with pytest.raises(sx.SourceAuthorityViolation):
            sx.assert_not_tefca_fact(src, ["org_managing_org", "is_active"])
        sx.assert_not_tefca_fact(src, ["observed_name", "observed_phone"])   # fine
    sx.assert_not_tefca_fact("ONC_RCE", ["part_of", "is_active"])            # the one source


# ── matching rules ───────────────────────────────────────────────────────────

NPPES_T2 = {"enumeration_type": "NPI-2"}
NPPES_T1 = {"enumeration_type": "NPI-1"}


def test_auto_match_only_for_unique_valid_type2_npi():
    e = uuid.uuid4()
    d = sx.evaluate_npi_match(source_npi=NPI_REGISTERED, nppes_evidence=NPPES_T2,
                              registry_entities_with_npi=[e], source_record_key="HCO-1")
    assert d["status"] == "AUTO_APPROVED" and d["method"] == "NPI_EXACT_TYPE2"
    assert d["entity_id"] == str(e) and d["confidence"] == 1.0


def test_type1_npi_is_an_exception_not_a_match():
    d = sx.evaluate_npi_match(source_npi=NPI_REGISTERED, nppes_evidence=NPPES_T1,
                              registry_entities_with_npi=[uuid.uuid4()], source_record_key="HCO-1")
    assert d["status"] == "EXCEPTION"


def test_ambiguous_npi_is_an_exception():
    d = sx.evaluate_npi_match(source_npi=NPI_REGISTERED, nppes_evidence=NPPES_T2,
                              registry_entities_with_npi=[uuid.uuid4(), uuid.uuid4()],
                              source_record_key="HCO-1")
    assert d["status"] == "EXCEPTION" and "2 registry entities" in d["reason"]


def test_invalid_npi_is_an_exception_and_unknown_type_is_candidate():
    d = sx.evaluate_npi_match(source_npi=NPI_BAD_CHECKSUM, nppes_evidence=NPPES_T2,
                              registry_entities_with_npi=[uuid.uuid4()], source_record_key="k")
    assert d["status"] == "EXCEPTION"
    d = sx.evaluate_npi_match(source_npi=NPI_REGISTERED, nppes_evidence=None,
                              registry_entities_with_npi=[uuid.uuid4()], source_record_key="k")
    assert d["status"] == "CANDIDATE"
    d = sx.evaluate_npi_match(source_npi="", nppes_evidence=NPPES_T2,
                              registry_entities_with_npi=[], source_record_key="k")
    assert d["status"] == "CANDIDATE"


def test_ccn_and_descriptive_methods_are_candidate_only():
    d = sx.evaluate_ccn_candidate(source_ccn="123456", source_record_key="k",
                                  registry_entities_with_ccn=[uuid.uuid4()])
    assert d["status"] == "CANDIDATE" and d["method"] == "CCN_CANDIDATE"
    for method in ("EXACT_NAME_ADDRESS_PHONE", "FUZZY_DISCOVERY"):
        d = sx.evaluate_descriptive_candidate(method=method, source_record_key="k",
                                              candidates=[uuid.uuid4()], score=0.99)
        assert d["status"] == "CANDIDATE"
    with pytest.raises(ValueError):
        sx.evaluate_descriptive_candidate(method="NPI_EXACT_TYPE2", source_record_key="k",
                                          candidates=[], score=None)


# ── access ───────────────────────────────────────────────────────────────────

def test_licensed_access_requires_flag_and_reviewer_floor(monkeypatch):
    monkeypatch.delenv(sx.LICENSED_SOURCE_FLAG, raising=False)
    from app.core.config import settings
    monkeypatch.setattr(settings, sx.LICENSED_SOURCE_FLAG, None, raising=False)
    assert sx.licensed_access_allowed(_User("admin", "a@x"))["availability"] == "not_configured"
    monkeypatch.setenv(sx.LICENSED_SOURCE_FLAG, "true")
    assert sx.licensed_access_allowed(_User("viewer", "v@x"))["availability"] == "requires_role:reviewer"
    assert sx.licensed_access_allowed(_User("contributor", "c@x"))["allowed"] is False
    assert sx.licensed_access_allowed(_User("reviewer", "r@x"))["allowed"] is True
    assert sx.licensed_access_allowed(_User("qalead", "q@x"))["allowed"] is True


def test_log_redaction_covers_licensed_keys():
    out = redact({"iqvia_hco_id": "X1", "OneKey": "Y", "hcp_name": "Dr Z", "hcp": "p",
                  "licensed_payload": {"a": 1}, "name": "kept", "hcpcs_code": "kept"})
    assert out["iqvia_hco_id"] == "[REDACTED]" and out["OneKey"] == "[REDACTED]"
    assert out["hcp_name"] == "[REDACTED]" and out["hcp"] == "[REDACTED]"
    assert out["licensed_payload"] == "[REDACTED]"
    assert out["name"] == "kept" and out["hcpcs_code"] == "kept"


# ── persistence: approval gate, maker/checker, CHECKs ────────────────────────

@pytest.mark.asyncio
async def test_snapshot_approval_is_append_only_and_gated(rolled_back_db):
    db = rolled_back_db
    now = datetime.now(timezone.utc)
    received = await sx.register_snapshot(
        db, source_system="IQVIA_HCO", label=f"{SYN}-HCO-2026-09", sha256="a" * 64,
        record_count=0, received_at=now, created_by="dataops@x")
    assert received.status == "RECEIVED"
    assert await sx.current_approved_snapshot(db, "IQVIA_HCO") is None

    with pytest.raises(PermissionError):
        await sx.approve_snapshot(db, received.id, user=_User("senior_analyst", "s@x"),
                                  approval_ref="DUA-1")
    with pytest.raises(PermissionError):   # registrant cannot approve their own
        await sx.approve_snapshot(db, received.id, user=_User("qalead", "dataops@x"),
                                  approval_ref="DUA-1")
    approved = await sx.approve_snapshot(db, received.id, user=_User("qalead", "qa@x"),
                                         approval_ref="DUA-1")
    assert approved.status == "APPROVED" and approved.supersedes_snapshot_id == received.id
    await db.refresh(received)
    assert received.status == "RECEIVED"          # never edited
    current = await sx.current_approved_snapshot(db, "IQVIA_HCO")
    assert current is not None and current.id == approved.id
    with pytest.raises(ValueError):                # cannot approve twice
        await sx.approve_snapshot(db, received.id, user=_User("qalead", "qa@x"),
                                  approval_ref="DUA-1")


@pytest.mark.asyncio
async def test_match_rows_honour_auto_only_npi_and_maker_checker(rolled_back_db):
    db = rolled_back_db
    now = datetime.now(timezone.utc)
    entity = await seed_entity(db, oid="9.99.777.94.1", name=f"{SYN} R1 ORG", npi=NPI_REGISTERED)
    snap = await sx.register_snapshot(
        db, source_system="IQVIA_HCO", label=f"{SYN}-HCO", sha256="b" * 64,
        record_count=1, received_at=now, created_by="dataops@x")

    auto = sx.evaluate_npi_match(source_npi=NPI_REGISTERED, nppes_evidence=NPPES_T2,
                                 registry_entities_with_npi=[entity], source_record_key="HCO-1")
    row = await sx.record_match(db, entity_id=entity, source_system="IQVIA_HCO",
                                snapshot_id=snap.id, decision=auto, proposed_by="SYSTEM")
    assert row.match_status == "AUTO_APPROVED"

    ccn = sx.evaluate_ccn_candidate(source_ccn="123456", source_record_key="HCO-2",
                                    registry_entities_with_ccn=[entity])
    with pytest.raises(ValueError):                # code refuses before the DB does
        await sx.record_match(db, entity_id=entity, source_system="IQVIA_HCO",
                              snapshot_id=snap.id,
                              decision=sx.MatchDecision(ccn, status="AUTO_APPROVED"),
                              proposed_by="SYSTEM")
    # and the DB refuses it too (ck_esm_auto_only_npi)
    async with db.begin_nested():
        db.add(sm.EntitySourceMatch(
            id=uuid.uuid4(), entity_id=entity, source_system="IQVIA_HCO",
            source_snapshot_id=snap.id, source_record_key="HCO-2",
            match_method="CCN_CANDIDATE", match_status="AUTO_APPROVED",
            matching_model_version="x", proposed_by="SYSTEM", correlation_id="t"))
        with pytest.raises(IntegrityError):
            await db.flush()

    # analyst determines; QA by the same person is refused; a different QA is fine
    cand = await sx.record_match(db, entity_id=entity, source_system="IQVIA_HCO",
                                 snapshot_id=snap.id,
                                 decision=sx.MatchDecision(ccn, status="ANALYST_APPROVED"),
                                 proposed_by="SYSTEM", reviewed_by="analyst@x")
    with pytest.raises(ValueError):
        await sx.record_match(db, entity_id=entity, source_system="IQVIA_HCO",
                              snapshot_id=snap.id,
                              decision=sx.MatchDecision(ccn, status="QA_APPROVED"),
                              proposed_by="SYSTEM", reviewed_by="analyst@x", qa_by="analyst@x",
                              supersedes_match_id=cand.id)
    async with db.begin_nested():                  # ck_esm_maker_checker
        db.add(sm.EntitySourceMatch(
            id=uuid.uuid4(), entity_id=entity, source_system="IQVIA_HCO",
            source_snapshot_id=snap.id, source_record_key="HCO-2",
            match_method="CCN_CANDIDATE", match_status="QA_APPROVED",
            matching_model_version="x", proposed_by="SYSTEM", reviewed_by="analyst@x",
            qa_by="analyst@x", correlation_id="t"))
        with pytest.raises(IntegrityError):
            await db.flush()
    qa = await sx.record_match(db, entity_id=entity, source_system="IQVIA_HCO",
                               snapshot_id=snap.id,
                               decision=sx.MatchDecision(ccn, status="QA_APPROVED"),
                               proposed_by="SYSTEM", reviewed_by="analyst@x", qa_by="qa@x",
                               supersedes_match_id=cand.id)
    assert qa.match_status == "QA_APPROVED" and qa.supersedes_match_id == cand.id
    rows = (await db.execute(select(sm.EntitySourceMatch)
                             .where(sm.EntitySourceMatch.entity_id == entity))).scalars().all()
    assert len(rows) == 3                          # append-only chain, nothing rewritten
