# AGT-TE-006 — Performance Baseline, Access Control & API Contract

**Contract:** 7571MN26F80064  ·  **Package:** AGT-TE-006  ·  **Environment:** Development

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service (Linux) |
| Build | Git SHA `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-01T22:33:41+00:00 |
| Contract | 7571MN26F80064 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| openapi-spec-validator | 0.9.0 |
| OWASP ZAP | Not Available — see ZAP_FINDING_VALIDATION.md |

## Part 1 — Role-Based Access Control

### Role accounts authenticated

| Role | Authenticated |
|------|---------------|
| viewer | Yes |
| analyst | Yes |
| reviewer | Yes |
| testadmin | Yes |
| admin | Yes |

### Endpoint access by role

| Endpoint | Expected Min Role | viewer | analyst | reviewer | admin |
|---|---|---|---|---|---|
| GET /health | Public | HTTP 200 | HTTP 200 | HTTP 200 | HTTP 200 |
| GET /bulletin/latest/fcc | Public | HTTP 200 | HTTP 200 | HTTP 200 | HTTP 200 |
| GET /bulletin/costs | Contributor+ | HTTP 403 | HTTP 200 | HTTP 200 | HTTP 200 |
| POST /bulletin/run/fcc | Contributor+ | HTTP 403 | HTTP 200 | HTTP 200 | HTTP 200 |
| GET /arc/review-rules | Viewer+ | HTTP 200 | HTTP 200 | HTTP 200 | HTTP 200 |
| POST /arc/samples | Contributor+ | HTTP 403 | HTTP 200 | HTTP 200 | HTTP 200 |
| GET /arc/reports | Viewer+ | HTTP 200 | HTTP 200 | HTTP 200 | HTTP 200 |
| POST /arc/review-rules | Admin | HTTP 403 | HTTP 403 | HTTP 403 | HTTP 422 |
| GET /tefca/registry/entities | Reviewer+ | HTTP 403 | HTTP 403 | HTTP 200 | HTTP 200 |

### Role enforcement scenarios

| Test ID | Description | Expected | Actual | Result |
|---------|-------------|----------|--------|--------|
| RBAC-01 | Expired token on a guarded endpoint | (401,) | HTTP 401 | PASS |
| RBAC-07 | JWT role escalation viewer -> admin | (401,) | HTTP 401 | PASS |
| RBAC-03 | Viewer cannot POST a write endpoint | 401/403 | HTTP 403 | PASS |
| RBAC-04 | Analyst cannot resolve a B3 review | 401/403 (404 if role passes but review absent) | HTTP 403 | PASS |
| RBAC-05 | Reviewer cannot create a rule | 401/403 | HTTP 403 | PASS |

**Result: 5 PASS / 0 FAIL / 0 Not Executed of 5.**

FastAPI 0.140 returns **401** for a missing or malformed bearer token and **403**
for a valid token with insufficient role. Both are correct denials; the
distinction is noted because an earlier framework version returned 403 in both
cases and a reader comparing against older evidence would otherwise read the
change as a regression.

## Part 2 — Performance Baseline

### CSV parse + validate (in-process, no database)

| Rows | Seconds | Rows/sec | Parsed OK | Errors |
|------|---------|----------|-----------|--------|
| 100 | 0.0025 | 39,331 | 100 | 0 |
| 1000 | 0.0168 | 59,640 | 1000 | 0 |
| 5000 | 0.0808 | 61,884 | 5000 | 0 |

### End-to-end CSV import (dev, HTTP)

| Rows | HTTP | Seconds | Rows/sec | Imported | Errors |
|------|------|---------|----------|----------|--------|
| 50 | 200 | 11.94 | 4.2 | 48 | 2 |

**Large-volume end-to-end import (1,000+ rows): Not Executed.**
No delete endpoint exists; a 1,000+ row benchmark would permanently contaminate the dev ARC registry and every subsequent sample draw and report.

The parse stage is measured at 5,000 rows precisely because it is the part that
can be measured without writing anything. The end-to-end figure is dominated by
database round-trips and per-entity savepoints, not parsing. The two are not
interchangeable and no throughput was extrapolated from one to the other.

### Read-path latency (5 samples per endpoint)

| Endpoint | n | Mean (s) | Median (s) | Min (s) | Max (s) |
|----------|---|----------|------------|---------|---------|
| `/api/tefca/registry/entities?limit=50` | 5 | 0.814 | 0.831 | 0.775 | 0.843 |
| `/api/tefca/registry/stats` | 5 | 1.363 | 1.077 | 0.929 | 2.703 |
| `/api/tefca/arc/reviews?limit=50` | 5 | 0.771 | 0.794 | 0.687 | 0.806 |
| `/api/tefca/arc/review-rules` | 5 | 0.71 | 0.704 | 0.652 | 0.783 |

> **Independently re-verified 2026-08-04.** These read-path figures were
> re-measured against the same environment and confirmed accurate. Four fresh
> samples of `/api/tefca/registry/stats` returned 2.34s (cold), 0.75s, 1.04s and
> 1.07s — consistent with the 1.363s mean / 1.077s median / 2.703s max recorded
> above.
>
> This matters because `docs/audit/PERFORMANCE_BASELINE.md` separately claimed the
> same endpoint had degraded to **5.38s** under the benchmark population. That
> claim did not reproduce and has been corrected in that document. **The figures in
> this evidence package were correct as delivered and are unchanged.**
>
> The 22,200 synthetic benchmark entities were soft-deleted on 2026-08-03/04, after
> these measurements were taken. Post-cleanup sampling returned 1.31s and 1.27s —
> within the same band, confirming the synthetic population was not the driver of
> read-path latency.

### Entity verification latency (live authoritative registries)

| n | Mean (s) | Median (s) | Min (s) | Max (s) |
|---|----------|------------|---------|---------|
| 10 | 1.84 | 1.68 | 1.56 | 2.53 |

Each verification queries NPPES, PECOS and OIG LEIE over the public internet, so
these timings include third-party latency outside the platform's control.

### Report generation

| Report | HTTP | Seconds | Entities Reviewed |
|--------|------|---------|-------------------|
| weekly | 200 | 0.86 | 32 |
| quarterly | 200 | 0.9 | 32 |

## Part 3 — API Contract

| Field | Value |
|-------|-------|
| OpenAPI version | 3.1.0 |
| Documented paths | 294 |
| Documented operations | 308 |
| Schema validation | PASS — no errors reported by `openapi-spec-validator` |

Full detail: `docs/audit/API_CONTRACT_VALIDATION.md`.

## Limitations

- Single-workstation client; network latency to Azure is included in every dev
  figure and was not isolated.
- **No concurrent-load or soak test was run.** All figures are single-request
  serial measurements. Concurrency behaviour: **Not Executed.**
- Sample size is 5 per read endpoint and 10 for verification — enough to show
  magnitude, not enough for a p95/p99 tail-latency claim. None is made.
- The dev App Service is an S1 tier instance; these are not capacity-planning
  numbers for production.
- No cold-start measurement is included; the app was already warm.
- Contract validation confirms the document is schema-valid and that the tested
  endpoints behave as documented. Response-schema conformance across all 308
  operations: **Not Executed.**

## Appendix — Connector Operational Monitoring

Each connector was called 5 times directly against its authoritative endpoint
during this test window. "Uptime" is the observed success rate across those
calls; it is **not** a historical availability figure and no long-run uptime is
claimed.

| Connector | Uptime | Last Success | Last Failure | Avg Latency |
|-----------|--------|-------------|--------------|-------------|
| NPPES | 5/5 (100.0%) | 2026-08-02T01:05:08+00:00 | None recorded | 391 ms |
| PECOS | 5/5 (100.0%) | 2026-08-02T01:05:12+00:00 | None recorded | 242 ms |
| OIG LEIE | 5/5 (100.0%) | 2026-08-02T01:05:17+00:00 | None recorded | 428 ms |
| SAM.gov | 0/5 (0.0%) | None recorded | 2026-08-02T01:05:22+00:00 | 252 ms |

SAM.gov returned HTTP 404 on all 5 calls. That is the documented behaviour of
`DEMO_KEY` against the entity-information API, not an outage: the endpoint
requires a registered api.data.gov key. SAM.gov is not operational and is
excluded from confidence scoring.

PECOS resolves through the same CMS NPI dataset as NPPES, so its timing is
correlated with NPPES by construction rather than independently sampled.

Full detail: `docs/audit/CONNECTOR_OPERATIONAL_MONITORING.md`.

---

## Appendix — Sprint update : SAM.gov, sources, and CI DAST

### SAM.gov connector — built, NOT operational

| Item | Status |
|------|--------|
| Entity Management API (`v3/entities`) | Implemented — registration + Active check |
| Exclusions API (`v4/exclusions`) | Implemented — queried **independently**, not inferred from the v3 summary flag |
| UEI exact match | Implemented |
| Legal-name fallback | Implemented; >1 match reports `ambiguous` for manual review |
| **API key** | **NOT PROVISIONED** |
| **Operational status** | **NOT OPERATIONAL — excluded from confidence scoring** |

Both endpoints were probed and returned **HTTP 404 with `DEMO_KEY` and with no
key**. A registered key is required for each. Steps to obtain one:
`docs/SAM_GOV_API_KEY_SETUP.md`.

A key alone is necessary but not sufficient — SAM is keyed on UEI, which the
registry does not currently capture.

### Classification rules — version 2 active

v1 retired (5 rules, `retired_date` set), v2 active (5 rules). SAM is wired in as
a **disqualifier**, never a requirement: every SAM condition fires only on a
positive finding, so with no key classification is identical to v1 — verified by
`test_v2_is_identical_to_v1_when_sam_is_silent`.

v2 also fixes a real defect in v1: RULE-005 matched only status `debarred`, but
the connector emits `excluded`, so a SAM-excluded entity with clean NPPES/PECOS
was classified **B1 "No Discrepancy"**. It is now B4.

### Connector matrix — current, measured

| Connector | Uptime (5 calls) | Avg latency | Scoring |
|-----------|------------------|-------------|---------|
| NPPES | 5/5 (100%) | 391 ms | **Included** |
| PECOS | 5/5 (100%) | 242 ms | **Included** |
| OIG LEIE | 5/5 (100%) | 428 ms | **Included** |
| SAM.gov | 0/5 (0%) — HTTP 404, no key | 252 ms | **Excluded — not operational** |

### Bulletin source health — 431 feeds probed twice

| Category | Count |
|----------|-------|
| ACTIVE | 161 |
| TRANSIENT_RECOVERED (working; first sweep was wrong) | 78 |
| DEAD_URL (404/410 twice — deactivated) | 78 |
| ACCESS_BLOCKED (401/403 — NOT deactivated) | 58 |
| STALE | 38 |
| UNREACHABLE | 15 |

The fast sweep reported 232 failures; **78 of them (34%) worked on a gentler
re-probe.** Only twice-confirmed 404/410 feeds were deactivated. Full evidence:
`docs/audit/SOURCE_HEALTH_INVESTIGATION.md`.

### DAST now runs in CI

DAST could not execute on the workstation (no container runtime, no JRE). It is
wired into GitHub Actions instead, dev-only, with a guard that fails the job if
the target resolves to production:

| Pipeline | Surface | Schedule |
|----------|---------|----------|
| `zap-scan.yml` (OWASP ZAP) | Unauthenticated | Mondays 06:00 UTC |
| `stackhawk-scan.yml` (StackHawk) | **Authenticated** (bearer token) | Mondays 06:30 UTC |

They are complementary, not redundant: an unauthenticated scan records every
TEFCA ARC endpoint as `401` and moves on. Setup and the mandatory finding-
validation workflow: `docs/DAST_CI_SETUP.md`.

**Neither has executed yet** — both are scheduled/manual-trigger, and StackHawk
additionally needs `HAWK_API_KEY`. Results: **Not Executed**.

### Risk acceptance

Six entries recorded in `docs/RISK_ACCEPTANCE_REGISTER.md` (RA-001..006), review
date 2026-10-31. The register is **unsigned** — risk acceptance is a human
decision and is not recorded as taken until it has been.
