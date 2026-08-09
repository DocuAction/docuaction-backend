# TEFCA ARC — Product Blueprint & Contract Traceability Assessment
### Master design specification for all future TEFCA ARC UI implementation

**Prepared:** 2026-07-09
**Builds on:** `TEFCA_ARC_Architecture_and_UX_Assessment.md` (approved as Phase 1)
**Constraint honored:** No code, backend, API, database, authentication, scheduler, or deployment modified. Read-only.

---

## 0. Document status & honesty statement (read first)

**What was requested:** review the *actual HHS ONC TEFCA procurement documents* (RFQ, PWS, amendments, Q&A, evaluation criteria, deliverables, acceptance criteria) and produce a contract-traceability assessment.

**Confirmed finding (evidence-based):**
- **No TEFCA procurement documents exist in this repository in any format** (searched all files: no RFQ/PWS/SOW/amendment/Q&A PDFs, DOCX, XLSX, or text). None were attached to the request.
- The **"ONC CONTRACT 7571MN26Q00027" shown in the TEFCA UI is a reused placeholder** — that solicitation number appears **only** in FCC Bulletin files (`fcc_sources.py`, etc.) and never in any `app/Tefca/` file. It is **not** a verified ONC/HHS TEFCA solicitation number.

**Consequence, stated plainly:** I **cannot** produce a genuine *contract-clause* traceability against documents I do not have, and I **will not fabricate** PWS clauses, deliverable IDs, acceptance criteria, evaluation factors, or performance/security requirements for a **US federal government deliverable**. Doing so would be a serious integrity failure.

**What I *can* legitimately use (and do):**
1. **The implemented system** as the *de-facto, authoritative* requirements baseline — it was built to the contract.
2. **Code-evidenced contract references** the developers left in the source (labeled `[CODE-REF]`): the endpoint summaries explicitly name **"SOW Task 3"**, **"SOW Task 4"**, deliverables **"D3.1 weekly progress report"** and **"D3.2 final report"**, plus Task-5 priority/COR reviews and Cochran-95%-CI sampling. These tell us the contract has a SOW with numbered tasks (1/3/4/5) and numbered deliverables — but **not their full text**.
3. **Published TEFCA domain standards** (Common Agreement, QHIN/RCE roles) as general context, never quoted as this contract's requirements.

**Therefore this document delivers:**
- **Part A — Contract Traceability Assessment** using the required six-category classification, traced against the *implemented baseline* (marked `PENDING CONTRACT VERIFICATION`).
- **Part B — Screen Catalog** (every screen, each mapped to *real* existing endpoints).
- **Part C — Product Blueprint** (the master design spec).

**To finalize true contract traceability, AGT must provide:** the RFQ, PWS/SOW, amendments, Q&A, evaluation criteria, deliverables list, and acceptance criteria (paste text, add files under `docs/tefca-contract/`, or provide the real SAM.gov/eBuy solicitation link). Part A converts from "implementation-derived" to "contract-traced" the moment those arrive.

---

## PART A — Contract Traceability Assessment (implementation-derived baseline)

**Legend — Implementation status:** ✅ Fully implemented · 🟡 Partially implemented · ⛔ Missing.
**Legend — Work type to reach target UX:** `UI-only` · `Backend` · `Docs-only`.
Every row is `PENDING CONTRACT VERIFICATION` until the RFQ/PWS is supplied.

