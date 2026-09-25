"""Automated verification coverage: load protection for the scheduler tick
(2026-09-25 remediation).

On DEV the candidate query ran for 34 minutes and copies of it overlapped
across container recycles, pinning the database at 100% CPU. Pinned here:

  * single flight across processes: when another connection holds the
    advisory lock the tick returns without looking for work or running a
    batch, and the lock is released again after a tick that did run;
  * the candidate lookup is bounded: the job candidate set carries a LIMIT,
    each candidate is probed with EXISTS, and a statement timeout is set on
    the lookup's transaction;
  * a lookup that exceeds the timeout is caught, logged, marks nothing and
    leaves the session usable;
  * the bounded lookup still finds a succeeded delivery with a promoted
    record that has no evidence row, and stops naming it once covered.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import rolled_back_db  # noqa: F401
from test_automated_verification import promoted_five  # noqa: F401

pytestmark = pytest.mark.asyncio


# ── pure ─────────────────────────────────────────────────────────────────────

def test_candidate_query_is_bounded_and_probes_with_exists():
    sql = av._ONE_DELIVERY_NEEDING_COVERAGE_SQL
    assert "LIMIT :job_limit" in sql                       # the job candidate set
    assert re.search(r"WHERE EXISTS \(", sql)               # one probe per candidate
    assert "NOT EXISTS" in sql and "CAST(r.canonical_entity_id AS TEXT)" in sql
    assert "SELECT DISTINCT" not in sql                     # never a scan of the population
    assert "j.state = 'SUCCEEDED'" in sql
    assert av.CANDIDATE_JOB_LIMIT == 25


def test_candidate_timeout_reads_env_with_a_safe_default(monkeypatch):
    monkeypatch.delenv(av.ENV_CANDIDATE_TIMEOUT_MS, raising=False)
    assert av.candidate_timeout_ms() == av.DEFAULT_CANDIDATE_TIMEOUT_MS == 15_000
    monkeypatch.setenv(av.ENV_CANDIDATE_TIMEOUT_MS, "30000")
    assert av.candidate_timeout_ms() == 30_000
    monkeypatch.setenv(av.ENV_CANDIDATE_TIMEOUT_MS, "5")        # clamped up
    assert av.candidate_timeout_ms() == av.MIN_CANDIDATE_TIMEOUT_MS
    monkeypatch.setenv(av.ENV_CANDIDATE_TIMEOUT_MS, "99999999")  # clamped down
    assert av.candidate_timeout_ms() == av.MAX_CANDIDATE_TIMEOUT_MS
    monkeypatch.setenv(av.ENV_CANDIDATE_TIMEOUT_MS, "soon")      # not an int
    assert av.candidate_timeout_ms() == av.DEFAULT_CANDIDATE_TIMEOUT_MS


def test_lock_key_is_a_fixed_signed_bigint():
    assert isinstance(av.COVERAGE_TICK_LOCK_KEY, int)
    assert 0 < av.COVERAGE_TICK_LOCK_KEY < 2 ** 63


# ── database ─────────────────────────────────────────────────────────────────

class _Statements:
    def __init__(self, db):
        self.engine = db.sync_session.get_bind().engine
        self.seen = []

    def _listener(self, conn, cursor, statement, parameters, context, executemany):
        self.seen.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._listener)


async def test_statement_timeout_is_set_locally_before_the_lookup(rolled_back_db, monkeypatch):
    db = rolled_back_db
    monkeypatch.setenv(av.ENV_CANDIDATE_TIMEOUT_MS, "12000")
    with _Statements(db) as s:
        await av._next_delivery_needing_coverage(db)
    timeouts = [i for i, stmt in enumerate(s.seen)
                if stmt.strip().upper().startswith("SET LOCAL STATEMENT_TIMEOUT")]
    lookups = [i for i, stmt in enumerate(s.seen) if "LIMIT $1" in stmt or "job_limit" in stmt
               or "rce_delivery_jobs" in stmt]
    assert timeouts and lookups, s.seen
    assert "12000" in s.seen[timeouts[0]]
    assert timeouts[0] < lookups[0], "the timeout must be in force before the lookup runs"
    # The lookup's transaction is ended afterwards, so SET LOCAL is gone and
    # the session is not left carrying the bound into the batch.
    assert (await db.execute(text("SHOW statement_timeout"))).scalar() != "12s"


async def test_a_cancelled_lookup_is_caught_marks_nothing_and_leaves_the_session_usable(
        rolled_back_db, monkeypatch, caplog):
    db = rolled_back_db
    # A typed parameter: asyncpg prepares the statement server-side and must
    # infer a type for every bind, exactly as the real lookup's LIMIT does.
    monkeypatch.setattr(av, "_ONE_DELIVERY_NEEDING_COVERAGE_SQL",
                        "SELECT pg_sleep(5), CAST(:job_limit AS int)")
    with caplog.at_level("WARNING", logger=av.logger.name):
        result = await av._next_delivery_needing_coverage(db, timeout_ms=1000)
    assert result is None
    assert any("cancelled" in rec.getMessage() for rec in caplog.records), caplog.records
    assert (await db.execute(text("SELECT 1"))).scalar() == 1


async def test_the_bounded_lookup_finds_a_succeeded_delivery_needing_coverage(promoted_five):
    db, intake_id = promoted_five
    job = (await db.execute(
        text("SELECT id FROM rce_delivery_jobs WHERE source_intake_id = CAST(:i AS uuid)"),
        {"i": str(intake_id)})).scalar()
    assert job is not None
    row = await db.get(RceDeliveryJob, job)
    # Newest succeeded delivery by a wide margin, whatever else the shared
    # database holds, so the newest-first bound cannot exclude it.
    row.state = RceDeliveryJob.STATE_SUCCEEDED
    row.active_marker = None
    row.completed_at = datetime(2999, 1, 1)
    await db.commit()

    assert await av._next_delivery_needing_coverage(db) == intake_id

    # Cover every promoted entity with one evidence row each — the exact
    # "attempted" definition the lookup's NOT EXISTS uses — written directly
    # so this proof does not depend on the batch's evidence pipeline.
    await db.execute(text("""
        INSERT INTO tefca_dimension_evidence
            (id, entity_id, evidence_dimension, source, disposition)
        SELECT gen_random_uuid(), CAST(canonical_entity_id AS TEXT),
               'D1_IDENTITY', 'synthetic_test', 'PASS'
        FROM rce_curated_records
        WHERE source_intake_id = CAST(:i AS uuid) AND canonical_entity_id IS NOT NULL"""),
        {"i": str(intake_id)})
    await db.commit()
    assert (await av.coverage_progress(db, intake_id))["remaining"] == 0
    # Fully covered: the lookup no longer names this delivery.
    assert await av._next_delivery_needing_coverage(db) != intake_id


async def _lock_holder():
    from app.core.database import _normalize_url

    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    conn = await engine.connect()
    got = (await conn.execute(text("SELECT pg_try_advisory_lock(CAST(:k AS bigint))"),
                              {"k": av.COVERAGE_TICK_LOCK_KEY})).scalar()
    assert got is True, "the test needs the lock free to begin with"
    return engine, conn


async def test_tick_is_single_flight_across_connections(db_required, monkeypatch, caplog):
    monkeypatch.setenv(av.ENV_FLAG, "true")
    engine, holder = await _lock_holder()
    try:
        with patch.object(av, "_next_delivery_needing_coverage", new=AsyncMock()) as lookup, \
                patch.object(av, "run_coverage_batch", new=AsyncMock()) as batch, \
                caplog.at_level("DEBUG", logger=av.logger.name):
            await av._coverage_tick()
        assert lookup.await_count == 0, "a tick that lost the lock must not look for work"
        assert batch.await_count == 0, "a tick that lost the lock must not run a batch"
        assert any("advisory lock" in rec.getMessage() and rec.levelname == "DEBUG"
                   for rec in caplog.records)
    finally:
        await holder.execute(text("SELECT pg_advisory_unlock(CAST(:k AS bigint))"),
                             {"k": av.COVERAGE_TICK_LOCK_KEY})
        await holder.close()
        await engine.dispose()


async def test_tick_releases_the_lock_after_running(db_required, monkeypatch):
    monkeypatch.setenv(av.ENV_FLAG, "true")
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value=None)) as lookup, \
            patch.object(av, "run_coverage_batch", new=AsyncMock()) as batch:
        await av._coverage_tick()
    assert lookup.await_count == 1      # the lock was acquired, work was looked for
    assert batch.await_count == 0       # nothing to do
    # The lock is free again: another connection can take it.
    engine, holder = await _lock_holder()
    try:
        pass
    finally:
        await holder.execute(text("SELECT pg_advisory_unlock(CAST(:k AS bigint))"),
                             {"k": av.COVERAGE_TICK_LOCK_KEY})
        await holder.close()
        await engine.dispose()


async def test_tick_releases_the_lock_even_when_the_batch_raises(db_required, monkeypatch, caplog):
    monkeypatch.setenv(av.ENV_FLAG, "true")
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value="00000000-0000-0000-0000-000000000000")), \
            patch.object(av, "run_coverage_batch",
                         new=AsyncMock(side_effect=RuntimeError("simulated batch failure"))), \
            caplog.at_level("ERROR", logger=av.logger.name):
        await av._coverage_tick()          # never raises: the scheduler must survive
    assert any("tick error" in rec.getMessage() for rec in caplog.records)
    engine, holder = await _lock_holder()  # asserts the lock is free
    try:
        pass
    finally:
        await holder.execute(text("SELECT pg_advisory_unlock(CAST(:k AS bigint))"),
                             {"k": av.COVERAGE_TICK_LOCK_KEY})
        await holder.close()
        await engine.dispose()


async def test_tick_is_inert_when_the_feature_is_off(monkeypatch):
    monkeypatch.delenv(av.ENV_FLAG, raising=False)
    with patch.object(av, "_try_acquire_tick_lock", new=AsyncMock()) as lock:
        await av._coverage_tick()
    assert lock.await_count == 0
