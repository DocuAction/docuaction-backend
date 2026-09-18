"""PR #76 validation: the required behaviors not already covered by
tests/test_review_cycle_report_scope_isolation.py, against a real database.

Cross-tenant access is NOT tested here. Confirmed and documented separately
(see the PR #76 evidence report): no model in this domain carries a
tenant_id column, `app.core.tenant`'s enforcement code has zero references
anywhere under app/tefca_registry/, app/reports/ or app/Tefca/, and the only
place a real user is ever issued a tenant_id hardcodes "default"
(app/api/routes.py). This is a single-tenant, global-role deployment by
current design, not a PR #76 gap - fabricating a tenant test here would
misrepresent a boundary that does not exist.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.database import AuditLog
from app.reports.generator import ReportParameterError, generate_report
from app.tefca_registry import models as reg
from app.tefca_registry.review_cycle import create_review_cycle, read_review_cycle
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

pytestmark = pytest.mark.asyncio


class _User:
    email = "qa-lifecycle@docuaction.io"
    id = None
    role = "program_manager"


@pytest.fixture
async def one_delivery(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(6, arc="9.99.777.85")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    cycle_result = await create_review_cycle(
        db, intake_id, user=_User(), review_type="quarterly",
        confidence=0.95, margin=0.10)
    return db, {"intake_id": intake_id, "job_id": job.id if job else None,
               "review_cycle_id": cycle_result["review_cycle_id"]}


# ── 1. Reject nonexistent review cycles ──────────────────────────────────────

async def test_rejects_a_review_cycle_id_that_does_not_exist_at_all(one_delivery):
    """review_cycle_id supplied ALONE (no delivery identifier), syntactically
    valid, but naming no row that exists anywhere."""
    db, delivery = one_delivery
    ghost = str(uuid.uuid4())
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io",
            persist=False, review_cycle_id=ghost)
    assert exc_info.value.code == "REVIEW_CYCLE_NOT_FOUND"
    assert exc_info.value.status == 404


# ── 2. Reject deliveries without a valid intake ──────────────────────────────

async def test_rejects_a_job_id_that_does_not_exist(rolled_back_db):
    db = rolled_back_db
    ghost_job = str(uuid.uuid4())
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io",
            persist=False, query_parameters={"job_id": ghost_job})
    assert exc_info.value.code == "DELIVERY_NOT_FOUND"
    assert exc_info.value.status == 404


async def test_rejects_an_intake_id_that_does_not_exist(rolled_back_db):
    db = rolled_back_db
    ghost_intake = str(uuid.uuid4())
    with pytest.raises(ReportParameterError) as exc_info:
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io",
            persist=False, query_parameters={"intake_id": ghost_intake})
    assert exc_info.value.code == "DELIVERY_NOT_FOUND"
    assert exc_info.value.status == 404


# ── 3. Prevent silent fallback to system-wide data from a rejected request ──

async def test_a_rejected_delivery_scoped_request_never_falls_through_to_all_records(
        rolled_back_db):
    """The documented all-records default is reachable ONLY by naming
    neither a delivery nor a review cycle (see the passing
    test_report_with_neither_review_cycle_nor_delivery_identifier_... in the
    sibling file). Every rejection path here must raise - it must never
    return a result at all, delivery-scoped or otherwise."""
    db = rolled_back_db
    for bad_params in (
        {"job_id": str(uuid.uuid4())},          # DELIVERY_NOT_FOUND
        {"intake_id": str(uuid.uuid4())},        # DELIVERY_NOT_FOUND
    ):
        with pytest.raises(ReportParameterError):
            await generate_report(
                db, report_type="verification", generated_by="qa@docuaction.io",
                persist=False, query_parameters=bad_params)
    with pytest.raises(ReportParameterError):
        await generate_report(
            db, report_type="verification", generated_by="qa@docuaction.io",
            persist=False, review_cycle_id=str(uuid.uuid4()))


# ── 4. Report generation audit logging ───────────────────────────────────────

async def test_delivery_scoped_verification_report_generation_is_audit_logged(
        one_delivery):
    """Required: an audit row with scope, actor, delivery, review-cycle id,
    timestamp and correlation id for a real delivery-scoped report
    generation. Run with persist=True (the real, non-test calling
    convention) so the actual production write path executes, not a
    shortcut."""
    db, delivery = one_delivery

    before = (await db.execute(select(AuditLog.id))).scalars().all()

    result = await generate_report(
        db, report_type="verification", generated_by="qa-audit@docuaction.io",
        persist=True, query_parameters={"job_id": str(delivery["job_id"])})

    after = (await db.execute(select(AuditLog.id))).scalars().all()
    new_rows = [r for r in after if r not in before]

    assert new_rows, (
        "generate_report(report_type='verification', ...) with a real "
        "delivery scope produced NO AuditLog row at all - "
        "record_report_generation's audit write is gated `if report_type in "
        "RCE_TYPES` (data_quality, intake, delivery_processing only), and "
        "'verification' is not one of them, so PR #76's own delivery-scoped "
        "report type is never audit-logged by this mechanism")

    audit = await db.get(AuditLog, new_rows[0])
    assert audit.action is not None                                    # actor's action
    assert audit.user_id is not None or (audit.details or {}).get("actor")  # actor
    assert (audit.details or {}).get("job_id") == str(delivery["job_id"])   # delivery
    assert (audit.details or {}).get("intake_id") == str(delivery["intake_id"])  # delivery
    assert audit.correlation_id is not None                             # correlation id
    assert audit.created_at is not None                                 # timestamp
    assert (audit.details or {}).get("review_cycle_id") == delivery["review_cycle_id"], (
        "AuditLog.details carries no review_cycle_id key at all - "
        "record_report_generation's `details` dict does not include it "
        f"(actual details keys: {sorted((audit.details or {}).keys())})")
