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
    from rce_traceability_support import anchor_for_entity
    return await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                    npi=NPI_VALID_OTHER, actor="SYSTEM",
                                    **await anchor_for_entity(db, entity_id))


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
    from rce_traceability_support import anchor_for_entity
    await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_NOT_FOUND,
                             npi=NPI_VALID_OTHER, actor="SYSTEM",
                             **await anchor_for_entity(db, entity_id))
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


# ── D-08 PROPOSED narrow semantics (flag-gated, default OFF) ─────────────────

@pytest.mark.asyncio
async def test_d08_blocking_only_flag_narrows_exclusion_to_enumerated_findings(
        rolled_back_db, monkeypatch):
    """`in_review` is overloaded: arc_pipeline writes it for every non-B1
    bucket, independent of any finding. Default (flag off): the status-based
    exclusion stands, byte-identical. Flag on (D-08 proposal): only an
    unresolved finding of an enumerated BLOCKING type excludes — an entity
    whose status says in_review but which carries no such finding is back in
    the frame, and a genuine blocking finding still excludes."""
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "D08")

    # Overload the status WITHOUT any finding — what arc_pipeline does for
    # every B2/B3/B4 classification.
    entity = await db.get(reg.TefcaRegEntity, entity_id)
    entity.verification_status = ppv.REVIEW_REQUIRED_STATUS
    await db.flush()

    # Default: excluded on status alone (current behavior, unchanged).
    monkeypatch.delenv(qs.PROPOSED_SAMPLING_BLOCKING_ONLY_ENV, raising=False)
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert entity_id not in {u["entity_id"] for u in eligible}
    meta = next(u for u in unresolved if u["entity_id"] == entity_id)
    assert meta["exclusion"] == qs.EXCLUSION_REVIEW_REQUIRED
    assert "verification_status=in_review" in meta["reason"]

    # Flag on: no unresolved BLOCKING finding exists, so the entity is in the
    # frame again.
    monkeypatch.setenv(qs.PROPOSED_SAMPLING_BLOCKING_ONLY_ENV, "1")
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert entity_id in {u["entity_id"] for u in eligible}

    # A genuine enumerated blocking finding excludes under the flag too, with
    # the narrow-semantics reason text.
    await _block(db, entity_id)
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert entity_id not in {u["entity_id"] for u in eligible}
    meta = next(u for u in unresolved if u["entity_id"] == entity_id)
    assert meta["exclusion"] == qs.EXCLUSION_REVIEW_REQUIRED
    assert "rce_issues" in meta["reason"]


# ── FULL-POPULATION classification frame (owner authorization 2026-11-27) ─────
# Methodology v1.4 §205/§69: automated full-population classification is separate from statistical sampling. A
# prior entity-level `in_review` status no longer keeps a delivered record out of a FULL-POPULATION frame; statistical
# draws and D-08 are unchanged; findings, holds and statuses are untouched by the frame decision.

@pytest.mark.parametrize("blocking_only_flag", [None, "1"])
async def test_full_population_frame_ignores_entity_level_review_status(rolled_back_db, monkeypatch,
                                                                        blocking_only_flag):
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, f"9.99.777.9{1 if blocking_only_flag else 0}")
    if blocking_only_flag:
        monkeypatch.setenv(qs.PROPOSED_SAMPLING_BLOCKING_ONLY_ENV, blocking_only_flag)
    else:
        monkeypatch.delenv(qs.PROPOSED_SAMPLING_BLOCKING_ONLY_ENV, raising=False)
    await _block(db, entity_id)                            # a genuine unresolved BLOCKING finding -> in_review

    # Statistical frame: still excluded, under either D-08 setting (unchanged behaviour).
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert eligible == [] and qs.review_required_exclusions(unresolved)
    # Full-population frame: in.
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id, full_population=True)
    assert [u["entity_id"] for u in eligible] == [entity_id]
    assert qs.review_required_exclusions(unresolved) == []

    plan = await qs.finalize_plan(db, intake_id, actor=SYN, full_population=True)
    assert entity_id in await _members(db, uuid.UUID(plan["sample_id"]))
    sample = await db.get(reg.ReviewSample, uuid.UUID(plan["sample_id"]))
    assert sample.strata_config["eligibility_basis"] == qs.FULL_POPULATION_ELIGIBILITY_BASIS
    assert sample.strata_config["excluded_review_required"] == 0
    finalized = [r for r in await _audit(db, "sampling_plan_finalized")
                 if r.metadata_.get("sample_id") == plan["sample_id"]]
    assert finalized[0].metadata_["eligibility_basis"] == qs.FULL_POPULATION_ELIGIBILITY_BASIS

    # The frame decision changes nothing else: status, finding and record untouched; statistical plan still refused.
    assert (await db.get(reg.TefcaRegEntity, entity_id)).verification_status == ppv.REVIEW_REQUIRED_STATUS
    assert await ppv.has_unresolved_blocking_finding(db, entity_id)
    ledger = await exception_ledger.list_exceptions(db, str(intake_id))
    assert "NPI_DEACTIVATED" in {i["issue_type"] for i in ledger["items"]}
    with pytest.raises(qs.SamplingRefused):
        await qs.finalize_plan(db, intake_id, seed=7, actor=SYN)


