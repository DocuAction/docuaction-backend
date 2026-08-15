# DocuAction TEFCA ARC Platform — System Design Document

**Version 2.0 | August 2026**

**Contract:** 7571MN26F80064
**Prepared by:** Alliance Global Tech, Inc. (AGT)
**Prepared for:** U.S. Department of Health and Human Services — Office of the National Coordinator for Health IT / Assistant Secretary for Technology Policy (HHS/ONC (ASTP))

> [!WARNING] SYSTEM STATUS: PRE-PRODUCTION DEMONSTRATION MODE. This document describes the system **as built** in the development/main codebase; Section 6.4 enumerates every difference from the deployed production build. Security categorization is **assumed** FIPS 199 Moderate — formal categorization is not complete. NIST SP 800-53 controls are **self-assessed**, not independently assessed. **No FedRAMP authorization exists or is being pursued, and no ATO has been granted.**

## Document Control

### Version History

| Version | Implemented By | Revision Date | Approved By | Approval Date | Description of Change |
|---|---|---|---|---|---|
| 1.0 | AGT Engineering | 2026-08-13 | *pending* | *pending* | Initial release. Architecture derived by direct inspection of route registrations, dependency graphs, ORM models, connector classes and deployment configuration. |
| 2.0 | AGT Engineering | 2026-08-13 | *pending* | *pending* | HHS client-ready release. USPS integration status corrected to “configured; zero production verification calls to date.” All diagrams given figure numbers, titles and descriptive captions. See `TEFCA_Document_Remediation_Log.md`. |

### Approval

The undersigned acknowledge they have reviewed the **System Design Document** and agree with the information presented within this document.

| Role | Name | Signature | Date |
|---|---|---|---|
| AGT Project Manager | | | |
| AGT Technical Lead | | | |
| ONC Contracting Officer's Representative (COR) | | | |
| ONC Program Manager | | | |

## Table of Contents

*(Generated field — press F9 in Microsoft Word to populate page numbers.)*

# 1. Introduction

## 1.1 Purpose of the System Design Document

This System Design Document (SDD) describes the architecture and detailed design of the **DocuAction TEFCA ARC Platform** as implemented under Contract 7571MN26F80064. It follows the HHS EPLC Design template structure, enriched with IEEE 1016 design viewpoints (context, logical, dependency, interface, structure, interaction, state dynamics, and deployment).

It is the design counterpart to the Requirements Document (RD) v1.0; Section 4.1 provides traceability back to the RD.

## 1.2 Scope

Covers the TEFCA ARC subsystem, TEFCA Registry subsystem, Bulletin Intelligence module, shared authentication/authorization/audit services, the external connector framework, and the Azure deployment topology. The platform's non-contract legacy modules are excluded.

## 1.3 Definitions, Acronyms, and Abbreviations

See RD Section 1.3. Additional design-specific terms:

| Term | Definition |
|---|---|
| Dependency tree | The full graph of FastAPI `Depends()` callables resolved for a route, including router-level dependencies |
| Role floor | The minimum role level admitted by a route, taking the maximum across its dependency tree |
| Effective floor | The role floor actually in force at runtime, including inherited router-level guards |
| Fail-closed | Behaviour in which an error or unavailability produces denial or "unavailable", never a permissive or clean result |

## 1.4 References

See RD Section 1.4. Additionally: IEEE 1016-2009 (Software Design Descriptions); `backend/docs/DEPLOYMENT_GUIDE.md`; `backend/docs/deployment/environment-topology.md`; `backend/docs/IQVIA_REMOVAL_EDITS.md`.

## 1.5 Document Overview

Section 2 states design assumptions and methodology. Section 3 presents the architecture across context, logical, component, infrastructure, software, security, communication and performance views. Section 4 details the design — traceability, database, APIs, UI and performance. Section 5 covers verification and validation. Section 6 covers deployment.

# 2. General Overview and Design Approach

## 2.1 Assumptions, Constraints, and Standards

| Standard / Framework | Application to this design |
|---|---|
| TEFCA Common Agreement | The system verifies Participant/Subparticipant information supplied by ONC; it is not an exchange participant and holds no Common Agreement obligations of its own |
| QHIN Technical Framework v2.1 | Entity model and identifier semantics align to QTF concepts (QHIN → Participant → Subparticipant hierarchy) |
| 45 CFR Part 172 (HTI-2) | TEFCA codification context for the review programme |
| NIST SP 800-53 Rev. 5 | Moderate baseline used as the control reference — **self-assessed** |
| NIST SP 800-160 Rev. 1 | Fail-closed design, least privilege, and defence-in-depth applied as engineering discipline |
| FIPS 199 | **Assumed** Moderate categorization; formal categorization not complete |
| Section 508 / WCAG 2.2 AA | Accessibility target; implementation in progress, no formal conformance testing |

**Design constraints.** Entity population data may originate only from ONC. No PHI may be transmitted to AI providers. Architectural changes in production require ONC approval. The system holds no ATO and operates in demonstration mode.

## 2.2 Alignment with Federal Enterprise Architecture

| FEA Reference Model | Alignment |
|---|---|
| Business Reference Model | Health — Health Care Administration; Management of Government Resources — Program Monitoring |
| Data Reference Model | Entity registry with versioned records, controlled vocabularies (bucket, tier, status enumerations), and provenance on every verification |
| Application Reference Model | Layered web application: presentation (static SPA), application services (REST API), data management (relational store) |
| Technical Reference Model | Commodity cloud PaaS — managed application hosting, managed relational database, managed static hosting |
| Security Reference Model | Role-based access control, encryption in transit and at rest, audit logging, least privilege |

