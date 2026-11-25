"""Exact IQVIA snapshot reconciliation, run as a background job.

WHY THIS EXISTS (2026-11-24)
----------------------------
`GET /iqvia/snapshots/{id}` and `POST /iqvia/snapshots/{id}/approve` used to run
`SELECT count(*)` over the observation table on every call. On DEV, for the
snapshot that had just finished a multi-hour import, that count took 42-181 s
(disk-read bound; the database was otherwise idle), so the portal timed out and
every reopen started another copy. This module moves the exact count off the
request path:

* Open / status polling returns the counters the importer RECORDED AT IMPORT
  (`recorded_counters`) -- no count, no write, immediate.
* The exact count runs once, as a durable job: POST only INSERTS a QUEUED row;
  the scheduler (`iqvia_import_scheduler._reconcile_tick`) claims it with
  FOR UPDATE SKIP LOCKED and runs it under a statement timeout with a
  heartbeat -- never a request-scoped task, which is the pattern the import
  path retired. A partial unique index makes "one active reconciliation per
  snapshot" a database guarantee, and the claim refuses to start a second
  count anywhere while one is CHECKING, so opening the page twice (or opening
  two snapshots) cannot pile counts on the database.
* Approval no longer counts. It requires the LATEST reconciliation for the
  snapshot to be PASSED **and** to belong to the snapshot as it stands now
  (`staging_token`). If the staged data changes -- a resumed import, a new
  attempt, a different summary -- the token changes, the result is invalid and a
  fresh check is required.

WHAT THE TOKEN DOES AND DOES NOT COVER
--------------------------------------
The token is a hash of everything the application records about how the staged
rows came to be (sha256, record_count, rows_rejected, import status and summary,
the reference-preflight gate, whether a decision successor exists, and the
latest import job id / attempt / state / completion time). Every code path that writes observation rows runs through the importer
and changes at least one of those. It does NOT detect a row inserted or deleted
by someone with direct database access: PASSED means "the table count matched at
`finished_at`", and nothing cheaper than a recount can say more. An expiry on
PASSED would be an owner decision and is not implemented.

WHAT IS UNCHANGED
-----------------
`approve_snapshot` (qalead floor, registrant cannot approve their own snapshot,
append-only successor row) and the approval audit rows. Only the source of the
"does it reconcile" answer changes, from an inline count to a stored exact
result.

No IQVIA row content is read, copied or stored here: counts and ids only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer, String,
                        Text, func, select, text, update)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import snapshot_models as sm

logger = logging.getLogger(__name__)

#: A CHECKING row whose heartbeat (database clock) is older than this is lost:
#: the process that owned it died. Heartbeats are written every HEARTBEAT_SECONDS.
HEARTBEAT_SECONDS = 10
STALE_AFTER_SECONDS = 120
#: A QUEUED row nobody claimed for this long means no scheduler is running.
QUEUED_MAX_SECONDS = 1800
#: After a FAILED check, a new one is refused for this long, so a failing check
#: cannot be re-started in a tight loop against the database.
COOLDOWN_SECONDS = 60
#: Upper bound for the exact count. Well above the 181 s observed on DEV, short
#: enough that a wedged count cannot hold the single slot for hours.
STATEMENT_TIMEOUT_SECONDS = 1800
#: Advisory-lock key that serialises `claim_next` across app instances (arbitrary constant).
CLAIM_LOCK_KEY = 2026112401


class IqviaSnapshotReconciliation(Base):
    """One exact reconciliation attempt for one snapshot. Terminal-immutable: a
    row is updated while QUEUED/CHECKING (claim, heartbeat, finish) and never
    again once PASSED/FAILED; a new attempt is a new row."""

    __tablename__ = "iqvia_snapshot_reconciliation"

    STATE_QUEUED = "QUEUED"
    STATE_CHECKING = "CHECKING"
    STATE_PASSED = "PASSED"
    STATE_FAILED = "FAILED"

    KIND_MISMATCH = "MISMATCH"        # the count ran and the snapshot does not reconcile
    KIND_CHECK_ERROR = "CHECK_ERROR"  # the count could not complete (timeout, error, lost worker)
    KIND_CHANGED = "CHANGED_DURING_CHECK"  # staged data changed while counting

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(UUID(as_uuid=True),
                         ForeignKey("source_snapshot.id", ondelete="RESTRICT"),
                         nullable=False)
    state = Column(String(16), nullable=False, default=STATE_QUEUED)
    failure_kind = Column(String(32))
    staging_token = Column(String(64), nullable=False)
    #: True while QUEUED/CHECKING, NULL when terminal -- keys the partial unique index.
    active_marker = Column(Boolean, nullable=True)
    requested_by = Column(String(320), nullable=False)
    #: Database clock (now()), never the process clock.
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    heartbeat_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True))
    duration_ms = Column(Integer)
    staged_count = Column(Integer)
    expected_count = Column(Integer)
    declared_record_count = Column(Integer)
    rows_rejected = Column(Integer)
    refusal_reasons = Column(JSONB)
    error_reason = Column(Text)

    __table_args__ = (
        Index("uq_iqvia_reconciliation_active_snapshot", "snapshot_id", "active_marker",
              unique=True, postgresql_where=text("active_marker IS TRUE")),
        Index("idx_iqvia_reconciliation_snapshot_started", "snapshot_id", "started_at"),
    )


# ── recorded-at-import counters (no count, no write) ─────────────────────────

def recorded_counters(metadata: Optional[Dict[str, Any]], record_count: Optional[int]) -> Dict[str, Any]:
    """What the importer recorded, and nothing recomputed.

    `rows_inserted_latest_attempt` is what the latest COMPLETED attempt newly
    inserted; `rows_already_present` is what that attempt found already staged
    (a resumed upload). `rows_processed_at_import` is their sum -- the
    importer's own arithmetic (every non-rejected row read), NOT a count of the
    table: an in-file duplicate key lands in `rows_already_present`, so the
    table can hold fewer rows than this. The exact staged total exists only
    once the reconciliation has run (`staged_row_count`)."""
    metadata = metadata or {}
    summary = metadata.get("import_summary") or {}
    preflight = metadata.get("reference_preflight") or {}
    inserted = summary.get("rows_staged")
    already = summary.get("rows_already_staged")
    processed = None if inserted is None and already is None else (inserted or 0) + (already or 0)
    return {
        "basis": "recorded_at_import",
        "label": "Recorded at import",
        "rows_read": summary.get("rows_read"),
        "rows_rejected": summary.get("rows_rejected", metadata.get("rows_rejected")),
        "rows_inserted_latest_attempt": inserted,
        "rows_already_present": already,
        "rows_processed_at_import": processed,
        "rows_processed_label": "Rows processed at import (not a table count)",
        "declared_record_count": record_count,
        "resumed": bool(already),
        "reference_preflight_gate": preflight.get("gate"),
        "note": ("Counters the importer recorded; the table was not counted for this "
                 "response. The exact staged total is the reconciliation result."),
    }


# ── the staged-data fingerprint ──────────────────────────────────────────────

def compute_staging_token(snapshot, latest_job, has_successor: bool) -> str:
    meta = snapshot.metadata_ or {}
    payload = {
        "sha256": snapshot.sha256, "status": snapshot.status, "has_successor": bool(has_successor),
        "record_count": snapshot.record_count,
        "rows_rejected": meta.get("rows_rejected"),
        "import_status": meta.get("import_status"),
        "import_summary": meta.get("import_summary"),
        "preflight_gate": (meta.get("reference_preflight") or {}).get("gate"),
        "job": None if latest_job is None else {
            "id": str(latest_job.id), "attempt_count": latest_job.attempt_count,
            "state": latest_job.state,
            "completed_at": latest_job.completed_at.isoformat() if latest_job.completed_at else None},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def current_staging_token(db, snapshot) -> str:
    successor = (await db.execute(
        select(sm.SourceSnapshot.id).where(sm.SourceSnapshot.supersedes_snapshot_id == snapshot.id)
        .limit(1))).first() is not None
    return compute_staging_token(snapshot, await jobs.latest_job_for_snapshot(db, snapshot.id), successor)


# ── reads (never write) ──────────────────────────────────────────────────────

async def latest_reconciliation(db, snapshot_id) -> Tuple[Optional[IqviaSnapshotReconciliation], Optional[float]]:
    """The newest row and its heartbeat age in seconds on the DATABASE clock."""
    r = (await db.execute(
        select(IqviaSnapshotReconciliation,
               func.extract("epoch", func.now() - IqviaSnapshotReconciliation.heartbeat_at),
               func.extract("epoch", func.now() - IqviaSnapshotReconciliation.started_at),
               func.extract("epoch", func.now() - IqviaSnapshotReconciliation.finished_at))
        .where(IqviaSnapshotReconciliation.snapshot_id == snapshot_id)
        .order_by(IqviaSnapshotReconciliation.started_at.desc(), IqviaSnapshotReconciliation.id.desc())
        .limit(1))).first()
    if r is None:
        return None, None
    row, hb_age, started_age, finished_age = r
    row._hb_age = float(hb_age or 0)       # transient, never persisted
    row._started_age = float(started_age or 0)
    row._finished_age = None if finished_age is None else float(finished_age)
    return row, row._hb_age


def _lost(row: IqviaSnapshotReconciliation) -> bool:
    if row.state == row.STATE_CHECKING:
        return getattr(row, "_hb_age", 0) > STALE_AFTER_SECONDS
    if row.state == row.STATE_QUEUED:
        return getattr(row, "_started_age", 0) > QUEUED_MAX_SECONDS
    return False


def describe(row: Optional[IqviaSnapshotReconciliation], current_token: str) -> Dict[str, Any]:
    """The portal's view: Checking / Passed / Failed / Not checked. A PASSED
    result for different staged data is reported as Not checked (stale), never
    as Passed. `finished_at` is always shown: PASSED means "the table count
    matched at that time"."""
    if row is None:
        return {"status": "not_checked", "label": "Not checked", "exact": True,
                "reason": "No exact reconciliation has been run for this snapshot."}
    base = {"exact": True, "reconciliation_id": str(row.id),
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "duration_ms": row.duration_ms, "requested_by": row.requested_by}
    if row.state in (row.STATE_QUEUED, row.STATE_CHECKING):
        if _lost(row):
            return {**base, "status": "failed", "label": "Failed", "failure_kind": row.KIND_CHECK_ERROR,
                    "reason": "The check stopped reporting progress. Start a new check."}
        return {**base, "status": "checking", "label": "Checking",
                "reason": "The exact count is queued or running in the background."}
    if row.staging_token != current_token:
        return {**base, "status": "not_checked", "label": "Not checked", "stale": True,
                "reason": "The staged data changed after this result. A fresh check is required."}
    counts = {"staged_row_count": row.staged_count, "expected_staged_count": row.expected_count,
              "declared_record_count": row.declared_record_count, "rows_rejected": row.rows_rejected}
    if row.state == row.STATE_PASSED:
        return {**base, **counts, "status": "passed", "label": "Passed", "reconciles": True,
                "refusal_reasons": []}
    return {**base, **counts, "status": "failed", "label": "Failed", "reconciles": False,
            "failure_kind": row.failure_kind, "refusal_reasons": row.refusal_reasons or [],
            "reason": row.error_reason}


