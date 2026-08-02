# RBAC Verification Matrix

**Contract:** 7571MN26F80064

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

## Role accounts authenticated

| Role | Authenticated |
|------|---------------|
| viewer | Yes |
| analyst | Yes |
| reviewer | Yes |
| testadmin | Yes |
| admin | Yes |

## Endpoint access by role

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

## Role enforcement scenarios

| Test ID | Description | Expected | Actual | Result |
|---|---|---|---|---|
| RBAC-01 | Expired token on a guarded endpoint | (401,) | HTTP 401 | PASS |
| RBAC-07 | JWT role escalation viewer -> admin | (401,) | HTTP 401 | PASS |
| RBAC-03 | Viewer cannot POST a write endpoint | 401/403 | HTTP 403 | PASS |
| RBAC-04 | Analyst cannot resolve a B3 review | 401/403 (404 if role passes but review absent) | HTTP 403 | PASS |
| RBAC-05 | Reviewer cannot create a rule | 401/403 | HTTP 403 | PASS |

**Result: 5 PASS / 0 FAIL / 0 Not Executed of 5.**

### Note on 401 vs 403

FastAPI 0.140 returns **401** for a missing or malformed bearer token and **403**
for a valid token whose role is insufficient. Both are correct denials; the
distinction is recorded here because an earlier framework version returned 403 in
both cases, and a reader comparing to older evidence would otherwise read the
change as a regression.
