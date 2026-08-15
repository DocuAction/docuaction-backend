# DocuAction TEFCA ARC Platform — Requirements Document

**Version 2.0 | August 2026**

**Contract:** 7571MN26F80064
**Prepared by:** Alliance Global Tech, Inc. (AGT)
**Prepared for:** U.S. Department of Health and Human Services — Office of the National Coordinator for Health IT / Assistant Secretary for Technology Policy (HHS/ONC (ASTP))

> [!WARNING] SYSTEM STATUS: PRE-PRODUCTION DEMONSTRATION MODE. This document describes the system **as built** in the development/main codebase. The production environment currently runs an earlier build; Appendix F enumerates every difference. Security categorization is **assumed** FIPS 199 Moderate — a formal categorization has not been completed. NIST SP 800-53 controls are **self-assessed**, not independently assessed. **No FedRAMP authorization exists or is being pursued.**

## Document Control

### Version History

| Version | Implemented By | Revision Date | Approved By | Approval Date | Description of Change |
|---|---|---|---|---|---|
| 1.0 | AGT Engineering | 2026-08-13 | *pending* | *pending* | Initial release. Requirements derived by direct inspection of the application source, database models, route registrations and configuration. |
| 2.0 | AGT Engineering | 2026-08-13 | *pending* | *pending* | HHS client-ready release. USPS integration status corrected to “configured; zero production verification calls to date.” All diagrams given figure numbers, titles and descriptive captions. See `TEFCA_Document_Remediation_Log.md`. |

### Approval

The undersigned acknowledge they have reviewed the **Requirements Document** and agree with the information presented within this document.

| Role | Name | Signature | Date |
|---|---|---|---|
| AGT Project Manager | | | |
| AGT Technical Lead | | | |
| ONC Contracting Officer's Representative (COR) | | | |
| ONC Program Manager | | | |

## Table of Contents

*(Generated field — press F9 in Microsoft Word to populate page numbers.)*

# 1. Introduction

## 1.1 Purpose of the Requirements Document

This Requirements Document (RD) defines the functional and non-functional requirements for the **DocuAction TEFCA ARC Platform**, the system Alliance Global Tech, Inc. operates to perform Participant and Subparticipant information verification under Contract 7571MN26F80064 for HHS/ONC (ASTP).

The document follows the HHS Enterprise Performance Life Cycle (EPLC) Requirements Definition template structure, enriched with IEEE 830-1998 / ISO/IEC/IEEE 29148:2011 requirements-specification rigor.

**Method.** Every requirement in this document was derived by inspecting the implementation — route registrations, dependency graphs, database models, connector classes, validation engines and configuration — rather than from prior design documentation. Where a capability could not be verified in code, it is marked **VERIFICATION REQUIRED** rather than asserted.

## 1.2 Scope

**In scope.** The TEFCA ARC review subsystem, the TEFCA Registry subsystem, the shared authentication/authorization/audit services, the Bulletin Intelligence module, and the external verification connector framework, as they support SOW Tasks 1 through 6.

**Out of scope.** The platform hosts additional non-contract legacy modules (applicant tracking, invoicing, deal tracking, staffing, procurement). Approximately 75 of the platform's 120 database tables belong to those modules. They are neither documented nor claimed as contract deliverables.

## 1.3 Definitions, Acronyms, and Abbreviations

| Term | Definition |
|---|---|
| ARC | ARC Review — the contract's Participant/Subparticipant verification programme |
| ASTP | Assistant Secretary for Technology Policy |
| B1–B4 | The four-bucket discrepancy taxonomy (Bucket 1 through Bucket 4) |
| COR | Contracting Officer's Representative |
| EPLC | HHS Enterprise Performance Life Cycle |
| FIPS 199 | Federal standard for security categorization of information systems |
| JWT | JSON Web Token |
| LEIE | OIG List of Excluded Individuals/Entities |
| NPI | National Provider Identifier (10 digits, Luhn check digit) |
| NPPES | National Plan and Provider Enumeration System |
| ODC | Other Direct Costs |
| PECOS | Provider Enrollment, Chain and Ownership System |
| PHI | Protected Health Information |
| QHIN | Qualified Health Information Network |
| QTF | QHIN Technical Framework |
| RBAC | Role-Based Access Control |
| RCE | Recognized Coordinating Entity (The Sequoia Project) |
| RTM | Requirements Traceability Matrix |
| SAM.gov | System for Award Management |
| SLA | Service Level Agreement |
| SOW | Statement of Work |
| T1/T2/T3 | The three review routing tiers |
| TEFCA | Trusted Exchange Framework and Common Agreement |
| USPS | United States Postal Service |

## 1.4 References

1. Contract **7571MN26F80064**, Statement of Work, Section C, Tasks 1–6
2. **TEFCA Common Agreement** (ONC/ASTP)
3. **QHIN Technical Framework (QTF) v2.1**
4. **45 CFR Part 172** — HTI-2 Final Rule (TEFCA codification)
5. **NIST SP 800-53 Rev. 5** — Security and Privacy Controls
6. **NIST SP 800-160 Rev. 1** — Engineering Trustworthy Secure Systems
7. **IEEE 830-1998** / **ISO/IEC/IEEE 29148:2011** — Requirements specification
8. **HHS Enterprise Performance Life Cycle (EPLC) Framework**
9. **FIPS 199** — Standards for Security Categorization
10. **Section 508** of the Rehabilitation Act; **WCAG 2.2 Level AA**
11. **USPS Publication 28** — Postal Addressing Standards

## 1.5 Document Overview

Section 2 describes the system in context. Section 3 specifies functional requirements organized by SOW Task, each with a unique identifier and traceability to its deliverable. Section 4 specifies non-functional requirements. Section 5 contains the appendices, including the Requirements Traceability Matrix (Appendix E) and the production delta (Appendix F).

# 2. Overall Description

## 2.1 Product Perspective

DocuAction operates as an ONC-directed verification and reporting system. It does **not** participate in TEFCA exchange, and it does **not** query the Recognized Coordinating Entity.

**Figure 1. DocuAction in the TEFCA Ecosystem**

```
   ┌──────────────────────────────────────────────────────────────────┐
   │                        TEFCA ECOSYSTEM                            │
   │                                                                   │
   │   HHS / ONC (ASTP)                                                │
   │        │                                                          │
   │        │  designates / oversees                                   │
   │        ▼                                                          │
   │   RCE (The Sequoia Project)                                       │
   │        │                                                          │
   │        │  designates                                              │
   │        ▼                                                          │
   │      QHINs  ──────►  Participants  ──────►  Subparticipants       │
   │                                                                   │
   └───────────────────────────┬──────────────────────────────────────┘
                               │
             ONC provides entity population data (contract direction)
                               │
                               ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │              DocuAction TEFCA ARC Platform (AGT)                  │
   │                                                                   │
   │   Import ──► Verify ──► Classify (B1–B4) ──► Route (T1/T2/T3)     │
   │                              │                                    │
   │                              ▼                                    │
   │                   Reports & Evidence Records ──► ONC              │
   └───────────────────────────┬──────────────────────────────────────┘
                               │ queries (read-only)
                               ▼
        NPPES  ·  OIG LEIE  ·  SAM.gov  ·  USPS Address APIs
```

*Figure 1 places the platform within the TEFCA ecosystem. ONC/ASTP oversees the Recognized Coordinating Entity, which designates QHINs; Participants and Subparticipants sit beneath them. The platform receives entity population data from ONC by contract direction — it does not query the RCE — runs a four-stage pipeline, and returns reports and evidence records to ONC.*

*Alt text: Layered diagram. HHS/ONC oversees the RCE, which designates QHINs; QHINs relate to Participants and then Subparticipants. ONC supplies entity population data downward to the DocuAction platform, which runs a four-stage pipeline — import, verify, classify into buckets B1 to B4, and route to tiers T1 to T3 — then returns reports and evidence records to ONC. The platform separately makes read-only queries to four external sources: NPPES, OIG LEIE, SAM.gov and USPS.*

