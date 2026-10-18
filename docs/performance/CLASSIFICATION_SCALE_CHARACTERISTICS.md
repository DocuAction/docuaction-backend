# Classification (`verify_and_classify`) — scale characteristics, measured 2026-10-03

Scope: the bulk delivery-processing path `arc_pipeline.verify_and_classify` (resolve →
gather evidence → persist → classify → review-id → ReviewRecord), as committed on
`feat/reporting-architecture-2026-10-01`. Every number below is a direct measurement on the
development host (Windows 11, 16 GB, local PostgreSQL 18 on port 5499); where a number is
an arithmetic bound rather than a measurement it says so. **Nothing in this document
supports a claim of 100-million-record readiness** — see §6.

## 1. What the measurements changed about the earlier story

Earlier rounds attributed the slow "first wave" of concurrent entities to TLS/DNS warm-up
and attributed the full-scale duration regression of the chunked loop to database
connection pooling (`NullPool`). Both were hypotheses. Measured directly this round
(`scripts/perf/connect_timing.py`, `scripts/perf/leie_load_timing.py`,
`tests/benchmark_classification.py` with `pipeline_metrics`):

| Hypothesis | Measurement | Verdict |
|---|---|---|
| Physical DB connects are expensive under `NullPool` | Isolated: 65 ms each, 0.37 s for 16 at once. Inside the pipeline under load: ~0.3 s average per scratch session, 2,501 connects, 754 s summed wait; QueuePool(5+10) cuts n=2,500 mock-connector classify from 170.8 s to 79.6 s (§3). | **Confirmed for the pytest harness only** — production already runs QueuePool; no production change needed; every earlier benchmark was inflated by it |
| The first-wave warm-up is TLS/DNS | One cold OIG LEIE CSV load (download + index 84,001 rows) = 3.5 s / +120 MB; **16 concurrent cold loads = 44.2 s wall and a 452 MB process peak** — exactly what the 16-wide first gather wave did, because `_ensure_leie_loaded()` had no single-flight guard | **Confirmed as the warm-up and a large share of peak memory**; fixed (§2) |
| The chunked loop regressed duration | (a) vs (d): chunked 170.8 s vs single-chunk 167.9 s at n=2,500 (+1.7 %).  The per-source token-bucket rate limiter (added 2026-10-02, after the 33.0-min unbatched baseline was measured, so the baseline never ran under it) bounds throughput: each entity with a valid NPI makes 2 `CMS_PPEF` calls (enrollment + revocation) against one shared 10 req/s bucket ⇒ ≤ 5 entities/s ⇒ **≥ 4,913 s (81.9 min) for 24,563 entities regardless of chunking or pooling**. The 88.1-min "pipelined regression" (5,286 s) and the >558 s n=2,500 diagnostic (bound: 500 s) both sit on that bound. | **The regression was the rate limiter, not chunking or pipelining** — confirmed by the live-connector variant in §3 |

## 2. The fix applied (smallest justified)

`app/Tefca/connectors.py::_ensure_leie_loaded` — a single-flight `asyncio.Lock` with a
re-check after acquiring. One loader runs; concurrent cold callers wait for it and read the
filled cache. Same data, same 24 h TTL, same fail-closed behaviour on a failed download (a
failure is not cached; the next caller retries). Measured after the fix: 16 concurrent cold
loads = 3.1 s / 224 MB peak (was 44.2 s / 452 MB). Guarded by
`tests/test_leie_single_flight.py` (4 tests, no network).

Deliberately **not** changed: chunk size (1000), gather concurrency (16), the single
commit per call, the once-per-call rule-set resolution and review-id lock, and the rate
limiter defaults. Raising a public source's request rate is a policy decision about that
source's real quota (none is published for NPPES or data.cms.gov; SAM.gov has a daily
quota), not a performance fix this lane is authorised to make — see §5.

## 3. n = 2,500 variants (same seeded delivery, repeat cycles; host shared with a peer session)

| Variant | Connectors | Pool in effect | Chunk | classify s | ms/entity | gather s | serial s | SQL stmts | physical connects | pool wait s (sum) | rate-limit wait s (sum, CMS_PPEF) | peak RSS MB (ext.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) current code, harness default | mock, 50 ms | NullPool | 1000 | 170.8 | 68.3 | 117.3 | 53.3 | 25,003* | 2,501 | 754.0 | 0 | 325.6 |
| (b) production pool shape | mock, 50 ms | QueuePool 5+10 | 1000 | **79.6** | 31.8 | 36.2 | 43.3 | 22,503 | 36 | 121.4 | 0 | 309.5 |
| (c) larger pool | mock, 50 ms | QueuePool 20+0 | 1000 | 86.2 | 34.5 | 40.9 | 45.2 | 22,503 | 16 | 62.7 | 0 | 309.1 |
| (d) pre-chunking shape | mock, 50 ms | NullPool | 2500 (1 chunk) | 167.9 | 67.2 | 118.6 | 49.1 | 22,503 | 2,501 | 671.7 | 0 | 347.4 |
| (e) **live NPPES/CMS, SAM keyless** | live | NullPool | 1000 | **568.1** | 227.2 | 534.0 | 34.0 | 22,503 | 2,501 | 191.2 | **14,246.7** | 304.5 |

\* variant (a) also ran the first cycle on a fresh delivery (2,500 extra statements from the NPI
ledger writes); (b)–(e) are repeat cycles on the same delivery. All five: 2,500/2,500 verified,
0 unresolved, bucket counts identical (B3 x 2,500 under the unmatched default, as expected for
synthetic NPPES-unknown NPIs), rule set size 5. LEIE pre-warm (timed separately, after the
single-flight fix): 3.8–4.7 s, +88–118 MB. Host shared with a peer session throughout; minimum
host free memory during a run 203–330 MB. Raw JSON/CSV per variant in `lanes/P/`.

