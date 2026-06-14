"""
DocuAction Bulletin Intelligence — Daily Delivery Scheduler
Schedule:
  Monday–Friday: 6:00 AM ET — deliver previous 24-hour briefing
  Saturday:       collect articles, store, do NOT deliver
  Sunday:         collect articles, store, do NOT deliver  
  Monday 6 AM:   deliver Mon-Fri briefing PLUS weekend summary (Sat+Sun combined)
"""
import logging
import asyncio
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo  # Python 3.9+ stdlib, no pytz dependency
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover — fallback if tzdata missing
    try:
        import pytz
        ET = pytz.timezone("America/New_York")
    except Exception:
        from datetime import timezone as _tz
        ET = _tz.utc  # last-resort: UTC (scheduler still runs, time approximate)

logger = logging.getLogger(__name__)
_scheduler = None


async def run_weekday_delivery(agency_id: str):
    """Run and deliver — Monday through Friday 6 AM ET."""
    try:
        from app.bulletin_intelligence.engine import run_daily_cycle, get_agency
        agency = get_agency(agency_id)
        if not agency:
            return
        logger.info(f"Weekday delivery starting: {agency.name}")
        result = await run_daily_cycle(
            agency_id=agency_id,
            auto_deliver=True,
            lookback_hours=24,
        )
        logger.info(f"Weekday delivery complete: {result.get('in_briefing',0)} articles → {agency.distribution_list}")
    except Exception as e:
        logger.error(f"Weekday delivery error {agency_id}: {e}")


async def run_weekend_collection(agency_id: str):
    """Collect Saturday/Sunday articles — do NOT deliver. Store for Monday."""
    try:
        from app.bulletin_intelligence.engine import (
            run_daily_cycle, get_agency, _briefings
        )
        agency = get_agency(agency_id)
        if not agency:
            return
        day = datetime.now(ET).strftime("%A")
        logger.info(f"{day} collection starting: {agency.name} (no delivery)")
        result = await run_daily_cycle(
            agency_id=agency_id,
            auto_deliver=False,   # collect only, do not send
            lookback_hours=24,
        )
        logger.info(f"{day} collection complete: {result.get('in_briefing',0)} articles stored")
    except Exception as e:
        logger.error(f"Weekend collection error {agency_id}: {e}")


async def run_monday_delivery(agency_id: str):
    """
    Monday 6 AM — deliver Friday briefing PLUS Saturday + Sunday combined summary.
    Lookback 72 hours to capture all weekend content.
    """
    try:
        from app.bulletin_intelligence.engine import run_daily_cycle, get_agency
        agency = get_agency(agency_id)
        if not agency:
            return
        logger.info(f"Monday delivery (72hr weekend rollup) starting: {agency.name}")
        result = await run_daily_cycle(
            agency_id=agency_id,
            auto_deliver=True,
            lookback_hours=72,    # Friday + Saturday + Sunday
        )
        logger.info(f"Monday delivery complete: {result.get('in_briefing',0)} articles → {agency.distribution_list}")
    except Exception as e:
        logger.error(f"Monday delivery error {agency_id}: {e}")


def trigger_all_agencies(mode: str):
    """Trigger the correct cycle for all registered agencies."""
    try:
        from app.bulletin_intelligence.engine import list_agencies
        agencies = list_agencies()
        loop = asyncio.get_event_loop()
        for agency in agencies:
            if mode == "weekday":
                loop.create_task(run_weekday_delivery(agency.agency_id))
            elif mode == "weekend":
                loop.create_task(run_weekend_collection(agency.agency_id))
            elif mode == "monday":
                loop.create_task(run_monday_delivery(agency.agency_id))
    except Exception as e:
        logger.error(f"Scheduler trigger error: {e}")


def start_scheduler():
    """
    Start the APScheduler with daily delivery:
      Every day 6 AM ET → 24-hour briefing + deliver (incl. weekends)
    """
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = AsyncIOScheduler(timezone=ET)

        # Every day 6 AM ET — 24hr briefing + deliver (7 days a week)
        _scheduler.add_job(
            lambda: trigger_all_agencies("weekday"),
            CronTrigger(day_of_week="mon,tue,wed,thu,fri,sat,sun",
                        hour=6, minute=0, timezone=ET),
            id="daily_delivery", replace_existing=True,
            name="Daily 6AM ET — Briefing + Delivery (incl. weekends)"
        )

        _scheduler.start()
        logger.info("Bulletin scheduler started — daily 6AM ET delivery (7 days/week)")

    except ImportError:
        logger.warning("APScheduler not installed — run: pip install apscheduler")
    except Exception as e:
        logger.error(f"Scheduler failed: {e}")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Bulletin scheduler stopped")