## 2.3 Design Methodology

**Agile/iterative** delivery with **code-first, evidence-based documentation**: this document was produced by inspecting the implementation, not by transcribing prior design intent. Where implementation could not be confirmed, the text says **VERIFICATION REQUIRED** rather than asserting a capability.

**Continuous verification.** The repository carries an automated test suite of **804 passing tests with 24 skipped and 0 failures**, executed against the codebase this document describes. Deployment gates additionally verify artifact shape and dependency integrity before release.

# 3. Architecture Design

## 3.1 System Context View

**Figure 1. System Context**

```
        ┌───────────────┐                    ┌────────────────────────┐
        │  HHS / ONC    │  entity dataset    │      8 user roles      │
        │    (ASTP)     │───────────────────►│  viewer → admin        │
        └───────▲───────┘                    └───────────┬────────────┘
                │                                        │ HTTPS (browser)
                │ reports, evidence,                     ▼
                │ audit exports          ┌───────────────────────────────┐
                │                        │   Azure Static Web Apps       │
                │                        │   Next.js static export       │
                │                        └───────────────┬───────────────┘
                │                                        │ REST / JSON / TLS
                │                                        ▼
        ┌───────┴────────────────────────────────────────────────────────┐
        │              Azure App Service (Linux) — FastAPI               │
        │   TEFCA ARC │ TEFCA Registry │ Bulletin │ Shared Services       │
        └───────┬──────────────────────────────┬────────────────────────┘
                │                              │
                ▼                              ▼
    ┌───────────────────────┐      ┌──────────────────────────────────┐
    │ Azure PostgreSQL      │      │  External sources (read-only)     │
    │ Flexible Server       │      │  NPPES · OIG LEIE · SAM.gov ·     │
    │ + SQLite (bulletin)   │      │  USPS v3 · Anthropic (advisory)   │
    └───────────────────────┘      └──────────────────────────────────┘
```

*Figure 1 shows the deployed system context: users reach a static front end on Azure Static Web Apps, which calls the FastAPI backend on Azure App Service. The backend persists to Azure PostgreSQL plus a separate SQLite store for bulletin data, and makes read-only outbound calls to the external sources.*

*Alt text: System context diagram. HHS/ONC supplies the entity dataset to the platform and receives reports, evidence and audit exports. Eight user roles reach the system over HTTPS through an Azure Static Web Apps front end running a Next.js static export, which calls a FastAPI backend on Azure App Service hosting four subsystems. The backend persists to Azure PostgreSQL plus a separate SQLite store for bulletin data, and makes read-only calls to external sources NPPES, OIG LEIE, SAM.gov, USPS v3 and Anthropic in advisory mode.*

## 3.2 Logical Architecture View

**Figure 2. Logical Architecture**

```
┌────────────────────────────────────────────────────────────┐
│                   DocuAction Platform                       │
├───────────────────────────┬────────────────────────────────┤
│      TEFCA ARC            │        TEFCA Registry           │
│      (app/Tefca/)         │     (app/tefca_registry/)       │
│                           │                                 │
│  External source verify   │  Internal rules & data quality  │
│  B1–B4 bucket classify    │  Entity management & versioning │
│  Sampling engine          │  Import pipeline & bridge       │
│  Report generation        │  Review rules, samples, SLA     │
│  Priority cases           │  Verification jobs              │
│  18 + 55 endpoints        │  22 + 21 endpoints              │
├───────────────────────────┴────────────────────────────────┤
│              Bulletin Intelligence (60 endpoints)           │
│           (app/bulletin_intelligence/) — SQLite store        │
├─────────────────────────────────────────────────────────────┤
│                      Shared Services                         │
│   Auth (9) │ Admin & RBAC (15) │ Audit │ Health (2)          │
│   app/core/security.py · app/api/admin_users.py              │
└─────────────────────────────────────────────────────────────┘
```

*Figure 2 shows the four logical subsystems and their endpoint counts. TEFCA ARC and TEFCA Registry are peers rather than layers — ARC verifies against external sources while Registry applies internal rules and data-quality checks — with Bulletin Intelligence and shared services beneath them.*

*Alt text: Layered block diagram of the platform. The top row splits into two side-by-side subsystems: TEFCA ARC under app/Tefca handling external source verification, bucket classification, sampling, reports and priority cases across 73 endpoints; and TEFCA Registry under app/tefca_registry handling internal rules, entity management and versioning, the import pipeline and bridge, review rules, samples and SLA, and verification jobs across 43 endpoints. Below them sits Bulletin Intelligence with 60 endpoints on a separate SQLite store, and beneath that a shared services layer providing authentication, admin and RBAC, audit and health endpoints.*

**Router registration** is performed in `app/main.py`, which mounts each subsystem router and applies a fail-soft loader so an optional module cannot prevent application start. The TEFCA routers are registered unconditionally.

## 3.3 Component Design

### 3.3.1 Authentication & Authorization

| Aspect | Detail |
|---|---|
| Purpose | Establish identity and enforce least privilege on every request |
| Key modules | `app/core/security.py` |
| Key elements | `ROLE_HIERARCHY`, `create_token_pair`, `get_current_user`, `require_role`, `_enforce_account_state`, `_token_revoked` |
| Endpoints | 9 authentication endpoints |
| Tables | `users` |
| Dependencies | `python-jose` (JWT), `bcrypt`, SQLAlchemy |
| Configuration | `SECRET_KEY`, `AZURE_AD_*` |

