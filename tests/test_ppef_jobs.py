"""
Durable PPEF ingestion jobs: lifecycle, concurrency, heartbeat and reaping.

WHAT THESE TESTS ARE ACTUALLY PROTECTING
The defect being fixed was not a crash. Ingestion ran in a FastAPI
BackgroundTask; when the worker recycled mid-load the task vanished and the
snapshot sat at `pending` with `error = None` — five of them on dev. Every
number on the screen looked fine. The bug was the ABSENCE of a record.

So the properties worth asserting are the ones whose failure is silent:

  * a job's state is in the DATABASE, not in process memory;
  * a worker that stops writing heartbeats is marked FAILED rather than staying
    pending forever;
  * a FAILED job releases its slot, so a retry is possible;
  * a partial or failed load never becomes readable evidence;
  * two concurrent loads of the same quarter are refused by POSTGRES, not by a
    check-then-insert with a race window in the middle.

The tests that need a real partial unique index are gated on `db_required` — a
partial index is a Postgres behaviour, and asserting it against a fake would
only assert the fake. Everything else runs with no database at all.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from app.Tefca import ppef_jobs
from app.Tefca.models import TEFCAPPEFIngestJob

pytestmark = pytest.mark.regression


# ── 1. The model encodes the lifecycle ───────────────────────────────────────

def test_job_states_are_the_specified_set():
    """The seven states, and the split between active and terminal.

    Asserted because the reaper selects on ACTIVE_STATES while the runner
    refuses to touch TERMINAL_STATES. A state added to one tuple and not the
    other would be invisible to the reaper — pending forever again, by a new
    route.
    """
    assert TEFCAPPEFIngestJob.STATE_QUEUED == "QUEUED"
    assert TEFCAPPEFIngestJob.STATE_STARTED == "STARTED"
    assert TEFCAPPEFIngestJob.STATE_DOWNLOADING == "DOWNLOADING"
    assert TEFCAPPEFIngestJob.STATE_VALIDATING == "VALIDATING"
    assert TEFCAPPEFIngestJob.STATE_LOADING == "LOADING"
    assert TEFCAPPEFIngestJob.STATE_COMPLETE == "COMPLETE"
    assert TEFCAPPEFIngestJob.STATE_FAILED == "FAILED"

    every = set(TEFCAPPEFIngestJob.ACTIVE_STATES) | set(TEFCAPPEFIngestJob.TERMINAL_STATES)
    assert every == {"QUEUED", "STARTED", "DOWNLOADING", "VALIDATING",
                     "LOADING", "COMPLETE", "FAILED"}
    # No state may be both, or the reaper would reap finished work.
    assert not set(TEFCAPPEFIngestJob.ACTIVE_STATES) & set(TEFCAPPEFIngestJob.TERMINAL_STATES)


def test_persisted_columns_cover_the_required_facts():
    cols = set(TEFCAPPEFIngestJob.__table__.columns.keys())
    for required in ("id", "component", "resource_version", "quarter", "state",
                     "created_at", "started_at", "heartbeat_at", "completed_at",
                     "failed_at", "attempt_count", "error_reason", "snapshot_id",
                     "checksum", "row_count"):
        assert required in cols, f"job table is missing {required}"


def test_concurrency_guard_is_a_partial_unique_index():
    """The guard must be a DATABASE constraint, not application logic.

    A check-then-insert leaves a window between the SELECT and the INSERT in
    which a second caller passes the same check. The partial unique index has no
    such window, and it keeps holding if the app ever runs more than one worker —
    which nothing currently prevents.
    """
    idx = {i.name: i for i in TEFCAPPEFIngestJob.__table__.indexes}
    guard = idx.get("uq_ppef_job_active_component")
    assert guard is not None, "the concurrency guard index is missing"
    assert guard.unique is True
    assert [c.name for c in guard.columns] == ["component", "resource_version",
                                               "active_marker"]
    where = guard.dialect_options["postgresql"].get("where")
    assert where is not None and "active_marker" in str(where), (
        "the index must be PARTIAL — a full unique index would also forbid a "
        "second COMPLETED load of the same quarter, making retry impossible")


# ── 2. Lifecycle against a recording fake session ────────────────────────────

class _FakeResult:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def all(self):
        return self._v if isinstance(self._v, list) else []


class _FakeSession:
    """Records what was executed and how often it committed.

    The assertions below care that state is COMMITTED, not merely assigned.
    State living only in a session that dies with its process is exactly the
    failure being fixed.
    """

    def __init__(self, get_result=None, select_result=None):
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.added = []
        self._get = get_result
        self._select = select_result

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._select)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        return None

    async def get(self, model, ident):
        return self._get

    async def scalar(self, stmt):
        return 0

    def add(self, obj):
        self.added.append(obj)


def _values(stmt) -> dict:
    """The column -> literal value map of an UPDATE.

    SQLAlchemy wraps each value in a BindParameter, so the raw `_values` map
    compares as an object rather than the value that will actually be written.
    """
    out = {}
    for col, val in stmt._values.items():
        out[col.name] = getattr(val, "value", val)
    return out


def _pg_sql(stmt) -> str:
    """Render a statement as POSTGRES would.

    The default dialect drops `SKIP LOCKED` entirely — asserting against it
    would pass while the production SQL silently lost its concurrency
    behaviour.
    """
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_transition_commits_immediately():
    """A transition that is not committed did not happen.

    Batching these would put the job's state back in process memory, where it
    cannot survive the recycle this whole mechanism exists to survive.
    """
    db = _FakeSession()
    await ppef_jobs.transition(db, "job-1", TEFCAPPEFIngestJob.STATE_DOWNLOADING)
    assert db.commits == 1
    assert "tefca_ppef_ingest_jobs" in str(db.statements[0]).lower()
    assert _values(db.statements[0])["state"] == "DOWNLOADING"


@pytest.mark.asyncio
async def test_transition_refreshes_the_heartbeat():
    """A phase change is proof of life and must count as one.

    Without it, a job that spends longer than the stale threshold crossing a
    phase boundary could be reaped while genuinely working.
    """
    db = _FakeSession()
    await ppef_jobs.transition(db, "job-1", TEFCAPPEFIngestJob.STATE_LOADING)
    assert _values(db.statements[0]).get("heartbeat_at") is not None


@pytest.mark.asyncio
async def test_finish_complete_releases_the_slot_and_records_the_evidence():
    db = _FakeSession()
    await ppef_jobs.finish_complete(db, "job-1", "snap-1", "a" * 64, 3899791)
    vals = _values(db.statements[0])
    assert vals["state"] == "COMPLETE"
    # active_marker must go NULL, or the next load of this quarter is blocked.
    assert vals["active_marker"] is None
    assert vals["row_count"] == 3899791
    assert vals["checksum"] == "a" * 64
    assert vals["completed_at"] is not None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_finish_failed_releases_the_slot_so_retry_is_permitted():
    """A failure that kept the slot would turn one bad load into a dead component."""
    db = _FakeSession()
    await ppef_jobs.finish_failed(db, "job-1", "worker_recycled_before_completion")
    vals = _values(db.statements[0])
    assert vals["state"] == "FAILED"
    assert vals["active_marker"] is None
    assert vals["error_reason"] == "worker_recycled_before_completion"
    assert vals["failed_at"] is not None


@pytest.mark.asyncio
async def test_failure_reason_is_truncated_not_dropped():
    """A 9KB traceback must not fail the write that records the failure.

    The record of WHY is the whole value of the row. Losing it to a column-length
    error would reproduce the original defect — a stopped job with no
    explanation — for a new reason.
    """
    db = _FakeSession()
    await ppef_jobs.finish_failed(db, "job-1", "x" * 9000)
    assert len(_values(db.statements[0])["error_reason"]) == 2000


@pytest.mark.asyncio
async def test_queue_job_translates_integrity_error_into_jobconflict():
    """The database refuses the duplicate; the service translates, never retries.

    Swallowing the IntegrityError and retrying would defeat the guard entirely.
    """
    from sqlalchemy.exc import IntegrityError

    class _Conflicting(_FakeSession):
        async def commit(self):
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    db = _Conflicting()
    with pytest.raises(ppef_jobs.JobConflict) as exc:
        await ppef_jobs.queue_job(db, component="ENROLLMENT",
                                  resource_version="2026.07.17")
    assert db.rollbacks == 1
    assert "ENROLLMENT" in str(exc.value)


@pytest.mark.asyncio
async def test_queued_job_starts_active_and_heartbeating():
    """A QUEUED job must already hold the slot and already look alive.

    If active_marker were only set at claim time, two jobs could queue for the
    same quarter and the conflict would surface minutes later, mid-download.
    A null heartbeat would meanwhile make a freshly queued job look stale.
    """
    db = _FakeSession()
    job = await ppef_jobs.queue_job(db, component="ENROLLMENT",
                                    resource_version="2026.07.17",
                                    quarter="Q3 2026", requested_by="a@b.c")
    assert job.state == TEFCAPPEFIngestJob.STATE_QUEUED
    assert job.active_marker is True
    assert job.heartbeat_at is not None
    assert job.attempt_count == 0
    assert job.requested_by == "a@b.c"


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_and_counts_the_attempt():
    """Claiming must be safe against a second poller, and must count attempts.

    FOR UPDATE SKIP LOCKED is what makes two pollers take DIFFERENT jobs rather
    than both taking the same one. attempt_count is what makes a job that keeps
    dying visible, instead of each retry looking like a fresh request.
    """
    job = TEFCAPPEFIngestJob(component="ENROLLMENT", state="QUEUED", attempt_count=0)
    db = _FakeSession(select_result=job)
    claimed = await ppef_jobs.claim_next_queued(db)

    sql = _pg_sql(db.statements[0]).upper()
    assert "FOR UPDATE" in sql and "SKIP LOCKED" in sql, sql
    assert claimed.state == TEFCAPPEFIngestJob.STATE_STARTED
    assert claimed.attempt_count == 1
    assert claimed.started_at is not None and claimed.heartbeat_at is not None


@pytest.mark.asyncio
async def test_claim_returns_none_when_the_queue_is_empty():
    db = _FakeSession(select_result=None)
    assert await ppef_jobs.claim_next_queued(db) is None
    assert db.commits == 0, "an empty poll must not write"


# ── 3. The reaper — what makes worker death recoverable ──────────────────────

@pytest.mark.asyncio
async def test_reaper_fails_a_job_whose_heartbeat_went_stale():
    """The test for the original defect.

    A dead process emits no signal except silence. The reaper reads that silence
    and turns "stuck forever, needs a human" into "FAILED, retry permitted".
    """
    stale = TEFCAPPEFIngestJob(
        component="REASSIGNMENT", state=TEFCAPPEFIngestJob.STATE_LOADING,
        active_marker=True,
        heartbeat_at=datetime.utcnow() - timedelta(
            seconds=ppef_jobs.STALE_HEARTBEAT_SECONDS + 60),
    )
    db = _FakeSession(select_result=[stale])
    reaped = await ppef_jobs.reap_stale_jobs(db)

    assert len(reaped) == 1
    assert stale.state == TEFCAPPEFIngestJob.STATE_FAILED
    assert stale.active_marker is None, "a reaped job must release its slot"
    assert stale.failed_at is not None
    # The reason has to say when it was last alive and what the threshold was,
    # or an operator cannot tell a dead worker from a slow one.
    assert "no_heartbeat" in stale.error_reason
    assert "last heartbeat" in stale.error_reason
    assert str(ppef_jobs.STALE_HEARTBEAT_SECONDS) in stale.error_reason
    assert db.commits == 1

    # The phase the worker died in — captured BEFORE the row was mutated.
    # Reading job.state afterwards reports "FAILED" for every reaped job: true,
    # and useless, because which phase it died in is the fact an investigation
    # actually needs.
    assert reaped[0]["was_state"] == TEFCAPPEFIngestJob.STATE_LOADING
    assert "state was LOADING" in stale.error_reason
    # Carried out so the caller can audit the failure against the requesting
    # admin without re-querying.
    assert "requested_by" in reaped[0]


@pytest.mark.asyncio
async def test_reaper_leaves_a_live_job_alone():
    """A slow CMS download is not a dead worker."""
    db = _FakeSession(select_result=[])
    assert await ppef_jobs.reap_stale_jobs(db) == []
    assert db.commits == 0, "a reap that found nothing must not write"


@pytest.mark.asyncio
async def test_reaper_query_targets_active_states_and_an_old_heartbeat():
    db = _FakeSession(select_result=[])
    await ppef_jobs.reap_stale_jobs(db)
    sql = str(db.statements[0])
    assert "IN (" in sql
    assert "heartbeat_at <" in sql


def test_heartbeat_interval_is_well_inside_the_stale_threshold():
    """Otherwise normal operation would look like death.

    A margin of at least 5x means several consecutive missed heartbeats are
    required before a live job is reaped.
    """
    assert ppef_jobs.HEARTBEAT_INTERVAL_SECONDS * 5 <= ppef_jobs.STALE_HEARTBEAT_SECONDS


@pytest.mark.asyncio
async def test_reaper_marks_the_orphaned_snapshot_failed_too():
    """A half-filled snapshot must never look loadable.

    It is left in place rather than deleted — it is the record of what happened —
    but its status has to say so, because `pending` is precisely what five dev
    rows claimed while nothing was working on them.
    """
    from app.Tefca.models import TEFCAPPEFSnapshot

    snap = TEFCAPPEFSnapshot(component="ENROLLMENT", ingest_status="pending")
    job = TEFCAPPEFIngestJob(
        component="ENROLLMENT", state=TEFCAPPEFIngestJob.STATE_LOADING,
        active_marker=True, snapshot_id="snap-1",
        heartbeat_at=datetime.utcnow() - timedelta(seconds=3600))

    class _S(_FakeSession):
        async def get(self, model, ident):
            return snap

    db = _S(select_result=[job])
    await ppef_jobs.reap_stale_jobs(db)
    assert snap.ingest_status == "failed"
    assert snap.error and "no_heartbeat" in snap.error


@pytest.mark.asyncio
async def test_reaper_never_downgrades_a_complete_snapshot():
    """A job reaped after its snapshot activated must not un-activate it.

    The snapshot is the evidence; a race between the completion write and the
    reaper must never destroy a good load.
    """
    from app.Tefca.models import TEFCAPPEFSnapshot

    snap = TEFCAPPEFSnapshot(component="ENROLLMENT", ingest_status="complete")
    job = TEFCAPPEFIngestJob(
        component="ENROLLMENT", state=TEFCAPPEFIngestJob.STATE_LOADING,
        active_marker=True, snapshot_id="snap-1",
        heartbeat_at=datetime.utcnow() - timedelta(seconds=3600))

    class _S(_FakeSession):
        async def get(self, model, ident):
            return snap

    await ppef_jobs.reap_stale_jobs(_S(select_result=[job]))
    assert snap.ingest_status == "complete"


@pytest.mark.asyncio
async def test_orphaned_pending_snapshots_are_closed():
    """The five dev rows: pending forever, with no job to go stale.

    They predate the job table, so the heartbeat sweep cannot see them. They are
    the same failure as a dead worker minus the evidence, and closing them is the
    same act — a truthful terminal state instead of a row that looks like work in
    progress.
    """
    from app.Tefca.models import TEFCAPPEFSnapshot

    orphan = TEFCAPPEFSnapshot(
        component="REASSIGNMENT", ingest_status="pending",
        ingested_at=datetime.utcnow() - timedelta(hours=6))
    orphan.id = "snap-orphan"

    class _S(_FakeSession):
        def __init__(self):
            super().__init__()
            self._calls = 0

        async def execute(self, stmt):
            self.statements.append(stmt)
            self._calls += 1
            # First query returns the candidate snapshots, second the set of
            # snapshot ids that some job already owns.
            return _FakeResult([orphan] if self._calls == 1 else [])

    db = _S()
    closed = await ppef_jobs.close_orphaned_snapshots(db)

    assert len(closed) == 1
    assert orphan.ingest_status == "failed"
    assert ppef_jobs.LEGACY_ORPHAN_REASON in orphan.error
    assert "worker_recycled_before_completion" == ppef_jobs.LEGACY_ORPHAN_REASON
    # The row is marked, never deleted — it is the record that a load was tried.
    assert db.commits == 1


@pytest.mark.asyncio
async def test_a_snapshot_owned_by_a_job_is_left_to_the_heartbeat_sweep():
    """Two mechanisms must not both decide one snapshot's fate.

    A snapshot with a job row belongs to reap_stale_jobs, which knows whether its
    worker is alive. Closing it here would kill a load in flight.
    """
    from app.Tefca.models import TEFCAPPEFSnapshot

    live = TEFCAPPEFSnapshot(
        component="ENROLLMENT", ingest_status="pending",
        ingested_at=datetime.utcnow() - timedelta(hours=6))
    live.id = "snap-owned"

    class _S(_FakeSession):
        def __init__(self):
            super().__init__()
            self._calls = 0

        async def execute(self, stmt):
            self.statements.append(stmt)
            self._calls += 1
            return _FakeResult([live] if self._calls == 1 else ["snap-owned"])

    db = _S()
    assert await ppef_jobs.close_orphaned_snapshots(db) == []
    assert live.ingest_status == "pending"
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_recent_pending_snapshot_is_never_closed():
    """A load that started a minute ago must survive a reap cycle."""
    db = _FakeSession(select_result=[])
    assert await ppef_jobs.close_orphaned_snapshots(db) == []
    sql = str(db.statements[0])
    assert "ingested_at <" in sql and "ingest_status" in sql


def test_orphan_window_cannot_catch_a_real_load():
    """Two hours is far beyond the longest real load (3.9M rows in minutes)."""
    assert ppef_jobs.LEGACY_ORPHAN_HOURS >= 2


# ── 4. Status reporting reads the database, never memory ─────────────────────

@pytest.mark.asyncio
async def test_job_status_reports_staleness_before_the_reaper_acts():
    """An operator should see that a worker went quiet without waiting a cycle."""
    job = TEFCAPPEFIngestJob(
        component="ENROLLMENT", state=TEFCAPPEFIngestJob.STATE_LOADING,
        heartbeat_at=datetime.utcnow() - timedelta(
            seconds=ppef_jobs.STALE_HEARTBEAT_SECONDS + 30),
        created_at=datetime.utcnow(), attempt_count=1)
    db = _FakeSession(get_result=job)
    status = await ppef_jobs.job_status(db, "job-1")

    assert status["heartbeat_stale"] is True
    assert status["terminal"] is False
    assert status["heartbeat_age_seconds"] > ppef_jobs.STALE_HEARTBEAT_SECONDS


@pytest.mark.asyncio
async def test_completed_job_is_never_reported_stale():
    """A finished job stops heartbeating by design; that is not a fault."""
    job = TEFCAPPEFIngestJob(
        component="ENROLLMENT", state=TEFCAPPEFIngestJob.STATE_COMPLETE,
        heartbeat_at=datetime.utcnow() - timedelta(days=30),
        completed_at=datetime.utcnow() - timedelta(days=30))
    db = _FakeSession(get_result=job)
    status = await ppef_jobs.job_status(db, "job-1")

    assert status["heartbeat_stale"] is False
    assert status["terminal"] is True


@pytest.mark.asyncio
async def test_job_status_returns_none_for_an_unknown_id():
    db = _FakeSession(get_result=None)
    assert await ppef_jobs.job_status(db, "nope") is None


# ── 5. Idempotency ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotency_lookup_excludes_truncated_snapshots():
    """A capped load is a different artefact and must not block a real one.

    Treating rows_truncated=True as "already loaded" would permanently prevent
    the full quarter from ever being ingested — a silent data gap that every
    status display would report as success.
    """
    db = _FakeSession(select_result=None)
    await ppef_jobs.find_complete_snapshot(db, "ENROLLMENT", "2026.07.17")
    sql = str(db.statements[0])
    assert "rows_truncated" in sql
    assert "ingest_status" in sql
    # Identity is (component, version): the checksum cannot be known before the
    # download, so it is compared afterwards, never used as the lookup key.
    assert "resource_version" in sql and "component" in sql


# ── 6. The scheduler holds no state ──────────────────────────────────────────

def test_scheduler_status_is_safe_before_start():
    from app.Tefca.ppef_scheduler import scheduler_status

    status = scheduler_status()
    assert status["running"] is False and status["jobs"] == []


def test_scheduler_module_stores_no_job_state():
    """APScheduler triggers; it must not be the record of what happened.

    Its default MemoryJobStore dies with the process — the very failure being
    fixed — so nothing durable may live in this module.
    """
    from app.Tefca import ppef_scheduler

    src = inspect.getsource(ppef_scheduler)
    assert "SQLAlchemyJobStore" not in src, (
        "job state belongs in the application's own table, not in an APScheduler "
        "store — the reaper and the status endpoint both read the former")
    assert "ppef_jobs.finish_complete" in src
    assert "ppef_jobs.finish_failed" in src
    assert "ppef_jobs.transition" in src


def test_scheduler_reuses_the_tested_ingestor_rather_than_reimplementing_it():
    """Heartbeats are added by EXTENSION at the documented seam.

    Reimplementing download, checksum or schema validation would mean the code
    that runs against CMS is no longer the code that was tested against CMS.
    """
    from app.Tefca import ppef_scheduler

    src = inspect.getsource(ppef_scheduler)
    assert "class _Impl(PPEFIngestor)" in src
    assert "async def iter_chunks" in src
    for reimplemented in ("hashlib.sha256", "csv.reader", "def validate_schema"):
        assert reimplemented not in src, f"{reimplemented} was reimplemented"


def test_activation_gate_requires_every_check_to_pass():
    """A snapshot becomes evidence only after checksum, schema, parity and joins."""
    from app.Tefca import ppef_scheduler

    src = inspect.getsource(ppef_scheduler.run_job)
    assert 'ingest_status = "complete"' in src
    for check in ("checksum missing", "schema not validated", "zero rows loaded",
                  "row-count mismatch", "relational validation failed"):
        assert check in src, f"the activation gate does not check: {check}"
    # The gate must precede activation, not follow it.
    assert src.index("problems") < src.index('ingest_status = "complete"')


def test_reassignment_requires_both_join_keys():
    """REASSIGNMENT carries a second key, and traversal depends on it.

    Loading it without RCV_BNFT_ENRLMT_ID would leave every row count correct
    and entity->practitioner traversal silently broken.
    """
    from app.Tefca import ppef_scheduler

    src = inspect.getsource(ppef_scheduler.run_job)
    assert "related_enrollment_id" in src and "RCV_BNFT_ENRLMT_ID" in src


def test_failure_path_is_audited_and_attribution_is_split():
    """A trail that records only successes cannot be used to investigate anything.

    Attribution is split deliberately: user_id is the admin who ASKED (activity
    feeds filter on user_id, so a null is invisible exactly where an operator
    looks), and executed_by names the service, so no row implies a human sat and
    watched millions of rows load.
    """
    from app.Tefca import ppef_scheduler

    src = inspect.getsource(ppef_scheduler)
    assert "PPEF_SNAPSHOT_INGEST_FAILED" in src
    assert "PPEF_SNAPSHOT_INGESTED" in src
    assert "system/ppef-scheduler" in src
    assert "requested_by" in src


def test_reaped_jobs_are_written_to_the_audit_trail():
    """A worker dying mid-load must leave a row where operators investigate.

    The job table and the snapshot both record the fate of a reaped job, but
    neither is the audit trail. Without this, the single failure this mechanism
    exists to handle would show as QUEUED-then-nothing in the audit log — the
    same silence as the original defect, relocated.
    """
    from app.Tefca import ppef_scheduler

    src = inspect.getsource(ppef_scheduler)
    assert "_audit_reaped" in src
    audit = inspect.getsource(ppef_scheduler._audit_reaped)
    assert 'action="PPEF_SNAPSHOT_INGEST_FAILED"' in audit
    assert "system/ppef-reaper" in audit
    assert "failed_in_state" in audit, "the audit must say which phase died"
    assert "user=actor" in audit, "attribute to the admin who requested the load"
    # An audit write that fails quietly is itself a defect.
    assert "AUDIT FAILED" in audit and "logger.error" in audit


def test_runner_ignores_a_job_that_is_already_terminal():
    """A reaped job must not be resurrected by a poller that claimed it late."""
    from app.Tefca import ppef_scheduler

    src = inspect.getsource(ppef_scheduler.run_job)
    assert "TERMINAL_STATES" in src


# ── 7. Endpoint contract ─────────────────────────────────────────────────────

def test_ingest_endpoints_are_registered():
    from app.Tefca.routes import tefca_dashboard_router as router

    paths = {r.path for r in router.routes}
    assert "/api/tefca/ppef/snapshots/ingest" in paths
    assert "/api/tefca/ppef/snapshots/ingest/{job_id}" in paths
    assert "/api/tefca/ppef/jobs" in paths


def test_ingest_endpoint_no_longer_uses_background_tasks():
    """The regression guard for the original defect.

    A BackgroundTask dies with its worker and leaves no record. If this comes
    back, five snapshots stuck at `pending` come back with it.
    """
    from app.Tefca import routes

    src = inspect.getsource(routes.ppef_ingest_component)
    assert "BackgroundTasks" not in src and "add_task" not in src
    assert "queue_job" in src


def test_status_endpoint_is_readable_by_a_viewer():
    """Operators who need to see a stuck load are not all admins.

    Starting a load stays admin-only; observing one does not.
    """
    from app.Tefca import routes

    assert 'require_role("viewer")' in inspect.getsource(routes.ppef_ingest_status)
    assert 'require_role("admin")' in inspect.getsource(routes.ppef_ingest_component)


def test_status_endpoint_rejects_a_malformed_job_id():
    """400 for a bad id and 404 for an unknown one — not a 500 from a UUID cast."""
    from app.Tefca import routes

    src = inspect.getsource(routes.ppef_ingest_status)
    assert "not a valid job id" in src and "404" in src


def test_ingest_endpoint_documents_idempotency_and_conflict():
    from app.Tefca import routes

    src = inspect.getsource(routes.ppef_ingest_component)
    assert "ALREADY_LOADED" in src
    assert "JobConflict" in src and "409" in src
    assert "force" in src


def test_scheduler_is_started_at_application_startup():
    """A reaper that never runs is a reaper that never recovers anything."""
    import app.main

    src = inspect.getsource(app.main)
    assert "start_ppef_scheduler" in src


# ── 8. Real database: the constraint must actually exist ─────────────────────

@pytest.mark.asyncio
async def test_partial_unique_index_refuses_a_second_active_job(db_required):
    """The concurrency guard, exercised against Postgres.

    A partial unique index is a database behaviour; asserting it against a fake
    would only assert the fake. This inserts two active jobs for the same
    component and quarter and requires the SECOND to be rejected — then requires
    a THIRD to succeed once the first is terminal, because a guard that forbids
    retry is its own outage.
    """
    import uuid

    from sqlalchemy import delete

    from app.core.database import async_session_maker

    component = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    version = "9999.99.99"

    async with async_session_maker() as db:
        first = await ppef_jobs.queue_job(db, component=component,
                                          resource_version=version,
                                          requested_by="test@test.local")
        try:
            with pytest.raises(ppef_jobs.JobConflict):
                await ppef_jobs.queue_job(db, component=component,
                                          resource_version=version,
                                          requested_by="test@test.local")

            # Terminal -> slot released -> a retry is accepted.
            await ppef_jobs.finish_failed(db, first.id, "test cleanup")
            retry = await ppef_jobs.queue_job(db, component=component,
                                              resource_version=version,
                                              requested_by="test@test.local")
            assert retry.id != first.id
            await ppef_jobs.finish_failed(db, retry.id, "test cleanup")
        finally:
            await db.execute(delete(TEFCAPPEFIngestJob)
                             .where(TEFCAPPEFIngestJob.component == component))
            await db.commit()


@pytest.mark.asyncio
async def test_reaper_against_a_real_row(db_required):
    """End to end: an active job with an old heartbeat becomes FAILED."""
    import uuid

    from sqlalchemy import delete

    from app.core.database import async_session_maker

    component = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    async with async_session_maker() as db:
        job = await ppef_jobs.queue_job(db, component=component,
                                        resource_version="9999.99.99")
        try:
            job.state = TEFCAPPEFIngestJob.STATE_LOADING
            job.heartbeat_at = datetime.utcnow() - timedelta(seconds=7200)
            await db.commit()

            reaped = await ppef_jobs.reap_stale_jobs(db)
            assert any(r["component"] == component for r in reaped)

            status = await ppef_jobs.job_status(db, job.id)
            assert status["state"] == "FAILED"
            assert status["terminal"] is True
            assert "no_heartbeat" in status["error_reason"]
        finally:
            await db.execute(delete(TEFCAPPEFIngestJob)
                             .where(TEFCAPPEFIngestJob.component == component))
            await db.commit()
