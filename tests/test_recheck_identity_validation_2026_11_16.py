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

Coverage (`automated_verification`) has no attempt marker other than the entity's evidence rows, so it cannot refuse
an attempt without being reselected forever; there the shared validation gates only the NPI ledger write and the
identifier change it carries (flagged in the result and counted in the batch summary).

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
        assert len(st["issues"]) == 2 and all(i.original_value == NPI for i in st["issues"])   # stored as digits
        assert st["identifier"] == [vf.INACTIVE_PENDING_REVIEW]     # the row that claims the flip really flipped it
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


async def test_the_writer_owns_the_savepoint_a_failure_after_the_flip_leaves_no_flip_and_no_row(rolled_back_db, monkeypatch):
    """For a caller WITHOUT its own savepoint (arc_pipeline, review_service): the real writer flips the identifier,
    then fails before its insert; neither survives and the session stays usable."""
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.78", entity_npi=NPI, delivered_npi=NPI)

    async def _boom(*_a, **_k):
        raise RuntimeError("synthetic failure after the identifier flip")
    monkeypatch.setattr(vf.run_selection, "current_run", _boom)
    with pytest.raises(RuntimeError):
        await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_DEACTIVATED,
                                    source_record_id=rec.id, intake_id=intake_id)
    await db.flush()                                                     # still usable
    st = await _state(db, ent, rec)
    assert st["identifier"] == ["active"] and st["issues"] == []


@pytest.mark.parametrize("npi", [None, "", "n/a"])
async def test_a_deactivation_reported_with_no_npi_writes_nothing(rolled_back_db, npi):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.83", entity_npi=NPI, delivered_npi=NPI)
    assert await vf.record_npi_outcome(db, entity_id=ent, npi=npi, outcome=vf.NPI_DEACTIVATED,
                                       source_record_id=rec.id, intake_id=intake_id) is None
    st = await _state(db, ent, rec)
    assert st["issues"] == [] and st["identifier"] == ["active"]


async def test_a_failed_ledger_write_is_flagged_on_the_item_not_swallowed(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.79", entity_npi=NPI, delivered_npi=NPI)

    async def _boom(*_a, **_k):
        raise RuntimeError("synthetic secret text that must not be stored")
    monkeypatch.setattr(vf.run_selection, "current_run", _boom)         # inside the REAL writer, after the flip
    from app.tefca_registry.rce import rechecks
    monkeypatch.setattr(rechecks, "evidence_sources_for", lambda _sid: ("NPPES",))
    job = SimpleNamespace(intake_id=intake_id, pinned={}, source_id="nppes", trigger_ref="x")
    item = SimpleNamespace(entity_ref=rec.source_rce_id, entity_id=ent, state=None, outcome=None,
                           new_disposition=None, processed_at=None, detail={})
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=_deactivated_evidence())):
        await rechecks._recheck_one(db, job, item, local_store=None)
    await db.flush()
    st = await _state(db, ent, rec)
    assert item.state == rm.ITEM_DONE
    assert item.detail["ledger_recorded"] is False and item.detail["ledger_error_class"] == "RuntimeError"
    assert "synthetic secret" not in str(item.detail)
    assert st["identifier"] == ["active"] and st["issues"] == [] and st["evidence"] >= 1


# ── the job: an all-refused job is not a success ─────────────────────────────────────────────────────────────────────

async def _real_job(db, intake_id, items):
    from app.tefca_registry.rce import rechecks
    from app.tefca_registry.rce.recheck_models import RceRecheckItem, RceRecheckJob
    job = RceRecheckJob(id=uuid.uuid4(), intake_id=intake_id, trigger_kind=rm.TRIGGER_SOURCE_RECOVERY, source_id="nppes",
                        trigger_ref="synthetic", idempotency_key=uuid.uuid4().hex, state=rm.STATE_RUNNING,
                        requested_by="syn", rationale="synthetic", pinned={}, baseline_hash="0" * 64,
                        target_count=len(items), processed_count=0)
    db.add(job)
    await db.flush()
    for ent, ref in items:
        db.add(RceRecheckItem(id=uuid.uuid4(), job_id=job.id, entity_id=ent, entity_ref=ref,
                              state=rm.ITEM_PENDING, detail={}))
    await db.flush()
    job.baseline_hash = await rechecks._current_baseline(db, job)
    await db.flush()
    return job


