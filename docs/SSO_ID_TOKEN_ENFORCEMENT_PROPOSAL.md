# Proposal: switching Entra ID-token validation from `audit` to `enforce` (FOR REVIEW - nothing applied)

Status: proposal only. Nothing in this document has been run, deployed or configured. No environment value is set by PR #150, and the default stays `audit`.

## 0. Audit mode is observation, not remediation

With `AZURE_AD_ID_TOKEN_VALIDATION=audit` (the default) the callback validates the ID token and, on failure, only writes a log line. The sign-in then **continues on the unverified claims**. A token with a bad or absent signature is therefore **still accepted** and the signature-validation gap **remains exploitable**. Only `enforce` closes it (403, or 502 if the JWKS cannot be fetched). Until `enforce` is set and verified, do not describe SR-01 as remediated. An unrecognised value is treated as `audit` and logs a startup warning (length and shape only), so a typo never silently looks like enforcement.

Exposure caveat (from code only): the id_token is received server-side from Microsoft's token endpoint over TLS and no browser-supplied token path exists, so reaching the gap needs a man-in-the-middle of that exchange or a misconfigured tenant. The real exposure on DEV/PROD is **unverified** (section 6).

## 1. Proposed setting

| Item | Value |
|---|---|
| Name | `AZURE_AD_ID_TOKEN_VALIDATION` |
| Proposed value | `enforce` (exact, lower case; anything else is treated as `audit`) |
| Where it would be set | An environment variable on the backend Container App (and the equivalent App Service setting if that host is still serving) for the target environment. DEV first, PROD only after DEV passes. |
| Applied by | An operator, deliberately, after sign-off. This PR does not set it and no `az` command was run in preparing this proposal. |
| Migration | None. A restart / new revision picks up the value at import time. |

## 2. Pre-flight evidence required before switching (all read-only)

1. Configuration presence (names only): run `scripts/sso_config_presence_check.py` against an exported environment; confirm `AZURE_AD_CLIENT_ID`, `AZURE_AD_CLIENT_SECRET`, `AZURE_AD_TENANT_ID` are SET (it prints SET/UNSET and a length bucket, never values). If any is UNSET the SSO routes return 503 and there is nothing to enforce.
2. Tenant, issuer, audience, JWKS facts the operator must confirm (not confirmed by this PR):
   - `AZURE_AD_TENANT_ID` is the single organisation tenant GUID (not `common`/`organizations`); the validator requires `iss` = `https://login.microsoftonline.com/<tenant>/v2.0` and `tid` = that tenant.
   - The app registration issues v2.0 tokens (not forced to the v1 issuer `sts.windows.net`); a v1 issuer would be rejected under enforce.
   - `aud` of the ID token equals `AZURE_AD_CLIENT_ID`.
   - `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys` is reachable from the backend network (egress/firewall); in enforce a JWKS outage returns 502 and blocks sign-in.
3. Audit-mode log evidence. Deploy #150 with the default `audit` and read the backend logs (Log Analytics / container logs) for:
   - **Would-reject signature**: `Azure ID token validation failed: reason=<reason> mode=audit`. Each occurrence is a sign-in that `enforce` would have blocked. Reasons: `bad_alg`, `no_kid`, `unknown_kid`, `malformed`, `jwks_unavailable`, `tid_mismatch`, `nonce_mismatch`, `invalid_<jose error class>` (signature, expiry, audience, issuer, nbf).
   - **Success denominator**: completed SSO callbacks (`GET /api/auth/callback/azure` answered 302 to the post-login redirect), by role of the resulting user.
   - **Startup**: one `SSO ID-token validation mode=audit` info line per start, and **no** `UNRECOGNISED value` warning (if present, fix the typo first).
4. Proposed threshold (for the reviewer to confirm or change): **zero** would-reject lines for real sign-ins over **at least 14 days AND at least 30 successful SSO sign-ins**, with **at least one successful sign-in for every role in use** (admin, program_manager, qalead, senior_analyst, reviewer, manager, contributor, viewer - whichever are provisioned). Any nonzero count must be explained in writing (for example a QA probe with a deliberately bad token) and excluded explicitly, not ignored. `jwks_unavailable` events need a network explanation first.
5. Rollback readiness: the previous container revision / setting value and the break-glass path in section 4 are identified before the change.

## 3. Staged rollout

1. Merge and deploy #150 with `audit` (default). Read logs for the window in section 2.4. No behaviour change for users.
2. DEV: set `enforce` in a controlled window with the operator present. Run the section 4 checks within 5 minutes.
3. Observe DEV for a full working day; compare would-reject counts (expected zero) and the 401/403 rate.
4. PROD: repeat steps 1-3 using PROD's own audit evidence (DEV evidence does not stand in for PROD tenant, redirect or JWKS egress facts). Prefer an off-peak window.

## 4. Rollback checks

Revert: set `AZURE_AD_ID_TOKEN_VALIDATION` back to `audit` (or remove it; unset means `audit`) and start a new revision, or reactivate the previous revision. No data change is involved, so rollback is configuration-only.

Within the first minutes after switching, observe:
- A successful SSO sign-in by an operator account for **each role** in scope (lands in the app; `GET /api/auth/me` returns 200 with the expected role).
- Callback outcomes: no unexpected 403 `ID token validation failed.` and no 502 on `/api/auth/callback/azure`; the 401/403 rate on auth endpoints is not above the pre-change baseline.
- Logs: no `Azure ID token validation failed: reason=... mode=enforce` for legitimate users. Any such line for a legitimate user means roll back immediately and record the `reason`.
- Email/password login still works (unchanged by this PR).

Break-glass: local email/password login (`POST /api/auth/login`) is independent of SSO and stays available for an admin account, so an enforce-induced SSO outage does not lock administrators out. Before the change, confirm at least one admin password account works and the operator holds its credentials.

## 5. Not covered

Account linking by the email claim, the unbound `state` (login CSRF) and the default-role choice are unchanged and out of scope.

## 6. Unverified

- The live `AZURE_AD_*` configuration on DEV and PROD (no evidence on disk; the presence check has only been exercised on a synthetic file).
- Whether the exposure is real on DEV/PROD (depends on the above and on the network path to the Microsoft token endpoint).
- Whether any legitimate sign-in would be rejected (needs the section 2.3 audit evidence, which does not exist because #150 is not deployed).
- Issuer version (v1 vs v2) and egress to the JWKS endpoint from the hosted environment.
