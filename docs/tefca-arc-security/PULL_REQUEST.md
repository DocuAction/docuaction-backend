# Pull Request — TEFCA ARC Security Hardening (RC2)

**Title:** `security: TEFCA ARC security hardening (RC2) — rate limiting, JWT revocation, error handling, DB SSL, PII masking, headers`

**Base:** `main` ← **Compare:** `security/tefca-arc-hardening`

---

## Summary

Security hardening for the TEFCA ARC module: 9 phases + 2 finalized findings (RC2). **Security hardening only** — no new functionality, no schema/API/business-logic changes, 100% backward compatible (all new protections are config-gated or no-ops for current roles/paths).

Closes the two findings from the RC1 verification:
- **R1** — immediate token revocation on role/permission change, account disable, and password change.
- **R2** — corrected the fail-open comment and added the opt-in `REVOCATION_FAIL_CLOSED` flag (default off).

## Controls (NIST 800-53 Rev. 5)

Rate limiting (SC-5) · standardized error handling + request correlation (SI-11/AU-3) · JWT revocation (AC-12/IA-11/AC-2(1)) · database SSL (SC-8/SC-13) · presentation-layer PII masking (AC-3) · audit correlation (AU-3(1)/AU-10) · security headers (SC-7/SC-18) · configurable session timeouts (AC-12) · fail-closed option (CM-6/SI-4).

## Changed files (13 code)

`app/main.py`, `app/core/{security,rate_limiter,error_handler,request_context,token_revocation,pii_presentation,database}.py`, `app/services/audit.py`, `app/api/{routes,admin_users,password_reset}.py`, `app/Tefca/routes.py`. New modules: `request_context.py`, `token_revocation.py`, `pii_presentation.py`.

**No changes** to models/migrations, API contracts, JWT format, validation engine, taxonomy, connectors, reports, or other modules (Bulletin / Healthcare / Enterprise / Case Management).

## Verification (executed, not assumed)

- RC1: 24/24 checks pass; 12/12 attempted attacks repelled.
- RC2: 11/11 regression checks pass (role/permission/disable/password revocation, fail-open default, fail-closed option, routes intact, no DB changes).
- Reports: `TEFCA_ARC_Security_Hardening_Report_v1.0.md`, `TEFCA_ARC_Final_Security_Verification.md`, `TEFCA_ARC_Final_Security_RC2.md`, `TEFCA_ARC_Security_Release_Notes_v1.0.md`.

## Deploy / rollback

Config + standard redeploy only; no migration. See `TEFCA_ARC_Deployment_Guide.md` and `TEFCA_ARC_RC_Checklist.md`. Rollback = unset an env var or `git revert` the independent phase commit(s); no data rollback.

## Reviewer notes

- Defaults preserve prior behavior; production tightening is via the documented env flags.
- Security code is **frozen** post-merge — further changes require a documented defect or approved change request.
