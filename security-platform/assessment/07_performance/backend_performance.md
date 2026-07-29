# Backend Performance Review

> Static review of the FastAPI/SQLAlchemy-async backend. Read-only. File:line anchors throughout.

## N+1 query patterns

**Isolated to the TEFCA registry/dashboard code** — the GovCon routers (`ats.py`, `invoices.py`, `quotes.py`, `staffing.py`, `finance.py`, `deals.py`) correctly use `selectinload` and have **no N+1**.

| ID | Location | Pattern | Impact |
|---|---|---|---|
| N-01 | `tefca_registry/queries.py:209-220` `list_qhins` | `_child_count(session, q.id)` **inside** the per-QHIN loop (l.217) → one `COUNT` per QHIN | linear in QHIN count |
| N-02 | `tefca_registry/queries.py:240-243` `list_participants` | `_child_count` per participant (l.242) | up to `limit=200` extra COUNTs/page |
| N-03 | `tefca_registry/queries.py:266-270` `get_children` | `_child_count` per child (l.269) | linear per level |
| N-04 | `tefca_registry/queries.py:279-290` `get_subtree` | **recursive** `build()` → `get_children` (itself N+1) per node to `max_depth=3`, plus per-node `session.get` (l.280) + `_child_count` (l.287) | **multiplicative — worst offender** |
| N-05 | `Tefca/routes.py:1171,1237,1258` | dashboard loads **whole tables** (`TEFCAReview`, `TEFCAConnectorLog`) then loops in Python (l.1180,1239,1260) — work `GROUP BY` should do | full scans + Python aggregation |
| N-06 | `Tefca/routes.py:1204-1208` | findings query then Python-counts finding types | avoidable scan |
| N-07 | `tefca_registry/fhir_import.py:244` | resolves parents one-by-one by `fhir_resource->>'id'` | per-resource query during import |

**Fix pattern:** replace `get_subtree`/`get_children` recursion with a single **recursive CTE**; replace per-row `_child_count` with a grouped `COUNT ... GROUP BY parent_id` join; push dashboard aggregation into SQL `GROUP BY`.

## Pagination / large result sets

- **Count anti-pattern** (loads the full filtered set just to count, then re-queries): `Tefca/routes.py:2433-2434` `total = len((await db.execute(q)).scalars().all())` then `q.limit().offset()` — **identical at 2542-2543 and 2797-2798**. Each list request **scans the whole filtered table twice**. Replace with `select(func.count())`.
- **Unbounded `.all()`** (no limit/offset) on growing tables: `Tefca/routes.py:263, 500, 665, 697, 710, 749, 789, 1080, 1171, 1237, 1258, 1534`. `.all()` frequency by file: `Tefca/routes.py` (35), `routers/deals.py` (17), `tefca_registry/queries.py` (14), `routers/ats.py` (10). These degrade linearly with table growth.

## Synchronous bottlenecks in async paths

- **AI call — up to ~150s worst case:** `services/ai_engine.py:424 _call_model` → `450 _anthropic_call`. `timeout=75` via `asyncio.wait_for` (l.432-435) **and** httpx `timeout=75.0` (l.467). Retry `for attempt in range(2)` (l.430): on `TimeoutError`, attempt 0 retries, attempt 1 raises → **75s + 75s ≈ 150s** before failing. **`httpx.AsyncClient` is created per call** (l.467) — no keep-alive/connection reuse across AI calls.
  - **On timeout:** the caller surfaces an error (no partial result, no queue/back-pressure). 75s single-attempt is generous but the 150s doubled tail is a latency risk under load.
- **File scanning is CPU-bound and synchronous on the event loop:** `services/file_scanner.py scan()` runs `hashlib.sha256(file_bytes)` (l.127) + full-content byte scans (`content.lower()`, l.205) synchronously for files up to **50MB** — **blocks the event loop**. Not offloaded to a thread/executor (`run_in_executor`/`anyio.to_thread`).
- Sync file I/O in services: `document_processor.py:86-87,135-136` base64-encodes whole files in memory; `services/audio_service.py:246-247` blocking `open().read()`.

## Connection pooling — two divergent engines

- `core/database.py:32` — `create_async_engine(..., pool_size=5, max_overflow=10, pool_pre_ping=True)` (**no `pool_recycle`**).
- `app/database.py:6` — a **second** engine `create_async_engine(..., pool_size=5, max_overflow=10)` (**no `pool_pre_ping`, no `pool_recycle`**).
- Two independent pools → up to ~30 connections and **divergent staleness behavior**; the second pool risks stale connections against Postgres/PgBouncer idle timeouts (no pre-ping/recycle). `pool_size=5` is low for a multi-module app under load. **Consolidate to one engine; set `pool_recycle` (e.g. 1800) and `pool_pre_ping=True`.**

## Background scheduler

- `bulletin_intelligence/scheduler.py:331-335` uses **APScheduler `AsyncIOScheduler`** **in-process on the app event loop** (`add_job` 343/352/361/371/398). Concerns:
  - (a) coroutine jobs run on the **request event loop** → a heavy job competes with request handling;
  - (b) single-process, **in-memory job store** → if the app scales to multiple instances/workers, jobs **duplicate** with no distributed lock;
  - (c) no visible `coalesce`/`max_instances` guard.
- Ties to Part 9: `ENABLE_SCHEDULER=true` in prod, single instance — currently masks (b), but any scale-out or `--workers 4` (per the gunicorn startup) can **duplicate jobs**. Move to an external scheduler/worker or add a DB advisory-lock guard.

## File processing — full in-memory reads

- **Every upload does `content = await file.read()` — the whole file into memory** before scan/persist: `Tefca/routes.py:2681`, `tefca_registry/routes.py:222,249`, `api/routes.py:447,525,579`, `routers/ats.py:443,485`, `routers/staffing.py:140,405`, `api/migration_routes.py:208`, `routers/suppliers.py:161`, `api/audio_routes.py:52`, `routers/ai_analysis.py:91,201`, `api/meeting_routes.py:49`, others. Combined with the 50MB scanner limit + in-memory base64, **concurrent large uploads are a memory-spike risk**. No streaming.

## Backend performance verdict
Newer code is well-built (eager loading, savepoints), but the **TEFCA registry hierarchy is the performance hot spot** (N+1 recursion where a CTE belongs), the **dashboard endpoints full-scan + count-twice**, there's **no caching**, **two DB engines with thin pools**, an **in-process scheduler that duplicates on scale-out**, and **whole-file in-memory uploads with a synchronous scan on the event loop**. All are fixable without rearchitecture.
