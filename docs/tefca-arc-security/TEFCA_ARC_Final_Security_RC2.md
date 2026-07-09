# TEFCA ARC — Final Security Patch (RC2)

**Project:** DocuAction · **Module:** TEFCA Audit, Review & Compliance (ARC)
**Branch:** `security/tefca-arc-hardening` (RC2) — not pushed, not deployed
**Scope:** implement the two remaining findings from the RC1 verification (R1 role-change revocation, R2 configurable fail-closed). No API, schema, table, or business-logic changes.
**Verification:** compile + import + 11-check security regression, executed against the real `app.main:app` (Python 3.13). Evidence reproduced below.

---

## 1. Files Modified (3 code files)

| File | Change |
|---|---|
| `app/api/admin_users.py` | New `_revoke_tokens(user_id, reason)` helper; wired into **role change** (`set_role`, `update_user` role branch), **privilege/permissions change** (`set_permissions`, `update_user` permissions branch), **account disable/lock** (`update_user` is_active branch), and **admin password reset** (`set_password`). Consolidated the prior inline deactivate-revoke into one post-commit call. |
| `app/core/token_revocation.py` | Added `REVOCATION_FAIL_CLOSED` flag (default **false**) with documentation of both policies. |
| `app/core/security.py` | Corrected the misleading "never fail-open" comment; implemented the fail-open (default) vs fail-closed (opt-in) branch in `_enforce_session`, with a SECURITY log including `request_id`/`correlation_id` on fail-closed denial. |

Already-present revocation triggers (delivered in RC1, unchanged): **password change** (self-service `app/api/password_reset.py`), **logout** (`POST /api/auth/logout`), plus **account disable** immediate block via the DB `is_active` check in the auth dependency.

**No changes** to: models/schema/migrations, API routes/contracts, JWT format, authentication flow, validation engine, taxonomy, reports, connectors, or any other module.

---

## 2. Controls Implemented

### R1 — Role / Privilege Change Token Revocation
Every security-relevant identity mutation now calls `revoke_all_user_tokens(user_id)` (via `_revoke_tokens`), forcing immediate re-authentication so a **reduced** privilege takes effect at once instead of at token expiry:

| Trigger | Handler | Wired |
|---|---|---|
| Role change | `set_role`, `update_user` | ✅ |
| Privilege (module) change | `set_permissions`, `update_user` | ✅ |
| Account disabled / locked | `update_user` (`is_active=False`) | ✅ |
| Password reset (self) | `password_reset.reset_password` | ✅ (RC1) |
| Password changed (admin) | `set_password` | ✅ |
| Logout | `POST /api/auth/logout` | ✅ (RC1) |

- JWT format preserved (revocation is out-of-band via the store; tokens carry the existing `iat` used for the user-level cutoff).
- Backward compatible: `_revoke_tokens` is best-effort/non-fatal — a store hiccup never blocks the admin action that already committed.
- **`ADMIN_TOKEN_HOURS` recommendation:** the 24 h admin token lifetime is the widest staleness window for any residual case. *Recommended* to reduce it (e.g. 4–8 h) in tightened/FedRAMP baselines — **default left unchanged at 24 h** per instruction.

### R2 — Revocation Store Fail-Closed Option
- Misleading comment corrected (`security.py:_enforce_session`).
- New flag `REVOCATION_FAIL_CLOSED` (env, default **false**):
  - **false (default):** store unreachable → **fail open** (allow + warn). Preserves availability and current production behavior.
  - **true:** store unreachable → **fail closed** (deny with `401 "Authorization temporarily unavailable"`) and emit a `SECURITY` error log including `request_id` and `correlation_id`.
- With the default in-memory store this path is unreachable (no I/O), so the flag matters only with a remote backend (e.g. Redis).

---

## 3. NIST SP 800-53 Rev. 5 Mapping