> [!WARNING] **AGT does not contact the RCE (The Sequoia Project) independently.** All TEFCA entity population data is provided by ONC. The class named `RCEDirectoryConnector` (`app/Tefca/connectors.py`) is a **loader for the ONC-provided dataset**, not a live directory integration — its `BASE_URL` is the URN `urn:docuaction:tefca/fhir/r4`, not a network endpoint. The name is retained because the identifier appears in stored database rows, source keys and API payloads; renaming it would be a data migration.

## 2.2 Product Functions

| # | Function | SOW Task |
|---|---|---|
| 1 | Ingest ONC-provided entity population data via CSV and FHIR bundle import | T3, T4 |
| 2 | Verify entity attributes against authoritative external sources | T3, T4, T5 |
| 3 | Classify discrepancies into the B1–B4 taxonomy | T2, T3 |
| 4 | Route findings through the T1/T2/T3 review tiers | T2, T3, T4 |
| 5 | Draw reproducible statistical samples at a stated confidence level | T2, T3 |
| 6 | Manage review cycles keyed to contract task type | T3, T4, T5 |
| 7 | Track SLA due dates, at-risk and overdue states | T5 |
| 8 | Generate weekly, bi-weekly, quarterly and final reports | T3, T4, T5, T6 |
| 9 | Maintain an append-only audit trail of every state transition | T2–T6 |
| 10 | Enforce eight-level role-based access control | Cross-cutting |
| 11 | Provide role-appropriate dashboards and operational views | Cross-cutting |
| 12 | Produce regulatory intelligence briefings (Bulletin Intelligence) | Cross-cutting |

## 2.3 User Classes and Characteristics

Eight roles are defined in `app/core/security.py` (`ROLE_HIERARCHY`). Authorization is **level-based**: a guard admits any role at or above the declared floor.

| Level | Role | Characteristics and needs |
|---|---|---|
| 1 | `viewer` | Read-only stakeholder. Needs dashboards, reports, entity lists and review history. Cannot import, verify, review, approve or modify. |
| 2 | `contributor` | Data-entry analyst. Everything a viewer can do, plus drawing samples and importing entity rosters. Cannot adjudicate. |
| 3 | `manager` | Generic supervisory role inherited from the base product. No TEFCA-specific privileges beyond contributor. |
| 4 | `reviewer` | Front-line Task 3/4/5 reviewer. Verifies entities, adds notes, submits reviews, resolves findings, executes priority cases. Cannot approve deliverables or manage users. |
| 5 | `senior_analyst` | Adds bucket overrides, the Bucket-3 escalation queue, record classification and calibration. |
| 6 | `qalead` | Adds QA approval, methodology sign-off, weekly/bi-weekly report generation and alert testing. Cannot manage users. |
| 7 | `program_manager` | Adds contract deliverable submission (final and quarterly reports), cycle creation and priority case creation. |
| 8 | `admin` | Full access including user and role management. A subset of admins are *super admins* (email in `ADMIN_EMAILS`) and alone may grant or modify the admin role. |

## 2.4 Operating Environment

| Layer | Technology |
|---|---|
| Backend runtime | Python 3.12 (Azure), FastAPI, Gunicorn + Uvicorn workers |
| Frontend | Next.js (App Router), React, static export |
| Database | Azure Database for PostgreSQL Flexible Server |
| Backend hosting | Azure App Service (Linux) |
| Frontend hosting | Azure Static Web Apps |
| Bulletin datastore | Separate SQLite datastore (`BULLETIN_DB_PATH`), independent of the PostgreSQL ORM layer |

Two fully isolated environments are maintained — development and production — with separate App Service applications, separate PostgreSQL servers, separate Microsoft Entra ID registrations, separate secrets, and separate allowed hosts/origins. No secret is shared between environments.

## 2.5 Assumptions and Dependencies

| ID | Assumption / Dependency | Evidence |
|---|---|---|
| A-01 | ONC provides all TEFCA entity population data. AGT does not source it independently. | `RCEDirectoryConnector` docstring; `app/Tefca/connectors.py` |
| A-02 | `TEFCA_ENTITY_DATA_KEY` signals that the ONC-provided dataset is in place. Absent it, `is_running_mock()` returns `True` and bundled development data is served flagged `data_source="MOCK"`. | `connectors.py:68` |
| A-03 | Security categorization is **assumed** FIPS 199 Moderate. Formal categorization is not complete. | Contract posture |
| A-04 | SAM.gov upstream availability is outside AGT control; the v3 entity endpoints have returned upstream 404s. | `SAMGovConnector` |
| A-05 | AI entity resolution is **disabled by default** and advisory only when enabled. | `AI_ENTITY_RESOLUTION`; `app/tefca_registry/entity_resolver.py` |
| A-06 | MOCK-sourced entities are never auto-classified as Bucket 1, so demonstration data cannot masquerade as a finalized clean finding. | `RCEDirectoryConnector` docstring; validation engine |
| A-07 | PECOS enrollment data is derived from the free, key-less NPPES NPI Registry. The PECOS payment-suspension feed requires COR provisioning and is reported as `None`. | `PECOSConnector`, `connectors.py:785` |

## 2.6 Constraints

| ID | Constraint |
|---|---|
| C-01 | Federal compliance obligations: FISMA, HIPAA, Section 508 |
| C-02 | No PHI may be transmitted to AI providers; egress is restricted to a field allowlist |
| C-03 | Architectural changes in production require ONC approval |
| C-04 | Entity population data may originate only from ONC |
| C-05 | The system operates in pre-production demonstration mode; it holds no ATO |
| C-06 | Audit records are **append-only at the application layer** — they are not cryptographically immutable and not write-once at the storage layer |

# 3. Functional Requirements

Requirement identifiers follow the format **FR-T*[task]*-*[sequence]*** for task-scoped requirements and **FR-CC-*[sequence]*** for cross-cutting requirements. Every requirement states its SOW Task, its contract Deliverable, and its verification method.

**Verification methods:** D = Demonstration, T = Test (automated), I = Inspection (code/configuration), A = Analysis.

## 3.1 Task 1 — Administrative / Kickoff

*Deliverable 1: Meeting schedule (within 3 business days); kickoff within 5 business days of award.*

| Req ID | Requirement | Deliv. | Verif. | Status |
|---|---|---|---|---|
| FR-T1-001 | The system shall support a live demonstration of operational verification against real National Provider Identifiers. | D1 | D | CURRENT |
| FR-T1-002 | The system shall provide a bundled development dataset for demonstration when the ONC dataset is not yet in place, flagged `data_source="MOCK"`. | D1 | I, T | CURRENT |
| FR-T1-003 | The system shall expose a demonstration cycle execution endpoint gated to administrators. | D1 | I | CURRENT (dev) |
| FR-T1-004 | The system shall expose platform health and connector status for readiness confirmation. | D1 | T | CURRENT |
| FR-T1-005 | The system shall render role-appropriate dashboards suitable for stakeholder walkthrough. | D1 | D | CURRENT |
| FR-T1-006 | The system shall record demonstration activity in the audit trail with actor attribution. | D1 | T | CURRENT |

## 3.2 Task 2 — Review Methodology and Control Framework

*Deliverable 2: COR-reviewed methodology document (within 2 weeks of award).*

| Req ID | Requirement | Deliv. | Verif. | Status |
|---|---|---|---|---|
| FR-T2-001 | The system shall implement a four-bucket discrepancy taxonomy (B1–B4) as an enumerated classification. | D2 | I, T | CURRENT |
| FR-T2-002 | The system shall expose the review methodology and control framework as a machine-readable reference endpoint. | D2 | T | CURRENT |
| FR-T2-003 | The system shall expose the discrepancy taxonomy, including finding codes, as a reference endpoint. | D2 | T | CURRENT |
| FR-T2-004 | The system shall implement Cochran sample-size calculation. | D2 | T | CURRENT |
| FR-T2-005 | The system shall support configurable confidence levels with documented z-values. | D2 | I, T | CURRENT |
| FR-T2-006 | The system shall support a configurable margin of error per sampling run. | D2 | T | CURRENT |
| FR-T2-007 | The system shall record the confidence level, margin of error and population size used for every sample drawn. | D2 | T | CURRENT |
| FR-T2-008 | The system shall implement three-tier routing (T1/T2/T3) with confidence thresholds. | D2 | I, T | CURRENT |
| FR-T2-009 | The system shall implement escalation triggers that move records between tiers. | D2 | T | CURRENT |
| FR-T2-010 | The system shall implement a source validation hierarchy across authoritative sources. | D2 | I | CURRENT |
| FR-T2-011 | The system shall implement conflict resolution when sources disagree. | D2 | I, A | CURRENT |
| FR-T2-012 | The system shall persist a five-element evidence record for each verification. | D2 | T | CURRENT |
| FR-T2-013 | The system shall support versioned, auditable review rules with change history. | D2 | T | CURRENT |
| FR-T2-014 | The system shall restrict review-rule authoring to administrators. | D2 | T | CURRENT |

