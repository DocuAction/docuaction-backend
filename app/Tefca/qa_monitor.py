"""
DocuAction TEFCA — QA continuous-monitoring scheduler (QA Task 5)

A standalone APScheduler instance that runs a periodic QA sweep. Independent of
the bulletin scheduler (app/bulletin_intelligence/scheduler.py is NOT touched).
Gated behind ENABLE_QA_MONITOR so only one box runs it; default OFF (safe).
"""
import os
import logging

logger = logging.getLogger("docuaction.tefca.qa.monitor")

_scheduler = None


async def _run_sweep_job():
    from app.core.database import async_session_maker
    from app.Tefca import qa_engine
    try:
        async with async_session_maker() as db:
            result = await qa_engine.run_qa_sweep(db, triggered_by="scheduled")
        logger.info(f"QA sweep: overall={result['overall_qa_score']} alerts={result['alert_count']} "
                    f"drift={result['drift_detected']}")
    except Exception as e:
        logger.warning(f"QA sweep job failed: {e}")


def start_qa_monitor():
    """Start the periodic QA sweep if ENABLE_QA_MONITOR=true. Returns the
    scheduler (or None if disabled). Non-blocking; never raises."""
    global _scheduler
    if os.getenv("ENABLE_QA_MONITOR", "false").strip().lower() != "true":
        logger.info("QA monitor DISABLED (set ENABLE_QA_MONITOR=true to enable)")
        return None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        interval = int(os.getenv("QA_MONITOR_INTERVAL_MIN", "60"))
        sched = AsyncIOScheduler()
        sched.add_job(_run_sweep_job, "interval", minutes=interval, id="qa_monitor",
                      max_instances=1, coalesce=True)
        sched.start()
        _scheduler = sched
        logger.info(f"QA monitor ENABLED — sweep every {interval} min")
        return sched
    except Exception as e:
        logger.warning(f"QA monitor failed to start: {e}")
        return None
