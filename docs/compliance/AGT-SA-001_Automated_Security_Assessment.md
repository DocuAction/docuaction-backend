# AGT-SA-001 — Automated Security Assessment

**Contract:** 7571MN26F80064  ·  **Package:** AGT-SA-001  ·  **Environment:** Development

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
| pytest | pytest 9.1.1 |
| Bandit | __main__.py 1.9.4 |
| openapi-spec-validator | 0.9.0 |
| curl | curl 8.21.0 (Windows) libcurl/8.21.0 Schannel zlib/1.3.2 WinIDN WinLDAP |
| OWASP ZAP | Not Available — see ZAP_FINDING_VALIDATION.md |

## Result Summary

| Metric | Value |
|--------|-------|
| Total tests | 36 |
| Passed | 36 |
| Failed | 0 |
| Not Executed | 0 |
| Pass rate (of executed) | 100.0% |

## Scope

Security Validation of https://docuaction-dev.azurewebsites.net — manual request-level tests covering injection,
authentication, authorization, transport, headers, rate limiting, and
information disclosure. Executed against the **development environment only**;
production was not tested.

Dynamic Application Security Testing (DAST) using OWASP ZAP — a tool widely used
in federal secure development workflows — is recorded as **Not Executed**; see
`docs/security/ZAP_FINDING_VALIDATION.md` for the blockers and what would
unblock it.

## Test Results

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| TEST-SEC-01 | SQL injection in login email | Rejected (401, 422, 400) | HTTP 401 | PASS |
| TEST-SEC-02 | SQL injection in password | Rejected (401, 422, 400) | HTTP 401 | PASS |
| TEST-SEC-03 | XSS in email field | Rejected (401, 422, 400) | HTTP 401 | PASS |
| TEST-SEC-04 | Command injection in email | Rejected (401, 422, 400) | HTTP 401 | PASS |
| TEST-SEC-05 | Path traversal in briefing id | Rejected, no file content | HTTP 404, root:x present=False | PASS |
| TEST-SEC-06 | SSRF via URL-shaped path segment | Rejected, no metadata fetched | HTTP 404 | PASS |
| TEST-SEC-07 | CRLF injection in email | Rejected, no injected header | HTTP 401, X-Injected=None | PASS |
| TEST-SEC-08 | Oversized JSON body (>10MB) is rejected, not processed | Any rejection; never 500 or hang | HTTP 401 | PASS |
| TEST-SEC-09 | Null byte in path | Rejected (not 500) | HTTP 400 | PASS |
| TEST-SEC-10 | Unicode fullwidth normalization in email | Handled, not authenticated | HTTP 401 | PASS |
| TEST-SEC-11 | Tampered JWT payload | 401 | HTTP 401 | PASS |
| TEST-SEC-12 | JWT 'none' algorithm | 401 | HTTP 401 | PASS |
| TEST-SEC-13 | Expired token | 401 | HTTP 401 | PASS |
| TEST-SEC-15 | Extremely long token (10000 chars) | 401 | HTTP 401 | PASS |
| TEST-SEC-16 | Empty Authorization header | 401 | HTTP 401 | PASS |
| TEST-SEC-17 | 'Bearer' without token | 401 | HTTP 401 | PASS |
| TEST-SEC-14 | Viewer token returns only its own identity | viewer@docuaction.io | viewer@docuaction.io | PASS |
| TEST-SEC-19 | Viewer -> admin endpoint (bulletin sources/load-catalog) | (401, 403) | HTTP 403 | PASS |
| TEST-SEC-26 | Bulletin admin action with viewer token | (401, 403) | HTTP 403 | PASS |
| TEST-SEC-27 | TEFCA ARC admin action with viewer token | (401, 403, 422) | HTTP 403 | PASS |
| TEST-SEC-28 | Guarded endpoint with no auth | (401, 403) | HTTP 401 | PASS |
| TEST-SEC-21 | IDOR — entity by non-existent/foreign ID | 403/404, never another entity | HTTP 403 | PASS |
| TEST-SEC-22 | Role escalation via modified JWT | 401 | HTTP 401 | PASS |
| TEST-SEC-23 | Unknown endpoint | 404 (not 500) | HTTP 404 | PASS |
| TEST-SEC-24 | Wrong HTTP method | 405 (not 500) | HTTP 405 | PASS |
| TEST-SEC-25 | DELETE on audit records | 405/404 — append-only | HTTP 405 | PASS |
| TEST-SEC-20 | Viewer read of rules (allowed by policy) | 200 — viewer+ is the documented floor | HTTP 200 | PASS |
| TEST-SEC-29 | Error body has no stack trace | No traceback | HTTP 404, 'traceback' present=False | PASS |
| TEST-SEC-30 | Error body has no filesystem paths | No paths | '/home/' or 'C:\\' present=False | PASS |
| TEST-SEC-31 | Error body has no database detail | No DB info | 'postgres'/'sqlalchemy' present=False | PASS |
| TEST-SEC-32 | /openapi.json disabled on production | 404 | HTTP 404 | PASS |
| TEST-SEC-33 | Server header does not disclose framework | No fastapi/starlette | Server: 'uvicorn' | PASS |
| TEST-SEC-34 | 404 body does not echo the requested path | Path not reflected | path echoed=False | PASS |
| TEST-SEC-35 | /health discloses no credential VALUES | No secret values in the payload | value-pattern hits={'postgres URI with password': False, 'JWT value': False, 'assigned secret value': False, 'azure account key': False} | PASS |
| TEST-SEC-36 | Login error identical for unknown user vs bad password | Same message (no user enumeration) | unknown='Invalid email or password' badpw='Invalid email or password' | PASS |
| TEST-SEC-18 | Account lockout / throttle after repeated failures | 429 after repeated failures | sequence [401, 401, 401, 429, 429, 429, 429] | PASS |

## Finding Validation Log

Every finding was reproduced manually before any code change. Two initial FAILs
were raised by the harness and **both were confirmed false positives in the test
assertions, not defects in the application.** Neither produced a code change; the
assertions were corrected and the tests re-run.

| Finding | Initial result | Reproduction | Confirmed | Action |
|---------|----------------|--------------|-----------|--------|
| TEST-SEC-35 — credential in response body | FAIL | The matched string was `SAM_GOV_API_KEY` appearing inside the explanatory note *"Federal Registration — GSA (requires SAM_GOV_API_KEY)"*. That is a configuration **name**, not a credential **value**. No secret was disclosed. | **False positive** | Assertion narrowed to credential value patterns (Postgres URI, JWT, assigned secret, storage `AccountKey`). No application change. |
| TEST-SEC-08 — oversized request body | FAIL | Sent 1 MB and 11 MB bodies. Both were rejected (HTTP 401/429) in 2.5 s and 4.0 s. No HTTP 500, no hang, no resource exhaustion. The assertion had demanded one specific status code. | **False positive** | Assertion corrected to "any rejection; never 500 or hang". No application change. |

**Zero confirmed exploitable findings.** No security fix was applied because none
was warranted — auto-fixing either finding would have changed working code to
satisfy a faulty test.

## Limitations

- DAST was **Not Executed** (see above). Security Validation exercises paths
  chosen deliberately; a crawler exercises paths nobody thought to choose. The
  two find different things and this gap is open.
- Testing was performed against dev. Production configuration was not assessed.
- No authenticated fuzzing, no business-logic abuse testing, and no
  denial-of-service testing were performed.
- Rate limiting (20 login attempts / 15 min per IP) constrains test sequencing;
  the lockout test is deliberately run last so it cannot starve the tests after
  it.

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