## 3.3 Task 3 — Retrospective Review (First 90 Days)

*Deliverables 3.1 (weekly stratified progress reports) and 3.2 (final retrospective report).*

| Req ID | Requirement | Deliv. | Verif. | Status |
|---|---|---|---|---|
| FR-T3-001 | The system shall import entity data from CSV files. | 3.1 | T | CURRENT |
| FR-T3-002 | The system shall import entity data from FHIR R4 bundles. | 3.1 | T | CURRENT |
| FR-T3-003 | The system shall validate NPI format and Luhn check digit. | 3.1 | T | CURRENT |
| FR-T3-004 | The system shall reject invalid import rows individually, reporting row number, field and reason, without failing the whole file. | 3.1 | T | CURRENT |
| FR-T3-005 | The system shall scan uploaded files for script injection before processing. | 3.1 | T | CURRENT |
| FR-T3-006 | The system shall record a SHA-256 checksum for every imported file. | 3.1 | T | CURRENT |
| FR-T3-007 | The system shall write an import history record even when no rows are imported. | 3.1 | T | CURRENT |
| FR-T3-008 | The system shall query NPPES for provider identity and taxonomy data. | 3.1 | T | CURRENT |
| FR-T3-009 | The system shall derive provider enrollment status from NPPES (reported under the PECOS source key). | 3.1 | I, T | CURRENT |
| FR-T3-010 | The system shall report PECOS payment suspension as `None` where the source does not provide it, never as a fabricated clean value. | 3.1 | I, T | CURRENT |
| FR-T3-011 | The system shall query the OIG LEIE for exclusion status. | 3.1 | T | CURRENT |
| FR-T3-012 | The system shall query SAM.gov for federal registration status. | 3.1 | T | CURRENT |
| FR-T3-013 | The system shall normalize addresses in accordance with USPS Publication 28. | 3.1 | T | CURRENT |
| FR-T3-014 | The system shall verify addresses via the USPS Address APIs v3 using OAuth 2.0. | 3.1 | T | CONFIGURED — zero production calls to date |
| FR-T3-015 | The system shall perform Jaro-Winkler name similarity matching. | 3.1 | T | CURRENT |
| FR-T3-016 | The system shall classify each entity into a B1–B4 bucket. | 3.1 | T | CURRENT |
| FR-T3-017 | The system shall refuse to auto-classify MOCK-sourced entities as Bucket 1. | 3.1 | T | CURRENT |
| FR-T3-018 | The system shall assign a review tier (T1/T2/T3) to each record. | 3.1 | T | CURRENT |
| FR-T3-019 | The system shall maintain a Tier-2 analyst queue. | 3.1 | T | CURRENT |
| FR-T3-020 | The system shall maintain a Tier-3 escalation queue restricted to senior analysts and above. | 3.1 | T | CURRENT |
| FR-T3-021 | The system shall support manual bucket reclassification by senior analysts. | 3.1 | T | CURRENT |
| FR-T3-022 | The system shall support escalation of a queued record to a higher tier. | 3.1 | T | CURRENT |
| FR-T3-023 | The system shall draw reproducible statistical samples from a defined population. | 3.1 | T | CURRENT |
| FR-T3-024 | The system shall persist sample membership so a drawn sample can be re-examined. | 3.1 | T | CURRENT |
| FR-T3-025 | The system shall report per-sample completion statistics. | 3.1 | T | CURRENT |
| FR-T3-026 | The system shall prioritize QHIN processing by entity volume. | 3.1 | A, I | VERIFICATION REQUIRED |
| FR-T3-027 | The system shall generate weekly stratified progress reports. | 3.1 | T | CURRENT |
| FR-T3-028 | The system shall export reports in PDF, DOCX, CSV and Excel formats. | 3.1 | T | CURRENT |
| FR-T3-029 | The system shall render reports as HTML for in-browser review. | 3.1 | T | CURRENT |
| FR-T3-030 | The system shall maintain a verification audit trail for every entity check. | 3.1 | T | CURRENT |
| FR-T3-031 | The system shall log every connector invocation with outcome and timing. | 3.1 | T | CURRENT |
| FR-T3-032 | The system shall cache source responses to limit redundant upstream calls. | 3.1 | I | CURRENT |
| FR-T3-033 | The system shall record findings against entities with a reason code. | 3.1 | T | CURRENT |
| FR-T3-034 | The system shall generate a final retrospective report. | 3.2 | T | CURRENT |

## 3.4 Task 4 — Ongoing Bi-Weekly Review

*Deliverable 4.1: Bi-weekly review reports.*

| Req ID | Requirement | Deliv. | Verif. | Status |
|---|---|---|---|---|
| FR-T4-001 | The system shall support continuous ingestion of new QHIN submissions. | 4.1 | T | CURRENT |
| FR-T4-002 | The system shall validate newly ingested entities through the same verification pipeline used in Task 3. | 4.1 | T | CURRENT |
| FR-T4-003 | The system shall route new entities through the same three-tier workflow. | 4.1 | T | CURRENT |
| FR-T4-004 | The system shall support a review cycle of type `TASK4_ONGOING`. | 4.1 | I, T | CURRENT |
| FR-T4-005 | The system shall generate bi-weekly review reports. | 4.1 | T | CURRENT |
| FR-T4-006 | The system shall restrict bi-weekly report generation to QA leads and above. | 4.1 | T | CURRENT |
| FR-T4-007 | The system shall distinguish new from returning entities. | 4.1 | T | VERIFICATION REQUIRED |
| FR-T4-008 | The system shall track review cycle metrics including completion and bucket distribution. | 4.1 | T | CURRENT |
| FR-T4-009 | The system shall list review cycles with status. | 4.1 | T | CURRENT |
| FR-T4-010 | The system shall report per-cycle statistics. | 4.1 | T | CURRENT |
| FR-T4-011 | The system shall surface newly submitted entity reviews awaiting action. | 4.1 | T | CURRENT |
| FR-T4-012 | The system shall maintain entity version history across re-ingestion. | 4.1 | I | CURRENT |

## 3.5 Task 5 — Priority Reviews

*Deliverable 5.1: Priority review reports (ad-hoc).*

| Req ID | Requirement | Deliv. | Verif. | Status |
|---|---|---|---|---|
| FR-T5-001 | The system shall support ad-hoc priority verification when ONC flags an entity. | 5.1 | T | CURRENT |
| FR-T5-002 | The system shall support a review cycle of type `TASK5_PRIORITY`. | 5.1 | I, T | CURRENT |
| FR-T5-003 | The system shall create priority cases with a severity assessment. | 5.1 | T | CURRENT |
| FR-T5-004 | The system shall execute a priority case through the full verification pipeline. | 5.1 | T | CURRENT |
| FR-T5-005 | The system shall assign a due date to each review. | 5.1 | T | CURRENT |
| FR-T5-006 | The system shall classify reviews as `overdue` when the due moment has passed. | 5.1 | T | CURRENT |
| FR-T5-007 | The system shall classify reviews as `at_risk` when two or fewer days remain. | 5.1 | T | CURRENT |
| FR-T5-008 | The system shall present a priority review dashboard with SLA banding. | 5.1 | T | CURRENT |
| FR-T5-009 | The system shall generate a per-case priority review report. | 5.1 | T | CURRENT |
| FR-T5-010 | The system shall support root cause analysis for flagged entities. | 5.1 | D | CURRENT |
| FR-T5-011 | The system shall restrict priority case creation to program managers, and case execution to reviewers and above. | 5.1 | T | CURRENT |

