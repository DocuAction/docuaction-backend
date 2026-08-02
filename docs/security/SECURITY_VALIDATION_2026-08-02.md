# Security Validation — Block 3

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `14fd30f37f0b17c3cd3717d93ca7deda452516a1` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T20:14:49.279815+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

## Scope and terminology

These are **Security Validation** tests: manual, reproducible probes executed
directly against the development API. They are not a penetration test, and they
do not substitute for DAST — see `ZAP_FINDING_VALIDATION.md` for the separate
dynamic-scanning record.

**Target: development only** (`https://docuaction-dev.azurewebsites.net`).
Production was not probed.

## Result summary

| Suite | Tests | Pass | Fail |
|---|---|---|---|
| A — Injection | 10 | 9 | 1 |
| B — Authentication | 8 | 8 | 0 |
| C — Access Control | 11 | 11 | 0 |
| D — Information Disclosure | 8 | 8 | 0 |
| **Total** | **37** | **36** | **1** |

## Suite A — Injection

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| A1 | SQL injection in login email field | 401/422, no auth, no SQL error | HTTP 401, leaks=[] | **PASS** |
| A2 | SQL injection in password field | 401, no auth | HTTP 401, leaks=[] | **PASS** |
| A3 | XSS payload in search query | not executable in a browser context | HTTP 200, ct=application/json, nosniff=nosniff, inert=True | **PASS** |
| A4 | Command injection metacharacters in search | handled safely, no shell output | HTTP 200, leaks=[] | **PASS** |
| A5 | Path traversal in entity id | 404/422, no file contents | HTTP 404 | **PASS** |
| A6 | SSRF payload (cloud metadata IP) | no outbound fetch | HTTP 200 | **PASS** |
| A7 | CRLF injection in parameter | no injected response header | HTTP 200 | **PASS** |
| A8 | Oversized JSON body (~2MB) | rejected, no crash | HTTP 422 | **PASS** |
| A9 | Raw null byte in parameter | handled without HTTP 500 | HTTP 500, leaks=[] | **FAIL** |
| A10 | Unicode/RTL-override payload | handled, no crash | HTTP 200, leaks=[] | **PASS** |

## Suite B — Authentication

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| B1 | Tampered JWT signature | 401 | HTTP 401 | **PASS** |
| B2 | 'none' algorithm JWT | 401 (alg confusion rejected) | HTTP 401 | **PASS** |
| B3 | Expired token | 401 | HTTP 401 | **PASS** |
| B4 | Very long bearer token (5000 chars) | 401/431, no crash | HTTP 401 | **PASS** |
| B5 | Empty Authorization header | 401/403 | HTTP 401 | **PASS** |
| B6 | 'Bearer' with no token | 401/403 | HTTP 401 | **PASS** |
| B7 | Valid non-admin token authenticates (fresh) | 200 | HTTP 200 | **PASS** |
| B8 | Account lockout after 5 failures (synthetic account) | 401 x5 then 429 'Account temporarily locked' | codes=[401, 401, 401, 401, 401, 429], account_locked=True, ip_throttled=False | **PASS** |

## Suite C — Access Control

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| C1 | viewer -> POST /arc/review-rules (admin-only) | 403 | HTTP 403 | **PASS** |
| C2 | viewer -> POST /arc/reports/generate (admin-only) | 403 | HTTP 403 | **PASS** |
| C3 | No Authorization header | 401 | HTTP 401 | **PASS** |
| C4 | IDOR probe, non-existent UUID | 404, no data | HTTP 404 | **PASS** |
| C5 | JWT role claim modified viewer->admin | 401, signature invalid | HTTP 401 | **PASS** |
| C6 | Unknown endpoint under auth prefix | 404 | HTTP 404 | **PASS** |
| C7 | Wrong HTTP method on known route | 405 | HTTP 405 | **PASS** |
| C8 | viewer -> POST /registry/dev/seed (admin-only) | 403 | HTTP 403 | **PASS** |
| C9 | analyst -> PATCH B3 resolve (reviewer-only) | 403 | HTTP 403 | **PASS** |
| C10 | reviewer -> POST /arc/review-rules (admin-only) | 403 | HTTP 403 | **PASS** |
| C11 | viewer -> GET /registry/entities (router requires reviewer) | 403 | HTTP 403 | **PASS** |

