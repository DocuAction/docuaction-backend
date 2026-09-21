"""APP-DEFECT-001 / APP-DEFECT-002, 2026-09-21: the migration-revision
diagnostic must never poison the caller's session, and report-id allocation
must never silently execute — or silently fail — on a session someone else
already broke.

CONTEXT
-------
Real DEV App Service logs (request id 133964d0-c24e-4e95-a5f1-fcd7aad2b339,
and four others the same day) showed this exact sequence on one request:

    app.reports.data.delivery_processing_data: "alembic_version unreadable:
        ... InsufficientPrivilegeError: permission denied for table
        alembic_version" (caught, logged at INFO, NOT rolled back)
    app.reports.data.report_snapshot: "report id allocation FAILED:
        DBAPIError: ... InFailedSQLTransactionError: current transaction is
        aborted" (next_report_id's own advisory-lock query, on the SAME
        now-poisoned session)

The migration revision is purely informational provenance — nothing decides
anything from it — so the fix is not a privilege grant (least privilege is
kept; DEV's database was not touched by this change). The two fixes:

  1. delivery_processing_data.py._build() now reads the migration revision on
     its own throwaway session (the same isolated-session pattern
     reconciliation.py already established for the identical failure mode on
     the delivery pipeline's own long-lived session, 2026-09-17, job
     fb32f946) — self.db is never touched by this read at all now.
  2. report_snapshot.py.next_report_id() detects the specific
     InFailedSQLTransactionError signature, rolls back, and retries its own
     work exactly once — recovering from a session some OTHER, unrelated
     statement broke, without ever silently allocating an id on a broken
     session and without masking a genuine allocation failure.

ISOLATION
---------
Same pattern as test_curated_text_columns.py / test_varchar500_curation_
defect_2026_09_21.py: every commit lands in a savepoint inside an outer
transaction that is rolled back. No DEV/PROD access; no privilege grants
issued anywhere, including on this disposable instance.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.core.logging_config import safe_exception_text
from app.reports.data.report_snapshot import (
    ReportIdAllocationError,
    _is_aborted_transaction,
    next_report_id,
)
from app.tefca_registry.rce.stage_events import safe_failure_text


# ── 1. _is_aborted_transaction: the exact reported exception shape ─────────

def _dbapi_error(orig_cls_name: str, message: str) -> DBAPIError:
    """A DBAPIError wrapping a fake exception with the given class name --
    mirrors how asyncpg's real exception classes arrive wrapped."""
    FakeOrig = type(orig_cls_name, (Exception,), {})
    orig = FakeOrig(message)
    return DBAPIError.instance(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        {"k": "docuaction:report_id_sequence"}, orig, DBAPIError)


def test_is_aborted_transaction_recognises_the_exact_reported_signature():
    exc = _dbapi_error("InFailedSQLTransactionError",
                       "current transaction is aborted, commands ignored "
                       "until end of transaction block")
    assert _is_aborted_transaction(exc) is True


def test_is_aborted_transaction_is_false_for_other_failures():
    exc = _dbapi_error("OperationalError", "connection refused")
    assert _is_aborted_transaction(exc) is False
    assert _is_aborted_transaction(ValueError("unrelated")) is False


# ── 2. genuine live-database round trip ──────────────────────────────────