| # | Requirement (implementation-derived) | Evidence | Impl. status | Work type to close UX gap |
|---|---|---|---|---|
| C1 | Entity registry (QHIN/Participant/Subparticipant) w/ submitted identifiers | `TEFCAEntity` | ✅ | UI-only (entity blade) |
| C2 | Six-source validation (NPPES, PECOS, LEIE, SAM.gov, RCE Dir, IQVIA) | `connectors.py`; `/connectors/status`; UI 4/6 live | 🟡 (4 live, 2 pending keys) | Backend (obtain RCE/IQVIA keys — **config, not code**) + UI |
| C3 | Four-bucket classification | `BucketClassification/Label` | ✅ | UI-only |
| C4 | Three-tier routing + human-in-the-loop | `TierAssignment`, `TEFCAAnalystQueue`; `/queue/tier2,tier3` | ✅ | UI-only (workbench) |
| C5 | Statistical sampling @95% CI (Cochran) `[CODE-REF]` | `/reviews/run-sample`, `/qa/sampling-validation` | ✅ | UI-only (sampling provenance) |
| C6 | **SOW Task 3** retrospective review + **D3.1 weekly** / **D3.2 final** reports `[CODE-REF]` | `/reports/weekly`, `/reports/final`, `CycleType.TASK3_RETROSPECTIVE` | ✅ | UI-only (reports library) |
| C7 | **SOW Task 4** ongoing review (bi-weekly + quarterly) `[CODE-REF]` | `/reports/biweekly`, `/reports/quarterly`, `/reviews/new-submissions`, `TASK4_ONGOING` | ✅ | UI-only |
| C8 | **Task 5** COR-directed priority reviews | `/priority/*`, `TEFCAPriorityCase`, `TASK5_PRIORITY` | ✅ | UI-only (COR SLA/deadline UX) |
| C9 | Five-element evidence record | `TEFCAEvidenceRecord`, `/evidence/generate` | ✅ | UI-only (guided capture) |
| C10 | Disposition recommendations (no-action/QHIN-minor/QHIN-corrective/ONC-escalate) | `DispositionRecommendation` | ✅ | UI-only |
| C11 | Role-based access (reviewer/senior_analyst/program_manager/qalead/COR/PII) | `require_role(...)` across routes | ✅ | UI-only (role landings) |
| C12 | Reporting: PDF + editable DOCX + CSV | `/reports/{id}/pdf,docx,csv`, `report_renderer.py` | ✅ | UI-only (library) |
| C13 | QA program (golden/regression/SLA/statistical/inter-rater/drift/alerts/scorecard) | `/qa/*` (20+ endpoints) | ✅ | UI-only (QA cockpit) |
| C14 | Evidence gate before report generation (chain-of-custody) | `/qa/report-gate`, `/qa/validate-evidence`, `/qa/audit` | ✅ | UI-only (surface audit trail) |
| C15 | Source-response caching (reproducibility/audit) | `TEFCASourceCache` | ✅ | UI-only (provenance/lineage) |
| C16 | Connector health/uptime | `TEFCAConnectorLog`, `/qa/connector-health` | ✅ | UI-only (uptime panel) |
| C17 | Honest MOCK vs PRODUCTION provenance | `is_mock_data`, `/status` | ✅ | UI-only (global banner) |
| C18 | Global entity search (NPI/name/QHIN + live NPPES) | `/search` | ✅ | UI-only (search surface) |
| C19 | Per-QHIN accuracy scoring vs 95% CI | UI Overview; `/dashboard/summary` | ✅ | UI-only (scorecards) |
| C20 | Executive/COR program-level dashboards | `/dashboard/summary,trends` | 🟡 (data exists; role views missing) | UI-only |
| C21 | Methodology & discrepancy taxonomy reference | `/methodology`, `/discrepancy-taxonomy` | ✅ | UI-only (Trust Center) |
| C22 | 508 / HIPAA / FISMA-Moderate conformance | UI chips (asserted) | 🟡 (claimed, unverified) | Docs-only (attach conformance artifacts) + UI links |
| C23 | Evaluation criteria / acceptance criteria / performance SLAs | — | ⛔ **Unknown** | **Docs-only — REQUIRES RFQ/PWS** |
| C24 | Full deliverables schedule (beyond D3.1/D3.2 code-refs) | partial `[CODE-REF]` | ⛔ **Unknown** | **Docs-only — REQUIRES RFQ/PWS** |

**Summary counts (implementation-derived):** Fully ✅ 18 · Partial 🟡 4 · Missing/Unknown ⛔ 2. **The two ⛔ are contract-knowledge gaps (C23, C24), not code gaps** — they can only be closed with the procurement documents. **Of the closeable gaps, ~20 are `UI-only`**, confirming the Phase-1 conclusion: this is a front-end maturation program on a complete backend. The only non-UI items are configuration (C2 keys) and documentation (C22, C23, C24).

---

