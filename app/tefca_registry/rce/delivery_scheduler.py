"""Poller and reaper for official ONC/RCE delivery jobs.

FOLLOWS `export_scheduler`, DOES NOT EDIT IT
────────────────────────────────────────────
Bulletin Intelligence, the TEFCA QA monitor, PPEF ingestion and controlled
exports each own an AsyncIOScheduler. This is the same shape for a fifth kind of
work, for the reason each of those gave in turn: a scheduler serving two
unrelated domains has to be gated, configured and reasoned about for both at
once.

IT STORES NOTHING
─────────────────
APScheduler's default MemoryJobStore dies with the process, which is exactly the
failure `rce_delivery_jobs` exists to survive. Every fact lives in the database
and this module only asks questions of it:

  poller  — claim one QUEUED delivery and process it.
  reaper  — fail deliveries whose heartbeat went stale.

The reaper is deliberately NOT conditional on anything the poller is conditional
on. Housekeeping must keep working even where processing is refused, or turning
processing off would leave dead jobs looking alive.

MULTI-WORKER
────────────
`claim_next_queued` uses SELECT ... FOR UPDATE SKIP LOCKED and the job table
carries a partial unique index over active jobs, so duplicate execution is
prevented by the DATABASE rather than by the topology. APScheduler 3.x
coordinates nothing of its own, so the topology still has to be reviewed before
scaling out.

ONE DELIVERY AT A TIME
──────────────────────
`max_instances=1` plus a single claim per tick. Two concurrent ingestions would
contend for the same connection pool and the same 2,000-row batch writes for no
throughput gain, and a delivery is not a latency-sensitive operation — it is
registered once a period.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_scheduler = None

#: Short, because the whole point is that an operator who just registered a
#: delivery sees it move to PROCESSING quickly rather than sitting at QUEUED.
POLL_INTERVAL_SECONDS = 5

#: The reaper is housekeeping; it does not need to be prompt.
REAP_INTERVAL_SECONDS = 120


async def _poll_tick():
    """Claim and process one queued delivery."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce.delivery_runner import run_delivery_job

    try:
        async with async_session_maker() as db:
            job = await jobs.claim_next_queued(db)
            if job is None:
                return
            logger.info("delivery poller claimed job %s (%s)", job.id,
                        job.delivery_label or job.original_filename)
            state = await run_delivery_job(db, job)
            logger.info("delivery job %s finished: %s", job.id, state)
    except Exception as exc:  # noqa: BLE001
        # A tick that raises must not stop the scheduler. The job it was working
        # on keeps its heartbeat; if the process really is broken the reaper
        # will notice the silence.
        logger.error("delivery poll tick error: %s", exc)


async def _reap_tick():
    """Fail delivery jobs whose worker stopped heartbeating, and say so."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import delivery_jobs as jobs

    try:
        async with async_session_maker() as db:
            reaped = await jobs.reap_stale_jobs(db)
        for job in reaped:
            await _audit_reaped(job)
    except Exception as exc:  # noqa: BLE001
        logger.error("delivery reap tick error: %s", exc)


async def _audit_reaped(job) -> None:
    """A reaped delivery is an operational event, so it is recorded as one.

    Written through the existing registry audit trail rather than a second log
    of its own. The record carries who registered it, which delivery, and what
    happened — never a Government value.

    `entity_id` is left NULL and the job id travels in the metadata. That column
    is a foreign key to `tefca_reg_entities`; a delivery job is not an entity,
    and putting its id there would either break the constraint or, worse,
    collide with a real entity's id.

    `audit.record` stages the row and deliberately does not commit — the caller
    owns the transaction — so this commits its own session.
    """
    from app.core.database import async_session_maker

    try:
        from app.tefca_registry import audit as reg_audit

        async with async_session_maker() as db:
            reg_audit.record(
                db,
                "rce_delivery_job_reaped",
                actor_email=job.get("registered_by") or "SYSTEM",
                metadata={
                    "job_id": job.get("job_id"),
                    "delivery_label": job.get("delivery_label"),
                    "intake_id": job.get("intake_id"),
                    "stage_reached": job.get("stage"),
                    "last_heartbeat": job.get("last_heartbeat"),
                    "note": ("No heartbeat within the stale threshold; the job "
                             "was marked failed and its registration slot "
                             "released. Any Area 1 already written is intact "
                             "and remains addressable."),
                })
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("could not audit reaped delivery job %s: %s",
                     job.get("job_id"), exc)


def start_delivery_scheduler():
    """Start the delivery poller + reaper. Safe to call once at app startup."""
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = AsyncIOScheduler()
        defaults = dict(coalesce=True, misfire_grace_time=120,
                        replace_existing=True, max_instances=1)

        _scheduler.add_job(_poll_tick,
                           IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
                           id="rce_delivery_poller",
                           name="Official ONC/RCE delivery queue poller",
                           **defaults)
        _scheduler.add_job(_reap_tick,
                           IntervalTrigger(seconds=REAP_INTERVAL_SECONDS),
                           id="rce_delivery_reaper",
                           name="Stale delivery job reaper", **defaults)
        _scheduler.start()
        logger.info("delivery scheduler started — poller %ss, reaper %ss",
                    POLL_INTERVAL_SECONDS, REAP_INTERVAL_SECONDS)
    except ImportError:
        logger.warning("APScheduler not installed — delivery scheduler not started")
    except Exception as exc:  # noqa: BLE001
        logger.error("delivery scheduler failed to start: %s", exc)


def scheduler_status() -> dict:
    """What the scheduler is doing, for health reporting."""
    if _scheduler is None:
        return {"running": False, "jobs": []}
    try:
        return {
            "running": _scheduler.running,
            "jobs": [{"id": j.id, "name": j.name,
                      "next_run": (j.next_run_time.isoformat()
                                   if j.next_run_time else None)}
                     for j in _scheduler.get_jobs()],
        }
    except Exception:  # noqa: BLE001
        return {"running": False, "jobs": []}
