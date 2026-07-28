# Business Architecture — Module Criticality Matrix

> Read-only. Endpoint/table counts are approximate per-module attributions from Part 1 static analysis. "In DB" = table materialized in the assessed database (see Part 1 model↔table gap).

## Module Criticality Matrix

| Module | Purpose | Critical | PII | PHI | HIPAA | TEFCA | Internet-Facing | DB Tables (declared / in-DB) | ~API Count | Primary Users |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| **TEFCA Registry** | Normalized QHIN/Participant/Sub registry, verification, FHIR/CSV import | **Yes** | Yes | Yes¹ | Yes | Yes | Yes | 10 / 10 | 19 | ONC / RCE reviewers |
| **TEFCA ARC (legacy)** | Review cycles, evidence, priority cases, QA, reports | **Yes** | Yes | Yes¹ | Yes | Yes | Yes | 13 / 13 | ~60 | ONC reviewers/analysts |
| **Auth / Users** | Signup, login, JWT, refresh, SSO, password reset, RBAC | **Yes** | Yes | No | Yes² | — | Yes | ~2 / 2 (users) | ~20 | All users / admins |
| **Admin** | User & access management, security status | **Yes** | Yes | No | Yes² | — | Yes | (users) | ~19 | Admins |
| **Platform Config** | Tenants/agencies/programs/modules/pages/jurisdictions/identifier-types | **Yes** | No | No | No | Indirect | No (data only) | 13 / 13 | 0 (seed) | Platform |
| **Healthcare Claims** | Claims adjudication engine | **Yes** | Yes | **Yes** | Yes | — | Yes | mixed / ❌ | ~9 | Analysts |
| **Case Management (CCM)** | Care plans, discharge, patients, gov cases | **Yes** | Yes | **Yes** | Yes | — | Yes | ~6 / ❌ | ~14 | Care mgrs |
| **Audio / Meetings** | Whisper transcription, meeting AI | Medium | Yes | Yes¹ | Yes | — | Yes | 2 / 2 | ~4 | Users |
| **Documents** | Upload, scan, AI extraction/summarize | Medium | Yes | Yes¹ | Yes | — | Yes | 2 / 2 | ~10 | Users |
| **Bulletin Intelligence** | FCC daily news aggregation + briefings | Medium | No | No | No | — | Yes (public reads) | in-memory / ~0 | 40 | Public / clients |
| **Migration Intelligence** | Schema/data migration mapping + validation | Medium | Yes | No | Maybe | — | Yes | ~9 / ❌ | 12 | Data engineers |
| **GovCon / ERP** | RFQs, quotes, suppliers, products, deals, invoices, finance | Medium³ | Yes | No | No | — | Yes | ~28 / ❌ | ~90 | Sales / ops |
| **ATS / Staffing** | Candidates, applications, submissions, bench | Medium³ | **Yes** | No | No | — | Yes | ~7 / ❌ | ~80 | Recruiters |
| **Intelligence / Governance / Decisions / SLA / Enterprise** | Cross-cutting AI intel, governance, decision bank | Medium | Yes | Maybe | Maybe | — | Yes | mixed | ~70 | Analysts |

¹ TEFCA/Documents/Audio can incidentally carry PHI (NPI is PHI; uploaded clinical docs/audio may contain PHI). ² Auth is HIPAA-relevant as an access control. ³ GovCon/ATS are business-critical **conceptually** but currently **dormant** (tables not deployed; endpoints largely unauthenticated — see `unauthenticated_endpoints.md`).

## Criticality classification (security scope)

**CRITICAL** — full scope for every future phase:
- TEFCA Registry, TEFCA ARC (legacy), Auth/Users, Admin, Platform Config, Healthcare Claims, Case Management.

**MEDIUM**:
- Documents, Audio/Meetings, Bulletin Intelligence, Migration, Intelligence/Governance/Decisions/SLA/Enterprise.

**LOW / DORMANT** (deprioritize functionally, but flag as attack surface):
- GovCon/ERP and ATS/Staffing — mounted but their tables are not deployed and most endpoints are unauthenticated. **Recommend gating or unmounting** rather than investing.

## Key business-architecture observations
1. **TEFCA is the flagship, but not the majority of the code.** ~2/3 of the endpoints and models belong to non-TEFCA products (GovCon/ATS/ERP/migration/intel) that are **not fully deployed**.
2. **Two distinct "products" share one codebase and two DB Bases:** the *federal/TEFCA* stack (deployed, on `app.core.database.Base`) and the *commercial GovCon/ATS/ERP* stack (dormant, on `app.database.Base`). This split should drive scoping: **secure and certify the federal stack; gate/quarantine the commercial stack.**
3. **PHI reaches multiple modules** (Healthcare Claims, Case Management, Documents, Audio, TEFCA identifiers) → HIPAA technical safeguards must be assessed across all of them (Part 10), not just TEFCA.
