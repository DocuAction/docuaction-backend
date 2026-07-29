# Security Headers & Transport Review

> Manual review of CORS, security headers, rate limiting, and cookie flags. Read-only. Primary evidence: `app/main.py:36-67`, `app/core/rate_limiter.py`, `frontend/public/staticwebapp.config.json`.

## Backend (`main.py`) — GOOD (Info)

| Control | Setting | Verdict |
|---|---|---|
| **CORS origins** | `allow_origins=settings.cors_origins` (explicit list, no wildcard) | ✅ |
| **CORS credentials** | `allow_credentials=False` | ✅ (safe with `methods=*`) |
| CORS methods/headers | `allow_methods=["*"]`, `allow_headers=["*"]` | ◐ minor — could tighten (Low) |
| **TrustedHost** | `TrustedHostMiddleware(allowed_hosts=settings.trusted_hosts)` | ✅ (unlisted host → 400) |
| **HSTS** | `Strict-Transport-Security: max-age=31536000; includeSubDomains` | ✅ |
| **X-Content-Type-Options** | `nosniff` | ✅ |
| **X-Frame-Options** | `DENY` | ✅ |
| **CSP** | `default-src 'self'` | ✅ (strict; API-only so low risk of breakage) |
| Referrer-Policy / Permissions-Policy | set | ✅ |
| **Rate limiting** | global `RateLimitMiddleware` (in-memory tiered) + stricter per-auth-endpoint limits | ✅ (caveat below) |
| API docs | disabled in prod (`ENABLE_OPENAPI` flag) | ✅ |
| Cookies | none — bearer-header auth only | ✅ (no cookie-flag surface) |

This is an **above-average header posture** for the sector — strict CORS, full security-header set, CSP, and TrustedHost all enforced in code.

### SH-01 — Rate limiting is in-memory / per-process (Low, CWE-770)
`core/rate_limiter.py:75` `RateLimitMiddleware` + the login/IP throttles are **in-process dicts** — not shared across workers/instances, and reset on restart. Effective on a single instance (current prod topology), but weakens under `--workers 4` or scale-out. **Fix:** back with Redis (same fix as AUTH-02 lockout + scheduler dedup). **Low.**

### SH-02 — CORS methods/headers wildcards (Low)
`allow_methods=["*"]`, `allow_headers=["*"]` — with `allow_credentials=False` this is low-risk, but tightening to the actually-used methods/headers is best practice. **Low.**

## Frontend Static Web App (`staticwebapp.config.json`) — partial

| Control | Setting | Verdict |
|---|---|---|
| X-Content-Type-Options | `nosniff` | ✅ |
| X-Frame-Options | `SAMEORIGIN` | ◐ (backend uses DENY; SWA serves the app UI so SAMEORIGIN is acceptable) |
| Referrer-Policy | `strict-origin-when-cross-origin` | ✅ |
| **CSP** | **absent** | ❌ SH-03 |
| **HSTS** | **absent** | ❌ SH-04 |
| trailingSlash | `always` | ✅ |
| 404 | `/404.html` | ✅ |

### SH-03 — No CSP on the SWA frontend (Medium, CWE-1021)
The static frontend sets no `Content-Security-Policy`. The app renders user/clinical data; a CSP (even a modest `default-src 'self'; connect-src 'self' https://api-prod.docuaction.io`) would materially cut XSS blast radius. **Fix:** add CSP to `staticwebapp.config.json` global headers. Effort: 0.5d + testing.

### SH-04 — No HSTS on the SWA frontend (Low)
The backend sends HSTS; the SWA does not. SWA serves HTTPS-only by default, so this is defense-in-depth. **Fix:** add HSTS header. Effort: trivial.

## Verdict
Transport/header security is a **relative strength** — the backend posture is strong and correct. The gaps are on the **frontend SWA** (no CSP, no HSTS — SH-03/04) and the **in-memory rate limiting** (SH-01, shared with the Redis theme). OWASP **A05 (Security Misconfiguration): Low-Medium**, driven mainly by SH-03 and the Part-9 infra items (public KV/Postgres, no App Service IP restrictions).

## NIST mapping
SC-7 (boundary protection) ✅, SC-5 (DoS/rate-limit) ◐, SC-18 (mobile code/CSP) ◐ (frontend gap).