## 3.6 Task 6 — Contract Closeout

*Deliverables 6.1 (final closeout report) and 6.2 (data/system transition package).*

| Req ID | Requirement | Deliv. | Verif. | Status |
|---|---|---|---|---|
| FR-T6-001 | The system shall generate a final comprehensive report. | 6.1 | T | CURRENT |
| FR-T6-002 | The system shall generate quarterly reports. | 6.1 | T | CURRENT |
| FR-T6-003 | The system shall restrict final and quarterly report generation to program managers. | 6.1 | T | CURRENT |
| FR-T6-004 | The system shall export the complete audit trail. | 6.2 | T | CURRENT |
| FR-T6-005 | The system shall export review data in machine-readable formats. | 6.2 | T | CURRENT |
| FR-T6-006 | The system shall retain all review records for the configured retention period. | 6.2 | I | CURRENT |
| FR-T6-007 | The system shall provide downloadable report artifacts by identifier. | 6.2 | T | CURRENT |
| FR-T6-008 | The system shall provide a per-user activity trail for transition and accountability review. | 6.2 | T | CURRENT |

## 3.7 Cross-Cutting Functional Requirements

| Req ID | Requirement | SOW Task | Verif. | Status |
|---|---|---|---|---|
| FR-CC-001 | The system shall authenticate users via signed JSON Web Tokens. | All | T | CURRENT |
| FR-CC-002 | The system shall enforce an eight-level role hierarchy by numeric level, not role name. | All | T | CURRENT |
| FR-CC-003 | The system shall issue 24-hour access tokens to administrators and 15-minute access tokens to all other roles. | All | I, T | CURRENT |
| FR-CC-004 | The system shall issue refresh tokens valid for seven days. | All | I | CURRENT |
| FR-CC-005 | The system shall reject tokens issued before a user's revocation epoch. | All | T | CURRENT |
| FR-CC-006 | The system shall terminate all sessions for an account on logout, disablement or password change. | All | T | CURRENT |
| FR-CC-007 | The system shall enforce account state (active, disabled, pending approval) on every authenticated request. | All | T | CURRENT |
| FR-CC-008 | The system shall require email verification before a self-registered account may sign in. | All | T | CURRENT |
| FR-CC-009 | The system shall require administrator approval before a self-registered account becomes active. | All | T | CURRENT |
| FR-CC-010 | The system shall throttle login attempts per IP address and lock accounts after repeated failures. | All | T | CURRENT |
| FR-CC-011 | The system shall perform exactly one password hash comparison per login attempt regardless of whether the account exists. | All | T | CURRENT |
| FR-CC-012 | The system shall restrict all user-management operations to administrators. | All | T | CURRENT |
| FR-CC-013 | The system shall permit only super administrators to grant or modify the admin role. | All | T | CURRENT |
| FR-CC-014 | The system shall support assignment of all eight roles through the administrative interface. | All | T | CURRENT |
| FR-CC-015 | The system shall support bulk role assignment by email list, restricted to administrators and audited per grant. | All | T | CURRENT |
| FR-CC-016 | The system shall never derive role or privilege from an email address or domain. | All | T, I | CURRENT |
| FR-CC-017 | The system shall grant new accounts a default module set determined by role. | All | T | CURRENT |
| FR-CC-018 | The system shall support per-user module access control across fifteen defined modules. | All | T | CURRENT |
| FR-CC-019 | The system shall support optional Microsoft Entra ID single sign-on. | All | I | CURRENT |
| FR-CC-020 | The system shall map Entra ID group claims to platform roles. | All | I | CURRENT |
| FR-CC-021 | The system shall maintain an append-only audit log of administrative actions. | All | T | CURRENT |
| FR-CC-022 | The system shall record actor identity, target, action and timestamp for every audited event. | All | T | CURRENT |
| FR-CC-023 | The system shall implement an entity lifecycle state machine with states `draft`, `pending_verification`, `active`, `suspended`, `inactive`. | T3, T4 | T | CURRENT |
| FR-CC-024 | The system shall implement entity review status values `PENDING_REVIEW`, `IN_REVIEW`, `REVIEWED_COMPLETE`, `CORRECTIVE_ACTION_OPEN`, `ESCALATED`. | T3–T5 | T | CURRENT |
| FR-CC-025 | The system shall reject state transitions that are not permitted by the state machine. | T3, T4 | T | CURRENT |
| FR-CC-026 | The system shall provide AI-assisted entity resolution as an advisory capability, disabled by default. | T3 | T | CURRENT (disabled) |
| FR-CC-027 | The system shall restrict AI data egress to an explicit field allowlist. | T3 | T | CURRENT |
| FR-CC-028 | The system shall require human review of every AI-assisted determination. | T3 | I | CURRENT |
| FR-CC-029 | The system shall fall back to deterministic matching when AI is unavailable or disabled. | T3 | T | CURRENT |
| FR-CC-030 | The system shall reject requests containing null bytes. | All | T | CURRENT |
| FR-CC-031 | The system shall provide role-filtered navigation, hiding areas a user has not been granted. | All | T | CURRENT |
| FR-CC-032 | The system shall provide entity search across the registry. | T3, T4 | T | CURRENT |
| FR-CC-033 | The system shall produce regulatory intelligence briefings with source attribution and export formats. | Cross | T | CURRENT |

**Total functional requirements: 118** (T1: 6, T2: 14, T3: 34, T4: 12, T5: 11, T6: 8, CC: 33).

# 4. Non-Functional Requirements

## 4.1 Design Constraints

| Req ID | Requirement | Verif. |
|---|---|---|
| NFR-DC-001 | The backend shall be deployed to Azure App Service (Linux). | I |
| NFR-DC-002 | The system of record shall be PostgreSQL. | I |
| NFR-DC-003 | The frontend shall be built with Next.js using the App Router and deployed as a static export to Azure Static Web Apps. | I |
| NFR-DC-004 | The backend API shall be implemented with FastAPI on Python 3.12. | I |
| NFR-DC-005 | Deployment artifacts shall be self-contained, with dependencies vendored for the target Linux/CPython platform rather than built on the host. | I |

## 4.2 Performance Requirements

| Req ID | Requirement | Verif. | Status |
|---|---|---|---|
| NFR-PR-001 | Interactive API endpoints shall respond within 2 seconds under nominal load. | T | VERIFICATION REQUIRED — no load test on record |
| NFR-PR-002 | A single-entity verification across all sources shall complete within 30 seconds. | T | VERIFICATION REQUIRED |
| NFR-PR-003 | The system shall support 50 concurrent users. | T | VERIFICATION REQUIRED — no capacity test on record |
| NFR-PR-004 | External source probes shall execute concurrently rather than serially. | I | CURRENT |
| NFR-PR-005 | HTTP connections to external sources shall be pooled and reused. | I | CURRENT |
| NFR-PR-006 | Source responses shall be cached to reduce redundant upstream calls. | I | CURRENT |
| NFR-PR-007 | Request rate shall be limited per client to protect the service. | T | CURRENT |

## 4.3 Security and Privacy Requirements

| Req ID | Requirement | Verif. | Status |
|---|---|---|---|
| NFR-SP-001 | The system shall handle data in a HIPAA-compliant manner. | A | Self-assessed |
| NFR-SP-002 | All data in transit shall be protected with TLS 1.2 or higher. | I | CURRENT |
| NFR-SP-003 | Data at rest shall be encrypted using the platform's AES-256 storage encryption. | I | CURRENT |
| NFR-SP-004 | Authentication shall use signed JWTs with an expiry and issued-at claim. | T | CURRENT |
| NFR-SP-005 | Authorization shall be enforced by an eight-level role hierarchy evaluated numerically. | T | CURRENT |
| NFR-SP-006 | Audit logging shall be **append-only at the application layer**. | I | CURRENT |
| NFR-SP-007 | Input shall be sanitized against null-byte and injection attacks on all routes. | T | CURRENT |
| NFR-SP-008 | Data sent to AI providers shall be restricted to a public-field allowlist; no PHI shall be transmitted. | T | CURRENT |
| NFR-SP-009 | Passwords shall be stored using a salted adaptive hash (bcrypt). | I | CURRENT |
| NFR-SP-010 | Password complexity shall require upper case, lower case, digit and special character, minimum 8 characters. | T | CURRENT |
| NFR-SP-011 | Secrets shall be supplied by environment/key vault reference and never committed to source. | I | CURRENT |
| NFR-SP-012 | Privilege shall never be derived from an email address or domain. | T | CURRENT |
| NFR-SP-013 | Uploaded files shall be scanned before processing. | T | CURRENT |
| NFR-SP-014 | Security response codes shall not reveal whether an account exists. | T | CURRENT |

