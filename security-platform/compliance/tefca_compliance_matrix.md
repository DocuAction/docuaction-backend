# TEFCA Compliance Matrix

**Project:** DocuAction  
**Scan:** `docuaction_20260728T003746`  
**Findings analysed:** 309  
**Generated:** 2026-07-28T01:18:14+00:00

> **How to read this.** A control is marked **GAP** only where a finding demonstrates a deficiency, and **EVIDENCED** only where a passing test or an observed configuration supports it. Everything else is **NOT ASSESSED** - an automated scan that finds nothing is not evidence that a control is satisfied. This document supports an assessment; it is not a certification.


Mapped against Common Agreement obligations, RCE Implementation Guide v1.14.0, and the Security & Privacy Principles.

| Ref | Requirement | Status | Evidence | Owner / next step |
|---|---|---|---|---|
| CA-1 | Identity proofing | **NOT ASSESSED** | No IAL2 identity-proofing flow exists in the application; signup is self-service email/password. | Organisational + engineering |
| CA-2 | Authentication | **EVIDENCED (partial)** | JWT with pinned HS256, bcrypt password hashing, Entra SSO route. All 13 JWT forgery attacks rejected (Phase 2A). | No MFA on the local password path. |
| CA-3 | Authorization | **GAP** | RBAC exists, but Phase 1 found 72 endpoints with no auth dependency and Phase 2 found no allowed-transition map (TEFCA-WF-002). | Sprint 2 |
| CA-4 | Audit logging | **EVIDENCED (partial)** | audit_logs table with actor/action/timestamp; pseudonymisation on delete. No hash chain (TEFCA-AUD-003b); pgaudit off at the database tier. | Sprint 2 |
| CA-5 | Encryption | **EVIDENCED** | TLS 1.2 floor and HTTPS-only on both App Services; Key Vault for secrets; database TLS required. | DB public network access still enabled. |
| CA-6 | Breach notification | **NOT ASSESSED** | Procedural control; Azure alerting exists but no documented notification workflow was provided. | Organisational |
| CA-7 | Minimum necessary | **GAP** | Phase 0 DP-05: no role-based PHI masking on read responses; full clinical narrative is sent to the AI provider. | Sprint 2 + BAA |
| IG-1 | FHIR R4 compliance | **PARTIAL** | FHIR import exists (fhir_import.py) and canonical NPI system URIs are used. Resource-level validation not exercised - registry not deployed to a test target. | Deploy registry to dev to test |
| IG-2 | Organization hierarchy (QHIN/Participant/Sub) | **PARTIAL** | A parent reference is modelled, but hierarchy tier rules and circular-reference prevention are untested (TEFCA-ENT-008/009). | Deploy registry to dev |
| IG-3 | Mandatory identifiers (TEFCAID + HCID) | **GAP** | FHIR-ID-006: identifiers are not enforced non-nullable in the schema. | Sprint 2 |
| IG-4 | NPI handling | **GAP** | FHIR-ID-002: no check-digit validation anywhere; 6 of 8 bundled sample NPIs are themselves invalid (FHIR-ID-002b). | Sprint 2 |
| IG-5 | Directory participation | **NOT ASSESSED** | Requires a live registry and RCE connectivity. | Deploy + RCE key |
| IG-6 | Endpoint management | **NOT ASSESSED** | tefca_entity_endpoints table exists; behaviour untested. | Deploy registry |
| SP-1 | Purpose limitation | **NOT ASSESSED** | Policy control. | Organisational |
| SP-2 | Data minimization | **GAP** | Full clinical narrative egresses to the AI provider (DP-02). | BAA + redesign |
| SP-3 | Individual access | **NOT ASSESSED** | Policy/feature. | Organisational |
| SP-4 | Correction | **NOT ASSESSED** | Policy/feature. | Organisational |
| SP-5 | Disclosure limitation | **PARTIAL** | Direct identifiers are stripped at the AI egress chokepoint (Sprint 1 DP-02); narrative is not. | BAA |
| SP-6 | Safeguards | **EVIDENCED (partial)** | 309 findings across SAST/DAST/infra; TLS, Key Vault, MI, RBAC in place. | See remediation roadmap |
| SP-7 | Accountability | **EVIDENCED (partial)** | Audit trail + this assessment programme. | Hash chain outstanding |

## Summary

- NOT: 7
- EVIDENCED: 5
- GAP: 5
- PARTIAL: 3

**Principal constraint:** the TEFCA registry is not deployed to any test environment, so roughly half of the Implementation Guide rows cannot be exercised. They are marked NOT ASSESSED rather than assumed compliant.