## PART B — Screen Catalog (every screen → real endpoints)

Each screen lists Purpose · Persona · Data sources · APIs (real, existing) · KPIs · Charts · Workflow · Reports · Permissions.

### S1 · Executive "Program Health" (role landing)
- **Purpose:** one-screen program status. **Persona:** Executive/Leadership (P6).
- **Data:** aggregate reviews, cycles, QA, connectors. **APIs:** `GET /dashboard/summary`, `GET /dashboard/trends`, `GET /status`, `GET /qa/score`.
- **KPIs:** avg accuracy %, cycle on-time %, backlog risk, sources live (4/6), QA pass, deliverables due.
- **Charts:** accuracy trend (line), bucket distribution (donut), per-QHIN mini-bars. **Workflow:** view → drill to any operational surface.
- **Reports:** links to Reports Library. **Permissions:** `reviewer`+ (read); summary/trends are aggregate/public.

### S2 · COR / ONC "Oversight" dashboard
- **Purpose:** client oversight of network accuracy + deliverables. **Persona:** COR/ONC (P5).
- **Data:** per-QHIN scores, non-compliance, priority cases, reports. **APIs:** `GET /dashboard/summary`, `GET /priority`, `GET /qa/sla`, `GET /reports`, `GET /status` (provenance).
- **KPIs:** per-QHIN accuracy vs 95% CI, Bucket-4 count, open escalations, priority-case SLA, deliverable status.
- **Charts:** per-QHIN scorecard (bar+CI line), escalations table, priority-case timeline. **Workflow:** review → open priority case → track disposition.
- **Reports:** quarterly/final; priority COR status (`GET /priority/{id}/report`). **Permissions:** COR/`program_manager` (+priority create).

### S3 · Reviewer "My Queue" (role landing)
- **Purpose:** daily T2 work list. **Persona:** Reviewer (P1).
- **APIs:** `GET /queue/tier2`, `PATCH /queue/{id}/classify`, `PATCH /queue/{id}/escalate`, `GET /qa/sla`.
- **KPIs:** my open items, due-today, my accuracy (QA), throughput. **Charts:** queue aging (bars), SLA timers.
- **Workflow:** claim → open Entity Review (S4) → submit. **Permissions:** `reviewer`.

### S4 · Entity Review — 6-source evidence workbench ★ core screen
- **Purpose:** compare submitted vs 6 sources, classify, capture 5 elements, disposition. **Persona:** Reviewer/SME (P1/P2).
- **Data:** entity, source cache, taxonomy. **APIs:** `POST /validate/entity`, `POST /reviews/{id}/execute` (live connectors), `POST /evidence/generate`, `GET /search`, `GET /connectors/status`, `GET /discrepancy-taxonomy`, `PATCH /queue/{id}/classify|escalate`.
- **KPIs:** confidence score, # discrepant fields, source freshness. **Charts/UI:** side-by-side field-diff matrix (submitted | NPPES | PECOS | LEIE | SAM | RCE | IQVIA) with per-cell freshness/citation; bucket selector; guided 5-element form; disposition.
- **Workflow:** compare → classify bucket → 5 elements → disposition → (override w/ reason / escalate T3) → submit. **Reports:** feeds evidence record → cycle report. **Permissions:** `reviewer` (create), `senior_analyst` (supervisor sign-off on B4/override).

### S5 · SME / Tier-3 "Escalations"
- **Purpose:** adjudicate B4/inexplicable/indeterminate + supervisor review. **Persona:** Senior Analyst/SME (P2).
- **APIs:** `GET /queue/tier3`, `PATCH /queue/{id}/classify`, `POST /evidence/generate`, `GET /qa/inter-rater`.
- **KPIs:** T3 backlog, supervisor-review pending, override rate. **Charts:** backlog aging, escalation pipeline. **Workflow:** deep reconcile → confirm/adjust → approve ONC escalation. **Permissions:** `senior_analyst`.

