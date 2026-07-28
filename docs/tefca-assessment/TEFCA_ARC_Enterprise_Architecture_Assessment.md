# TEFCA ARC — Enterprise Architecture Assessment
### Independent Federal Enterprise Architecture Review Board · definitive review-board record

**Prepared:** 2026-07-09
**Consolidates & supersedes (as the authoritative version):** `TEFCA_ARC_Architecture_and_UX_Assessment.md`, `TEFCA_ARC_Product_Blueprint.md`, `TEFCA_ARC_Master_Product_Blueprint.md` (retained for history).
**Scope guardrails honored:** No code, backend, API, database, authentication, scheduler, deployment, or infrastructure was modified or discussed; no Dev-vs-Production or Azure discussion. **Assessment only. Phase 9 (UI/UX modernization) is deferred until AGT approves this blueprint.**

### Evidence-tagging (never mixed)
`[VERIFIED]` observed in source (`app/Tefca/*`) or the live app · `[INFERRED]` logic-based deduction from verified evidence · `[RECOMMENDED]` proposed future design (not present, not a requirement). Public-domain ecosystem facts are cited to sources.

---

## PHASE 1 — Evidence base (what was read) & the one blocking gap

**Read in full for this review `[VERIFIED]`:** data model (`models.py`), API surface (`routes.py`, ~60 endpoints), and the **business-logic engines** — `validation_engine.py`, `review_engine.py`, `connectors.py`, `reporting.py`, `report_renderer.py`, `qa_engine.py`, `mock_data.py`, plus the `/methodology` and `/discrepancy-taxonomy` payloads — and the live application UI.

### 🔴 Contract-identity finding (VERIFIED — action for AGT)
- **The TEFCA code consistently identifies the award as `[VERIFIED]` `Contract 7571MN26F80064`, contractor Alliance Global Tech, Inc. (AGT)** — stamped in every engine module header, the HTTP User-Agent (`DocuAction-TEFCA/6.0`), report footers ("CONFIDENTIAL … Contract 7571MN26F80064"), and the `/methodology` payload.
- **The TEFCA UI displays `7571MN26Q00027`** — which appears **only** in FCC Bulletin files and is the **FCC** solicitation number `[VERIFIED]`. **This is a data-integrity defect in the UI, not the real TEFCA contract number.** AGT should correct the displayed number to `7571MN26F80064` (or confirm the correct identifier).

**Still missing (blocks contract traceability):** the **RFQ, Award, SOW/PWS, amendments, Q&A, evaluation criteria, deliverables schedule, acceptance criteria, AGT Technical Proposal, and the AGT "D2 Review Methodology and Control Framework" document.** None are in the repository or attached. **I do not fabricate them.** However, the *implemented* D2 methodology is served live at `GET /methodology` and is summarized below as `[VERIFIED]` — the document itself is still required for certification (see Deliverable 2).

---

## PHASE 2 — TEFCA ecosystem (public-domain context; the product should reflect this business process)

`[VERIFIED — public sources]`
- **TEFCA** (Trusted Exchange Framework and Common Agreement) is the ONC/ASTP framework for nationwide EHI exchange across health information networks. **The Sequoia Project is the Recognized Coordinating Entity (RCE)** under a 5-year ONC contract (awarded Aug 2023); it develops/maintains the **Common Agreement**, which every **QHIN** signs and whose terms **flow down to Participants and Subparticipants**. There are **>21,000 organizations live on TEFCA** representing **>96,000 connections** — consistent with the platform's coded population **N=94,231** `[VERIFIED: code]`.
- **QHIN lifecycle:** application → onboarding → designation (rigorous, ~12 months) → signs Common Agreement → operates and connects Participants/Subparticipants.
- **Authoritative validation sources the platform uses** `[VERIFIED: connectors.py]`:
  - **NPPES** (NPI Registry, CMS/HHS) — provider identity/enumeration.
  - **PECOS** (Medicare enrollment, CMS) — enrollment/screening; monitors adverse actions.
  - **OIG-LEIE** (HHS-OIG List of Excluded Individuals/Entities) — program exclusions.
  - **SAM.gov** (GSA) — federal registration/debarment.
  - **RCE Directory** (Sequoia FHIR R4) — the directory of entities under review.
  - **IQVIA OneKey** — commercial provider-hierarchy reference.