async def _run_job(db, monkeypatch, job, resolve):
    from app.tefca_registry.rce import rechecks
    monkeypatch.setattr(rechecks, "evidence_sources_for", lambda _sid: ("NPPES",))
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=resolve)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=_deactivated_evidence())):
        return await rechecks.run_batch(db, job.id)


async def test_a_job_whose_every_item_was_refused_fails_with_a_reason(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=True)
    other = await seed_entity(db, oid=f"{SYN}-OTHER-{uuid.uuid4().hex[:8]}", name=f"{SYN} other entity", npi=OTHER_NPI)
    job = await _real_job(db, intake_b, [(ent, rec_b.source_rce_id)])

    async def _resolve(_db, _ref):
        return _resolved(other, OTHER_NPI)
    out = await _run_job(db, monkeypatch, job, _resolve)
    assert out["state"] == rm.STATE_FAILED and out["complete"] is True      # never SUCCEEDED
    assert "refused at identity validation" in out["error_reason"]
    assert "resolver" not in out["error_reason"].lower()                    # the reason is identity, not the resolver
    assert out["summary"]["identity_refused"] == 1
    assert out["summary"]["by_outcome"] == {rm.OUTCOME_IDENTITY_REFUSED: 1}
    _untouched(await _state(db, ent, rec_a, rec_b))
    _untouched(await _state(db, other))


async def test_a_mixed_job_succeeds_and_reports_the_refused_count(rolled_back_db, monkeypatch):
    db = rolled_back_db
    good, intake_id, (rec_good,) = await _one_delivery(db, "9.99.777.84", entity_npi=NPI, delivered_npi=NPI)
    # a second entity whose reference is NOT carried by this delivery -> refused
    bad = await seed_entity(db, oid=f"{SYN}-BAD-{uuid.uuid4().hex[:8]}", name=f"{SYN} bad entity", npi=OTHER_NPI)
    job = await _real_job(db, intake_id, [(good, rec_good.source_rce_id), (bad, "9.99.777.84.not-delivered")])

    async def _resolve(_db, ref):
        return _resolved(good) if ref == rec_good.source_rce_id else _resolved(bad, OTHER_NPI)
    out = await _run_job(db, monkeypatch, job, _resolve)
    assert out["state"] == rm.STATE_SUCCEEDED and out["complete"] is True
    assert out["summary"]["identity_refused"] == 1 and out["summary"]["ledger_not_recorded"] == 0
    assert out["summary"]["by_outcome"] == {rm.OUTCOME_IDENTITY_REFUSED: 1, rm.OUTCOME_RISK_SIGNAL: 1}
    assert [i.source_record_id for i in (await _state(db, good, rec_good))["issues"]] == [rec_good.id]
    assert (await _state(db, bad))["evidence"] == 0


