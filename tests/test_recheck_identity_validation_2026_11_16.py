"""A recheck validates IDENTITY FIRST, before anything is persisted (CodeRabbit on #233, rounds 5-6; owner 2026-11-16).

`_recheck_one` used to persist the entity's dimension evidence, write the ledger row, flip an NPI identifier to
inactive-pending-review and move the entity to `in_review` BEFORE (or without) checking that the queued reference still
resolves to the queued entity and still has exactly one delivered record in the job's delivery. Now ONE validation step
runs first (queued entity == resolved registry entity; exactly one delivered record of the job's delivery carrying the
reference and linking that entity; the delivered NPI read from that record). A refusal persists NOTHING - no evidence,
no ledger row, no identifier-status change, no entity-status change. On success the ledger write uses the delivered
record's context. When the delivered NPI differs from the NPI the evidence was gathered for, the entity-level evidence is
kept but no NPI outcome is written for that delivery.

Deliberately NOT implemented (it contradicts the refusal rule): a CodeRabbit nitpick asked the refusal test to assert that
`record_from_evidence` is awaited with `source_record_id=None`; a refused recheck must not reach outcome recording at all.
Synthetic data only.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.Tefca.models import TEFCADimensionEvidence
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import recheck_models as rm
from app.tefca_registry.rce import verification_findings as vf
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_entity, seed_intake  # noqa: F401
from test_npi_outcome_record_anchoring_2026_11_16 import NPI, T0, _entity_dict, _two_deliveries

pytestmark = pytest.mark.asyncio

OTHER_NPI = "1245319599"


def _deactivated_evidence() -> dict:
    return {"dimensions": [{"dimension": "IDENTITY", "disposition": "REVIEW", "applicability": "APPLICABLE",
                            "evidence": [{"source": "NPPES", "disposition": "REVIEW",
                                          "rule_applied": "NPPES_NPI_DEACTIVATED",
                                          "original_values": {"status": "DEACTIVATED", "npi": NPI},
                                          "note": "NPI deactivated in NPPES."}]}]}


async def _state(db, ent, *recs):
    evidence = (await db.execute(select(TEFCADimensionEvidence).where(
        TEFCADimensionEvidence.entity_id == str(ent)))).scalars().all()
    ident = (await db.execute(select(reg.TefcaEntityIdentifier.identifier_status).where(
        reg.TefcaEntityIdentifier.entity_id == ent, reg.TefcaEntityIdentifier.identifier_type == "npi"))).scalars().all()
    entity_status = (await db.get(reg.TefcaRegEntity, ent)).verification_status
    issues = (await db.execute(select(m.RceIssue).where(
        m.RceIssue.source_record_id.in_([r.id for r in recs])))).scalars().all() if recs else []
    return {"evidence": len(evidence), "identifier": ident, "entity_status": entity_status, "issues": issues}


async def _run(db, monkeypatch, *, ent, intake, ref, resolved, evidence=None):
    from app.tefca_registry.rce import rechecks
    monkeypatch.setattr(rechecks, "evidence_sources_for", lambda _sid: ("NPPES",))
    job = SimpleNamespace(intake_id=intake, pinned={}, source_id="nppes", trigger_ref="x")
    item = SimpleNamespace(entity_ref=ref, entity_id=ent, state=None, outcome=None, new_disposition=None,
                           processed_at=None, detail={})
    real = vf.record_from_evidence
    spy = AsyncMock(side_effect=real)
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=evidence or _deactivated_evidence())), \
            patch.object(vf, "record_from_evidence", new=spy):
        await rechecks._recheck_one(db, job, item, local_store=None)
    return item, spy


def _refused(item):
    assert item.state == rm.ITEM_DONE and item.outcome == rm.OUTCOME_IDENTITY_REFUSED, (item.state, item.outcome)
    assert item.detail.get("reason")


@pytest.mark.parametrize("a_promoted_later", [True, False])
@pytest.mark.parametrize("case", ["another_entity", "no_registry_id", "reference_not_in_this_delivery"])
async def test_a_refused_recheck_persists_nothing(rolled_back_db, monkeypatch, a_promoted_later, case):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    other = await seed_entity(db, oid=f"{SYN}-OTHER-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=OTHER_NPI)
    resolved, ref = _entity_dict(ent), rec_b.source_rce_id
    if case == "another_entity":
        resolved = _entity_dict(other)
    elif case == "no_registry_id":
        resolved.pop("_registry_entity_id")
    else:
        ref = rec_a.source_rce_id                      # delivery A's reference, job is delivery B's: no delivered record
    before = await _state(db, ent, rec_a, rec_b)
    item, spy = await _run(db, monkeypatch, ent=ent, intake=intake_b, ref=ref, resolved=resolved)
    _refused(item)
    after = await _state(db, ent, rec_a, rec_b)
    assert after["evidence"] == before["evidence"] == 0          # no dimension evidence persisted
    assert after["issues"] == [] and after["identifier"] == ["active"]
    assert after["entity_status"] == before["entity_status"] == "not_verified"
    spy.assert_not_awaited()                                      # never reaches outcome recording


async def test_a_duplicate_delivered_record_is_refused_and_persists_nothing(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent = await seed_entity(db, oid=f"{SYN}-DUP-{uuid.uuid4().hex[:8]}", name=f"{SYN} dup entity", npi=NPI)
    rows = make_rows(2, arc="9.99.777.74")
    for r in rows:
        r["NPI"] = NPI
    rows[1]["id"] = rows[0]["id"]                                   # the SAME delivered reference twice in one delivery
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    for r in recs:
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=r.id, record_status="CLEAN",
                                  rce_org_oid=r.source_rce_id, transformation_version="test", canonical_entity_id=ent,
                                  promoted_at=T0))
    await db.flush()
    item, spy = await _run(db, monkeypatch, ent=ent, intake=intake_id, ref=recs[0].source_rce_id, resolved=_entity_dict(ent))
    _refused(item)
    st = await _state(db, ent, *recs)
    assert st["evidence"] == 0 and st["issues"] == [] and st["identifier"] == ["active"] and st["entity_status"] == "not_verified"
    spy.assert_not_awaited()


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_a_successful_recheck_uses_the_delivered_record_context(rolled_back_db, monkeypatch, a_promoted_later):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    item, spy = await _run(db, monkeypatch, ent=ent, intake=intake_b, ref=rec_b.source_rce_id, resolved=_entity_dict(ent))
    assert item.state == rm.ITEM_DONE and item.outcome != rm.OUTCOME_IDENTITY_REFUSED
    st = await _state(db, ent, rec_a, rec_b)
    assert st["evidence"] >= 1                                                   # evidence persisted after validation
    assert [i.source_record_id for i in st["issues"]] == [rec_b.id]              # exactly one ledger row, on delivery B
    assert st["issues"][0].source_intake_id == intake_b and st["issues"][0].issue_type == vf.NPI_DEACTIVATED
    assert st["identifier"] == [vf.INACTIVE_PENDING_REVIEW] and st["entity_status"] == "in_review"
    kw = spy.await_args.kwargs
    assert kw["source_record_id"] == rec_b.id and kw["intake_id"] == intake_b and kw["npi"] == NPI   # the delivered record's NPI
    assert not item.detail.get("npi_outcome_skipped")


async def test_a_delivered_npi_that_differs_from_the_evidence_npi_writes_no_ledger_row_for_that_delivery(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent = await seed_entity(db, oid=f"{SYN}-MISMATCH-{uuid.uuid4().hex[:8]}", name=f"{SYN} shared entity", npi=NPI)
    rows = make_rows(1, arc="9.99.777.75")
    rows[0]["NPI"] = OTHER_NPI                                    # delivery submitted a DIFFERENT NPI than the registry's
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    rec = (await db.execute(select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().one()
    db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id, record_status="CLEAN",
                              rce_org_oid=rec.source_rce_id, transformation_version="test", canonical_entity_id=ent,
                              promoted_at=T0))
    await db.flush()
    item, spy = await _run(db, monkeypatch, ent=ent, intake=intake_id, ref=rec.source_rce_id, resolved=_entity_dict(ent))
    st = await _state(db, ent, rec)
    assert item.state == rm.ITEM_DONE and item.outcome != rm.OUTCOME_IDENTITY_REFUSED
    assert st["issues"] == [] and st["identifier"] == ["active"]                 # nothing recorded for an NPI it did not submit
    assert item.detail.get("npi_outcome_skipped")
    spy.assert_not_awaited()
    assert st["evidence"] >= 1                                                    # the entity-level evidence is still appended
