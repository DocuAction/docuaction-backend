# Performance Architecture (Section 2L)

Code-review only (no load testing). Detailed remediation deferred to Part 7.

## Caching
- **No Redis / external cache.** `pool_pre_ping` and connection pools only.
- **In-memory caches exist ad hoc:** TEFCA `tefca_source_cache` table (authoritative-source responses), bulletin **in-memory article/briefing store**, in-memory rate-limit/lockout windows.
- **Should be cached but isn't:** platform_config reference data (jurisdictions, identifier types, agencies) read per request in some paths; TEFCA `/stats` recomputed each call; QHIN/hierarchy lookups. Low volume today; candidates as usage grows.

## Database performance
- **N+1 patterns:** present in registry read helpers — `list_qhins`/`list_participants` call `_child_count()` **per row** (one COUNT query per QHIN/participant), and `get_subtree` recurses with per-node `get_children`. Fine at 11 QHINs / 45 participants; **won't scale** to large trees. The N+1 for **NPI/TEFCAID was already fixed** (batched `_attach_identifiers`) — a good precedent to apply to the counts.
- **Missing FK indexes:** 15 FK columns without a leading index (see `database_inventory.md`) → slow joins/cascades.
- **JSONB without GIN:** `fhir_resource`, `snapshot_data`, `exchange_purposes`, etc. queried by key (`fhir_resource->>'id'`) without GIN — fine now, index as data grows.
- **Pagination:** implemented on registry list endpoints (`limit`/`offset`); **offset pagination** degrades on deep pages at scale (cursor-based preferred later). Some legacy list endpoints may lack pagination (verify Part 7).
- **Large result sets:** bulk `/verify` loads all entities + all identifiers into memory (fine at 177; watch at 10×).

## API performance
| Category | Expected latency | Notes |
|---|---|---|
| Simple reads (stats, lists) | fast (<200ms) | pending N+1 fixes at scale |
| Entity detail | moderate (several queries + parent-chain walk) | parent-chain does iterative fetch (bounded) |
| Verification (bulk) | seconds | in-process, synchronous |
| **AI endpoints** (extraction, briefings, transcription) | **slow (2–180s)** | external LLM/Whisper latency; **synchronous** request handling |
| Import (FHIR/CSV) | moderate | two-pass, per-entity savepoints |

## Frontend performance
- **Static export** (Next 16, Turbopack) → cached on SWA CDN ✅.
- **Lazy loading:** TEFCA registry hierarchy is **lazy** (one level per fetch) ✅; entity search **debounced + race-guarded** ✅.
- **No list virtualization** — the platform DataTable is hand-rolled with client-side pagination; very large tables (legacy TEFCA/bulletin) could render slowly.
- **React optimization:** `useCallback`/`useMemo` used in newer TEFCA pages; older pages (procurement/ATS) not audited.
- **Bundle:** heavy client-export libs (`jspdf`, `html2canvas-pro`, `docx`, `xlsx`, `recharts`) increase bundle weight; per-route code splitting via App Router mitigates.

## Background processing
- **Scheduler:** APScheduler (bulletin daily job) — in-process, single-instance. **No distributed queue** (Celery/Azure Queue). A second instance would double-run jobs (mitigated today by capacity 1 + `ENABLE_SCHEDULER`).
- **AI pipeline:** **synchronous** (request blocks on the LLM/Whisper call) — long requests tie up a worker; no async job/queue for long AI operations. **Scalability + timeout risk.**
- **Uploads:** written to local disk (`/home/site/wwwroot/uploads`) — not durable/shared across instances.

## Top performance items (→ Part 7)
1. Batch the per-row COUNT queries (N+1) in registry list endpoints.
2. Add the 15 missing FK indexes; GIN on hot JSONB paths later.
3. Move long AI/transcription operations to an **async job/queue**.
4. Introduce a shared cache (Redis) for reference/config + rate-limit/lockout state (also fixes the multi-instance auth-state problem).
5. Persist bulletin state to DB; move uploads to Blob Storage.