async def test_the_job_summary_counts_a_failed_ledger_write(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.85", entity_npi=NPI, delivered_npi=NPI)
    job = await _real_job(db, intake_id, [(ent, rec.source_rce_id)])
    real_current_run = vf.run_selection.current_run
    calls = {"n": 0}

    async def _boom_in_writer(db_, intake):
        import inspect
        if any(f.function == "_write_npi_outcome" for f in inspect.stack()):
            raise RuntimeError("synthetic ledger failure")
        return await real_current_run(db_, intake)
    monkeypatch.setattr(vf.run_selection, "current_run", _boom_in_writer)

    async def _resolve(_db, _ref):
        return _resolved(ent)
    out = await _run_job(db, monkeypatch, job, _resolve)
    assert out["summary"]["ledger_not_recorded"] == 1
    st = await _state(db, ent, rec)
    assert st["issues"] == [] and st["identifier"] == ["active"] and st["evidence"] >= 1


# ── the sibling automated-coverage path uses the same validation ─────────────────────────────────────────────────────

async def _coverage(db, *, ref, ent, intake, resolved, evidence=None):
    from app.tefca_registry.rce import automated_verification as av
    build = AsyncMock(return_value=evidence or _deactivated_evidence())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
        out = await av.record_coverage_attempt(db, ref=ref, canonical_entity_id=ent, intake_id=intake)
    return out, build


@pytest.mark.parametrize("case", ["another_entity", "no_registry_id", "reference_not_in_this_delivery", "npi_differs"])
async def test_coverage_without_a_validated_context_writes_no_npi_outcome_and_changes_no_identifier(rolled_back_db, case):
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
    out, _build = await _coverage(db, ref=ref, ent=ent, intake=intake_b, resolved=resolved)
    assert out["npi_outcome_not_anchored"]                                  # flagged, with the reason
    for e in (ent, other):
        st = await _state(db, e, rec_a, rec_b)
        assert st["issues"] == [] and vf.INACTIVE_PENDING_REVIEW not in st["identifier"]


async def test_a_failed_coverage_ledger_write_is_flagged_and_leaves_no_flip(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, intake_id, (rec,) = await _one_delivery(db, "9.99.777.86", entity_npi=NPI, delivered_npi=NPI)

    async def _boom(*_a, **_k):
        raise RuntimeError("synthetic secret text that must not be stored")
    monkeypatch.setattr(vf.run_selection, "current_run", _boom)
    out, _build = await _coverage(db, ref=rec.source_rce_id, ent=ent, intake=intake_id, resolved=_resolved(ent))
    await db.flush()
    st = await _state(db, ent, rec)
    assert out["ledger_recorded"] is False and out["ledger_error_class"] == "RuntimeError"
    assert "synthetic secret" not in str(out)
    assert st["identifier"] == ["active"] and st["issues"] == [] and st["evidence"] >= 1


async def test_coverage_terminates_when_more_entities_lack_a_validated_context_than_fit_in_a_batch(rolled_back_db):
    """The regression this guards: refusing a coverage attempt without persisting anything reselects the same
    entities every batch. Three entities whose probes would use an NPI the delivery did not submit, batch size 2:
    the second batch must advance and coverage must complete."""
    from app.tefca_registry.rce import automated_verification as av
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.87")
    for r in rows:
        r["NPI"] = NPI
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    by_ref = {}
    for r in recs:
        e = await seed_entity(db, oid=f"{SYN}-COV-{uuid.uuid4().hex[:8]}", name=f"{SYN} cov entity")
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=r.id,
                                  record_status="CLEAN", rce_org_oid=r.source_rce_id, transformation_version="test",
                                  canonical_entity_id=e, promoted_at=T0))
        by_ref[r.source_rce_id] = _resolved(e, OTHER_NPI)        # registry NPI != the delivered NPI
    await db.flush()

    async def _resolve(_db, ref):
        return by_ref[ref]
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=_resolve)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(return_value=_not_found_evidence())):
        first = await av.run_coverage_batch(db, intake_id, batch_size=2)
        second = await av.run_coverage_batch(db, intake_id, batch_size=2)
    assert first["processed_this_call"] == 2 and first["remaining"] == 1 and first["complete"] is False
    assert second["processed_this_call"] == 1 and second["remaining"] == 0 and second["complete"] is True
    assert first["npi_outcome_not_anchored"] == 2 and second["npi_outcome_not_anchored"] == 1
    assert (await db.execute(select(m.RceIssue).where(m.RceIssue.source_intake_id == intake_id))).scalars().all() == []


@pytest.mark.parametrize("a_promoted_later", [True, False])
async def test_a_successful_coverage_attempt_writes_on_the_delivered_record(rolled_back_db, a_promoted_later):
    db = rolled_back_db
    ent, (intake_a, rec_a), (intake_b, rec_b) = await _two_deliveries(db, a_promoted_later=a_promoted_later)
    out, _build = await _coverage(db, ref=rec_b.source_rce_id, ent=ent, intake=intake_b, resolved=_resolved(ent))
    st = await _state(db, ent, rec_a, rec_b)
    assert st["evidence"] >= 1
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


