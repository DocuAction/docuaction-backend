# DocuAction Authentication — Current Baseline and Target Architecture

DOCUMENT_TITLE = Authentication Target Architecture
DOCUMENT_VERSION = 0.1
STATUS = PENDING_REVIEW
AUTHORED_BY = Release 1.0 structural hardening builder session (AI-assisted, maker role)
AUTHORED_DATE = 2026-09-14
SOURCE_PR = backend feat/structural-hardening-1.0 (see PR description)
SOURCE_SHA = backend main 75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96; frontend PR #42 head 52f91d57cf46690389e560ba871626c21c46a9f7
REVIEWED_BY = PENDING
REVIEW_DATE = PENDING
APPROVED_BY = PENDING
APPROVAL_DATE = PENDING

This is an assessment. It changes nothing at runtime (`AUTH_RUNTIME_CHANGED = NO`). It is not ONC or COR direction and does not authorize a migration; any change to authentication is a separately authorized sprint.

## 1. Current architecture (observed, not inferred)

Every statement below was read from the code at the SOURCE_SHA or reproduced on a local synthetic runtime on 2026-09-14. No secret values were read or printed.

| Aspect | Observed |
|---|---|
| Token type | JWT, HS256, signed with the single process `SECRET_KEY` (`app/core/security.py`, `ALGORITHM`); the same key signs access and refresh tokens |
| Claims | `sub` (user id), `role`, `exp`, `jti`; refresh tokens add `type=refresh` |
| Access-token lifetime | `ACCESS_EXPIRE_NORMAL = 15 minutes` for every role except admin; `ACCESS_EXPIRE_ADMIN = 24 hours` (admin role or an address in `ADMIN_EMAILS`) |
| Refresh flow | `create_token_pair()` mints a refresh token and `refresh_access_token()` rotates it with revocation and account-state re-checks, **but** `/api/auth/login` returns `TokenResponse(access_token, user)` only — the refresh token is discarded — and the router that exposes `POST /api/auth/refresh` (`app/api/auth_endpoints.py`) is not mounted in `app/main.py`. Net effect: no refresh flow exists for any caller |
| Consequence | Every non-admin user (all TEFCA operational roles: program_manager, qalead, senior_analyst, reviewer, viewer) is hard-logged-out 15 minutes after login. Reproduced: a fresh program_manager token reads `/api/tefca/registry/*` 200; the same token after 15 minutes gets 401 from `/api/auth/me` and the frontend redirects to `/login`. This is the true cause of the "registry 401 for Program Manager" report; it is not an RBAC defect (see FEDERAL_DEPLOYMENT_ISOLATION_ASSESSMENT.md, section 10) |
| Logout / invalidation | Server-side: `POST /api/auth/logout` stamps `users.tokens_revoked_at`; `_token_revoked()` rejects any token issued before that stamp on every request. Password reset and account disable use the same epoch. This part is sound |
| Role lookup | `get_current_user` loads the user row on every request and RBAC (`require_role`, `ROLE_HIERARCHY`) uses the **database** role, not the JWT claim. Role changes take effect immediately. The JWT `role` claim is used by the rate limiter only (tier selection) |
| 401 vs 403 | 401 = missing/invalid/expired token or unknown user (`get_current_user`); 403 = authenticated but below the required role (`require_role`, "Admin access required"). Reproduced for admin-only endpoints with reviewer/viewer/PM tokens |
| Storage (browser) | `localStorage` under five keys: `token`, `govcon_token` (same value written twice at login), `user` (profile JSON incl. role and allowed_modules), `access_token`, `refresh_token` (the last two are legacy keys; `src/lib/session.ts` clears all five on logout). No cookie is used; `allow_credentials=False` on CORS |
| Session storage | none |
| Same-origin exposure | Any script running on the frontend origin can read the token (XSS = full session theft for the token's lifetime). The Content-Security-Policy on the Static Web App is the only mitigation; the 15-minute lifetime bounds the window for non-admins, the 24-hour admin lifetime does not |
| Multiple-app origin sharing | One backend, one CORS allow-list (`ALLOWED_ORIGINS`), one token namespace for Core, TEFCA and GovCon pages. `govcon_token` is a duplicate key, not a separate session — the GovCon client (`src/lib/api.ts`) and the platform clients read the same JWT |
| Rate-limit interaction | Identity for the limiter comes from the JWT `role` claim; before this sprint the tier map omitted every TEFCA role, so those users fell to the free tier (60/min, burst 10 per 5 s) and CORS preflights were counted per client IP. Both corrected in this sprint (`app/core/rate_limiter.py`; tests `tests/test_rate_limit_roles_and_preflight.py`). Limits were not raised globally and the limiter is not disabled |
| Secret scope | one `SECRET_KEY` per process; length/entropy enforced at startup (≥ 64 characters). Key rotation would invalidate all sessions; there is no key id (`kid`) |
| SSO | Microsoft Entra ID login is an additional path (`app/api/azure_auth_routes.py`) that ends by minting the same JWT |

### 1.1 Risk statement

CURRENT_AUTH_RISK = MEDIUM-HIGH for a federal deployment: bearer JWT in `localStorage` (XSS-exfiltratable), no refresh flow (usability failure that pushes users toward workarounds), 24-hour admin tokens, single shared signing key without rotation, one token namespace across three product domains.

What is sound and must be preserved by any target: database-authoritative role on every request, server-side revocation epoch, bcrypt password hashing, per-request audit of auth events, CORS without credentials.

## 2. Target options

| Option | Security | Implementation complexity | Current-QA impact | Role-model impact | API impact | Migration risk | Rollback | Azure compatibility | Program isolation |
|---|---|---|---|---|---|---|---|---|---|
| **A. Current architecture, hardened** — issue the refresh token at login, mount `/api/auth/refresh`, silent renewal in the shared API clients, shorten admin access tokens to 15 min (refresh carries the session), add `kid` for key rotation, keep `localStorage` | Medium (token still readable by page scripts) | Low: code exists; ~4 files backend, ~3 files frontend | Low: transparent to users; QA logins simply stop expiring at 15 min | None | Additive (`refresh_token` field in login response) | Low | Trivial (feature flag on issuing refresh) | Full | None by itself |
| **B. Server session cookie** — `HttpOnly; Secure; SameSite=Strict` session cookie, CSRF token for state-changing calls, server-side session table with idle/absolute timeouts | High (no token readable by scripts; CSRF handled) | Medium: cookie plumbing, CSRF, `allow_credentials=True` on CORS with an exact origin list, session store | Medium: QA must re-login once; SWA and App Service are different origins so `SameSite=Strict` needs a same-site domain plan (custom domain + API subdomain) or `SameSite=None` with CSRF | None | Every client stops sending `Authorization`; `fetch` gains `credentials: 'include'` | Medium (origin/domain planning) | Medium | Full (App Service + SWA custom domains) | Cookie scope can be per program host |
| **C. BFF (backend-for-frontend)** — the SWA-linked API or a thin gateway holds the session and proxies to the platform API; browser never sees a token | High | High: new component, deployment topology change, two hops | Medium | None | Frontend calls same-origin `/api/*`; platform API becomes internal | Medium-High | Medium | Full (SWA "Bring your own functions" or App Service front) | Strong: one BFF per program host |
| **D. Microsoft Entra / Azure-native identity** — Entra ID as the identity provider (already wired as SSO), MSAL in the browser with PKCE, API validates Entra-issued tokens (RS256, JWKS), app roles or groups mapped to DocuAction roles | High (no shared HS256 secret; rotation by the IdP; conditional access, MFA, PIV/HSPD-12 path) | High: tenant/app registrations, role mapping, all local-password users migrate or keep a fallback | High during migration: QA accounts must exist in the tenant; local password login retired or dual-run | Medium: DocuAction roles stay, mapping layer added | Bearer token stays but issuer changes; API adds JWKS validation | High | Hard once local login is retired | Native | Strong: per-program app registration and tenant policy |
| **E. Hybrid (recommended path)** — A now; D for identity when the federal tenant is available; B or C for the browser session layer chosen with the domain plan | High at end state; Medium immediately | Staged | Staged | None now | Additive now | Low now | Trivial now | Full | Reached in stage 2/3 |

## 3. Recommendation

TARGET_AUTH_PATTERN = E (hybrid): **stage 1 = option A** (refresh flow and rotation-ready keys, no storage change) as the Release 1.0 usability fix; **stage 2 = option D** identity (Entra ID with PKCE; HSPD-12/PIV becomes possible) once the program tenant and role mapping are approved; **stage 3 = option B or C** for the browser session layer, decided together with the custom-domain plan for the TEFCA deployment (same-site cookie requires the SWA and API under one registrable domain).

MIGRATION_COMPLEXITY = LOW (stage 1) / HIGH (stages 2–3)
QA_IMPACT = LOW (stage 1: sessions stop expiring at 15 minutes; nothing else changes for testers) / HIGH (stage 2)
RECOMMENDED_RELEASE = stage 1 in Release 1.0 as a **separately authorized** change (it alters the login response and mounts a router; it is not part of this sprint); stages 2–3 in Release 1.1 under the federal deployment isolation plan.

## 4. What this sprint did and did not change

- Changed: nothing in authentication. `AUTH_RUNTIME_CHANGED = NO`, `ROLE_RUNTIME_CHANGED = NO`, `ROLE_SCHEMA_CHANGED = NO`.
- Changed, adjacent: rate-limiter tier mapping and preflight handling (documented above); middleware order so a 429 carries CORS headers.
- Not changed, deliberately: token lifetime, refresh issuance, storage, signing algorithm, SSO.

## 5. Human decisions required

1. Authorize stage 1 (issue refresh token at login, mount `/api/auth/refresh`, silent renewal, admin token lifetime) as its own change with its own checker pass.
2. Decide the TEFCA deployment domain plan (custom domain for SWA + API) — this decides between B and C.
3. Decide whether the federal deployment authenticates against a Government Entra tenant (stage 2) and who owns role mapping.
4. Decide the admin token lifetime policy for the interim (24 hours is long for a bearer token in `localStorage`).
