"""CHARACTERIZATION (2026-11-26): the adjudication check blocks the NEXT classification batch.

Observed on DEV (backend e9064c2, synthetic 5-record delivery): one review-cycle call classified four members,
two of them default-routed (rule code DEFAULT-UNMATCHED, bucket B3). Every later call for that delivery was refused
409 with "Failed: Every default-routed determination is adjudicated", while `GET .../reconciliation` and the
delivery page still said the delivery had passed.

This file REPRODUCES that, on unmodified main, with the real parts:

    real intake -> quality -> curation -> promotion        (rce_traceability_support)
    real `reconcile_delivery`                              (NOT replaced - every other cycle test replaces it)
    real `persist_snapshot` (trigger PIPELINE)             (what the delivery job stores at its RECONCILIATION stage)
    real review cycle: plan, members, walk, writer, gatherer, classifier, shipped rules
    sources: recorders that answer "unavailable"           (nothing leaves the machine)

It asserts what the code DOES today, not what it should do. A change to what blocks the cycle must change this
file deliberately, in the same commit, with the owner's adjudication-policy decision recorded.

Why no earlier test saw it: every review-cycle test in the suite replaces `reconcile_delivery` with a stub that
returns `{"passed": True}` (see `_real_cycle` in tests/test_blank_npi_classified_on_submitted_values_2026_11_25.py),
and the adjudication check's own test (tests/test_default_routing_provenance.py) never runs a cycle.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import disposition_followups
from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce import routes as rce_routes
from app.tefca_registry.rce import status_model
from app.tefca_registry.rce.delivery_jobs import job_for_intake
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401 - `rolled_back_db` is a fixture
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)
from test_blank_npi_classified_on_submitted_values_2026_11_25 import _PM, _Sources, _give_qhin

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("no_outbound_network")]

ADJUDICATION_CHECK = "Every default-routed determination is adjudicated"
MEMBERS = 6
BATCH = 2
VIEWER = SimpleNamespace(id=None, email="viewer@synthetic-test.docuaction.invalid", role="viewer")


def _cycle_with_the_real_gate(monkeypatch, db):
    """A real review cycle INCLUDING the real reconciliation gate. Only two things are replaced: the release
    barrier (a process-wide control, not part of this question) and the session the resolver reads on (the
    writer's scratch sessions cannot see this test's uncommitted transaction)."""
    import app.tefca_registry.processing_controls as pc
    from app.Tefca.entity_resolution import resolve_from_db

    async def _enter(_db):
        return None

    async def _resolve(_db, ref):
        return await resolve_from_db(db, ref)

    async def _resolve_all(refs):
        return {ref: await resolve_from_db(db, ref) for ref in dict.fromkeys(refs)}

    monkeypatch.setattr(pc, "enter_processing", _enter)
    monkeypatch.setattr("app.Tefca.entity_resolution.resolve_entity", _resolve)
    monkeypatch.setattr(ap, "_resolve_all", _resolve_all)


@pytest.fixture
async def delivery(rolled_back_db, monkeypatch):
    """One promoted synthetic delivery of six records, reconciled and snapshotted as the delivery job leaves it:
    BEFORE any review exists (verification is deferred to the review cycle - delivery_runner.py:667-675)."""
    db = rolled_back_db
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    monkeypatch.delenv("ENABLE_SAM_EXTRACT_SCREENING", raising=False)
    sources = _Sources().install(monkeypatch)
    rows = make_rows(MEMBERS, arc="9.99.781.1")
    intake_id, job = await seed_intake(db, rows)
    job_id = job.id
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    entities = (await db.execute(text_entities(intake_id))).scalars().all()
    assert len(entities) == MEMBERS
    await _give_qhin(db, entities)
    _cycle_with_the_real_gate(monkeypatch, db)

    at_job_end = await reconciliation.reconcile_delivery(db, intake_id)
    assert at_job_end["passed"] is True, at_job_end["failed_checks"]
    snapshot = await reconciliation.persist_snapshot(
        db, intake_id, at_job_end, job_id=job_id, actor=SYN, trigger="PIPELINE")
    return SimpleNamespace(db=db, intake=intake_id, job_id=job_id, sources=sources, entities=entities,
                           snapshot_id=str(snapshot.id), snapshot_sequence=snapshot.sequence,
                           checks_at_job_end=len(at_job_end["checks"]))


def text_entities(intake_id):
    from app.tefca_registry.rce import models as m
    return select(m.RceCuratedRecord.canonical_entity_id).where(
        m.RceCuratedRecord.source_intake_id == intake_id,
        m.RceCuratedRecord.canonical_entity_id.isnot(None))


async def _default_routed(db, entities):
    return (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id.in_(list(entities)),
        reg.ReviewRecord.classification_rule == ap.UNMATCHED_RULE_CODE))).scalars().all()


async def test_the_first_batch_with_a_default_routed_review_blocks_every_later_batch(delivery):
    d = delivery

    # ── batch 1: the gate passes (no review exists yet), two members are classified ──
    first = await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert first["reconciliation_passed"] is True
    assert first["members"] == MEMBERS and first["linked_this_call"] == BATCH
    assert first["remaining"] == MEMBERS - BATCH and first["complete"] is False
    default_routed = await _default_routed(d.db, d.entities)
    assert len(default_routed) >= 1, "the first batch must produce at least one default-routed review"
    assert {r.classification_bucket for r in default_routed} == {"B3"}
    assert all(r.reviewer_resolution is None for r in default_routed)
    asked = len(d.sources.npi_calls) + len(d.sources.name_calls)

    # ── batch 2: refused, with exactly the reason seen on DEV; four members stay unclassified ──
    with pytest.raises(rc.CycleRefused) as refused:
        await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert str(refused.value) == (
        f"Delivery {d.intake} has not passed reconciliation; a review cycle cannot be drawn from a population "
        f"that does not close. Failed: {ADJUDICATION_CHECK}.")
    assert len(d.sources.npi_calls) + len(d.sources.name_calls) == asked           # no source was asked
    members = (await d.db.execute(select(reg.SampleEntity).where(
        reg.SampleEntity.entity_id.in_(list(d.entities))))).scalars().all()
    assert sum(1 for mbr in members if mbr.review_id is None) == MEMBERS - BATCH   # ... and nothing more linked
    audit = (await d.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_refused"))).scalars().all()
    assert [(a.metadata_ or {}).get("failed_checks") for a in audit] == [[ADJUDICATION_CHECK]]

    # ── the LIVE evaluation: ONE check fails, and it is the adjudication one. Every population-closure and
    #    classification-integrity check still passes: the population closes exactly as it did at the job's end ──
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert live["passed"] is False
    assert [c["check"] for c in live["failed_checks"]] == [ADJUDICATION_CHECK]
    assert live["dimensions"]["verification"]["default_unadjudicated"] == len(default_routed)
    assert live["dimensions"]["verification"]["without_evidence"] == 0
    assert live["dimensions"]["verification"]["without_rule"] == 0
    assert live["equation"]["holds"] is True


async def test_the_stored_snapshot_and_every_stored_status_still_say_passed(delivery):
    """The stale "passed": what the API and the delivery page read after the gate has started refusing."""
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    with pytest.raises(rc.CycleRefused):
        await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)

    # GET /api/tefca/rce/deliveries/{id}/reconciliation (default: no recompute) - the stored snapshot, taken
    # before any review existed.
    served = await rce_routes.reconciliation(str(d.intake), recompute=False, db=d.db, user=VIEWER)
    assert served["source"] == "persisted_snapshot" and served["snapshot"]["id"] == d.snapshot_id
    assert served["passed"] is True and served["failed_checks"] == []
    assert len(served["checks"]) == d.checks_at_job_end
    stored_check = next(c for c in served["checks"] if c["check"] == ADJUDICATION_CHECK)
    assert stored_check["passed"] is True and stored_check["detail"].startswith("0 default-routed")
    assert served["dimensions"]["verification"]["reviews"] == 0        # the snapshot predates every review

    # Nothing the cycle does writes a snapshot: the one from the job's end is still the latest.
    latest = await reconciliation.latest_snapshot(d.db, d.job_id)
    assert str(latest.id) == d.snapshot_id and latest.passed is True
    assert await reconciliation.snapshot_history_count(d.db, d.job_id) == 1

    # "reconciliation_passed ... snapshot ... passed=True" on the delivery page is this basis line, built from
    # that stored snapshot.
    outcome = status_model.processing_outcome(
        job_state="SUCCEEDED", job_stage="READY_FOR_REVIEW", snapshot=latest.to_dict(),
        stages_completed=list(status_model.REQUIRED_STAGES))
    basis = next(b for b in outcome["basis"] if b["criterion"] == "reconciliation_passed")
    assert basis["held"] is True and f"snapshot {d.snapshot_id} passed=True" in basis["detail"]

    # "Reconciliation current: CURRENT" is NOT a comparison with a live evaluation. It only says no recorded
    # analyst finding-decision has delivery processing outstanding.
    processing = await disposition_followups.summary(d.db, d.intake)
    assert processing["reconciliation_current"] is True

    # The same question asked live (qalead and above: ?recompute=true) gives the opposite answer.
    assert (await job_for_intake(d.db, d.intake)) is not None
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert live["passed"] is False


async def test_a_later_delivery_of_the_same_organisations_is_refused_on_its_first_call(delivery):
    """The check is scoped by ENTITY, not by delivery, sample or source record: it counts every unadjudicated
    default-routed review of any entity this delivery promoted to - including reviews made for an EARLIER
    delivery. So a later delivery of the same organisations cannot start its cycle at all."""
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    earlier = await _default_routed(d.db, d.entities)
    assert earlier and all(r.verification_results.get("source_intake_id") == str(d.intake) for r in earlier)

    # The same organisations (same ids), delivered again; one address line differs so the file is a new file.
    later, _job = await seed_intake(d.db, make_rows(MEMBERS, arc="9.99.781.1", address_line="2 Synthetic Way"))
    await run_quality_and_curation(d.db, later)
    await promote_delivery(d.db, later, actor=SYN)
    assert set((await d.db.execute(text_entities(later))).scalars().all()) == set(d.entities)
    assert not (await d.db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.verification_results["source_intake_id"].astext == str(later)))).scalars().all()

    with pytest.raises(rc.CycleRefused) as refused:
        await rc._create_review_cycle(d.db, later, user=_PM, full_population=True, batch=BATCH)
    assert str(refused.value).endswith(f"Failed: {ADJUDICATION_CHECK}.")
    live = await reconciliation.reconcile_delivery(d.db, later)
    assert [c["check"] for c in live["failed_checks"]] == [ADJUDICATION_CHECK]
    assert live["dimensions"]["verification"]["default_unadjudicated"] == len(earlier)
    assert not (await d.db.execute(select(reg.ReviewSample).where(
        reg.ReviewSample.strata_config["source_intake_id"].astext == str(later)))).scalars().all()   # no plan drawn


async def test_the_route_answers_409(delivery):
    from app.tefca_registry import workflow_routes as wr

    d = delivery
    body = wr.ReviewCycleRequest(batch=BATCH, full_population=True)
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    first = await wr.create_review_cycle_route(str(d.intake), body, request, db=d.db, user=_PM)
    assert first["linked_this_call"] == BATCH
    with pytest.raises(HTTPException) as refused:
        await wr.create_review_cycle_route(str(d.intake), body, request, db=d.db, user=_PM)
    assert refused.value.status_code == 409 and ADJUDICATION_CHECK in refused.value.detail
