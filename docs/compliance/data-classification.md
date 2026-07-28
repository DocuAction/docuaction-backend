# DocuAction AI — Data Classification Standard

**Product:** DocuAction AI (Version 6.0.0) — Healthcare/TEFCA suite: DocuAction TEFCA ARC
**Owner:** Alliance Global Tech, Inc. ("AGT")
**Document Classification:** Internal — Compliance Reference
**Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.**
**Security Contact:** security@agtbi.com (48-hour acknowledgment SLA)

---

## 1. Purpose and Scope

This standard defines the data classification tiers used by DocuAction AI, the handling requirements for each tier, and how the tiers map to healthcare/TEFCA data and to the platform's external connector data (NPPES, LEIE, PECOS). Classification drives encryption, access, retention, logging, and masking decisions across the Platform.

---

## 2. Classification Tiers

| Tier | Definition |
|---|---|
| **PHI** | Protected Health Information under HIPAA: individually identifiable health information created, received, or maintained by the Platform on behalf of a covered entity or business associate. |
| **PII** | Personally Identifiable Information: data that can identify an individual (e.g., name, contact details, government identifiers) outside the HIPAA context. |
| **CUI** | Controlled Unclassified Information: federal non-classified information requiring safeguarding or dissemination controls per applicable federal policy. |
| **Internal** | Non-public business information whose disclosure would be inconvenient but not regulated (e.g., internal configuration, non-sensitive operational data). |
| **Public** | Information approved for public release (e.g., published documentation, public API descriptions). |

---

## 3. Examples in DocuAction Context

| Tier | Examples |
|---|---|
| **PHI** | Patient-linked healthcare records or identifiers processed within TEFCA ARC workflows. |
| **PII** | User account details, contact information, authentication identities. |
| **CUI** | Federal contract deliverables and controlled procurement/program data handled for government engagements. |
| **Internal** | Application logs (redacted), workflow metadata, non-sensitive configuration. |
| **Public** | This Platform's public documentation and marketing material. |

---

## 4. Handling Requirements by Tier

| Requirement | PHI | PII | CUI | Internal | Public |
|---|---|---|---|---|---|
| **Encryption in transit** | Required (TLS 1.2+) | Required | Required | Required | Recommended |
| **Encryption at rest** | Required (AES-256 via Azure TDE/SSE) | Required | Required | Recommended | Optional |
| **Access control** | Strict RBAC, least privilege, need-to-know | RBAC least privilege | RBAC least privilege | RBAC | Open (as published) |
| **Masking/redaction** | PII masking in AI pipeline; minimize identifiers | PII masking in AI pipeline | Mask where sensitive | Not required | N/A |
| **Audit logging** | Required (access + admin events) | Required | Required | Recommended | N/A |
| **Retention** | Per contractual/regulatory minimums; dispose securely | Per policy | Per federal requirement | Per policy | N/A |
| **Labeling** | Highest handling controls | Sensitive | Marked CUI per federal guidance | Internal | Public |

**Cross-cutting controls applied to sensitive tiers (PHI/PII/CUI):**
- TLS in transit and database SSL; AES-256 at rest (Azure platform-managed keys, CMK optional).
- 8-level RBAC with least-privilege defaults (`docs/security/access-control-model.md`).
- Multi-pattern PII masking (redaction) in the AI pipeline to minimize identifiers before downstream processing.
- Enterprise audit logging; centralized error handling that avoids exposing sensitive data (request IDs used for correlation).
- Secrets kept out of code and logs (`docs/security/secrets-management.md`).

---

## 5. Labeling

- Sensitive data (PHI/PII/CUI) is handled at the highest applicable control level when the tier is ambiguous (classify up, not down).
- CUI is marked and handled consistent with applicable federal marking guidance.
- Internal and Public materials are labeled to prevent inadvertent over-restriction or over-exposure.

---

## 6. Mapping to TEFCA / Healthcare and Connector Data

DocuAction TEFCA ARC integrates with external federal healthcare data sources. Classification of connector data:

| Connector | Nature of Data | Typical Classification |
|---|---|---|
| **NPPES** (National Plan & Provider Enumeration System) | Provider enumeration/registry data (NPI). Publicly published provider directory data. | Public/Internal for the registry fields; **PII/PHI** when combined with patient or engagement context. |
| **LEIE** (List of Excluded Individuals/Entities, OIG) | Published exclusion list data. | Public/Internal as published; **PII** when linked to individuals in workflow context. |
| **PECOS** (Provider Enrollment, Chain, and Ownership System) | Provider enrollment data (aligned to NPPES sourcing). | Internal/PII depending on linkage and engagement context. |

When registry data is joined with patient, program, or contract context inside TEFCA ARC workflows, the resulting dataset is classified at the **highest applicable tier** (PHI or CUI) and handled accordingly. See `docs/compliance/hipaa-safeguards.md` for HIPAA-specific safeguards.

---

## 7. Document Control

| Field | Value |
|---|---|
| Version | 1.0 |
| Status | Baseline |
| Review cadence | Per release or on data-handling change |
| Approver | AGT Security (security@agtbi.com) |
