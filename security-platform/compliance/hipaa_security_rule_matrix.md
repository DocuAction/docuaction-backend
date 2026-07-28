# HIPAA Security Rule - Control Matrix

**Project:** DocuAction  
**Scan:** `docuaction_20260728T003746`  
**Findings analysed:** 309  
**Generated:** 2026-07-28T01:18:14+00:00

> **How to read this.** A control is marked **GAP** only where a finding demonstrates a deficiency, and **EVIDENCED** only where a passing test or an observed configuration supports it. Everything else is **NOT ASSESSED** - an automated scan that finds nothing is not evidence that a control is satisfied. This document supports an assessment; it is not a certification.


## §164.312 Technical Safeguards

| Section | Requirement | Type | Status | Findings | Evidence / Gap |
|---|---|---|---|---|---|
| 164.312(a)(1) | Access Control | Required | **GAP** | 143 | 143 finding(s): H75 M5 L63 |
| 164.312(a)(2)(i) | Unique User Identification | Required | **GAP** | 3 | 3 finding(s): C1 H2 |
| 164.312(a)(2)(ii) | Emergency Access Procedure | Required | NOT ASSESSED | 0 | No automated finding maps here - manual assessment required |
| 164.312(a)(2)(iii) | Automatic Logoff | Addressable | NOT ASSESSED | 0 | No automated finding maps here - manual assessment required |
| 164.312(a)(2)(iv) | Encryption and Decryption | Addressable | NOT ASSESSED | 0 | No automated finding maps here - manual assessment required |
| 164.312(b) | Audit Controls | Required | **GAP** | 8 | 8 finding(s): H2 M6 |
| 164.312(c)(1) | Integrity | Required | **GAP** | 4 | 4 finding(s): M3 L1 |
| 164.312(c)(2) | Mechanism to Authenticate ePHI | Addressable | NOT ASSESSED | 0 | No automated finding maps here - manual assessment required |
| 164.312(d) | Person or Entity Authentication | Required | **GAP** | 72 | 72 finding(s): H72 |
| 164.312(e)(1) | Transmission Security | Required | **GAP** | 4 | 4 finding(s): H3 L1 |
| 164.312(e)(2)(i) | Integrity Controls | Addressable | NOT ASSESSED | 0 | No automated finding maps here - manual assessment required |
| 164.312(e)(2)(ii) | Encryption | Addressable | **GAP** | 3 | 3 finding(s): C1 H2 |

### Evidence detail by safeguard

**§164.312(a)(1) Access Control** - 143 finding(s)

- `B324` [high] B324: Use of weak MD5 hash for security. Consider usedforsecurity=False (backend/app/bulletin_intelligence/engine.py:216)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /saml/config' has no authentication dependency (backend/app/api/auth_endpoints.py:25)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /domains' has no authentication dependency (backend/app/api/meeting_routes.py:298)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /projects' has no authentication dependency (backend/app/api/migration_routes.py:102)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /projects/{project_id}' has no authentication dependency (backend/app/api/migration_routes.py:138)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /status' has no authentication dependency (backend/app/api/migration_routes.py:726)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /info' has no authentication dependency (backend/app/api/plans.py:33)
- `AGT-AUTHZ-001` [high] Endpoint 'POST /api/auth/verify-email' has no authentication dependency (backend/app/api/routes.py:284)

**§164.312(a)(2)(i) Unique User Identification** - 3 finding(s)

- `openai-api-key` [critical] OpenAI API key in .env (backend/.env:5)
- `generic-api-key` [high] Generic API key / high-entropy string in appService.bicep (backend/infra/modules/appService.bicep:140)
- `generic-api-key` [high] Generic API key / high-entropy string in FCC_Bulletin_Implementation_Specification.md (backend/docs/fcc-bulletin-review/FCC_Bulletin_Implementation_Specification.md:265)

**§164.312(b) Audit Controls** - 8 finding(s)

- `AGT-PHI-001` [high] Potential PHI written to application logs without masking (backend/app/Tefca/connectors.py:300)
- `AGT-PHI-001` [high] Potential PHI written to application logs without masking (backend/app/Tefca/connectors.py:611)
- `TEFCA-AUD-003b` [medium] Audit log has tamper-evidence (hash chain) (STATIC app/services/audit.py)
- `AZ-DB-009-docuaction-db-dev` [medium] [dev] Database audit logging (AZ-READ docuaction-db-dev)
- `AZ-DB-009-docuaction-db-geo` [medium] [prod] Database audit logging (AZ-READ docuaction-db-geo)
- `AZ-DB-009-docuaction-db` [medium] [prod] Database audit logging (AZ-READ docuaction-db)
- `AZ-MON-007` [medium] Diagnostic settings on PostgreSQL (AZ-READ PostgreSQL)
- `AZ-MON-008` [medium] Diagnostic settings on Key Vault (AZ-READ Key Vault)

**§164.312(c)(1) Integrity** - 4 finding(s)

