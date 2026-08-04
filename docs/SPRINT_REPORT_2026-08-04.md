# Sprint Report — Benchmark Cleanup, Finding Remediation, Evidence Correction

**Contract:** 7571MN26F80064 · **Date:** 2026-08-04 · **Environment:** Development

All figures below come from actual execution output. Anything not run is recorded
as **Not Executed** with its reason, never estimated.

---

## Readiness matrix

| Area | Status | Evidence |
|---|---|---|
| Block 6 benchmark cleanup | **Complete — 22,198 of 22,200** | `scripts/cleanup_benchmark_entities.py`; 2 residual, see Open items |
| F-001 CSV import error disclosure | **Fixed, deployed — not live-verified** | `routes.py`, `fhir_import.py:234`; unit-covered |
| F-002 NUL byte returns HTTP 500 | **Fixed, deployed, VERIFIED LIVE** | `app/core/input_sanitize.py`, `app/main.py` |
| Backend test suite | **274 passed, 22 skipped, 0 failed** | 537.97s, local |
| Dev deployment | **Deployed and verified** | `cd325555-8047-4dea-a380-ef6849f9e1ea` |
| `PERFORMANCE_BASELINE.md` corrections | **Complete** | three figures corrected, see below |
| `AGT-TE-006` performance figures | **Re-verified — already correct** | no numeric change required |
| Evidence package retention | **All 5 retained** | decision recorded below |
| Production deployment | **Not performed — out of scope by instruction** | — |
| Credential rotation | **Deferred by instruction** | see Open items |

---

## Block 6 cleanup

The load benchmark left synthetic entities in the dev registry. Targeting was by
TEFCAID prefix, computed client-side: the list endpoint's `q` parameter filters on
`name` only (`queries.py:127`), and TEFCAID lives in a separate identifiers table
attached per page, so no server-side filter for these patterns exists.

| Pattern | Matched | Deleted | Residual |
|---|---:|---:|---:|
| `TID-P100-%` | 200 | 200 | 0 |
| `TID-P1000-%` | 2,000 | 2,000 | 0 |
| `TID-P10000-%` | 20,000 | 19,998 | 2 |
| `TID-TH%` | 0 | — | — |
| **Total** | **22,200** | **22,198** | **2** |

**Survivors held at exactly 74 across every inventory pass** — six passes taken
before, during and after deletion. `no TEFCAID` was 0 throughout. Nothing outside
the three populated patterns was touched.

Deletion is soft: rows retain `is_deleted`/`deleted_at` so `review_records`,
`tefca_verifications` and `sample_entities` keep their referent, and deleted rows
drop out of listings, stats and the sample frame. Nothing was physically removed.

**`TID-TH%` matched zero rows** on every pass. It was removed from the documented
cleanup recommendation.

### Execution record

| Batch | Workers | Attempted | Deleted | Failed | Wall time | Rate |
|---|---:|---:|---:|---:|---|---:|
| Verification | 1 | 100 | 100 | 0 | 3.9 min | 0.4/s |
| Calibration | 6 | 300 | 300 | 0 | 1.9 min | 2.63/s |
| Bulk (crashed) | 6 | 21,800 | 3,070 | 14 | — | 2.6/s |
| Bulk (stopped) | 6 | 18,730 | 12,174 | 1 | — | 6.1/s |
| Final | 6 | 6,556 | 6,554 | 2 | 16.6 min | 6.6/s |

The third batch terminated on an unhandled `ConnectionResetError`. Azure's
connection resets surface as raw `OSError`, not `urllib.error.URLError`, so the
handler missed them and the exception propagated out of a worker thread through
`pool.map`, tearing down the run. Fixed by catching `OSError` with exponential
backoff and making workers non-propagating. Failure rate fell from 14-in-3,050 to
1-in-12,149 and throughput more than doubled as connections warmed.

All failures across all batches were transient network faults (`status 0`). There
were **no API rejections, no auth failures and no 409s** — no entity was ever
refused by the server.

---

## Finding remediation

### F-001 — CSV import returns raw database exceptions (Medium)

`POST /api/tefca/registry/import/csv`. Reported as returning HTTP 200 with
`status: "failed"` and an unmodified SQLAlchemy exception in `errors[]`, disclosing
ORM name, driver, exception class, constraint, table, columns and the raw INSERT.

**Sanitisation was already complete** in the working tree and required no change:
`safe_import_error` covers the parse path (`csv_import.py:91`), the entity persist
path (`fhir_import.py:234` — where the finding's duplicate-NPI `IntegrityError`
actually originates) and the relationship path (`:260`).

The outstanding gap was the status code. Now:

| Outcome | Status | Rationale |
|---|---|---|
| Nothing imported | **422** | Submission unprocessable as a whole — the reported case |
| Partial success | **207** | Rows really were created; a 4xx would invite a blind full retry |
| All imported | 200 | — |

### F-002 — Raw NUL byte returns HTTP 500 (Low)

Postgres cannot store or compare a NUL inside text, so a raw `\x00` reaching any
query raised at the driver and surfaced as HTTP 500 — a validation failure reported
as a server fault.

The prior fix guarded `/search` only. Replaced with request-level middleware
covering path and query string on **every** route, so the guarantee no longer
depends on each handler remembering to call `reject_null_bytes()`.

Scope is deliberately URL-only. Buffering request bodies in middleware would hold
every CSV/FHIR upload in memory before the route sees it, and the import endpoints
exist to accept multi-megabyte files. Body-borne NULs remain the handlers'
responsibility.

### Live verification (post-deploy, dev)