@pytest.mark.parametrize("scope", ["full", "enrollment_only"])
async def test_conflicting_rce_and_fhir_npis_refuse_before_any_probe(rolled_back_db, monkeypatch, scope):
    from app.tefca_registry.rce import rechecks
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.777.84", entity_npi=NPI, delivered_npi=NPI)
    entity = _resolved(ent, OTHER_NPI)
    entity["_rce"] = {"NPI": NPI}
    enrollment = AsyncMock()
    monkeypatch.setattr(rechecks, "_enrollment_only_evidence", enrollment)
    pinned = {"evidence_scope": rechecks.SCOPE_CMS_ENROLLMENT_ONLY} if scope == "enrollment_only" else {}
    item, writer, build = await _recheck(db, monkeypatch, ent=ent, intake=intake,
        ref=rec.source_rce_id, resolved=entity, pinned=pinned)
    _refused(item)
    writer.assert_not_awaited()
    build.assert_not_awaited()
    enrollment.assert_not_awaited()
    _untouched(await _state(db, ent, rec))


@pytest.mark.parametrize("representation", ["rce_only", "fhir_only", "matching", "blank_first"])
async def test_recheck_probes_share_one_npi_without_mutating_the_resolver_result(rolled_back_db, monkeypatch, representation):
    from copy import deepcopy
    from app.Tefca.connectors import _extract_npi
    from app.Tefca.rce_fields import rce_npi
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.777.85", entity_npi=NPI, delivered_npi=NPI)
    entity = _resolved(ent, None if representation == "rce_only" else NPI)
    if representation != "fhir_only":
        entity["_rce"] = {"NPI": NPI}
    if representation == "blank_first":
        entity["identifier"].insert(0, {"system": "http://hl7.org/fhir/sid/us-npi", "value": ""})
    before = deepcopy(entity)
    item, writer, build = await _recheck(db, monkeypatch, ent=ent, intake=intake,
        ref=rec.source_rce_id, resolved=entity)
    assert item.outcome == rm.OUTCOME_RISK_SIGNAL
    probed = build.await_args.args[0]
    assert _extract_npi(probed) == rce_npi(probed) == writer.await_args.kwargs["npi"] == NPI
    assert entity == before


@pytest.mark.parametrize("failed_read", ["anchor", "source"])
async def test_a_failed_anchor_read_does_not_poison_the_callers_transaction(rolled_back_db, monkeypatch, failed_read):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.777.86", entity_npi=NPI, delivered_npi=NPI)
    original_anchor = vf._curated_for_anchor
    original_get = db.get

    async def broken_anchor(*args, **kwargs):
        await db.execute(text("SELECT 1 / 0"))

    async def broken_get(model, *args, **kwargs):
        if model is m.RceSourceRecord:
            await db.execute(text("SELECT 1 / 0"))
        return await original_get(model, *args, **kwargs)

    with monkeypatch.context() as mp:
        if failed_read == "anchor":
            mp.setattr(vf, "_curated_for_anchor", broken_anchor)
        else:
            mp.setattr(db, "get", broken_get)
        from sqlalchemy.exc import DBAPIError
        with pytest.raises(DBAPIError):
            await vf.record_npi_outcome(db, entity_id=ent, npi=NPI, outcome=vf.NPI_DEACTIVATED,
                source_record_id=rec.id, intake_id=intake)
    assert await db.scalar(text("SELECT 41 + 1")) == 42
    assert await original_anchor(db, ent, rec.id, intake) is not None
    _untouched(await _state(db, ent, rec))


async def test_conflicting_fhir_npi_identifiers_are_not_overwritten(rolled_back_db, monkeypatch):
    from copy import deepcopy
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.777.87", entity_npi=NPI, delivered_npi=NPI)
    entity = _resolved(ent)
    entity["identifier"].append({"system": "http://hl7.org/fhir/sid/us-npi", "value": OTHER_NPI})
    before = deepcopy(entity)
    item, writer, build = await _recheck(db, monkeypatch, ent=ent, intake=intake,
        ref=rec.source_rce_id, resolved=entity)
    _refused(item)
    writer.assert_not_awaited()
    build.assert_not_awaited()
    assert entity == before
    _untouched(await _state(db, ent, rec))


