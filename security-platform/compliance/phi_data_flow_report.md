# PHI / PII Data Flow Report

**Project:** DocuAction  
**Scan:** `docuaction_20260728T003746`  
**Findings analysed:** 309  
**Generated:** 2026-07-28T01:18:14+00:00

> **How to read this.** A control is marked **GAP** only where a finding demonstrates a deficiency, and **EVIDENCED** only where a passing test or an observed configuration supports it. Everything else is **NOT ASSESSED** - an automated scan that finds nothing is not evidence that a control is satisfied. This document supports an assessment; it is not a certification.


## Where PHI enters

| Entry point | Control | Status |
|---|---|---|
| `POST /api/v1/case-management/*` | Router-level auth (Sprint 1 AUTHZ-01) | **Verified 403 anonymously on dev** |
| FHIR Bundle / CSV import (`tefca_registry`) | reviewer+ RBAC | Not testable - registry not deployed |
| Document upload | magic-byte + macro/PE scan | Heuristic only (Phase 0 FU-02) |

## Where PHI is stored

- PostgreSQL flexible servers (`docuaction-db`, `-geo`). Encrypted at rest by Azure platform.
- **All three servers accept public network access** (AZ-DB-006, HIGH) - the largest infrastructure exposure for stored PHI.
- `cm_*` case-management tables are **not deployed**; Phase 0 established the exposure is PHI ingress/egress, not PHI at rest.

## Where PHI is transmitted

- Client to API: TLS 1.2 floor, HTTPS-only enforced (AZ-APP-001/002 PASS).
- API to database: TLS required at the server; but over a public endpoint because the App Service is not VNet-integrated (AZ-NET-007).

## Where PHI is logged (it should not be)

8 finding(s) relate to logging of sensitive values:

- `AGT-PHI-001` [high] Potential PHI written to application logs without masking (backend/app/Tefca/connectors.py:300)
- `AGT-PHI-001` [high] Potential PHI written to application logs without masking (backend/app/Tefca/connectors.py:611)
- `AGT-AUTHZ-001` [high] Endpoint 'GET /methodology' has no authentication dependency (backend/app/Tefca/routes.py:1623)
- `TEFCA-AUD-003b` [medium] Audit log has tamper-evidence (hash chain) (STATIC app/services/audit.py)
- `AZ-DB-009-docuaction-db-dev` [medium] [dev] Database audit logging (AZ-READ docuaction-db-dev)
- `AZ-DB-009-docuaction-db-geo` [medium] [prod] Database audit logging (AZ-READ docuaction-db-geo)
- `AZ-DB-009-docuaction-db` [medium] [prod] Database audit logging (AZ-READ docuaction-db)
- `AUTH-001` [low] Valid login -> 200 + token (POST /api/auth/login)

Phase 0 DP-01 named `password_reset.py:182` and `connectors.py:300,611`; the Phase 1 custom rule AGT-PHI-001 independently reproduced `connectors.py:300`.

## Where PHI exits to external APIs

| Destination | Data | De-identification | Residual risk |
|---|---|---|---|
| Anthropic API | Clinical narrative + structured fields | Direct identifiers stripped at the `_call_claude` chokepoint (Sprint 1 DP-02, 11 of 12 egress sites) | **Narrative is still PHI. Closable only by a signed BAA + zero retention.** |
| NPPES / PECOS / LEIE | NPI, organisation name | Not PHI (provider data) | Low |
| SendGrid | Recipient email | Not clinical PHI | Medium - no BAA evidence |

## De-identification status (Sprint 1)

`phi_deidentify.py` performs exact-value replacement of name / MRN / DOB / SSN / phone before AI egress, verified by intercepting real outbound payloads. Known and accepted limitations:

- `generate_government_case_document` takes `case_facts`, not `patient_context`, and is the 1 of 12 sites not covered.
- Over-redaction is possible (a patient surnamed *Stone* turns "kidney stone" into "kidney [PATIENT_LAST]"). Accepted: over-redaction is visible at the `requires_review` gate; a leak is not.
- The clinical narrative itself is not redacted and remains PHI.

## PHI-tagged findings: 197

- `openai-api-key` [critical] OpenAI API key in .env
- `B324` [high] B324: Use of weak MD5 hash for security. Consider usedforsecurity=False
- `AGT-AUTHZ-001` [high] Endpoint 'GET /saml/config' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /domains' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /projects' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /projects/{project_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /status' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /info' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'POST /api/auth/verify-email' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /residency' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /status' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /download/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /download-options/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /download-excel/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /briefings/{briefing_id}/excel' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /costs' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /profiles' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /coverage/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /agencies' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /agencies/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /admin/last-window/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /latest/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /today/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /queue/{agency_id}' has no authentication dependency
- `AGT-AUTHZ-001` [high] Endpoint 'GET /history/{agency_id}' has no authentication dependency