`require_role(minimum)` returns a dependency that compares numeric levels, admitting any role at or above the floor. It exposes `role_checker.minimum_role` so a route's effective gate can be asserted by tests without minting a token per role.

### 3.3.2 TEFCA ARC Review Engine

| Aspect | Detail |
|---|---|
| Purpose | Task 3/4/5 review execution, queues, findings and cycles |
| Key modules | `app/Tefca/routes.py`, `review_engine.py`, `qa_engine.py`, `qa_monitor.py` |
| Endpoints | `/api/v1/tefca/*` (18), `/api/tefca/*` (55) |
| Tables | `tefca_entities`, `tefca_reviews`, `tefca_findings`, `tefca_analyst_queue`, `tefca_review_cycles` |
| Dependencies | Verification pipeline, classification engine, report generator |

### 3.3.3 Verification Pipeline

| Aspect | Detail |
|---|---|
| Purpose | Verify entity attributes against authoritative sources and emit evidence |
| Key modules | `app/Tefca/validation_engine.py`, `app/tefca_registry/verification.py`, `entity_resolver.py`, `address_normalizer.py` |
| Behaviour | Concurrent source probes; Jaro-Winkler name similarity; USPS Pub 28 address normalization (USPS API **configured; zero production calls to date**); NPI format + Luhn validation; **fail-closed** — an unavailable source is recorded as unavailable, never clean |
| Tables | `tefca_verifications`, `tefca_verification_checks`, `tefca_evidence_records` |

### 3.3.4 Connector Framework

| Connector | Source | Auth | Status |
|---|---|---|---|
| `NPPESConnector` | NPPES NPI Registry v2.1 | key-less | CURRENT |
| `OIGLEIEConnector` | OIG List of Excluded Individuals/Entities | key-less | CURRENT |
| `SAMGovConnector` | SAM.gov Entity Management v3 | `SAM_GOV_API_KEY` | DEGRADED (upstream 404) |
| `PECOSConnector` | **NPPES-derived** (`npiregistry.cms.hhs.gov`) | key-less | CURRENT |
| `RCEDirectoryConnector` | **ONC-provided dataset loader** (`urn:docuaction:tefca/fhir/r4`) | n/a | CURRENT |
| `IQVIAOneKeyConnector` | IQVIA OneKey | `IQVIA_ONEKEY_API_KEY` | **PLANNED / DISABLED** |
| `SourceConnectorManager` | orchestration, circuit breaking, logging | — | CURRENT |

> [!WARNING] **PECOS is not a direct integration.** `PECOSConnector` queries the free, key-less CMS NPPES NPI Registry to confirm enrollment identity attributes. The PECOS payment-suspension feed requires COR provisioning and is therefore reported as `payment_suspension: None` — "not provided by this free source" — and never as a fabricated clean value.

> [!WARNING] **AGT does not query the RCE.** `RCEDirectoryConnector` is a loader for the ONC-provided dataset. Its `BASE_URL` is the URN `urn:docuaction:tefca/fhir/r4`, not a network endpoint. Until the ONC dataset is in place, `is_running_mock()` returns true and a bundled development dataset is served flagged `data_source="MOCK"`; the validation engine refuses to auto-classify MOCK-sourced entities as Bucket 1. The `RCE_DIRECTORY_API_KEY` environment variable is **vestigial** — **VERIFICATION REQUIRED** for its removal.

> [!WARNING] **IQVIA OneKey is not a current capability.** The connector returns `unavailable` without an API key ("pending ODC"). See `docs/IQVIA_REMOVAL_EDITS.md`. Do not represent IQVIA as an active data source.

### 3.3.5 Sampling Engine

| Aspect | Detail |
|---|---|
| Purpose | Cochran sample-size calculation and reproducible sample drawing |
| Key module | `app/tefca_registry/sampling_engine.py` |
| Design note | z-values for common two-sided confidence levels are **looked up rather than computed**, so a report can state exactly which confidence level, margin and population produced a given sample |
| Persistence | `review_samples` → `sample_entities` (membership retained for re-examination) |
| Endpoints | `POST /api/tefca/arc/samples` (contributor), `GET .../samples`, `.../{id}`, `.../{id}/stats` (viewer) |

### 3.3.6 B1–B4 Classification Engine

| Aspect | Detail |
|---|---|
| Purpose | Assign discrepancy bucket and finding codes |
| Key modules | `app/tefca_registry/bucket_classifier.py`, `app/Tefca/models.py` (`BucketClassification`, `BucketLabel`) |
| Values | `BUCKET_1`="1", `BUCKET_2`="2", `BUCKET_3`="3", `BUCKET_4`="4" |
| Guard | MOCK-sourced entities are ineligible for automatic Bucket 1 |
| Override | Senior analysts may reclassify (`PATCH /api/v1/tefca/queue/{record_id}/classify`) |

### 3.3.7 Report Generator

| Aspect | Detail |
|---|---|
| Key modules | `app/Tefca/reporting.py`, `report_renderer.py`, `app/tefca_registry/report_generator.py`, `report_excel.py` |
| Formats | PDF, DOCX, CSV, XLSX, HTML |
| Cadences | weekly (qalead), bi-weekly (qalead), quarterly (program_manager), final (program_manager) |
| Dependencies | `python-docx`, `openpyxl` |

### 3.3.8 Review Cycle Management