async def view_for_snapshot(db, snapshot) -> Dict[str, Any]:
    """The reconciliation view for Open / status polling. Pure read, never a count.

    A PENDING snapshot nobody has approved: `describe` against the current token.
    A snapshot that HAS BEEN APPROVED - the approved successor row, or the PENDING
    row it was approved from - shows the check the approval rested on. Approval
    writes a successor, which necessarily changes the token, so `describe` alone
    would report that passed check as stale / not checked on a snapshot that was
    approved correctly. An approval made before these checks were recorded says so."""
    approved_from = snapshot.supersedes_snapshot_id if snapshot.status != sm.SNAPSHOT_PENDING else None
    if approved_from is None and snapshot.status == sm.SNAPSHOT_PENDING:
        has_successor = (await db.execute(
            select(sm.SourceSnapshot.id).where(sm.SourceSnapshot.supersedes_snapshot_id == snapshot.id)
            .limit(1))).first() is not None
        if not has_successor:
            latest, _ = await latest_reconciliation(db, snapshot.id)
            return describe(latest, await current_staging_token(db, snapshot))
        approved_from = snapshot.id
    if approved_from is None:
        return {"status": "not_recorded", "label": "Not recorded", "exact": True,
                "reason": "This snapshot is not pending and records no exact reconciliation."}
    row = (await db.execute(
        select(IqviaSnapshotReconciliation)
        .where(IqviaSnapshotReconciliation.snapshot_id == approved_from,
               IqviaSnapshotReconciliation.state == IqviaSnapshotReconciliation.STATE_PASSED)
        .order_by(IqviaSnapshotReconciliation.finished_at.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        return {"status": "not_recorded", "label": "Not recorded", "exact": True,
                "checked_snapshot_id": str(approved_from),
                "reason": "Approved before exact reconciliation results were recorded."}
    return {"status": "passed_before_approval", "label": "Passed before approval", "exact": True,
            "reconciles": True, "refusal_reasons": [], "reconciliation_id": str(row.id),
            "checked_snapshot_id": str(approved_from),
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "duration_ms": row.duration_ms, "requested_by": row.requested_by,
            "staged_row_count": row.staged_count, "expected_staged_count": row.expected_count,
            "declared_record_count": row.declared_record_count, "rows_rejected": row.rows_rejected}


async def approval_gate(db, snapshot) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """May this snapshot be approved on reconciliation grounds? Pure read.
    Returns (ok, code, view). The caller holds the snapshot row lock."""
    active = await jobs.active_job_for_snapshot(db, snapshot.id)
    if active is not None:
        return False, "IMPORT_IN_PROGRESS", {"import_job_state": active.state}
    token = await current_staging_token(db, snapshot)
    row, _ = await latest_reconciliation(db, snapshot.id)
    view = describe(row, token)
    if row is None:
        return False, "RECONCILIATION_REQUIRED", view
    code = {"checking": "RECONCILIATION_IN_PROGRESS", "not_checked": "RECONCILIATION_STALE",
            "failed": "RECONCILIATION_FAILED"}.get(view["status"])
    return (code is None), code, view


# ── start (single-flight, enqueue only) ──────────────────────────────────────

class ReconciliationRefused(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def request_reconciliation(db, snapshot_id, *, requested_by: str
                                 ) -> Tuple[IqviaSnapshotReconciliation, str]:
    """Enqueue an exact reconciliation. Returns (row, outcome) where outcome is
    `started` (a new QUEUED row), `already_active` (one is queued/running -- the
    page opened twice) or `already_passed` (a PASSED result for the current
    staged data exists -- nothing to recount). This function never runs a count
    and never repairs state: lost rows are closed by the reaper tick."""
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    if snapshot is None:
        raise ReconciliationRefused("NOT_FOUND", f"no snapshot {snapshot_id}")
    if snapshot.status != sm.SNAPSHOT_PENDING:
        raise ReconciliationRefused("NOT_PENDING", f"snapshot is {snapshot.status}, not PENDING")
    status = (snapshot.metadata_ or {}).get("import_status")
    if status != "completed":
        raise ReconciliationRefused("IMPORT_NOT_COMPLETE", f"import_status is {status!r}, not 'completed'")
    if await jobs.active_job_for_snapshot(db, snapshot_id) is not None:
        raise ReconciliationRefused("IMPORT_IN_PROGRESS", "an import job is still active")

    token = await current_staging_token(db, snapshot)
    latest, _ = await latest_reconciliation(db, snapshot_id)
    if latest is not None:
        if latest.state in (latest.STATE_QUEUED, latest.STATE_CHECKING) and not _lost(latest):
            return latest, "already_active"
        if latest.state == latest.STATE_PASSED and latest.staging_token == token:
            return latest, "already_passed"
        if latest.state == latest.STATE_FAILED and getattr(latest, "_finished_age", None) is not None:
            age = latest._finished_age
            if age < COOLDOWN_SECONDS and latest.staging_token == token:
                raise ReconciliationRefused(
                    "RECONCILIATION_COOLDOWN",
                    f"the last check failed {int(age)} s ago; retry after {COOLDOWN_SECONDS} s")
    row = IqviaSnapshotReconciliation(
        id=uuid.uuid4(), snapshot_id=snapshot_id, state=IqviaSnapshotReconciliation.STATE_QUEUED,
        staging_token=token, active_marker=True, requested_by=(requested_by or "UNKNOWN")[:320],
        declared_record_count=snapshot.record_count)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # Another request inserted the active row first (or a lost row still holds
        # the slot until the reaper closes it).
        await db.rollback()
        existing = (await db.execute(
            select(IqviaSnapshotReconciliation)
            .where(IqviaSnapshotReconciliation.snapshot_id == snapshot_id,
                   IqviaSnapshotReconciliation.active_marker.is_(True)))).scalars().first()
        if existing is None:
            raise
        return existing, "already_active"
    return row, "started"


# ── scheduler side: claim, run, reap ─────────────────────────────────────────

async def claim_next(db) -> Optional[IqviaSnapshotReconciliation]:
    """Take the oldest QUEUED row -> CHECKING. At most ONE check runs anywhere:
    if any CHECKING row has a live heartbeat, nothing is claimed (each check
    needs two pool connections and a full index scan; the pool is small).

    The busy check and the claim are serialised across app instances by a
    transaction-scoped advisory lock: without it two replicas could both see
    "nothing running" and each claim a different QUEUED row. The lock ends with
    the caller's transaction (the commit below, or the tick closing its session
    when nothing is claimed); this function does not roll the caller back."""
    got = (await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": CLAIM_LOCK_KEY})).scalar()
    if not got:
        return None
    busy = (await db.execute(
        select(IqviaSnapshotReconciliation.id)
        .where(IqviaSnapshotReconciliation.state == IqviaSnapshotReconciliation.STATE_CHECKING,
               IqviaSnapshotReconciliation.heartbeat_at
               > func.now() - text(f"interval '{int(STALE_AFTER_SECONDS)} seconds'"))
        .limit(1))).first()
    if busy is not None:
        return None
    row = (await db.execute(
        select(IqviaSnapshotReconciliation)
        .where(IqviaSnapshotReconciliation.state == IqviaSnapshotReconciliation.STATE_QUEUED)
        .order_by(IqviaSnapshotReconciliation.started_at.asc()).limit(1)
        .with_for_update(skip_locked=True))).scalar_one_or_none()
    if row is None:
        return None
    row.state = IqviaSnapshotReconciliation.STATE_CHECKING
    row.heartbeat_at = func.now()
    await db.commit()
    await db.refresh(row)
    return row


async def reap_stale(db) -> int:
    """Close rows whose worker is gone. Owned by the scheduler's reaper tick;
    GET handlers only DESCRIBE a lost row as Failed, they never repair it."""
    r1 = await db.execute(
        update(IqviaSnapshotReconciliation)
        .where(IqviaSnapshotReconciliation.state == IqviaSnapshotReconciliation.STATE_CHECKING,
               IqviaSnapshotReconciliation.heartbeat_at
               < func.now() - text(f"interval '{int(STALE_AFTER_SECONDS)} seconds'"))
        .values(state=IqviaSnapshotReconciliation.STATE_FAILED,
                failure_kind=IqviaSnapshotReconciliation.KIND_CHECK_ERROR, active_marker=None,
                finished_at=func.now(), error_reason="WORKER_LOST"))
    r2 = await db.execute(
        update(IqviaSnapshotReconciliation)
        .where(IqviaSnapshotReconciliation.state == IqviaSnapshotReconciliation.STATE_QUEUED,
               IqviaSnapshotReconciliation.started_at
               < func.now() - text(f"interval '{int(QUEUED_MAX_SECONDS)} seconds'"))
        .values(state=IqviaSnapshotReconciliation.STATE_FAILED,
                failure_kind=IqviaSnapshotReconciliation.KIND_CHECK_ERROR, active_marker=None,
                finished_at=func.now(), error_reason="NEVER_CLAIMED"))
    await db.commit()
    return (r1.rowcount or 0) + (r2.rowcount or 0)


async def run_reconciliation(reconciliation_id: str) -> None:
    """Execute one CHECKING row (already claimed) to a terminal state. Opens its
    own sessions: the counting session is busy for the whole count, so the
    heartbeat uses another. Compares the staging token before AND after the
    count; a change is FAILED, never PASSED."""
    from app.core.database import async_session_maker

    rid = uuid.UUID(reconciliation_id)
    stop = asyncio.Event()

    async def _beat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                try:
                    async with async_session_maker() as hb:
                        await hb.execute(
                            update(IqviaSnapshotReconciliation)
                            .where(IqviaSnapshotReconciliation.id == rid,
                                   IqviaSnapshotReconciliation.active_marker.is_(True))
                            .values(heartbeat_at=func.now()))
                        await hb.commit()
                except Exception:  # noqa: BLE001 -- a missed beat must not kill the count
                    logger.warning("IQVIA reconciliation %s: heartbeat write failed", rid, exc_info=True)

    t0 = time.monotonic()
    beat = asyncio.create_task(_beat())
    outcome: Dict[str, Any]
    try:
        async with async_session_maker() as db:
            row = await db.get(IqviaSnapshotReconciliation, rid)
            if row is None or row.state != row.STATE_CHECKING:
                return
            snapshot_id, token = row.snapshot_id, row.staging_token
            try:
                snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
                if await current_staging_token(db, snapshot) != token:
                    outcome = {"state": "FAILED", "failure_kind": row.KIND_CHANGED,
                               "error_reason": "STAGED_DATA_CHANGED_BEFORE_COUNT", "counts": None}
                else:
                    await db.rollback()
                    await db.execute(text(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_SECONDS) * 1000}"))
                    result = await ii.verify_snapshot_staged_completely(db, snapshot_id)
                    await db.rollback()  # read-only; releases the SET LOCAL
                    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
                    if await current_staging_token(db, snapshot) != token:
                        outcome = {"state": "FAILED", "failure_kind": row.KIND_CHANGED,
                                   "error_reason": "STAGED_DATA_CHANGED_DURING_COUNT", "counts": result}
                    elif result["reconciles"]:
                        outcome = {"state": "PASSED", "counts": result}
                    else:
                        outcome = {"state": "FAILED", "failure_kind": row.KIND_MISMATCH,
                                   "error_reason": "COUNT_DOES_NOT_RECONCILE", "counts": result}
            except Exception as exc:  # noqa: BLE001 -- the job must always reach a terminal state
                await db.rollback()
                logger.error("IQVIA reconciliation %s failed: %s", rid, type(exc).__name__, exc_info=True)
                # Failure CODE only: raw exception text may quote a value.
                outcome = {"state": "FAILED", "failure_kind": row.KIND_CHECK_ERROR,
                           "error_reason": f"CHECK_ERROR:{type(exc).__name__}"[:120], "counts": None}
    finally:
        stop.set()
        await beat

    counts = outcome.get("counts") or {}
    async with async_session_maker() as db:
        await db.execute(
            update(IqviaSnapshotReconciliation)
            .where(IqviaSnapshotReconciliation.id == rid,
                   IqviaSnapshotReconciliation.active_marker.is_(True))
            .values(state=outcome["state"], failure_kind=outcome.get("failure_kind"),
                    active_marker=None, finished_at=func.now(),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    staged_count=counts.get("staged_row_count"),
                    expected_count=counts.get("expected_staged_count"),
                    declared_record_count=counts.get("declared_record_count"),
                    rows_rejected=counts.get("rows_rejected"),
                    refusal_reasons=counts.get("refusal_reasons") if counts else None,
                    error_reason=outcome.get("error_reason")))
        await db.commit()
