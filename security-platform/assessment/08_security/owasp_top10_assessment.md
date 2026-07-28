# OWASP Top 10 (2021) Assessment

> Application-level assessment from manual code review of the **live** `app.main:app` surface. Read-only.

| OWASP | Category | Risk | Key findings |
|---|---|:--:|---|
| **A01** | Broken Access Control | **Critical** | **Unauthenticated Case Management PHI router** (AUTHZ-01, `case_management/routes.py:34`, 12 endpoints); **IDOR** on healthcare-claims appeal/fwa/revenue/validate (AUTHZ-02). Rest of app is well-gated (RBAC ladder, `allowed_modules`, per-user filtering). |
| **A02** | Cryptographic Failures | **Medium** | Live API keys in working-tree `.env` (SEC-01, not in git); DB TLS not pinned in code (crypto CWE-319); Entra id_token unverified (AUTH-03). Core crypto (bcrypt, pinned HS256, TLS verify) is sound. |
| **A03** | Injection | **Low** | **None found** — ORM-parameterized SQL, list-arg subprocess (no shell), UUID upload paths, no eval/exec/pickle. Strongest category. |
| **A04** | Insecure Design | **High** | **Unauthenticated + unmasked PHI → Anthropic** from case-management engines (DP-02); PHI in query strings (DP-03); no read-time role masking (DP-05); dual auth stacks (dead + live). |
| **A05** | Security Misconfiguration | **Medium** | No CSP/HSTS on the SWA frontend (SH-03/04); in-memory rate-limit/lockout (SH-01); **infra**: public Key Vault + public Postgres, no App Service IP restrictions, private endpoint authored-not-deployed (Part 9). Backend header posture is strong. |
| **A06** | Vulnerable & Outdated Components | **Low-Medium** | Dependabot + `pip-audit` + `npm audit` + CodeQL in CI (Part 9) — active dependency governance. Residual: scans are report-only (`|| true`); no runtime SCA gate on deploy; dead `@tanstack/react-table`. No pinned-vulnerable package surfaced in this pass. |
| **A07** | Identification & Auth Failures | **Medium** | Entra id_token signature **not verified** + no nonce binding (AUTH-03); in-memory lockout bypassable across workers (AUTH-02); admin token 24h (AUTH-01). Core auth (bcrypt, refresh rotation, revocation epoch, timing-attack mitigation) is strong. |
| **A08** | Software & Data Integrity Failures | **Medium** | Audit records are **mutable** — `compliance.py:129-134` deletes and `admin_users.py:433` updates `audit_logs` (Part 10 §23); `evidence_hash` column exists but written `None`; no hash-chain/WORM. File integrity (SHA-256 on upload) is good. No insecure deserialization. |
| **A09** | Security Logging & Monitoring Failures | **Medium** | **PHI *read* access not logged** (Part 10 §22); email/NPI in some logs (DP-01); App Insights **not code-instrumented**, logs stdout with 3-day HTTP retention, single-recipient email alerts (Part 9). Auth events, 403s, and AI calls **are** logged. |
| **A10** | Server-Side Request Forgery | **Low** | Connectors target **fixed** government URLs; user input is query params, not host. No user-supplied fetch-by-URL. Forward-looking caution only. |

## Overall OWASP posture
- **1 Critical** (A01), **1 High** (A04), **6 Medium** (A02/A05/A07/A08/A09 + A06 low-medium), **2 Low** (A03/A10).
- The profile is unusual and instructive: **the classic technical categories (injection, crypto primitives, headers, file upload) are genuinely strong** — this is a security-aware codebase. The risk is concentrated in **one module** (`case_management`, unauthenticated, shipped alongside otherwise well-gated code) and in **design/governance** (PHI egress minimization, audit immutability, read-audit, observability). Fixing the single A01/A04 case-management gap plus audit-immutability and DB-TLS would move the whole profile from "Critical" to "Medium" quickly.

## NIST 800-53 cross-map (headline)
AC-3/AC-6 (A01) · SC-12/SC-28/SC-8 (A02) · SI-10 (A03) · SA-8/SA-17 (A04) · CM-6/CM-7 (A05) · SA-22/SI-2 (A06) · IA-2/AC-7 (A07) · AU-9/SI-7 (A08) · AU-2/AU-3/AU-12/SI-4 (A09) · SC-7 (A10).