> [!WARNING] Audit records are **append-only by application convention** — the application performs no update or delete on audit rows. They are **not** cryptographically immutable, not hash-chained, and not stored on write-once media. A database administrator with direct access could alter them. Do not represent these records as tamper-proof.

## 4.4 Reliability Requirements

| Req ID | Requirement | Verif. | Status |
|---|---|---|---|
| NFR-RL-001 | Verification shall fail closed: an unavailable source shall never be recorded as a clean result. | T | CURRENT |
| NFR-RL-002 | Connector failures shall be contained by a circuit breaker. | I | CURRENT |
| NFR-RL-003 | The system shall fall back to deterministic matching when AI is unavailable. | T | CURRENT |
| NFR-RL-004 | Transient upstream failures shall be retried with backoff. | I | CURRENT |
| NFR-RL-005 | Unavailable sources shall be reported explicitly as unavailable, with reason. | T | CURRENT |
| NFR-RL-006 | Two rollback generations of the deployment artifact shall be retained. | I | CURRENT |

## 4.5 Interface Requirements

| Req ID | Interface | Auth | Status |
|---|---|---|---|
| NFR-IR-001 | NPPES NPI Registry REST API (`npiregistry.cms.hhs.gov`), version 2.1 | Key-less | CURRENT |
| NFR-IR-002 | OIG LEIE exclusion data | Key-less | CURRENT |
| NFR-IR-003 | SAM.gov Entity Management API v3 | API key | DEGRADED — upstream 404 observed |
| NFR-IR-004 | USPS Address APIs v3 (`apis.usps.com`) | OAuth 2.0 | CONFIGURED — zero production calls to date |
| NFR-IR-005 | Anthropic Claude Messages API | API key | CURRENT (advisory, disabled by default) |
| NFR-IR-006 | ONC-provided TEFCA entity dataset loader (`urn:docuaction:tefca/fhir/r4`) | N/A — local dataset | CURRENT |
| NFR-IR-007 | IQVIA OneKey provider hierarchy | API key | **PLANNED / DISABLED** — pending ODC; see `IQVIA_REMOVAL_EDITS.md` |

## 4.6 Compliance and Standards Requirements

| Req ID | Requirement | Status |
|---|---|---|
| NFR-CS-001 | The system shall align to the NIST SP 800-53 Rev. 5 Moderate baseline. | **Self-assessed**, not independently assessed |
| NFR-CS-002 | The system shall apply NIST SP 800-160 Rev. 1 systems security engineering discipline. | Self-assessed |
| NFR-CS-003 | The system shall conform to Section 508 and WCAG 2.2 Level AA. | **In progress** — see 4.7 |
| NFR-CS-004 | The system shall align to the TEFCA Common Agreement. | Self-assessed |
| NFR-CS-005 | The system shall align to the QHIN Technical Framework v2.1. | Self-assessed |
| NFR-CS-006 | Security categorization shall be FIPS 199 Moderate. | **Assumed** — formal categorization not complete |

> [!WARNING] **No FedRAMP authorization exists and none is being pursued.** No Authority to Operate (ATO) has been granted. NIST SP 800-53 control alignment is a self-assessment performed by AGT; it has not been independently assessed, audited, or validated by a third-party assessment organization.

## 4.7 Section 508 Compliance

| Req ID | Requirement | Status |
|---|---|---|
| NFR-508-001 | Interactive controls shall expose accessible names and states. | Implementation in progress |
| NFR-508-002 | Text and interface elements shall meet a 4.5:1 minimum contrast ratio. | Implementation in progress |
| NFR-508-003 | Modal dialogs shall manage focus and support keyboard dismissal. | Implementation in progress |

**Honest status.** Accessibility remediation work is under way and is visible in the codebase — contrast ratios have been raised on the application shell and administrative surfaces, ARIA attributes and accessible names have been added to icon-only controls, and modal focus management with Escape-to-close has been implemented. **No formal conformance testing has been performed.** No automated accessibility scan (axe, pa11y) is part of the test suite, and **no Voluntary Product Accessibility Template (VPAT) has been produced**. Section 508 conformance must therefore be described as *in progress and unverified*, not as achieved.

**Total non-functional requirements: 48** (DC: 5, PR: 7, SP: 14, RL: 6, IR: 7, CS: 6, 508: 3).

# 5. Appendices

## Appendix A — Approval Signature Page

The undersigned acknowledge they have reviewed the **DocuAction TEFCA ARC Platform Requirements Document, Version 1.0** and agree with the information presented within this document. Changes to this document shall be coordinated with, and approved by, the undersigned or their designated representatives.

| Role | Name | Signature | Date |
|---|---|---|---|
| AGT Project Manager | | | |
| AGT Technical Lead | | | |
| ONC Contracting Officer's Representative | | | |
| ONC Program Manager | | | |

## Appendix B — References

See Section 1.4.

## Appendix C — Business Process Model

### C.1 Entity verification workflow

**Figure 2. Entity Verification Workflow**

```
  ONC dataset / CSV / FHIR bundle
            │
            ▼
   ┌─────────────────┐   invalid rows reported individually
   │  Import & scan  │──────────────────────────────────────►  Import history
   │  SHA-256 hash   │                                          (+ row errors)
   └────────┬────────┘
            ▼
   ┌─────────────────┐
   │ NPI validation  │  format + Luhn check digit
   └────────┬────────┘
            ▼
   ┌──────────────────────────────────────────────┐
   │        Concurrent source verification         │
   │   NPPES   OIG LEIE   SAM.gov   USPS address   │
   └────────┬─────────────────────────────────────┘
            │  unavailable source ⇒ recorded unavailable, never "clean"
            ▼
   ┌─────────────────┐
   │ Name similarity │  Jaro-Winkler
   │ Address normal. │  USPS Pub 28
   └────────┬────────┘
            ▼
   ┌─────────────────┐        ┌──────────────────────┐
   │ B1–B4 classify  │───────►│ Evidence record (5)  │
   └────────┬────────┘        └──────────────────────┘
            ▼
   ┌─────────────────┐
   │ Tier routing    │  T1 auto · T2 analyst queue · T3 senior escalation
   └────────┬────────┘
            ▼
      Review → Finding → Report
```

*Figure 2 shows the end-to-end verification path for a single entity, from import through classification to review. Two control points are load-bearing: an unavailable source is recorded as unavailable rather than clean, and invalid import rows are rejected individually with row, field and reason rather than failing the whole file.*

*Alt text: Vertical flowchart of entity verification. Data enters from the ONC dataset, CSV or FHIR bundle into an import-and-scan step that hashes the file and reports invalid rows to an import history. Valid rows pass NPI format and Luhn validation, then concurrent verification against NPPES, OIG LEIE, SAM.gov and USPS, where an unavailable source is recorded as unavailable rather than clean. Results pass through name-similarity and address-normalization, then bucket classification which emits a five-element evidence record, then tier routing to T1 automatic, T2 analyst queue or T3 senior escalation, ending in review, finding and report.*

### C.2 Review lifecycle

**Figure 3. Review Lifecycle State Transitions**

```
  PENDING_REVIEW ──► IN_REVIEW ──► REVIEWED_COMPLETE
                         │
                         ├──► CORRECTIVE_ACTION_OPEN ──► REVIEWED_COMPLETE
                         └──► ESCALATED ──► (senior analyst) ──► REVIEWED_COMPLETE
```

*Figure 3 shows the permitted transitions for a review record. Corrective action and escalation are branches from IN_REVIEW that both rejoin at REVIEWED_COMPLETE; escalated records are handled by a senior analyst before they can complete.*