`CycleType` maps directly to contract tasks — `TASK3_RETROSPECTIVE`, `TASK4_ONGOING`, `TASK5_PRIORITY` — making cycle records self-describing for traceability. Cycle creation is restricted to `program_manager`. Tables: `tefca_review_cycles`, `review_cycles`.

### 3.3.9 Priority Review / SLA Engine

| Aspect | Detail |
|---|---|
| Key module | `app/tefca_registry/sla.py` |
| Bands | `days_remaining < 0` → `overdue`; `≤ 2` → `at_risk` |
| Design note | A review due today has 0 days remaining and is **at_risk, not overdue**; it becomes overdue only once the due moment has passed |
| Constants | `AT_RISK_DAYS = 2`, `OVERDUE = "overdue"`, `AT_RISK = "at_risk"` |
| Tables | `tefca_priority_cases` |

### 3.3.10 Entity Import Pipeline

CSV (`csv_import.py`) and FHIR R4 bundle (`fhir_import.py`) ingestion. Every upload is scanned before processing, hashed with SHA-256, and recorded in import history **even when zero rows import**. Invalid rows are rejected individually with row number, field and reason — never silently dropped and never failing the whole file. Tables: `tefca_import_batches`, `tefca_import_history`.

### 3.3.11 Entity State Machine

`app/tefca_registry/state_machine.py` and `lifecycle.py`. Lifecycle states: `draft`, `pending_verification`, `active`, `suspended`, `inactive`. Review statuses (`EntityStatus`): `PENDING_REVIEW`, `IN_REVIEW`, `REVIEWED_COMPLETE`, `CORRECTIVE_ACTION_OPEN`, `ESCALATED`. Transitions not permitted by the machine are rejected with a reason.

### 3.3.12 TEFCA Registry Module

Normalized entity registry with identifiers, endpoints, relationships, versions and findings as child tables; verification jobs; versioned `review_rules` with change history (authoring restricted to `admin`); status transitions restricted to `reviewer`; entity deletion restricted to `admin`.

### 3.3.13 Bulletin Intelligence Module

Regulatory intelligence collection, clustering, scoring, quality gating and briefing generation across 60 endpoints. **Uses a separate SQLite datastore (`BULLETIN_DB_PATH`) with raw `CREATE TABLE IF NOT EXISTS` statements, independent of the application's PostgreSQL ORM layer.** Tables: `bulletin_articles`, `bulletin_briefings`, `bulletin_run_log`, `bulletin_source_outcome`, `bulletin_source_registry`, `bulletin_delivery_log`, `bulletin_audit_log`.

> [!WARNING] **All 60 Bulletin endpoints are public (unauthenticated) by design in the current configuration.** `BULLETIN_AUTH_ENABLED` exists and `guard()` is wired into the collect/send routes, but it is a **deliberate no-op** while the flag is false — its default. Enabling the flag activates `require_role` on those routes without further code change.

### 3.3.14 AI Entity Resolution (Advisory)

| Aspect | Detail |
|---|---|
| Location | `app/tefca_registry/ai_client.py`, `entity_resolver.py`, `review_service.py` — **there is no `app/ai/` directory** |
| Default | **Disabled** (`AI_ENTITY_RESOLUTION`) |
| Modes | disabled / advisory / production; unrecognized values **fail closed** to disabled |
| Egress allowlist | `PUBLIC_FIELDS = ("name", "address", "npi", "entity_type", "state", "tefcaid")` — enforced at `entity_resolver.py:205` |
| Human review | Always required; AI output is advisory and never final |
| Fallback | Deterministic matching when AI is unavailable or disabled |
| Provider | Anthropic Claude Messages API (`ANTHROPIC_API_KEY`) |

> [!WARNING] No PHI is transmitted to any AI provider. Only the six allowlisted public fields ever leave the system, filtered by an explicit allowlist rather than a denylist, so a newly added entity field cannot leak by default.

### 3.3.15 Audit & Compliance Framework

Append-only audit records written on administrative actions, state transitions, imports (with SHA-256 file hash) and authentication events. Actor identity, target, action, details and timestamp are recorded. Tables: `audit_logs`, `tefca_reg_audit_log`, `tefca_connector_logs`.

> [!WARNING] **Audit records are append-only, not immutable.** The application performs no update or delete against audit rows. They are **not** cryptographically hash-chained, not digitally signed, and not stored on write-once media. A database administrator with direct access could modify them. Do not represent these records as tamper-proof or immutable.

### 3.3.16 Admin & User Management

15 endpoints under `/api/admin`, all gated by `require_admin`. Capabilities: user listing, invitation with secure set-password link (never a plaintext password by email), creation, role assignment (single and **bulk, audited per grant**), module permission assignment, approval/rejection of self-registered accounts, activation toggling, password reset, deletion, and per-user activity trail. Only super administrators (email in `ADMIN_EMAILS`) may grant or modify the admin role, and an administrator cannot remove their own admin role.

**Role-based default module grants.** New accounts receive a default module set determined by role (`DEFAULT_MODULES_BY_ROLE`): non-admin roles receive `tefca_review` and `bulletin_intelligence`; `admin` receives all 15 modules. An explicit permission list always overrides the default, and the default is applied on approval only when an account has no grant at all — so it can never re-widen an account an administrator has deliberately narrowed.

> [!WARNING] **Privilege is never derived from an email address or domain.** Email-based automatic role elevation was deliberately removed from this system and must not be reintroduced (least privilege / authorized personnel only, per HHSAR 352.204-71 and FAR 52.212-4). The bulk role-assignment endpoint exists precisely so that onboarding a group is an *authorized, attributable* administrative act rather than an inferred one. A regression test asserts that no role assignment is derived from an email address anywhere on the authentication surface.

