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
