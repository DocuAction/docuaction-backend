"""
PPEF ingestion job lifecycle — durable state, database-enforced.

WHAT THIS FIXES
FastAPI BackgroundTasks ran the ingest in-process. When a worker was recycled
mid-load the task vanished and the snapshot sat at `pending` forever: five such
rows accumulated on dev, every one with `error = None`. Nothing was wrong with
the ingestion itself — what was missing was any record that the work had stopped.

The division of responsibility here is deliberate:

  APScheduler  triggers and polls. It holds NO state — its default MemoryJobStore
               dies with the process, which is the very failure being fixed.
  Database     is the authoritative state store. Every transition, every
               heartbeat, every failure reason is committed before it is believed.

WHY THE HEARTBEAT IS THE CENTRE OF THE DESIGN
A process that dies cannot report that it died. The only signal a dead worker
emits is silence, so liveness has to be inferred from the absence of a recent
write. The reaper reads that silence and marks the job FAILED — which is what
turns "stuck forever, needs a human" into "failed, retry permitted".

ACTIVATION IS UNCHANGED AND DELIBERATELY CONSERVATIVE
A snapshot becomes readable evidence only when its `ingest_status` reaches
`complete`, and that happens only after download, schema validation, truncation
recording, load commit and relational validation have ALL passed. A FAILED,
stale, partial or abandoned job never produces evidence. This module does not
"improve" recovery by salvaging partial loads — a half-loaded quarter is not a
smaller quarter, it is a misleading one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

#: A job whose heartbeat is older than this is presumed dead. Comfortably longer
#: than the gap between heartbeats (one per downloaded chunk / written batch) so
#: a slow CMS response is never mistaken for a dead worker.
STALE_HEARTBEAT_SECONDS = 300

#: How often the runner writes a heartbeat, at most. Bounds database churn on a
#: 3.9M-row load without letting the gap approach the stale threshold.
HEARTBEAT_INTERVAL_SECONDS = 20


class JobConflict(RuntimeError):
    """Another active job already owns this component + version."""


class AlreadyLoaded(RuntimeError):
    """An identical COMPLETE snapshot exists; nothing to do."""


async def find_complete_snapshot(db, component: str, resource_version: str,
                                 file_name: Optional[str] = None):
    """An existing COMPLETE, untruncated snapshot for this exact quarter.

    Identity is (component, resource_version) — the CMS quarterly file is
    uniquely identified by its version stamp, and the checksum cannot be known
    before downloading, so it is compared AFTER the fact rather than used as the
    lookup key.

    Truncated snapshots are excluded: a capped load is not the same artefact,
    and treating it as "already loaded" would permanently block a real one.
    """
    from app.Tefca.models import TEFCAPPEFSnapshot

    stmt = (select(TEFCAPPEFSnapshot)
            .where(TEFCAPPEFSnapshot.component == component)
            .where(TEFCAPPEFSnapshot.ingest_status == "complete")
            .where(TEFCAPPEFSnapshot.resource_version == resource_version)
            .where(TEFCAPPEFSnapshot.rows_truncated.is_(False))
            .order_by(TEFCAPPEFSnapshot.ingested_at.desc())
            .limit(1))
    if file_name:
        stmt = stmt.where(TEFCAPPEFSnapshot.file_name == file_name)
    return (await db.execute(stmt)).scalar_one_or_none()


async def queue_job(db, component: str, resource_version: Optional[str],
                    quarter: Optional[str] = None, requested_by: Optional[str] = None,
                    max_rows: Optional[int] = None):
    """Create a QUEUED job, or refuse because one is already active.

    The refusal is enforced by the database, not by a prior SELECT. A check-then-
    insert has a window between the two statements; the partial unique index has
    none. Two workers racing produce one job and one IntegrityError, and that
    holds if the deployment ever moves past a single worker.
    """
    from app.Tefca.models import TEFCAPPEFIngestJob

    job = TEFCAPPEFIngestJob(
        component=component,
        resource_version=resource_version,
        quarter=quarter,
        state=TEFCAPPEFIngestJob.STATE_QUEUED,
        active_marker=True,          # participates in the partial unique index
        created_at=datetime.utcnow(),
        heartbeat_at=datetime.utcnow(),
        attempt_count=0,
        requested_by=requested_by,
        max_rows=max_rows,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise JobConflict(
            f"An ingestion job for {component} {resource_version or '(unknown version)'} "
            f"is already active. Concurrent loads of the same component and quarter are "
            f"refused at the database level."
        )
    await db.refresh(job)
    return job


async def claim_next_queued(db) -> Optional[Any]:
    """Take the oldest QUEUED job and move it to STARTED.

    `with_for_update(skip_locked=True)` is what makes this safe for more than one
    poller: each claimer locks a different row instead of two of them picking up
    the same job. Single-worker today, correct regardless.
    """
    from app.Tefca.models import TEFCAPPEFIngestJob

    result = await db.execute(
        select(TEFCAPPEFIngestJob)
        .where(TEFCAPPEFIngestJob.state == TEFCAPPEFIngestJob.STATE_QUEUED)
        .order_by(TEFCAPPEFIngestJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return None
    job.state = TEFCAPPEFIngestJob.STATE_STARTED
    job.started_at = datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    job.attempt_count = (job.attempt_count or 0) + 1
    await db.commit()
    await db.refresh(job)
    return job


async def transition(db, job_id, state: str, **fields) -> None:
    """Move a job to `state` and stamp a heartbeat, committing immediately.

    Committed rather than batched on purpose: state that is only true in memory
    is exactly the property this design exists to remove.
    """
    from app.Tefca.models import TEFCAPPEFIngestJob

    values: Dict[str, Any] = {"state": state, "heartbeat_at": datetime.utcnow()}
    values.update(fields)
    await db.execute(
        update(TEFCAPPEFIngestJob)
        .where(TEFCAPPEFIngestJob.id == job_id)
        .values(**values)
    )
    await db.commit()


async def heartbeat(db, job_id) -> None:
    """Record that the worker is still alive."""
    from app.Tefca.models import TEFCAPPEFIngestJob

    await db.execute(
        update(TEFCAPPEFIngestJob)
        .where(TEFCAPPEFIngestJob.id == job_id)
        .values(heartbeat_at=datetime.utcnow())
    )
    await db.commit()


async def finish_complete(db, job_id, snapshot_id, checksum: str, row_count: int) -> None:
    """Terminal success. Clearing active_marker releases the concurrency slot."""
    from app.Tefca.models import TEFCAPPEFIngestJob

    await db.execute(
        update(TEFCAPPEFIngestJob)
        .where(TEFCAPPEFIngestJob.id == job_id)
        .values(state=TEFCAPPEFIngestJob.STATE_COMPLETE,
                active_marker=None,
                completed_at=datetime.utcnow(),
                heartbeat_at=datetime.utcnow(),
                snapshot_id=snapshot_id,
                checksum=checksum,
                row_count=row_count)
    )
    await db.commit()


async def finish_failed(db, job_id, reason: str) -> None:
    """Terminal failure. The slot is released so a clean retry is permitted."""
    from app.Tefca.models import TEFCAPPEFIngestJob

    await db.execute(
        update(TEFCAPPEFIngestJob)
        .where(TEFCAPPEFIngestJob.id == job_id)
        .values(state=TEFCAPPEFIngestJob.STATE_FAILED,
                active_marker=None,
                failed_at=datetime.utcnow(),
                error_reason=(reason or "")[:2000])
    )
    await db.commit()


async def reap_stale_jobs(db, threshold_seconds: int = STALE_HEARTBEAT_SECONDS) -> List[Dict[str, Any]]:
    """Mark jobs whose worker stopped writing heartbeats as FAILED.

    This is the piece that makes worker death recoverable. A dead process cannot
    report its own death, so the only evidence is a heartbeat that stopped
    advancing; past the threshold that silence is treated as failure.

    Any snapshot the dead job was filling stays at `pending` and therefore never
    becomes evidence — `latest_snapshot()` reads `complete` only. The row is left
    in place rather than deleted: it is the record of what happened.

    The orphaned RECORD rows are deliberately NOT deleted here. The reaper acts
    on a job it cannot see and did not run: a worker presumed dead may still hold
    an open connection whose writes have not yet failed, and issuing a bulk
    DELETE against rows another transaction is inserting is how a reaper turns a
    stuck job into a lock pile-up. Marking the snapshot `failed` is enough to keep
    the data unreadable, and `scripts/cleanup_stuck_ppef_snapshots.py` clears the
    rows afterwards, out of band. The in-process failure path (`_abort`) does
    delete them, because there the worker is this process and is demonstrably
    finished with them.
    """
    from app.Tefca.models import TEFCAPPEFIngestJob, TEFCAPPEFSnapshot

    cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
    stale = (await db.execute(
        select(TEFCAPPEFIngestJob)
        .where(TEFCAPPEFIngestJob.state.in_(TEFCAPPEFIngestJob.ACTIVE_STATES))
        .where(TEFCAPPEFIngestJob.heartbeat_at < cutoff)
    )).scalars().all()

    reaped: List[Dict[str, Any]] = []
    for job in stale:
        last = job.heartbeat_at.isoformat() if job.heartbeat_at else "never"
        # Captured BEFORE the mutation below. Reading job.state afterwards would
        # report "FAILED" for every reaped job — true, but useless: the fact an
        # investigation needs is which phase the worker died in.
        was_state = job.state
        reason = (f"worker_died_no_heartbeat: last heartbeat {last}, "
                  f"threshold {threshold_seconds}s, state was {was_state}")
        job.state = TEFCAPPEFIngestJob.STATE_FAILED
        job.active_marker = None
        job.failed_at = datetime.utcnow()
        job.error_reason = reason
        # The half-filled snapshot is marked failed too, so nothing downstream
        # can mistake it for a completed load.
        if job.snapshot_id:
            snap = await db.get(TEFCAPPEFSnapshot, job.snapshot_id)
            if snap is not None and snap.ingest_status not in ("complete",):
                snap.ingest_status = "failed"
                snap.error = reason
        reaped.append({"job_id": str(job.id), "component": job.component,
                       "was_state": was_state, "reason": reason,
                       # Carried out so the caller can AUDIT the failure against
                       # the admin who requested the load, without re-querying.
                       "requested_by": job.requested_by,
                       "snapshot_id": str(job.snapshot_id) if job.snapshot_id else None,
                       "attempt_count": job.attempt_count})
        logger.warning("PPEF reaper: job %s (%s) marked FAILED — %s",
                       job.id, job.component, reason)
    if reaped:
        await db.commit()
    return reaped


#: A `pending` snapshot with NO job row at all is an orphan from before the job
#: table existed. Nothing will ever advance it. Two hours is far beyond the
#: longest real load (the 3.9M-row Reassignment file completes in minutes), so
#: the window cannot catch work that is genuinely in flight.
LEGACY_ORPHAN_HOURS = 2

#: The reason recorded for those rows. Names the cause rather than the symptom:
#: the work did not fail, the worker holding it was recycled and the in-process
#: BackgroundTask went with it.
LEGACY_ORPHAN_REASON = "worker_recycled_before_completion"


async def close_orphaned_snapshots(db, older_than_hours: int = LEGACY_ORPHAN_HOURS
                                   ) -> List[Dict[str, Any]]:
    """Close `pending` snapshots that no job will ever finish.

    WHY THIS BELONGS IN THE REAPER
    Five such rows sit on dev. They were created by the old BackgroundTask path,
    which left no record of any kind when its worker was recycled — so there is
    no job to go stale and nothing for `reap_stale_jobs` to notice. They are the
    same failure as a dead worker, minus the evidence, and closing them is the
    same act: turn "stuck forever, needs a human" into a truthful terminal state.

    WHY THIS CANNOT KILL A LIVE LOAD
    Two independent conditions must both hold:

      1. NO job row references the snapshot. Every snapshot the new mechanism
         creates gets a job row before any bytes are fetched, so a load in flight
         is excluded by this alone.
      2. The snapshot is older than `older_than_hours`, far beyond the longest
         real load.

    The rows are marked, never deleted — they are the record that a load was
    attempted. Their orphaned RECORD rows are left to the out-of-band cleanup
    script for the same reason `reap_stale_jobs` leaves them.
    """
    from app.Tefca.models import TEFCAPPEFIngestJob, TEFCAPPEFSnapshot

    cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
    candidates = (await db.execute(
        select(TEFCAPPEFSnapshot)
        .where(TEFCAPPEFSnapshot.ingest_status == "pending")
        .where(TEFCAPPEFSnapshot.ingested_at < cutoff)
    )).scalars().all()
    if not candidates:
        return []

    claimed = set((await db.execute(
        select(TEFCAPPEFIngestJob.snapshot_id)
        .where(TEFCAPPEFIngestJob.snapshot_id.isnot(None))
    )).scalars().all())

    closed: List[Dict[str, Any]] = []
    for snap in candidates:
        if snap.id in claimed:
            # A job owns this snapshot; reap_stale_jobs decides its fate, not us.
            continue
        snap.ingest_status = "failed"
        snap.error = (f"{LEGACY_ORPHAN_REASON}: no ingestion job was ever recorded "
                      f"for this snapshot and it has been pending since "
                      f"{snap.ingested_at.isoformat() if snap.ingested_at else 'unknown'}. "
                      f"Created by the pre-job BackgroundTask path, which left no "
                      f"record when its worker was recycled.")
        closed.append({"snapshot_id": str(snap.id), "component": snap.component,
                       "pending_since": (snap.ingested_at.isoformat()
                                         if snap.ingested_at else None)})
        logger.warning("PPEF reaper: orphaned snapshot %s (%s) closed as failed — %s",
                       snap.id, snap.component, LEGACY_ORPHAN_REASON)
    if closed:
        await db.commit()
    return closed


async def job_status(db, job_id) -> Optional[Dict[str, Any]]:
    """Persisted job state. Never reads process memory."""
    from app.Tefca.models import TEFCAPPEFIngestJob

    job = await db.get(TEFCAPPEFIngestJob, job_id)
    if job is None:
        return None
    now = datetime.utcnow()
    age = (now - job.heartbeat_at).total_seconds() if job.heartbeat_at else None
    return {
        "job_id": str(job.id),
        "component": job.component,
        "resource_version": job.resource_version,
        "quarter": job.quarter,
        "state": job.state,
        "terminal": job.state in job.TERMINAL_STATES,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        "heartbeat_age_seconds": round(age, 1) if age is not None else None,
        "heartbeat_stale": (age is not None and age > STALE_HEARTBEAT_SECONDS
                            and job.state in job.ACTIVE_STATES),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "failed_at": job.failed_at.isoformat() if job.failed_at else None,
        "attempt_count": job.attempt_count,
        "error_reason": job.error_reason,
        "snapshot_id": str(job.snapshot_id) if job.snapshot_id else None,
        "checksum": job.checksum,
        "row_count": job.row_count,
        "requested_by": job.requested_by,
        "max_rows": job.max_rows,
        "state_note": (
            "State is read from the database, not from process memory. A worker "
            "that dies mid-load stops heartbeating and is marked FAILED by the "
            "reaper; its snapshot never becomes evidence."
        ),
    }