@pytest.fixture
async def rolled_back_db(db_required):
    engine = create_async_engine(
        _normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    connection = await engine.connect()
    outer = await connection.begin()
    session = AsyncSession(bind=connection,
                           join_transaction_mode="create_savepoint",
                           expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()
        await connection.close()
        await engine.dispose()


async def _existing_report_ids(db, prefix: str) -> set:
    rows = (await db.execute(
        text("SELECT report_id FROM review_reports WHERE report_id LIKE :p"),
        {"p": f"{prefix}%"})).scalars().all()
    return set(rows)


@pytest.mark.asyncio
async def test_migration_revision_isolated_never_touches_the_callers_session(
        rolled_back_db):
    """delivery_processing_data._build() must not require a working self.db
    for the migration-revision field at all -- it now runs on its own
    session. Proven by calling the real function and then immediately
    running a real, unrelated query on the SAME session."""
    from app.reports.data.delivery_processing_data import DeliveryProcessingDataService

    db = rolled_back_db
    service = DeliveryProcessingDataService(db)
    build = await service._build()
    assert build["migration_revision"]  # a real value, or "unknown" -- never raises

    # The session must still be fully usable for real, unrelated work.
    year = (await db.execute(text("SELECT 1"))).scalar()
    assert year == 1


@pytest.mark.asyncio
async def test_next_report_id_recovers_from_a_genuinely_aborted_session(
        rolled_back_db):
    """A REAL aborted-transaction state (not a mock), caused by an unrelated
    bad statement on the SAME session -- exactly the shape of the DEV
    incident, minus needing an actual privilege revoke to produce it."""
    db = rolled_back_db
    year = 2026
    prefix = f"DA-ARC-{year}-"
    before = await _existing_report_ids(db, prefix)

    # Poison the session with an unrelated, deliberately invalid statement --
    # the same effect InsufficientPrivilegeError on alembic_version had.
    with pytest.raises(Exception):
        await db.execute(text("SELECT 1/0"))

    # A query issued directly on this still-aborted session must fail --
    # confirms the poisoning actually happened, not a no-op.
    with pytest.raises(Exception):
        await db.execute(text("SELECT 1"))
    await db.rollback()  # test-harness cleanup only; next_report_id does not need this

    # Poison it again, THIS time hand it straight to next_report_id().
    with pytest.raises(Exception):
        await db.execute(text("SELECT 1/0"))
    report_id = await next_report_id(db, "verification", now=None)

    assert report_id.startswith(prefix)
    after = await _existing_report_ids(db, prefix)
    assert after == before, "allocation must not itself create a report row"

    # The session must remain usable afterward -- prove with a real query.
    assert (await db.execute(text("SELECT 1"))).scalar() == 1


@pytest.mark.asyncio
async def test_next_report_id_still_fails_closed_on_a_genuine_failure(
        rolled_back_db):
    """Not every failure is a recoverable aborted transaction. An exception
    that is NOT the InFailedSQLTransactionError signature -- a real,
    unrelated database failure -- must raise ReportIdAllocationError on the
    first attempt, with no retry and no id issued."""
    db = rolled_back_db

    async def _boom(*args, **kwargs):
        raise _dbapi_error("OperationalError",
                           "server closed the connection unexpectedly")

    db.execute = _boom  # instance-level override; this session is discarded after the test
    with pytest.raises(ReportIdAllocationError) as exc_info:
        await next_report_id(db, "verification")
    text_out = safe_exception_text(exc_info.value)
    assert "sequence unavailable" in str(exc_info.value)
    assert "SELECT" not in text_out
    assert "pg_advisory" not in text_out


@pytest.mark.asyncio
async def test_two_recoveries_in_a_row_do_not_allocate_the_same_id(
        rolled_back_db):
    """Sequential recoveries (two separate poisoned-then-recovered calls, as
    two different requests would look) must not collide. next_report_id()
    only computes the next id -- it does not reserve it -- so the id only
    advances once a report row is actually persisted with it, exactly as the
    real report-generation flow does between allocating and inserting."""
    from app.tefca_registry import models as reg

    db = rolled_back_db
    with pytest.raises(Exception):
        await db.execute(text("SELECT 1/0"))
    first = await next_report_id(db, "verification")
    db.add(reg.ReviewReport(report_id=first, report_type="verification"))
    # commit (not just flush): next_report_id's own rollback-and-retry, on the
    # SECOND poison below, must not also discard this row -- exactly as a real
    # request commits the allocated report before any later request runs.
    await db.commit()

    with pytest.raises(Exception):
        await db.execute(text("SELECT 1/0"))
    second = await next_report_id(db, "verification")

    assert first != second


# ── 3. sanitization is preserved end to end ─────────────────────────────

def test_report_id_allocation_error_message_never_carries_sql():
    exc = ReportIdAllocationError(
        "report id sequence unavailable (InFailedSQLTransactionError); no id issued")
    text_out = safe_exception_text(exc)
    stage_text = safe_failure_text(exc)
    for surface in (text_out, stage_text):
        assert "SELECT" not in surface
        assert "pg_advisory_xact_lock" not in surface
        assert "docuaction:report_id_sequence" not in surface
    assert "sequence unavailable" in text_out
