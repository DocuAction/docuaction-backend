# DocuAction TEFCA ARC — NIST SP 800-53 Rev 5 Control Traceability Matrix
## Date: July 20, 2026 · Classification: CUI
## Status legend: Implemented = control operating and verified in this assessment.

| Control | Family | Requirement | Evidence | Status |
|---------|--------|-------------|----------|--------|
| AC-2 | Access Control | Account Management | RBAC module; user lifecycle; admin approval; audit_logs account events | Implemented |
| AC-3 | Access Control | Access Enforcement | `require_role` middleware; 403 + `authorization_denied` audit on denial | Implemented |
| AC-6 | Access Control | Least Privilege | 8-level RBAC; per-module gating; no self-escalation | Implemented |
| AC-7 | Access Control | Unsuccessful Logon Attempts | Lockout (429 at 6th attempt, verified) + rate limiting | Implemented |
| AC-11 | Access Control | Session Lock | Short-lived access token | Implemented |
| AC-12 | Access Control | Session Termination | Server-side token revocation epoch (logout invalidates tokens) | Implemented |
| AC-17 | Access Control | Remote Access | HTTPS-only; TLS 1.2; no SSH/remote shell | Implemented |
| IA-2 | Identification & Authentication | User Authentication | JWT credential login + Microsoft Entra ID SSO (307 redirect verified) | Implemented |
| IA-4 | Identification & Authentication | Identifier Management | UUID primary keys; unique email | Implemented |
| IA-5 | Identification & Authentication | Authenticator Management | bcrypt password hashing; secrets in Key Vault | Implemented |
| IA-6 | Identification & Authentication | Authentication Feedback | Generic 401 "Invalid email or password" (no enumeration, verified) | Implemented |
| IA-11 | Identification & Authentication | Re-Authentication | Token rotation | Implemented |
| AU-2 | Audit & Accountability | Event Logging | Immutable `audit_logs` — login success/failure/lockout, logout, 403, role/account/CRUD, admin | Implemented |
| AU-3 | Audit & Accountability | Content of Audit Records | Timestamp, correlation ID, user ID, IP address, user agent | Implemented |
| AU-9 | Audit & Accountability | Protection of Audit Information | Append-only table; 90-day Log Analytics retention | Implemented |
| AU-12 | Audit & Accountability | Audit Record Generation | Generated automatically at each state change | Implemented |
| SC-7 | System & Communications Protection | Boundary Protection | Strict CORS + TrustedHost; Key Vault private endpoint (public access disabled) | Implemented |
| SC-8 | System & Communications Protection | Transmission Confidentiality/Integrity | TLS 1.2 + HSTS (`max-age=31536000; includeSubDomains`) | Implemented |
| SC-12 | System & Communications Protection | Cryptographic Key Establishment/Mgmt | Azure Key Vault (soft-delete + purge protection) + Managed Identity | Implemented |
| SC-13 | System & Communications Protection | Cryptographic Protection | SHA-256 evidence hashing; bcrypt; HS256 token signing | Implemented |
| SC-23 | System & Communications Protection | Session Authenticity | Token fingerprint / rotation | Implemented |
| SC-28 | System & Communications Protection | Protection of Information at Rest | Azure platform encryption (TDE) for PostgreSQL & Key Vault | Implemented |
| SI-4 | System & Information Integrity | System Monitoring | Microsoft Defender (6 Standard plans) + Application Insights + 4 Monitor alerts | Implemented |
| SI-10 | System & Information Integrity | Information Input Validation | Pydantic schema validation + ORM (422 on bad input, verified) | Implemented |
| SI-11 | System & Information Integrity | Error Handling | Generic JSON errors; no stack traces / file paths (verified via 404 & 5xx handling) | Implemented |

## Supporting controls (documented in the compliance package)
- **CP-9 / CP-10** — Contingency Plan + DR Validation Report (PITR 14-day; RTO 4h / RPO ~min). *Gap: geo-redundant backup disabled.*
- **CM-2 / CM-3 / CA-7** — Configuration Management Plan + Continuous Monitoring Strategy; GitHub is the configuration baseline; Bicep IaC in `infra/`.
- **RA-3 / RA-5** — Threat Model (STRIDE/800-30); Dependabot + pip-audit/npm audit + CodeQL + Dependency Review workflows.
- **IR-1..IR-8** — Incident Response Plan (HHSAR 352.239-72, 1-hour COR notification).
- **PL-2** — SSP referenced throughout. *Gap: SSP not stored as a file in-repo.*
- **AR / privacy** — Privacy Impact Assessment; Data Flow Diagram.

_All "Implemented" entries above were exercised during the July 20, 2026 read-only verification (96/99 checks passed)._
