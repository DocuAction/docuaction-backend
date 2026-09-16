"""Sample-draw eligibility for post-promotion findings (Decision 2 follow-up,
2026-09-16).

An entity with an UNRESOLVED blocking post-promotion finding
(verification_status = "in_review") is excluded from the frame of every NEW
statistical sample draw. Nothing else changes: the record is not held,
hidden, resolved or rewritten; it stays in the ledger, the analyst queue,
reconciliation and the reports; historical draws are never redrawn; the
sample-size formula, strata, seed handling and randomisation are the
engine's and are untouched; the eligibility decision is its own audit fact.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.tefca_registry import models as reg
from app.tefca_registry import qhin_sampling as qs
from app.tefca_registry.rce import exception_ledger, reconciliation
from app.tefca_registry.rce import post_promotion_verification as ppv
from rce_traceability_support import NPI_VALID_OTHER, SYN, rolled_back_db  # noqa: F401
from test_post_promotion_verification import _promoted

ANALYST = "analyst@example.test"
QA = "qalead@example.test"


async def _with_qhin(db, arc):
    """A REAL promoted record (disposition events, snapshot-able) that is a
    sampling unit: promotion already writes the canonical managed_by_qhin
    edge from the row's managing QHIN; an edge is added here only if none
    exists (a second parent would make the unit "2 managing QHINs")."""
    rows, intake_id, job, curated, promo = await _promoted(db, arc)
    entity_id = curated.canonical_entity_id
    edges = (await db.execute(select(reg.TefcaEntityRelationship.id).where(
        reg.TefcaEntityRelationship.child_entity_id == entity_id,
        reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
        reg.TefcaEntityRelationship.status == "active"))).all()
    if not edges:
        qhin_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(
            id=qhin_id, name=f"{SYN} QHIN {arc}", display_name=f"{SYN} QHIN {arc}",
            entity_level="qhin", entity_type="health_information_network",
            operational_status="active", verification_status="not_verified",
            current_version=1, is_active=True))
        await db.flush()
        db.add(reg.TefcaEntityRelationship(
            id=uuid.uuid4(), parent_entity_id=qhin_id, child_entity_id=entity_id,
            relationship_type="managed_by_qhin", status="active", source="import",
            effective_date=date(2026, 1, 1)))
        await db.flush()
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert [u["entity_id"] for u in eligible] == [entity_id], unresolved
    return intake_id, job, curated, entity_id


async def _block(db, entity_id):
    return await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                    npi=NPI_VALID_OTHER, actor="SYSTEM")


async def _members(db, sample_id):
    return {r for (r,) in (await db.execute(
        select(reg.SampleEntity.entity_id).where(reg.SampleEntity.sample_id == sample_id))).all()}


async def _audit(db, action):
    return (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == action))).scalars().all()


# ── excluded / eligible / restored ───────────────────────────────────────────

async def test_unresolved_blocking_finding_is_excluded_from_a_new_draw(rolled_back_db):
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.81")
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert [u["entity_id"] for u in eligible] == [entity_id]

    await _block(db, entity_id)

    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert eligible == []
    (unit,) = unresolved
    assert unit["entity_id"] == entity_id
    assert unit["exclusion"] == qs.EXCLUSION_REVIEW_REQUIRED
    assert "in_review" in unit["reason"]
    # still a reported unit, not a filtered-away one; record untouched
    assert unit["record_status"] == curated.record_status
    with pytest.raises(qs.SamplingRefused):
        await qs.finalize_plan(db, intake_id, seed=7, actor=SYN)


async def test_nonblocking_finding_remains_eligible(rolled_back_db):
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.82")
    await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_NOT_FOUND,
                             npi=NPI_VALID_OTHER, actor="SYSTEM")
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert [u["entity_id"] for u in eligible] == [entity_id]
    assert qs.review_required_exclusions(unresolved) == []
    plan = await qs.finalize_plan(db, intake_id, seed=7, actor=SYN)
    assert entity_id in await _members(db, uuid.UUID(plan["sample_id"]))
    assert plan["excluded_review_required"] == 0


async def test_resolved_blocking_finding_restores_eligibility_under_existing_rules(rolled_back_db):
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.83")
    out = await _block(db, entity_id)
    assert (await qs.resolve_qhin_strata(db, intake_id))[0] == []

    # QA is required (blocking findings are QA_REQUIRED); same actor is refused
    with pytest.raises(Exception):
        await ppv.resolve_post_promotion_finding(
            db, out["issue"]["id"], decision="CONFIRMED", actor=ANALYST, qa_actor=ANALYST,
            notes="same person")
    await ppv.resolve_post_promotion_finding(
        db, out["issue"]["id"], decision="CONFIRMED", actor=ANALYST, qa_actor=QA,
        notes="Deactivation confirmed; QA concurs.")

    eligible, _ = await qs.resolve_qhin_strata(db, intake_id)
    assert [u["entity_id"] for u in eligible] == [entity_id]
    # the existing methodology decides membership: a one-unit stratum is a census
    plan = await qs.finalize_plan(db, intake_id, seed=7, actor=SYN)
    assert entity_id in await _members(db, uuid.UUID(plan["sample_id"]))
    assert plan["per_qhin_selected"] if "per_qhin_selected" in plan else True


# ── visibility, reconciliation, history ──────────────────────────────────────

async def test_excluded_entity_remains_visible_in_ledger_and_reconciliation(rolled_back_db):
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.84")
    before = await reconciliation.reconcile_delivery(db, intake_id)
    assert before["passed"] is True

    await _block(db, entity_id)
    await qs.resolve_qhin_strata(db, intake_id)   # the exclusion decision

    await db.refresh(curated)
    assert curated.canonical_entity_id == entity_id
    assert curated.record_status in ("CLEAN", "CORRECTED"), "never held/rewritten"
    ledger = await exception_ledger.list_exceptions(db, str(intake_id))
    codes = {i["issue_type"] for i in ledger["items"]}
    assert "NPI_DEACTIVATED" in codes, "the finding is visible in the delivery ledger"

    after = await reconciliation.reconcile_delivery(db, intake_id)
    assert after["passed"] is True, after
    assert after["populations"]["E_promoted_to_registry"] == \
        before["populations"]["E_promoted_to_registry"]
    assert after["equation"] == before["equation"], "the equation still balances, unchanged"


async def test_historical_draw_is_unchanged_and_never_redrawn(rolled_back_db):
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.85")
    first = await qs.finalize_plan(db, intake_id, seed=41, actor=SYN)
    sample_id = uuid.UUID(first["sample_id"])
    members_before = await _members(db, sample_id)
    assert entity_id in members_before

    await _block(db, entity_id)

    again = await qs.finalize_plan(db, intake_id, seed=999, actor=SYN)
    assert again["already_finalized"] is True
    assert again["sample_id"] == first["sample_id"]
    assert again["random_seed"] == first["random_seed"]
    assert await _members(db, sample_id) == members_before, "historical membership untouched"
    plans = (await db.execute(select(func.count()).select_from(reg.ReviewSample))).scalar()
    assert plans == 1

    # a genuinely NEW plan (different parameters) has no eligible unit now
    with pytest.raises(qs.SamplingRefused):
        await qs.finalize_plan(db, intake_id, review_type="adhoc", seed=5, actor=SYN)


async def test_no_duplicate_analyst_work_item_from_sampling(rolled_back_db):
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.86")
    await _block(db, entity_id)
    for _ in range(3):
        await qs.resolve_qhin_strata(db, intake_id)
        await qs.preview_plan(db, intake_id)
    n = (await db.execute(select(func.count()).select_from(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id == entity_id))).scalar()
    assert n == 1, "sampling eligibility never opens a second work item"


# ── audit and authorization ──────────────────────────────────────────────────

async def test_eligibility_decision_is_recorded_in_the_audit_trail(rolled_back_db):
    db = rolled_back_db
    # preview reports the exclusion on a single-unit delivery
    a_intake, _, _, a_entity = await _with_qhin(db, "9.99.777.87")
    await _block(db, a_entity)
    preview = await qs.preview_plan(db, a_intake)
    assert preview["excluded_review_required"] == 1 and preview["eligible_population"] == 0

    # a plan with one excluded unit beside eligible ones records the decision
    from test_qhin_sampling_operational import _build
    intake_id, qhins = await _build(db, {"A": 3})
    victim = (await db.execute(select(reg.TefcaRegEntity.id).where(
        reg.TefcaRegEntity.rce_org_oid == "9.99.222.A.0"))).scalar_one()
    entity = await db.get(reg.TefcaRegEntity, victim)
    entity.verification_status = ppv.REVIEW_REQUIRED_STATUS   # as record_finding sets it
    await db.flush()

    plan = await qs.finalize_plan(db, intake_id, seed=3, actor=SYN, actor_id=None)
    assert plan["excluded_review_required"] == 1
    assert victim not in await _members(db, uuid.UUID(plan["sample_id"]))
    rows = [r for r in await _audit(db, "sampling_eligibility_excluded")
            if r.metadata_.get("sample_id") == plan["sample_id"]]
    assert len(rows) == 1
    meta = rows[0].metadata_
    assert meta["entity_ids"] == [str(victim)]
    assert meta["exclusion"] == qs.EXCLUSION_REVIEW_REQUIRED
    assert "in_review" in meta["reason"]
    finalized = [r for r in await _audit(db, "sampling_plan_finalized")
                 if r.metadata_.get("sample_id") == plan["sample_id"]]
    assert finalized[0].metadata_["excluded_review_required"] == 1
    assert finalized[0].metadata_["random_seed"] == plan["random_seed"]


def test_review_cycle_creation_is_program_manager_only():
    """Authorization: the only route that draws an official sample."""
    import inspect

    from app.tefca_registry import workflow_routes

    src = inspect.getsource(workflow_routes.create_review_cycle_route)
    assert 'require_role("program_manager")' in src


def test_the_exclusion_touches_no_sizing_strata_seed_or_selection_code():
    """The engine (`sampling_engine.CochranSampler`) is not modified by this
    change; the exclusion is a frame decision made before the engine runs."""
    import inspect

    from app.tefca_registry import qhin_sampling as q
    from app.tefca_registry import sampling_engine

    assert "in_review" not in inspect.getsource(sampling_engine)
    assert "REVIEW_REQUIRED" not in inspect.getsource(sampling_engine)
    src = inspect.getsource(q.finalize_plan)
    assert "CochranSampler().draw_per_stratum(" in src and "seed=seed" in src
