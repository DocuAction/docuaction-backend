"""Poller and reaper for controlled export jobs.

FOLLOWS `ppef_scheduler`, DOES NOT EDIT IT
──────────────────────────────────────────
Bulletin Intelligence, the TEFCA QA monitor and PPEF ingestion each own an
AsyncIOScheduler. This is the same shape for a fourth kind of work, for the same
reason `ppef_scheduler` gave when it declined to edit the other two: a scheduler
that serves two unrelated domains has to be gated, configured and reasoned about
for both at once.

IT STORES NOTHING
─────────────────
APScheduler's default MemoryJobStore dies with the process, which is exactly the
failure the job table exists to survive. Every fact lives in the database and
this module only asks questions of it:

  poller  — claim one QUEUED job and run it.
  reaper  — fail jobs whose heartbeat went stale.

The reaper is deliberately NOT conditional on anything the poller is conditional
on. Housekeeping must keep working even where generation is refused, or turning
generation off would leave dead jobs looking alive.

MULTI-WORKER
────────────
One gunicorn worker today, and nothing enforces that. If it changes, every
worker starts its own scheduler and polls the same queue. `claim_next_queued`
uses SELECT ... FOR UPDATE SKIP LOCKED and the job table carries a partial
unique index over active jobs, so duplicate execution is prevented by the
DATABASE rather than by the topology. APScheduler 3.x coordinates nothing of its
own, so the topology still has to be reviewed before scaling out — see the
deployment section of the operationalization document.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_scheduler = None

#: Short, because the whole point is that a caller who just pressed the button
#: sees RUNNING quickly rather than QUEUED for a minute.
POLL_INTERVAL_SECONDS = 5

#: The reaper is housekeeping; it does not need to be prompt.
REAP_INTERVAL_SECONDS = 120


async def _poll_tick():
    """Claim and run one queued export.

    One at a time on purpose. A full-population workbook was measured at roughly
    690 MB of peak heap; running two concurrently would double that in one
    process for no gain, since both would be competing for the same threadpool.
    """
    from app.core.database import async_session_maker
    from app.reports.data import export_jobs
    from app.reports.export_runner import run_export_job

    try:
        async with async_session_maker() as db:
            job = await export_jobs.claim_next_queued(db)
            if job is None:
                return
            logger.info("export poller claimed job %s (%s)", job.id,
                        job.export_type)
            state = await run_export_job(db, job)
            logger.info("export job %s finished: %s", job.id, state)
    except Exception as exc:  # noqa: BLE001
        # A tick that raises must not stop the scheduler. The job it was working
        # on keeps its heartbeat; if the process really is broken the reaper
        # will notice the silence.
        logger.error("export poll tick error: %s", exc)


async def _reap_tick():
    """Fail export jobs whose worker stopped heartbeating, and say so."""
    from app.core.database import async_session_maker
    from app.reports.data import export_jobs

    try:
        async with async_session_maker() as db:
            reaped = await export_jobs.reap_stale_jobs(db)
        for job in reaped:
            await _audit_reaped(job)
    except Exception as exc:  # noqa: BLE001
        logger.error("export reap tick error: %s", exc)


async def _audit_reaped(job) -> None:
    """A reaped job is an operational event, so it is recorded as one.

    Written through the existing audit trail rather than a second log of its
    own. The record carries who asked, what was asked for and what happened —
    never a Government value, because an audit entry that copied the data would
    be a second copy of it.
    """
    from app.core.database import async_session_maker

    try:
        from app.reports.data.export_audit import record_export_event

        async with async_session_maker() as db:
            await record_export_event(
                db, action="EXPORT_JOB_REAPED",
                actor=job.get("requested_by") or "SYSTEM",
                job_id=job.get("job_id"),
                detail=(f"No heartbeat since {job.get('last_heartbeat')}; the "
                        f"job was marked failed and its export slot released."))
    except Exception as exc:  # noqa: BLE001
        logger.error("could not audit reaped export job %s: %s",
                     job.get("job_id"), exc)


def start_export_scheduler():
    """Start the export poller + reaper. Safe to call once at app startup."""
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = AsyncIOScheduler()
        defaults = dict(coalesce=True, misfire_grace_time=120,
                        replace_existing=True, max_instances=1)

        _scheduler.add_job(_poll_tick,
                           IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
                           id="export_poller",
                           name="Controlled export queue poller", **defaults)
        _scheduler.add_job(_reap_tick,
                           IntervalTrigger(seconds=REAP_INTERVAL_SECONDS),
                           id="export_reaper",
                           name="Stale export job reaper", **defaults)
        _scheduler.start()
        logger.info("export scheduler started — poller %ss, reaper %ss",
                    POLL_INTERVAL_SECONDS, REAP_INTERVAL_SECONDS)
    except ImportError:
        logger.warning("APScheduler not installed — export scheduler not started")
    except Exception as exc:  # noqa: BLE001
        logger.error("export scheduler failed to start: %s", exc)


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
