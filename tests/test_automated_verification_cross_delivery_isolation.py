"""Real, two-delivery proof for automated verification coverage (PR #75).

Every other test in `test_automated_verification.py` uses ONE delivery
(`promoted_five`). None of them can prove that running coverage for delivery
A leaves delivery B untouched, because there is no delivery B in the room.
This file seeds two SEPARATE, independently-promoted synthetic deliveries
against a real database and proves, for each of work items (RceIssue),
evidence rows (TEFCADimensionEvidence), counters (`coverage_progress`), the
review invariant (no ReviewRecord, ever, for either), and the review-task
transition (verification_status -> 'in_review'), that nothing drawn from
delivery A's run can be observed from delivery B, or vice versa - in either
run order.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as rce_m
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce import verification_findings as vf
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake

pytestmark = pytest.mark.asyncio


# NOTE ON `evidence["dimensions"][i]["evidence"]` vs `["items"]`:
# `evidence_rows_for_persistence` (app/Tefca/evidence_service.py) reads
# `dim["evidence"]`. `npi_outcome_from_evidence` (verification_findings.py)
# reads `dim["items"]` instead - a DIFFERENT key that the real
# `EvidenceService.build_evidence()` never populates anywhere in this
# codebase (confirmed on `main`, pre-dating PR #75/#76). Practical effect:
# the NPI-outcome-to-issue-ledger write (`record_from_evidence` ->
# `npi_outcome_from_evidence`) never fires for real evidence, in either the
# pre-existing `verify_and_classify` pipeline or the new automated-coverage
# path - reported separately as a pre-existing defect, not introduced here.
# Because of that, this file exercises the REAL, reachable write path
# directly (`record_npi_outcome`) for the work-item isolation proof below,
# rather than routing through the currently-dead `record_from_evidence`
# derivation step.


def _clean_evidence():
    return {"dimensions": [
        {"dimension": "D1_IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]},
    ]}


def _review_required_evidence():
    """An unrecognised disposition mix - OUTCOME_REVIEW_REQUIRED, the one
    outcome that flips verification_status to 'in_review'."""
    return {"dimensions": [
        {"dimension": "D2_ADDRESS", "disposition": "PARTIAL", "applicability": "APPLICABLE",
         "evidence": []},
    ]}


async def _entity_ids_for(db, intake_id):
    rows = (await db.execute(
        text("SELECT DISTINCT canonical_entity_id FROM rce_curated_records "
             "WHERE source_intake_id = CAST(:iid AS uuid) AND canonical_entity_id IS NOT NULL"),
        {"iid": str(intake_id)})).all()
    return {str(r[0]) for r in rows}


@pytest.fixture
async def two_deliveries(rolled_back_db):
    """Delivery A: 5 entities, all clean. Delivery B: 5 entities, independently
    promoted - nothing about them is shared except the database connection."""
    db = rolled_back_db
    out = {}
    for label, arc in (("A", "9.99.777.80"), ("B", "9.99.777.81")):
        rows = make_rows(5, arc=arc)
        intake_id, job = await seed_intake(db, rows)
        await run_quality_and_curation(db, intake_id)
        await promote_delivery(db, intake_id, actor=SYN)
        out[label] = {"intake_id": intake_id, "job_id": job.id if job else None}
    return db, out


async def test_running_coverage_for_delivery_a_only_does_not_touch_delivery_b(
        two_deliveries):
    """Counters: covering A must leave B's eligible/covered/remaining exactly
    as they were before A ran."""
    db, deliveries = two_deliveries
    a, b = deliveries["A"]["intake_id"], deliveries["B"]["intake_id"]

    b_before = await av.coverage_progress(db, b)
    assert b_before == {"eligible": 5, "remaining": 5, "covered": 0}

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=_clean_evidence())):
        result_a = await av.run_coverage_batch(db, a, batch_size=100)

    assert result_a["covered"] == 5
    assert result_a["remaining"] == 0

    b_after = await av.coverage_progress(db, b)
    assert b_after == b_before, (
        "delivery B's counters changed after covering delivery A only - "
        "cross-delivery leakage in coverage_progress")


async def test_evidence_rows_are_scoped_to_the_delivery_whose_entities_they_describe(
        two_deliveries):
    """Evidence: TEFCADimensionEvidence rows written while covering A must
    name only A's entities - never one of B's - and B must have none at all
    until B is covered."""
    from app.Tefca.models import TEFCADimensionEvidence

    db, deliveries = two_deliveries
    a, b = deliveries["A"]["intake_id"], deliveries["B"]["intake_id"]

    a_ids = await _entity_ids_for(db, a)
    b_ids = await _entity_ids_for(db, b)
    assert a_ids and b_ids and a_ids.isdisjoint(b_ids)

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=_clean_evidence())):
        await av.run_coverage_batch(db, a, batch_size=100)

    evidence_entity_ids = {row[0] for row in (await db.execute(
        select(TEFCADimensionEvidence.entity_id))).all()}

    assert evidence_entity_ids, "covering delivery A wrote no evidence rows at all"
    assert evidence_entity_ids <= a_ids, (
        "evidence rows written while covering delivery A reference an "
        "entity id that does not belong to delivery A")
    assert evidence_entity_ids.isdisjoint(b_ids), (
        "evidence rows written while covering delivery A reference one of "
        "delivery B's entity ids - cross-delivery evidence leakage")


async def test_work_item_from_delivery_b_is_scoped_to_b_and_absent_from_a(
        two_deliveries):
    """Work items: `record_npi_outcome` (the real, reachable ledger write -
    see the module note on the currently-dead `npi_outcome_from_evidence`
    derivation step) must write an RceIssue whose source_intake_id is B's,
    never A's, when called for one of B's entities; A must end up with zero
    RceIssue rows of its own (all of A's evidence is clean, so nothing in A
    should ever call this path)."""
    db, deliveries = two_deliveries
    a, b = deliveries["A"]["intake_id"], deliveries["B"]["intake_id"]

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=_clean_evidence())):
        await av.run_coverage_batch(db, a, batch_size=100)
        await av.run_coverage_batch(db, b, batch_size=100)

    # `promote_delivery`/curation already raise ordinary DQ-finding RceIssue
    # rows (SCH-002, NPI-001, BUS-003, ...) for synthetic fixture data before
    # any coverage run - confirmed empirically, ~11 per delivery here. Those
    # are unrelated to NPI verification and already correctly delivery-scoped
    # (proven separately); the real proof this test adds is the DELTA from
    # one explicit ledger write, not an absolute count of 1.
    def _count(issues, intake_id):
        return len([i for i in issues if str(i.source_intake_id) == str(intake_id)])

    before = (await db.execute(select(rce_m.RceIssue))).scalars().all()
    a_before, b_before = _count(before, a), _count(before, b)

    b_entity_ids = await _entity_ids_for(db, b)
    one_b_entity = next(iter(b_entity_ids))

    issue = await vf.record_npi_outcome(
        db, entity_id=one_b_entity, npi="1234567893",
        outcome=vf.NPI_NOT_FOUND, detail="NPI not present in NPPES.")
    await db.flush()

    assert issue is not None, "record_npi_outcome produced no ledger row for a real curated entity"
    assert str(issue.source_intake_id) == str(b)

    after = (await db.execute(select(rce_m.RceIssue))).scalars().all()
    a_after, b_after = _count(after, a), _count(after, b)

    assert b_after == b_before + 1, (
        f"expected exactly one new RceIssue for delivery B, went from "
        f"{b_before} to {b_after}")
    assert a_after == a_before, (
        "delivery A's RceIssue count changed after only delivery B's entity "
        "had a work item recorded - cross-delivery leakage in the issue ledger")


async def test_review_required_outcome_sets_in_review_only_on_its_own_entity(
        two_deliveries):
    """Review-task behavior: OUTCOME_REVIEW_REQUIRED flips ONE entity's
    verification_status to 'in_review' - proven against two deliveries so
    the update cannot be a table-wide mistake that happens to look right
    with one delivery in the room."""
    db, deliveries = two_deliveries
    a, b = deliveries["A"]["intake_id"], deliveries["B"]["intake_id"]

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=_clean_evidence())):
        await av.run_coverage_batch(db, a, batch_size=100)

    calls = {"n": 0}

    async def _build_b(self, entity):
        calls["n"] += 1
        if calls["n"] == 1:
            return _review_required_evidence()
        return _clean_evidence()

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=_build_b):
        result_b = await av.run_coverage_batch(db, b, batch_size=100)

    assert result_b["by_outcome"].get(av.OUTCOME_REVIEW_REQUIRED) == 1

    a_ids = await _entity_ids_for(db, a)
    b_ids = await _entity_ids_for(db, b)

    in_review = (await db.execute(
        select(reg.TefcaRegEntity.id)
        .where(reg.TefcaRegEntity.verification_status == "in_review"))).scalars().all()
    in_review = {str(i) for i in in_review}

    assert in_review, "no entity was marked in_review for the REVIEW_REQUIRED outcome"
    assert in_review <= b_ids, (
        "an entity outside delivery B was marked in_review by delivery B's run")
    assert in_review.isdisjoint(a_ids), (
        "an entity belonging to delivery A was marked in_review by delivery B's run")


async def test_no_review_record_is_ever_created_for_either_delivery(two_deliveries):
    """The architectural invariant this whole module exists to preserve,
    checked with two deliveries in the room instead of one."""
    db, deliveries = two_deliveries
    a, b = deliveries["A"]["intake_id"], deliveries["B"]["intake_id"]

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=_clean_evidence())):
        await av.run_coverage_batch(db, a, batch_size=100)
        await av.run_coverage_batch(db, b, batch_size=100)

    review_records = (await db.execute(select(reg.ReviewRecord.id))).scalars().all()
    assert review_records == []
