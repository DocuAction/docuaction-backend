"""The missing link, end to end: `review_cycle.create_review_cycle` now
persists a `ReviewCycle` row per drawn sample (it did not before
2026-09-17 — see that module's own note), a delivery-scoped report can
resolve it via `parameters.job_id`/`parameters.intake_id`, and — the point of
this file — a report for delivery A can never be generated against delivery
B's review cycle, by mismatch OR by omission.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.reports.generator import ReportParameterError, generate_report
from app.tefca_registry import models as reg
from app.tefca_registry.review_cycle import create_review_cycle, read_review_cycle
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)


class _User:
    email = "qa-isolation@docuaction.io"
    id = None
    role = "program_manager"


@pytest.fixture
async def two_deliveries(rolled_back_db):
    """Two SEPARATE, independently-promoted synthetic deliveries, each with
    its own review cycle drawn — the minimum needed to prove isolation."""
    db = rolled_back_db
    results = {}
    for label, arc in (("A", "9.99.777.70"), ("B", "9.99.777.71")):
        rows = make_rows(6, arc=arc)
        intake_id, job = await seed_intake(db, rows)
        await run_quality_and_curation(db, intake_id)
        await promote_delivery(db, intake_id, actor=SYN)
        cycle_result = await create_review_cycle(
            db, intake_id, user=_User(), review_type="quarterly",
            confidence=0.95, margin=0.10)  # wider margin -> small, fast sample
        results[label] = {
            "intake_id": intake_id, "job_id": job.id if job else None,
            "review_cycle_id": cycle_result["review_cycle_id"],
        }
    return db, results


async def test_create_review_cycle_persists_exactly_one_review_cycle_row_per_sample(
        two_deliveries):
    db, deliveries = two_deliveries
    for label in ("A", "B"):
        rows = (await db.execute(select(reg.ReviewCycle)
                                 .where(reg.ReviewCycle.id == deliveries[label]["review_cycle_id"]))
               ).scalars().all()
        assert len(rows) == 1


async def test_calling_create_review_cycle_again_does_not_create_a_second_cycle_row(
        two_deliveries):
    db, deliveries = two_deliveries
    intake_id = deliveries["A"]["intake_id"]
    again = await create_review_cycle(db, intake_id, user=_User(),
                                      review_type="quarterly", confidence=0.95, margin=0.10)
    assert again["review_cycle_id"] == deliveries["A"]["review_cycle_id"]

    all_cycles_for_sample = (await db.execute(
        select(reg.ReviewCycle).where(
            reg.ReviewCycle.sample_id.in_(
                select(reg.ReviewSample.id).where(
                    reg.ReviewSample.strata_config["source_intake_id"].astext
                    == str(intake_id)))))).scalars().all()
    assert len(all_cycles_for_sample) == 1


async def test_read_review_cycle_exposes_the_cycle_id(two_deliveries):
    db, deliveries = two_deliveries
    result = await read_review_cycle(db, deliveries["A"]["intake_id"])
    assert result["plans"][0]["review_cycle_id"] == deliveries["A"]["review_cycle_id"]


async def test_report_for_delivery_a_resolves_its_own_cycle_by_job_id_alone(two_deliveries):
    db, deliveries = two_deliveries
    result = await generate_report(
        db, report_type="verification", generated_by="qa@docuaction.io",
        persist=False, query_parameters={"job_id": str(deliveries["A"]["job_id"])})
    assert result["dataset"]["review_cycle_id"] == deliveries["A"]["review_cycle_id"]
    assert result["dataset"]["delivery"]["intake_id"] == str(deliveries["A"]["intake_id"])


async def test_report_rejects_delivery_a_paired_with_delivery_bs_review_cycle(two_deliveries):
    """THE cross-delivery hazard this fix exists to close."""
    db, deliveries = two_deliveries
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io",
            persist=False,
            query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                              "review_cycle_id": deliveries["B"]["review_cycle_id"]})
    assert exc_info.value.code == "REVIEW_CYCLE_DELIVERY_MISMATCH"
    assert exc_info.value.status == 409


async def test_report_rejects_a_delivery_with_no_review_cycle_yet(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.72")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    # No create_review_cycle call at all - matches the real 8c597e51 state.

    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io",
            persist=False, query_parameters={"job_id": str(job.id)})
    assert exc_info.value.code == "REVIEW_CYCLE_NOT_FOUND"
    assert exc_info.value.status == 422


async def test_report_with_neither_review_cycle_nor_delivery_identifier_still_gets_the_documented_all_records_default(
        rolled_back_db):
    """The intentional system-wide default (tests/test_reports.py's own
    TestRendering suite) must survive this fix untouched when NOTHING scopes
    the request — this pins that "no identifiers at all" is different from
    "a delivery identifier with no cycle yet", which IS rejected above."""
    db = rolled_back_db
    result = await generate_report(
        db, report_type="verification", generated_by="qa@docuaction.io", persist=False)
    assert result["dataset"]["review_cycle_id"] is None
    assert result["dataset"].get("delivery") is None
