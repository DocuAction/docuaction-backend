"""Durable lifecycle for IQVIA chunked uploads and their staging/import jobs.

Reuses the exact concurrency pattern `app/reports/data/export_jobs.py` proved
for controlled exports: `SELECT ... FOR UPDATE SKIP LOCKED` to claim, a
partial unique index to make concurrent duplicate work impossible at the
database level (not the application's), and a heartbeat + reaper to turn
worker death into a bounded, visible retry instead of a job stuck RUNNING
forever. See `iqvia_upload_models.py` for why these three tables exist.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.tefca_registry.rce.iqvia_upload_models import (IqviaImportJob,
                                                         IqviaUploadChunk,
                                                         IqviaUploadSession)

logger = logging.getLogger(__name__)

#: Comfortably longer than one chunk's upload should ever take, short enough
#: that an abandoned upload does not block a resume check indefinitely.
STALE_HEARTBEAT_SECONDS = 900


async def create_upload_session(db, *, source: str, label: str, total_size: int,
                                chunk_size: int, total_chunks: int, temp_path: str,
                                created_by: str) -> IqviaUploadSession:
    session = IqviaUploadSession(
        source=source, label=label, total_size=total_size, chunk_size=chunk_size,
        total_chunks=total_chunks, temp_path=temp_path, created_by=created_by,
        status=IqviaUploadSession.STATUS_UPLOADING)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_upload_session(db, upload_id) -> Optional[IqviaUploadSession]:
    return await db.get(IqviaUploadSession, upload_id)


async def record_chunk_received(db, *, upload_id, chunk_index: int) -> None:
    """Idempotent: retrying the same chunk index is a no-op here (the bytes
    themselves are overwritten at their file offset regardless; this table
    only has to not double-count a retry as new progress)."""
    stmt = pg_insert(IqviaUploadChunk).values(
        upload_id=upload_id, chunk_index=chunk_index).on_conflict_do_nothing(
        index_elements=["upload_id", "chunk_index"])
    await db.execute(stmt)
    await db.commit()


async def received_chunk_indices(db, upload_id) -> set:
    rows = (await db.execute(
        select(IqviaUploadChunk.chunk_index)
        .where(IqviaUploadChunk.upload_id == upload_id))).scalars().all()
    return set(rows)


async def mark_upload_complete(db, upload_id, *, snapshot_id) -> None:
    session = await db.get(IqviaUploadSession, upload_id)
    if session is None:
        return
    session.status = IqviaUploadSession.STATUS_STAGING_QUEUED
    session.snapshot_id = snapshot_id
    await db.commit()


async def mark_upload_staged(db, upload_id) -> None:
    session = await db.get(IqviaUploadSession, upload_id)
    if session is None:
        return
    session.status = IqviaUploadSession.STATUS_STAGED
    await db.commit()


async def mark_upload_failed(db, upload_id) -> None:
    session = await db.get(IqviaUploadSession, upload_id)
    if session is None:
        return
    session.status = IqviaUploadSession.STATUS_FAILED
    await db.commit()


class ImportJobConflict(RuntimeError):
    """An import job for this snapshot is already active."""


async def enqueue_import_job(db, *, snapshot_id, source: str, file_path: str,
                             label: str, created_by: str,
                             upload_id=None) -> IqviaImportJob:
    """Create a QUEUED job, or return the already-active one for this
    snapshot. The partial unique index on (snapshot_id) WHERE active_marker
    IS TRUE is what actually prevents two jobs racing for the same
    snapshot -- this function's job is to make that collision land as "here
    is the existing job", not as an unhandled IntegrityError."""
    existing = await active_job_for_snapshot(db, snapshot_id)
    if existing is not None:
        return existing

    job = IqviaImportJob(
        upload_id=upload_id, snapshot_id=snapshot_id, source=source,
        file_path=file_path, label=label, created_by=created_by,
        state=IqviaImportJob.STATE_QUEUED, active_marker=True, attempt_count=0)
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await active_job_for_snapshot(db, snapshot_id)
        if winner is None:
            raise ImportJobConflict(
                f"an import job for snapshot {snapshot_id} was refused as a "
                "duplicate, but no active job could be read back")
        return winner
    await db.refresh(job)
    return job


async def active_job_for_snapshot(db, snapshot_id) -> Optional[IqviaImportJob]:
    return (await db.execute(
        select(IqviaImportJob)
        .where(IqviaImportJob.snapshot_id == snapshot_id,
              IqviaImportJob.active_marker.is_(True)))).scalars().first()