async def test_full_population_frame_still_excludes_held_and_missing_qhin(rolled_back_db):
    """Only the entity-status exclusion is lifted: a unit with no QHIN edge stays unresolved with its reason."""
    db = rolled_back_db
    rows, intake_id, job, curated, promo = await _promoted(db, "9.99.777.93")
    await db.execute(reg.TefcaEntityRelationship.__table__.update().where(
        reg.TefcaEntityRelationship.child_entity_id == curated.canonical_entity_id).values(status="inactive"))
    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id, full_population=True)
    assert eligible == [] and unresolved[0]["reason"] == "no canonical managed_by_qhin edge"


def test_statistical_paths_do_not_pass_full_population():
    """preview_plan and the workload rollup are sampling views and keep the statistical frame."""
    import inspect

    from app.tefca_registry import qhin_workload
    assert "full_population" not in inspect.getsource(qs.preview_plan)
    assert "full_population=True" not in inspect.getsource(qhin_workload)
    assert "full_population=full_population" in inspect.getsource(qs.finalize_plan)


def test_identity_precheck_can_preview_the_full_population_frame():
    import inspect

    from app.tefca_registry import review_cycle as rc
    from app.tefca_registry import workflow_routes
    assert "full_population=full_population" in inspect.getsource(rc.preview_identity_precheck)
    assert "full_population=full_population" in inspect.getsource(
        workflow_routes.review_cycle_identity_precheck_route)


# ── independent review 2026-11-27: BLOCKING 1 and 2 ─────────────────────────

async def test_an_open_extract_candidate_is_uncleared_prior_risk(rolled_back_db):
    """Lifting the frame exclusion must not let a clean B1 verify an entity whose SAM extract candidate is open:
    the candidate sets `in_review` without a review record or a BLOCKING finding, so prior_risk must see it."""
    from app.tefca_registry.rce import prior_risk
    from app.tefca_registry.rce import verification_findings as vf
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.94")
    assert await prior_risk.unresolved_prior_risk(db, entity_id) is None
    await vf.record_sam_extract_candidate(
        db, entity_id=entity_id, npi=None, org_name=f"{SYN} ORG", intake_id=intake_id,
        source_record_id=curated.source_record_id,
        extract={"publication_date": "2026-11-26", "extract_sha256": "b" * 64, "candidate_count": 1})
    await db.flush()
    prior = await prior_risk.unresolved_prior_risk(db, entity_id)
    assert prior and prior["open_extract_candidate"] is True
    assert "SAM public-extract candidate" in prior_risk.rationale_suffix(prior)


async def _old_frame(db, plan, excluded):
    from sqlalchemy.orm.attributes import flag_modified
    sample = await db.get(reg.ReviewSample, uuid.UUID(plan["sample_id"]))
    config = dict(sample.strata_config)
    config.pop("eligibility_basis", None)
    config["excluded_review_required"] = excluded
    sample.strata_config = config
    flag_modified(sample, "strata_config")
    await db.flush()


