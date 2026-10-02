"""Poller and reaper for durable IQVIA import jobs.

FOLLOWS `export_scheduler.py`, DOES NOT EDIT IT
------------------------------------------------
Same reasoning that module gives for not editing `ppef_scheduler`: a
scheduler serving two unrelated domains has to be gated and reasoned about
for both at once. This is the same poll/reap shape for a third kind of durable
work.

WHY THIS EXISTS AT ALL
------------------------
`iqvia_import._import_csv` was already correctly resumable at the row level
(`ON CONFLICT DO NOTHING` keyed by `(snapshot_id, source_record_key)`) -- the
missing piece was never the importer, it was that nothing called it again
after the request-scoped `BackgroundTasks` task that started it died with the
process. This poller is that missing caller: it claims QUEUED
`IqviaImportJob` rows (including ones the reaper below requeued after a
worker died mid-import) and invokes the importer with the job's
`snapshot_id`, which is exactly the resume call `_import_csv` already
supports.

MULTI-WORKER
------------
`claim_next_queued` uses `SELECT ... FOR UPDATE SKIP LOCKED` and the job
table carries a partial unique index over `(snapshot_id) WHERE active_marker
IS TRUE`, so duplicate execution of the same snapshot's import is prevented
by the database, not by this process being the only one running -- the same
guarantee `export_scheduler.py` documents for itself.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_scheduler = None

#: Short -- an operator who just finished an upload should see RUNNING
#: within seconds, not sit at QUEUED.
POLL_INTERVAL_SECONDS = 5

#: Housekeeping; does not need to be prompt.
REAP_INTERVAL_SECONDS = 60

#: Written while an import chunk-commit loop is in flight (see
#: `iqvia_routes._run_import_job`'s progress callback).
HEARTBEAT_INTERVAL_SECONDS = 20


async def _poll_tick():
    """Claim and run one queued IQVIA import job.

    One at a time, deliberately: the HCP_ADDR extract alone is ~3.8GB and
    the importer already streams it in bounded chunks, so running two such
    imports concurrently in one process would double that chunk-buffer
    memory for no benefit -- both would still be serialized on the same
    database connection pool regardless.
    """
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import iqvia_upload_jobs as jobs
    from app.tefca_registry.rce.iqvia_routes import run_import_job

    try:
        async with async_session_maker() as db:
            job = await jobs.claim_next_queued(db)
            if job is None:
                return
            logger.info("IQVIA import poller claimed job %s (snapshot %s, attempt %s)",
                       job.id, job.snapshot_id, job.attempt_count)
        # Deliberately a NEW session for the run itself: a multi-minute (at
        # real scale, multi-hour) import must not hold the claim transaction
        # open for its entire duration.
        await run_import_job(str(job.id))
    except Exception as exc:  # noqa: BLE001
        # A tick that raises must not stop the scheduler. The job's own
        # heartbeat silence is what the reaper acts on if the process itself
        # is actually broken.
        logger.error("IQVIA import poll tick error: %s", exc)


async def _reap_tick():
    """Requeue or fail IQVIA import jobs whose worker stopped heartbeating."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import iqvia_upload_jobs as jobs

    try:
        async with async_session_maker() as db:
            reaped = await jobs.reap_stale_jobs(db)
        if reaped:
            logger.warning("IQVIA import reaper acted on %d job(s): %s",
                           len(reaped), reaped)
    except Exception as exc:  # noqa: BLE001
        logger.error("IQVIA import reap tick error: %s", exc)


def start_iqvia_import_scheduler():
    """Start the IQVIA import poller + reaper. Safe to call once at startup."""
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = AsyncIOScheduler()
        defaults = dict(coalesce=True, misfire_grace_time=120,
                        replace_existing=True, max_instances=1)

        _scheduler.add_job(_poll_tick,
                           IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
                           id="iqvia_import_poller",
                           name="IQVIA durable import queue poller", **defaults)
        _scheduler.add_job(_reap_tick,
                           IntervalTrigger(seconds=REAP_INTERVAL_SECONDS),
                           id="iqvia_import_reaper",
                           name="IQVIA stale import job reaper", **defaults)
        _scheduler.start()
        logger.info("IQVIA import scheduler started -- poller %ss, reaper %ss",
                    POLL_INTERVAL_SECONDS, REAP_INTERVAL_SECONDS)
    except ImportError:
        logger.warning("APScheduler not installed -- IQVIA import scheduler not started")
    except Exception as exc:  # noqa: BLE001
        logger.error("IQVIA import scheduler failed to start: %s", exc)


def scheduler_status() -> dict:
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