*Alt text: State diagram. A review begins at PENDING_REVIEW and moves to IN_REVIEW. From IN_REVIEW it may complete directly, or branch to CORRECTIVE_ACTION_OPEN or ESCALATED, both of which return to REVIEWED_COMPLETE — escalation via senior analyst handling.*

### C.3 Entity lifecycle state machine

**Figure 4. Entity Lifecycle State Machine**

```
  draft ──► pending_verification ──► active ──► suspended ──► inactive
                                        ▲            │
                                        └────────────┘
```

*Figure 4 shows the entity lifecycle. Only the transitions drawn are permitted; the state machine rejects any other transition with a reason, so an entity cannot skip verification on its way to active.*

*Alt text: Linear state machine with five states — draft, pending_verification, active, suspended, inactive — where suspended may return to active.*

### C.4 B1–B4 classification decision tree

**Figure 5. B1–B4 Classification Decision Tree**

```
  Source data available for entity?
    │
    ├─ NO  ──────────────────────────────────────► B4  (unverifiable)
    │
    └─ YES
         │
         ├─ Data source is MOCK? ── YES ─────────► NOT ELIGIBLE for B1
         │                                          (demonstration data guard)
         └─ NO
              │
              ├─ All attributes match authoritative sources? ── YES ──► B1  (clean)
              │
              ├─ Minor//formatting discrepancy only? ─────────────────► B2
              │
              ├─ Material discrepancy requiring analyst judgement? ──► B3
              │
              └─ Adverse finding (exclusion / debarment)? ───────────► B4
```

*Figure 5 shows the structural logic by which a discrepancy is assigned to a bucket. The MOCK guard is a deliberate safety property: demonstration data is barred from automatic Bucket 1 so it can never masquerade as a finalized clean finding. Numeric confidence thresholds are governed by the COR-approved methodology and the versioned review rules, not by this diagram.*

*Alt text: Decision tree for bucket classification. If no source data is available the entity is Bucket 4 unverifiable. If data came from MOCK sources the entity is barred from Bucket 1. Otherwise a full match yields Bucket 1, a minor or formatting discrepancy yields Bucket 2, a material discrepancy needing analyst judgement yields Bucket 3, and an adverse finding such as an exclusion or debarment yields Bucket 4.*

> [!WARNING] The precise B1–B4 decision thresholds are implemented in the validation engine and review-rules tables and are configurable per review cycle. The tree above is the structural logic; the numeric confidence thresholds are governed by the COR-approved methodology (Deliverable 2) and by the versioned review rules, and are therefore marked VERIFICATION REQUIRED for any specific numeric value.

## Appendix D — Logical Data Model

The contract-relevant logical model spans two entity stores plus shared services.

**Figure 6. Logical Data Model — Entity-Relationship Overview**

```
   users ──1:N──► audit_logs
     │
     └──1:N──► (actor on) tefca_reviews, tefca_verifications, import batches

   ┌──────────────── TEFCA ARC (app/Tefca) ────────────────┐
   │  tefca_entities ──1:N──► tefca_reviews                 │
   │        │                      │                        │
   │        │                      └──1:N──► tefca_findings  │
   │        ├──1:N──► tefca_evidence_records                 │
   │        └──1:N──► tefca_priority_cases                   │
   │  tefca_review_cycles ──1:N──► tefca_reviews             │
   │  tefca_analyst_queue · tefca_connector_logs             │
   │  tefca_source_cache  · tefca_import_history             │
   │  tefca_reports                                          │
   └────────────────────────────────────────────────────────┘
                      ▲
                      │  import bridge (CSV upload syncs import → registry)
                      ▼
   ┌────────────── TEFCA Registry (app/tefca_registry) ─────┐
   │  tefca_reg_entities ──1:N──► tefca_entity_identifiers   │
   │        ├──1:N──► tefca_entity_endpoints                 │
   │        ├──1:N──► tefca_entity_relationships             │
   │        ├──1:N──► tefca_entity_versions                  │
   │        ├──1:N──► tefca_entity_findings                  │
   │        └──1:N──► tefca_verifications ──► tefca_verification_checks
   │  tefca_verification_jobs · tefca_import_batches          │
   │  tefca_reg_audit_log                                     │
   │  review_cycles · review_records · review_reports         │
   │  review_rules  · review_samples ──1:N──► sample_entities │
   └─────────────────────────────────────────────────────────┘
```

*Figure 6 shows the contract-relevant logical data model across the two entity stores and shared services. The import bridge between them reflects the known two-table split documented as design debt; consumers must know which store they are reading, as the two are not interchangeable.*

*Alt text: Entity-relationship overview. Users have many audit log entries and act on reviews, verifications and import batches. The TEFCA ARC store centres on tefca_entities, which has many reviews (each with findings), evidence records and priority cases, alongside review cycles, an analyst queue, connector logs, a source cache, import history and reports. An import bridge links it to the TEFCA Registry store, which centres on tefca_reg_entities with child tables for identifiers, endpoints, relationships, versions, findings and verifications with checks, plus verification jobs, import batches, an audit log, and review cycle, record, report, rule and sample tables where samples own sample entities.*

> [!WARNING] **Known architectural issue.** Entity data is split across two tables — `tefca_entities` (ARC) and `tefca_reg_entities` (Registry) — with a bridge that syncs import into the registry on CSV upload. This duplication is a known design debt documented here for future unification. It is not a defect in current operation, but it means "the entity record" has two homes and consumers must know which store they are reading.

Full column-level detail is provided in the System Design Document, Appendix E.

## Appendix E — Requirements Traceability Matrix (RTM)

Bidirectional traceability: **SOW Task → Requirement → Design Element → Test/Verification**.

