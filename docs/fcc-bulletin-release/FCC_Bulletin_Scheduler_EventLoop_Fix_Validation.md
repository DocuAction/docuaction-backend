# FCC Bulletin — Scheduler Event-Loop Fix: Validation Report

**Prepared:** 2026-07-08
**Scope:** Scheduler implementation only (`app/bulletin_intelligence/scheduler.py`). No new features, no architecture change, no TEFCA business-logic change, no production change.

---

## 1. Issue (from Railway Development logs)

Repeating hourly runtime error:
```
Watchdog tick error: There is no current event loop in thread 'ThreadPoolExecutor-0_0'
```

## 2. Root cause (identified + reproduced)

`AsyncIOScheduler` runs a **coroutine** job by awaiting it on the scheduler's event loop, but runs a **synchronous** job in a **thread-pool worker**. The affected jobs were synchronous and then called `asyncio.get_event_loop()`:

- `_watchdog_tick` (hourly) — `asyncio.get_event_loop().create_task(...)`
- `trigger_all_agencies` (via the `lambda:` cron jobs for Mon–Sat delivery, Monday rollup, Sunday preview) — `loop = asyncio.get_event_loop()`

On Python 3.10+ (Dev runs 3.13), `asyncio.get_event_loop()` **raises `RuntimeError` when called from a thread that has no event loop** — exactly the worker thread APScheduler used. Only the watchdog was *visible* because it fires hourly; the daily delivery/preview jobs have the identical defect but fire once per day.

**Reproduced locally (same error class + message):**
```
root_cause_reproduced: True
root_cause_message: "There is no current event loop in thread 'ThreadPoolExecutor_0'."
```

## 3. Fix (scheduler implementation only)

Converted the affected jobs to **coroutines** so `AsyncIOScheduler` awaits them on the loop (never a worker thread, never `get_event_loop()`). **All jobs preserved** — same ids, triggers, names, and defaults.

| Before | After |
|---|---|
| `def _watchdog_tick()` → `get_event_loop().create_task(...)` | `async def _watchdog_tick()` → `await ensure_todays_briefing(...)` |
| `def trigger_all_agencies()` → `loop = get_event_loop(); loop.create_task(...)` | `async def trigger_all_agencies()` → `await asyncio.gather(*coros, return_exceptions=True)` |
| `add_job(lambda: trigger_all_agencies("weekday"), ...)` | `add_job(_job_weekday, ...)` (named `async def` wrappers) |
| startup catch-up: `get_event_loop().create_task(...)` | `get_running_loop().create_task(...)` + strong ref (`_startup_task`) |

Business logic unchanged: `ensure_todays_briefing`, `run_weekday_delivery`, `run_monday_delivery`, `run_weekend_collection` are untouched. Per-agency failures are now isolated (`return_exceptions=True`) so one bad agency can't abort the batch.

**File changed:** `app/bulletin_intelligence/scheduler.py` only.

## 4. Verification — regression test (real APScheduler, before/after)

Hermetic test (no DB, no network, no API keys): reproduces the failure, then drives the **real `AsyncIOScheduler`** dispatching the jobs.

| Check | Result |
|---|---|
| Root cause reproduced (sync job in thread pool → `get_event_loop()` raises) | ✅ True |
| `_watchdog_tick` is a coroutine | ✅ True |
| `trigger_all_agencies` is a coroutine | ✅ True |
| Cron wrappers (`_job_weekday/_job_monday/_job_preview`) are coroutines | ✅ True |
| **OLD** sync watchdog dispatched by real `AsyncIOScheduler` → job error | ✅ True (`"no current event loop"`) |
| **NEW** async watchdog dispatched by real `AsyncIOScheduler` → **ran** | ✅ `NEW_watchdog_ran: True` |
| **NEW** async watchdog → **no job errors** | ✅ `NEW_watchdog_no_errors: True` |
| Watchdog executes and calls `ensure_todays_briefing` cleanly | ✅ True |
| Weekday delivery triggers the agency cycle | ✅ True |
| Monday rollup triggers the agency cycle | ✅ True |
| Sunday preview triggers the agency cycle | ✅ True |
| **ALL SCHEDULER REGRESSION CHECKS PASS** | ✅ **True** |

This is the required proof that **Bulletin collection trigger, daily delivery, Monday rollup, preview, and the hourly watchdog all execute correctly** with no event-loop exceptions.

## 5. TEFCA QA readiness (`scheduler`) — honest assessment

The TEFCA readiness `scheduler` check (`app/Tefca/qa_engine.py::check_scheduler`) is a **separate signal**: it passes only if the TEFCA QA monitor recorded a `qa_sweep` audit row within 24h. Findings:

- The TEFCA QA monitor job (`app/Tefca/qa_monitor.py::_run_sweep_job`) is **already an `async` coroutine** — it does **not** have the event-loop bug and needs no code change.
- Both schedulers start in the **same async startup context** (running loop), so the monitor binds correctly.
- Therefore `scheduler=FAIL` in Dev is a **freshness/config state**: `check_scheduler` returns FAIL until a `qa_sweep` has run, which requires `ENABLE_QA_MONITOR=true` on the Dev instance. It is **not** caused by the bulletin watchdog bug, and it is resolved operationally, not by changing TEFCA logic.

**Action taken:** none in TEFCA (per the instruction "do not change TEFCA logic"). **To make TEFCA report `scheduler=OK`:** set `ENABLE_QA_MONITOR=true` on the Dev instance so the monitor records a sweep (passes within 24h). If the Dev logs show a *TEFCA-side* event-loop error at `qa_monitor` startup, share that exact line and I will address it within the same scheduler-fix scope — but the current TEFCA code does not contain the defect.

> Honest note: I fixed and verified the bulletin scheduler bug (the actual repeating log error) against real APScheduler locally. I could **not** verify `scheduler=OK` against the live Development instance from here (no Dev access); that verification must be read from the Dev `/health` + TEFCA readiness endpoints after redeploy, with `ENABLE_QA_MONITOR=true`.

## 6. Scope compliance

- ✅ Changes limited to `scheduler.py` (scheduler implementation).
- ✅ No new features, no instrumentation added, no architecture change.
- ✅ No FCC Bulletin business logic changed.
- ✅ No TEFCA logic changed (none required).
- ✅ No production change; no merge, no tag.

## 7. Post-deploy checks (Development)

After redeploying the branch to Development with `ENABLE_SCHEDULER=true`:
1. `GET /health` → `scheduler.running = true`, jobs list shows `bulletin_watchdog`, `weekday_delivery`, `monday_delivery`, `sunday_preview` with `next_run` times.
2. Watch logs for one hour → **no** `Watchdog tick error` lines.
3. With `ENABLE_QA_MONITOR=true`, confirm a `qa_sweep` is recorded → TEFCA readiness `scheduler = OK` (passes within 24h).
