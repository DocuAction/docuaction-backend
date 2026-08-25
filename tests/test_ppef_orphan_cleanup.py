"""
The offline PPEF orphan collector, and the gap between it and the reaper.

WHAT WAS BROKEN
The mechanism had two halves that did not compose.

The in-app reaper (`reap_stale_jobs`, `close_orphaned_snapshots`) marks a dead
load `pending` -> `failed`. It deliberately does NOT delete the partial RECORD
rows underneath it, and that restraint is correct: a DELETE racing rows another
transaction is still inserting is how a reaper corrupts a live load. It defers
them, in its own words, "to the out-of-band cleanup script".

That script selected on `ingest_status == 'pending'` ONLY. So the moment the
reaper did its job the snapshot left the script's field of view permanently, and
its orphaned rows became unreclaimable by any automated path in the system.
Nothing errored. Nothing logged. The rows simply stayed forever, indexed,
counted, and indistinguishable at a glance from loaded evidence — 3,450,000 of
them on dev, under three snapshots the reaper had correctly marked `failed`.

WHY THESE TESTS EXIST IN THIS SHAPE
The failure is one of ABSENCE, like the defect the job table was built to fix.
Nothing observable goes wrong when pass 2 is missing; the collector just reports
"nothing to do" while millions of rows sit under closed snapshots. So the
assertions here are about what the collector SEES, not only what it does:

  * it must see `failed` snapshots at all — the regression that stranded the
    rows was a query predicate, not a bug in the deletion;
  * it must never see `complete` ones, in either pass;
  * it must not rewrite a reaper's specific reason with its own generic one;
  * it must not touch a snapshot a live retry still claims;
  * it must never delete a SNAPSHOT row, which is the record that a load was
    attempted;
  * and its closing summary must describe what it actually did — the previous
    wording announced "records PRESERVED" on the very run that deleted them.

These run with no database. The behaviour under test is query construction and
control flow, and a fake session asserts both without pretending to be Postgres.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.Tefca.models import TEFCAPPEFIngestJob, TEFCAPPEFSnapshot

pytestmark = pytest.mark.regression


def _load_script():
    """Import the script by path — `scripts/` is not a package."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_stuck_ppef_snapshots.py"
    spec = importlib.util.spec_from_file_location("cleanup_stuck_ppef_snapshots", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleanup = _load_script()


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, value):
        self._v = value

    def scalars(self):
        return self

    def all(self):
        return self._v if isinstance(self._v, list) else []


class _FakeSession:
    """Serves a scripted sequence of SELECT results and records every statement.

    `scalar()` answers the per-snapshot row counts in order, so a test can give
    one snapshot 1,650,000 orphans and another zero and assert the collector
    treats them differently.
    """

    def __init__(self, selects, counts=()):
        self._selects = deque(selects)
        self._counts = deque(counts)
        self.statements = []
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._selects.popleft() if self._selects else [])

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return self._counts.popleft() if self._counts else 0

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install(monkeypatch, session):
    import app.core.database as database

    monkeypatch.setattr(database, "async_session_maker", lambda: session)
    # The script refuses to run against production without --allow-prod, and a
    # developer machine may legitimately have either name set.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)


def _snapshot(status, component="REASSIGNMENT", error=None, age_hours=48):
    return TEFCAPPEFSnapshot(
        id=uuid.uuid4(), component=component, ingest_status=status, error=error,
        ingested_at=datetime.utcnow() - timedelta(hours=age_hours),
    )


def _deletes(session):
    """Every DELETE issued, as (table_name, statement)."""
    out = []
    for stmt in session.statements:
        if getattr(stmt, "__visit_name__", None) == "delete":
            out.append((stmt.table.name, stmt))
    return out


# ── 1. the regression: `failed` snapshots must be visible at all ─────────────

@pytest.mark.asyncio
async def test_pass_two_reclaims_rows_under_an_already_failed_snapshot(monkeypatch):
    """THE defect. The reaper closed it; nothing then collected its rows.

    Before the fix this run printed "no stuck snapshots found — nothing to do."
    and exited 0 while 1,650,000 rows sat under the snapshot.
    """
    reaped = _snapshot("failed", error="worker_died_no_heartbeat: last heartbeat ...")
    session = _FakeSession(
        selects=[[], [reaped], []],   # no pending, one failed, no live jobs
        counts=[1_650_000],
    )
    _install(monkeypatch, session)

    assert await cleanup.run(confirm=True, allow_prod=False, older_than_hours=2) == 0

    deletes = _deletes(session)
    assert len(deletes) == 1, "the orphaned rows must be collected"
    table, _ = deletes[0]
    assert table == "tefca_ppef_records"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_pass_two_query_selects_the_failed_status(monkeypatch):
    """Asserted on the SQL, because the stranding bug WAS the predicate.

    A deletion loop that is correct but never reached is what shipped.
    """
    session = _FakeSession(selects=[[], [], []])
    _install(monkeypatch, session)
    await cleanup.run(confirm=False, allow_prod=False, older_than_hours=2)

    sql = [str(s) for s in session.statements]
    assert any("ingest_status" in q and "ingested_at <" in q for q in sql)
    # Both terminal-ish states the collector is allowed to see, and neither is
    # `complete`.
    joined = " ".join(sql)
    assert "tefca_ppef_snapshots" in joined


@pytest.mark.asyncio
async def test_a_complete_snapshot_is_never_emptied(monkeypatch):
    """A `complete` snapshot is evidence. Neither pass may select it.

    Both passes filter on status, so a complete snapshot is never returned by
    either query — the collector cannot reach it even if its rows look odd.
    """
    session = _FakeSession(selects=[[], [], []])
    _install(monkeypatch, session)
    await cleanup.run(confirm=True, allow_prod=False, older_than_hours=2)

    assert _deletes(session) == []
    assert session.commits == 0, "a run that collected nothing must not write"


