# FCC Bulletin v1.0 — Performance Report (Staging)

**Executed:** 2026-07-07 · Local staging (uvicorn `127.0.0.1:8000`, PostgreSQL 18.3 `docuaction-db`, real feeds).
**Caveat:** these are **developer-workstation** measurements, not production benchmarks. Only actually-measured values are reported; unmeasured metrics are marked so.

---

## Measured

| Metric | Value | Method / Notes |
|---|---|---|
| Backend module import | ~3.96 s | `import app.main` timed once. |
| Backend boot → `/health` 200 | a few seconds after launch | uvicorn cold start + startup event (`init_store`, hydrate). |
| DB connection | < 8 s (succeeded first attempt after export) | asyncpg connect. |
| **Full collection cycle (348 articles)** | **254.1 s** (`duration_ms=254104`) | From `bulletin_run_log`. **NOT representative** — inflated by hundreds of Anthropic `401` retries (DEF-S1). A valid-key run must be re-timed. |
| Cycle funnel | ingested 348 → after_dedup 318 → in_briefing 150 → rejected 168; dupes 30 | From run log. |
| Word export | < 30 s; 57,046 bytes | `GET /download/fcc?days=3`, valid .docx. |
| Excel export | < 30 s; 37,880 bytes | `GET /download-excel/fcc?days=3`, valid .xlsx. |
| HTML preview | < 30 s; 99,349 bytes | `GET /briefings/{id}/preview`. |
| Simple read endpoints (`/health`, `/coverage-assurance`, `/runs`) | sub-second (interactive) | curl round-trips returned promptly. |

## Not measured (require follow-up)

| Metric | Reason |
|---|---|
| Healthy-key collection duration | Blocked by DEF-S1; re-run with a valid Anthropic key. |
| Frontend page load (local FE ↔ local BE) | Frontend not re-pointed at the local backend this round (Step 2). |
| Large-dataset behavior | Only one cycle (~348 articles) was run; no stress/scale test. |
| Delivery duration (real email) | Blocked by DEF-S3 (dry-run only). |
| Export timing under large briefings | Only the current briefing sizes were measured; no precise timer captured (bounded < 30 s each). |
| Concurrency / throughput | Not tested. |

## Observations

- The dominant cost in a cycle is **AI processing per article** — confirmed indirectly: the 254 s cycle was almost entirely the AI-call retry loop. With a valid key, the per-article Claude latency (Haiku/Sonnet) will set the real cycle time; re-benchmark before capacity planning.
- Export and simple reads are fast; the DB (Postgres 18.3) responded without contention at this small scale.

*Only observed measurements are reported. No performance figure is estimated or extrapolated.*