## 3.4 Hardware / Infrastructure Architecture

| Environment | Backend App Service | Resource group | Frontend SWA | Database |
|---|---|---|---|---|
| Development | `docuaction-dev` | `rg-docuaction-dev` | `docuaction-frontend-dev` | Azure PostgreSQL Flexible Server (dev) |
| Production | `Docuaction` | `rg-docuaction-prod` | `docuaction-frontend` | Azure PostgreSQL Flexible Server (prod) |

Isolation is non-negotiable and forms part of the control baseline: separate App Service applications (no shared compute), separate PostgreSQL servers and databases (production data never resides on a dev server), separate Microsoft Entra ID registrations, **no shared secrets**, and separate allowed hosts/origins.

**No failover environment exists.** The system is in pre-production demonstration mode; a warm standby has not been provisioned and is not currently a contract requirement. Recovery relies on redeploying a retained artifact generation (Section 6.2) against the managed database's platform backups. **VERIFICATION REQUIRED** for formal RTO/RPO targets — none are documented.

## 3.5 Software Architecture

| Layer | Technology |
|---|---|
| Backend language/runtime | Python 3.12 on Azure Linux |
| API framework | FastAPI |
| WSGI/ASGI server | Gunicorn with Uvicorn workers, 600s timeout |
| ORM | SQLAlchemy (async) |
| Database driver | asyncpg |
| Frontend | Next.js App Router, React, static export |
| Authentication | JWT via `python-jose`; bcrypt password hashing |
| Document generation | `python-docx`, `openpyxl` |
| Name matching | Jaro-Winkler / Levenshtein similarity |
| XML parsing | `defusedxml` for untrusted remote feeds |
| HTTP client | `httpx.AsyncClient` with pooling |
| AI (optional) | Anthropic Claude Messages API |

> [!WARNING] `defusedxml` is a security dependency, not a convenience one. It is used to parse XML from feeds the system does not control. The call sites fall back to the standard-library parser if it is absent, which means its removal degrades silently to an unhardened parser rather than failing loudly. Deployment gates verify its presence in every artifact.

## 3.6 Security Architecture

### 3.6.1 Authentication Model

Signed JWTs carrying `sub`, `role`, `exp`, `iat`, `type` and `jti`. Access tokens: **24 hours for administrators, 15 minutes for all other roles**. Refresh tokens: **7 days**. Tokens issued before a user's `tokens_revoked_at` epoch are rejected, so logout, disablement and password reset terminate outstanding sessions. Login is throttled per IP with account lockout after repeated failures, and performs exactly one bcrypt comparison whether or not the account exists, so response timing cannot reveal account existence. Optional Microsoft Entra ID SSO maps group claims to platform roles.

### 3.6.2 Authorization Model

Eight-level hierarchy evaluated **numerically, never by role name**:

```
  viewer 1 · contributor 2 · manager 3 · reviewer 4
  senior_analyst 5 · qalead 6 · program_manager 7 · admin 8
```

Guards are declared per route; router-level dependencies apply a floor to every route beneath them. Account state (active / disabled / pending approval) and token revocation are re-checked on **every** authenticated request, not only at login.

**Effective role floors across all 325 endpoints:**

| Floor | Endpoints |
|---|---|
| `authenticated` (no role floor; includes 14 `/api/admin` routes gated by `require_admin`) | 120 |
| `viewer` | 76 |
| `PUBLIC` (no authentication dependency) | 72 |
| `admin` | 25 |
| `reviewer` | 13 |
| `program_manager` | 6 |
| `contributor` | 5 |
| `senior_analyst` | 4 |
| `qalead` | 4 |

> [!WARNING] A router-level floor is a **ceiling on permissiveness** for every route beneath it. A floor set too high silently overrides each endpoint's own declaration and can render an entire module unreachable for all roles that can actually be assigned — a configuration defect invisible to tests that only check the deny direction. Verification therefore asserts both directions: that each role is denied what it should not reach, **and admitted to what it should**.

### 3.6.3 Data Protection

TLS 1.2+ in transit; Azure platform encryption at rest; secrets supplied by environment/Key Vault reference and never committed; AI egress restricted to `PUBLIC_FIELDS`; no PHI to AI providers.

### 3.6.4 Audit Architecture

Append-only at the application layer. Every state transition, administrative action, import (with SHA-256 file hash) and authentication event is recorded with actor, target, action, detail and timestamp. **Not cryptographically immutable** — see the warning in 3.3.15.

### 3.6.5 Input Validation

Null-byte rejection middleware returns 422 on all routes. Uploaded files are scanned for script injection before processing. NPI values are validated for format and Luhn check digit. Request bodies are validated by typed Pydantic models.

### 3.6.6 AI Security Controls

Disabled by default; three modes with fail-closed handling of unrecognized values; egress allowlist; human review always required; deterministic fallback. **Anthropic BAA signed. OpenAI BAA requested — VERIFICATION REQUIRED for its current status.**

## 3.7 Communication Architecture

REST over HTTPS with JSON payloads. Outbound integrations:

