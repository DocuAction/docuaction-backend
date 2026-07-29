# Security Findings Register (sortable)

> All findings from the manual code review, most-severe first. IDs, severity, file:line, OWASP, CWE, NIST control, remediation, effort. Read-only — no code modified.

## Severity tally
> **Sprint 1 progress (2026-07-26):** AUTHZ-01 (Critical) remediated — see
> `remediation/AUTHZ-01_remediation.md`. DP-02 (High) partially remediated — direct
> identifiers masked; clinical-narrative egress still requires a BAA — see
> `backend/docs/compliance/AI_EGRESS_PHI.md`. Open counts below are unchanged pending merge approval.
>
> **Two register rows have been found inaccurate on verification** (AUTHZ-01 endpoint count and
> PHI-at-rest claim; DP-02's prescribed `mask_pii` remediation). Verify each remaining row against
> current code before implementing its stated fix.

| Severity | Count |
|---|:--:|
| **Critical** | 1 (1 remediated, pending merge) |
| **High** | 2 (1 partially remediated, pending merge) |
| **Medium** | 6 |
| **Low** | 7 |
| Info / GOOD | 11 |

## Findings

| ID | Sev | Title | File:line | OWASP | CWE | NIST | Remediation | Effort |
|---|:--:|---|---|:--:|:--:|:--:|---|:--:|
| ~~**AUTHZ-01**~~ | **Critical** | Entire Case Management PHI router unauthenticated (**22** endpoints, not 12; live via `main.py:321`). Exposure is unauthenticated PHI **ingress → Anthropic egress**, not PHI at rest — the `cm_*` tables are not deployed and all GETs return stubs | `case_management/routes.py:34-37`, endpoints :132–:711 | A01 | 306 | AC-3 | **REMEDIATED 2026-07-26** — router-level `dependencies=[Depends(get_current_user)]` on `cm_router` + `Authorization` header added to the 3 frontend calls. Verified: all 22 routes gated (403 anon / 401 bad token / 200 authenticated), `/health` 200, 92 TEFCA routes intact. Branch `sprint1/authz-01-case-management-auth` (both repos). See `remediation/AUTHZ-01_remediation.md`. **Still open:** module gate (`allowed_modules` is never enforced server-side) and role tiering — tracked as follow-ups | 0.5–1d |
| **DP-02** | **High** | Unauthenticated + unmasked PHI sent to Anthropic | `case_management/services/ccm_engine.py:25,164`; `discharge_engine.py:19,33`; `ai_engine.py:251`+`pii_masking.py:15-49` | A04/A01 | 200 | AC-4/SC-8 | **PARTIALLY FIXED 2026-07-26** — auth done via AUTHZ-01; direct identifiers (name/MRN/DOB/SSN/phone) now stripped by exact-value replacement at the `_call_claude` chokepoint in both engines (11/12 egress sites; new `phi_deidentify.py`), verified by intercepting real outbound payloads. **Correction: the prescribed `mask_pii` fix is a no-op here** — it redacts **0 items** from the actual prompt (no name pattern at all; DOB pattern misses bare ISO dates), so do not re-attempt as written. **STILL OPEN:** clinical narrative is still sent in full and is PHI — closable only by a signed BAA + zero-retention (contractual; there is **no** per-request zero-retention header — it is an org-level setting). See `backend/docs/compliance/AI_EGRESS_PHI.md` | 1–2d + BAA |
| **SEC-01** | **High** | Live Anthropic/OpenAI API keys in working-tree `.env` (gitignored, not in history) | `.env:2-5` | A02 | 798 | SC-12/IA-5 | **CODE HARDENED 2026-07-26; ROTATION STILL OPEN + MANUAL.** Verified live against Azure: `.env` **never committed on any ref** (full object scan → only `.env.example`), so **no history rewrite needed**. Scope corrected: **~18 secret-bearing vars, only 4 Key Vault backed** (SECRET_KEY, ANTHROPIC_API_KEY, SENDGRID_API_KEY, AZURE_AD_CLIENT_SECRET — all `Resolved` via system-assigned MI; vault public access disabled). `DATABASE_URL` still plaintext app setting in prod **and** dev; **dev is entirely un-vaulted**; `OPENAI_API_KEY` exists **only** in local `.env`. `JWT_SECRET` **does not exist** (JWTs use SECRET_KEY) — strike that row. **New latent defect found + fixed:** an unresolved `@Microsoft.KeyVault(...)` reference is passed through literally by App Service and its 71-char length **passed** the 64-char SECRET_KEY floor → app would boot signing JWTs with a publicly derivable constant. Guard added in `core/config.py` (fail-fast on required, log on optional). See `backend/docs/compliance/SECRETS_MANAGEMENT.md` §4 for the rotation checklist | 0.5d+rotate |
| **AUTH-03** | **Medium** | Entra id_token signature not verified (no JWKS, no nonce) | `azure_auth_routes.py:182` | A07 | 347 | IA-2 | Validate id_token vs Microsoft JWKS; enforce nonce/iss/exp | 1–2d |
| **AUTHZ-02** | **Medium** | IDOR — no ownership check on healthcare-claims handlers | `healthcare_claims_routes.py:171,212,233,243` | A01 | 639 | AC-3 | Apply `claim.user_id == user.id or admin` (as in `get_claim`) | 0.5d |
| **DP-03** | **Medium** | PHI in URL query strings | `healthcare_claims_routes.py:100` | A09 | 598 | SC-8 | Move PHI to request bodies | 0.5d |
| **CRYPTO-DBTLS** | **Medium** | DB TLS not pinned in code (relies on connection-string sslmode) | `core/database.py:32`; `app/database.py:6` | A02 | 319 | SC-8 | Pin `ssl` in `connect_args` on both engines | 0.5d |
| **AUDIT-MUT** | **Medium** | `audit_logs` mutable — deleted/updated by admin/compliance flows; no WORM/hash-chain | `compliance.py:129-134`; `admin_users.py:433` | A08 | 778 | AU-9 | **APP LAYER FIXED 2026-07-26; hash-chain + WORM STILL OPEN.** Verification splits the row: the **DELETE** path is in `compliance.py`, which is **NOT MOUNTED** (`/api/user/hard-delete` → 404) — dead code, so live exposure was loss of *attribution*, not loss of *records*. The **UPDATE** path is live but preserves the row and is **structurally required** — `audit_logs_user_id_fkey` is `NO ACTION`, proven by `ForeignKeyViolationError` on user delete. Fixes: GDPR flow now **pseudonymises** (retain row, null `user_id`/`ip_address`, redact personal keys in `details`) per HIPAA §164.316(b)(2) six-year retention + GDPR Art. 17(3)(b) legal-obligation exemption; response contract corrected (it falsely promised audit deletion); admin path records `audit_rows_detached`. **DB triggers assessed and DEFERRED with reasons** — a trigger would have broken the live admin path via the `NO ACTION` FK, and is self-droppable by the table-owning app role. Next: hash-chain for detection, then privilege separation/external sink for prevention. See `backend/docs/compliance/AUDIT_LOG_INTEGRITY.md` | 2–3d |
| **AUDIT-READ** | **Medium** | PHI *read* access not logged | registry GETs; `documents` GETs (no audit call) | A09 | 778 | AU-2/AU-12 | Log PHI reads (who/what/when) | 2–3d |
| **SH-03** | **Medium** | No CSP on the SWA frontend | `frontend/public/staticwebapp.config.json` | A05 | 1021 | SC-18 | Add CSP global header | 0.5d |
| **AUTH-01** | Low | Admin access token TTL = 24h | `core/security.py:25` | A07 | 613 | AC-12 | Reduce to ≤1h; rely on refresh | trivial |
| **AUTH-02** | Low | Account lockout in-memory / per-process | `api/routes.py:90-160` | A07 | 307 | AC-7 | Back with Redis | 1–2d |
| **SH-01** | Low | Rate limiting in-memory / per-process | `core/rate_limiter.py:75` | A05 | 770 | SC-5 | Back with Redis | (w/ AUTH-02) |
| **AUTHZ-03** | Low | Untyped-dict admin user update | `admin_users.py:349-388` | A01 | 915 | AC-3 | Typed Pydantic model | 0.5d |
| **DP-01** | Low | Email/NPI in some info logs | `password_reset.py:182`; `connectors.py:300,611` | A09 | 532 | AU-3 | Mask/omit email+NPI in logs | 0.5d |
| **SEC-03** | Low | `DATABASE_URL` as direct credential (not vaulted) | `infra/appService.bicep` | A05 | 798 | SC-12 | Vault DATABASE_URL | 0.5d |
| **DP-05** | Low | No role-based PHI masking on read responses | `pii_masking.py` (egress-only) | A04 | 200 | AC-4 | Role-aware field masking on reads | 2–3d |
| **SH-04** | Low | No HSTS on SWA frontend | `staticwebapp.config.json` | A05 | 319 | SC-8 | Add HSTS header | trivial |
| **FU-02** | Low | No true anti-malware (heuristic scanner only) | `file_scanner.py` | A04 | 434 | SI-3 | Optional async ClamAV on high-risk intake | 2–3d |

## GOOD / Info (positive controls — documented for the ATO evidence base)
| Area | Evidence |
|---|---|
| SQL injection | ORM-parameterized; safe `text()`; no string-built SQL (`injection_review.md`) |
| Command injection | list-arg subprocess, no shell; no eval/exec/pickle |
| Path traversal | UUID storage + commonpath containment (`upload_security.py`) |
| Password hashing | bcrypt + per-hash salt (`core/security.py:52-56`) |
| JWT | HS256 pinned on decode; refresh rotation; revocation epoch |
| Login hardening | timing-attack mitigation; account lockout; IP throttle |
| Randomness | `secrets` module for all security tokens |
| TLS | no `verify=False`; all outbound HTTPS |
| Error handling | generic bodies; stack traces internal-only |
| Security headers | HSTS/nosniff/DENY/CSP/strict CORS/TrustedHost (backend) |
| File upload | magic-byte + macro/PE/ELF/shebang scan + SHA-256 + generic reject |

## Security score: **6.0 / 10**
A **security-aware codebase** (strong injection/crypto/header/upload posture, mature auth) carrying **one Critical** (unauthenticated PHI module) that, with the PHI-egress High and the audit-immutability/observability Mediums, holds the score at 6.0. Remediating AUTHZ-01 + DP-02 + SEC-01 alone would lift it toward ~7.5–8.