What the table shows:

1. **Chunking is not a regression**: (a) vs (d) = 170.8 vs 167.9 s, +1.7 % at n = 2,500, with
   the same per-chunk serial cost. Memory at this n is similar either way; the chunked loop's
   benefit is bounded evidence residency at full scale, not speed.
2. **With live connectors the rate limiter dominates**: (e) gathers at 4.4 entities/s against a
   hard bound of 5/s (2 `CMS_PPEF` calls per entity, one 10 req/s bucket). Summed token-bucket
   wait 14,247 s across the 16 concurrent tasks; NPPES waited 6 s in total. Pool wait fell to
   191 s because connects were no longer the thing tasks queued on. **At 24,563 entities this
   bound alone is 4,913 s (81.9 min)**; the 88.1-min "pipelined regression" is this bound, not
   pipelining.
3. **NullPool is a pytest-harness artifact that inflated every earlier benchmark**: (a) vs (b)
   = 170.8 vs 79.6 s with mocked connectors; 2,501 physical connects at ~0.3 s average under
   load versus 36. Production (`app/core/database.py`) already uses QueuePool(5+10), and a
   bigger pool (c) is not faster, so **no pool change is made**. The production-shape number
   for the pipeline's own overhead is (b): ~32 ms/entity, ~18 ms of it the serial
   persist/classify step.
4. **The committed fix (single-flight LEIE) removes the first-wave warm-up** (44 s → 3 s on a
   cold process) and ~230 MB of transient peak; it is independent of the three points above.

## 4. Equivalence — what is compared and what is excluded, with reasons

`tests/test_chunked_gather_correctness.py` runs the same 600 entities through a single
chunk (the pre-chunking shape) and through 4 chunks, and compares every outcome field
(`bucket`, `rule_code`, `rule_version`, `rule_matched`, `tier`, `assigned_role`,
`dimensions`, `applicability`) plus the persisted `ReviewRecord` rows. Six keys are
excluded, each for an individually stated reason; nothing else is:

| Excluded key | Why it legitimately differs between two executions |
|---|---|
| `generation_timestamp` | `datetime.utcnow()` stamped when evidence is assembled |
| `retrieved_at` | per-source retrieval wall-clock stamp (`evidence_dimensions.py`) |
| `query_timestamp` | per-query wall-clock stamp (`cms_ppef.py` `CMSQuery`) |
| `discovered_at` | wall-clock stamp on address evidence (`address_evidence.py`) |
| `http_last_modified` | the upstream `Last-Modified` response header, echoed verbatim |
| `upstream_request_id` | CMS's per-request `x-request-id`, unique per network call by design |

Result: 600/600 entities identical on every remaining field. The single-flight LEIE guard
cannot change any of these fields: it changes who performs the download, not what is
indexed.

## 5. Remaining scale characteristics (measured or bounded, not solved)

- **Throughput ceiling is the rate limiter**: ≤ 5 entities/s while enrollment and
  revocation share one `CMS_PPEF` bucket; ≤ 10/s if they were split (same physical host
  data.cms.gov — whether that is acceptable to CMS is unknown). NPPES at 10/s is the next
  bound. Classification of a 24,563-entity delivery therefore takes **at least 82 minutes**
  on the committed defaults; the 33-minute figure from 2026-10-02 was measured before the
  limiter existed and is not reproducible on current code.
- **Memory** is now bounded by chunk size (1000 entities' evidence held at once) plus one
  LEIE index (~120 MB); the 1,230 MB unbatched peak included 16 concurrent CSV parses.
- **Transaction shape**: one transaction spans the whole call (every chunk's rows, one
  `commit`). At 24,563 entities that is a long-held write transaction; it is the designed
  atomicity guarantee, not an accident, and it is the reason per-chunk commits were not
  introduced.
- **Production pool**: `app/core/database.py` uses QueuePool(5 + 10 overflow = 15) while
  the gather wave can need 16 scratch sessions plus the caller's session (17). The excess
  waits for a checkout (default `pool_timeout` 30 s) rather than failing; measured in §3
  variant (b).
- **Event-loop occupancy**: evidence assembly and CSV parsing are CPU work on the loop;
  they inflate every concurrent task's measured wait. Not quantified separately.

## 6. Future capacity-test plan (not executed)

1. Repeat the full-population run (`qa-evidence/.../lanes/P/FULLSCALE-RUNBOOK.md`) on a
   quiet host, with the external RSS sampler, after the LEIE fix — the first complete
   full-scale number for the code as committed.
2. Same run with `TEFCA_RATE_LIMIT_CMS_PPEF_RPS` raised **only** after CMS confirms an
   acceptable rate, to measure the pipeline's own ceiling once pacing is not the bound.
3. Sustained-run checks the full-scale run cannot show: a 24 h-TTL LEIE refresh landing
   mid-run (the single-flight guard covers it; not yet exercised), source outages mid-run
   (unavailable must stay unavailable, never clear), and Postgres autovacuum behaviour
   under one long write transaction.
4. Multi-delivery concurrency: two `verify_and_classify` calls at full scale at once —
   review-id lock contention and pool behaviour (today proven only at 2-4 concurrent small
   batches).
5. Anything beyond ~10^5 entities per delivery needs a different design (per-chunk
   commits with a resumable cursor, a bounded evidence table rather than in-memory
   dicts) — out of scope and **not** inferable from any run here. 100 million records is
   three orders of magnitude beyond the largest measured population; no claim is made.