### S6 · Cycles "Delivery" (Program Manager)
- **Purpose:** plan/run/monitor Task-3/4/5 cycles. **Persona:** Program Manager (P4).
- **APIs:** `POST /cycles`, `GET /cycles`, `POST /validate/batch`, `GET /validate/status/{cycle_id}`, `POST /reviews/run-sample`, `GET /sampling-runs`.
- **KPIs:** completion %, sample size vs target, queue depth/aging, throughput vs SLA. **Charts:** burn-down (line), aging (bars), per-QHIN completion (stacked). **Workflow:** plan sample (95% CI) → launch batch → monitor → complete → generate report. **Reports:** weekly/final/biweekly/quarterly. **Permissions:** `program_manager` (create), `reviewer` (view).

### S7 · Priority Cases (Task 5 / COR)
- **Purpose:** COR-directed case lifecycle. **Persona:** COR (P5), PM (P4), Reviewer (P1).
- **APIs:** `POST /priority/create`, `GET /priority`, `GET /priority/{id}`, `POST /priority/{id}/execute`, `GET /priority/{id}/report`, `POST /priority/quarterly-report`, `GET /qa/sla`.
- **KPIs:** open cases, overdue, avg time-to-resolution, severity mix. **Charts:** case timeline, SLA gauge, severity donut. **Workflow:** COR creates → assign → execute review → root-cause → disposition → COR status report. **Permissions:** COR/admin (create), `reviewer` (execute).

### S8 · Findings ledger
- **Purpose:** faceted discrepancy findings. **Persona:** PM/QA/COR.
- **APIs:** `GET /reports/export` (CSV, PII-gated), `GET /dashboard/summary`, `GET /discrepancy-taxonomy` (+ evidence records).
- **KPIs:** findings by bucket/connector/severity/QHIN. **Charts:** faceted table + severity/connector bars. **Workflow:** filter → inspect → export. **Permissions:** `reviewer` (view); CSV export `reviewer`+ (PII-gated).

### S9 · QA "Cockpit"
- **Purpose:** methodology integrity + SLA + drift. **Persona:** QA Lead (P3).
- **APIs:** `GET /qa/score`, `/qa/regression`, `/qa/golden-records`, `/qa/statistical`, `/qa/inter-rater`, `/qa/internal-consistency`, `/qa/sampling-validation`, `/qa/sla`, `/qa/sweep`, `/qa/alerts`, `/qa/evidence-summary`, `/qa/report-gate`, `/qa/audit`, `POST /qa/report`, `GET /qa/audit/export`.
- **KPIs:** golden pass %, regression/drift status, SLA %, CI adequacy, connector uptime. **Charts:** regression/drift trend (line), statistical checks (table), alert log, uptime. **Workflow:** monitor → investigate drift/alert → run sweep → sign QA scorecard. **Reports:** QA scorecard (PDF/DOCX). **Permissions:** `qalead` (actions), `reviewer` (view).

### S10 · Connectors & Sources
- **Purpose:** 6-source health/uptime/lineage + key status. **Persona:** Operations/Admin (P7).
- **APIs:** `GET /connectors/status`, `GET /qa/connector-health`, `GET /status`.
- **KPIs:** uptime %, latency, 4/6 live, 2 pending keys. **Charts:** uptime timeline, latency bars, cycle-impact. **Workflow:** monitor → flag key-pending impact. **Permissions:** `reviewer`+ (view); admin (config note).

### S11 · Reports Library
- **Purpose:** generate/browse/download all deliverables. **Persona:** PM/COR/QA.
- **APIs:** `GET /reports`, `GET /reports/{id}`, `/reports/{id}/pdf|docx|csv`, `POST /reports/weekly|final|biweekly|quarterly`, `POST /qa/report`, `GET /qa/report-gate`.
- **KPIs:** reports by type/period/status, submission state, provenance. **Charts:** library table (type, period, ●MOCK/●PRODUCTION, formats, submitted). **Workflow:** select period/type → (evidence gate check) → generate → download → track submission. **Permissions:** `program_manager` (generate), `reviewer` (view/download).

### S12 · Data Import
- **Purpose:** ingest RCE directory / COR case lists with validation. **Persona:** PM/Admin.
- **APIs:** `GET /reviews/new-submissions`, `POST /validate/batch`, `GET /mock/entities` (dev), admin seed.
- **KPIs:** rows imported, validation errors, new vs updated. **Charts:** import summary, diff preview. **Workflow:** upload → validate → preview diffs → confirm → provenance-stamp (rollback if needed). **Permissions:** `program_manager`/admin.