| Req ID | Description (abbrev.) | SOW Task | Deliv. | Type | Priority | Design Element (SDD §) | System Component | Verification | Status |
|---|---|---|---|---|---|---|---|---|---|
| FR-T1-001 | Live demonstration | T1 | D1 | Func | High | 3.3.2 | ARC Review Engine | D | CURRENT |
| FR-T1-002 | MOCK-flagged demo dataset | T1 | D1 | Func | High | 3.3.4 | Connector Framework | I, T | CURRENT |
| FR-T1-003 | Demo cycle endpoint (admin) | T1 | D1 | Func | Low | 3.3.8 | Cycle Management | I | CURRENT (dev) |
| FR-T1-004 | Health & connector status | T1 | D1 | Func | Med | 3.3.15 | Audit & Compliance | T | CURRENT |
| FR-T1-005 | Role-appropriate dashboards | T1 | D1 | Func | Med | 4.5 | Frontend | D | CURRENT |
| FR-T1-006 | Demo activity audited | T1 | D1 | Func | Med | 3.6.4 | Audit Architecture | T | CURRENT |
| FR-T2-001 | B1–B4 taxonomy | T2 | D2 | Func | High | 3.3.6 | Classification Engine | I, T | CURRENT |
| FR-T2-002 | Methodology reference endpoint | T2 | D2 | Func | High | 3.3.2 | ARC Review Engine | T | CURRENT |
| FR-T2-003 | Discrepancy taxonomy endpoint | T2 | D2 | Func | High | 3.3.6 | Classification Engine | T | CURRENT |
| FR-T2-004 | Cochran sample size | T2 | D2 | Func | High | 3.3.5 | Sampling Engine | T | CURRENT |
| FR-T2-005 | Configurable confidence level | T2 | D2 | Func | High | 3.3.5 | Sampling Engine | I, T | CURRENT |
| FR-T2-006 | Configurable margin of error | T2 | D2 | Func | High | 3.3.5 | Sampling Engine | T | CURRENT |
| FR-T2-007 | Record sampling parameters | T2 | D2 | Func | High | 3.3.5 | Sampling Engine | T | CURRENT |
| FR-T2-008 | T1/T2/T3 routing | T2 | D2 | Func | High | 3.3.3 | Verification Pipeline | I, T | CURRENT |
| FR-T2-009 | Escalation triggers | T2 | D2 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T2-010 | Source validation hierarchy | T2 | D2 | Func | High | 3.3.4 | Connector Framework | I | CURRENT |
| FR-T2-011 | Conflict resolution | T2 | D2 | Func | High | 3.3.3 | Verification Pipeline | I, A | CURRENT |
| FR-T2-012 | Five-element evidence record | T2 | D2 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T2-013 | Versioned review rules | T2 | D2 | Func | Med | 3.3.12 | Registry Module | T | CURRENT |
| FR-T2-014 | Rule authoring admin-only | T2 | D2 | NF-Sec | High | 3.6.2 | Authorization | T | CURRENT |
| FR-T3-001 | CSV import | T3 | 3.1 | Func | High | 3.3.10 | Import Pipeline | T | CURRENT |
| FR-T3-002 | FHIR bundle import | T3 | 3.1 | Func | Med | 3.3.10 | Import Pipeline | T | CURRENT |
| FR-T3-003 | NPI Luhn validation | T3 | 3.1 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T3-004 | Row-level rejection | T3 | 3.1 | Func | High | 3.3.10 | Import Pipeline | T | CURRENT |
| FR-T3-005 | Upload injection scan | T3 | 3.1 | NF-Sec | High | 3.6.5 | Input Validation | T | CURRENT |
| FR-T3-006 | SHA-256 file checksum | T3 | 3.1 | Func | High | 3.6.4 | Audit Architecture | T | CURRENT |
| FR-T3-007 | Import history always written | T3 | 3.1 | Func | Med | 3.3.10 | Import Pipeline | T | CURRENT |
| FR-T3-008 | NPPES query | T3 | 3.1 | Func | High | 3.3.4 | Connector Framework | T | CURRENT |
| FR-T3-009 | Enrollment derived from NPPES | T3 | 3.1 | Func | High | 3.3.4 | Connector Framework | I, T | CURRENT |
| FR-T3-010 | Payment suspension reported None | T3 | 3.1 | Func | High | 3.3.4 | Connector Framework | I, T | CURRENT |
| FR-T3-011 | OIG LEIE query | T3 | 3.1 | Func | High | 3.3.4 | Connector Framework | T | CURRENT |
| FR-T3-012 | SAM.gov query | T3 | 3.1 | Func | High | 3.3.4 | Connector Framework | T | DEGRADED |
| FR-T3-013 | USPS Pub 28 normalization | T3 | 3.1 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T3-014 | USPS v3 OAuth verification | T3 | 3.1 | Func | High | 3.3.4 | Connector Framework | T | CURRENT |
| FR-T3-015 | Jaro-Winkler matching | T3 | 3.1 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T3-016 | B1–B4 classification | T3 | 3.1 | Func | High | 3.3.6 | Classification Engine | T | CURRENT |
| FR-T3-017 | MOCK never auto-B1 | T3 | 3.1 | NF-Rel | High | 3.3.6 | Classification Engine | T | CURRENT |
| FR-T3-018 | Tier assignment | T3 | 3.1 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T3-019 | Tier-2 analyst queue | T3 | 3.1 | Func | High | 3.3.2 | ARC Review Engine | T | CURRENT |
| FR-T3-020 | Tier-3 escalation queue | T3 | 3.1 | Func | High | 3.3.2 | ARC Review Engine | T | CURRENT |
| FR-T3-021 | Manual bucket override | T3 | 3.1 | Func | Med | 3.3.6 | Classification Engine | T | CURRENT |
| FR-T3-022 | Record escalation | T3 | 3.1 | Func | Med | 3.3.2 | ARC Review Engine | T | CURRENT |
| FR-T3-023 | Reproducible sampling | T3 | 3.1 | Func | High | 3.3.5 | Sampling Engine | T | CURRENT |
| FR-T3-024 | Sample membership persisted | T3 | 3.1 | Func | High | 3.3.5 | Sampling Engine | T | CURRENT |
| FR-T3-025 | Sample completion stats | T3 | 3.1 | Func | Med | 3.3.5 | Sampling Engine | T | CURRENT |
| FR-T3-026 | QHIN volume prioritization | T3 | 3.1 | Func | Med | 3.3.8 | Cycle Management | A, I | **VERIFICATION REQUIRED** |
| FR-T3-027 | Weekly stratified reports | T3 | 3.1 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T3-028 | PDF/DOCX/CSV/XLSX export | T3 | 3.1 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T3-029 | HTML report rendering | T3 | 3.1 | Func | Med | 3.3.7 | Report Generator | T | CURRENT |
| FR-T3-030 | Verification audit trail | T3 | 3.1 | Func | High | 3.6.4 | Audit Architecture | T | CURRENT |
| FR-T3-031 | Connector invocation logging | T3 | 3.1 | Func | Med | 3.3.4 | Connector Framework | T | CURRENT |
| FR-T3-032 | Source response caching | T3 | 3.1 | NF-Perf | Med | 3.8 | Performance | I | CURRENT |
| FR-T3-033 | Findings with reason codes | T3 | 3.1 | Func | High | 3.3.6 | Classification Engine | T | CURRENT |
| FR-T3-034 | Final retrospective report | T3 | 3.2 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T4-001 | Continuous ingestion | T4 | 4.1 | Func | High | 3.3.10 | Import Pipeline | T | CURRENT |
| FR-T4-002 | Same verification pipeline | T4 | 4.1 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T4-003 | Same tier workflow | T4 | 4.1 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T4-004 | TASK4_ONGOING cycle type | T4 | 4.1 | Func | High | 3.3.8 | Cycle Management | I, T | CURRENT |
| FR-T4-005 | Bi-weekly reports | T4 | 4.1 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T4-006 | Bi-weekly gated to qalead | T4 | 4.1 | NF-Sec | High | 3.6.2 | Authorization | T | CURRENT |
| FR-T4-007 | New vs returning entities | T4 | 4.1 | Func | Med | 3.3.11 | State Machine | T | **VERIFICATION REQUIRED** |
| FR-T4-008 | Cycle metrics | T4 | 4.1 | Func | High | 3.3.8 | Cycle Management | T | CURRENT |
| FR-T4-009 | Cycle listing | T4 | 4.1 | Func | Med | 3.3.8 | Cycle Management | T | CURRENT |
| FR-T4-010 | Per-cycle statistics | T4 | 4.1 | Func | Med | 3.3.8 | Cycle Management | T | CURRENT |
| FR-T4-011 | New submissions surfaced | T4 | 4.1 | Func | Med | 3.3.2 | ARC Review Engine | T | CURRENT |
| FR-T4-012 | Entity version history | T4 | 4.1 | Func | Med | 4.2 | Database Design | I | CURRENT |
| FR-T5-001 | Ad-hoc priority verification | T5 | 5.1 | Func | High | 3.3.9 | Priority/SLA Engine | T | CURRENT |
| FR-T5-002 | TASK5_PRIORITY cycle type | T5 | 5.1 | Func | High | 3.3.8 | Cycle Management | I, T | CURRENT |
| FR-T5-003 | Severity assessment | T5 | 5.1 | Func | High | 3.3.9 | Priority/SLA Engine | T | CURRENT |
| FR-T5-004 | Full-pipeline execution | T5 | 5.1 | Func | High | 3.3.3 | Verification Pipeline | T | CURRENT |
| FR-T5-005 | Review due dates | T5 | 5.1 | Func | High | 3.3.9 | Priority/SLA Engine | T | CURRENT |
| FR-T5-006 | Overdue classification | T5 | 5.1 | Func | High | 3.3.9 | Priority/SLA Engine | T | CURRENT |
| FR-T5-007 | At-risk classification (≤2 days) | T5 | 5.1 | Func | High | 3.3.9 | Priority/SLA Engine | T | CURRENT |
| FR-T5-008 | Priority dashboard with SLA bands | T5 | 5.1 | Func | High | 4.5 | Frontend | T | CURRENT |
| FR-T5-009 | Per-case priority report | T5 | 5.1 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T5-010 | Root cause analysis | T5 | 5.1 | Func | Med | 3.3.9 | Priority/SLA Engine | D | CURRENT |
| FR-T5-011 | Creation/execution role split | T5 | 5.1 | NF-Sec | High | 3.6.2 | Authorization | T | CURRENT |
| FR-T6-001 | Final comprehensive report | T6 | 6.1 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T6-002 | Quarterly reports | T6 | 6.1 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T6-003 | Final/quarterly gated to PM | T6 | 6.1 | NF-Sec | High | 3.6.2 | Authorization | T | CURRENT |
| FR-T6-004 | Audit trail export | T6 | 6.2 | Func | High | 3.6.4 | Audit Architecture | T | CURRENT |
| FR-T6-005 | Machine-readable review export | T6 | 6.2 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T6-006 | Retention period | T6 | 6.2 | NF-Comp | Med | 6.3 | Configuration | I | CURRENT |
| FR-T6-007 | Report artifact download | T6 | 6.2 | Func | High | 3.3.7 | Report Generator | T | CURRENT |
| FR-T6-008 | Per-user activity trail | T6 | 6.2 | Func | Med | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-001 | JWT authentication | All | All | Func | High | 3.6.1 | Authentication | T | CURRENT |
| FR-CC-002 | Level-based 8-role RBAC | All | All | Func | High | 3.6.2 | Authorization | T | CURRENT |
| FR-CC-003 | 24h admin / 15min user tokens | All | All | NF-Sec | High | 3.6.1 | Authentication | I, T | CURRENT |
| FR-CC-004 | 7-day refresh tokens | All | All | NF-Sec | Med | 3.6.1 | Authentication | I | CURRENT |
| FR-CC-005 | Revocation-epoch rejection | All | All | NF-Sec | High | 3.6.1 | Authentication | T | CURRENT |
| FR-CC-006 | Session termination on change | All | All | NF-Sec | High | 3.6.1 | Authentication | T | CURRENT |
| FR-CC-007 | Account state on every request | All | All | NF-Sec | High | 3.6.2 | Authorization | T | CURRENT |
| FR-CC-008 | Email verification gate | All | All | NF-Sec | High | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-009 | Admin approval gate | All | All | NF-Sec | High | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-010 | Login throttle & lockout | All | All | NF-Sec | High | 3.6.1 | Authentication | T | CURRENT |
| FR-CC-011 | Constant-time login path | All | All | NF-Sec | High | 3.6.1 | Authentication | T | CURRENT |
| FR-CC-012 | User mgmt admin-only | All | All | NF-Sec | High | 3.6.2 | Authorization | T | CURRENT |
| FR-CC-013 | Super-admin gate on admin role | All | All | NF-Sec | High | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-014 | All 8 roles assignable | All | All | Func | High | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-015 | Audited bulk role assignment | All | All | Func | Med | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-016 | No privilege from email domain | All | All | NF-Sec | High | 3.6.2 | Authorization | T, I | CURRENT |
| FR-CC-017 | Role-based default modules | All | All | Func | Med | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-018 | 15-module per-user access | All | All | Func | Med | 3.3.16 | Admin & User Mgmt | T | CURRENT |
| FR-CC-019 | Entra ID SSO (optional) | All | All | Func | Low | 3.6.1 | Authentication | I | CURRENT |
| FR-CC-020 | Entra group→role mapping | All | All | Func | Low | 3.6.1 | Authentication | I | CURRENT |
| FR-CC-021 | Append-only admin audit log | All | All | Func | High | 3.6.4 | Audit Architecture | T | CURRENT |
| FR-CC-022 | Actor/target/action/timestamp | All | All | Func | High | 3.6.4 | Audit Architecture | T | CURRENT |
| FR-CC-023 | Entity lifecycle state machine | T3, T4 | — | Func | High | 3.3.11 | State Machine | T | CURRENT |
| FR-CC-024 | Review status enumeration | T3–T5 | — | Func | High | 3.3.11 | State Machine | T | CURRENT |
| FR-CC-025 | Illegal transitions rejected | T3, T4 | — | NF-Rel | High | 3.3.11 | State Machine | T | CURRENT |
| FR-CC-026 | AI advisory, disabled default | T3 | — | Func | Med | 3.3.14 | AI Entity Resolution | T | CURRENT (disabled) |
| FR-CC-027 | AI egress field allowlist | T3 | — | NF-Sec | High | 3.6.6 | AI Security Controls | T | CURRENT |
| FR-CC-028 | Human review always required | T3 | — | NF-Rel | High | 3.6.6 | AI Security Controls | I | CURRENT |
| FR-CC-029 | Deterministic AI fallback | T3 | — | NF-Rel | High | 3.3.14 | AI Entity Resolution | T | CURRENT |
| FR-CC-030 | Null-byte rejection | All | All | NF-Sec | High | 3.6.5 | Input Validation | T | CURRENT |
| FR-CC-031 | Role-filtered navigation | All | All | Func | Med | 4.5 | Frontend | T | CURRENT |
| FR-CC-032 | Registry entity search | T3, T4 | — | Func | Med | 3.3.12 | Registry Module | T | CURRENT |
| FR-CC-033 | Bulletin briefings | Cross | — | Func | Low | 3.3.13 | Bulletin Module | T | CURRENT |

