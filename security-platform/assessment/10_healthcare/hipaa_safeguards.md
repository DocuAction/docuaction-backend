# HIPAA Technical Safeguards Assessment

> §164.312 Technical Safeguards, assessed from code/config evidence. Read-only. Status: **Compliant / Partial / Gap.** Cross-references Part 8 (security) throughout.

## Safeguard matrix

| Safeguard | Section | Status | Evidence |
|---|---|:--:|---|
| **Access Control** | §164.312(a) | **Partial** | RBAC + auth on most endpoints — **but** the unauthenticated `case_management` PHI router (Part 8 AUTHZ-01) and **dual auth stacks** |
| **Audit Controls** | §164.312(b) | **Partial** | Writes + auth events logged — **but PHI reads not logged** |
| **Integrity** | §164.312(c) | **Partial** | SHA-256 on uploads/connectors — **but `evidence_hash` unset; audit log mutable** |
| **Person/Entity Authentication** | §164.312(d) | **Compliant** | JWT + bcrypt + per-request account-state enforcement |
| **Transmission Security** | §164.312(e) | **Gap** | Outbound TLS OK — **but no in-app inbound HTTPS/HSTS enforcement layer; DB TLS not pinned in code** |

## Detail

### Access Control §164.312(a) — Partial
- **Auth/RBAC:** registry router globally gated `Depends(require_role("reviewer"))` (`tefca_registry/routes.py:24-28`); writes require higher roles. Numeric `ROLE_HIERARCHY` (viewer=1…admin=8, `core/security.py:33-43`); per-user `allowed_modules` allowlist (`models/database.py:24`).
- **Unique user IDs:** UUID PK + unique email (`models/database.py:14-16`). ✅
- **Automatic logoff:** access token **15 min** (`security.py:24`), admin 24h (:25), refresh 7d; revocation epoch `tokens_revoked_at` (`security.py:76-95`). ✅
- **Gaps:** (1) the **unauthenticated `case_management` PHI router** (Part 8 AUTHZ-01) directly violates access control; (2) a **second, weaker auth stack** exists (`services/auth.py` + `config.py`, 480-min token, string-set roles) — dead code today, but a consistency/attack-surface risk if wired. **Partial.**
- **Encryption:** bcrypt for passwords; **no field-level encryption** on stored PHI (platform-level at-rest only).

### Audit Controls §164.312(b) — Partial
- Canonical writer `services/audit.py:41-73` `log_tefca_event` records user_id, action, resource_type, resource_id, ip_address, details(result), timestamp; registry has append-only `tefca_reg_audit_log` (`models.py:318-347`) with actor/action/metadata/ip.
- Logged: TEFCA mutations, verification, import, AI processing (`audit_logger.py`), **403 denials** (`error_handler.py:103-125`).
- **Gap:** **PHI/document READ access is not logged** — `GET /api/documents/...` and registry GET reads have no audit call (Part 8 AUDIT-READ). HIPAA expects PHI *access* (including views) to be auditable. **Partial.**

### Integrity §164.312(c) — Partial
- SHA-256 present: upload `checksum_sha256` (`models/database.py:59`), import file checksum (`models.py:294`), connector `hash_payload` (`connectors.py:164-171`), `evidence_hash` column (`models.py:237`).
- **Gaps:** `evidence_hash` is written **`None`** by the internal engine (`verification.py:336,346`) — column exists, unpopulated; **audit records have no hash-chain / tamper-evidence** and are **actively mutable** — `compliance.py:129-134` deletes audit rows, `admin_users.py:433` updates them (Part 8 AUDIT-MUT). Immutability is convention-only and violated. **Partial → weak.**

### Authentication §164.312(d) — Compliant
JWT HS256 (`security.py:112-116`), bcrypt verify (`:55`), **account state enforced on every request** (`_enforce_account_state`, :118-127) incl. disabled/revoked; email-based auto-admin escalation removed (:142-145); SAML present-but-disabled. **Compliant** (see Part 8 AUTH-03 for the separate Entra id_token-signature caveat). ✅

### Transmission Security §164.312(e) — Gap
- **Outbound TLS:** all connectors HTTPS (`connectors.py:243,350,459,558,638`); AI over HTTPS (`ai_engine.py:469`). ✅
- **Inbound:** only `TrustedHostMiddleware` — **no `HTTPSRedirectMiddleware`/`X-Forwarded-Proto` enforcement in app** (HSTS header *is* set; App Service enforces `httpsOnly` at platform → defense-in-depth exists, but not an in-app enforcement layer).
- **DB:** both `create_async_engine` calls pass **no `ssl`/`sslmode`** (`core/database.py:32`, `app/database.py:6`) — TLS to Postgres relies on the connection string, not pinned in code (Part 8 CRYPTO-DBTLS). **PHI-to-DB traffic could be cleartext if the string omits `sslmode`.** **Gap.**

## HIPAA verdict
**3 of 5 safeguards Partial, 1 Compliant, 1 Gap.** Authentication is solid. The safeguard-level blockers for a HIPAA production posture are: **(1) close the unauthenticated PHI module** (Access Control), **(2) make the audit log immutable + log PHI reads** (Audit + Integrity), and **(3) pin DB TLS + add an inbound-HTTPS enforcement layer** (Transmission). All three are Part-8 findings with concrete fixes. **A BAA with Anthropic is a prerequisite** before any PHI egress (see `phi_data_flow.md`).

## NIST 800-53 / 800-66 cross-map
AC-3/AC-6/AC-7 · AU-2/AU-3/AU-9/AU-12 · SC-8/SC-13/SC-28 · IA-2/IA-5 · SI-7.
