# Caching Assessment

> Is anything cached? What should be? Read-only.

## What exists — almost nothing
- **No Redis / aioredis / cachetools / in-memory data cache / HTTP `Cache-Control`.** 0 matches across backend and frontend.
- The **only** cache is `@lru_cache()` on `config.py:31 get_settings()` — a settings **singleton**, not data caching.
- `Tefca/models.py:~209 nppes_cache` is a **DB persistence table**, not a latency cache — every hit is still a Postgres round-trip.
- Frontend: **no SWR/react-query** — no client cache or request dedup (see `frontend_performance.md`); every component mount re-fetches.

**Net: there is effectively no caching layer at any tier** — not in the browser, not in the app, not in front of the DB, not in front of external APIs.

## What should be cached (and isn't)

| Candidate | Why | Suggested TTL / mechanism |
|---|---|---|
| **QHIN hierarchy** (`list_qhins`, `hierarchy_roots`, `get_subtree`) | expensive N+1 tree walk (N-04), changes rarely | in-memory/Redis, minutes–hours; invalidate on entity/relationship write |
| **Dashboard aggregates** (`/dashboard/stats`, `/dashboard/trends`) | full-table scans on `TEFCAReview`/`TEFCAConnectorLog` every load | short TTL (30–120s) cached result or a materialized view |
| **NPPES / connector lookups** | live external HTTPS calls or DB round-trips per request | short-TTL cache keyed by NPI (the `nppes_cache` table could gain a TTL + in-memory front) |
| **`platform_config` / static reference data** (finding-reason labels, connector list, feature flags) | recomputed per request, essentially immutable between deploys | process-lifetime cache, invalidate on config write |

## HTTP response caching
- **No `Cache-Control`/`ETag` headers** set on any API response. Even immutable reference endpoints (connector catalog, config) are re-fetched fully each time. Adding `Cache-Control: public, max-age=…` (or `ETag`) to reference/read-mostly endpoints would offload the CDN/browser for free.
- Static export means the **frontend HTML/JS is already CDN-cached by SWA**; the gap is purely the **data layer**, which is 100% dynamic client fetches.

## Caching verdict
**Caching: NOT implemented (beyond a config singleton).** This is the second-highest performance risk after the hierarchy N+1 — and the two compound: the most expensive query in the system (the hierarchy tree) is recomputed on every request with no cache. A modest cache (even in-process `cachetools` with TTL, before reaching for Redis) on the hierarchy, dashboard aggregates, and reference data would remove most repeat load. Redis becomes worthwhile once the app scales beyond one instance (also needed to fix the scheduler-duplication and in-memory-rate-limit/lockout issues noted in Parts 8/9).