## Suite D — Information Disclosure

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| D1 | Malformed UUID returns no stack trace | no traceback | HTTP 422, leaks=[] | **PASS** |
| D2 | No filesystem paths in error body | none | found=[] | **PASS** |
| D3 | No database engine details in error body | none | found=[] | **PASS** |
| D4 | OpenAPI posture on dev | reachable on dev by design | HTTP 200 | **PASS** |
| D5 | Server header hides stack version | no version string | server='uvicorn' | **PASS** |
| D6 | Generic 404 on unknown path | 404, no internals | HTTP 404, leaks=[] | **PASS** |
| D7 | /health leaks no secrets | clean payload | HTTP 200 | **PASS** |
| D8 | Unknown-user error == wrong-password error | identical generic message | HTTP 401, msg='Invalid email or password' | **PASS** |

## Findings

### A9 — HTTP 500 on a raw null byte in a query parameter — CONFIRMED

| Field | Value |
|---|---|
| Severity | Low |
| Endpoint | `GET /api/tefca/registry/search?q=<NUL>` |
| Status | Confirmed by reproduction |

**Reproduction.** A raw `\x00` in `q` returns **HTTP 500**. The percent-encoded
form `%00` returns 200, and an otherwise identical clean string returns 200. The
failure is specific to an unescaped NUL byte reaching the query layer.

**Assessment.** PostgreSQL cannot store or compare NUL bytes inside a text value,
so the driver raises and the request ends in the generic error handler. This is
an input-validation gap, not a data-exposure one: the response body is the
standard generic error with a `request_id`, and leaks no traceback, driver name,
file path or SQL fragment (verified — see D1–D3).

**Disposition.** Low severity, so per the governing rule it goes to risk
acceptance rather than an immediate fix. The correct fix is to reject or strip
NUL bytes during request validation, before the value reaches the query.

### A3 — Reflected XSS payload — FALSE POSITIVE

The initial pass flagged `<script>alert(1)</script>` as reflected unescaped in the
search response. Validation shows it is **not exploitable**, on three independent
grounds:

1. `Content-Type: application/json` — the browser does not parse the body as markup.
2. `X-Content-Type-Options: nosniff` — MIME sniffing that could reinterpret it is off.
3. `Content-Security-Policy: default-src 'self'` — inline script execution is blocked.

The payload is reflected into a JSON **string value**, which is inert. Recorded as
a false positive rather than silenced, so a later reader can see it was examined.

## A correction to this run's own method

The first execution of Suite C reported C1, C2, C8, C9 and C10 as failures —
401 where the role gate should return 403. **Those results were invalid**, and the
cause was in the test harness, not the application.

Access tokens are not uniform: `ACCESS_EXPIRE_NORMAL` is **15 minutes**, while
`ACCESS_EXPIRE_ADMIN` is **24 hours** (`app/core/security.py:24-25`). The harness
cached tokens to stay inside the per-IP login throttle (20 attempts / 15 min), and
the non-admin tokens silently expired mid-run while the admin token kept working.
An expired token fails `decode_token` and returns 401 "Invalid or expired token" —
which is indistinguishable, at the status-code level, from a role rejection unless
you check the body.

Suite C was re-executed with tokens minted immediately before use, and with an
explicit `/api/auth/me` liveness assertion on each token before any role claim was
tested. All role gates then returned **403** as specified. The corrected results
are the ones tabulated above.

This is recorded because a stale-token artefact reported as an RBAC defect would
have been a false finding in a security document — the same class of error the
finding workflow exists to catch.

## Note for Block 4

Any RBAC matrix must mint non-admin tokens per test batch, or it will produce
this same false 401 across every non-admin cell.
