"""Official ONC/RCE delivery job lifecycle — durable state, database-enforced.

This module owns ONE question: has this delivery been registered, is it
processing, did it finish. It does not parse, does not hash bytes, does not know
what a rule is and does not decide what a record status means. Every one of
those already has an owner in `app/tefca_registry/rce/`, and this hands the work
to them.

THE SHAPE IS `reports/data/export_jobs.py`, DELIBERATELY
────────────────────────────────────────────────────────
Same three-outcome `request_job`, same `FOR UPDATE SKIP LOCKED` claim, same
heartbeat, same reaper. That module in turn took the shape from
`Tefca/ppef_jobs.py`. Three kinds of long work now run the same discipline, and
an operator who has read one queue can read all three.

WHAT A RE-DELIVERY DOES
───────────────────────
`intake.ingest_delivery` accepts byte-identical re-deliveries as their own
intake and links them to the earlier one, because ONC may legitimately resend
and a rejected re-delivery would leave no record that it arrived. That behaviour
is preserved exactly. What the partial unique index prevents is narrower and
different: two registrations of the SAME bytes under the SAME label while one is
still in flight — a double-click, a refresh, a second browser tab. Once a job is
terminal its `active_marker` is NULL, the index no longer applies, and a genuine
re-delivery registers normally.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


class DeliveryJobConflict(RuntimeError):
    """A registration for these bytes is already in flight."""


#: How long a job may go without writing a heartbeat before the reaper decides
#: the worker is gone. Generous: a 100K-record delivery legitimately spends a
#: long time inside one stage, and killing a slow but healthy ingestion would be
#: far worse than leaving a dead one visible for another few minutes. The runner
#: heartbeats between stages AND inside the long ones.
STALE_HEARTBEAT_SECONDS = 1800

#: Written by the runner while work is in flight.
HEARTBEAT_INTERVAL_SECONDS = 20

REAPED_REASON = "worker_stopped_without_reporting"


def job_identity(*, sha256: str, delivery_label: Optional[str],
                 received_date: Optional[datetime]) -> str:
    """What makes two registrations the SAME registration.

    The bytes, the label the operator gave them, and the receipt date. Not the
    registering operator and not the moment: two people registering the
    September delivery a minute apart have registered ONE delivery, and giving
    them two would put two Area 1 intakes of identical content into the evidence
    store for one arrival.

    The receipt date is in here because it is a real distinction. The same file
    genuinely received twice — ONC resending in October what it sent in
    September — is two deliveries, and recording it as one would lose the second
    arrival.
    """
    material = "|".join([
        sha256,
        (delivery_label or "").strip().lower(),
        received_date.date().isoformat() if received_date else "",
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def active_job(db, identity: str):
    """The in-flight job for this identity, if there is one. Read-only."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    return (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.identity == identity,
               RceDeliveryJob.active_marker.is_(True)))).scalars().first()


async def request_job(db, *, identity: str, original_filename: str,
                      storage_path: str, sha256: str, file_size_bytes: int,
                      registered_by: str,
                      delivery_label: Optional[str] = None,
                      declared_delimiter: Optional[str] = None,
                      received_date: Optional[datetime] = None,
                      government_reference: Optional[str] = None,
                      notes: Optional[str] = None,
                      source_name: Optional[str] = None) -> Any:
    """Return the job for this identity, creating one only if none is active.

    THE THREE OUTCOMES, AND WHY EACH IS RIGHT

      * an ACTIVE job exists — return it. A second click, a refresh, and a poll
        that arrives before the first request committed all land here, and all
        three want the same answer: "it is being processed".
      * no active job — create one QUEUED.
      * two callers race — the database decides. The partial unique index on
        (identity, active_marker) has no window; a check-then-insert does. The
        loser catches IntegrityError and re-reads the winner's row, so both
        callers get a job back and there is still only one.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    existing = await active_job(db, identity)
    if existing is not None:
        return existing

    now = datetime.utcnow()
    job = RceDeliveryJob(
        identity=identity,
        delivery_label=delivery_label,
        original_filename=original_filename,
        storage_path=storage_path,
        sha256=sha256,
        file_size_bytes=file_size_bytes,
        declared_delimiter=declared_delimiter,
        received_date=received_date,
        government_reference=government_reference,
        notes=notes,
        source_name=source_name,
        state=RceDeliveryJob.STATE_QUEUED,
        stage=RceDeliveryJob.STAGE_ACCEPTED,
        active_marker=True,
        registered_by=registered_by,
        created_at=now,
        heartbeat_at=now,
        attempt_count=0,
        stage_detail={},
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
            raise DeliveryJobConflict(
                "This delivery was refused as a duplicate registration, but no "
                "active job could be read back.")
        return winner
    await db.refresh(job)
    return job


async def claim_next_queued(db):
    """Take the oldest QUEUED job and move it to RUNNING.

    `with_for_update(skip_locked=True)` is what makes this safe for more than
    one poller: two claimers lock different rows rather than both picking up the
    same delivery. One worker today; correct regardless.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.state == RceDeliveryJob.STATE_QUEUED)
        .order_by(RceDeliveryJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True))).scalar_one_or_none()
    if job is None:
        return None
    now = datetime.utcnow()
    job.state = RceDeliveryJob.STATE_RUNNING
    job.started_at = now
    job.heartbeat_at = now
    job.attempt_count = (job.attempt_count or 0) + 1
    await db.commit()
    await db.refresh(job)
    return job


