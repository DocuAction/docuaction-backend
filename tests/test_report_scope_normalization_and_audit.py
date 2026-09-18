"""Regression coverage for the two PR #76 defects fixed 2026-09-18:

Defect A — generate_report's cross-delivery mismatch check only read the
top-level `review_cycle_id` keyword; one supplied inside `query_parameters`
was silently ignored. Fixed by `_normalize_report_scope`, the one path for
job_id/intake_id/review_cycle_id regardless of which the caller used, which
also rejects a caller naming BOTH with different values.

Defect B — `record_report_generation`'s audit write only ran for
report_type in RCE_TYPES, so a delivery-scoped `verification` report (PR
#76's own type) produced no AuditLog row at all. Fixed by calling it for
every report type, with an explicit scope_type (DELIVERY/GLOBAL) and
review_cycle_id in `details`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.database import AuditLog
from app.reports.generator import ReportParameterError, generate_report
from app.tefca_registry.review_cycle import create_review_cycle
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

pytestmark = pytest.mark.asyncio


class _User:
    email = "qa-scope@docuaction.io"
    id = None
    role = "program_manager"


@pytest.fixture
async def two_deliveries(rolled_back_db):
    db = rolled_back_db
    out = {}
    for label, arc in ("A", "9.99.777.90"), ("B", "9.99.777.91"):
        rows = make_rows(6, arc=arc)
        intake_id, job = await seed_intake(db, rows)
        await run_quality_and_curation(db, intake_id)
        await promote_delivery(db, intake_id, actor=SYN)
        cycle_result = await create_review_cycle(
            db, intake_id, user=_User(), review_type="quarterly",
            confidence=0.95, margin=0.10)
        out[label] = {"intake_id": intake_id, "job_id": job.id if job else None,
                     "review_cycle_id": cycle_result["review_cycle_id"]}
    return db, out


# ── Defect A: accepted input paths ───────────────────────────────────────────

async def test_review_cycle_id_via_top_level_field_alone_resolves_correctly(
        two_deliveries):
    db, deliveries = two_deliveries
    result = await generate_report(
        db, report_type="verification", review_cycle_id=deliveries["A"]["review_cycle_id"],
        generated_by="qa@docuaction.io", persist=False,
        query_parameters={"job_id": str(deliveries["A"]["job_id"])})
    assert result["dataset"]["review_cycle_id"] == deliveries["A"]["review_cycle_id"]


async def test_review_cycle_id_via_nested_parameters_alone_resolves_correctly(
        two_deliveries):
    """THE compatibility path Defect A adds: review_cycle_id nested inside
    query_parameters, no top-level keyword at all."""
    db, deliveries = two_deliveries
    result = await generate_report(
        db, report_type="verification", generated_by="qa@docuaction.io", persist=False,
        query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                          "review_cycle_id": deliveries["A"]["review_cycle_id"]})
    assert result["dataset"]["review_cycle_id"] == deliveries["A"]["review_cycle_id"]


async def test_same_review_cycle_id_in_both_locations_is_not_a_conflict(
        two_deliveries):
    db, deliveries = two_deliveries
    result = await generate_report(
        db, report_type="verification", review_cycle_id=deliveries["A"]["review_cycle_id"],
        generated_by="qa@docuaction.io", persist=False,
        query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                          "review_cycle_id": deliveries["A"]["review_cycle_id"]})
    assert result["dataset"]["review_cycle_id"] == deliveries["A"]["review_cycle_id"]


# ── Defect A: conflicting identifiers ────────────────────────────────────────

async def test_conflicting_top_level_and_nested_review_cycle_id_is_rejected_422(
        two_deliveries):
    db, deliveries = two_deliveries
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", review_cycle_id=deliveries["A"]["review_cycle_id"],
            generated_by="qa@docuaction.io", persist=False,
            query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                              "review_cycle_id": deliveries["B"]["review_cycle_id"]})
    assert exc_info.value.code == "REVIEW_CYCLE_IDENTIFIER_CONFLICT"
    assert exc_info.value.status == 422


# ── Defect A: cross-delivery combinations (both input locations) ────────────

async def test_delivery_a_job_id_with_nested_delivery_b_review_cycle_id_is_rejected(
        two_deliveries):
    """THE originally-failing scenario
    (test_report_rejects_delivery_a_paired_with_delivery_bs_review_cycle),
    exercised via the nested-parameters path specifically - now caught."""
    db, deliveries = two_deliveries
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io", persist=False,
            query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                              "review_cycle_id": deliveries["B"]["review_cycle_id"]})
    assert exc_info.value.code == "REVIEW_CYCLE_DELIVERY_MISMATCH"
    assert exc_info.value.status == 409


async def test_delivery_a_job_id_with_delivery_b_intake_id_is_rejected(two_deliveries):
    db, deliveries = two_deliveries
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io", persist=False,
            query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                              "intake_id": str(deliveries["B"]["intake_id"])})
    assert exc_info.value.code == "DELIVERY_IDENTIFIER_MISMATCH"
    assert exc_info.value.status == 422


# ── Defect A: nonexistent identifiers ────────────────────────────────────────

async def test_nonexistent_review_cycle_id_via_nested_parameters_is_rejected(
        two_deliveries):
    db, deliveries = two_deliveries
    ghost = str(uuid.uuid4())
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io", persist=False,
            query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                              "review_cycle_id": ghost})
    assert exc_info.value.code == "REVIEW_CYCLE_DELIVERY_MISMATCH"
    assert exc_info.value.status == 409


# ── Defect A: silent-fallback prevention ─────────────────────────────────────

async def test_conflicting_identifiers_never_fall_through_to_any_result(
        two_deliveries):
    """A rejected request must raise - never return delivery-scoped OR
    global data."""
    db, deliveries = two_deliveries
    with pytest.raises(ReportParameterError):
        await generate_report(
            db, report_type="verification", review_cycle_id=deliveries["A"]["review_cycle_id"],
            generated_by="qa@docuaction.io", persist=False,
            query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                              "review_cycle_id": deliveries["B"]["review_cycle_id"]})


# ── Defect B: successful auditing, scope-explicit ────────────────────────────

async def test_delivery_scoped_report_writes_one_audit_row_marked_delivery(
        two_deliveries):
    db, deliveries = two_deliveries
    before = set((await db.execute(select(AuditLog.id))).scalars().all())

    result = await generate_report(
        db, report_type="verification", generated_by="qa-audit@docuaction.io",
        generated_by_id=None, persist=True,
        query_parameters={"job_id": str(deliveries["A"]["job_id"])})

    after = set((await db.execute(select(AuditLog.id))).scalars().all())
    new_ids = after - before
    assert len(new_ids) == 1, f"expected exactly one new audit row, got {len(new_ids)}"

    audit = await db.get(AuditLog, new_ids.pop())
    d = audit.details or {}
    assert d["scope_type"] == "DELIVERY"
    assert d["job_id"] == str(deliveries["A"]["job_id"])
    assert d["intake_id"] == str(deliveries["A"]["intake_id"])
    assert d["review_cycle_id"] == deliveries["A"]["review_cycle_id"]
    assert d["report_type"] == "verification"
    assert d["actor"] == "qa-audit@docuaction.io"
    assert audit.correlation_id is not None
    assert audit.created_at is not None
    assert audit.outcome == "success"
    assert audit.resource_id == result["report_id"]
    # No dataset/HTML/CSV content in the audit row.
    assert "html" not in d and "csv" not in d and "dataset" not in d


async def test_global_report_writes_one_audit_row_marked_global(rolled_back_db):
    db = rolled_back_db
    before = set((await db.execute(select(AuditLog.id))).scalars().all())

    await generate_report(
        db, report_type="verification", generated_by="qa-audit@docuaction.io",
        persist=True, query_parameters=None)

    after = set((await db.execute(select(AuditLog.id))).scalars().all())
    new_ids = after - before
    assert len(new_ids) == 1

    audit = await db.get(AuditLog, new_ids.pop())
    d = audit.details or {}
    assert d["scope_type"] == "GLOBAL"
    assert d["job_id"] is None and d["intake_id"] is None
    assert d["review_cycle_id"] is None


# ── Defect B: mismatch rejection creates no success audit event ─────────────

async def test_rejected_mismatch_creates_no_audit_row(two_deliveries):
    db, deliveries = two_deliveries
    before = set((await db.execute(select(AuditLog.id))).scalars().all())

    with pytest.raises(ReportParameterError):
        await generate_report(
            db, report_type="verification", generated_by="qa-audit@docuaction.io",
            persist=True,
            query_parameters={"job_id": str(deliveries["A"]["job_id"]),
                              "review_cycle_id": deliveries["B"]["review_cycle_id"]})

    after = set((await db.execute(select(AuditLog.id))).scalars().all())
    assert after == before, "a rejected (mismatched) request must never write a success audit event"


# ── Defect B: retry behavior ──────────────────────────────────────────────────

async def test_two_legitimate_generations_each_get_their_own_audit_row(
        two_deliveries):
    """Not idempotent, by design (matches every other write in this module):
    two real calls are two real reports, and each gets its own audit row -
    this is NOT the "misleading duplicate" the requirement warns about,
    which is two rows for ONE call."""
    db, deliveries = two_deliveries
    before = set((await db.execute(select(AuditLog.id))).scalars().all())

    r1 = await generate_report(
        db, report_type="verification", generated_by="qa-audit@docuaction.io",
        persist=True, query_parameters={"job_id": str(deliveries["A"]["job_id"])})
    r2 = await generate_report(
        db, report_type="verification", generated_by="qa-audit@docuaction.io",
        persist=True, query_parameters={"job_id": str(deliveries["A"]["job_id"])})

    after = set((await db.execute(select(AuditLog.id))).scalars().all())
    new_ids = after - before
    assert len(new_ids) == 2
    assert r1["report_id"] != r2["report_id"]
    resource_ids = {(await db.get(AuditLog, i)).resource_id for i in new_ids}
    assert resource_ids == {r1["report_id"], r2["report_id"]}


# ── Defect B: cross-delivery isolation of the audit trail ───────────────────

async def test_delivery_b_audit_row_never_references_delivery_a(two_deliveries):
    db, deliveries = two_deliveries

    await generate_report(
        db, report_type="verification", generated_by="qa-audit@docuaction.io",
        persist=True, query_parameters={"job_id": str(deliveries["B"]["job_id"])})

    rows = (await db.execute(select(AuditLog))).scalars().all()
    b_rows = [r for r in rows if (r.details or {}).get("job_id") == str(deliveries["B"]["job_id"])]
    assert b_rows
    for r in b_rows:
        d = r.details or {}
        assert d.get("job_id") != str(deliveries["A"]["job_id"])
        assert d.get("intake_id") != str(deliveries["A"]["intake_id"])
        assert d.get("review_cycle_id") != deliveries["A"]["review_cycle_id"]
