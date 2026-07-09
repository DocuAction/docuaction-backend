# TEFCA ARC — Release Candidate Checklist (Security Hardening RC2)

Branch: `security/tefca-arc-hardening` · Target: `main`
Purpose: gate the security-hardening RC through merge → deploy → post-deploy.

---

## A. Code freeze & scope
- [x] Security code frozen at RC2 (`ebd77e4`)
- [x] No new functionality / no refactor / no optimization / no redesign
- [x] No changes to models, migrations, API contracts, JWT format
- [x] No changes to Bulletin / Healthcare / Enterprise / Case Management
- [x] Further security changes require a documented defect or approved CR

## B. Verification (pre-merge) — all executed, evidence in reports
- [x] `py_compile` on all changed files → OK
- [x] `import app.main` → OK (full router set)
- [x] JWT: valid decode; tamper / alg-none / wrong-key / expired rejected
- [x] RBAC: below-min role → 403 (before DB access)
- [x] Revocation: logout / role change / permission change / disable / password change → 401
- [x] Fail-open default allows on store error; `REVOCATION_FAIL_CLOSED=true` denies (401)
- [x] Rate limiting: auth → 429 after burst; non-auth not throttled
- [x] Error handling: real 500 → safe body, no stack-trace/SQL/secret leak
- [x] Security headers present on every response (incl. errors)
- [x] Host-header spoofing rejected (400)
- [x] SQL injection payload bound as parameter (not inlined)
- [x] PII masking: reviewer+ byte-identical; lower role masked
- [x] No DB/schema/migration files changed (`git diff` verified)
- [x] RC1 report (`TEFCA_ARC_Final_Security_Verification.md`) — READY
- [x] RC2 report (`TEFCA_ARC_Final_Security_RC2.md`) — READY

## C. Pull request
- [ ] PR opened `security/tefca-arc-hardening` → `main` (see `PULL_REQUEST.md`)
- [ ] PR description references release notes + both verification reports
- [ ] Reviewer(s) assigned; security reviewer sign-off
- [ ] CI green (if configured)

## D. Pre-deploy configuration (production env)
- [ ] `SECRET_KEY` set (64+ chars, from secret store)
- [ ] `DATABASE_URL` set (managed Postgres)
- [ ] `DATABASE_SSL=require` (or `verify-full`) — **validate against DB endpoint first**
- [ ] `ALLOWED_ORIGINS` restricted to production origins
- [ ] `ALLOWED_HOSTS` restricted to production hosts
- [ ] `REDIS_URL` set IF running >1 instance (else in-memory revocation is per-instance)
- [ ] Confirm `RATE_LIMIT_*` values match policy (defaults 10/min, burst 5)
- [ ] Decide `REVOCATION_FAIL_CLOSED` (default `false`; `true` for high-assurance)
- [ ] Decide `ADMIN_TOKEN_HOURS` (default `24`; consider shorter)
- [ ] Leave `STANDARDIZED_ERROR_ENVELOPE=false` unless client-compat confirmed
- [ ] `ENABLE_QA_MONITOR` / `ENABLE_SCHEDULER` set on exactly one instance (unchanged policy)

## E. Deploy
- [ ] Merge PR to `main`
- [ ] Deploy `main` to production (existing pipeline)
- [ ] Confirm app boots (startup log: routers loaded, TEFCA registered)

## F. Post-deploy smoke verification
- [ ] `GET /health` → 200, module `tefca_review_protocol` active
- [ ] Response carries security headers (HSTS, CSP, X-Frame, nosniff, Referrer-Policy, Permissions-Policy) + `X-Request-ID`
- [ ] Authenticated TEFCA endpoint returns data for a reviewer token
- [ ] Unauthenticated TEFCA operational endpoint → 401/403
- [ ] Invalid/expired token → 401
- [ ] Rapid failed logins → 429 after burst
- [ ] Logout then reuse token → 401
- [ ] (If `DATABASE_SSL` enabled) DB connections succeed over TLS
- [ ] Error responses contain no stack traces; carry request id
- [ ] Audit records include request/correlation ids on a sampled action

## G. Rollback readiness
- [ ] Config rollback: unset the specific env var (e.g. `DATABASE_SSL`, `REVOCATION_FAIL_CLOSED`)
- [ ] Code rollback: `git revert <phase sha>` (phases are independent) or redeploy prior `main`
- [ ] No data rollback required (no schema/data changes)

## H. Sign-off
- [ ] Engineering
- [ ] Security
- [ ] Product / COR (as applicable)
- [ ] Go / No-Go recorded