async def _running_job(db, job_id, *, act: str):
    """The job, ONLY if it is still RUNNING. None otherwise, and it says why.

    WHY EVERY WRITE GOES THROUGH THIS
    ─────────────────────────────────
    The reaper fails a job whose heartbeat went stale. But a stale heartbeat is
    not proof the worker is dead — it may be alive and deep inside a long stage.
    When that worker finally surfaces and calls `finish_succeeded`, an unguarded
    write would flip the reaped FAILED row back to SUCCEEDED, and worse, the
    reaper had already cleared `active_marker`, so a SECOND registration of the
    same delivery may have been accepted and may now be running in parallel.
    Two workers ingesting the same bytes into Area 1 is exactly the duplication
    the job table exists to prevent.

    So a job that is no longer RUNNING is never written to by a worker. The late
    worker's outcome is logged and discarded; the reaper's verdict stands. A
    delivery that genuinely completed under a reaped job is visible in Area 1
    regardless — `bind_intake` ran before the stages, so the intake id is on
    the row and the dashboard reads the truth from reconciliation.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = await db.get(RceDeliveryJob, job_id)
    if job is None:
        return None
    if job.state != RceDeliveryJob.STATE_RUNNING:
        logger.warning(
            "delivery job %s: %s refused — job is %s (%s), not RUNNING. A late "
            "worker does not overwrite a settled job.",
            job_id, act, job.state, job.error_reason or job.stage)
        return None
    return job


async def heartbeat(db, job_id, *, stage: Optional[str] = None,
                    records_received: Optional[int] = None,
                    records_processed: Optional[int] = None,
                    detail: Optional[Dict[str, Any]] = None) -> None:
    """Say the worker is still alive, and what it has actually observed.

    The stage is a real transition, and the counts are rows the stage itself
    counted. Nothing here is estimated: an operator watching a delivery is
    entitled to assume that a number on the screen was measured.
    """
    job = await _running_job(db, job_id, act="heartbeat")
    if job is None:
        return
    job.heartbeat_at = datetime.utcnow()
    if stage:
        job.stage = stage
    if records_received is not None:
        job.records_received = records_received
    if records_processed is not None:
        job.records_processed = records_processed
    if detail:
        # Replaced wholesale rather than mutated in place: SQLAlchemy does not
        # detect an in-place change to a JSONB dict, and a stage report that
        # silently fails to persist is worse than no stage report.
        merged = dict(job.stage_detail or {})
        merged.update(detail)
        job.stage_detail = merged
    await db.commit()


async def bind_intake(db, job_id, intake_id, *, records_received: int) -> None:
    """Record the Area 1 intake this job produced, as soon as it exists.

    Written in its own commit and as early as possible. From this moment the
    delivery is addressable through the existing `/deliveries/{intake_id}`
    surface even if every later stage fails — which is the point: a delivery
    whose Area 1 landed but whose quality run died must still be findable, not
    orphaned behind a FAILED job row.
    """
    job = await _running_job(db, job_id, act="bind_intake")
    if job is None:
        return
    job.source_intake_id = intake_id
    job.records_received = records_received
    job.heartbeat_at = datetime.utcnow()
    await db.commit()


async def finish_succeeded(db, job_id, *, reconciliation_passed: bool,
                           records_processed: Optional[int] = None,
                           detail: Optional[Dict[str, Any]] = None) -> None:
    """Terminal success: the delivery is processed and ready for review.

    SUCCEEDED means the pipeline RAN to completion, not that reconciliation
    passed. Those are different facts and they are reported separately:
    a delivery can process cleanly end-to-end and still fail the A–F gate, and
    collapsing the two would hide exactly the condition the gate exists to
    surface. The dashboard shows both.

    `active_marker` is cleared LAST and in the same commit: while it is set the
    partial unique index refuses another job for this identity, and clearing it
    is what makes a legitimate re-delivery possible.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = await _running_job(db, job_id, act="finish_succeeded")
    if job is None:
        return
    now = datetime.utcnow()
    job.state = RceDeliveryJob.STATE_SUCCEEDED
    job.stage = RceDeliveryJob.STAGE_READY
    job.completed_at = now
    job.heartbeat_at = now
    job.reconciliation_passed = reconciliation_passed
    if records_processed is not None:
        job.records_processed = records_processed
    if detail:
        merged = dict(job.stage_detail or {})
        merged.update(detail)
        job.stage_detail = merged
    job.active_marker = None
    await db.commit()


