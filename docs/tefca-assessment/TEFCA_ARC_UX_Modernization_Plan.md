# TEFCA Audit, Review & Compliance (ARC) — Enterprise UX Modernization Plan
### World-class product-experience redesign · backend unchanged · blueprint for future UI implementation

**Prepared:** 2026-07-09
**Product identity (official):** **TEFCA Audit, Review & Compliance (ARC)** — hereafter **ARC**.
**Builds on (verified backend facts):** `TEFCA_ARC_Enterprise_Architecture_Assessment.md`.
**Absolute guardrails:** No code, API, authentication, database schema/tables, business logic, connectors, scheduler, deployment, or infrastructure is changed. No microservices/Kubernetes/Azure/Dev-vs-Prod discussion. **Every recommendation is UI/UX only and 100% backward-compatible on the existing endpoints.**

> Tagging: `[VERIFIED]` grounded in source/live app · `[RECOMMENDED]` proposed design. Every screen maps to an **existing** endpoint (see Enterprise Assessment for the API surface).

---

## 1. Executive Architecture Review
ARC's backend is **federally rigorous** `[VERIFIED]` — Cochran 95%-CI sampling (N=94,231→n=383, 11 QHINs), fail-closed six-source validation, worst-finding bucketing, five-element evidence with chain-of-custody, evidence-gated reporting, immutable QA audit, and an explicit *"AGT produces findings; the ONC COR determines."* posture. **The problem is the experience, not the protocol.** Today ARC presents as a single dense dashboard with dual navigation, role-blind landings, static charts, demo-data inconsistencies, and — critically — a **procurement number in the operational header**. This plan modernizes ARC into a **role-driven, Fluent-aligned federal platform** comparable to Azure Portal / Power BI / Purview, delivered entirely on the current backend.

**Design thesis:** *One platform, ten roles, one lifecycle.* Every user lands in a workspace scoped to their job; the review lifecycle (Import → Validate → Review → QA → Disposition → Report → Closeout) is the spine; evidence and provenance are always visible; nothing procurement-facing leaks into operational screens.

---

## 2. Product Assessment (current state — condensed)
`[VERIFIED]` Strengths: correct domain model; honest MOCK/PRODUCTION labeling; rich QA; per-QHIN accuracy; PDF/DOCX/CSV reporting; 60+ endpoints already power everything below.
`[VERIFIED]` Problems: (P1) procurement number `7571MN26Q00027` shown in header — **wrong number** (FCC placeholder) *and* wrong to show at all; (P2) dual navigation with duplicate destinations; (P3) no role-based landing; (P4) no end-to-end reviewer workspace; (P5) demo-data inconsistencies on the flagship screen; (P6) static, non-interactive charts; (P7) QA/analytics under-surfaced; (P8) no executive/COR view.

---

## 3. Product Identity & Naming `[RECOMMENDED]`
- **Display name everywhere:** *TEFCA Audit, Review & Compliance (ARC)*; short form **ARC**.
- **Never** display Solicitation / Contract / Award / Task-Order / Procurement IDs in operational UI. **Remove** the number from the header.
- Contract metadata (award number `7571MN26F80064` `[VERIFIED in code]`, contractor, period of performance) lives **only** at **Administration → System Information / About This System**, visible to Admin/Security roles.
- Header shows: product wordmark + agency context ("HHS ONC · TEFCA") + environment-neutral status + user/role. No compliance-badge clutter in the operational chrome (badges move to Trust Center).

---

## 4. Design System — UI & UX Standards (Fluent-aligned) `[RECOMMENDED]`
A single design system a UI team can implement directly.

**4.1 Foundations**
- **Type:** Segoe UI Variable / system stack. Scale: Display 40 · H1 28 · H2 22 · H3 18 · Body 14 · Caption 12; line-height 1.4; weights 400/600/700.
- **Spacing:** 4-pt grid (4/8/12/16/24/32/48). Generous whitespace; content max-width 1440 with fluid panels.
- **Elevation:** 3 levels (card 0-1-2) with soft shadows; radius 8; hairline #E1E1E1 borders.
- **Grid:** 12-column responsive; collapsible left nav (280 → 48 rail).
- **Motion:** 150–250ms ease; drill-through slide; skeleton loaders.