| Check | Expected | Actual |
|---|---|---|
| `GET /health` | 200 | **200** |
| `GET /health?x=%00` | 422 | **422** |
| `GET /api/tefca/registry/search?q=%00` | 422 | **422** |
| `GET /api/tefca/registry/search?q=test` (no auth) | 401 | **401** |
| `GET /health?x=%2500` (literal, double-encoded) | 200 | **200** |

The registry endpoint returning **422 rather than its usual 401** confirms the
guard executes ahead of authentication, i.e. it is genuinely request-level. The
`%2500` case confirms no false positive on percent-encoded text.

---

## Evidence corrections

### `docs/audit/PERFORMANCE_BASELINE.md` — three figures corrected

| Claim | Was | Corrected to |
|---|---|---|
| `/registry/stats` latency under benchmark load | 5.38s | ~0.75-1.07s warm, 2.34s cold |
| Non-synthetic entity count | 71 | 74 |
| Cleanup patterns | 4 patterns | 3 — `TID-TH%` matched nothing |

The latency claim did not reproduce. Four samples taken with the full 22,274-row
population still present returned 2.34s (cold), 0.75s, 1.04s, 1.07s — **below the
1.82s mean recorded in the same document as the healthy baseline**. The 5.38s
figure was most likely captured while delayed bulk imports were still committing,
describing write contention rather than a steady state. Post-cleanup samples of
1.31s and 1.27s sit in the same band, confirming the synthetic population was not
driving read-path latency.

The old counts also failed to reconcile: 71 + 22,172 = 22,243 against a measured
22,274. The corrected 74 + 22,200 = 22,274 exactly.

### `AGT-TE-006_Performance_and_Access_Control` — verified, unchanged

Its read-path table already reported `/registry/stats` at mean 1.363s / median
1.077s / max 2.703s, consistent with re-measurement. **The bad 5.38s figure never
entered delivered compliance evidence** — it existed only in the internal audit
document. A dated re-verification note was added; no numbers were altered.

### Evidence package retention — all five retained

`AGT-TE-005` and `AGT-TE-006` each name two documents. They are **complementary
deliverables sharing a package ID, not superseded versions**:

| Package | Document | Unique content |
|---|---|---|
| AGT-TE-005 | Functional Test Evidence | Omnibus summary across blocks |
| AGT-TE-005 | TEFCA Operational Validation | Connector monitoring + SAM.gov appendices |
| AGT-TE-006 | TEFCA ARC Validation | Task 3/4/5 ARC evidence |
| AGT-TE-006 | Performance and Access Control | Per-sample performance + RBAC detail |
| AGT-SA-001 | Automated Security Assessment | Security validation suite |

Deleting the earlier pair would have destroyed the only detailed performance and
access-control evidence. **No evidence documents were deleted.**

---

## Deployment

| Field | Value |
|---|---|
| Target | `docuaction-dev` / `rg-docuaction-dev` |
| Method | Overlay zip, `--clean false` |
| Artifact | `dev-deploy.zip`, 195 files, 0.8 MB, app-only |
| Deployment ID | `cd325555-8047-4dea-a380-ef6849f9e1ea` |
| Completed | 2026-08-04T20:03:01Z, status 4, active |
| Production | **Not deployed — excluded by instruction** |

Overlay rather than `--clean` because the change set is three Python files with no
dependency changes, and `--clean` wipes wwwroot before unpacking — a bad artifact
takes the site down rather than failing safely. The artifact was verified before
upload: all four changed files present, correct content, zero `__pycache__` entries.

**`--restart true` did not restart the app.** The first verification pass ran
against the old build and both fixes appeared absent. An explicit
`az webapp restart` was required before the new code took effect. This reproduces
the warning at `DEPLOYMENT_GUIDE.md:52` and is a second independent confirmation
that the flag cannot be relied upon.

---

## Open items

**From this sprint:**

- [ ] **2 residual synthetic entities** (`TID-P10000-%`). Both were transient
      network failures, not rejections. Re-running the cleanup script clears them;
      it is idempotent and treats already-deleted rows as 409/skip.
- [ ] **F-001 not verified live.** Unit-covered and deployed, but the 422 path was
      not exercised against dev — it needs an authenticated reviewer-role upload of
      a CSV with a duplicate NPI. Recommended before the finding is closed.
- [ ] **`admin@docuaction.io` credential rotation.** Deferred by instruction until
      QA finishes. The password was disclosed in a working session and
      `docs/SESSION_STATE.md` requires rotation on disclosure.
      `scripts/reset_qa_passwords.py` is ready; it stamps `tokens_revoked_at`, so it
      must run between jobs, not during one.
- [ ] **Nothing committed.** All changes are working-tree only, including two new
      scripts and the pre-existing uncommitted fixes. Note that F-001/F-002 shipped
      undeployed for two days precisely because they sat uncommitted.
- [ ] **Count discrepancy unreconciled.** The 2026-08-01 readiness matrix reports
      RBAC 5/5 and TEFCA operational 24-of-25; `AGT-TE-005:64-66` reports 6/6 and
      26/26. The later run probably supersedes, but two delivered artifacts under
      one contract state different counts for the same blocks.

**Carried forward:**

- [ ] DAST/ZAP **Not Executed** — no Docker, no JRE; needs `HAWK_API_KEY`
- [ ] Semgrep never run — every security score to date is an upper bound
- [ ] Database restore never rehearsed, RTO unmeasured
- [ ] Risk acceptance register unsigned
- [ ] SAM.gov and NewsData.io API keys not provisioned
- [ ] Railway → Azure DNS cutover (needs registrar access)
- [ ] 58 access-blocked feeds (User-Agent/headers)
- [ ] UEI population for SAM exact matching
- [ ] Audit-log tamper-evidence not implemented