| Source | Endpoint | Auth | Notes |
|---|---|---|---|
| NPPES | `npiregistry.cms.hhs.gov/api/` v2.1 | key-less | Primary identity source |
| OIG LEIE | LEIE exclusion data | key-less | Exclusion status |
| SAM.gov | `api.sam.gov` v3 | API key | Upstream 404s observed |
| USPS | `apis.usps.com` v3 | OAuth 2.0 | Address standardization — **configured; zero production calls to date** |
| Anthropic | `api.anthropic.com` messages | API key | Advisory, disabled by default |
| ONC dataset | `urn:docuaction:tefca/fhir/r4` | n/a | Local dataset loader, not a network call |

## 3.8 Performance Architecture

Concurrent (not serial) source probes per entity; pooled `httpx.AsyncClient` connections; circuit breaker on connector failures with retry and backoff for transient errors; source response caching (`tefca_source_cache`) to suppress redundant upstream calls; per-client rate limiting. A bulletin history payload optimization reduced a response from approximately 11.8 MB to 87 KB.

> [!WARNING] **VERIFICATION REQUIRED — no load or capacity test is on record.** The response-time, pipeline-duration and 50-concurrent-user figures in RD §4.2 are design targets, not measured results. They must not be presented to the Government as demonstrated performance until a load test is executed and reported.

# 4. System Design

## 4.1 Business Requirements Traceability

Every requirement in RD Sections 3 and 4 traces to a design element in this document via the RTM at **RD Appendix E**, which carries the `Design Element (SDD §)` and `System Component` columns. Reverse traceability: each component in Section 3.3 above names the requirements it satisfies through the RTM's component column.

## 4.2 Database Design

Two relational stores plus a separate bulletin datastore:

1. **PostgreSQL (system of record)** — 120 tables total, of which 45 are contract-relevant: TEFCA ARC (11), TEFCA Registry (17), shared core (6), case management (6), plus supporting tables. Approximately 75 belong to non-contract legacy modules and are out of scope.
2. **SQLite (bulletin)** — 7 tables created by raw `CREATE TABLE IF NOT EXISTS` at startup, independent of the ORM.

Column-level detail for all contract-relevant tables is provided in **Appendix E**, generated directly from the model definitions.

**Entity-relationship overview:** see RD Appendix D.

> [!WARNING] **Known architectural issue — two-table entity split.** Entity data lives in both `tefca_entities` (ARC) and `tefca_reg_entities` (Registry), with a bridge that syncs import into the registry on CSV upload. This duplication is documented design debt slated for future unification. Consumers must know which store they are reading; the two are not interchangeable.

## 4.3 Data Conversion / Migration

Entity population data is supplied by ONC and ingested via CSV or FHIR R4 bundle. `TEFCA_ENTITY_DATA_KEY` signals that the ONC-provided dataset is in place; `is_running_mock()` returns `True` in its absence and the system serves a bundled development dataset flagged `data_source="MOCK"`, which the validation engine refuses to auto-classify as Bucket 1. Schema evolution is applied at application startup using idempotent `ADD COLUMN IF NOT EXISTS` statements, so an older database converges without a separate migration step.

## 4.4 Application Program Interfaces

**Total: 325 endpoints across 15 modules.**

| Module | Endpoints |
|---|---|
| Bulletin Intelligence | 60 |
| Other/Core | 56 |
| TEFCA Dashboard/QA/Reports | 55 |
| Case Management | 22 |
| TEFCA Registry | 22 |
| TEFCA ARC Review (Tasks 3–5) | 21 |
| TEFCA ARC Review (`/api/v1/tefca`) | 18 |
| Enterprise | 17 |
| Admin & User Management | 15 |
| Decision Intelligence | 12 |
| Authentication | 9 |
| Healthcare Claims | 9 |
| Intelligence | 4 |
| Validation Queue | 3 |
| Health/Status | 2 |

**Representative endpoint contracts:**

| Method | Path | Role | Request | Response | Errors |
|---|---|---|---|---|---|
| POST | `/api/auth/login` | PUBLIC | `{email, password}` | `{access_token, user}` | 401 invalid, 403 unverified/pending/disabled, 429 throttled |
| GET | `/api/tefca/dashboard/summary` | viewer | — | aggregate metrics | 401, 403 |
| GET | `/api/tefca/registry/entities` | viewer | query filters | entity page | 401, 403 |
| POST | `/api/tefca/registry/import/csv` | contributor | multipart file | batch result + row errors | 400 invalid, 401, 403, 422 |
| POST | `/api/tefca/registry/entities/{id}/verify` | contributor | `VerifyOptions` | verification result | 401, 403, 404 |
| PATCH | `/api/v1/tefca/queue/{id}/classify` | senior_analyst | `{bucket, rationale}` | updated record | 401, 403, 404 |
| POST | `/api/tefca/reports/weekly` | qalead | cycle parameters | report artifact | 401, 403, 422 |
| POST | `/api/tefca/reports/final` | program_manager | cycle parameters | report artifact | 401, 403, 422 |
| GET | `/api/admin/users` | admin | — | user list | 401, 403 |
| POST | `/api/admin/users/bulk-role` | admin | `{emails[], role}` | `{updated[], skipped[]}` | 400 invalid role/list, 401, 403 |

The complete inventory of all 325 endpoints with effective role floors is provided in **Appendix D**.

## 4.5 User Interface Design

**75 pages total: 26 TEFCA pages, 1 Bulletin page, 48 other.** Next.js App Router with static export.

Role-based visibility is applied in the application shell: an `ALWAYS_ALLOWED` module list defines areas every authenticated user may reach, per-user `allowed_modules` grants additional areas, and administrators bypass the check entirely. **The shell controls visibility only** — it is not an authorization boundary. What a role may *do* inside an area is decided server-side by `require_role` on each endpoint, so a viewer who opens a TEFCA page still receives 403 on every write.

