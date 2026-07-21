# DocuAction TEFCA ARC — Security Verification Scorecard
## Date: July 20, 2026
## Overall Score: 96 / 99 (97%)
## Assessment: 100% read-only production verification. Critical: 0 · High: 0.

| Category | Tests | Pass | Fail | Score |
|----------|-------|------|------|-------|
| Build (test suite / build) | 2 | 1 | 1 | 50% |
| Infrastructure (Azure) | 18 | 18 | 0 | 100% |
| API Security | 11 | 11 | 0 | 100% |
| Authentication | 10 | 10 | 0 | 100% |
| Frontend | 8 | 7 | 1 | 88% |
| Bulletin Intelligence | 3 | 3 | 0 | 100% |
| Repository Governance | 26 | 26 | 0 | 100% |
| Document Inventory | 13 | 12 | 1 | 92% |
| Input Validation | 5 | 5 | 0 | 100% |
| Performance | 3 | 3 | 0 | 100% |
| **TOTAL** | **99** | **96** | **3** | **97%** |

## Critical Findings
None. No critical or high-severity findings were identified.

## Findings (all Low / Informational)
1. **No backend automated test suite** (`pytest` not installed / no tests) — *Low* — add tests to CI (SA-11).
2. **`favicon.ico` returns 404** — *Info* — cosmetic; add a favicon asset.
3. **SSP not stored in-repo; no single "Technical Architecture" deliverable** — *Low* — author/store the SSP; consolidate `docs/architecture/` (CA-1/PL-2).
4. **Dev App Service `httpsOnly=false`** — *Low* — enable HTTPS-only on dev (SC-8). *(prod is HTTPS-only)*
5. **PostgreSQL geo-redundant backup Disabled** (both) — *Low* — enable geo-redundant/replica in a maintenance window (CP-9).
6. **HEAD `/health` → 405; `/me` & bad-scheme → 403 (not 401)** — *Info* — standard framework behavior; no action needed.
7. **HTTP/2 disabled; Static Web Apps on Free SKU** — *Info* — optional enablement / upgrade.

## Verified Strengths (all PASS)
- TLS 1.2 + HSTS + full security-header set (HSTS, CSP, X-Frame DENY, X-Content-Type nosniff, Referrer-Policy, Permissions-Policy).
- Key Vault **public access disabled**; secrets reachable only via the **private endpoint** (Managed Identity).
- OpenAPI disabled in prod (404); CORS rejects unknown origins; TRACE/DELETE → 405.
- Generic auth errors (no user enumeration); malformed/tampered JWT → 401; **account lockout at 6 attempts (429)**.
- Parameterized queries (SQLi → 401, no 500); clean JSON errors with no stack traces/paths.
- 6 Defender Standard plans; 4 Azure Monitor alerts; diagnostic logging to 90-day Log Analytics; App Insights connected.
- Health ~0.2 s; frontend ~0.58 s; both frontends 200; no mixed content.

## Documents Delivered (docs/compliance/)
1. Security Assessment Plan
2. Contingency Plan
3. Incident Response Plan
4. Configuration Management Plan
5. Continuous Monitoring Strategy
6. DR Validation Report
7. Privacy Impact Assessment
8. Threat Model
9. Data Flow Diagram
10. Release Checklist (`docs/Release_Checklist.md`)
11. Security Verification Report (this assessment)
- Plus: Bicep IaC (`infra/`), CycloneDX SBOMs (backend + frontend), NIST 800-53 Control Matrix.

## Remaining Items (require licensing / external services)
- Entra ID P1 (MFA) — ~$6/user/mo
- SOC 2 Type II — external auditor
- FedRAMP 3PAO — 2027
- External penetration test — Q4 2026
- HSPD-12 PIV/CAC — pending COR