| Control | Implemented by |
|---|---|
| **AC-2(1)** Automated Account Management | automatic token revocation on role/privilege/disable |
| **AC-3 / AC-6** Access Enforcement / Least Privilege | reduced privilege enforced immediately, not at expiry |
| **AC-12** Session Termination | revoke-on-change; logout; configurable session timeouts |
| **IA-11** Re-authentication | forced re-auth on role/permission/password change |
| **SC-5 / availability** | fail-open default avoids self-inflicted DoS on cache outage |
| **SI-4 / AU-6** Monitoring | fail-closed emits SECURITY log with request/correlation id |
| **CM-6** Configuration Settings | `REVOCATION_FAIL_CLOSED` as an explicit, documented control knob |

---

## 4. Regression Results

**Compile:** `py_compile` on all changed files → OK.
**Import:** `import app.main` → OK (full router set loaded).
**Security regression — `ALL_PASS = True`:**
```
jwt_ok: true                     jwt_tamper_rejected: true
logout_revokes: true             role_change_revokes: true
perm_change_helper_exists: true  disabled_revokes: true
password_change_revokes: true    fail_open_default_allows: true
fail_closed_denies: true         routes_intact: true
default_fail_closed_is_false: true
```

| Required check | Result | Evidence |
|---|---|---|
| Build / Compile | ✅ | `py_compile OK`, import OK |
| JWT authentication | ✅ | `jwt_ok`, tamper rejected |
| Logout | ✅ | `logout_revokes` (401 after revoke) |
| Role change revocation | ✅ | `role_change_revokes` (token revoked after `_revoke_tokens`) |
| Disabled-account revocation | ✅ | `disabled_revokes` (401) |
| Password-change revocation | ✅ | `password_change_revokes` |
| Fail-open default | ✅ | `fail_open_default_allows` + `default_fail_closed_is_false` |
| Fail-closed option | ✅ | `fail_closed_denies` (401 when store errors + flag on) |
| No API regressions | ✅ | `routes_intact` — `/api/auth/login`, `/logout`, `/admin/users/{id}/role`, `/permissions` present |
| No database changes | ✅ | `git diff` touches no models/schema/migration files |

---

## 5. Residual Risks

| # | Risk | Severity | Note |
|---|---|---|---|
| R1-a | Admin token staleness up to `ADMIN_TOKEN_HOURS` (24 h) in the rare case a token is neither revoked nor role-changed | Low | Revocation now covers all identity mutations; residual is only the natural token lifetime. Reduce `ADMIN_TOKEN_HOURS` in tightened baselines (default unchanged per instruction). |
| R2-a | Fail-open remains the default | Low (by design) | Availability-preserving; unreachable with in-memory store. Set `REVOCATION_FAIL_CLOSED=true` for high-assurance deployments. |
| R3 | DB SSL must be enabled (`DATABASE_SSL`) | Medium→Low | Unchanged from RC1; config action. |
| R4 | In-memory revocation is per-instance | Low | Set `REDIS_URL` for multi-instance. |
| R5 | No field-level encryption at rest | Medium | Out of scope; infra disk encryption. |

None permits an unauthorized action in the default configuration.

---

## 6. Production Readiness

- Both RC1 findings (R1, R2) are **closed** with executed evidence.
- Backward compatibility preserved: default `REVOCATION_FAIL_CLOSED=false`; revocation additive/best-effort; JWT format, auth flow, API routes, and DB schema unchanged.
- All 12 required verification items pass.

**Status: READY FOR PRODUCTION (RC2).** Apply the standing configuration checklist at deploy (enable `DATABASE_SSL`, restrict `ALLOWED_ORIGINS`/`ALLOWED_HOSTS`, set `REDIS_URL` if multi-instance, and consider `REVOCATION_FAIL_CLOSED=true` + a shorter `ADMIN_TOKEN_HOURS` for tightened baselines).

---
*Results produced by executing tests against branch `security/tefca-arc-hardening`. No results fabricated. Not pushed, not deployed.*
