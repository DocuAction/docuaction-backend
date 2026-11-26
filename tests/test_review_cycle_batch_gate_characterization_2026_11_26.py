"""The review-cycle gate and unadjudicated default-routed reviews (2026-11-26).

SUPERSEDED EXPECTATIONS - READ THIS FIRST
─────────────────────────────────────────
On main, and on the PR this branch is stacked on (#252), this file CHARACTERIZES the gate as it is: after the
first batch writes a default-routed (DEFAULT-UNMATCHED) review, every later review-cycle call is refused 409
"Failed: Every default-routed determination is adjudicated", and a later delivery of the same organisations is
refused on its first call. Those expectations are intact there.

THIS branch proposes Option A (OWNER DECISION REQUIRED - not approved): when that adjudication check is the ONLY
failed check, classification may continue and the requirement is reported as open. So in THIS branch only, the
three "refused" expectations below are deliberately replaced by "proceeds, and says what is open". Each replaced
expectation is marked `SUPERSEDED BY OPTION A`. If the owner declines Option A, this file reverts to its #252
content with the application change.

What does NOT change, and is still asserted here exactly as on #252: the live evaluation fails exactly ONE check
(the adjudication one) and every population-closure and classification-integrity check passes; reconciliation
reads NOT passed; the stored PIPELINE snapshot still says passed and `current` still says current (the staleness
#252 attributes). The full Option A behaviour, and everything that must still refuse, is in
tests/test_review_cycle_open_adjudication_2026_11_26.py.

Real intake -> quality -> curation -> promotion, real `reconcile_delivery` (NOT replaced), real
`persist_snapshot`, real cycle, writer, gatherer, classifier and shipped rules; sources are recorders.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import disposition_followups
from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce import routes as rce_routes
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
                           checks_at_job_end=len(at_job_end["checks"]),
                           check_names=[c["check"] for c in at_job_end["checks"]])


def text_entities(intake_id):
    from app.tefca_registry.rce import models as m
    return select(m.RceCuratedRecord.canonical_entity_id).where(
        m.RceCuratedRecord.source_intake_id == intake_id,
        m.RceCuratedRecord.canonical_entity_id.isnot(None))


async def _default_routed(db, entities):
    return (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id.in_(list(entities)),
        reg.ReviewRecord.classification_rule == ap.UNMATCHED_RULE_CODE))).scalars().all()


async def test_the_first_batch_with_a_default_routed_review_no_longer_blocks_the_next_batch(delivery):
    d = delivery

    # ── batch 1: the gate passes (no review exists yet), two members are classified - as on #252 ──
    first = await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert first["reconciliation_passed_at_gate"] is True
    assert first["members"] == MEMBERS and first["linked_this_call"] == BATCH
    assert first["remaining"] == MEMBERS - BATCH and first["complete"] is False
    default_routed = await _default_routed(d.db, d.entities)
    assert len(default_routed) >= 1, "the first batch must produce at least one default-routed review"
    assert {r.classification_bucket for r in default_routed} == {"B3"}
    assert all(r.reviewer_resolution is None for r in default_routed)

    # ── the LIVE evaluation - UNCHANGED from #252: ONE check fails, and it is the adjudication one ──
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert live["passed"] is False
    assert [c["check"] for c in live["failed_checks"]] == [ADJUDICATION_CHECK]
    assert live["dimensions"]["verification"]["default_unadjudicated"] == len(default_routed)
    assert live["dimensions"]["verification"]["without_evidence"] == 0
    assert live["dimensions"]["verification"]["without_rule"] == 0
    assert live["equation"]["holds"] is True

    # ── batch 2. SUPERSEDED BY OPTION A: on #252 this call raises CycleRefused "... Failed: Every default-routed
    #    determination is adjudicated." Here it proceeds, and says that reconciliation has NOT passed ──
    second = await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert second["linked_this_call"] == BATCH and second["remaining"] == MEMBERS - 2 * BATCH
    assert second["reconciliation_passed"] is False and second["reconciliation_passed_at_gate"] is False
    assert second["adjudication_open"] is True and second["default_routed_unadjudicated"] >= len(default_routed)
    audit = (await d.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_refused"))).scalars().all()
    assert audit == []


async def test_the_stored_snapshot_and_every_stored_status_still_say_passed(delivery):
    """The stale "passed" - unchanged by Option A: what the API reads from the stored snapshot after reviews
    exist. (The cycle still writes no snapshot.)"""
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    # SUPERSEDED BY OPTION A: on #252 a second call here is refused; the stored facts below are the same either way.

    served = await rce_routes.reconciliation(str(d.intake), recompute=False, db=d.db, user=VIEWER)
    assert served["source"] == "persisted_snapshot" and served["snapshot"]["id"] == d.snapshot_id
    assert served["passed"] is True and served["failed_checks"] == []
    assert len(served["checks"]) == d.checks_at_job_end
    stored_check = next(c for c in served["checks"] if c["check"] == ADJUDICATION_CHECK)
    assert stored_check["passed"] is True and stored_check["detail"].startswith("0 default-routed")
    assert served["dimensions"]["verification"]["reviews"] == 0        # the snapshot predates every review
    assert served["attribution"]["passed_now"] is False                # ... and #252's attribution says so

    latest = await reconciliation.latest_snapshot(d.db, d.job_id)
    assert str(latest.id) == d.snapshot_id and latest.passed is True
    assert await reconciliation.snapshot_history_count(d.db, d.job_id) == 1

    processing = await disposition_followups.summary(d.db, d.intake)
    assert processing["reconciliation_current"] is True

    assert (await job_for_intake(d.db, d.intake)) is not None
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert live["passed"] is False


async def test_a_later_delivery_of_the_same_organisations_is_no_longer_refused_on_its_first_call(delivery):
    """The check is scoped by ENTITY (unchanged): it counts every unadjudicated default-routed review of any
    entity this delivery promoted to, including reviews made for an EARLIER delivery.

    SUPERSEDED BY OPTION A: on #252 the later delivery's first call is refused before a plan is drawn. Here it
    proceeds - and the count it reports INCLUDES the earlier delivery's reviews, and says so."""
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    earlier = await _default_routed(d.db, d.entities)
    assert earlier and all(r.verification_results.get("source_intake_id") == str(d.intake) for r in earlier)

    later, _job = await seed_intake(d.db, make_rows(MEMBERS, arc="9.99.781.1", address_line="2 Synthetic Way"))
    await run_quality_and_curation(d.db, later)
    await promote_delivery(d.db, later, actor=SYN)
    assert set((await d.db.execute(text_entities(later))).scalars().all()) == set(d.entities)

    live = await reconciliation.reconcile_delivery(d.db, later)                 # unchanged: NOT passed
    assert [c["check"] for c in live["failed_checks"]] == [ADJUDICATION_CHECK]
    assert live["dimensions"]["verification"]["default_unadjudicated"] == len(earlier)

    first = await rc._create_review_cycle(d.db, later, user=_PM, full_population=True, batch=BATCH)
    assert first["linked_this_call"] == BATCH
    assert first["reconciliation_passed"] is False and first["reconciliation_passed_at_gate"] is False
    assert first["default_routed_unadjudicated_at_gate"] == len(earlier)        # the earlier delivery's reviews
    assert first["default_routed_unadjudicated"] >= len(earlier)
    assert "earlier delivery" in first["open_requirements"][0]["scope"]


async def test_the_route_no_longer_answers_409_for_an_open_adjudication_requirement(delivery):
    """SUPERSEDED BY OPTION A: on #252 the second call is HTTP 409."""
    from app.tefca_registry import workflow_routes as wr

    d = delivery
    body = wr.ReviewCycleRequest(batch=BATCH, full_population=True)
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    first = await wr.create_review_cycle_route(str(d.intake), body, request, db=d.db, user=_PM)
    assert first["linked_this_call"] == BATCH
    second = await wr.create_review_cycle_route(str(d.intake), body, request, db=d.db, user=_PM)
    assert second["linked_this_call"] == BATCH and second["adjudication_open"] is True
    assert second["reconciliation_passed"] is False