**4.2 Color (WCAG AA)**
- Brand navy `#003366`; accent `#0F6CBD` (Fluent blue). Neutrals `#FAFAFA/#F3F2F1/#E1E1E1/#605E5C/#242424`.
- **Semantic status = ARC buckets** (color + icon + label, never color-alone): No-Discrepancy `#107C10`✓ · Minor/Admin `#B88217`⚠ · Inexplicable `#8764B8`? · Non-Compliant `#C50F1F`✗ · **Indeterminate** (source unavailable) `#605E5C`◑ (a first-class state `[VERIFIED: fail-closed]`).
- Risk chips: low/medium/high/critical mirror bucket ramp.

**4.3 Components (spec)**
KPI tile (value + label + trend delta + sparkline + as-of) · status chip · faceted data grid (virtualized, sortable, column-chooser, saved views, inline export) · card · blade/side-panel (entity, connector, report) · command bar (context + primary + overflow) · filter bar (chips + date range + saved views) · chart set (line/area/bar/stacked/donut/gauge; Power BI-style tooltips + drill-through) · workflow timeline · evidence panel · activity/audit feed · wizard (stepper) · empty/loading/error states (standardized) · **global provenance banner** (● PRODUCTION / ● MOCK).

**4.4 UX standards**
Role-based landing · ≤3 clicks to any primary task · every KPI carries "as-of {cycle} · {timestamp}" · drill-through on every metric · export (CSV/PDF/DOCX) on every data view · override/escalate always require a reason `[VERIFIED rule]` · keyboard-first · never show a fabricated value (mirror the backend's fail-closed honesty).

**4.5 Accessibility (Section 508 / WCAG 2.1 AA)**
Keyboard operable; visible focus; SR labels/landmarks; color-independent status; contrast-checked palette; data-table fallback for every chart; accessible grids (row/col headers, announced sort/filter). Move the "508 Compliant" claim to Trust Center **linked to a real conformance artifact** (asserted-not-verified today `[VERIFIED gap]`).

---

## 5. Information Architecture & Navigation Blueprint `[RECOMMENDED]`
Collapse today's dual nav (rail + duplicate in-page tabs `[VERIFIED problem]`) into **one role-aware left rail**, grouped by the review lifecycle:

```
ARC
■ HOME                     role-aware landing → routes to the right workspace
■ REVIEW
   ├ My Work               reviewer/SME queue
   ├ Entity Review         6-source evidence workspace  ★ core
   ├ Priority Cases        COR-directed (Task 5)
   └ Findings              faceted discrepancy ledger
■ CYCLES & SAMPLING
   ├ Review Cycles         Task 3/4/5, burn-down
   ├ Sampling              Cochran 95% CI, provenance
   └ Data Import           wizard
■ ANALYTICS & REPORTS
   ├ Executive / COR
   ├ QHIN Scorecards
   ├ QA Cockpit
   ├ Connectors & Sources
   ├ Reports Library
   └ Explore               self-serve analytics
■ ADMINISTRATION
   ├ Users & Access
   ├ Methodology & Taxonomy
   ├ Trust Center          508/FISMA/HIPAA evidence + audit
   └ System Information     ← the ONLY place contract metadata appears
```
Rules: no duplicate destinations; retire in-page tabs that duplicate rail items; command bar + global entity search `[VERIFIED /search]`; breadcrumb + as-of context.

---

## 6. Role-based Experience & User Journeys `[RECOMMENDED]` (roles per `[VERIFIED]` guards + `[INFERRED]`)
| Role | Lands on | Primary journey |
|---|---|---|
| Executive Leadership | Executive dashboard | health → trend → exception drill-through |
| COR / ONC | COR Oversight | per-QHIN scorecards → escalations → open Task-5 case → deliverables |
| Program Manager | Cycle Delivery | plan sample → launch → monitor burn-down → generate report |
| QA Manager / Analyst | QA Cockpit | golden/regression/drift → SLA → sign scorecard |
| Reviewer / Compliance Analyst | My Work → Entity Review | claim → compare 6 sources → 5-element → disposition → submit |
| Senior Reviewer (SME) | Escalations | T3/B4 adjudication → supervisor sign-off → ONC escalation |
| Auditor | Trust Center | audit timeline → chain-of-custody → export |
| Healthcare Analyst | Explore | filter by QHIN/connector/bucket → export |
| Operations / Support | Connectors & Sources | uptime/latency/key-status → cycle impact |

**Reviewer journey (VERIFIED lifecycle):** My Work → open entity → 6-source diff auto-loaded → confidence + finding codes shown → pick bucket → guided 5 elements → disposition (deadline auto-set: B2 30d/B3 21d/B4 10d) → submit; B4/override → supervisor review → escalate.

---

## 7. Dashboard Blueprint (role-scoped) `[RECOMMENDED]`
Each dashboard shows **only** its role's decisions; all on `/dashboard/summary`,`/dashboard/trends`,`/qa/*` `[VERIFIED]`.
- **Executive:** accuracy trend (line), cycle on-time %, backlog-risk gauge, sources-live (4/6), QA status, deliverables-due. Green/amber/red, one screen.
- **COR:** per-QHIN scorecard (bar vs 95%-CI line), Bucket-4 & escalations table, priority-case SLA, deliverable status, **provenance banner**.
- **Program Manager:** active-cycle burn-down, queue depth/aging, reviewer throughput vs SLA, per-QHIN completion, report-readiness.
- **QA:** golden pass %, regression/drift trend, statistical checks (Cochran n=383, Wilson CI), SLA %, connector uptime, alert log.
- **Reviewer:** my queue (priority-sorted), due-today, my accuracy (QA feedback), SLA timers.
- **Operations:** 6-source uptime/latency, 2 keys pending, cycle impact, error log.
- **Analytics/Compliance:** self-serve Explore; Trust Center audit posture.

---

## 8. Review Workspace (the crown jewel — Purview-class) `[RECOMMENDED]`
A single split-screen workspace; everything in one place; maps to `/validate/entity`, `/reviews/{id}/execute`, `/evidence/generate`, `/search`, `/connectors/status`, `/discrepancy-taxonomy`, `/queue/{id}/classify|escalate` `[VERIFIED]`.

```
┌ Command bar: ◀Queue · Entity · [Save draft] [Submit] [Escalate T3] · provenance ●────┐
├── LEFT: ENTITY PANEL ──┬── CENTER: SOURCE-VALIDATION MATRIX ──┬── RIGHT: DECISION ───┤
│ Legal name, NPI, UEI   │ Field | Submitted|NPPES|PECOS|LEIE|  │ Confidence 0.82 ◑     │
│ Type, QHIN, address    │ SAM|RCE  (diffs highlighted; each     │ Bucket ○1 ●2 ○3 ○4    │
│ Endpoints, FHIR ▸      │ cell: freshness · citation · hash)   │ Finding codes (auto): │
│ Risk score · status    │ ● Indeterminate flag if a required   │  NAME_ABBREVIATION…   │
│ Validation history ▾   │   source unavailable (fail-closed)   │ 5-ELEMENT (guided):   │
│ Comments / notes       │ [view raw source JSON]               │ 1✓2✓3✓4 cite 5 dispo  │
│                        │                                      │ Disposition ▾ + reason│
│                        │                                      │ Deadline: 30d (B2)    │
├── BOTTOM: AUDIT TIMELINE (who/what/when · source diffs · override reasons) ──────────┤
```
Includes: evidence panel, entity panel, decision panel, validation history, risk/confidence indicators, source validation, workflow status, comments, reviewer notes, disposition, audit trail — all on one screen.

---

## 9. Reporting Blueprint `[RECOMMENDED]` (on `[VERIFIED]` report engine)
- **Reports Library**: faceted (type · period · status · ● MOCK/PRODUCTION · format), submission tracking, one-click regenerate; **evidence-gate indicator must be green before generate** `[VERIFIED /qa/report-gate]`.
- **Executive-quality report templates** for the 7 existing types (Weekly D3.1, Final D3.2, Bi-weekly, Quarterly, Priority COR, Priority-Quarterly, QA Scorecard): cover page (ARC identity, **no procurement ID on face** — footer only, Admin-level), executive summary, KPI band, Power BI-style charts (already Recharts-ready in quarterly `[VERIFIED]`), per-QHIN scorecards, methodology footnote, provenance. Formats unchanged (PDF/DOCX/CSV 12-col `[VERIFIED]`).

---

## 10. Analytics Blueprint `[RECOMMENDED]`
Executive analytics on existing aggregates + QA: KPIs, accuracy/discrepancy trends, risk by QHIN, review volume/distribution, reviewer productivity, SLA adherence, connector health, validation/evidence quality, compliance trends. Add drill-through, cross-filter, time-range, **saved views**, faceting (QHIN/connector/bucket/tier/cycle), export. Map/geographic view by entity_state `[VERIFIED field]` for regional distribution.

---

## 11. Data Import Experience `[RECOMMENDED]`
Professional wizard (stepper): **Upload → Map → Validate → Preview diffs → Confirm → Provenance-stamp → History (rollback)**. Surfaces new-vs-updated, validation errors inline, MOCK/PRODUCTION stamp, and an import audit log. On `/reviews/new-submissions`, `/validate/batch` `[VERIFIED]`. (RCE Directory is MOCK until Sequoia key — config, not code `[VERIFIED]`.)

---

## 12. Connectors UX (experience only) `[RECOMMENDED]`
Card-per-source (NPPES · PECOS · LEIE · SAM.gov · RCE · IQVIA): live/mock/pending-key badge, uptime %, latency band (`[VERIFIED weights]` 0.40 avail/0.25 latency/0.20 freshness/0.15 schema), last-checked, cycle-impact, and honest notes ("SAM.gov requires registered key"; "PECOS payment-suspension pending COR feed") — **display only; no connector logic touched**.

---

## 13. QA Operations — Quality Management Workspace `[RECOMMENDED]`
QA cockpit surfacing the existing engine `[VERIFIED]`: QA overall score (threshold 85), sampling adequacy (Cochran n=383 + Wilson CI), confidence distribution, **drift** (golden-record regression, 8 cases), reviewer quality, evidence-gate status, audit quality, SLA (critical 2/high 5/medium 10/low 21 days), alert log. Honesty preserved: label internal-consistency as **not** inter-rater reliability `[VERIFIED disclaimer]`.

---

## 14. Workflow Blueprint `[VERIFIED lifecycle]`
`Import → Validation → Review → QA → Disposition → Reporting → Closeout`, with **Indeterminate** as a first-class branch (source unavailable ⇒ Tier-2, never auto-complete). The UI mirrors this as a persistent lifecycle tracker on cycle and entity views.

---

## 15. Screen Inventory & Screen-by-Screen Redesign `[RECOMMENDED]`
For each: Purpose · Current problems · Business/usability gaps · Redesign · Wireframe (see §16) · Justification · HHS alignment.

| # | Screen | Current problems | Redesign essence | HHS alignment |
|---|---|---|---|---|
| 1 | **Home/Overview** | procurement # shown; role-blind; dense; demo-data inconsistencies; static | Replace with **role-aware landing**; remove procurement #; fix data + as-of; interactive KPIs | Federal exec expectation of at-a-glance, trustworthy status |
| 2 | **Entity Review** | no unified workspace | **§8 Purview-class split-screen** | Defensible, evidence-based determinations |
| 3 | **My Work (queue)** | generic list | priority-sorted queue, SLA timers, one-click open | Reviewer efficiency/throughput |
| 4 | **Escalations (T3)** | mixed with queue | dedicated SME/B4 surface + supervisor sign-off | ONC-escalation quality |
| 5 | **Review Cycles** | list-only | burn-down/aging, sample provenance | Program delivery/SLA |
| 6 | **Sampling** | opaque | show Cochran inputs/outputs, stratification, seed | Statistical defensibility |
| 7 | **Priority Cases** | no SLA/timeline | COR case lifecycle + deadlines + timeline | COR directive tracking |
| 8 | **Findings** | flat | faceted ledger + export | Audit/analysis |
| 9 | **Reports** | not a library | Reports Library + evidence-gate + provenance | Deliverable management |
| 10 | **QA Operations** | under-surfaced | §13 QA cockpit | Methodology integrity |
| 11 | **Connectors** | status-only | §12 uptime/latency/key panel | Source reliability |
| 12 | **Analytics/Trust** | overlapping | split: Explore (analytics) + Trust Center (audit/compliance) | Compliance & oversight |
| 13 | **Data Import** | thin | §11 wizard | Governed ingestion |
| 14 | **Admin** | mixed | Users/Access + Methodology + Trust + **System Information (contract metadata here only)** | Governance & procurement separation |

---

## 16. Wireframes (layouts only)
See §5 (nav), §7 (Executive/COR), §8 (Review Workspace), plus:

**Cycle Delivery (PM)**
```
[Task3 ▾][+Plan]  Cycle 12 · 95% CI · sample 383/383 ▓▓▓▓▓▓▓ 100%
Burn-down (line) ╲╲__  | Queue aging (bars) 0-1d|2-3d|4d+ | Throughput vs SLA
Per-QHIN completion (stacked)                         [Generate cycle report ▸]
```
**Reports Library**
```
Type ▾ Period ▾ Status ▾ Provenance ▾ [Generate ▾]
Quarterly · Q2 · ●PRODUCTION · evidence-gate ✓ · [PDF][DOCX][CSV] · Submitted
Priority · CASE-014 · ●MOCK · [PDF][DOCX] · Draft
```
**QA Cockpit**
```
[QA 92 ●][Golden 8/8 ●][Drift None ●][SLA 96% ●][Uptime 4/6]
Regression/drift trend (line) | Sampling n=383 + Wilson CI | Alerts (list)
```
**Data Import Wizard**
```
①Upload ─ ②Map ─ ③Validate ─ ④Preview diffs ─ ⑤Confirm ─ ⑥History
 42 new · 8 updated · 3 errors ⚠   [Back][Next]      ●MOCK stamp
```

---

## 17. Benchmark — why each decision
| Decision | Benchmark | Rationale |
|---|---|---|
| Role-aware rail + blades | **Azure Portal** | scalable, discoverable, low click-cost |
| Role dashboards + drill-through + saved views | **Power BI / Fabric** | governed, self-serve, executive-grade |
| Provenance/lineage + audit timeline | **Purview** | data trust + chain-of-custody |
| Severity queue → investigation workspace | **Defender / Sentinel** | efficient triage of high-severity work |
| Case lifecycle + SLA + approvals | **ServiceNow / Dynamics 365** | COR case management |
| One-object-many-sources reconciliation | **Palantir Foundry** | the 6-source review workspace |
| Role homepages + 508 + audit | **Salesforce Lightning / federal** | enterprise federal credibility |

Outcome: ARC reads as an **enterprise federal platform**, not a CRUD/template app.

---

## 18. Product Roadmap & Prioritized Improvement Plan
UI-only; every item on existing endpoints; backward-compatible.

| Phase | Theme | Highest-value items | Effort |
|---|---|---|---|
| **P1 Quick Wins** | Trust & identity | Rename to ARC everywhere; **remove procurement # from header → System Information**; fix flagship data + as-of/provenance banner; consolidate to single role-aware nav | **S–M** |
| **P2 Core Loop** | Reviewer productivity | **Review Workspace (§8)**; My Work landing; Escalations; Findings faceting/export | **M–L** |
| **P3 Enterprise UX** | Program & reporting | Cycle Delivery; Reports Library; QA Cockpit; Connectors panel; Data Import wizard | **M–L** |
| **P4 Executive Analytics** | Leadership visibility | Executive + COR dashboards; QHIN scorecards; Explore (drill/saved views); Trust Center + audit timeline | **M** |
| **P5 Future** | Intelligence & reach | AI decision-support (triage/evidence-draft/root-cause/NL-query — human-approved); responsive exec/COR view; 508/AA conformance hardening | **L** |

**Prioritization logic:** P1 fixes credibility (identity, trust, nav) fastest and cheapest; P2 delivers the biggest daily-productivity gain (the workspace); P3–P4 complete the enterprise/executive experience; P5 is optional intelligence. **Dependencies:** design system (§4) precedes all; each phase builds on the prior. **Risk:** the Review Workspace is the pivotal build — prototype and usability-test it first.

---

## Closing
ARC's protocol is already federal-grade; this plan makes the **experience** match it — Fluent-aligned, role-driven, evidence-first, provenance-honest, procurement-clean — and hands a UI team an implementable blueprint that leaves the backend **entirely untouched**. **Nothing here changes code, APIs, schema, auth, connectors, scheduler, deployment, or infrastructure.** The only non-UI items (out of scope, noted for completeness) are three pending connector keys (configuration) and a 508/FISMA conformance artifact (documentation).

*No code, backend, API, database, authentication, deployment, or infrastructure was modified or discussed; no Dev-vs-Production or Azure discussion. `[VERIFIED]` items are grounded in `app/Tefca/` source or the live app; `[RECOMMENDED]` items are design proposals; no contract requirement was fabricated.*