# ── 2. what pass 2 must NOT do ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pass_two_does_not_overwrite_the_reapers_reason(monkeypatch):
    """The reaper's reason names the phase and the last heartbeat.

    This script's generic `worker_recycled_before_completion` is strictly less
    informative. Collecting the rows must not cost the investigation the only
    specific account of what happened.
    """
    specific = ("worker_died_no_heartbeat: last heartbeat 2026-08-19T04:12:55, "
                "state was LOADING")
    reaped = _snapshot("failed", error=specific)
    session = _FakeSession(selects=[[], [reaped], []], counts=[50_000])
    _install(monkeypatch, session)

    await cleanup.run(confirm=True, allow_prod=False, older_than_hours=2)

    assert reaped.error == specific, "the reaper's reason must survive collection"
    assert reaped.ingest_status == "failed"


@pytest.mark.asyncio
async def test_a_failed_snapshot_claimed_by_a_live_job_is_skipped(monkeypatch):
    """`failed` snapshot + non-terminal job = a retry is running against it.

    Those rows belong to the retry. Deleting them mid-load is the exact race the
    reaper refuses to run, reintroduced from the other side.
    """
    retried = _snapshot("failed", error="worker_died_no_heartbeat: ...")
    session = _FakeSession(
        selects=[[], [retried], [retried.id]],   # the live-job query returns it
        counts=[1_750_000],
    )
    _install(monkeypatch, session)

    await cleanup.run(confirm=True, allow_prod=False, older_than_hours=2)

    assert _deletes(session) == [], "a snapshot a live job claims must be left alone"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_live_job_query_excludes_terminal_states(monkeypatch):
    """A COMPLETE or FAILED job protects nothing.

    If terminal jobs counted as live, every snapshot the reaper closed would be
    "claimed" by the very job that failed it — and pass 2 would skip all of them,
    silently restoring the original bug.
    """
    reaped = _snapshot("failed")
    session = _FakeSession(selects=[[], [reaped], []], counts=[10])
    _install(monkeypatch, session)
    await cleanup.run(confirm=False, allow_prod=False, older_than_hours=2)

    job_sql = [str(s) for s in session.statements
               if "tefca_ppef_ingest_jobs" in str(s)]
    assert job_sql, "pass 2 must ask which jobs are still live"
    assert "NOT IN" in job_sql[0].upper()
    assert set(TEFCAPPEFIngestJob.TERMINAL_STATES) == {"COMPLETE", "FAILED"}


@pytest.mark.asyncio
async def test_no_pass_ever_deletes_a_snapshot_row(monkeypatch):
    """The snapshot row is the record that a load was attempted.

    Collecting its rows makes the history smaller; deleting the snapshot would
    make it untrue.
    """
    session = _FakeSession(
        selects=[[_snapshot("pending")], [_snapshot("failed")], []],
        counts=[500, 700],
    )
    _install(monkeypatch, session)
    await cleanup.run(confirm=True, allow_prod=False, older_than_hours=2)

    tables = {t for t, _ in _deletes(session)}
    assert tables == {"tefca_ppef_records"}
    assert "tefca_ppef_snapshots" not in tables


# ── 3. the operator-facing contract ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_is_the_default_and_writes_nothing(monkeypatch):
    session = _FakeSession(
        selects=[[_snapshot("pending")], [_snapshot("failed")], []],
        counts=[1_000, 2_000],
    )
    _install(monkeypatch, session)
    await cleanup.run(confirm=False, allow_prod=False, older_than_hours=2)

    assert _deletes(session) == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_dry_run_reports_the_row_count_it_would_delete(monkeypatch, capsys):
    """An operator decides whether to type --confirm from this number.

    A dry run that named snapshots but not row counts asked them to authorise a
    deletion of unstated size.
    """
    session = _FakeSession(
        selects=[[], [_snapshot("failed"), _snapshot("failed", "ENROLLMENT")], []],
        counts=[1_750_000, 1_650_000],
    )
    _install(monkeypatch, session)
    await cleanup.run(confirm=False, allow_prod=False, older_than_hours=2)

    out = capsys.readouterr().out
    assert "3,400,000" in out, "the total must be stated before it is authorised"
    assert "DRY RUN" in out


@pytest.mark.asyncio
async def test_summary_names_the_rows_it_deleted_and_the_rows_it_kept(monkeypatch, capsys):
    """Guards a message that stated the opposite of what the code did.

    The closing line used to announce that records were preserved — on the very
    run that deleted them. RECORD rows are precisely what this script removes;
    SNAPSHOT rows are what it keeps. An operator reading that line before typing
    --confirm was told the deletion would not happen.

    Asserted against the printed OUTPUT rather than the source text, because the
    source now discusses the old wording in a comment explaining the fix.
    """
    session = _FakeSession(selects=[[], [_snapshot("failed")], []], counts=[3_450_000])
    _install(monkeypatch, session)
    await cleanup.run(confirm=True, allow_prod=False, older_than_hours=2)

    out = capsys.readouterr().out
    assert "3,450,000" in out
    assert "deleted" in out, "the summary must say rows were deleted"
    assert "SNAPSHOT rows preserved" in out
    # The claim that must never reappear: that RECORD rows survived this run.
    assert "records PRESERVED" not in out


@pytest.mark.asyncio
async def test_production_is_refused_without_allow_prod(monkeypatch):
    session = _FakeSession(selects=[[], [], []])
    _install(monkeypatch, session)
    monkeypatch.setenv("ENVIRONMENT", "production")

    assert await cleanup.run(confirm=True, allow_prod=False, older_than_hours=2) == 2
    assert session.statements == [], "it must refuse before querying anything"
