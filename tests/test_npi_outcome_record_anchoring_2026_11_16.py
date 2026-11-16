"""NPPES outcome ledger writes are anchored to the VERIFIED source record (owner option A, 2026-11-16).

`record_npi_outcome` used to resolve its target with `_curated_for_entity`: the most recently promoted curated record
of the ENTITY across every delivery. An entity promoted by two deliveries therefore received delivery B's NPI-005/006/009
finding on whichever delivery promoted it last. Now the caller names the record it verified; the target is that record's
curated row, validated against the entity (and the delivery when the caller knows it), and the SAME row serves insertion
and duplicate detection. No anchor -> nothing is written, never "the latest record". Synthetic data only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import verification_findings as vf
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_entity, seed_intake  # noqa: F401

pytestmark = pytest.mark.asyncio

NPI = "1234567893"
T0 = datetime(2026, 11, 1, 9, 0, 0)


def _not_found_evidence() -> dict:
    return {"dimensions": [{"dimension": "D1_IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
                            "evidence": [{"source": "NPPES", "disposition": "NOT_FOUND",
                                          "note": "NPI not present in NPPES."}]}]}


async def _delivery(db, arc: str, entity, promoted_at: datetime):
    """One synthetic delivery with ONE curated record linking the shared `entity`."""
    rows = make_rows(1, arc=arc)
    rows[0]["NPI"] = NPI                      # the delivered NPI equals the entity's registered NPI (single-record review check)
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    rec = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().one()
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id, record_status="CLEAN",
        rce_org_oid=rec.source_rce_id, transformation_version="test", canonical_entity_id=entity,
        promoted_at=promoted_at))
    await db.flush()
    return intake_id, rec


async def _two_deliveries(db, *, a_promoted_later: bool):
    ent = await seed_entity(db, oid=f"{SYN}-ANCHOR-{uuid.uuid4().hex[:8]}", name=f"{SYN} shared entity", npi=NPI)
    t_a, t_b = (T0 + timedelta(days=1), T0) if a_promoted_later else (T0, T0 + timedelta(days=1))
    a = await _delivery(db, "9.99.777.71", ent, t_a)
    b = await _delivery(db, "9.99.777.72", ent, t_b)
    return ent, a, b


async def _issues(db, rec):
    return (await db.execute(select(m.RceIssue).where(
        m.RceIssue.source_record_id == rec.id, m.RceIssue.issue_type == vf.NPI_NOT_FOUND))).scalars().all()


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_two_deliveries_sharing_an_entity_never_receive_each_others_findings(rolled_back_db, a_promoted_later):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)

    issue_a = await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND,
                                          source_record_id=rec_a.id, intake_id=intake_a)
    issue_b = await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND,
                                          source_record_id=rec_b.id, intake_id=intake_b)
    assert issue_a is not None and issue_b is not None and issue_a.id != issue_b.id
    assert issue_a.source_record_id == rec_a.id and issue_a.source_intake_id == intake_a
    assert issue_b.source_record_id == rec_b.id and issue_b.source_intake_id == intake_b
    assert len(await _issues(db, rec_a)) == 1 and len(await _issues(db, rec_b)) == 1   # each only its own


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_duplicate_detection_is_per_record_not_per_entity(rolled_back_db, a_promoted_later):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    first = await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND, source_record_id=rec_a.id)
    again = await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND, source_record_id=rec_a.id)
    assert again.id == first.id and len(await _issues(db, rec_a)) == 1          # a repeat on A adds nothing
    other = await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND, source_record_id=rec_b.id)
    assert other is not None and other.id != first.id                           # B is NOT swallowed by A's open issue
    assert len(await _issues(db, rec_a)) == 1 and len(await _issues(db, rec_b)) == 1


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_record_from_evidence_carries_the_anchor_to_the_ledger(rolled_back_db, a_promoted_later):
    db = rolled_back_db
    ent, (_ia, rec_a), (_ib, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    ev = _not_found_evidence()
    issue = await vf.record_from_evidence(db, entity_id=ent, npi=NPI, evidence=ev, source_record_id=rec_b.id)
    assert issue is not None and issue.source_record_id == rec_b.id
    assert await _issues(db, rec_a) == []                                       # the other delivery got nothing


async def test_an_unanchored_call_writes_nothing(rolled_back_db):
    db = rolled_back_db
    ent, (_ia, rec_a), (_ib, rec_b) = await _two_deliveries(db, a_promoted_later=True)
    assert await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND) is None
    assert await vf.record_from_evidence(db, entity_id=ent, npi=NPI, evidence=_not_found_evidence()) is None
    assert await vf.record_from_sources(
        db, entity_id=ent, sources={"nppes": {"npi_outcome": vf.NPI_NOT_FOUND, "lookup_identifier": NPI}}) is None
    assert await _issues(db, rec_a) == [] and await _issues(db, rec_b) == []    # never "the latest record"
    total = (await db.execute(select(m.RceIssue).where(m.RceIssue.issue_type == vf.NPI_NOT_FOUND))).scalars().all()
    assert [i for i in total if i.source_record_id in (rec_a.id, rec_b.id)] == []


async def test_an_anchor_for_a_different_entity_or_delivery_writes_nothing(rolled_back_db):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=True)
    other_ent = await seed_entity(db, oid=f"{SYN}-ANCHOR-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=NPI)
    # the record is real, but its curated row links a DIFFERENT entity
    assert await vf.record_npi_outcome(db, entity_id=other_ent, npi=NPI, outcome=vf.NPI_NOT_FOUND,
                                       source_record_id=rec_a.id) is None
    # the record is real and links the entity, but the caller named another delivery
    assert await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND,
                                       source_record_id=rec_a.id, intake_id=intake_b) is None
    # an anchor that matches no curated record at all
    assert await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_NOT_FOUND,
                                       source_record_id=uuid.uuid4()) is None
    assert await _issues(db, rec_a) == [] and await _issues(db, rec_b) == []


async def test_anchor_for_reference_is_delivery_scoped_and_strict(rolled_back_db):
    """The shared resolver used by the coverage and recheck callers: exactly ONE curated row of the named delivery
    carries the reference AND links the entity; anything else is no anchor."""
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=True)
    assert await vf.anchor_for_reference(db, intake_id=intake_a, ref=rec_a.source_rce_id, entity_id=ent) == rec_a.id
    assert await vf.anchor_for_reference(db, intake_id=intake_b, ref=rec_b.source_rce_id, entity_id=ent) == rec_b.id
    assert await vf.anchor_for_reference(db, intake_id=intake_a, ref=rec_b.source_rce_id, entity_id=ent) is None
    assert await vf.anchor_for_reference(db, intake_id=None, ref=rec_a.source_rce_id, entity_id=ent) is None
    assert await vf.anchor_for_reference(db, intake_id=intake_a, ref=rec_a.source_rce_id, entity_id=uuid.uuid4()) is None
    # two curated rows of the SAME delivery carrying the same delivered reference make it ambiguous
    intake_c, _ = await seed_intake(db, make_rows(2, arc="9.99.777.73"), with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_c))).scalars().all()
    for r in recs:
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_c, source_record_id=r.id, record_status="CLEAN",
            rce_org_oid="DUP-REF", transformation_version="test", canonical_entity_id=ent, promoted_at=T0))
    await db.flush()
    assert await vf.anchor_for_reference(db, intake_id=intake_c, ref="DUP-REF", entity_id=ent) is None


# ── the callers: each passes its OWN delivery's record (real ledger writer, real two-delivery fixture) ──────────────

from unittest.mock import AsyncMock, patch  # noqa: E402


def _entity_dict(ent):
    return {"_registry_entity_id": ent, "id": str(ent), "name": f"{SYN} shared entity",
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": NPI}]}


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_automated_coverage_writes_the_finding_on_its_own_deliverys_record(rolled_back_db, a_promoted_later):
    from app.tefca_registry.rce import automated_verification as av
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_entity_dict(ent))), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=_not_found_evidence())):
        await av.record_coverage_attempt(db, ref=rec_b.source_rce_id, canonical_entity_id=ent, intake_id=intake_b)
        assert [i.source_record_id for i in await _issues(db, rec_b)] == [rec_b.id] and await _issues(db, rec_a) == []
        # a reference the named delivery does not carry has no anchor: nothing is written for A
        await av.record_coverage_attempt(db, ref=rec_b.source_rce_id, canonical_entity_id=ent, intake_id=intake_a)
        assert await _issues(db, rec_a) == []
        await av.record_coverage_attempt(db, ref=rec_a.source_rce_id, canonical_entity_id=ent, intake_id=intake_a)
        assert len(await _issues(db, rec_a)) == 1 and len(await _issues(db, rec_b)) == 1


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_recheck_writes_the_finding_on_the_jobs_own_deliverys_record(rolled_back_db, monkeypatch, a_promoted_later):
    from types import SimpleNamespace
    from app.tefca_registry.rce import rechecks
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    monkeypatch.setattr(rechecks, "evidence_sources_for", lambda _sid: ("NPPES",))
    job = SimpleNamespace(intake_id=intake_b, pinned={}, source_id="nppes", trigger_ref="x")
    item = SimpleNamespace(entity_ref=rec_b.source_rce_id, entity_id=ent, state=None, outcome=None,
                           new_disposition=None, processed_at=None, detail={})
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_entity_dict(ent))), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=_not_found_evidence())):
        await rechecks._recheck_one(db, job, item, local_store=None)
    assert len(await _issues(db, rec_b)) == 1 and await _issues(db, rec_a) == []


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_the_arc_pipeline_writes_the_finding_on_the_verified_deliverys_record(rolled_back_db, a_promoted_later):
    from app.tefca_registry.rce import arc_pipeline
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    gathered = [{"ref": rec_b.source_rce_id, "entity": _entity_dict(ent), "evidence": _not_found_evidence()}]
    with patch.object(arc_pipeline, "_gather_all_evidence", new=AsyncMock(return_value=gathered)):
        await arc_pipeline.verify_and_classify(db, [rec_b.source_rce_id], intake_id=intake_b, actor="anchor-test")
    assert len(await _issues(db, rec_b)) == 1 and await _issues(db, rec_a) == []
    # without a delivery (no intake) the reference cannot be anchored: nothing is written anywhere
    with patch.object(arc_pipeline, "_gather_all_evidence", new=AsyncMock(return_value=gathered)):
        await arc_pipeline.verify_and_classify(db, [rec_b.source_rce_id], intake_id=None, actor="anchor-test")
    assert len(await _issues(db, rec_b)) == 1 and await _issues(db, rec_a) == []


# ── the single-record review caller (review_service.run_review) ──────────────────────────────────────────────────

class _User:
    role = "reviewer"
    email = "anchor-test-reviewer@synthetic-test.docuaction.invalid"
    id = None


def _wire_review(monkeypatch):
    """Real run_review, real database; only the NPPES probe is replaced with a NOT_FOUND answer."""
    from app.tefca_registry import review_service as rs

    async def _probe(_db, _entity_id, **_kw):
        return {"nppes": {"status": "not_found", "npi_outcome": vf.NPI_NOT_FOUND,
                          "npi_outcome_detail": "NPI not present in NPPES.", "lookup_identifier": NPI}}
    monkeypatch.setattr(rs, "probe_sources", _probe)
    monkeypatch.setattr(rs._classifier, "_rules", None)
    monkeypatch.setattr(rs._classifier, "_loaded_at", 0.0)
    return rs


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_run_review_writes_the_finding_on_the_anchored_deliverys_record(rolled_back_db, monkeypatch, a_promoted_later):
    from app.tefca_registry import models as reg
    db = rolled_back_db
    rs = _wire_review(monkeypatch)
    ent, (_ia, rec_a), (_ib, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    entity = await db.get(reg.TefcaRegEntity, ent)
    await rs.run_review(db, entity, user=_User(), source_record_id=rec_b.id)
    assert [i.source_record_id for i in await _issues(db, rec_b)] == [rec_b.id]
    assert await _issues(db, rec_a) == []                                   # never the other delivery's ledger


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_an_unanchored_or_mismatched_run_review_writes_no_finding(rolled_back_db, monkeypatch, a_promoted_later):
    from app.tefca_registry import models as reg
    db = rolled_back_db
    rs = _wire_review(monkeypatch)
    ent, (_ia, rec_a), (_ib, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    entity = await db.get(reg.TefcaRegEntity, ent)
    await rs.run_review(db, entity, user=_User())                           # entity-level review: no record anchor
    assert await _issues(db, rec_a) == [] and await _issues(db, rec_b) == []
    other = await seed_entity(db, oid=f"{SYN}-ANCHOR-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=NPI)
    other_entity = await db.get(reg.TefcaRegEntity, other)
    with pytest.raises(ValueError):                                         # a record that is not this entity's is refused
        await rs.run_review(db, other_entity, user=_User(), source_record_id=rec_b.id)
    assert await _issues(db, rec_a) == [] and await _issues(db, rec_b) == []


# ── record_from_sources (run_review's NPPES-outcome writer): same anchor, same validation ────────────────────────

_SOURCES = {"nppes": {"status": "not_found", "npi_outcome": vf.NPI_NOT_FOUND,
                      "npi_outcome_detail": "NPI not present in NPPES.", "lookup_identifier": NPI}}


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_record_from_sources_enforces_the_same_anchor_and_scope(rolled_back_db, a_promoted_later):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    # missing scope: nothing written, whatever the entity's other deliveries hold
    assert await vf.record_from_sources(db, entity_id=ent, sources=_SOURCES) is None
    # invalid scope: B's record named with A's delivery; A's record named with B's delivery; another entity
    assert await vf.record_from_sources(db, entity_id=ent, sources=_SOURCES,
                                        source_record_id=rec_b.id, intake_id=intake_a) is None
    assert await vf.record_from_sources(db, entity_id=ent, sources=_SOURCES,
                                        source_record_id=rec_a.id, intake_id=intake_b) is None
    other = await seed_entity(db, oid=f"{SYN}-ANCHOR-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=NPI)
    assert await vf.record_from_sources(db, entity_id=other, sources=_SOURCES, source_record_id=rec_b.id) is None
    assert await _issues(db, rec_a) == [] and await _issues(db, rec_b) == []
    # valid scope: the same record serves insertion and duplicate detection
    first = await vf.record_from_sources(db, entity_id=ent, sources=_SOURCES, source_record_id=rec_b.id, intake_id=intake_b)
    again = await vf.record_from_sources(db, entity_id=ent, sources=_SOURCES, source_record_id=rec_b.id, intake_id=intake_b)
    assert first is not None and again.id == first.id and first.source_record_id == rec_b.id
    assert len(await _issues(db, rec_b)) == 1 and await _issues(db, rec_a) == []
