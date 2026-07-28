# TEFCA ARC — Architecture & UX Assessment Report
### Blueprint for future TEFCA ARC UI work (no implementation — assessment only)

**Prepared:** 2026-07-09
**Prepared as (perspective):** joint enterprise-architecture / Fluent-UX / federal-HCD review
**Program:** ONC TEFCA Review Protocol — QHIN / Participant / Subparticipant directory-accuracy audit (Contract ref shown in app: 7571MN26Q00027)
**Constraint honored:** No code, backend, API, DB, auth, scheduler, or deployment was modified. This is a read-only assessment.

---

## 0. Evidence base & honesty statement

This assessment is grounded in three **real** sources examined during the review:
1. **Implemented data model** — `app/Tefca/models.py` (entities, buckets, tiers, cycles, 5-element evidence records, priority cases, analyst queue, reports, QA, connector logs).
2. **Implemented API surface** — `app/Tefca/routes.py` (~60 endpoints; role guards `reviewer` / `senior_analyst` / `program_manager` / `qalead`).
3. **Live application** — the deployed TEFCA UI (Overview page + navigation), reviewed read-only.

> **⚠️ Phase-1 limitation — stated plainly, not worked around.** No TEFCA **contract documents** (RFQ, PWS, amendments, Q&A, evaluation criteria, formal acceptance criteria) exist in this repository — every document under `docs/` is FCC Bulletin. **I did not fabricate contract requirements.** The Requirements Traceability Matrix in Phase 1 is therefore **derived from the authoritative implemented system + published TEFCA domain standards**, and each row is marked accordingly. **To produce a true contract-traceable RTM, AGT must provide the RFQ/PWS/amendments.** Where a requirement is inferred, it is labeled `[INFERRED]`; where it is evidenced in code/UI, it is labeled `[EVIDENCED]`.

**Executive verdict:** The TEFCA ARC platform has a **strong, correct domain core** (the review methodology, classification, sampling, evidence model, and QA are well-modeled) and a **competent Overview dashboard**. The primary opportunity is **not** rebuilding the engine — it is **maturing the UX from a demo-grade single-dashboard into a role-driven, workflow-complete federal review workbench**: closing the loop from *sample → review → evidence → disposition → report → audit*, adding role-specific dashboards, and elevating executive/COR reporting to federal-grade.

---

## PHASE 1 — Requirements baseline & Requirements Traceability Matrix (derived)

**Domain, as implemented (authoritative):** a statistically-sampled, human-in-the-loop review protocol that validates TEFCA directory entities against six authoritative federal sources, classifies discrepancies into four buckets, routes by three tiers, produces a five-element evidence record per entity, and reports per cycle to ONC.

### 1.1 Requirements Traceability Matrix (derived — pending contract confirmation)

