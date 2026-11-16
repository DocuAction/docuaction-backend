"""Verification writes validate IDENTITY FIRST, before anything is persisted (CodeRabbit on #233, rounds 5-6; owner
2026-11-16). One shared validation (`verification_findings.validated_delivery_context`) serves the recheck and the
automated-coverage callers; the ledger writer (`record_npi_outcome`) validates its anchor and the delivered NPI before
it changes an identifier status or inserts a row.

Rules proved here:
  * queued entity == resolved registry entity; exactly one delivered record of the delivery carries the reference and
    links that entity; the NPI the probes are asked about is the NPI that record delivered (digits compared, a
    spreadsheet's trailing ".0" tolerated; both blank is equal) - the rule `arc_pipeline` already applies.
  * a refusal persists NOTHING: no evidence, ledger row, identifier status or entity status - for the queued entity
    or for the entity the reference resolves to now;
  * the writer changes no identifier status and writes no row without an anchor, with a mismatched anchor, with no
    NPI, or for an NPI the anchored record did not deliver;
  * a failure inside the ledger write rolls back to a savepoint (no orphan identifier change, session still usable);
  * a job whose every item was refused FAILS with a reason and reports the count; it never reads SUCCEEDED.

Deliberately NOT implemented (it contradicts the refusal rule): a review nitpick asked the refusal test to assert that
`record_from_evidence` is awaited with `source_record_id=None`; a refused recheck must not reach outcome recording.
Synthetic data only.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

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


def _not_found_evidence() -> dict:
    return {"dimensions": [{"dimension": "IDENTITY", "disposition": "NOT_FOUND", "applicability": "APPLICABLE",
                            "evidence": [{"source": "NPPES", "disposition": "NOT_FOUND",
                                          "note": "NPI not present in NPPES."}]}]}


async def _state(db, ent, *recs):
    evidence = (await db.execute(select(TEFCADimensionEvidence).where(
        TEFCADimensionEvidence.entity_id == str(ent)))).scalars().all()
    ident = (await db.execute(select(reg.TefcaEntityIdentifier.identifier_status).where(
        reg.TefcaEntityIdentifier.entity_id == ent, reg.TefcaEntityIdentifier.identifier_type == "npi"))).scalars().all()
    entity_status = (await db.get(reg.TefcaRegEntity, ent)).verification_status
    issues = (await db.execute(select(m.RceIssue).where(
        m.RceIssue.source_record_id.in_([r.id for r in recs])))).scalars().all() if recs else []
    return {"evidence": len(evidence), "identifier": ident, "entity_status": entity_status, "issues": issues}


def _untouched(st):
    assert st["evidence"] == 0 and st["issues"] == [] and st["identifier"] == ["active"], st
    assert st["entity_status"] == "not_verified", st


async def _one_delivery(db, arc, *, entity_npi, delivered_npi, duplicate=False):
    ent = await seed_entity(db, oid=f"{SYN}-ONE-{uuid.uuid4().hex[:8]}", name=f"{SYN} shared entity", npi=entity_npi)
    rows = make_rows(2 if duplicate else 1, arc=arc)
    for r in rows:
        r["NPI"] = delivered_npi
    if duplicate:
        rows[1]["id"] = rows[0]["id"]                          # the SAME delivered reference twice in one delivery
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    for r in recs:
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=r.id,
                                  record_status="CLEAN", rce_org_oid=r.source_rce_id, transformation_version="test",
                                  canonical_entity_id=ent, promoted_at=T0))
    await db.flush()
    return ent, intake_id, recs


def _resolved(ent, npi=NPI):
    d = _entity_dict(ent)
    d["identifier"] = [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi}] if npi else []
    return d


async def _recheck(db, monkeypatch, *, ent, intake, ref, resolved, evidence=None, pinned=None):
    from app.tefca_registry.rce import rechecks
    monkeypatch.setattr(rechecks, "evidence_sources_for", lambda _sid: ("NPPES",))
    job = SimpleNamespace(intake_id=intake, pinned=pinned or {}, source_id="nppes", trigger_ref=str(uuid.uuid4()))
    item = SimpleNamespace(entity_ref=ref, entity_id=ent, state=None, outcome=None, new_disposition=None,
                           processed_at=None, detail={})
    spy = AsyncMock(side_effect=vf.record_from_evidence)
    build = AsyncMock(return_value=evidence or _deactivated_evidence())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build), \
            patch.object(vf, "record_from_evidence", new=spy):
        await rechecks._recheck_one(db, job, item, local_store=None)
    return item, spy, build


def _refused(item):
    assert item.state == rm.ITEM_DONE and item.outcome == rm.OUTCOME_IDENTITY_REFUSED, (item.state, item.outcome)
    assert item.detail.get("reason") and item.detail.get("persisted") is False


# ── the shared ledger writer: validate before any change ────────────────────────────────────────────────────────────

async def test_the_writer_changes_no_identifier_status_without_a_valid_anchor(rolled_back_db):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=True)
    other = await seed_entity(db, oid=f"{SYN}-OTHER-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=OTHER_NPI)
    for kw in ({},                                                           # unanchored
               {"source_record_id": uuid.uuid4()},                            # no such record
               {"source_record_id": rec_a.id, "intake_id": intake_b}):        # another delivery named
        assert await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_DEACTIVATED, **kw) is None
    assert await vf.record_npi_outcome(db, entity_id=other, npi=NPI, outcome=vf.NPI_DEACTIVATED,
                                       source_record_id=rec_a.id) is None   # the record links a different entity
    _untouched(await _state(db, ent, rec_a, rec_b))
    other_state = await _state(db, other)
    assert other_state["evidence"] == 0 and vf.INACTIVE_PENDING_REVIEW not in other_state["identifier"]
    issue = await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_DEACTIVATED,
                                        source_record_id=rec_b.id, intake_id=intake_b)
    st = await _state(db, ent, rec_a, rec_b)
    assert issue is not None and [i.source_record_id for i in st["issues"]] == [rec_b.id]
    assert st["identifier"] == [vf.INACTIVE_PENDING_REVIEW]                   # the anchored write still flips it


@pytest.mark.parametrize("delivered,reported,writes", [
    (NPI, NPI, True), (f"{NPI}.0", NPI, True), (f" {NPI} ", NPI, True), (NPI, f"{NPI}.0", True),
    (OTHER_NPI, NPI, False), ("", NPI, False)])
async def test_the_writer_records_only_an_npi_the_anchored_record_delivered(rolled_back_db, delivered, reported, writes):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.81", entity_npi=NPI, delivered_npi=delivered)
    for outcome in (vf.NPI_NOT_FOUND, vf.NPI_DEACTIVATED):
        issue = await vf.record_npi_outcome(db, entity_id=ent, npi=reported, outcome=outcome,
                                            source_record_id=rec.id, intake_id=intake_id)
        assert (issue is not None) is writes, (delivered, reported, outcome)
    st = await _state(db, ent, rec)
    if writes:
        assert len(st["issues"]) == 2 and all(i.original_value for i in st["issues"])
    else:
        assert st["issues"] == [] and st["identifier"] == ["active"]         # no row (never npi=None), no status change


# ── recheck: a refusal persists nothing ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a_promoted_later", [True, False])
@pytest.mark.parametrize("case", ["another_entity", "no_registry_id", "reference_not_in_this_delivery"])
@pytest.mark.parametrize("scope", ["full", "enrollment_only"])
async def test_a_refused_recheck_persists_nothing(rolled_back_db, monkeypatch, a_promoted_later, case, scope):
    from app.tefca_registry.rce import rechecks
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    other = await seed_entity(db, oid=f"{SYN}-OTHER-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=OTHER_NPI)
    resolved, ref = _resolved(ent), rec_b.source_rce_id
    if case == "another_entity":
        resolved = _resolved(other, OTHER_NPI)
    elif case == "no_registry_id":
        resolved.pop("_registry_entity_id")
    else:
        ref = rec_a.source_rce_id                  # delivery A's reference, job is delivery B's: no delivered record
    enrollment = AsyncMock()
    monkeypatch.setattr(rechecks, "_enrollment_only_evidence", enrollment)
    pinned = {"evidence_scope": rechecks.SCOPE_CMS_ENROLLMENT_ONLY} if scope == "enrollment_only" else {}
    item, spy, build = await _recheck(db, monkeypatch, ent=ent, intake=intake_b, ref=ref, resolved=resolved,
                                      pinned=pinned)
    _refused(item)
    _untouched(await _state(db, ent, rec_a, rec_b))
    _untouched(await _state(db, other))                              # the entity it resolves to NOW is untouched too
    spy.assert_not_awaited(); build.assert_not_awaited(); enrollment.assert_not_awaited()   # nothing was even gathered


@pytest.mark.parametrize("entity_npi,delivered", [(NPI, OTHER_NPI), (NPI, "")])
async def test_a_recheck_is_refused_when_the_delivery_submitted_a_different_or_no_npi(
        rolled_back_db, monkeypatch, entity_npi, delivered):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.75", entity_npi=NPI, delivered_npi=delivered)
    item, spy, build = await _recheck(db, monkeypatch, ent=ent, intake=intake_id, ref=rec.source_rce_id,
                                      resolved=_resolved(ent, entity_npi))
    _refused(item)
    assert "NPI" in item.detail["reason"]
    _untouched(await _state(db, ent, rec))
    spy.assert_not_awaited(); build.assert_not_awaited()


async def test_a_duplicate_delivered_record_is_refused_and_persists_nothing(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, intake_id, recs = await _one_delivery(db, "9.99.777.74", entity_npi=NPI, delivered_npi=NPI, duplicate=True)
    item, spy, _build = await _recheck(db, monkeypatch, ent=ent, intake=intake_id, ref=recs[0].source_rce_id,
                                       resolved=_resolved(ent))
    _refused(item)
    _untouched(await _state(db, ent, *recs))
    spy.assert_not_awaited()


# ── recheck: success uses the delivered record context ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_a_successful_recheck_uses_the_delivered_record_context(rolled_back_db, monkeypatch, a_promoted_later):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    item, spy, _build = await _recheck(db, monkeypatch, ent=ent, intake=intake_b, ref=rec_b.source_rce_id,
                                       resolved=_resolved(ent))
    assert item.state == rm.ITEM_DONE and item.outcome == rm.OUTCOME_RISK_SIGNAL
    st = await _state(db, ent, rec_a, rec_b)
    assert st["evidence"] >= 1                                                   # evidence persisted after validation
    assert [i.source_record_id for i in st["issues"]] == [rec_b.id]              # exactly one ledger row, on delivery B
    assert st["issues"][0].source_intake_id == intake_b and st["issues"][0].issue_type == vf.NPI_DEACTIVATED
    assert st["identifier"] == [vf.INACTIVE_PENDING_REVIEW] and st["entity_status"] == "in_review"
    kw = spy.await_args.kwargs
    assert kw["source_record_id"] == rec_b.id and kw["intake_id"] == intake_b and kw["npi"] == NPI


async def test_a_formatted_delivered_npi_is_the_same_npi(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.76", entity_npi=NPI, delivered_npi=f"{NPI}.0")
    item, _spy, _build = await _recheck(db, monkeypatch, ent=ent, intake=intake_id, ref=rec.source_rce_id,
                                        resolved=_resolved(ent))
    assert item.outcome == rm.OUTCOME_RISK_SIGNAL
    assert [i.source_record_id for i in (await _state(db, ent, rec))["issues"]] == [rec.id]


@pytest.mark.parametrize("delivered", ["", NPI])
async def test_no_probe_npi_proceeds_but_never_writes_an_npi_outcome(rolled_back_db, monkeypatch, delivered):
    """No NPI to ask about (both blank, or the registry holds none): nothing NPI-derived can be produced, so the item
    is processed, and no ledger row is written - in particular never a NOT_FOUND row with npi=None."""
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.77", entity_npi=None, delivered_npi=delivered)
    item, _spy, _build = await _recheck(db, monkeypatch, ent=ent, intake=intake_id, ref=rec.source_rce_id,
                                        resolved=_resolved(ent, None), evidence=_not_found_evidence())
    assert item.state == rm.ITEM_DONE and item.outcome != rm.OUTCOME_IDENTITY_REFUSED
    st = await _state(db, ent, rec)
    assert st["evidence"] >= 1 and st["issues"] == []                 # entity evidence appended; no ledger row (npi=None)


async def test_a_failing_ledger_write_rolls_back_to_its_savepoint(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.78", entity_npi=NPI, delivered_npi=NPI)

    async def _flip_then_fail(db_, **kw):
        await vf._mark_identifier_inactive(db_, kw["entity_id"], NPI)   # what the writer does before its insert
        await db_.execute(text("select * from table_that_does_not_exist_2026_11_16"))

    from app.tefca_registry.rce import rechecks
    monkeypatch.setattr(rechecks, "evidence_sources_for", lambda _sid: ("NPPES",))
    job = SimpleNamespace(intake_id=intake_id, pinned={}, source_id="nppes", trigger_ref="x")
    item = SimpleNamespace(entity_ref=rec.source_rce_id, entity_id=ent, state=None, outcome=None,
                           new_disposition=None, processed_at=None, detail={})
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=_deactivated_evidence())), \
            patch.object(vf, "record_from_evidence", new=AsyncMock(side_effect=_flip_then_fail)):
        await rechecks._recheck_one(db, job, item, local_store=None)
    await db.flush()                                                     # the session is still usable
    st = await _state(db, ent, rec)
    assert item.state == rm.ITEM_DONE
    assert st["identifier"] == ["active"] and st["issues"] == []          # no status change without its ledger row
    assert st["evidence"] >= 1                                            # the evidence appended before it stays


# ── the job: an all-refused job is not a success ─────────────────────────────────────────────────────────────────────

async def test_a_job_whose_every_item_was_refused_fails_with_a_reason(rolled_back_db, monkeypatch):
    from app.tefca_registry.rce import rechecks
    from app.tefca_registry.rce.recheck_models import RceRecheckItem, RceRecheckJob
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=True)
    other = await seed_entity(db, oid=f"{SYN}-OTHER-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=OTHER_NPI)
    job = RceRecheckJob(id=uuid.uuid4(), intake_id=intake_b, trigger_kind=rm.TRIGGER_SOURCE_RECOVERY, source_id="nppes",
                        trigger_ref="synthetic", idempotency_key=uuid.uuid4().hex, state=rm.STATE_RUNNING,
                        requested_by="syn", rationale="synthetic", pinned={}, baseline_hash="0" * 64, target_count=1,
                        processed_count=0)
    db.add(job)
    await db.flush()
    db.add(RceRecheckItem(id=uuid.uuid4(), job_id=job.id, entity_id=ent, entity_ref=rec_b.source_rce_id,
                          state=rm.ITEM_PENDING, detail={}))
    await db.flush()
    job.baseline_hash = await rechecks._current_baseline(db, job)
    await db.flush()
    monkeypatch.setattr(rechecks, "evidence_sources_for", lambda _sid: ("NPPES",))
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(other, OTHER_NPI))), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=_deactivated_evidence())):
        out = await rechecks.run_batch(db, job.id)
    assert out["state"] == rm.STATE_FAILED and out["complete"] is True      # never SUCCEEDED
    assert "refused at identity validation" in out["error_reason"]
    assert out["summary"]["identity_refused"] == 1
    assert out["summary"]["by_outcome"] == {rm.OUTCOME_IDENTITY_REFUSED: 1}
    _untouched(await _state(db, ent, rec_a, rec_b))
    _untouched(await _state(db, other))


# ── the sibling automated-coverage path uses the same validation ─────────────────────────────────────────────────────

async def _coverage(db, *, ref, ent, intake, resolved, evidence=None):
    from app.tefca_registry.rce import automated_verification as av
    build = AsyncMock(return_value=evidence or _deactivated_evidence())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
        out = await av.record_coverage_attempt(db, ref=ref, canonical_entity_id=ent, intake_id=intake)
    return out, build


@pytest.mark.parametrize("case", ["another_entity", "no_registry_id", "reference_not_in_this_delivery", "npi_differs"])
async def test_a_refused_coverage_attempt_persists_nothing(rolled_back_db, case):
    from app.tefca_registry.rce import automated_verification as av
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=True)
    other = await seed_entity(db, oid=f"{SYN}-OTHER-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=OTHER_NPI)
    resolved, ref = _resolved(ent), rec_b.source_rce_id
    if case == "another_entity":
        resolved = _resolved(other, OTHER_NPI)
    elif case == "no_registry_id":
        resolved.pop("_registry_entity_id")
    elif case == "npi_differs":
        resolved = _resolved(ent, OTHER_NPI)                 # the probes would ask about an NPI delivery B did not submit
    else:
        ref = rec_a.source_rce_id
    out, build = await _coverage(db, ref=ref, ent=ent, intake=intake_b, resolved=resolved)
    assert out["outcome"] == av.OUTCOME_NOT_ELIGIBLE and out["persisted"] is False and out["reason"]
    _untouched(await _state(db, ent, rec_a, rec_b))
    _untouched(await _state(db, other))
    build.assert_not_awaited()


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_a_successful_coverage_attempt_writes_on_the_delivered_record(rolled_back_db, a_promoted_later):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    out, _build = await _coverage(db, ref=rec_b.source_rce_id, ent=ent, intake=intake_b, resolved=_resolved(ent))
    st = await _state(db, ent, rec_a, rec_b)
    assert out.get("persisted") is not False and st["evidence"] >= 1
    assert [i.source_record_id for i in st["issues"]] == [rec_b.id] and st["issues"][0].source_intake_id == intake_b
    assert st["identifier"] == [vf.INACTIVE_PENDING_REVIEW] and st["entity_status"] == "in_review"


@pytest.mark.parametrize("npi", ["", None])
async def test_the_nppes_derived_helpers_write_nothing_without_an_npi(rolled_back_db, npi):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.82", entity_npi=NPI, delivered_npi="")
    for ev in (_not_found_evidence(), _deactivated_evidence()):
        assert await vf.record_from_evidence(db, entity_id=ent, npi=npi, evidence=ev, source_record_id=rec.id,
                                             intake_id=intake_id) is None
    assert await vf.record_from_sources(db, entity_id=ent, source_record_id=rec.id, intake_id=intake_id, sources={
        "nppes": {"npi_outcome": vf.NPI_NOT_FOUND, "lookup_identifier": npi}}) is None
    st = await _state(db, ent, rec)
    assert st["issues"] == [] and st["identifier"] == ["active"]
