"""APP-DEFECT-001, 2026-09-21: the migration-revision diagnostic must never
poison the caller's session. Report-id allocation must never silently
execute on a session someone else already broke -- and must not try to fix
that session itself, since it does not own it.

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

The migration revision is purely informational provenance -- nothing decides
anything from it -- so the fix is not a privilege grant (least privilege is
kept; DEV's database was not touched by this change).

THE FIX
-------
  1. delivery_processing_data.py._build() now reads the migration revision on
     its own throwaway session (the same isolated-session pattern
     reconciliation.py already established for the identical failure mode on
     the delivery pipeline's own long-lived session, 2026-09-17, job
     fb32f946) -- self.db is never touched by this read at all now. This is
     the fix: the caller's session is simply never put at risk in the first
     place.
  2. next_report_id() itself is deliberately left alone beyond that: it does
     not own the session it is handed, so it does not roll it back or retry
     on its behalf. Any failure -- an already-aborted session included --
     still raises the existing sanitized, fail-closed ReportIdAllocationError,
     exactly as before this defect was ever introduced. No new recovery
     behaviour was added here, on purpose.

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
from app.reports.data.report_snapshot import ReportIdAllocationError, next_report_id
from app.tefca_registry.rce.stage_events import safe_failure_text


def _dbapi_error(orig_cls_name: str, message: str) -> DBAPIError:
    """A DBAPIError wrapping a fake exception with the given class name --
    mirrors how asyncpg's real exception classes arrive wrapped."""
    FakeOrig = type(orig_cls_name, (Exception,), {})
    orig = FakeOrig(message)
    return DBAPIError.instance(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        {"k": "docuaction:report_id_sequence"}, orig, DBAPIError)


# ── genuine live-database round trip ────────────────────────────────────

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
    assert (await db.execute(text("SELECT 1"))).scalar() == 1


@pytest.mark.asyncio
async def test_next_report_id_fails_closed_on_an_already_aborted_session(
        rolled_back_db):
    """next_report_id() does not own its session and must not try to repair
    one someone else broke -- a REAL aborted-transaction state (not a mock),
    caused by an unrelated bad statement on the SAME session, exactly the
    shape of the DEV incident. It must raise ReportIdAllocationError, issue
    no id, and leave the session exactly as broken as it found it (proving
    it never called rollback on a transaction it does not own)."""
    db = rolled_back_db
    prefix = "DA-ARC-2026-"
    before = await _existing_report_ids(db, prefix)

    # Poison the session with an unrelated, deliberately invalid statement --
    # the same effect InsufficientPrivilegeError on alembic_version had.
    with pytest.raises(Exception):
        await db.execute(text("SELECT 1/0"))

    with pytest.raises(ReportIdAllocationError) as exc_info:
        await next_report_id(db, "verification")
    text_out = safe_exception_text(exc_info.value)
    assert "sequence unavailable" in str(exc_info.value)
    assert "SELECT" not in text_out
    assert "pg_advisory" not in text_out

    # The session must remain exactly as aborted as before the call -- proof
    # next_report_id() did not roll back a transaction it does not own. Any
    # query, including a verification one, still fails on this same session.
    with pytest.raises(Exception):
        await db.execute(text("SELECT 1"))

    # No partial/orphan/duplicate report was created by the failed attempt.
    # Verifying this requires ending the transaction first (the harness's own
    # cleanup, not next_report_id's) -- the assertion above already proved
    # next_report_id itself never did this.
    await db.rollback()
    after = await _existing_report_ids(db, prefix)
    assert after == before


@pytest.mark.asyncio
async def test_next_report_id_still_fails_closed_on_any_other_genuine_failure(
        rolled_back_db):
    """A failure unrelated to an aborted transaction -- any other real
    database error -- must raise ReportIdAllocationError just the same, with
    no id issued and no SQL detail leaked."""
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
async def test_next_report_id_still_succeeds_on_a_healthy_session(
        rolled_back_db):
    """Sanity check that the fail-closed path above is specific to a broken
    session, not a regression in the ordinary case: a healthy session still
    allocates a well-formed, correctly-prefixed id."""
    db = rolled_back_db
    report_id = await next_report_id(db, "verification")
    assert report_id.startswith("DA-ARC-2026-")


# ── sanitization is preserved end to end ────────────────────────────────

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