| # | Requirement (derived) | Source of truth | Evidenced where | Status in app |
|---|---|---|---|---|
| R1 | Maintain master registry of TEFCA entities (QHIN/Participant/Subparticipant) with submitted identifiers (NPI, UEI, address, endpoints, FHIR) | `[EVIDENCED]` | `TEFCAEntity` model | ✅ Implemented |
| R2 | Validate each entity against 6 authoritative sources (NPPES, PECOS, OIG-LEIE, SAM.gov, RCE Directory, IQVIA OneKey) | `[EVIDENCED]` | `connectors.py`, `/health` connectors, Overview "4/6 Sources Live" | ✅ 4 live / 2 pending keys |
| R3 | Four-bucket discrepancy classification (No Discrepancy / Minor-Admin / Inexplicable / Non-Compliant) | `[EVIDENCED]` | `BucketClassification`, `BucketLabel`; Overview bucket cards | ✅ Implemented |
| R4 | Three-tier routing (T1 auto / T2 analyst / T3 SME-ONC) with human-in-the-loop for B2–B4/indeterminate | `[EVIDENCED]` | `TierAssignment`, `TEFCAAnalystQueue`; Overview tier routing | ✅ Implemented |
| R5 | Statistical sampling at 95% confidence per cycle | `[EVIDENCED]` | `TEFCAReviewCycle.sample_confidence_level`; Overview "95% Confidence"; `/sampling` | ✅ Implemented |
| R6 | Review cycles by contract task — Task 3 retrospective, Task 4 ongoing, Task 5 priority/COR | `[EVIDENCED]` | `CycleType` enum | ✅ Implemented |
| R7 | Five-element evidence record per entity/cycle (identification, finding, source-comparison, citations, disposition) | `[EVIDENCED]` | `TEFCAEvidenceRecord` | ✅ Implemented |
| R8 | Disposition recommendations (no action / QHIN minor / QHIN corrective / escalate ONC) | `[EVIDENCED]` | `DispositionRecommendation` | ✅ Implemented |
| R9 | COR-directed priority cases with severity, root cause, deadlines (Task 5) | `[EVIDENCED]` | `TEFCAPriorityCase` | ✅ Implemented |
| R10 | Role-based access (reviewer / senior analyst / program manager / QA lead; COR/PII) | `[EVIDENCED]` | `require_role(...)` across routes | ✅ Implemented (see Phase 2) |
| R11 | Reporting: weekly, bi-weekly, quarterly, final, priority, QA — with PDF + editable DOCX + CSV | `[EVIDENCED]` | `report_renderer.py`, `/reports/*` | ✅ Implemented |
| R12 | QA program: golden-record regression, SLA, statistical checks, drift, alerts, scorecard | `[EVIDENCED]` | `qa_engine.py`, `/qa/*`, QA monitor | ✅ Implemented |
| R13 | Source-response caching for reproducibility/audit (hash, freshness, versions) | `[EVIDENCED]` | `TEFCASourceCache` | ✅ Implemented |
| R14 | Connector health/uptime tracking | `[EVIDENCED]` | `TEFCAConnectorLog`, `/connectors` | ✅ Implemented |
| R15 | Honest MOCK vs PRODUCTION data provenance labeling | `[EVIDENCED]` | `is_mock_data`, data_source labels; Overview "Mock" tags | ✅ Implemented |
| R16 | QHIN-level accuracy scoring across all QHINs (95% CI target) | `[EVIDENCED]` | Overview "QHIN Accuracy Rates" | ✅ Implemented (dashboard) |
| R17 | 508 / HIPAA / FISMA-Moderate posture | `[INFERRED]` from badges | Overview compliance chips | ⚠️ Claimed; not verified in this review |
| R18 | Data import / ingestion of RCE directory + COR case lists | `[EVIDENCED]` (nav) | "Data Import" page | ⚠️ Present; depth unverified |
| R19 | Executive / COR reporting views (program-level rollups) | `[INFERRED]` | Overview KPIs | 🟡 Partial (see gaps) |
| R20 | Audit trail / chain-of-custody for every finding & override | `[INFERRED]` (federal norm) | `analyst_override_reason`, timestamps, source cache | 🟡 Data captured; UI surfacing partial |

**Action for AGT:** supply the RFQ/PWS so R17–R20 and any acceptance criteria, performance SLAs (e.g., turnaround per cycle), and security control mappings (e.g., specific FISMA controls, 508 conformance level) can be traced to contract clauses. Until then, treat this RTM as the **de-facto** baseline.

---

## PHASE 2 — User personas

Derived from the role guards in `routes.py` (`reviewer`, `senior_analyst`, `program_manager`, `qalead`) plus the domain (COR/ONC oversight, executive/leadership, admin). Seven personas:

### P1 — Reviewer (Tier-2 Analyst) — *primary daily user*
- **Goals:** clear the analyst queue accurately and on time; produce defensible evidence records.
- **Daily workflow:** claim queue items → compare submitted vs source data → classify bucket → write the 5 elements → recommend disposition → submit.
- **Decisions:** bucket (1–4), disposition, escalate vs resolve, override auto-classification (with reason).
- **Information needed:** side-by-side submitted-vs-6-source comparison, source freshness/citations, prior findings for the entity, similar-entity precedent.
- **Pain points (current):** no evidenced dedicated review workspace surfaced in the top nav beyond "Entity Reviews"/"Validation Queue"; unclear how much of the 5-element capture is guided vs freeform.
- **Reports/dashboards:** My Queue, My Productivity/SLA, recently completed.
- **Permissions:** `reviewer` (create/edit own evidence, claim T2 queue). **Typical tasks:** 20–40 reviews/day.