async def latest_job_for_snapshot(db, snapshot_id) -> Optional[IqviaImportJob]:
    """The most recent job for this snapshot, active or terminal -- for
    status display, where a caller wants to see FAILED/SUCCEEDED too, not
    just a currently-active job."""
    return (await db.execute(
        select(IqviaImportJob)
        .where(IqviaImportJob.snapshot_id == snapshot_id)
        .order_by(IqviaImportJob.created_at.desc())
        .limit(1))).scalars().first()


async def claim_next_queued(db) -> Optional[IqviaImportJob]:
    """Take the oldest QUEUED job and move it to RUNNING. `FOR UPDATE
    SKIP LOCKED` is what makes this safe if more than one poller is ever
    running: two claimers lock different rows instead of both picking up
    the same job."""
    job = (await db.execute(
        select(IqviaImportJob)
        .where(IqviaImportJob.state == IqviaImportJob.STATE_QUEUED)
        .order_by(IqviaImportJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True))).scalar_one_or_none()
    if job is None:
        return None
    job.state = IqviaImportJob.STATE_RUNNING
    job.started_at = datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    job.attempt_count = (job.attempt_count or 0) + 1
    await db.commit()
    await db.refresh(job)
    return job


async def heartbeat(db, job_id, phase: Optional[str] = None) -> None:
    job = await db.get(IqviaImportJob, job_id)
    if job is None:
        return
    job.heartbeat_at = datetime.utcnow()
    if phase:
        job.phase = phase
    await db.commit()


async def finish_succeeded(db, job_id) -> None:
    job = await db.get(IqviaImportJob, job_id)
    if job is None:
        return
    job.state = IqviaImportJob.STATE_SUCCEEDED
    job.phase = "Staged"
    job.completed_at = datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    job.active_marker = None
    await db.commit()


async def finish_failed_or_requeue(db, job_id, reason: str) -> str:
    """A job that has not yet exhausted MAX_ATTEMPTS goes back to QUEUED
    (the importer's own row-level resume means the retry picks up where the
    last attempt left off, never from byte zero). One that has is FAILED
    for a human to look at -- bounded retries, not infinite ones.

    Returns the resulting state, for the caller/tests to assert on."""
    job = await db.get(IqviaImportJob, job_id)
    if job is None:
        return IqviaImportJob.STATE_FAILED
    job.error_reason = (reason or "")[:2000]
    job.heartbeat_at = datetime.utcnow()
    if (job.attempt_count or 0) < IqviaImportJob.MAX_ATTEMPTS:
        job.state = IqviaImportJob.STATE_QUEUED
        job.phase = f"Retry pending (attempt {job.attempt_count})"
        # active_marker stays True -- still the live job for this snapshot,
        # just back in the queue.
    else:
        job.state = IqviaImportJob.STATE_FAILED
        job.phase = "Failed"
        job.failed_at = datetime.utcnow()
        job.active_marker = None
    await db.commit()
    return job.state


async def reap_stale_jobs(db, threshold_seconds: int = STALE_HEARTBEAT_SECONDS
                          ) -> List[Dict[str, Any]]:
    """A RUNNING job whose worker stopped heartbeating (process killed,
    crashed, restarted) is silence, not a report -- this is what turns that
    silence into a bounded retry or a visible FAILED state, exactly the
    export-job reaper's own reasoning."""
    cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
    stale = (await db.execute(
        select(IqviaImportJob)
        .where(IqviaImportJob.state == IqviaImportJob.STATE_RUNNING,
              IqviaImportJob.heartbeat_at < cutoff))).scalars().all()

    reaped = []
    for job in stale:
        reaped.append({"job_id": str(job.id), "snapshot_id": str(job.snapshot_id),
                       "attempt_count": job.attempt_count,
                       "last_heartbeat": job.heartbeat_at.isoformat()
                       if job.heartbeat_at else None})
        if (job.attempt_count or 0) < IqviaImportJob.MAX_ATTEMPTS:
            job.state = IqviaImportJob.STATE_QUEUED
            job.phase = "Reaped -- requeued for retry"
        else:
            job.state = IqviaImportJob.STATE_FAILED
            job.phase = "Failed"
            job.failed_at = datetime.utcnow()
            job.error_reason = "worker_stopped_without_reporting"
            job.active_marker = None
    if reaped:
        await db.commit()
        logger.warning("reaped %d stale IQVIA import job(s)", len(reaped))
    return reaped


async def get_import_job(db, job_id) -> Optional[IqviaImportJob]:
    return await db.get(IqviaImportJob, job_id)