# -- the preflight reads: a real database failure in one item must not lose the batch (CodeRabbit 5916281973) --------

@pytest.mark.parametrize("failed_read", ["anchor", "source"])
async def test_a_failed_preflight_read_fails_one_item_and_the_batch_session_stays_usable(rolled_back_db, monkeypatch, failed_read):
    """`validated_delivery_context` reads the anchor and the delivered record. When PostgreSQL itself rejects one of
    those reads for one item (a real statement error, not a Python exception), that item ends ERROR with nothing
    persisted, the NEXT item is still processed, and the batch flushes its summary on the same session."""
    db = rolled_back_db
    ent_fail = await seed_entity(db, oid=f"{SYN}-PF-A-{uuid.uuid4().hex[:8]}", name=f"{SYN} preflight a", npi=NPI)
    ent_ok = await seed_entity(db, oid=f"{SYN}-PF-B-{uuid.uuid4().hex[:8]}", name=f"{SYN} preflight b", npi=OTHER_NPI)
    rows = make_rows(2, arc="9.99.777.88")
    rows[0]["NPI"], rows[1]["NPI"] = NPI, OTHER_NPI
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    by_ref = {r.source_rce_id: r for r in recs}
    rec_fail, rec_ok = by_ref[rows[0]["id"]], by_ref[rows[1]["id"]]
    for rec, ent in ((rec_fail, ent_fail), (rec_ok, ent_ok)):
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
                                  record_status="CLEAN", rce_org_oid=rec.source_rce_id, transformation_version="test",
                                  canonical_entity_id=ent, promoted_at=T0))
    await db.flush()
    job = await _real_job(db, intake_id, [(ent_fail, rec_fail.source_rce_id), (ent_ok, rec_ok.source_rce_id)])
    job_id, fail_ref, fail_rec_id = job.id, rec_fail.source_rce_id, rec_fail.id

    async def _resolve(_db, ref):
        return _resolved(ent_fail, NPI) if ref == fail_ref else _resolved(ent_ok, OTHER_NPI)

    original_anchor, original_get = vf.anchor_for_reference, db.get

    async def broken_anchor(db_, *, intake_id, ref, entity_id):
        if ref == fail_ref:
            await db_.execute(text("SELECT 1 / 0"))                 # PostgreSQL rejects the statement
        return await original_anchor(db_, intake_id=intake_id, ref=ref, entity_id=entity_id)

    async def broken_get(model, ident, *args, **kwargs):
        if model is m.RceSourceRecord and ident == fail_rec_id:
            await db.execute(text("SELECT 1 / 0"))
        return await original_get(model, ident, *args, **kwargs)

    with monkeypatch.context() as mp:
        if failed_read == "anchor":
            mp.setattr(vf, "anchor_for_reference", broken_anchor)
        else:
            mp.setattr(db, "get", broken_get)
        out = await _run_job(db, mp, job, _resolve)

    assert await db.scalar(text("SELECT 41 + 1")) == 42                     # the batch session is still usable
    assert out["summary"]["by_outcome"] == {rm.ITEM_ERROR: 1, rm.OUTCOME_RISK_SIGNAL: 1}, out["summary"]
    assert out["complete"] is True
    items = {i.entity_ref: i for i in (await db.execute(select(rm.RceRecheckItem).where(
        rm.RceRecheckItem.job_id == job_id))).scalars().all()}
    assert items[fail_ref].state == rm.ITEM_ERROR and items[fail_ref].outcome is None
    assert items[rec_ok.source_rce_id].state == rm.ITEM_DONE
    _untouched(await _state(db, ent_fail, rec_fail))                        # nothing persisted for the failed item
    ok = await _state(db, ent_ok, rec_ok)                                   # the other item was fully processed
    assert ok["evidence"] > 0 and [i.source_record_id for i in ok["issues"]] == [rec_ok.id]