### P2 — Senior Analyst / SME (Tier-3) — *escalation authority*
- **Goals:** adjudicate Bucket-4 / inexplicable / indeterminate cases; ensure ONC-escalation quality.
- **Workflow:** work T3 queue → deep source reconciliation → supervisor review of T2 records → sign-off.
- **Decisions:** confirm/adjust bucket, approve escalation to ONC COR, supervisor approval of overrides.
- **Info needed:** full evidence chain, override reasons, source-cache diff, root-cause tools.
- **Reports:** T3 backlog, escalation pipeline, reviewer QA (accuracy of T2 work).
- **Permissions:** `senior_analyst` (T3 queue, supervisor review).

### P3 — QA Lead — *methodology integrity*
- **Goals:** prove the protocol is statistically valid, reproducible, drift-free, on-SLA.
- **Workflow:** run/monitor QA sweeps → review golden-record regression → investigate drift/alerts → sign QA scorecard.
- **Decisions:** methodology version bumps, sampling adequacy, pass/fail QA gates, alert triage.
- **Info needed:** golden-record pass rate, inter-rater/statistical checks, SLA adherence, connector uptime, drift trends.
- **Reports:** QA scorecard, regression report, SLA report, drift/alert log.
- **Permissions:** `qalead` (QA endpoints, alerts test, methodology).

### P4 — Program Manager — *delivery & cycle ownership*
- **Goals:** run cycles on schedule, meet sampling targets, deliver ONC reports on time, manage workload.
- **Workflow:** plan/launch cycle → monitor completion % and queue depth → balance reviewer load → generate & submit cycle reports.
- **Decisions:** cycle scope/sample, staffing/assignment, report sign-off/submission, corrective-action tracking.
- **Info needed:** cycle burn-down, queue aging, throughput vs SLA, per-QHIN status, report readiness.
- **Reports:** cycle status, throughput/SLA, corrective-action tracker, report library.
- **Permissions:** `program_manager` (cycle create, report generate/submit).

### P5 — COR / ONC Oversight — *the client*
- **Goals:** confidence the TEFCA network directory is accurate; direct priority reviews; receive audit-grade deliverables.
- **Workflow:** review executive rollups → open Task-5 priority cases → track disposition of non-compliant entities → consume final/quarterly reports.
- **Decisions:** accept deliverables, direct priority reviews, act on QHIN corrective actions/escalations.
- **Info needed:** network-wide accuracy trend, per-QHIN scorecards, non-compliance & escalations, priority-case status, provenance (mock vs real).
- **Reports:** Executive/COR dashboard, quarterly & final reports, priority-case ledger.
- **Permissions:** read + priority-case authoring (COR). **Note:** a dedicated COR view is a key gap (Phase 4).

### P6 — Executive / Leadership (AGT + ONC) — *program health at a glance*
- **Goals:** one screen answering "is the program healthy, on-track, and defensible?"
- **Info needed:** accuracy trend, cycle-on-time %, backlog risk, data-source availability, QA status, deliverable status.
- **Reports:** Executive dashboard, trend deck, exception summary.

### P7 — System Administrator — *access & configuration*
- **Goals:** manage users/roles, connectors/keys status, methodology config, data provenance.
- **Info needed:** user & role directory, connector key status (2/6 pending), audit of access, config versions.
- **Permissions:** admin (observed logged-in user `admin@docuaction.io`).

---

## PHASE 3 — Current application review

Reviewed: the live TEFCA area and its navigation (Federal Compliance: **Overview, Data Import, Review Cycles, Entity Reviews, Validation Queue, Priority Reviews, Findings, Reports, QA Operations, Connectors, Analytics**; plus Governance: Analytics, Trust Center), with the **Overview** page examined in depth, cross-referenced to the backend.

### 3.1 Overview (dashboard) — reviewed in depth
- **Purpose:** program landing / at-a-glance status.
- **Strengths:** genuinely strong content — headline KPIs (entities reviewed, avg accuracy, QHINs covered, priority reviews); scope tiles (in-scope, T1/T2/T3, confidence, sources live); **4-bucket distribution** with %; **per-QHIN accuracy** with 95%-CI legend; **3-tier routing**; **data-source status** with honest **Live/Mock** labels; federal compliance chips (508/HIPAA/FISMA/ONC). Provenance honesty is a standout (Mock vs Live is explicit).
- **Weaknesses:** (a) **single dense screen** mixing executive, operational, and methodology content — no role targeting; (b) **internal number inconsistencies** in the demo data (21,847 vs 21,086 vs "21K"; T2 3,091 vs 3,030) that undermine trust on a federal dashboard; (c) tabs (Overview/Review Queue/Reports/Sampling/Methodology) duplicate left-nav destinations — two competing navigation systems; (d) charts are largely static bars/lists — limited interactivity (no drill-down, filter, or time range); (e) no "as-of" timestamp / cycle context on the KPIs; (f) no clear primary call-to-action per role.
- **Government usability:** compliance chips are asserted, not linked to evidence (508 conformance report, FISMA SSP). No visible "official use / data provenance" banner beyond Mock tags.

