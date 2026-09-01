"""Controlled export job lifecycle — durable state, database-enforced.

WHY THERE IS A JOB AT ALL
─────────────────────────
Step #17 measured the delivered population at roughly seven and a half minutes
and 1.96 million cells. A browser request cannot be the thing that holds that
work: gateways time out, users refresh, laptops sleep, and a worker recycle
takes the whole export with it. What the caller needs back is not the workbook —
it is a receipt.

WHY THIS IS NOT A SECOND JOB FRAMEWORK
──────────────────────────────────────
`Tefca/ppef_jobs.py` already solved this exact problem for PPEF ingestion, and
it solved it the right way: the database is the authoritative state store, a
partial unique index makes concurrent duplicates impossible, `FOR UPDATE SKIP
LOCKED` makes claiming safe for more than one poller, and a heartbeat plus a
reaper turn "the worker died" from "stuck forever" into "FAILED, retry
permitted". That mechanism is reused here verbatim in shape.

What is NOT reused is the PPEF table. `tefca_ppef_ingest_jobs` is keyed on a
CMS component and quarter and carries a foreign key to a PPEF snapshot; an
export is neither. Overloading it would mean a column that means one thing for
ingestion and another for exports, which is how a shared table stops being
shared and starts being ambiguous.

WHAT A JOB IS ALLOWED TO PRODUCE
────────────────────────────────
An artifact in the EXISTING registry, and nothing else. This module does not
store bytes, does not hash them, does not decide classification and does not
know what a sheet is. It owns one question — has this export been asked for,
is it running, did it finish — and hands everything else to the code that was
certified in Step #17.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


class ExportJobConflict(RuntimeError):
    """An export for this identity is already in flight."""


#: How long a job may go without writing a heartbeat before the reaper decides
#: the worker is gone. Comfortably longer than the measured full-scale render so
#: a slow but healthy export is never killed, and short enough that a dead one
#: does not sit RUNNING overnight.
STALE_HEARTBEAT_SECONDS = 900

#: Written by the runner while work is in flight.
HEARTBEAT_INTERVAL_SECONDS = 20

REAPED_REASON = "worker_stopped_without_reporting"


def job_identity(*, intake_id, workbook_version: str, engine_version: str,
                 classification: str, export_type: str) -> str:
    """What makes two export requests the SAME request.

    Not the requester and not the moment: two people asking for this delivery's
    workbook a minute apart want the same file, and giving them two would put
    two "official" artifacts of identical content into the registry.

    Every dimension that could change the BYTES is in here. The generator and
    engine versions are, because a workbook built by a newer generator is a
    different document even from an unchanged delivery. The classification is,
    because the same data exported under a different label is a different
    artifact with different handling.
    """
    material = "|".join([
        export_type, str(intake_id), workbook_version, engine_version,
        classification,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def request_job(db, *, identity: str, export_type: str, intake_id,
                      classification: str, generator_version: str,
                      requested_by: str) -> Any:
    """Return the job for this identity, creating one only if none is active.

    THE THREE OUTCOMES, AND WHY EACH IS RIGHT

      * an ACTIVE job exists — return it. A second click, a refresh and a poll
        that arrives before the first request committed all land here, and all
        three want the same answer: "it is being made".
      * no active job — create one QUEUED.
      * two callers race — the database decides. The partial unique index on
        (identity, active_marker) has no window; a check-then-insert does. The
        loser catches IntegrityError and re-reads the winner's row, so both
        callers still get a job back and there is still only one.

    A previously SUCCEEDED job is deliberately NOT returned as active. Whether
    its artifact should be reused is a question about ARTIFACTS, and the
    registry already answers it: finalising byte-identical content returns the
    existing registration instead of creating a second one. Answering it here as
    well would be a second cache with its own opinion.
    """
    from app.reports.data.export_job_model import ReportExportJob

    existing = await active_job(db, identity)
    if existing is not None:
        return existing

    job = ReportExportJob(
        identity=identity, export_type=export_type,
        source_intake_id=intake_id, classification=classification,
        generator_version=generator_version,
        state=ReportExportJob.STATE_QUEUED,
        active_marker=True,
        requested_by=requested_by,
        created_at=datetime.utcnow(),
        heartbeat_at=datetime.utcnow(),
        attempt_count=0,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await active_job(db, identity)
        if winner is None:
            # The index refused the insert but nothing active is there to find.
            # Something other than a race is wrong; say so rather than loop.
            raise ExportJobConflict(
                "An export for this delivery was refused as a duplicate, but no "
                "active job could be read back.")
        return winner
    await db.refresh(job)
    return job


async def active_job(db, identity: str):
    """The in-flight job for this identity, if there is one. Read-only."""
    from app.reports.data.export_job_model import ReportExportJob

    return (await db.execute(
        select(ReportExportJob)
        .where(ReportExportJob.identity == identity,
               ReportExportJob.active_marker.is_(True)))).scalars().first()


async def claim_next_queued(db):
    """Take the oldest QUEUED job and move it to RUNNING.

    `with_for_update(skip_locked=True)` is what makes this safe for more than
    one poller: two claimers lock different rows rather than both picking up the
    same job. One worker today; correct regardless.
    """
    from app.reports.data.export_job_model import ReportExportJob

    job = (await db.execute(
        select(ReportExportJob)
        .where(ReportExportJob.state == ReportExportJob.STATE_QUEUED)
        .order_by(ReportExportJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True))).scalar_one_or_none()
    if job is None:
        return None
    job.state = ReportExportJob.STATE_RUNNING
    job.started_at = datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    job.attempt_count = (job.attempt_count or 0) + 1
    await db.commit()
    await db.refresh(job)
    return job


async def heartbeat(db, job_id, phase: Optional[str] = None) -> None:
    """Say the worker is still alive, and optionally what it is doing.

    The phase is a real observation of where the run is, not a percentage. A
    progress bar that cannot measure progress is a decoration that lies.
    """
    from app.reports.data.export_job_model import ReportExportJob

    job = await db.get(ReportExportJob, job_id)
    if job is None:
        return
    job.heartbeat_at = datetime.utcnow()
    if phase:
        job.phase = phase
    await db.commit()


async def finish_succeeded(db, job_id, *, report_id: str, artifact_id: str,
                           artifact_version: int, rendered_sha256: str,
                           size_bytes: int) -> None:
    """Terminal success. The artifact is named, so the job is a receipt for it.

    `active_marker` is cleared LAST and in the same commit: while it is set the
    partial unique index refuses another job for this identity, and clearing it
    is exactly what makes a re-export possible.
    """
    from app.reports.data.export_job_model import ReportExportJob

    job = await db.get(ReportExportJob, job_id)
    if job is None:
        return
    job.state = ReportExportJob.STATE_SUCCEEDED
    job.phase = "Ready"
    job.completed_at = datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    job.report_id = report_id
    job.artifact_id = artifact_id
    job.artifact_version = artifact_version
    job.rendered_sha256 = rendered_sha256
    job.size_bytes = size_bytes
    job.active_marker = None
    await db.commit()


async def finish_failed(db, job_id, reason: str) -> None:
    """Terminal failure, with a reason a person can act on.

    No artifact is named, because none may be trusted: a run that stopped
    part-way has produced either nothing or something incomplete, and an
    incomplete workbook that can be downloaded is worse than a failure that
    cannot.
    """
    from app.reports.data.export_job_model import ReportExportJob

    job = await db.get(ReportExportJob, job_id)
    if job is None:
        return
    job.state = ReportExportJob.STATE_FAILED
    job.phase = "Failed"
    job.failed_at = datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    job.error_reason = (reason or "")[:2000]
    job.report_id = None
    job.artifact_id = None
    job.active_marker = None
    await db.commit()


async def reap_stale_jobs(db, threshold_seconds: int = STALE_HEARTBEAT_SECONDS
                          ) -> List[Dict[str, Any]]:
    """Fail jobs whose worker stopped saying anything.

    A process that dies cannot report that it died; the only signal it emits is
    silence. Reading that silence is what turns "RUNNING forever, needs a human"
    into "FAILED, and the identity is free again".
    """
    from app.reports.data.export_job_model import ReportExportJob

    cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
    stale = (await db.execute(
        select(ReportExportJob)
        .where(ReportExportJob.state.in_(ReportExportJob.ACTIVE_STATES),
               ReportExportJob.heartbeat_at < cutoff))).scalars().all()

    reaped = []
    for job in stale:
        reaped.append({"job_id": str(job.id), "identity": job.identity,
                       "state": job.state, "requested_by": job.requested_by,
                       "last_heartbeat": job.heartbeat_at.isoformat()
                       if job.heartbeat_at else None})
        job.state = ReportExportJob.STATE_FAILED
        job.phase = "Failed"
        job.failed_at = datetime.utcnow()
        job.error_reason = REAPED_REASON
        job.active_marker = None
    if reaped:
        await db.commit()
        logger.warning("reaped %d stale export job(s)", len(reaped))
    return reaped


async def get_job(db, job_id):
    """One job by id, or None. Reads only — polling must never start work."""
    from app.reports.data.export_job_model import ReportExportJob

    try:
        return await db.get(ReportExportJob, job_id)
    except Exception as exc:  # noqa: BLE001 — a malformed id is a 404, not a 500
        logger.info("export job lookup failed for %r: %s", job_id, exc)
        return None
