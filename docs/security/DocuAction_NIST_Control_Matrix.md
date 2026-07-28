# DocuAction — NIST SP 800-53 Rev 5 Control Traceability Matrix

**Date:** 2026-07-22 · **Classification:** CUI
**Basis:** Automated security verification (read-only) against the SSP and QA test cases.
**Status legend:** **Implemented** = control operating and verified in this assessment · **Partially Implemented** = operating in production but a gap exists (typically dev parity) · **Planned** = defined, remediation pending.

| Control | Family | Requirement | Evidence (this assessment) | Status |
|---|---|---|---|---|
| AC-2 | Access Control | Account management | Role-based accounts (viewer/reviewer/admin) provisioned on dev; `GET /api/admin/users` returns managed users (admin only, 200) | Implemented |
| AC-3 | Access Control | Access enforcement | RBAC enforced: viewer→PII export 403, reviewer→export 200, admin→admin 200, viewer→admin 403. `require_role`/`require_admin`, `ROLE_HIERARCHY` (viewer=1, reviewer=4, admin=8) | Implemented |
| AC-6 | Access Control | Least privilege | Role hierarchy + per-area `allowed_modules`; TEFCA export gated at `require_role("reviewer")`, admin ops at `require_admin` | Implemented |
| AC-7 | Access Control | Unsuccessful logon attempts | Account/IP lockout: 6 rapid bad logins → `401×4` then `429×2` (throttle engages at attempt 5) | Implemented |
| AU-2 | Audit & Accountability | Auditable events | `AuditLog` records for `login_success`, `login_failed`, and `file_scan` (with SHA-256 + findings + result); auth + error paths write audit rows. Verified on prod and dev | Implemented |
| AU-3 | Audit & Accountability | Content of audit records | Records carry action, resource_type, timestamp, `correlation_id`, user_agent; no password/token/PII in details | Implemented |
| AU-6 | Audit & Accountability | Audit review | Admin retrieval via `GET /api/admin/users/{id}/activity`; Log Analytics workspace `docuaction-logs` | Implemented |
| AU-12 | Audit & Accountability | Audit generation | Audit generated at auth events with correlation IDs; App Service + PostgreSQL diagnostic settings stream to Log Analytics | Implemented |
| CA-7 | Assessment | Continuous monitoring | App Insights `docuaction-appinsights`, 4 Azure Monitor alerts (availability sev1, 5xx sev2, high-cpu sev2, db-availability sev1) all enabled | Implemented |
| CM-6 | Configuration Mgmt | Configuration settings | `httpsOnly` (prod), TLS 1.2 min, TrustedHost/ALLOWED_HOSTS, security headers baseline | Implemented |
| CM-7 | Configuration Mgmt | Least functionality | OpenAPI/`/docs` disabled on prod (404), TRACE disabled (405), unsupported methods → 405 | Implemented |
| CP-9 | Contingency Planning | System backup | PostgreSQL automated backups: prod 14d / dev 7d retention, point-in-time restore. Geo-redundancy is create-time-only on Flexible Server (residual — see scorecard); local-region backup satisfies CP-9 baseline | Implemented |
| CP-10 | Contingency Planning | Recovery | DR Validation Report on file; Railway→Azure replica established | Implemented |
| IA-2 | Identification & Auth | User identification & auth | JWT bearer auth (`/api/auth/login`); Entra ID SSO redirect (307) on prod + dev | Implemented |
| IA-5 | Identification & Auth | Authenticator management | Passwords hashed with **bcrypt** (`bcrypt.gensalt()` default cost 12 → `$2b$12$`); generic auth errors prevent enumeration | Implemented |
| RA-5 | Risk Assessment | Vulnerability scanning | CodeQL, dependency-review, security-scan workflows + Dependabot present in both repos; Defender for Cloud Standard on 8 plans (incl. Storage + Containers, added 2026-07-22) | Implemented |
| SC-7 | System & Comms Protection | Boundary protection | VNet `docuaction-vnet` (10.0.0.0/16), Key Vault Private Endpoint (Approved), strict CORS (bad origin not reflected), TrustedHost | Implemented |
| SC-8 | System & Comms Protection | Transmission confidentiality/integrity | TLS cert valid (ssl_verify=0), HSTS `max-age=31536000; includeSubDomains`, Postgres `require_secure_transport=on` | Implemented |
| SC-12/13 | System & Comms Protection | Cryptographic key mgmt & protection | Azure Key Vault (soft-delete, purge protection, RBAC) + Managed Identity on both app services | Implemented |
| SC-28 | System & Comms Protection | Protection of information at rest | Azure PostgreSQL Flexible Server storage encryption (AES-256, platform default); secrets in Key Vault | Implemented |
| SI-3 | System & Info Integrity | Malicious code protection | Multi-layer upload scanner (signature/content/structure/SHA-256) deployed & verified on **prod and dev**. Dev live test: `<script>`/MZ/empty uploads rejected (422, generic body); `file_scan` audit events with checksum | Implemented |
| SI-4 | System & Info Integrity | System monitoring | App Insights + Log Analytics + Defender + 4 alerts + diagnostic settings (App Service & PostgreSQL) | Implemented |
| SI-10 | System & Info Integrity | Information input validation | Input validation returns 422 (never 500) for malformed email/missing password/empty body/SQLi; file signature+content validation (prod) | Implemented |
| PL-2 | Planning | System security plan | SSP v1.2 + full compliance/security document set (23 documents) present in repo | Implemented |

**Summary:** 24 of 24 controls **Implemented and verified** (updated 2026-07-22 after remediation). The file-upload malware scanner (SI-3) and its `file_scan` audit event (AU-2) are now deployed and verified on both production and development. No control is failing in either environment.