### 3.2 Other pages (nav-level + backend-evidenced)
| Page | Purpose (evidenced) | Observation |
|---|---|---|
| Data Import | ingest RCE directory / COR case lists | Present; import UX depth unverified — likely a gap for validation/preview/rollback |
| Review Cycles | manage Task 3/4/5 cycles, sampling, status | Backend rich (`/cycles`); UI likely list-centric — needs cycle **burn-down** & sample provenance |
| Entity Reviews | per-entity evidence records | The core reviewer surface — needs a true **side-by-side 6-source workbench** (Phase 8) |
| Validation Queue | analyst queue (T2/T3) | Duplicated in both "Operations" and "Federal Compliance" nav — IA confusion |
| Priority Reviews | Task-5 COR cases | Present; needs COR-facing SLA/deadline tracking |
| Findings | discrepancy findings ledger | Needs filter/severity/connector faceting + export |
| Reports | generate/list/download (PDF/DOCX/CSV) | Backend strong; UI needs a **report library** with status & provenance |
| QA Operations | golden/regression/SLA/drift/alerts | Backend very rich; UI likely under-surfaces the QA scorecard |
| Connectors | 6-source health/uptime | Good candidate for an uptime/latency panel |
| Analytics | trends | Overlaps Governance→Analytics — consolidate |

### 3.3 Cross-cutting issues
- **Two navigation systems** (left rail + in-page tabs) that overlap → orientation cost.
- **Duplicate destinations** ("Validation Queue" and "Analytics" appear twice across nav groups).
- **No role-based landing** — every persona lands on the same dense Overview.
- **Demo-data inconsistencies** visible on the flagship screen — a credibility risk for a federal audience.
- **Workflow not visibly closed-loop** in the UI (sample → review → evidence → disposition → report → audit) even though the backend supports it.
- **Empty-space / density imbalance:** flagship screen is very dense; deeper pages likely sparse.

---

## PHASE 4 — Gap analysis (ranked)

| ID | Gap | Type | Severity |
|---|---|---|---|
| G1 | **No role-based dashboards/landing** — Reviewer, QA Lead, PM, COR, Exec all see the same Overview | Workflow/Dashboards | **Critical** |
| G2 | **No end-to-end reviewer workbench** — a guided 6-source side-by-side compare + 5-element capture + disposition on one screen | Workflow | **Critical** |
| G3 | **No Executive/COR dashboard** — network-accuracy trend, per-QHIN scorecards, escalations, deliverable status for the client | Executive views | **Critical** |
| G4 | **Demo-data inconsistencies + no "as-of"/provenance context on KPIs** — trust risk on a federal dashboard | Data presentation | **High** |
| G5 | **QA scorecard under-surfaced** — rich QA backend (golden/regression/SLA/drift) not elevated to a QA-Lead cockpit | Dashboards/Analytics | **High** |
| G6 | **Cycle management lacks burn-down/aging/throughput** and sample-provenance visualization | Analytics/Workflow | **High** |
| G7 | **IA duplication & dual-nav** — overlapping destinations and competing nav systems | Navigation | **High** |
| G8 | **Reports lack a first-class library** (status, period, provenance, PDF/DOCX/CSV, submission state) | Reports | **High** |
| G9 | **Priority-case (Task 5) COR workflow** lacks deadline/SLA tracking + escalation timeline | Workflow | **Medium** |
| G10 | **Findings ledger lacks faceting/export** (by QHIN, connector, severity, bucket, cycle) | Data presentation | **Medium** |
| G11 | **Data Import lacks validate→preview→confirm→rollback** and provenance stamping UX | Workflow | **Medium** |
| G12 | **Audit trail / chain-of-custody not surfaced** (overrides, source-cache diffs, timestamps exist in data) | Compliance | **Medium** |
| G13 | **Connector panel** could show uptime %, latency trend, key-status (2/6 pending) and impact-on-cycle | Visualizations | **Medium** |
| G14 | **508 conformance & FISMA evidence** asserted via chips but not linked to artifacts | Government usability | **Medium** |
| G15 | **Limited interactivity** — static charts; no drill-down/filter/time-range/saved-views | Visualizations/Analytics | **Low–Medium** |

