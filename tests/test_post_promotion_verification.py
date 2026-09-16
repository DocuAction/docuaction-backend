"""Decision 2 of the pre-merge review (2026-09-16/18): append-only
post-promotion verification.

A finding discovered AFTER a record is promoted must never rewrite the
original promotion event (`rce_disposition_events`, `record_status`,
`canonical_entity_id`). BLOCKING findings (an invalid active identifier, a
confirmed deactivation, a material identifier conflict) instead: create a new
append-only finding, set the entity's verification_status to "in_review",
open exactly one analyst work item (idempotent per finding), exclude the
entity from a NEW approved sample draw's finalization and from
`Completed — Clean`, and create a NEW reconciliation snapshot. NONBLOCKING
findings (NPI_NOT_FOUND, NPI_VERIFICATION_UNAVAILABLE) are visible but never
hold, exclude, or force a work item.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import curation, dispositions as disp
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import post_promotion_verification as ppv
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_VALID_OTHER, SYN, curated_by_oid, issues_for, make_rows,
    rolled_back_db, run_quality_and_curation, seed_entity, seed_intake,
)

ANALYST = "analyst@example.test"
QA = "qalead@example.test"


async def _promoted(db, arc):
    """One CLEAN record, promoted CREATED, with no NPI conflict — the plain
    starting state every scenario in this module needs."""
    rows = make_rows(1, arc=arc)
    rows[0]["NPI"] = NPI_VALID_OTHER
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    result = await promote_delivery(db, intake_id, actor=SYN)
    await db.refresh(curated)
    assert curated.record_status in ("CLEAN", "CORRECTED")
    assert curated.canonical_entity_id is not None, "fixture must actually promote"
    return rows, intake_id, job, curated, result


async def _entity(db, entity_id):
    return await db.get(reg.TefcaRegEntity, entity_id)


async def _latest_disposition(db, intake_id):
    return (await disp.current_for_intake(db, intake_id))[0]


async def _latest_snapshot(db, intake_id):
    from app.tefca_registry.rce import traceability_models as tm

    result = await db.execute(
        select(tm.RceReconciliationSnapshot)
        .where(tm.RceReconciliationSnapshot.intake_id == intake_id)
        .order_by(tm.RceReconciliationSnapshot.sequence.desc()).limit(1))
    return result.scalar_one_or_none()


# ── blocking finding ─────────────────────────────────────────────────────────

async def test_blocking_finding_never_rewrites_the_original_disposition(rolled_back_db):
    db = rolled_back_db
    rows, intake_id, job, curated, promo = await _promoted(db, "9.99.888.01")
    entity_id = curated.canonical_entity_id
    before_disp = await _latest_disposition(db, intake_id)
    before_status = curated.record_status

    out = await ppv.record_finding(
        db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED, npi=NPI_VALID_OTHER,
        detail={"deactivation_date": "2026-09-01"}, actor="SYSTEM")

    assert out["blocking"] is True
    assert out["issue"]["issue_type"] == "NPI_DEACTIVATED"
    await db.refresh(curated)
    # the historical promotion fact is untouched
    assert curated.record_status == before_status
    assert curated.canonical_entity_id == entity_id
    after_disp = await _latest_disposition(db, intake_id)
    assert after_disp["disposition"] == before_disp["disposition"]
    assert after_disp["sequence"] == before_disp["sequence"], \
        "no new disposition event was written for a verification finding"


async def test_blocking_finding_sets_review_required_and_opens_exactly_one_case(rolled_back_db):
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.02")
    entity_id = curated.canonical_entity_id

    out = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                   npi=NPI_VALID_OTHER, actor="SYSTEM")
    assert out["verification_status"] == "in_review"
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "in_review"
    assert out["case"]["created"] is True

    from sqlalchemy import text as _text
    rows = (await db.execute(_text(
        "select count(*) from review_records where entity_id = :e "
        "and verification_results->>'queue_source' = :q"),
        {"e": str(entity_id), "q": "RCE_POST_PROMOTION_VERIFICATION"})).scalar()
    assert rows == 1


async def test_blocking_finding_creates_a_new_snapshot_and_leaves_the_old_one_unchanged(
        rolled_back_db):
    from app.tefca_registry.rce import reconciliation

    db = rolled_back_db
    rows, intake_id, job, curated, promo = await _promoted(db, "9.99.888.03")
    entity_id = curated.canonical_entity_id
    # A baseline snapshot, the way the pipeline runner would have already
    # persisted one for a real delivery (promote_delivery alone does not).
    full = await reconciliation.reconcile_delivery(db, intake_id)
    original = await reconciliation.persist_snapshot(
        db, intake_id, full, job_id=job.id, actor=SYN, trigger="PIPELINE")
    original_hash, original_seq = original.hash, original.sequence

    out = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                   npi=NPI_VALID_OTHER, actor="SYSTEM")

    await db.refresh(original)
    assert original.hash == original_hash and original.sequence == original_seq, \
        "the historical snapshot must never be modified"
    assert out["snapshot_id"] and out["snapshot_id"] != str(original.id)
    newest = await _latest_snapshot(db, intake_id)
    assert newest.sequence > original_seq
    assert newest.trigger == "POST_PROMOTION_VERIFICATION"


async def test_material_identifier_conflict_and_invalid_active_identifier_are_blocking(
        rolled_back_db):
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.04")
    entity_id = curated.canonical_entity_id

    conflict = await ppv.record_post_promotion_conflict(
        db, entity_id=entity_id, identifier_type="npi",
        submitted_value="1234567893", existing_value=NPI_VALID_OTHER, actor="SYSTEM")
    assert conflict["blocking"] is True
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "in_review"
    # neither value was changed on the registry
    active = (await db.execute(select(reg.TefcaEntityIdentifier).where(
        reg.TefcaEntityIdentifier.entity_id == entity_id,
        reg.TefcaEntityIdentifier.identifier_type == "npi",
        reg.TefcaEntityIdentifier.identifier_status == "active"))).scalars().all()
    assert [a.identifier_value for a in active] == [NPI_VALID_OTHER]

    invalid = await ppv.check_active_identifier_validity(db, entity_id, actor="SYSTEM")
    assert invalid is None, "the active NPI is valid; nothing to report"


# ── nonblocking finding ──────────────────────────────────────────────────────

async def test_nonblocking_finding_is_visible_but_never_holds_or_excludes(rolled_back_db):
    db = rolled_back_db
    rows, intake_id, job, curated, promo = await _promoted(db, "9.99.888.05")
    entity_id = curated.canonical_entity_id
    before_status = curated.record_status

    out = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_NOT_FOUND,
                                   npi=NPI_VALID_OTHER, actor="SYSTEM")
    assert out["blocking"] is False
    assert out["case"] is None and out["verification_status"] is None
    await db.refresh(curated)
    assert curated.record_status == before_status
    entity = await _entity(db, entity_id)
    assert entity.verification_status != "in_review"

    issues = await issues_for(db, curated.source_record_id, rule_id="NPI-005")
    assert len(issues) == 1
    assert issues[0].severity == "MEDIUM"
    assert issues[0].resolution == "OPEN"  # remains OPEN: visible for analyst review


async def test_external_verification_unavailable_is_recorded_and_nonblocking(rolled_back_db):
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.06")
    entity_id = curated.canonical_entity_id

    out = await ppv.record_finding(db, entity_id=entity_id,
                                   outcome=ppv.NPI_VERIFICATION_UNAVAILABLE,
                                   npi=NPI_VALID_OTHER, detail="NPPES timed out",
                                   actor="SYSTEM")
    assert out["blocking"] is False
    entity = await _entity(db, entity_id)
    assert entity.verification_status != "in_review"
    issues = await issues_for(db, curated.source_record_id, rule_id="NPI-009")
    assert issues[0].severity == "INFORMATIONAL"


# ── duplicate finding delivery / repeated verification ──────────────────────

async def test_duplicate_finding_delivery_writes_one_issue_and_one_case(rolled_back_db):
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.07")
    entity_id = curated.canonical_entity_id

    first = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                     npi=NPI_VALID_OTHER, actor="SYSTEM")
    second = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                      npi=NPI_VALID_OTHER, actor="SYSTEM")
    assert first["issue"]["issue_code"] == second["issue"]["issue_code"]
    assert second["case"]["created"] is False

    issues = await issues_for(db, curated.source_record_id, rule_id="NPI-006")
    assert len(issues) == 1


async def test_repeated_verification_after_resolution_opens_a_new_finding(rolled_back_db):
    """A second, LATER verification pass that finds the SAME outcome again
    after the first was resolved must not be silently swallowed."""
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.08")
    entity_id = curated.canonical_entity_id

    first = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                     npi=NPI_VALID_OTHER, actor="SYSTEM")
    await ppv.resolve_post_promotion_finding(
        db, first["issue"]["id"], decision="FALSE_POSITIVE", actor=ANALYST,
        notes="Reactivated in NPPES since the check ran.")
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "verified"

    second = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                      npi=NPI_VALID_OTHER, actor="SYSTEM")
    assert second["issue"]["id"] != first["issue"]["id"]
    assert second["case"]["created"] is True
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "in_review"


# ── finding resolved by analyst / returned by QA ────────────────────────────

async def test_finding_resolved_by_analyst_requires_independent_qa(rolled_back_db):
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.09")
    entity_id = curated.canonical_entity_id
    out = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                   npi=NPI_VALID_OTHER, actor="SYSTEM")
    issue_id = out["issue"]["id"]

    with pytest.raises(curation.CorrectionRefused):
        await ppv.resolve_post_promotion_finding(
            db, issue_id, decision="CONFIRMED", actor=ANALYST, qa_actor=ANALYST,
            notes="same person, should be refused")

    resolved = await ppv.resolve_post_promotion_finding(
        db, issue_id, decision="CONFIRMED", actor=ANALYST, qa_actor=QA,
        notes="Confirmed deactivated in NPPES; QA concurs.")
    assert resolved["resolution"] == "RESOLVED"
    assert resolved["entity_restored_to_verified"] is True
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "verified"


async def test_finding_returned_by_qa_keeps_the_entity_excluded(rolled_back_db):
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.10")
    entity_id = curated.canonical_entity_id
    out = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                   npi=NPI_VALID_OTHER, actor="SYSTEM")
    issue_id = out["issue"]["id"]

    returned = await ppv.resolve_post_promotion_finding(
        db, issue_id, decision="RETURNED", actor=QA, notes="Needs a second NPPES check.")
    assert returned["resolution"] == "UNDER_REVIEW"
    assert returned["entity_restored_to_verified"] is False
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "in_review", "still excluded until an approved disposition"


# ── entity excluded while unresolved / restored only after disposition ─────

async def test_entity_restored_only_after_the_last_blocking_finding_is_resolved(rolled_back_db):
    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.11")
    entity_id = curated.canonical_entity_id

    a = await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                                 npi=NPI_VALID_OTHER, actor="SYSTEM")
    conflict = await ppv.record_post_promotion_conflict(
        db, entity_id=entity_id, identifier_type="npi",
        submitted_value="1234567893", existing_value=NPI_VALID_OTHER, actor="SYSTEM")

    entity = await _entity(db, entity_id)
    assert entity.verification_status == "in_review"

    await ppv.resolve_post_promotion_finding(
        db, a["issue"]["id"], decision="CONFIRMED", actor=ANALYST, qa_actor=QA, notes="ok")
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "in_review", \
        "must stay excluded: the conflict finding is still unresolved"

    await ppv.resolve_post_promotion_finding(
        db, conflict["issue"]["id"], decision="CONFIRMED", actor=ANALYST, qa_actor=QA,
        notes="Verified against NPPES; delivered value confirmed.")
    entity = await _entity(db, entity_id)
    assert entity.verification_status == "verified", \
        "restored only once EVERY blocking finding is resolved"


class _FakeUser:
    def __init__(self, uid, email, role):
        self.id = uid
        self.email = email
        self.role = role


async def test_final_classification_is_refused_while_unresolved(rolled_back_db):
    """qa_gate.submit_qa_review's QA_APPROVE must refuse to finalize an entity
    that has an unresolved post-promotion blocking finding."""
    import uuid as _uuid

    from app.tefca_registry import qa_gate

    db = rolled_back_db
    _rows, _intake_id, _job, curated, _promo = await _promoted(db, "9.99.888.12")
    entity_id = curated.canonical_entity_id
    await ppv.record_finding(db, entity_id=entity_id, outcome=ppv.NPI_DEACTIVATED,
                             npi=NPI_VALID_OTHER, actor="SYSTEM")

    review = reg.ReviewRecord(
        review_id="REV-2026-900001", entity_id=entity_id,
        source_record_id=curated.source_record_id, verification_results={})
    db.add(review)
    await db.flush()

    analyst_user = _FakeUser(_uuid.uuid4(), ANALYST, "reviewer")
    qa_user = _FakeUser(_uuid.uuid4(), QA, "qalead")
    await qa_gate.record_analyst_determination(
        db, review.review_id, user=analyst_user, determination="CONFIRM",
        rationale="Reviewed against NPPES; looks fine.")

    with pytest.raises(qa_gate.QaGateRefused, match="in_review"):
        await qa_gate.submit_qa_review(
            db, review.review_id, user=qa_user, qa_action=qa_gate.E.QA_APPROVE,
            qa_reason="Concur with the analyst determination.")
    await db.refresh(review)
    assert review.reportable_at is None
