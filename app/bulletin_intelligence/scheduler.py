"""
DocuAction Bulletin Intelligence — Daily Delivery Scheduler (self-healing)

Schedule (all times America/New_York):
  Sunday 8 PM ET   → Preview run (generate, hold for review — no auto-send)
  Monday 1 AM ET   → Weekend rollup delivery (Fri+Sat+Sun, 72h) + deliver
  Tue–Sat 1 AM ET  → Daily 24h briefing + deliver

Why 1 AM (was 6 AM): gives a large buffer before the team needs the bulletin,
so a slow run, a retry, or a self-heal catch-up still lands well before anyone is
waiting on it in the morning.

Self-healing design — three independent safety nets so a morning is never
silently dropped (the failure mode we hit when a restart near run-time made
APScheduler skip the whole day):

  1. misfire_grace_time + coalesce on every job — if the scheduler is alive but
     the job fires late (busy loop, brief pause), it still runs instead of being
     skipped by APScheduler's 1-second default grace.

  2. Catch-up watchdog (hourly + on startup) — independent of APScheduler's
     timing. It asks "is today a delivery day, is it past run-time, and is there
     NO briefing for today yet?" If so, it runs the cycle now. This recovers the
     case APScheduler cannot: a process that started AFTER the scheduled time
     (Railway redeploy at 4:30 AM) never fires today's cron, but the watchdog
     does. Once today's briefing exists, the watchdog is a no-op.

  3. Retry + alert — every cycle runs through _run_cycle_with_retry: up to 3
     attempts, and if it still fails (or comes back empty) an alert email goes
     to ALERT_EMAIL so a human knows immediately. Alerts are de-duped to at most
     one per day per reason so a persistent failure doesn't spam the inbox.
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta

import httpx

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
_startup_task = None   # keeps a strong ref to the boot catch-up task (avoids GC)

# ── Tunables ────────────────────────────────────────────────────────────────
RUN_HOUR = 0                       # 12 AM ET (delivery fires at RUN_HOUR:RUN_MINUTE)
RUN_MINUTE = 1                     # :01 → 12:01 AM ET daily delivery
PREVIEW_HOUR = 20                  # Sunday 8 PM ET preview
MAX_CYCLE_ATTEMPTS = 3             # retries per cycle before alerting
RETRY_BACKOFF_S = 90               # wait between cycle retries
MIN_VALID_ARTICLES = 1             # a real briefing must carry at least this many
# A late job is still worth running any time before the team needs it. 5 hours
# means a job scheduled for 1 AM still fires up to 6 AM if the scheduler was busy.
MISFIRE_GRACE_S = 5 * 3600
ALERT_EMAIL = os.getenv("BULLETIN_ALERT_EMAIL", "imran@agtbi.com")
ALERT_FROM = os.getenv("BULLETIN_ALERT_FROM", "intelligence@docuaction.io")

# De-dupe alerts: at most one email per (date, reason) so a stuck day doesn't spam.
_alerted = set()


# ── Alerting ────────────────────────────────────────────────────────────────
async def send_alert(subject: str, body: str, *, reason_key: str = "") -> None:
    """Email an operator when the bulletin pipeline has a problem.

    De-duped per (ET-date, reason_key): the watchdog runs hourly, so without this
    a persistent failure would email every hour. Pass a stable reason_key to
    collapse repeats; pass "" to always send.
    """
    if reason_key:
        stamp = (datetime.now(ET).strftime("%Y-%m-%d"), reason_key)
        if stamp in _alerted:
            logger.info(f"Alert suppressed (already sent today): {reason_key}")
            return
        _alerted.add(stamp)

    key = os.getenv("SENDGRID_API_KEY", "")
    if not key:
        logger.error(f"[ALERT — no SENDGRID_API_KEY, logging only] {subject}\n{body}")
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "personalizations": [{"to": [{"email": ALERT_EMAIL}]}],
                    "from": {"email": ALERT_FROM, "name": "DocuAction Bulletin Monitor"},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
            )
            resp.raise_for_status()
        logger.info(f"Alert emailed to {ALERT_EMAIL}: {subject}")
    except Exception as e:
        logger.error(f"Failed to send alert email ({subject}): {e}")


# ── Cycle runner with retry + alert ──────────────────────────────────────────
async def _run_cycle_with_retry(agency_id: str, *, label: str,
                                 auto_deliver: bool, lookback_hours: int) -> dict:
    """Run a daily cycle with retries; alert ALERT_EMAIL if it ultimately fails.

    A run counts as failed if it raises, returns an explicit error, or comes back
    with no articles in the briefing. 'already_running' is treated as success —
    another worker has it. Returns the last result dict (possibly an error dict).
    """
    from app.bulletin_intelligence.engine import run_daily_cycle, get_agency

    agency = get_agency(agency_id)
    if not agency:
        logger.warning(f"{label}: agency {agency_id} not registered — skipping")
        return {"error": f"agency {agency_id} not registered"}

    last_error = ""
    for attempt in range(1, MAX_CYCLE_ATTEMPTS + 1):
        try:
            logger.info(f"{label}: {agency.name} attempt {attempt}/{MAX_CYCLE_ATTEMPTS} "
                        f"(deliver={auto_deliver}, lookback={lookback_hours}h)")
            result = await run_daily_cycle(
                agency_id=agency_id,
                auto_deliver=auto_deliver,
                lookback_hours=lookback_hours,
            )

            if result.get("status") == "already_running":
                logger.info(f"{label}: cycle already running for {agency_id} — leaving it")
                return result

            count = result.get("in_briefing", result.get("article_count", 0)) or 0
            if result.get("error"):
                last_error = str(result["error"])
                logger.error(f"{label}: attempt {attempt} returned error: {last_error}")
            elif count < MIN_VALID_ARTICLES:
                last_error = f"briefing produced 0 articles (result={result})"
                logger.error(f"{label}: attempt {attempt} — {last_error}")
            elif auto_deliver and result.get("status") == "error":
                # Briefing was built but the email send failed — that's still a
                # missed morning bulletin, so retry/alert rather than call it done.
                last_error = f"delivery failed: {result.get('delivery')}"
                logger.error(f"{label}: attempt {attempt} — {last_error}")
            else:
                logger.info(f"{label} complete: {count} articles → {agency.distribution_list}")
                return result
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.error(f"{label}: attempt {attempt} raised: {last_error}")

        if attempt < MAX_CYCLE_ATTEMPTS:
            await asyncio.sleep(RETRY_BACKOFF_S)

    # Exhausted all attempts — alert a human.
    await send_alert(
        subject=f"[DocuAction] Bulletin FAILED this morning — {agency.short_name} ({label})",
        body=(
            f"The {label} cycle for {agency.name} failed after {MAX_CYCLE_ATTEMPTS} attempts.\n\n"
            f"Time (ET): {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Agency: {agency_id}\n"
            f"Deliver: {auto_deliver}\nLookback: {lookback_hours}h\n\n"
            f"Last error:\n{last_error}\n\n"
            f"The bulletin was NOT delivered. The watchdog will retry on its next "
            f"hourly pass; you can also POST /api/v1/bulletin/run/{agency_id}?"
            f"auto_deliver=true&lookback_hours={lookback_hours} to recover it now."
        ),
        reason_key=f"{label}:{agency_id}",
    )
    return {"error": last_error or "cycle failed"}


# ── Delivery entry points (called by APScheduler jobs) ───────────────────────
async def run_weekday_delivery(agency_id: str):
    """Tue–Sat 1 AM ET — run and deliver the last-24h briefing."""
    await _run_cycle_with_retry(agency_id, label="weekday delivery",
                                auto_deliver=True, lookback_hours=24)


async def run_monday_delivery(agency_id: str):
    """Monday 1 AM ET — deliver Friday + Sat + Sun combined (72h lookback)."""
    await _run_cycle_with_retry(agency_id, label="Monday weekend-rollup delivery",
                                auto_deliver=True, lookback_hours=72)


async def run_weekend_collection(agency_id: str):
    """Sunday 8 PM ET preview — generate but do NOT deliver (held for review)."""
    await _run_cycle_with_retry(agency_id, label="Sunday preview (no send)",
                                auto_deliver=False, lookback_hours=48)


async def trigger_all_agencies(mode: str):
    """Trigger the correct cycle for all registered agencies.

    Coroutine job: AsyncIOScheduler awaits it directly on the scheduler's event
    loop, so it never runs in a worker thread and never calls get_event_loop()
    (the source of the 'no current event loop in thread ThreadPoolExecutor' error).
    Per-agency failures are isolated via return_exceptions so one bad agency can't
    abort the others."""
    try:
        from app.bulletin_intelligence.engine import list_agencies
        agencies = list_agencies()
        coros = []
        for agency in agencies:
            if mode == "weekday":
                coros.append(run_weekday_delivery(agency.agency_id))
            elif mode == "monday":
                coros.append(run_monday_delivery(agency.agency_id))
            elif mode == "preview":
                coros.append(run_weekend_collection(agency.agency_id))
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
    except Exception as e:
        logger.error(f"Scheduler trigger error: {e}")


# Named coroutine wrappers so AsyncIOScheduler unambiguously detects each job as a
# coroutine (awaited on the loop), replacing the old sync lambdas that were
# dispatched to a thread pool.
async def _job_preview():
    await trigger_all_agencies("preview")


async def _job_monday():
    await trigger_all_agencies("monday")


async def _job_weekday():
    await trigger_all_agencies("weekday")


# ── Self-healing catch-up watchdog ───────────────────────────────────────────
def _todays_delivery_plan(now_et: datetime):
    """What SHOULD have been delivered today, given the day of week.

    Returns (label, lookback_hours) for a delivery day, or None for Sunday
    (preview-only, nothing auto-delivered).
    """
    dow = now_et.weekday()  # Mon=0 .. Sun=6
    if dow == 6:            # Sunday — preview only, no morning delivery
        return None
    if dow == 0:            # Monday — weekend rollup
        return ("weekday catch-up (Monday rollup)", 72)
    return ("weekday catch-up", 24)  # Tue–Sat


def _has_briefing_for_today(agency_id: str) -> bool:
    """True if a briefing was already generated for this agency today.

    Matches on the briefing_id date prefix (agency_<YYYYMMDD>_...), which is
    minted from the same clock the scheduler runs on, so it's a reliable
    'already done today' check regardless of timezone drift.
    """
    try:
        from app.bulletin_intelligence.engine import _briefings
    except Exception:
        return False
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"{agency_id}_{today}"
    return any(bid.startswith(prefix) for bid in _briefings)


async def ensure_todays_briefing(*, source: str = "watchdog"):
    """Self-heal: if today is a delivery day, it's past run-time, and there is
    no briefing for an agency yet, run the cycle now and deliver.

    This is the safety net APScheduler can't provide: a process that boots AFTER
    1 AM never fires today's cron, but this catch-up does. Idempotent — once a
    briefing exists for today, it does nothing.
    """
    try:
        from app.bulletin_intelligence.engine import list_agencies
    except Exception as e:
        logger.warning(f"Catch-up ({source}) skipped — engine unavailable: {e}")
        return

    now_et = datetime.now(ET)
    plan = _todays_delivery_plan(now_et)
    if plan is None:
        return  # Sunday — nothing to deliver in the morning
    if (now_et.hour, now_et.minute) < (RUN_HOUR, RUN_MINUTE):
        return  # too early — the scheduled job hasn't been due yet

    label, lookback = plan
    for agency in list_agencies():
        if _has_briefing_for_today(agency.agency_id):
            continue  # already delivered/generated today — nothing to heal
        logger.warning(
            f"Catch-up ({source}): no briefing for {agency.agency_id} today and it's "
            f"{now_et.strftime('%H:%M %Z')} — running {label} now"
        )
        await _run_cycle_with_retry(
            agency.agency_id, label=label, auto_deliver=True, lookback_hours=lookback
        )


async def _watchdog_tick():
    """Hourly self-heal. Coroutine job: awaited on the scheduler's event loop, so
    there is no get_event_loop()/thread-pool involvement. This is the fix for the
    'Watchdog tick error: There is no current event loop in thread
    ThreadPoolExecutor-0_0' — a sync job was being run in a worker thread that has
    no event loop."""
    try:
        await ensure_todays_briefing(source="watchdog")
    except Exception as e:
        logger.error(f"Watchdog tick error: {e}")


async def _startup_catchup():
    """Run a catch-up shortly after boot so a redeploy that missed the 1 AM cron
    still recovers the morning bulletin without waiting for the hourly watchdog."""
    await asyncio.sleep(60)  # let hydrate_from_store finish restoring prior state
    try:
        await ensure_todays_briefing(source="startup")
    except Exception as e:
        logger.error(f"Startup catch-up error: {e}")


# ── Scheduler wiring ─────────────────────────────────────────────────────────
def start_scheduler():
    """Start APScheduler with the client-agreed schedule, self-healing enabled."""
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = AsyncIOScheduler(timezone=ET)

        # coalesce=True: if multiple fires are pending (process was paused), run
        # once. misfire_grace_time: still run a late job instead of skipping it.
        job_defaults = dict(coalesce=True, misfire_grace_time=MISFIRE_GRACE_S,
                            replace_existing=True)

        # Sunday 8 PM ET — preview run (generate but do NOT auto-deliver)
        _scheduler.add_job(
            _job_preview,
            CronTrigger(day_of_week="sun", hour=PREVIEW_HOUR, minute=0, timezone=ET),
            id="sunday_preview",
            name=f"Sunday {PREVIEW_HOUR}:00 ET — Preview for review (no auto-send)",
            **job_defaults,
        )

        # Monday 12:01 AM ET — weekend rollup (72h) + deliver
        _scheduler.add_job(
            _job_monday,
            CronTrigger(day_of_week="mon", hour=RUN_HOUR, minute=RUN_MINUTE, timezone=ET),
            id="monday_delivery",
            name=f"Monday {RUN_HOUR:02d}:{RUN_MINUTE:02d} ET — Weekend Rollup + Delivery",
            **job_defaults,
        )

        # Tuesday–Saturday 12:01 AM ET — daily 24h briefing + deliver
        _scheduler.add_job(
            _job_weekday,
            CronTrigger(day_of_week="tue,wed,thu,fri,sat", hour=RUN_HOUR, minute=RUN_MINUTE, timezone=ET),
            id="weekday_delivery",
            name=f"Tue-Sat {RUN_HOUR:02d}:{RUN_MINUTE:02d} ET — Daily Briefing + Delivery",
            **job_defaults,
        )

        # Hourly self-healing watchdog — recovers any missed morning delivery
        # (e.g. a redeploy that started after the cron fire time).
        _scheduler.add_job(
            _watchdog_tick,
            IntervalTrigger(hours=1, timezone=ET),
            id="bulletin_watchdog",
            name="Hourly watchdog — ensure today's briefing exists",
            coalesce=True, misfire_grace_time=600, replace_existing=True,
        )

        _scheduler.start()
        logger.info(
            f"Bulletin scheduler started — Sun {PREVIEW_HOUR}:00 preview + "
            f"Mon-Sat {RUN_HOUR:02d}:{RUN_MINUTE:02d} delivery; hourly self-heal watchdog active; "
            f"alerts → {ALERT_EMAIL}"
        )

        # Kick a one-shot catch-up after boot in case this start happened AFTER
        # today's scheduled time (the exact failure that dropped a morning).
        # Use get_running_loop() (start_scheduler runs inside the app's running
        # loop) instead of get_event_loop(), which raises on 3.10+ when no loop is
        # current. Keep a strong ref so the task isn't garbage-collected.
        try:
            global _startup_task
            _startup_task = asyncio.get_running_loop().create_task(_startup_catchup())
        except RuntimeError:
            # No running loop yet — fall back to a one-shot scheduler job so the
            # catch-up still fires without touching get_event_loop().
            from apscheduler.triggers.date import DateTrigger
            _scheduler.add_job(_startup_catchup, DateTrigger(run_date=None),
                               id="startup_catchup", replace_existing=True)
        except Exception as e:
            logger.warning(f"Startup catch-up not scheduled: {e}")

    except ImportError:
        logger.warning("APScheduler not installed — run: pip install apscheduler")
    except Exception as e:
        logger.error(f"Scheduler failed: {e}")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Bulletin scheduler stopped")


def scheduler_status() -> dict:
    """Observable scheduler state for the /health endpoint.

    Lets us confirm post-deploy that the scheduler (and the self-healing
    watchdog) actually started — i.e. that ENABLE_SCHEDULER is set on this box —
    without needing Railway access. `running: false` here means no morning
    delivery and no self-heal will happen, regardless of this code.
    """
    running = bool(_scheduler and getattr(_scheduler, "running", False))
    jobs = []
    if running:
        for j in _scheduler.get_jobs():
            nrt = getattr(j, "next_run_time", None)
            jobs.append({
                "id": j.id,
                "name": j.name,
                "next_run": nrt.isoformat() if nrt else None,
            })
    return {
        "running": running,
        "run_hour_et": RUN_HOUR,
        "run_minute_et": RUN_MINUTE,
        "run_time_et": f"{RUN_HOUR:02d}:{RUN_MINUTE:02d}",
        "alert_email": ALERT_EMAIL,
        "jobs": jobs,
    }
