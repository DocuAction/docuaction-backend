# Performance Review — Summary (Part 7)

> Static source review of the DocuAction backend + frontend. No load testing, no live `EXPLAIN`. Read-only.

## Verdict
**Performance risk score: 5.5 / 10** — fast today, latent risk at scale, all cheaply fixable.

The app is well-built where it counts recently (eager loading in GovCon routers, savepoints in import, richly-indexed registry tables), and the static-export frontend paints fast off the SWA CDN. But three compounding issues create real risk as data grows: **an uncached N+1 hierarchy recursion**, **full-scan + count-twice dashboard endpoints**, and **no caching layer at any tier**. None require rearchitecture.

## Top 5 performance risks
1. **Registry hierarchy N+1 + Python recursion** — `tefca_registry/queries.py` `get_subtree`/`get_children`/`list_qhins`/`list_participants` do per-node `_child_count` and recurse in app code; `get_subtree` is multiplicative. **Fix:** one recursive CTE + grouped counts.
2. **No caching anywhere** — only a config `lru_cache` singleton; no Redis/in-memory/HTTP cache, no SWR on the frontend. The most expensive query (hierarchy) recomputes every request. **Fix:** TTL cache on hierarchy, dashboard aggregates, and reference data.
3. **Client-side-only pagination, no virtualization** — `platform/components/DataTable.js` defaults `pageSize=0` (renders all rows), sorts the full dataset in JS, and the server returns the whole set. **Fix:** server-side paging + virtualization; fix the default.
4. **Count anti-pattern + unbounded `.all()`** — `Tefca/routes.py:2433/2542/2797` load the full filtered set just to `len()` it, then re-query; ~12 endpoints `.all()` with no limit. **Fix:** `select(func.count())` + enforce limits.
5. **AI timeout up to ~150s** with per-call httpx clients (`ai_engine.py:424-467`) + **whole-file in-memory uploads with a synchronous scan on the event loop** (`file_scanner.py`, all `await file.read()` sites). **Fix:** cap total AI time, reuse an httpx client, offload the scan/hash to a thread, stream large files.

## Requested metrics
- **N+1 query count:** **7** (all TEFCA registry/dashboard; one multiplicative). GovCon/ATS routers are clean (`selectinload`).
- **Missing index count:** **~10** unindexed FK/lookup columns (legacy `Tefca/models.py`) + **0 GIN indexes** on any JSONB column (`fhir_resource` etc.).
- **Caching:** **NOT implemented** (config singleton only).
- **Performance risk score:** **5.5 / 10.**

## Cheapest high-impact wins (ordered)
1. Add a TTL cache on the hierarchy + dashboard aggregates (largest latency win). 
2. Replace the hierarchy recursion with a recursive CTE.
3. Fix `func.count()` and enforce list limits.
4. Add GIN on `fhir_resource` + an expression index on `fhir_resource->>'id'`.
5. `next/dynamic` the export/chart libs + drop the dead `@tanstack/react-table`.
6. Consolidate to one DB engine with `pool_pre_ping` + `pool_recycle`.

*Cross-references: the frontend leaks tie to Part 4 (DS-11 dead dep); the scheduler/lockout/rate-limit in-memory issues tie to Part 8/9 (Redis would fix all three); the DB-TLS and dual-engine notes tie to Part 10.*