---

## PHASE 5 — Industry benchmark (best practices to adapt — not copy)

| Reference | Pattern worth adapting to TEFCA ARC |
|---|---|
| **Azure Portal** | Left rail + resource blades; consistent command bar; "at-a-glance + drill-in"; resource health blades → model **connector health** & **entity blades**. |
| **Microsoft Fabric / Power BI Service** | Governed dashboards vs report authoring separation; drill-through, bookmarks/saved views, cross-filtering → model **role dashboards** + **analytics**. |
| **Microsoft Purview** | Data map + compliance posture + data-source scans + lineage → model **connector/source lineage** and **provenance/chain-of-custody**. |
| **Microsoft Defender** | Incident queue → triage → investigation graph → resolution; severity-driven work queues → model **analyst queue → evidence workbench**. |
| **ServiceNow** | Case lifecycle, SLAs, assignment, approvals, audit history → model **priority cases (Task 5)** and **corrective-action tracking**. |
| **Splunk / Datadog** | Ops dashboards: uptime, latency, SLOs, alerting; time-range + saved views → model **QA/connector operations** & **SLA**. |
| **Palantir Foundry** | Object-centric investigation, source reconciliation, lineage of a claim → model the **6-source side-by-side evidence** view. |
| **Salesforce Gov Cloud / Federal audit systems** | Role homepages, approval chains, immutable audit, accessibility → model **role landing pages**, **508**, **audit trail**. |

**Design language:** adopt a **Fluent-2-aligned, federal, high-contrast, 508-first** system: clear command bars, consistent card/table/blade patterns, semantic status colors that pass WCAG, keyboard-first, and dense-but-legible data grids.

---

## PHASE 6 — Information architecture (recommended)

Collapse the dual-nav into **one role-aware left rail** with four top groups, and retire in-page tabs that duplicate destinations.

```
TEFCA ARC
├── HOME  (role-aware landing → routes to the right dashboard per role)
├── REVIEW (operations)
│   ├── My Queue            (Reviewer/SME work list)
│   ├── Entity Review        (the 6-source evidence workbench)  ← core
│   ├── Priority Cases       (Task 5 / COR)
│   └── Findings             (ledger, faceted)
├── CYCLES & SAMPLING
│   ├── Review Cycles        (Task 3/4/5, burn-down)
│   ├── Sampling             (method, 95% CI, provenance)
│   └── Data Import          (validate→preview→confirm)
├── ANALYTICS & REPORTS
│   ├── Executive / COR Dashboard
│   ├── QHIN Scorecards
│   ├── QA Cockpit
│   ├── Connectors & Sources (health/uptime/lineage)
│   └── Reports Library      (PDF/DOCX/CSV + status + provenance)
└── ADMIN
    ├── Users & Access
    ├── Methodology & Config
    └── Trust Center         (508/FISMA/HIPAA evidence, audit trail)
```

**Workflow spines**
- **Review:** My Queue → Entity Review (compare→classify→5 elements→disposition) → supervisor sign-off (if B4/override) → Findings → Report.
- **Cycle:** Plan sample (95% CI) → launch → monitor burn-down/aging → complete → generate cycle report → submit to COR.
- **Reporting:** select period/type → generate (PDF/DOCX/CSV) → provenance-stamp → library → submission tracking.
- **Import:** upload → validate → preview diffs → confirm → provenance stamp → (rollback available).
- **Administration:** users/roles → connector keys → methodology version → audit.
- **Analytics:** role dashboard → drill-through → saved view → export.

---

## PHASE 7 — Dashboard strategy (per persona)