async def test_a_pre_correction_full_population_plan_that_excluded_entities_is_not_reused(rolled_back_db):
    db = rolled_back_db
    intake_id, *_ = await _with_qhin(db, "9.99.777.95")
    plan = await qs.finalize_plan(db, intake_id, actor=SYN, full_population=True)
    await _old_frame(db, plan, excluded=3)
    with pytest.raises(qs.SamplingRefused, match="earlier frame"):
        await qs.finalize_plan(db, intake_id, actor=SYN, full_population=True)


async def test_a_pre_correction_full_population_plan_with_no_exclusion_is_reused(rolled_back_db):
    db = rolled_back_db
    intake_id, *_ = await _with_qhin(db, "9.99.777.96")
    plan = await qs.finalize_plan(db, intake_id, actor=SYN, full_population=True)
    await _old_frame(db, plan, excluded=0)
    again = await qs.finalize_plan(db, intake_id, actor=SYN, full_population=True)
    assert again["already_finalized"] is True and again["sample_id"] == plan["sample_id"]


async def test_a_recheck_risk_signal_clears_only_by_a_person_cleared_review_after_it(rolled_back_db):
    """CodeRabbit 2026-11-27: the recheck signal clears exactly as `clearance_state` clears an exclusion signal
    (reclassified to B1/B2 by a person AND QA-reportable), and only when the person's act postdates the signal."""
    from datetime import datetime, timedelta

    from app.tefca_registry.rce import prior_risk
    from app.tefca_registry.rce import recheck_models as rm
    db = rolled_back_db
    intake_id, job, curated, entity_id = await _with_qhin(db, "9.99.777.97")
    t0 = datetime(2026, 11, 20, 12, 0, 0)
    rj = rm.RceRecheckJob(intake_id=intake_id, trigger_kind="test", source_id="sam_gov", trigger_ref="t",
                          idempotency_key=uuid.uuid4().hex, requested_by=SYN, rationale="synthetic",
                          baseline_hash="0" * 64)
    db.add(rj)
    await db.flush()
    db.add(rm.RceRecheckItem(job_id=rj.id, entity_id=entity_id, entity_ref="ref", state="DONE",
                             outcome=rm.OUTCOME_RISK_SIGNAL, processed_at=t0))
    await db.flush()

    async def _review(n, **kw):
        db.add(reg.ReviewRecord(review_id=f"REV-TST-97{n:04d}", entity_id=entity_id, classification_bucket="B3", **kw))
        await db.flush()

    assert (await prior_risk.unresolved_prior_risk(db, entity_id))["uncleared_recheck_risk_signal_at"]
    # reclassified to a FINDING after the signal, reportable: not a clearance
    await _review(1, reviewer_resolution="reclassified", reclassified_to="B3",
                  reclassified_at=t0 + timedelta(hours=1), reportable_at=t0 + timedelta(hours=2))
    assert (await prior_risk.unresolved_prior_risk(db, entity_id))["uncleared_recheck_risk_signal_at"]
    # cleared by a person BEFORE the signal, QA-approved after it: not a clearance of THIS signal
    await _review(2, reviewer_resolution="reclassified", reclassified_to="B1",
                  reclassified_at=t0 - timedelta(hours=1), reportable_at=t0 + timedelta(hours=3))
    assert (await prior_risk.unresolved_prior_risk(db, entity_id))["uncleared_recheck_risk_signal_at"]
    # reclassified to B1 by a person after the signal but NOT yet QA-approved: still uncleared
    await _review(3, reviewer_resolution="reclassified", reclassified_to="B1", reclassified_at=t0 + timedelta(hours=4))
    assert (await prior_risk.unresolved_prior_risk(db, entity_id))["uncleared_recheck_risk_signal_at"]
    # person-cleared after the signal and QA-reportable: cleared
    await _review(4, reviewer_resolution="reclassified", reclassified_to="B1",
                  reclassified_at=t0 + timedelta(hours=5), reportable_at=t0 + timedelta(hours=6))
    prior = await prior_risk.unresolved_prior_risk(db, entity_id)
    assert prior is None or "uncleared_recheck_risk_signal_at" not in prior