### S13 · Trust Center / Compliance
- **Purpose:** 508/FISMA/HIPAA evidence + immutable audit trail + lineage. **Persona:** Compliance/COR/Admin.
- **APIs:** `GET /qa/audit`, `GET /qa/audit/export`, `GET /status`, `GET /methodology`.
- **KPIs:** audit events, control status, provenance coverage. **Charts:** audit timeline, control matrix. **Workflow:** browse audit/chain-of-custody → export → link conformance artifacts (AGT to supply). **Permissions:** `reviewer`+ (view), admin (config).

### S14 · Admin — Users & Access / Methodology
- **Purpose:** role management + methodology/taxonomy config. **Persona:** Admin (P7).
- **APIs:** `GET /methodology`, `GET /discrepancy-taxonomy` (+ app auth user mgmt).
- **KPIs:** users by role, methodology version. **Workflow:** manage roles → view methodology/taxonomy versions. **Permissions:** admin.

### S15 · Global Search (utility, in command bar)
- **Purpose:** find any entity fast. **APIs:** `GET /search` (NPI/name/QHIN + live NPPES). **Permissions:** `reviewer`+.

---

## PART C — TEFCA ARC Product Blueprint (master design spec)

### C.1 Design system
Fluent-2-aligned, **federal, 508-first, WCAG 2.1 AA**. Command bar (context + primary action) · left **role-aware rail** · card-grid dashboards · faceted data grids w/ export · resource **blades** (entity/connector/report) · drill-through everywhere · persistent **data-provenance banner** (● MOCK / ● PRODUCTION) · semantic status colors that pass contrast (No-Discrepancy/green, Minor/amber, Inexplicable/violet, Non-Compliant/red) · keyboard-first, screen-reader labeled.

### C.2 Information architecture (single role-aware nav — supersedes dual-nav)
`HOME (role landing) · REVIEW {My Queue, Entity Review, Priority Cases, Findings} · CYCLES & SAMPLING {Review Cycles, Sampling, Data Import} · ANALYTICS & REPORTS {Executive/COR, QHIN Scorecards, QA Cockpit, Connectors, Reports Library} · ADMIN {Users & Access, Methodology, Trust Center}`. Retire in-page tabs that duplicate destinations.

### C.3 Role → landing map
Reviewer→S3 · SME→S5 · PM→S6 · QA Lead→S9 · COR→S2 · Exec→S1 · Admin→S14.

### C.4 Global patterns
Provenance banner on every screen · "as-of {cycle} · {timestamp}" on every KPI · evidence-gate indicator before any report generate · override/escalate always require a reason · export (CSV/PDF/DOCX) on every data view · empty/loading/error states standardized.

### C.5 Non-functional standards
508/WCAG AA conformance target; performance budget for dense grids (virtualized tables); consistent auth-error and permission-denied UX per role; no client-side PII caching; provenance/lineage visible for every finding.

### C.6 Guardrails (unchanged from constraints)
No backend/API/DB/auth/scheduler/deployment change is required or permitted; every screen above maps to an **existing** endpoint. Two live-data items (TEFCA entity data, IQVIA OneKey) await **API keys** (configuration), not code.

---

## Next step (blocking item made explicit)
1. **AGT provides the procurement documents** (RFQ, PWS/SOW, amendments, Q&A, evaluation criteria, deliverables schedule, acceptance criteria) — paste text, drop files in `docs/tefca-contract/`, or share the real solicitation link. On receipt I will convert **Part A** from *implementation-derived* to *contract-traced* (clause-by-clause), confirm C23/C24, and validate every screen against acceptance criteria.
2. **AGT reviews & approves this Product Blueprint** as the authoritative design specification.
3. **No UI implementation proceeds** until approval.

*This document modified no code, backend, API, authentication, scheduler, database, or deployment. Every implemented claim is grounded in `app/Tefca/` source or the live application; contract-knowledge gaps are labeled and require the procurement documents — none were fabricated.*