| Audience | Dashboard | Core cards / visuals |
|---|---|---|
| **Executive / Leadership** | **Program Health** | Network accuracy trend (line); cycle on-time %; backlog risk gauge; sources-live (4/6); QA status; deliverables due. One screen, green/amber/red. |
| **COR / ONC** | **Oversight** | Per-QHIN scorecards (accuracy vs 95% CI); non-compliance (Bucket-4) & ONC escalations; priority-case ledger w/ deadlines; final/quarterly report status; **Mock vs Real provenance banner**. |
| **Program Manager** | **Delivery** | Active cycles burn-down; queue depth & aging; reviewer throughput vs SLA; per-QHIN completion; report-readiness. |
| **QA Lead** | **QA Cockpit** | Golden-record pass rate; regression/drift trend; inter-rater/statistical checks; SLA adherence; connector uptime; alert log. |
| **Reviewer** | **My Work** | My queue (priority-sorted); today's completed; my accuracy (QA feedback); SLA timers; quick-open Entity Review. |
| **Senior Analyst / SME** | **Escalations** | T3 backlog; supervisor-review queue; override audit; root-cause tools; escalation pipeline to ONC. |
| **Operations** | **Connectors & SLA** | 6-source uptime/latency; key-status (2 pending); cycle-impact; error log. |
| **Compliance** | **Trust Center** | 508 conformance, FISMA control status, HIPAA safeguards, immutable audit trail, data-provenance lineage. |
| **Analytics (self-serve)** | **Explore** | Filter by QHIN/connector/bucket/tier/cycle/time; drill-through; saved views; export. |

Principle: **each role lands on its dashboard**, with a consistent card system and drill-through into the operational surfaces.

---

## PHASE 8 — Wireframes (layouts only — no code)

**8.1 Executive / COR "Program Health" (role landing)**
```
┌───────────────────────────────────────────────────────────────────────┐
│ TEFCA ARC · Executive         Cycle 12 · as of Jul 9 2026 · ● Mixed data│
├───────────────────────────────────────────────────────────────────────┤
│ [Accuracy 91.4% ▲]  [On-time cycles 100%]  [Backlog risk ●Low]         │
│ [Sources 4/6 ●]     [QA ●Pass]             [Deliverables 2 due]        │
├───────────────────────────────┬───────────────────────────────────────┤
│ Network Accuracy Trend (line) │ Per-QHIN Scorecard (bar vs 95% CI line)│
│  ╱╲__╱‾‾ 12 cycles            │  eHealthEx 87.4 ▓▓▓▓▓░ | CommonWell 92 ▓│
├───────────────────────────────┼───────────────────────────────────────┤
│ Bucket distribution (donut)   │ Escalations & Non-Compliance (table)   │
│  ●83 ●10 ●5 ●2                │  Entity | QHIN | Bucket4 | Disposition │
└───────────────────────────────┴───────────────────────────────────────┘
```

**8.2 Entity Review — 6-source evidence workbench (Reviewer) ← the core screen**
```
┌───────────────────────────────────────────────────────────────────────┐
│ ◀ Queue   Entity: Acme Health (NPI 1234567893)   T2 · Cycle 12  [Save] │
├─────────────┬─────────────────────────────────────────────────────────┤
│ SUBMITTED   │  SOURCE COMPARISON (side-by-side, diffs highlighted)      │
│ Legal name  │  Field        Submitted | NPPES | PECOS | LEIE | SAM |RCE │
│ NPI / UEI   │  Legal name   Acme…     | Acme… | Acme… |  —   |Acme|Acme │
│ Address     │  Address      123 A St  | 123 A |125 A ⚠| —    |123A|123A │
│ Endpoints   │  Exclusion    —         |  —    |  —    | none | —  | —   │
│ FHIR raw ▸  │  ● freshness/citation per cell · [view source JSON]      │
├─────────────┼─────────────────────────────────────────────────────────┤
│ CLASSIFY    │ 5-ELEMENT EVIDENCE (guided)                              │
│ ○B1 ●B2 ○B3 │ 1 Identification ✓  2 Finding: Minor addr unit diff      │
│ ○B4         │ 3 Source compare ✓  4 Citations: PECOS 2026-07 …         │
│ Confidence  │ 5 Disposition: ● QHIN notification (minor)               │
│  0.82       │ [Override reason ▾]  [Escalate T3]   [Submit evidence]   │
└─────────────┴─────────────────────────────────────────────────────────┘
```