**Key UI flow:**

**Figure 3. Primary User Flow**

```
  Login ──► Dashboard ──► Entity Queue ──► Decision Workspace
                                                │
                                                ▼
                                    Review ──► Approve / Escalate
```

*Figure 3 shows the principal operator path from sign-in to disposition. Each step is gated server-side by the role floor on the underlying endpoint, so reaching a page confers no authority to act on it.*

*Alt text: Linear user flow from login to dashboard to entity queue to decision workspace, then to review, ending in approve or escalate.*

## 4.6 System Performance

Design targets are stated in RD §4.2. Measured characteristics currently on record: the full automated test suite executes in approximately 10–12 minutes; a bulletin collection run processes on the order of 131 articles in roughly 6 minutes; a bulletin history response was reduced from ~11.8 MB to ~87 KB. **All other performance figures are targets, not measurements — VERIFICATION REQUIRED.**

## 4.7 Section 508 Compliance

Remediation is in progress and visible in the codebase: contrast ratios raised on the application shell and administrative surfaces, accessible names and ARIA state on icon-only controls, modal focus management with Escape-to-close, and true heading structure.

**No formal conformance testing has been performed.** There is no automated accessibility scan (axe, pa11y) in the test suite and **no VPAT has been produced**. Section 508 conformance is therefore **in progress and unverified** — it must not be represented as achieved.

# 5. Verification and Validation

## 5.1 Test Architecture

**804 tests passing, 24 skipped, 0 failures.** Skips are explicit and self-describing (no database reachable; a feature flag deliberately off), so a skip never reads as a pass.

Registered pytest markers (`pytest.ini`, enforced with `--strict-markers` so a typo fails rather than silently selecting nothing):

| Marker | Purpose |
|---|---|
| `regression` | Must pass on every deploy |
| `qa_defect` | Confirmed QA defects (August 2026 report) |
| `security` | Security-specific regression tests |
| `bulletin` | Bulletin module regression tests |
| `e2e` | End-to-end workflow tests |

An end-to-end workflow test exercises the Monday operational sequence against real NPIs.

## 5.2 Test Coverage by Component

| Component | Representative coverage |
|---|---|
| Authentication & RBAC | Role hierarchy ordering; both-direction endpoint authorization per role; forged-token rejection; account-state enforcement; no privilege derived from email domain |
| Verification pipeline | NPI Luhn validation; address normalization across three layers; entity resolution; deduplication |
| Connectors | SAM.gov failure diagnosis; data provenance; QA verification |
| Import | CSV/legacy import paths; entity import defects; upload scanning |
| Review & cycles | Review cycles; state machine; lifecycle; priority reviews; review reports |
| Bulletin | Sources, Excel export, quality fixes, enhancements, deduplication, date filtering |
| Security | Injection, rate limiting, security headers, client IP handling, regression security suite |

## 5.3 QA Defect History

The August 2026 QA cycle identified **17 defects across 5 modules; all 17 are fixed**, with regression tests retained under the `qa_defect` marker. A subsequent P0 — TEFCA reachable only by administrators — was traced to three compounding defects (an unassignable role vocabulary, a router-level floor above every assignable non-admin role, and a frontend module-visibility omission) and fixed with both-direction role verification added to prevent recurrence.

# 6. Deployment Architecture

## 6.1 Environment Topology

See Section 3.4. Development and production are fully isolated in compute, database, identity, secrets and origins.

## 6.2 Deployment Process

1. Build the artifact from a **known-good base**, replacing only the application tree; dependencies are carried byte-identical from the artifact currently in production.
2. **Gate the artifact before deploying**: zero backslash entry names (a .NET-written zip produces path names Linux does not treat as separators); exactly the expected top-level roots; `app/main.py` present; `defusedxml` present; the intended change actually present in the file.
3. Deploy with `--clean true`. `--clean` wipes the target **before** unpacking, so a malformed artifact is an outage rather than a failed no-op — hence step 2.
4. Issue an **explicit restart** after every deploy.
5. Verify server-side. A CLI error such as `RemoteDisconnected` does **not** mean the deploy failed — the CLI lost its polling connection while the server continued. **Never retry on a CLI error**; a retry starts a second concurrent build that collides with the first. Query deployment status instead, and confirm the deployed file content directly.
6. **Two rollback generations are retained** (`prod-deploy.prev.zip`, `prod-deploy.prev2.zip`). Rollback is a redeploy of the previous generation with `--clean true` followed by a restart.

**Frontend.** `NEXT_PUBLIC_*` values are inlined at build time, so one artifact carries one API URL and cannot serve both environments — each environment is built separately and each build is gated to confirm the other environment's hostname does not appear in it.

## 6.3 Environment Variables

96 variables are referenced. Values are never stored in source. By category:

| Category | Variables |
|---|---|
| Core / platform | `ENVIRONMENT`, `APP_VERSION`, `APP_URL`, `APP_BASE_URL`, `API_PUBLIC_URL`, `DATABASE_URL`, `UPLOAD_DIR`, `STORAGE_PROVIDER`, `S3_BUCKET`, `DATA_RETENTION_DAYS` |
| Authentication & identity | `SECRET_KEY`, `AZURE_AD_CLIENT_ID`, `AZURE_AD_CLIENT_SECRET`, `AZURE_AD_TENANT_ID`, `AZURE_AD_DEFAULT_ROLE`, `AZURE_AD_POST_LOGIN_REDIRECT`, `BLOCK_DISPOSABLE_EMAILS` |
| TEFCA | `TEFCA_ENTITY_DATA_KEY`, `TEFCA_ALERT_FROM`, `TEFCA_ALERT_RECIPIENTS`, `RCE_DIRECTORY_API_KEY` *(vestigial — VERIFICATION REQUIRED)* |
| AI configuration | `AI_ENTITY_RESOLUTION`, `AI_ENTITY_RESOLUTION_MODEL`, `AI_ENTITY_RESOLUTION_MAX_TOKENS`, `AI_ENTITY_RESOLUTION_TIMEOUT`, `ANTHROPIC_API_KEY` |
| Connector keys | `SAM_GOV_API_KEY`, `USPS_CLIENT_ID`, `USPS_CLIENT_SECRET`, `USPS_API_USER_ID`, `USPS_ENV`, `USPS_TIMEOUT`, `USPS_TIMEOUT_S`, `USPS_CACHE_TTL_S`, `USPS_DAILY_BUDGET`, `IQVIA_ONEKEY_API_KEY` *(disabled)* |
| Bulletin configuration | `BULLETIN_DB_PATH`, `BULLETIN_AUTH_ENABLED`, `BULLETIN_RATE_LIMIT_ENABLED`, `BULLETIN_RATE_MAX_PER_HOUR`, `BULLETIN_AUDIT_ENABLED`, `BULLETIN_AUDIT_DIR`, `BULLETIN_SEND_FROM`, `BULLETIN_ALERT_EMAIL`, `BULLETIN_MIN_ARTICLES`, `BULLETIN_MIN_PUBLISHERS`, `BULLETIN_MAX_DUPLICATE_PCT`, `BULLETIN_STRICT_FCC_GATE`, `BULLETIN_EDITORIAL_STRICT`, and 20 further tuning flags |
| Email delivery | `SENDGRID_API_KEY`, `EMAIL_FROM`, `EMAIL_FROM_NAME`, `MAIL_FROM`, `MAIL_FROM_NAME`, `EMAIL_TEMPLATE_VERSION` |
| Feature flags | `ENABLE_DEMO`, `ENABLE_SCHEDULER`, `ENABLE_QA_MONITOR`, `QA_MONITOR_INTERVAL_MIN`, `QA_BASE_URL` |
| Third-party intelligence | `PERIGON_*` (9), `NEWSAPI_KEY`, `NEWSDATA_API_KEY`, `GOVINFO_API_KEY`, `CONGRESS_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `YOUTUBE_API_KEY`, `TAVILY_API_KEY`, `PERPLEXITY_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `TALKWALKER_FEED_URLS` |

## 6.4 Production Delta

The production environment runs an earlier build. See **RD Appendix F** for the complete difference list. Summary: 29 modified application files and 7 absent files; the RBAC-critical subset has been deployed, and production currently enforces a `reviewer` floor on TEFCA reads where development enforces `viewer`.

# Appendices

## Appendix A — Design Approval Signature Page

The undersigned acknowledge they have reviewed the **DocuAction TEFCA ARC Platform System Design Document, Version 1.0** and agree with the information presented within this document.

| Role | Name | Signature | Date |
|---|---|---|---|
| AGT Project Manager | | | |
| AGT Technical Lead | | | |
| ONC Contracting Officer's Representative | | | |
| ONC Program Manager | | | |

## Appendix B — References

See Section 1.4 and RD Section 1.4.

## Appendix C — Key Terms / Glossary

See RD Section 1.3 and SDD Section 1.3.

<!-- INCLUDE: appendix_api.md -->

<!-- INCLUDE: appendix_schema.md -->

# Appendix F — Connector Interface Specifications

| Connector | Base | Version | Auth | Failure behaviour | Status |
|---|---|---|---|---|---|
| `NPPESConnector` | `https://npiregistry.cms.hhs.gov/api/` | 2.1 | none | Retry with backoff; reports `unavailable` with reason | CURRENT |
| `OIGLEIEConnector` | OIG LEIE dataset | — | none | Fail-closed; exclusion never inferred absent | CURRENT |
| `SAMGovConnector` | `https://api.sam.gov` | v3 | `SAM_GOV_API_KEY` | Reports `unavailable`; upstream 404s observed | DEGRADED |
| `PECOSConnector` | `https://npiregistry.cms.hhs.gov/api/` | 2.1 | none | `payment_suspension: None` when unavailable — never a fabricated clean value | CURRENT (NPPES-derived) |
| `RCEDirectoryConnector` | `urn:docuaction:tefca/fhir/r4` | R4 | n/a | Serves MOCK-flagged bundled dataset until ONC data present; MOCK never auto-Bucket-1 | CURRENT (ONC dataset loader) |
| `IQVIAOneKeyConnector` | `https://api.iqvia.com/onekey/v1/practitioners` | v1 | `IQVIA_ONEKEY_API_KEY` | Returns `unavailable` — "pending ODC" | **PLANNED / DISABLED** |
| `SourceConnectorManager` | — | — | — | Orchestrates concurrent probes, circuit breaking, connector logging | CURRENT |

**Common contract.** Every connector returns a `SourceResult` carrying the source key, payload, query parameters and source API version, so each verification records exactly what was asked and of which version. `SourceResult.unavailable(...)` is a first-class outcome distinct from a negative finding — the design refuses to let "we could not check" and "we checked and it was clean" collapse into the same value.

---

*End of System Design Document.*