- **Program-integrity framing** `[VERIFIED — public sources]`: **CMS One PI (One Program Integrity)** is CMS's fraud/waste/abuse analytics "one-stop shop" over an Integrated Data Repository; PECOS continuously screens/validates providers and monitors adverse actions. The TEFCA ARC review protocol is analogous in spirit — a **federal quality/integrity review** that reconciles directory-submitted attributes against authoritative sources and classifies discrepancies for COR determination.
- **Business-process implication `[INFERRED]`:** the UI should be organized around the **federal quality-review lifecycle** (sample → validate → classify → evidence → disposition → escalate → report → COR determination), not around technology modules.

*Sources:* [ONC TEFCA](https://healthit.gov/policy/tefca/) · [Sequoia RCE](https://rce.sequoiaproject.org/) · [Common Agreement](https://rce.sequoiaproject.org/common-agreement/) · [ASTP/Sequoia continuation](https://www.healthcareitnews.com/news/astp-continues-sequoia-project-tefca-implementation) · [CMS One PI](https://security.cms.gov/pia/one-program-integrity) · [CMS One PI leaflet](https://www.cms.gov/files/document/dasg-leaflet-one-pi.pdf)

---

## PHASE 3 — Current product review (per surface)

| Surface | Purpose / objective | Assessment |
|---|---|---|
| **Overview** `[VERIFIED]` | Program landing: KPIs, 4-bucket distribution, per-QHIN accuracy (10 shown; code defines **11 QHINs**), 3-tier routing, 6-source status (4 live/2 mock) | Strong content & honest MOCK/Live labels; **weak:** single dense screen for all roles; **demo-data inconsistencies** (21,847 vs 21,086 vs 21K; T2 3,091 vs 3,030); dual-nav (rail + in-page tabs) duplicating destinations; static charts (no drill/filter/time); **wrong contract number displayed**; no "as-of cycle" context |
| **Review Cycles / Sampling** `[VERIFIED backend]` | Task 3/4/5 cycles; Cochran 95%-CI (N=94,231→n=383) | Backend rigorous; UI needs burn-down/aging + sample-provenance |
| **Entity Reviews** | Per-entity 5-element evidence | **Core gap:** no true 6-source side-by-side workbench surfaced |
| **Validation Queue** | T2/T3 analyst queue | Appears in two nav groups — IA duplication |
| **Priority Reviews** | Task-5 COR cases (~20/mo) | Needs SLA/deadline + escalation timeline (SLA targets exist in code) |
| **Findings** | Discrepancy ledger | Needs faceting (QHIN/connector/bucket/severity/cycle) + export |
| **Reports** | 7 report types, PDF/DOCX/CSV, evidence-gated | Backend strong; UI needs a first-class library w/ provenance + submission state |
| **QA Operations** | 6-dimension QA program | Backend very rich; UI under-surfaces the QA scorecard/cockpit |
| **Connectors** | 6-source health/uptime | Good candidate for uptime/latency/key-status panel |
| **Analytics / Trust Center** | trends / governance | Overlaps; audit/chain-of-custody data exists but isn't surfaced |

Cross-cutting `[VERIFIED]`: two competing navigation systems; duplicate destinations; no role-based landing; flagship data inconsistencies; closed-loop workflow not visible even though fully implemented.

---

## PHASE 4 — Navigation vs. the ten roles

Roles evidenced in code `[VERIFIED]`: `reviewer`, `senior_analyst`, `program_manager`, `qalead`, admin (observed); COR referenced (`cor_reference`, `PENDING_COR`, priority-create). Auditor / Security Officer / Executive are `[INFERRED]` (no distinct role guard).

| Role | Current nav supports? | Assessment |
|---|---|---|
| Executive | 🟡 partial (Overview) | No dedicated exec landing `[RECOMMENDED]` |
| COR | 🟡 partial | Priority + reports exist; no COR oversight landing `[RECOMMENDED]` |
| QA Manager (`qalead`) | 🟡 | QA Operations page exists; not a cockpit |
| Program Manager | 🟡 | Cycles/Reports exist; no delivery burn-down |
| Senior Reviewer / SME | 🟡 | T3 queue exists; no escalation landing |
| Reviewer | 🟡 | Queue exists; no "My Work" landing or workbench |
| Analyst | (≈reviewer) | same as reviewer |
| Auditor | 🔴 | Audit data exists (`tefca_qa_audit`, source cache) but no auditor view `[RECOMMENDED]` |
| Administrator | ✅ | Users & Access present |
| Security Officer | 🔴 | No distinct surface `[RECOMMENDED]` |

**Assessment (no redesign):** the current single, dual-system nav does **not** provide role-based entry for 8 of 10 roles. Grouping should move to a **single role-aware rail** with a role→landing map `[RECOMMENDED]`.

---

## PHASE 5 — Dashboards (which should exist)

| Dashboard | Should exist? | Basis |
|---|---|---|
| Executive | ✅ `[RECOMMENDED]` | program health at a glance |
| Program Health | ✅ | on-time %, backlog, sources-live, QA, deliverables |
| Operational (PM/Ops) | ✅ | cycle burn-down, queue aging, throughput, connector uptime |
| Reviewer | ✅ | my queue, my accuracy, SLA timers |
| QA | ✅ | golden/regression/SLA/statistical/evidence-gate (all coded) |
| COR | ✅ | per-QHIN scorecards, escalations, priority SLA, deliverables |
| Analytics (self-serve) | ✅ | drill-through/filter/saved views |
| Compliance / Trust | ✅ | audit trail, provenance lineage, 508/FISMA evidence |

All are supported by existing aggregate/QA endpoints `[VERIFIED]`; only the **role-targeted presentation** is missing `[RECOMMENDED]`.

---

## PHASE 6 — Workflow vs. contract (deeply evidenced)

The implemented workflow — now read at the engine level — is rigorous and defensible `[VERIFIED]`:

1. **Import** — RCE Directory (FHIR R4) is the source of entities; currently **MOCK** (30 synthetic FHIR Organizations across 4 buckets) pending Sequoia key (Case #00055525).
2. **Sampling** — **Cochran with finite-population correction**, 95% CI, z=1.96, p=0.5, margin 0.05, proportional stratification across **11 QHINs**, deterministic seed 42 → **N=94,231 ⇒ n=383**.
3. **Validation (Tier-1)** — concurrent query of NPPES/LEIE/SAM/PECOS; **fail-closed** (a required source unavailable ⇒ `INDETERMINATE`, never a clean B1 auto-complete); confidence starts 1.0 and each finding deducts (0.40 severe … 0.10 minor).
4. **Classification** — **worst-finding-wins** bucket (B4 Non-Compliant → B3 Inexplicable → B2 Minor/Admin → B1 None); explicit **finding codes** (e.g., `LEIE_ACTIVE_EXCLUSION`, `SAM_ACTIVE_DEBARMENT`, `PECOS_PAYMENT_SUSPENSION`, `NPI_NOT_FOUND` → B4).
5. **Routing** — B1@≥0.95 → **Tier-1 auto-complete**; B2/B3/indeterminate → **Tier-2 analyst**; B4 → **Tier-3 SME** (supervisor_review_required).
6. **Evidence** — **5-element record** (identification, finding classification, source comparison, citations w/ response-hash & api-version, disposition). **Deadlines by bucket:** B2 30d / B3 21d / B4 10d.
7. **Disposition** — B1 no-action · B2 QHIN-notification-minor · B3/B4 QHIN-corrective-action · exclusion/debarment/suspension ⇒ **Escalate-to-ONC**; indeterminate ⇒ analyst-review-required (never "no action").
8. **QA review** — post-review gate ladder (intake Luhn/fields/dup → connectors-ran → findings → evidence-complete → qa_score ≥85), evidence chain-of-custody gate, golden-record regression (8 cases, drift detection), Wilson CI, sampling validation, SLA (critical 2/high 5/medium 10/low 21 days).
9. **Priority (Task 5)** — COR-directed (~20/month) with severity/root-cause heuristics and a COR-friendly status report.
10. **Reporting** — Weekly (**D3.1**, Task 3), Final retrospective (**D3.2**, Task 3, 120-day), Bi-weekly (Task 4), Quarterly (Task 4, 90-day, Recharts-ready charts), Priority COR status + quarterly (Task 5), QA scorecard (Tasks 1–6); **evidence-gate must pass before generation**; PDF/DOCX/CSV (12-column); AGT-branded; MOCK banner when applicable.
11. **COR determination** — **"AGT produces findings/recommendations; the ONC COR makes all final determinations"** is stamped everywhere `[VERIFIED]`.

**Does the workflow match the contract?** The code's **SOW task mapping is self-evident** `[VERIFIED via code refs]` — Task 2 (engine/connectors/sampling/taxonomy), Task 3 (weekly D3.1 + final D3.2), Task 4 (bi-weekly + quarterly), Task 5 (COR priority ~20/mo), QA Tasks 1–6 — **but final "matches the contract" certification requires the SOW/deliverables/acceptance-criteria documents** (Deliverable 2). The workflow is UI-under-surfaced, not under-built.

---

## PHASE 7 — Benchmark (efficiency patterns to adapt — not copy)

| System | Efficiency pattern → TEFCA ARC application |
|---|---|
| **Azure Portal** | Left rail + resource blades; consistent command bar; resource-health blades → **entity/connector blades** |
| **Power BI / Fabric** | Governed dashboards vs authoring; drill-through, cross-filter, saved views → **role dashboards + Explore** |
| **Purview** | Data map, source scans, lineage, compliance posture → **provenance/chain-of-custody + connector lineage** |
| **Defender / Sentinel** | Incident queue → triage → investigation graph → resolution; severity work-queues → **analyst queue → evidence workbench** |
| **ServiceNow** | Case lifecycle, SLAs, assignment, approvals, immutable history → **priority cases + corrective action** |
| **Palantir Foundry** | Object-centric investigation; source reconciliation & claim lineage → **6-source side-by-side evidence** |
| **CMS One PI** | Unified provider view across disparate federal systems; PI analytics → **single reviewer view over 6 sources** |
| **Salesforce Gov Cloud / federal audit** | Role homepages, approval chains, 508, audit → **role landings, audit trail** |

**Efficiency themes:** clear hierarchy, dense-but-legible grids, powerful filtering, drill-through everywhere, severity-driven queues, one-object-many-sources reconciliation, exec rollups with honest provenance.

---

## PHASE 8 — Product Architecture Blueprint

**Executive Vision `[RECOMMENDED]`** — the federal system of record for TEFCA directory-data integrity: defensible, provenance-labeled, audit-ready findings from a role-driven workbench, with COR/ONC oversight at a glance.

**Business Vision `[INFERRED/RECOMMENDED]`** — maximize network directory accuracy; deliver every SOW report on time and evidence-gated; keep every finding reproducible; reduce reviewer time-per-entity.

**User Personas** — Reviewer, Senior Analyst/SME, QA Lead, Program Manager, COR/ONC, Executive, Auditor, Administrator, Security Officer (roles `[VERIFIED]` for the first four + admin; others `[INFERRED]`). Detailed goals/pain points in the Master Blueprint §4.

**Information Architecture `[RECOMMENDED]`** — single role-aware rail: HOME · REVIEW (My Queue, Entity Review, Priority Cases, Findings) · CYCLES & SAMPLING · ANALYTICS & REPORTS (Executive/COR, QHIN Scorecards, QA Cockpit, Connectors, Reports Library) · ADMIN (Users, Methodology, Trust Center). Retire dual-nav/duplicate destinations `[VERIFIED problem]`.

**Navigation Architecture `[RECOMMENDED]`** — command bar (context + primary action + global search `[VERIFIED /search]`), role→landing routing, breadcrumb + as-of context, persistent MOCK/PRODUCTION banner.

**Workflow Architecture** — the 11-step lifecycle in Phase 6 `[VERIFIED]`; surface it end-to-end.

**Screen Inventory** — 15 target screens with real API mappings (Master Blueprint §Part B); pivotal: **S4 Entity Review 6-source workbench**.

**Data Architecture `[VERIFIED]`** — entities → validation engine → 6 connectors → **source cache (SHA-256 hash + freshness + api_version)** → bucket+confidence → 5-element evidence → queue/disposition → reports + immutable `tefca_qa_audit`. Denormalized `tefca_reviews`/`tefca_findings` back dashboards; authoritative rich data in `tefca_evidence_records`.

**Connector Architecture `[VERIFIED]`** — 6 sources, **fail-closed** (VERIFIED-ok vs unavailable; never fabricated clean values), 3-retry exponential backoff (transient only), per-source health logging. Live: NPPES (keyless), OIG-LEIE (free CSV, 24h cache), PECOS (via NPPES proxy; **payment_suspension intentionally None** pending COR feed). Pending keys: **SAM.gov** (`SAM_GOV_API_KEY`), **RCE Directory** (Sequoia Case #00055525), **IQVIA OneKey** (federal ODC). *Key provisioning is configuration, not code.*

**Analytics Architecture `[RECOMMENDED]`** — on `/dashboard/summary,trends` + QA endpoints; add drill-through, cross-filter, time-range, saved views, faceting.

**Reporting Architecture `[VERIFIED]`** — 7 report types mapped to SOW Tasks 3/4/5 + QA; **evidence-gate precondition**; PDF/DOCX/CSV (12-col); schema-driven renderer; provenance-stamped. Add a Reports Library `[RECOMMENDED]`.

**Security Architecture `[VERIFIED/INFERRED]`** — every route authenticated; role-floor via `require_role`; a few aggregate endpoints public; PII exports role-gated; least-privilege tiering `[INFERRED]`. 508/HIPAA/FISMA chips are **asserted, not conformance-verified** `[INFERRED]` — needs artifacts.

**Audit Architecture `[VERIFIED]`** — immutable append-only `tefca_qa_audit`; source-cache reproducibility (hash/freshness/version); `analyst_override_reason`; supervisor timestamps; evidence chain-of-custody gate. Surface an entity/finding **audit timeline** `[RECOMMENDED]`.

**Role Matrix** — Master Blueprint §15 `[VERIFIED]`.

**Contract Traceability Matrix** — **implementation-derived only** (Master Blueprint Part A); **cannot be certified** without the procurement documents (Deliverable 2). New precision from this review: SOW Task 2/3/4/5 + QA Tasks 1–6 are **code-referenced**, and the **award number is `7571MN26F80064`** `[VERIFIED]`.

**Module Relationships `[VERIFIED]`** — `connectors` → `validation_engine` → `review_engine` → (`models`/DB) → `reporting`/`report_renderer` → routes; `qa_engine` observes reviews/connectors/audit; `mock_data` stands in for RCE until keyed.

**Future Roadmap** — 5 phases (Quick Wins → Operational → Enterprise UX → Executive Analytics → Future/AI) with effort/deps in the Master Blueprint; all UI-only on existing endpoints.

---

## PHASE 9 — DEFERRED

**Per instruction, the UI/UX modernization strategy is produced ONLY AFTER AGT approves this blueprint.** Not included here.

---

## DELIVERABLE 2 — Contract Gap Assessment (still INCOMPLETE)

> **Contract Traceability cannot be certified until the official HHS/ONC TEFCA ARC procurement documents are reviewed.** No contract requirement is invented in this assessment.

### Documents Required Before Contract Traceability Can Be Certified
- ☐ RFQ ☐ Award ☐ PWS/SOW ☐ Amendments ☐ Questions & Answers ☐ Evaluation Criteria ☐ Deliverables schedule ☐ Acceptance Criteria ☐ Attachments/Exhibits ☐ Sample/Template Reports ☐ **AGT Technical Proposal** ☐ **AGT D2 Review Methodology & Control Framework (document)** ☐ Government-Furnished Information (GFI) ☐ Government-Furnished Property (GFP, if any)

**Also request AGT confirm:** the correct award identifier — code says **`7571MN26F80064`** `[VERIFIED]`; UI shows `7571MN26Q00027` (FCC) `[VERIFIED defect]`.

On receipt, Deliverable 2 becomes a clause-by-clause traceability matrix (fully/partial/missing/UI-only/backend/docs-only) and every screen is validated against acceptance criteria.

---

## Review-board conclusion
The TEFCA ARC **engine is federally rigorous** — statistically-sampled, fail-closed, evidence-gated, audit-trailed, provenance-honest, and self-consciously non-adjudicating (COR decides). The gap is **presentation, not protocol**: role-based dashboards, an end-to-end reviewer workbench, a single coherent IA, and executive/COR visibility — all deliverable on the **existing** backend. **Two items are non-UI and outside this scope:** three pending connector keys (config) and 508/FISMA conformance artifacts (docs). **Next step:** AGT provides the procurement documents (to certify traceability and correct the contract number) and approves this blueprint; only then does Phase 9 (UI/UX modernization) begin.

*No code, backend, API, database, authentication, scheduler, deployment, or infrastructure was modified or discussed. Every `[VERIFIED]` claim is grounded in `app/Tefca/` source, the live app, or cited public sources; `[INFERRED]`/`[RECOMMENDED]` are labeled; no contract requirement was fabricated.*
