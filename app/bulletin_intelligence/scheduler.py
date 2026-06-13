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
import pytz

logger = logging.getLogger(__name__)
_scheduler = None
ET = pytz.timezone("America/New_York")


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
    Start the APScheduler with the FCC delivery schedule:
      Mon 6 AM ET  → 72-hour weekend rollup + deliver
      Tue-Fri 6AM  → 24-hour briefing + deliver
      Sat 6 AM ET  → collect only (no delivery)
      Sun 6 AM ET  → collect only (no delivery)
    """
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = AsyncIOScheduler(timezone=ET)

        # Monday 6 AM — 72hr rollup (Fri+Sat+Sun) + deliver
        _scheduler.add_job(
            lambda: trigger_all_agencies("monday"),
            CronTrigger(day_of_week="mon", hour=6, minute=0, timezone=ET),
            id="monday_delivery", replace_existing=True,
            name="Monday 6AM — Weekend Rollup + Delivery"
        )

        # Tuesday–Friday 6 AM — 24hr briefing + deliver
        _scheduler.add_job(
            lambda: trigger_all_agencies("weekday"),
            CronTrigger(day_of_week="tue,wed,thu,fri", hour=6, minute=0, timezone=ET),
            id="weekday_delivery", replace_existing=True,
            name="Tue-Fri 6AM — Daily Briefing + Delivery"
        )

        # Saturday 6 AM — collect only
        _scheduler.add_job(
            lambda: trigger_all_agencies("weekend"),
            CronTrigger(day_of_week="sat", hour=6, minute=0, timezone=ET),
            id="saturday_collection", replace_existing=True,
            name="Saturday 6AM — Collect Only"
        )

        # Sunday 6 AM — collect only
        _scheduler.add_job(
            lambda: trigger_all_agencies("weekend"),
            CronTrigger(day_of_week="sun", hour=6, minute=0, timezone=ET),
            id="sunday_collection", replace_existing=True,
            name="Sunday 6AM — Collect Only"
        )

        _scheduler.start()
        logger.info("Bulletin scheduler started — Mon-Fri 6AM delivery, Sat-Sun collection only")

    except ImportError:
        logger.warning("APScheduler not installed — run: pip install apscheduler pytz")
    except Exception as e:
        logger.error(f"Scheduler failed: {e}")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Bulletin scheduler stopped")
