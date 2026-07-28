# Performance Risk Matrix

> Per-module performance risk from static review. Risk = likelihood × impact at realistic growth (100K+ rows / multi-user). Read-only.

## Per-module risk

| Module | Risk | Key concern |
|---|:--:|---|
| **TEFCA Registry (hierarchy)** | **High** | N+1 recursion in `queries.py` `get_subtree`/`get_children`/`list_*` (per-node `_child_count`); should be a recursive CTE. No cache on a rarely-changing, expensive tree. |
| **TEFCA Dashboard** (`Tefca/routes.py`) | **High** | Full-table scans of `TEFCAReview`/`TEFCAConnectorLog` + Python aggregation (l.1171/1237/1258); count-twice anti-pattern (l.2433/2542/2797); unbounded `.all()`; no aggregate indexes. |
| **AI Engine** (`ai_engine.py`) | **Medium-High** | ~150s worst-case timeout (75s ×2 retry); per-call httpx client (no keep-alive); synchronous nothing else, but long tail under load. |
| **File upload / scan** | **Medium** | Whole-file `await file.read()` into memory (up to 50MB) + synchronous SHA-256/content scan **on the event loop**; concurrent large uploads spike memory + block. |
| **Dashboard (frontend)** | **Medium** | Multiple fetches on mount, no client cache (no SWR); heavy statically-imported libs (recharts). |
| **DataTable / list pages (frontend)** | **Medium** | No virtualization; `pageSize=0` default renders all rows; client-side sort of full dataset; server returns whole set. |
| **Import engine** (FHIR/CSV) | **Medium** | Per-resource parent lookup by `fhir_resource->>'id'` (no GIN → seq-scan); full-file in-memory read. |
| **Scheduler** (bulletin) | **Medium** | In-process APScheduler on the request loop; duplicates across instances/workers (in-memory job store, no lock). |
| **Connection pool** | **Medium** | Two engines, `pool_size=5`, missing `pool_recycle` (both) + `pool_pre_ping` (one) → stale-connection + saturation risk. |
| **Auth / login** | **Low** | In-memory lockout/throttle is per-process (a correctness/security caveat more than perf); bcrypt cost fine. |
| **GovCon/ATS routers** | **Low (dead)** | Use `selectinload` (no N+1); but not wired into the live app (see Part 8) — no runtime perf impact. |
| **Static shell (SWA)** | **Low** | Fast CDN-served static HTML/JS; the perf gap is all in the dynamic data layer. |

## Cross-cutting risks (not module-specific)

| Risk | Severity | Reach |
|---|:--:|---|
| **No caching at any tier** | **High** | every dashboard/hierarchy/reference view recomputes from Postgres/external APIs each request |
| **No GIN on JSONB** (`fhir_resource` etc.) | Medium | FHIR-id lookups + any containment query seq-scan |
| **~10 unindexed legacy FK columns** (`Tefca/models.py`) | Medium | join/filter seq-scans as parents grow |
| **No code splitting** (heavy frontend libs static-imported) | Medium | initial JS weight on every load |

## Aggregate performance risk score: **5.5 / 10**

Rationale: today's low data volumes mask most issues, and the newer code is well-built (eager loading, savepoints, good registry indexes). But the **highest-value query in the system (the hierarchy tree) is an uncached N+1 recursion**, the **dashboard endpoints full-scan and count-twice**, and there is **no caching layer anywhere** — three compounding issues that turn a fast app at 1K rows into a slow one at 100K. The fixes are mechanical (CTE, `func.count()`, GIN indexes, a TTL cache, `next/dynamic`) — no rearchitecture — so the score reflects *latent* risk that is cheap to retire, not present-day failure.

## Key metrics
- **N+1 query sites: 7** (N-01…N-07), all in TEFCA registry/dashboard; one (get_subtree) is multiplicative.
- **Unindexed FK/lookup columns: ~10** (legacy `Tefca/models.py`).
- **JSONB GIN indexes: 0.**
- **Caching: NOT implemented** (only a config `lru_cache` singleton).
- **Virtualized lists: 0.** **Code-split chunks: 0.**
