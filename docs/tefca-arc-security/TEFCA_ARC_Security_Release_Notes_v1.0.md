# TEFCA ARC — Security Hardening Release Notes v1.0 (RC2)

**Module:** TEFCA Audit, Review & Compliance (ARC)
**Branch:** `security/tefca-arc-hardening` · **Status:** Release Candidate (RC2) — security code **frozen**
**Change type:** Security hardening only. No new functionality, no schema/API/business-logic changes. 100% backward compatible.

---

## 1. What's in this release

A 9-phase security hardening pass plus two finalized findings (RC2). Every change is additive and either config-gated or a no-op for current roles/paths, so default runtime behavior is unchanged until an operator opts into a stricter setting.

| Area | Change | NIST |
|---|---|---|
| Rate limiting | `RateLimitMiddleware` registered; scoped to auth endpoints; env-configurable | SC-5 |
| Error handling | `ErrorHandlerMiddleware` wired (safe bodies, no stack traces) + request/correlation IDs | SI-11, AU-3 |
| JWT revocation | Logout, password change, **role/permission change**, disable → immediate revocation (Redis-or-memory) | AC-12, IA-11, AC-2(1) |
| Database SSL | Optional `DATABASE_SSL` (require/verify-full) | SC-8, SC-13 |
| PII masking | Presentation-layer, role-aware (no-op for reviewer+) | AC-3 |
| Audit correlation | request/correlation/session id + duration in existing `details` JSON | AU-3(1), AU-10 |
| Security headers | Added Referrer-Policy + Permissions-Policy | SC-7, SC-18 |
| Session security | Configurable timeouts; verified bearer-only (no CSRF surface) | AC-12 |
| Fail-closed option | `REVOCATION_FAIL_CLOSED` (default off) | CM-6, SI-4 |

## 2. Commits (11)

```
ebd77e4  security: finalize JWT revocation and configurable fail-closed behavior (RC2)
50ee230  RC1 final security verification
d226753  P9 FedRAMP readiness + final report
1adb73b  P8 configurable session timeouts
1799422  P7 security headers (Referrer-Policy, Permissions-Policy)
0c06e13  P6 audit correlation enrichment
d55b52b  P5 presentation-layer PII masking
aba27c6  P4 optional database SSL
fecca4f  P3 JWT revocation
50e9728  P2 standardized error handling + request correlation
5a3febc  P1 scoped rate limiting
```

## 3. Files changed (13 code files, +/- additive)

`app/main.py`, `app/core/{security,rate_limiter,error_handler,request_context,token_revocation,pii_presentation,database}.py`, `app/services/audit.py`, `app/api/{routes,admin_users,password_reset}.py`, `app/Tefca/routes.py`.

**New modules (3):** `request_context.py`, `token_revocation.py`, `pii_presentation.py`.
**No changes** to models, migrations, API contracts, JWT format, validation engine, taxonomy, connectors, reports, scheduler, or any other module (Bulletin / Healthcare / Enterprise / Case Management).

## 4. New configuration (all optional; defaults preserve prior behavior)

| Variable | Default | Effect |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | master switch |
| `RATE_LIMIT_SCOPE` | `sensitive` | `sensitive` = auth only; `all` = tier-wide |
| `RATE_LIMIT_AUTH_PER_MINUTE` / `RATE_LIMIT_AUTH_BURST` | `10` / `5` | auth throttle |
| `RATE_LIMIT_SENSITIVE_PATHS` | auth path list | override matched paths |
| `TOKEN_REVOCATION_ENABLED` | `true` | revocation enforcement |
| `REDIS_URL` | unset | distributed revocation (else in-memory) |
| `REVOCATION_FAIL_CLOSED` | `false` | deny on store outage when `true` |
| `TOKEN_REVOCATION_MAX_TTL` | `604800` | revocation entry ceiling (s) |
| `DATABASE_SSL` | unset | `require` / `verify-full` |
| `STANDARDIZED_ERROR_ENVELOPE` | `false` | uniform error body when `true` |
| `ACCESS_TOKEN_MINUTES` / `ADMIN_TOKEN_HOURS` / `REFRESH_TOKEN_DAYS` | `15` / `24` / `7` | session lifetimes |
| `PII_MASKING_ENABLED` | `true` | no-op for reviewer+ |

## 5. Verification evidence

- **RC1 verification** (`TEFCA_ARC_Final_Security_Verification.md`): 24/24 checks pass; 12/12 attacks repelled (JWT forgery/alg-none/expired, RBAC 403, disabled/revoked-token 401, SQLi bound-param, host-spoof 400, brute-force 429, no stack-trace leak).
- **RC2 verification** (`TEFCA_ARC_Final_Security_RC2.md`): 11/11 regression checks pass — role/permission/disable/password revocation, fail-open default, fail-closed option, routes intact, no DB changes.
- Method: executed tests against the real `app.main:app` (Python 3.13). No fabricated results.

## 6. Backward compatibility & rollback

- Every phase is individually revertible (`git revert <sha>`) and most are also disabled by a single env var.
- Default configuration is behavior-identical to pre-hardening `main`.
- No database migration required; no rollback of data.

## 7. Known / residual (non-blocking) items

Tracked in the verification reports: enable `DATABASE_SSL` in prod; set `REDIS_URL` for multi-instance revocation; field-level encryption at rest remains a future item; consider shorter `ADMIN_TOKEN_HOURS` and `REVOCATION_FAIL_CLOSED=true` for tightened/FedRAMP baselines.

---
*Security code is frozen as of RC2. Further security changes require a documented defect or approved change request.*