**8.3 Program Manager — Cycle "Delivery"**
```
┌───────────────────────────────────────────────────────────────────────┐
│ Cycles  [Task3 ▾][Task4][Task5]     [+ Plan cycle]                     │
├───────────────────────────────────────────────────────────────────────┤
│ Cycle 12 · Task3 · 95% CI · sample 1,540/21,086     ▓▓▓▓▓▓▓░ 78%       │
│ Burn-down (line) ╲╲╲__   | Queue aging (bars) 0-1d 2-3d 4d+            │
├───────────────────────────────┬───────────────────────────────────────┤
│ Reviewer throughput vs SLA    │ Per-QHIN completion (stacked bar)      │
│  R.Lee 34 ● | J.Ng 28 ●       │  done / in-review / pending            │
└───────────────────────────────┴─────────────────[Generate cycle report]┘
```

**8.4 QA Lead — "QA Cockpit"**
```
[Golden pass 98% ●] [Regression ●Pass] [Drift ●None] [SLA 96% ●] [Uptime 4/6]
Regression/drift trend (line) ── | Statistical checks (table) | Alerts (list)
```

**8.5 Reports Library**
```
Type ▾ | Period ▾ | Status ▾ | Provenance ▾
Quarterly Q2 · Cycle 9-12 · ●PRODUCTION · [PDF][DOCX][CSV] · Submitted ✓
Priority · CASE-014 · ●MOCK · [PDF][DOCX] · Draft
```

Global patterns: top **command bar** (context + primary action), left **role rail**, **card grid** dashboards, **faceted tables** with export, **drill-through** everywhere, persistent **data-provenance banner** (Mock/Production), 508 keyboard/contrast throughout.

---

## PHASE 9 — Implementation roadmap (UI only; no backend changes required — all endpoints exist)

> All recommended screens map to **existing** API endpoints (cycles, evidence, queue, priority cases, reports, QA, connectors, dashboard summary/trends). This is a **front-end maturation** program, not a backend rebuild.

### Phase 1 — Trust, IA & the core loop (foundation)
- Fix flagship-dashboard **data consistency** + add **as-of/provenance** context (G4).
- Consolidate **IA / single role-aware nav**; retire duplicate destinations & competing tabs (G7).
- Build the **Entity Review 6-source workbench** with guided 5-element capture + disposition (G2).
- **Reviewer "My Work"** landing (G1 partial).
- **Effort:** M–L. **Deps:** design system/tokens; role→route map. **Risk:** Med (workbench is the pivotal screen).

### Phase 2 — Role dashboards & reporting
- **Executive/COR dashboard** + **per-QHIN scorecards** (G3).
- **QA Cockpit** (G5); **Cycle Delivery** burn-down/aging/throughput (G6).
- **Reports Library** with status/provenance/exports (G8).
- **Effort:** M. **Deps:** Phase-1 design system; dashboard-summary/trends endpoints. **Risk:** Low–Med.

### Phase 3 — Depth, compliance & self-serve analytics
- **Priority-case (Task 5) COR workflow** with SLA/deadlines + escalation timeline (G9).
- **Findings** faceting/export (G10); **Data Import** validate→preview→confirm→rollback (G11).
- **Trust Center**: audit trail/chain-of-custody surfacing + 508/FISMA evidence links (G12, G14).
- **Connectors** uptime/latency/key-status/lineage (G13); **self-serve Explore** with drill-through & saved views (G15).
- **Effort:** M–L. **Deps:** Phases 1–2. **Risk:** Med (compliance evidence needs artifacts from AGT).

**Cross-cutting (all phases):** 508/WCAG AA conformance, Fluent-aligned federal design system, consistent provenance banner, keyboard-first, performance on dense grids.

---

## Recommendation & next step
The TEFCA ARC **domain engine is sound and largely complete**; the work ahead is **UX maturation into a role-driven, workflow-complete, federal-grade review workbench** — deliverable entirely on the existing backend. **Before UI implementation begins, AGT should (1) provide the RFQ/PWS/amendments** so the RTM (Phase 1) becomes contract-traceable and acceptance/security/performance requirements are confirmed, and **(2) approve this assessment** as the blueprint. No implementation will proceed until AGT reviews and approves.

*This assessment modified no code, backend, API, database, authentication, scheduler, or deployment. Every "implemented" claim is grounded in `app/Tefca/` source or the live application; unverified items are labeled.*
