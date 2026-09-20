"""QA-039/QA-V03/QA-049/QA-051 — delivery-scoped rollups.

Two independent synthetic deliveries prove that (1) a delivery's review
counts include only cases created AGAINST that delivery, not every case for
an entity it happens to contain, and (2) QHIN placement stays canonical when
an entity is excluded from a NEW sample draw (held, or under review after a
blocking finding): the entity stays under its QHIN, its live case stays in
every progress count, and draw eligibility is reported separately.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)


class _User:
    email = "rollups@docuaction.io"
    id = None
    role = "program_manager"


async def _delivery(db, arc: str, n: int = 6):
    rows = make_rows(n, arc=arc)
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    return intake_id, job


async def _promoted_entity_ids(db, intake_id):
    from app.tefca_registry.rce import models as m

    return [row[0] for row in (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None)))).all()]


@pytest.mark.asyncio
async def test_open_items_count_only_cases_created_against_this_delivery(rolled_back_db):
    from app.tefca_registry.rce import delivery_jobs
    from app.tefca_registry.rce.delivery_routes import _review_counts

    db = rolled_back_db
    intake_a, _ = await _delivery(db, "9.99.777.80")
    entity_ids = await _promoted_entity_ids(db, intake_a)
    assert entity_ids

    before = await _review_counts(db, intake_a)
    # A case for one of A's entities that belongs to ANOTHER queue/delivery.
    foreign_intake = str(uuid.uuid4())
    db.add(reg.ReviewRecord(
        review_id=f"REV-9999-{uuid.uuid4().hex[:6].upper()}", entity_id=entity_ids[0],
        verification_results={"source_intake_id": foreign_intake, "queue_source": "priority"}))
    # And an unstamped legacy case for the same entity.
    db.add(reg.ReviewRecord(
        review_id=f"REV-9998-{uuid.uuid4().hex[:6].upper()}", entity_id=entity_ids[0],
        verification_results={}))
    await db.flush()

    after = await _review_counts(db, intake_a)
    assert after["open_work_items"] == before["open_work_items"]
    other = await delivery_jobs._review_counts(db, intake_a)
    assert other["open"] == before["open_work_items"]

    # A case created AGAINST the delivery is counted, and labelled by source.
    db.add(reg.ReviewRecord(
        review_id=f"REV-9997-{uuid.uuid4().hex[:6].upper()}", entity_id=entity_ids[1],
        verification_results={"source_intake_id": str(intake_a), "queue_source": "review_cycle"}))
    await db.flush()
    own = await _review_counts(db, intake_a)
    assert own["open_work_items"] == before["open_work_items"] + 1
    assert own["open_breakdown"].get("review_cycle") == 1


def test_review_state_labels_the_open_breakdown():
    from app.tefca_registry.rce import status_model

    state = status_model.review_state(
        outcome_code="COMPLETED_WITH_EXCEPTIONS", snapshot_passed=True,
        open_work_items=45, open_breakdown={"data_quality": 8, "review_cycle": 37})
    assert state["code"] == "READY_FOR_ANALYST_REVIEW"
    assert "45 open item(s) (8 exception work item(s) from delivery findings; " \
           "37 sampled review case(s))" in state["detail"]
    assert state["open_breakdown"] == {"data_quality": 8, "review_cycle": 37}


@pytest.mark.asyncio
async def test_qhin_placement_is_stable_when_entities_leave_the_draw_frame(rolled_back_db):
    from app.tefca_registry.qhin_sampling import resolve_qhin_strata
    from app.tefca_registry.qhin_workload import qhin_detail, qhin_rollup
    from app.tefca_registry.rce.post_promotion_verification import REVIEW_REQUIRED_STATUS

    db = rolled_back_db
    intake_id, _ = await _delivery(db, "9.99.777.81")
    # Review cases created against the delivery for two placed entities — the
    # shape `review_cycle.verify_and_classify` writes (stamped with the intake
    # and queue source), seeded directly so the test does not depend on live
    # Government-source lookups.
    entity_ids = await _promoted_entity_ids(db, intake_id)
    for entity_id in entity_ids[:2]:
        db.add(reg.ReviewRecord(
            review_id=f"REV-9996-{uuid.uuid4().hex[:6].upper()}", entity_id=entity_id,
            verification_results={"source_intake_id": str(intake_id),
                                  "queue_source": "review_cycle"}))
    await db.flush()

    base = await qhin_rollup(db, intake_id)
    population = base["totals"]["population"]
    in_review = base["totals"]["in_review"]
    assert population > 0 and in_review > 0
    assert base["draw_eligibility"]["placed"] == population

    # Put every sampled entity under review (a blocking post-promotion finding).
    sampled = [r.entity_id for r in (await db.execute(
        select(reg.ReviewRecord).where(
            reg.ReviewRecord.verification_results["source_intake_id"].astext == str(intake_id))
    )).scalars().all()]
    for entity_id in sampled:
        entity = await db.get(reg.TefcaRegEntity, entity_id)
        entity.verification_status = REVIEW_REQUIRED_STATUS
    await db.flush()

    # The sampling frame excludes them ...
    eligible, unresolved = await resolve_qhin_strata(db, intake_id)
    assert len(eligible) == population - len(sampled)
    # ... but placement, progress and the unresolved gap do not move (QA-049/051).
    after = await qhin_rollup(db, intake_id)
    assert after["totals"]["population"] == population
    assert after["totals"]["in_review"] == in_review
    assert after["unresolved"]["count"] == base["unresolved"]["count"]
    assert after["totals"]["eligible_for_draw"] == population - len(sampled)
    assert after["draw_eligibility"]["review_required"] == len(sampled)
    qhin = after["qhins"][0]
    assert qhin["excluded_from_draw"]["review_required"] >= 1
    assert "items" in after["unresolved"]

    # The QHIN drill-down still lists the cases so they can be assigned (QA-050).
    detail = await qhin_detail(db, intake_id, qhin["qhin_entity_id"])
    assert detail["population"] == qhin["population"]
    listed = [i for i in detail["items"] if i["in_review"]]
    assert len(listed) == qhin["in_review"]
    assert {i["draw_eligibility"] for i in listed} == {"review_required"}


@pytest.mark.asyncio
async def test_unplaced_records_are_drillable(rolled_back_db):
    from app.tefca_registry.qhin_workload import qhin_rollup

    db = rolled_back_db
    intake_id, _ = await _delivery(db, "9.99.777.82")
    rollup = await qhin_rollup(db, intake_id)
    # Every unresolved unit is listed with its identifier and reason (QA-047).
    for item in rollup["unresolved"]["items"]:
        assert item["reason"] and (item["rce_org_oid"] or item["entity_id"])
    assert rollup["unresolved"]["items_capped"] is False
