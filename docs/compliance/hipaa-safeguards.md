# DocuAction AI — HIPAA Security Rule Safeguards Mapping

**Product:** DocuAction AI (Version 6.0.0) — Healthcare/TEFCA suite: DocuAction TEFCA ARC
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Compliance Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour acknowledgment SLA)

---

## 1. Purpose and Scope

This document maps DocuAction AI controls to the **HIPAA Security Rule** (45 CFR Part 164, Subpart C) safeguard categories: **Administrative**, **Physical**, and **Technical**. It addresses required and addressable implementation specifications relevant to a business-associate context where the Platform processes Protected Health Information (PHI) within TEFCA ARC workflows.

This mapping states control posture honestly and marks items **Implemented**, **Partial**, **Planned**, or **Inherited** (from Microsoft Azure). It does not assert a completed independent HIPAA audit or certification. A Business Associate Agreement (BAA) requirement is addressed in Section 5.

---

## 2. Administrative Safeguards (§164.308)

| Specification | DocuAction Control | Status |
|---|---|---|
| Security Management Process — Risk Analysis / Risk Management (§164.308(a)(1)) | Organizational security governance (AGT ISO 27001 / CMMI L3); documented security standards in `docs/security`; dependency/vulnerability monitoring. | Partial |
| Assigned Security Responsibility (§164.308(a)(2)) | Security responsibility centralized under AGT Security (security@agtbi.com). | Implemented |
| Workforce Security / Information Access Management (§164.308(a)(3)–(4)) | 8-level RBAC; least-privilege defaults; admin-approval on registration; admin-managed access. | Implemented |
| Security Awareness & Training (§164.308(a)(5)) | Organizational awareness program. | Partial |
| Security Incident Procedures (§164.308(a)(6)) | Documented Incident Response Plan; Defender alerts; audit logs; breach-notification process. | Implemented |
| Contingency Plan (§164.308(a)(7)) | Azure PostgreSQL backup/recovery capabilities; recovery procedures. | Inherited / Partial |
| Evaluation (§164.308(a)(8)) | Periodic review of controls and documentation. | Partial |
| Business Associate Contracts (§164.308(b)) | BAA with Microsoft/Azure and with covered-entity customers. | Required — see §5 |

---

## 3. Physical Safeguards (§164.310)

| Specification | DocuAction Control | Status |
|---|---|---|
| Facility Access Controls (§164.310(a)) | Data-center physical access controls. | Inherited (Azure) |
| Workstation Use / Security (§164.310(b)–(c)) | Organizational workstation policy; access to PHI mediated by the Platform's authentication/RBAC. | Partial / Inherited |
| Device and Media Controls (§164.310(d)) | Media handling, storage encryption, and sanitization at the Azure platform layer. | Inherited (Azure) |

Physical safeguards for the hosting environment are provided by Microsoft Azure under the shared-responsibility model.

---

## 4. Technical Safeguards (§164.312)

| Specification | Requirement Type | DocuAction Control | Status |
|---|---|---|---|
| Access Control (§164.312(a)(1)) — Unique User ID; Emergency Access; Automatic Logoff; Encryption/Decryption | Required/Addressable | Unique user identity via Entra SSO / accounts; 8-level RBAC; session controls; JWT revocation; AES-256 encryption. | Implemented / Partial (automatic logoff via session controls) |
| Audit Controls (§164.312(b)) | Required | Enterprise audit logging of access and administrative activity; request-ID correlation. | Implemented |
| Integrity (§164.312(c)(1)) — protect PHI from improper alteration/destruction | Required/Addressable | RBAC write restrictions; JWT-signed sessions (HS256); centralized error handling; database SSL; separation of duties in TEFCA workflow. | Implemented / Partial |
| Person or Entity Authentication (§164.312(d)) | Required | bcrypt password hashing; Microsoft Entra ID SSO; JWT access+refresh with revocation. | Implemented |
| Transmission Security (§164.312(e)(1)) — Integrity Controls; Encryption | Required/Addressable | TLS 1.2+ at Azure edge; HTTPS-only; HSTS; database connections over SSL. | Implemented |
| Encryption (supporting all above) | Addressable | AES-256 at rest (Azure TDE/SSE, platform-managed keys, CMK optional); AES-256/ECDHE in transit; PII masking in AI pipeline. | Implemented / Inherited |

---

## 5. Business Associate Agreement (BAA)

Where DocuAction AI processes PHI on behalf of a covered entity or another business associate, HIPAA requires a **Business Associate Agreement**:
- AGT must maintain a **BAA with Microsoft** for the in-scope Azure services (Azure supports HIPAA-eligible services under its BAA).
- AGT must execute a **BAA with each covered-entity customer** whose PHI is processed.
- BAA obligations include permitted uses/disclosures, safeguards, subcontractor flow-down, and breach-notification timelines (see §6).

Establishing and maintaining applicable BAAs is a prerequisite to processing PHI in production.

---

## 6. Breach Notification Context

Where a reportable breach of unsecured PHI is confirmed, notifications are made **without unreasonable delay and no later than 60 calendar days** from discovery, consistent with the HIPAA Breach Notification Rule and BAA flow-down obligations. The breach risk assessment and notification workflow are defined in `docs/security/incident-response-plan.md`.

---

## 7. Honest Posture Statement

DocuAction AI implements the core HIPAA **technical safeguards** (access control, audit controls, authentication, transmission security, and encryption) and a substantial set of **administrative safeguards**, with **physical safeguards inherited from Microsoft Azure**. Several administrative items (formal risk analysis documentation, training records, evaluation cadence) are **Partial/Planned**, and PHI processing in production is contingent on executed **BAAs**. **This document does not assert a completed independent HIPAA audit or certification.**

---

## 8. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Regulatory reference | 45 CFR Part 164, Subpart C (HIPAA Security Rule) |
| Review cadence | Per release or on safeguard change |
| Approver | AGT Security (security@agtbi.com) |