async def finish_failed(db, job_id, reason: str, *,
                        detail: Optional[Dict[str, Any]] = None) -> None:
    """Terminal failure, with a reason a person can act on.

    The intake binding is deliberately NOT cleared. If Area 1 landed before the
    failure, that Area 1 exists and is evidence; unbinding it here would leave a
    real intake with nothing pointing at it. The job says what failed and at
    which stage, and the delivery remains addressable.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = await _running_job(db, job_id, act="finish_failed")
    if job is None:
        return
    now = datetime.utcnow()
    job.state = RceDeliveryJob.STATE_FAILED
    job.failed_at = now
    job.heartbeat_at = now
    job.error_reason = (reason or "")[:2000]
    if detail:
        merged = dict(job.stage_detail or {})
        merged.update(detail)
        job.stage_detail = merged
    job.active_marker = None
    await db.commit()


async def reap_stale_jobs(db, threshold_seconds: int = STALE_HEARTBEAT_SECONDS
                          ) -> List[Dict[str, Any]]:
    """Fail jobs whose worker stopped saying anything.

    A process that dies cannot report that it died; the only signal it emits is
    silence. Reading that silence is what turns "RUNNING forever, needs a human"
    into "FAILED, and the identity is free to be registered again".
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
    stale = (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.state.in_(RceDeliveryJob.ACTIVE_STATES),
               RceDeliveryJob.heartbeat_at < cutoff))).scalars().all()

    reaped = []
    for job in stale:
        reaped.append({
            "job_id": str(job.id), "identity": job.identity, "state": job.state,
            "stage": job.stage, "registered_by": job.registered_by,
            "delivery_label": job.delivery_label,
            "intake_id": (str(job.source_intake_id)
                          if job.source_intake_id else None),
            "last_heartbeat": (job.heartbeat_at.isoformat()
                               if job.heartbeat_at else None),
        })
        job.state = RceDeliveryJob.STATE_FAILED
        job.failed_at = datetime.utcnow()
        job.error_reason = REAPED_REASON
        job.active_marker = None
    if reaped:
        await db.commit()
        logger.warning("reaped %d stale delivery job(s)", len(reaped))
    return reaped


async def get_job(db, job_id):
    """One job by id, or None. Reads only — polling must never start work."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    try:
        return await db.get(RceDeliveryJob, job_id)
    except Exception as exc:  # noqa: BLE001 — a malformed id is a 404, not a 500
        logger.info("delivery job lookup failed for %r: %s", job_id, exc)
        return None


async def list_jobs(db, *, limit: int = 50, state: Optional[str] = None):
    """Recent registrations, newest first."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    stmt = select(RceDeliveryJob).order_by(RceDeliveryJob.created_at.desc())
    if state:
        stmt = stmt.where(RceDeliveryJob.state == state)
    return (await db.execute(stmt.limit(limit))).scalars().all()


async def job_for_intake(db, intake_id):
    """The job that produced this Area 1 intake, if one did.

    A delivery ingested through the pre-existing synchronous route has no job,
    and that is not an error — it returns None and the dashboard reports the
    delivery from Area 1 alone.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    return (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.source_intake_id == intake_id)
        .order_by(RceDeliveryJob.created_at.desc()))).scalars().first()