- `FHIR-ID-002` [medium] Backend validates the NPI check digit (Luhn/80840) (STATIC app/tefca_registry/*, app/Tefca/*)
- `FHIR-ID-006` [medium] Mandatory TEFCA identifiers are non-nullable (STATIC app/tefca_registry/schemas.py)
- `TEFCA-AUD-003b` [medium] Audit log has tamper-evidence (hash chain) (STATIC app/services/audit.py)
- `FHIR-ID-002b` [low] Bundled TEFCA sample/mock NPIs carry valid check digits (STATIC app/Tefca/mock_data.py)

**§164.312(d) Person or Entity Authentication** - 72 finding(s)

- `AGT-AUTHZ-001` [high] Endpoint 'GET /saml/config' has no authentication dependency (backend/app/api/auth_endpoints.py:25)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /domains' has no authentication dependency (backend/app/api/meeting_routes.py:298)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /projects' has no authentication dependency (backend/app/api/migration_routes.py:102)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /projects/{project_id}' has no authentication dependency (backend/app/api/migration_routes.py:138)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /status' has no authentication dependency (backend/app/api/migration_routes.py:726)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /info' has no authentication dependency (backend/app/api/plans.py:33)
- `AGT-AUTHZ-001` [high] Endpoint 'POST /api/auth/verify-email' has no authentication dependency (backend/app/api/routes.py:284)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /residency' has no authentication dependency (backend/app/api/security.py:52)

**§164.312(e)(1) Transmission Security** - 4 finding(s)

- `AZ-DB-006-docuaction-db-dev` [high] [dev] Public network access (AZ-READ docuaction-db-dev)
- `AZ-DB-006-docuaction-db-geo` [high] [prod] Public network access (AZ-READ docuaction-db-geo)
- `AZ-DB-006-docuaction-db` [high] [prod] Public network access (AZ-READ docuaction-db)
- `AZ-KV-005-dev` [low] [dev] Private endpoint configured (AZ-READ docuaction-kv-dev)

**§164.312(e)(2)(ii) Encryption** - 3 finding(s)

- `openai-api-key` [critical] OpenAI API key in .env (backend/.env:5)
- `generic-api-key` [high] Generic API key / high-entropy string in appService.bicep (backend/infra/modules/appService.bicep:140)
- `generic-api-key` [high] Generic API key / high-entropy string in FCC_Bulletin_Implementation_Specification.md (backend/docs/fcc-bulletin-review/FCC_Bulletin_Implementation_Specification.md:265)

## §164.308 Administrative and §164.310 Physical Safeguards

These are largely NOT machine-verifiable. Recorded so the gaps are explicit rather than absent.

| Section | Safeguard | Type | Assessment |
|---|---|---|---|
| 164.308(a)(1) | Security Management Process | Required | PARTIAL - this platform IS the risk-analysis capability (§164.308(a)(1)(ii)(A)). Risk management, sanction policy and information-system activity review remain organisational. |
| 164.308(a)(2) | Assigned Security Responsibility | Required | NOT ASSESSABLE by scanner - name a Security Official. |
| 164.308(a)(3) | Workforce Security | Required | PARTIAL - RBAC roles exist in code; authorisation/clearance/termination procedures are organisational. |
| 164.308(a)(4) | Information Access Management | Required | PARTIAL - role-based access is implemented and tested (AUTHZ suite); the access authorisation POLICY is organisational. |
| 164.308(a)(5) | Security Awareness and Training | Addressable | GAP - no evidence. Training records are organisational and none were provided. |
| 164.308(a)(6) | Security Incident Procedures | Required | PARTIAL - Azure alerts + action group exist (AZ-MON-004/005); a documented incident response plan was not provided. |
| 164.308(a)(7) | Contingency Plan | Required | PARTIAL - database backups exist with retention; geo-redundancy is disabled on one prod server and there is no tested DR plan or HA. |
| 164.308(b)(1) | Business Associate Contracts | Required | **GAP - BLOCKING.** Clinical narrative is transmitted to Anthropic. Phase 0 DP-02 established this is closable only by a signed BAA plus zero-retention. No BAA evidence exists. This is the single largest HIPAA exposure and no code change resolves it. |
| 164.310 | Physical Safeguards | Required | INHERITED - workloads run in Azure; facility, workstation and device controls inherit from Microsoft's FedRAMP/HITRUST attestations. Obtain the current Azure SOC 2 / HITRUST report as evidence. |
| 164.316(b)(2) | Documentation Retention (6 years) | Required | PARTIAL - audit rows are retained and pseudonymised rather than deleted (Sprint 1 AUDIT-MUT). App Insights telemetry retention is far shorter, but telemetry is not the audit record. |

## Summary

- Technical safeguards with findings (**GAP**): **7 of 12**
- Technical safeguards with no automated finding (NOT ASSESSED): 5
- **Blocking organisational gap: no Business Associate Agreement evidence for the AI subprocessor (§164.308(b)(1)).**
