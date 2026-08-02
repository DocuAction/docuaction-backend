# Performance Baseline — Block 6

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `ebfcd38e067fd2b879e095eee547e40931a8e027` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T22:01:51.930566+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

## Targets and outcome

| Operation | Target | Measured | Verdict |
|---|---|---|---|
| CSV import, 100 rows | < 30s | **35.47s** (HTTP 200, 100 imported, server 33,976 ms) | **MISS** |
| CSV import, 1,000 rows | < 5 min | connection dropped; rows landed anyway | **FAIL (client)** |
| CSV import, 10,000 rows | — | connection dropped; rows landed later | **FAIL (client)** |
| Verification, per entity | < 3s | 4.50s / 4.60s / 3.61s mean (n=1 / 10 / 100) | **MISS** |
| Report generation | < 30s | 1.49s – 2.74s | **MEETS** |

## Import benchmarks

| Rows | Payload | Wall time | HTTP | Imported | Errors |
|---|---|---|---|---|---|
| 100 | 8.9 KB | 35.47s | 200 | 100 | 0 |
| 1000 | 94.5 KB | 273.86s | RemoteProtocolError: Server disconnected wit | - | - |
| 10000 | 1002.7 KB | 277.61s | RemoteProtocolError: Server disconnected wit | - | - |

Throughput at the one clean data point: **2.8 rows/sec** (100 rows, 35.47s).

## Finding — long imports exceed the request window and drop the connection

**Both large imports returned `RemoteProtocolError: Server disconnected without
sending a response`.** The important part is what happened next: **the rows
landed anyway.** The registry stood at 2,274 when the benchmark reported "DONE",
and at **22,274** when measured again a few minutes later — the 10,000-row
payloads completed server-side long after the client had given up, and the retry
wrapper had re-sent them, so the work was done twice.

**What this means operationally.** A caller importing a large file receives a
transport error and cannot tell whether the import succeeded, partially applied,
or failed. Retrying is the natural response and duplicates the work. There is no
completion signal to poll.

**A caveat on the wall times above.** The 1,000 and 10,000 row figures include
retry attempts, so they are upper bounds rather than single-request latency. A
follow-up single-attempt run (no retry) dropped at **60.8s** for
400 rows, and a subsequent 1,000-row attempt returned **HTTP
500** in 1.85s while the server was still
working through the queued imports.

**Not diagnosed here.** The precise cause — platform idle timeout, worker
saturation, or per-request limit — was not isolated, and this document does not
assert one. What is established is the behaviour and its consequence.

**Recommendation.** Make large imports asynchronous: accept the file, return
`202 Accepted` with a batch id, and let the client poll `/import/{batch_id}`.
The batch record already exists and already carries status and counts.

## Verification benchmarks

| Batch | Total | Mean | Median | Max | Errors | vs 3s target |
|---|---|---|---|---|---|---|
| 1 | 4.5s | 4.496s | 4.496s | 4.496s | 0 | MISSES |
| 10 | 45.97s | 4.597s | 4.227s | 8.602s | 0 | MISSES |
| 100 | 360.64s | 3.606s | 3.615s | 7.205s | 0 | MISSES |

Every batch misses the 3s-per-entity target. Verification makes live calls to
NPPES, PECOS and the OIG LEIE list, so the floor is set by those upstream
services rather than by application code. Note the single-entity case (4.50s) is
slower per entity than the 100-entity case (3.61s), which is consistent with
connection reuse amortising across a batch.

## Report generation

| Iteration | HTTP | Seconds | Reviews covered | vs 30s target |
|---|---|---|---|---|
| 1 | 200 | 1.7s | 152 | MEETS |
| 2 | 200 | 2.74s | 152 | MEETS |
| 3 | 200 | 1.49s | 152 | MEETS |

## Read-path latency

| Endpoint | Runs | Mean | Median | Max |
|---|---|---|---|---|
| GET /registry/entities (limit 50) | 5 | 1.314s | 1.349s | 1.513s |
| GET /registry/stats | 5 | 1.82s | 1.624s | 3.063s |
| GET /arc/reviews (limit 100) | 5 | 1.181s | 1.159s | 1.315s |

## Environmental impact of this benchmark — cleanup required

The registry grew from **71 entities at the start of the session to
2274 at the end of the benchmark, and to 22,274 once the
delayed imports completed**. Of those, 22,172 are synthetic `draft` rows created
solely by this benchmark.

Measured side effects:

- `GET /registry/stats` latency degraded from ~1.8s to **5.38s**.
- Report distributions computed after this point cover a population dominated by
  synthetic data.

**The evidence packages in Block 8 are built from responses captured BEFORE the
bulk of this data landed**, so their counts describe the real registry rather
than the benchmark residue. Any figure regenerated after 2026-08-02T22:20Z will
not match them, and that is expected rather than a discrepancy.

**Recommended cleanup.** Soft-delete every entity whose TEFCAID matches
`TID-P100-%`, `TID-P1000-%`, `TID-P10000-%` or `TID-TH%`. This was not performed
automatically — bulk deletion against a shared environment is a deliberate act,
not a test teardown.

## Not measured

Server-side memory and CPU are not observable from the test client. They are
recorded as **not measured** rather than estimated. Obtaining them requires
Application Insights metrics for the App Service over the benchmark window.