**Non-functional requirements** trace as follows: NFR-DC-001…005 → SDD §3.4/§3.5 (all tasks, infrastructure); NFR-PR-001…007 → SDD §3.8 (T3, T4 throughput); NFR-SP-001…014 → SDD §3.6 (all tasks); NFR-RL-001…006 → SDD §3.3.4, §6.2 (T3–T5); NFR-IR-001…007 → SDD §3.7, Appendix F (T3–T5); NFR-CS-001…006 → SDD §2.1 (all tasks); NFR-508-001…003 → SDD §4.7 (all tasks).

## Appendix F — Production Delta (Deployment Backlog)

This document describes the system **as built** in the development/main codebase. The production environment currently runs an earlier build. The following differences are outstanding.

| Area | Development / main (as documented) | Production (as deployed) | Requirement affected |
|---|---|---|---|
| TEFCA read access floor | `viewer` (level 1) may read TEFCA dashboards, reports, reviews, findings | `reviewer` (level 4) required for all TEFCA reads | FR-CC-002, §2.3 user classes |
| Role assignability | All 8 roles assignable | All 8 roles assignable (deployed) | FR-CC-014 — **parity** |
| Role default modules | Implemented | Implemented (deployed) | FR-CC-017 — **parity** |
| Bulk role assignment | Implemented | Implemented (deployed) | FR-CC-015 — **parity** |
| Bulletin navigation | Visible to all authenticated users | Visible to all authenticated users (deployed) | FR-CC-031 — **parity** |
| Upload security scanning on ARC upload | Present | Absent | FR-T3-005 (registry path only in prod) |
| USPS address verification module | Present (`usps_client.py`, `usps_routes.py`) | Absent | FR-T3-014 |
| Review SLA module | Present (`sla.py`) | Absent | FR-T5-005…008 |
| Import bridge | Present (`import_bridge.py`) | Absent | FR-T3-001 (bridge behaviour) |
| Bulletin document generation | Present (`word_generator.py`, `email_template.py`, `reviewed_upload.py`) | Absent | FR-CC-033 |
| Application startup DDL | Includes one additional `ALTER TABLE` | Not applied | §4.2 schema |

**Scale of delta:** 29 modified application files and 7 files absent from production, of which the RBAC-critical subset (2 files) has been deployed. Deploying the remainder requires independent review of the non-RBAC changes and is tracked as contract technical debt.

## Appendix G — Discovery Provenance

| Artifact | Method | Result |
|---|---|---|
| API endpoints | Walked every mounted router in the live FastAPI app; resolved each route's full dependency tree for its effective role floor | 325 endpoints across 15 modules |
| Database tables | Scanned all `__tablename__` declarations across `app/` | 120 tables (45 contract-relevant, ~75 non-contract legacy) |
| Roles | Read `ROLE_HIERARCHY` from `app/core/security.py` | 8 roles, levels 1–8 |
| Connectors | Enumerated classes in `app/Tefca/connectors.py` | 6 source connectors + manager |
| Configuration | Scanned `os.getenv` / `os.environ` references | 96 environment variables |
| Frontend pages | Counted `page.js` / `page.tsx` under `frontend/src/app` | 75 total; 26 TEFCA; 1 Bulletin |
| Test suite | Executed the full suite | 804 passed, 24 skipped, 0 failed |

---

*End of Requirements Document.*
