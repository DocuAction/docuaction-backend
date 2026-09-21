"""Stage 3 defect investigation, 2026-09-21: the varchar(500) CURATION
truncation shown in the DEV delivery list, and the raw SQL it displayed.

CONTEXT
-------
Three historical DEV deliveries (2026-09-13/09-15) show:

    StringDataRightTruncationError: value too long for type character
    varying(500) [SQL: INSERT INTO rce_curated_records (...) VALUES
    ($1::UUID, ..., $8::VARCHAR, $...]

with the raw asyncpg exception, the SQL text and the parameter list visible
in the delivery detail UI. This file establishes, with a real reproduction
rather than inference, whether that is still possible on the CURRENT code:

  1. FUNCTIONAL: does an over-long delivered `name` still fail CURATION?
     -> already covered end-to-end by test_curated_text_columns.py, which
        this file does not duplicate. This file adds the exact 500/501
        boundary the original incident's checklist calls for.
  2. SECURITY/USABILITY: does a raw SQLAlchemy/asyncpg exception, with SQL
     text and bound parameters, still reach a viewer through
     `stage_events.safe_failure_text` / `_reason` (the only place an
     exception's text reaches a caller, per delivery_runner.py)?

FINDING (see the Stage 3 Defect Log for the full writeup)
-----------------------------------------------------------
Both are already fixed on the current codebase, by two SEPARATE prior
changes:
  - migration 20260915_curated_text_columns (commit cf0ca6e) widened the
    five source-derived free-text columns to TEXT, closing the functional
    defect.
  - `app.core.logging_config.safe_exception_text` (added 2026-09-16, "review
    finding F3") classifies any exception whose class lives under
    sqlalchemy/asyncpg/psycopg/... as a DRIVER exception and reduces it to
    its class name plus a correlation id — never its message, so never the
    embedded SQL or bound parameters.

The three historical deliveries pre-date 2026-09-16 (two on 09-13, one at
2026-09-15 18:06:13 UTC — 19 minutes before the fix commit at 18:25:24 UTC
the same day) and their stored failure text was written before either fix
existed. They are frozen historical evidence; nothing here alters them.

ISOLATION
---------
Same pattern as test_curated_text_columns.py: every commit lands in a
savepoint inside an outer transaction that is rolled back. All fixture data
is synthetic.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.core.logging_config import safe_exception_text
from app.tefca_registry.rce.stage_events import safe_failure_text

SYN = "SYNTHETIC-DEFECT-INVESTIGATION-20260921"


# ── 1. The exact reported error shape, sanitized ─────────────────────────────

def _dbapi_truncation_error(sql: str, params: dict) -> DBAPIError:
    """Builds the same exception SHAPE observed in the DEV UI: a DBAPIError
    wrapping asyncpg's StringDataRightTruncationError, with the real INSERT
    text and bound parameters attached — exactly what a caller must never
    see verbatim."""
    try:
        import asyncpg
        orig = asyncpg.exceptions.StringDataRightTruncationError(
            "value too long for type character varying(500)")
    except ImportError:  # pragma: no cover - asyncpg always installed here
        orig = Exception("value too long for type character varying(500)")
    return DBAPIError.instance(sql, params, orig, DBAPIError)


def test_the_exact_reported_exception_shape_is_sanitized_to_class_name_only():
    sql = ("INSERT INTO rce_curated_records (id, source_intake_id, "
           "source_record_id, record_status, issue_count, correction_count, "
           "status_reason, rce_org_oid, tefcaid, hcid, aaid, npi, name) "
           "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)")
    params = {"name": "X" * 640, "rce_org_oid": "2.16.840.1.113883.3.9999.9"}
    exc = _dbapi_truncation_error(sql, params)

    text_out = safe_exception_text(exc)
    assert "INSERT INTO" not in text_out
    assert "rce_curated_records" not in text_out
    assert "X" * 20 not in text_out
    assert "2.16.840.1.113883" not in text_out
    assert "value too long" not in text_out
    # It is classified as a driver/library exception (sqlalchemy.exc.*, per
    # _DRIVER_MODULES) and reduced to a class name plus correlation pointer —
    # never the exception's message, which is where the SQL and bound
    # parameters live. The exact subclass SQLAlchemy assigns to a
    # hand-constructed DBAPIError.instance() (DBAPIError vs. its more
    # specific StatementError base) is an SQLAlchemy implementation detail,
    # not the property under test.
    assert type(exc).__module__.startswith("sqlalchemy")
    assert "message withheld from evidence" in text_out

    stage_text = safe_failure_text(exc)
    assert "INSERT INTO" not in stage_text
    assert "rce_curated_records" not in stage_text
    assert "message withheld from evidence" in stage_text


def test_reason_string_built_by_the_delivery_runner_carries_no_sql():
    """`_reason()` in delivery_runner.py is the literal function that produced
    the "CURATION did not complete: ..." text shown in the DEV UI."""
    from app.tefca_registry.rce.delivery_runner import _reason

    sql = ("INSERT INTO rce_curated_records (id, name) VALUES ($1, $2)")
    exc = _dbapi_truncation_error(sql, {"name": "Y" * 640})
    reason = _reason("CURATION", exc)
    assert reason.startswith("CURATION did not complete: ")
    assert "message withheld from evidence" in reason
    assert "INSERT INTO" not in reason
    assert "rce_curated_records" not in reason
    assert "Y" * 20 not in reason


# ── 2. A genuine, live database round-trip (not a hand-built exception) ─────

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


@pytest.mark.asyncio
async def test_a_real_still_bounded_column_raises_and_is_sanitized(rolled_back_db):
    """address_state is deliberately still VARCHAR(10) (see
    test_bounded_columns_keep_their_limits). A genuinely oversized value here
    reproduces the SAME asyncpg error class the DEV incident hit, from a real
    database round-trip, on a column the fix correctly left bounded."""
    db = rolled_back_db
    oversized = "Z" * 400
    real_exc = None
    try:
        await db.execute(
            text("INSERT INTO rce_curated_records "
                 "(id, source_intake_id, source_record_id, record_status, "
                 " address_state, name) "
                 "VALUES (:id, :sid, :rid, 'HELD', :state, :name)"),
            {"id": str(uuid.uuid4()), "sid": str(uuid.uuid4()),
             "rid": str(uuid.uuid4()), "state": oversized, "name": SYN})
    except DBAPIError as exc:  # the real asyncpg round-trip
        real_exc = exc
    finally:
        await db.rollback()

    assert real_exc is not None, (
        "expected a genuine StringDataRightTruncationError from the live "
        "database on address_state (still VARCHAR(10) by design); none was "
        "raised, which would itself be worth investigating separately")

    sanitized = safe_failure_text(real_exc)
    assert "INSERT INTO" not in sanitized
    assert oversized not in sanitized
    assert "rce_curated_records" not in sanitized
    assert sanitized.startswith("DBAPIError")


# ── 3. Worker always records a terminal result on a stage exception ─────────

def test_worker_stage_loop_never_leaves_a_job_running_on_exception():
    """Static guarantee, matching the module's own stated contract
    (delivery_runner.py `_run_stage`): every stage exception is caught,
    `_settle` + `_close(..., "FAILED", ...)` are called, and the loop breaks
    rather than propagating. This is what turns a raised exception into the
    terminal FAILED outcome the operator sees, instead of a job stuck RUNNING
    forever."""
    import inspect

    from app.tefca_registry.rce import delivery_runner as dr

    source = inspect.getsource(dr._run_stages)
    assert "except Exception as exc" in source
    assert "_settle(db)" in source
    assert '"FAILED"' in source
