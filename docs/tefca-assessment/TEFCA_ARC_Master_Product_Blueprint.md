# TEFCA ARC — Master Product Blueprint
### Authoritative design document for all future TEFCA ARC UI work

**Prepared:** 2026-07-09
**Supersedes/consolidates:** `TEFCA_ARC_Architecture_and_UX_Assessment.md` + `TEFCA_ARC_Product_Blueprint.md` (kept for history).
**Constraint honored:** No code, backend, API, database, authentication, scheduler, deployment, or infrastructure was modified or discussed. Assessment only.

### Evidence-tagging methodology (applied throughout — never mixed)
- **`[VERIFIED]`** — directly observed in the existing implementation: `app/Tefca/models.py`, `app/Tefca/routes.py`, connector code, or the live application UI.
- **`[INFERRED]`** — a reasonable, logic-based deduction from verified evidence (e.g., persona goals from role names). Not asserted as fact.
- **`[RECOMMENDATION]`** — a proposed future design. Not present today; not a requirement.

> No HHS/ONC contract requirement is asserted anywhere in this document. Contract traceability is deferred to **Deliverable 2** and requires the procurement documents.

---

# DELIVERABLE 1 — TEFCA ARC Product Blueprint

## 1. Executive Vision
**`[RECOMMENDATION]`** TEFCA ARC becomes the **federal-grade system of record for TEFCA directory-data integrity** — a role-driven workbench where reviewers adjudicate discrepancies with full six-source evidence, program managers run statistically-valid cycles on schedule, QA proves methodology integrity, and ONC/COR leadership sees network accuracy and deliverables at a glance — every finding defensible, provenance-labeled, and audit-ready.

## 2. Product Vision
**`[RECOMMENDATION]`** One platform, four experiences (do the work · run the program · prove the quality · oversee the network), on a **single role-aware information architecture**, Fluent-2-aligned, 508-first, with a persistent MOCK/PRODUCTION provenance signal so trust is never ambiguous.

## 3. Business Objectives
- **`[INFERRED]`** Maximize directory accuracy across the TEFCA network (QHINs/Participants/Subparticipants) — the platform's entire data model exists to detect and classify discrepancies `[VERIFIED: models.py]`.
- **`[INFERRED]`** Deliver review cycles and reports on time (weekly/bi-weekly/quarterly/final) — report generators exist for each `[VERIFIED: routes.py /reports/*]`.
- **`[INFERRED]`** Keep every finding defensible and reproducible — source-response caching + evidence gate + audit trail exist `[VERIFIED: TEFCASourceCache, /qa/report-gate, /qa/audit]`.
- **`[RECOMMENDATION]`** Reduce reviewer time-per-entity and escalation rework via a guided workbench; give leadership a single health view.

## 4. User Personas
Roles are **`[VERIFIED]`** (`require_role`: `reviewer`, `senior_analyst`, `program_manager`, `qalead`; admin user observed; PII-gated exports; COR referenced in model `cor_reference`/`PENDING_COR`). Persona goals/pain points are **`[INFERRED]`**.

| Persona | Role evidence | Goals (inferred) | Key screens |
|---|---|---|---|
| **Reviewer (T2)** | `reviewer` `[VERIFIED]` | Clear queue accurately/on-time; produce 5-element evidence | My Queue, Entity Review |
| **Senior Analyst / SME (T3)** | `senior_analyst` `[VERIFIED]` | Adjudicate B4/inexplicable; supervisor sign-off; ONC escalation | Escalations, Entity Review |
| **QA Lead** | `qalead` `[VERIFIED]` | Prove statistical validity, drift-free, on-SLA | QA Cockpit |
| **Program Manager** | `program_manager` `[VERIFIED]` | Run cycles on schedule; staffing; deliver reports | Cycles, Reports |
| **COR / ONC** | `cor_reference`, `PENDING_COR`, priority-create admin `[VERIFIED]` | Oversee accuracy; direct Task-5 reviews; consume deliverables | COR Oversight, Priority Cases |
| **Executive / Leadership** | `[INFERRED]` | Program health at a glance | Executive dashboard |
| **Administrator** | admin user `[VERIFIED: admin@docuaction.io]` | Manage roles, connectors, methodology | Admin, Trust Center |

## 5. Information Architecture
- **`[VERIFIED]` Current:** dual navigation — a left rail group "Federal Compliance" (Overview, Data Import, Review Cycles, Entity Reviews, Validation Queue, Priority Reviews, Findings, Reports, QA Operations, Connectors, Analytics) **plus** in-page tabs (Overview/Review Queue/Reports/Sampling/Methodology); "Validation Queue" and "Analytics" appear in more than one nav group.
- **`[RECOMMENDATION]` Target:** one role-aware rail, four groups, no duplicate destinations, no competing tab system:
```
HOME (role landing)
REVIEW            My Queue · Entity Review · Priority Cases · Findings
CYCLES & SAMPLING Review Cycles · Sampling · Data Import
ANALYTICS/REPORTS Executive/COR · QHIN Scorecards · QA Cockpit · Connectors · Reports Library
ADMIN             Users & Access · Methodology · Trust Center
```

## 6. Navigation Architecture
- **`[VERIFIED]`** Global left rail; user/role chip; module groups (Operations, Intelligence, Federal Compliance, Governance, Admin).
- **`[RECOMMENDATION]`** Add a top **command bar** (context + primary action + global search `[VERIFIED: /search]`); **role→landing routing**; breadcrumb + "as-of {cycle}/{timestamp}"; retire duplicate destinations.

## 7. Module Architecture `[VERIFIED: models.py + routes.py]`
Entity registry · Review cycles (Task 3/4/5) · Tier-1 validation engine (6 connectors) · Analyst queue (T2/T3) · 5-element evidence records · Priority cases (COR/Task 5) · Reporting (weekly/final/biweekly/quarterly/priority/QA; PDF/DOCX/CSV) · QA engine (golden/regression/SLA/statistical/inter-rater/drift/alerts/evidence-gate/audit) · Source cache · Connector health logs · Dashboard aggregates · Global search.

## 8. Screen Inventory
**`[VERIFIED]` Current screens** (nav-observed): Overview, Data Import, Review Cycles, Entity Reviews, Validation Queue, Priority Reviews, Findings, Reports, QA Operations, Connectors, Analytics, Trust Center.
**`[RECOMMENDATION]` Target screen set** (15; each maps to existing endpoints — see prior Screen Catalog for full API mapping):
S1 Executive Health · S2 COR Oversight · S3 Reviewer My-Queue · **S4 Entity Review 6-source workbench (core)** · S5 SME Escalations · S6 Cycle Delivery · S7 Priority Cases · S8 Findings · S9 QA Cockpit · S10 Connectors · S11 Reports Library · S12 Data Import · S13 Trust Center · S14 Admin · S15 Global Search.

## 9. Dashboard Inventory
- **`[VERIFIED]`** Existing Overview shows KPIs, 4-bucket distribution, per-QHIN accuracy (10 QHINs vs 95% CI), 3-tier routing, 6-source status. `/dashboard/summary` + `/dashboard/trends` exist.
- **`[RECOMMENDATION]`** Split into role dashboards: Executive Health, COR Oversight, PM Delivery, QA Cockpit, Reviewer My-Work, SME Escalations, Ops Connectors, Compliance Trust Center, self-serve Explore.

## 10. Review Workflow `[VERIFIED: models + endpoints]`
```
Ingest entities → plan sample (95% CI) → Tier-1 batch validate vs 6 sources
 → auto-classify bucket + confidence
   ├─ B1 (No Discrepancy) → auto-complete (Tier 1)
   └─ B2/B3/B4 or indeterminate → Analyst Queue
        → Tier-2 reviewer: 5-element evidence + disposition
             ├─ resolve → FINALIZED
             └─ B4 / override → Tier-3 SME supervisor review → escalate ONC
 → evidence gate (QA) → generate cycle report (PDF/DOCX/CSV) → (Task 5 priority cases as directed)
```
Statuses `[VERIFIED]`: EntityStatus, RecordStatus (DRAFT→PENDING_REVIEW→REVIEWED→FINALIZED), QueueStatus, CaseStatus (…→PENDING_COR→RESOLVED/ESCALATED).

## 11. Analytics Strategy
- **`[VERIFIED]`** aggregates + monthly trends exist (`/dashboard/summary`, `/dashboard/trends`), per-QHIN accuracy, bucket %, tier counts.
- **`[RECOMMENDATION]`** Add drill-through, cross-filtering, time-range, saved views; faceting by QHIN/connector/bucket/tier/cycle; exportable everywhere.

## 12. Reporting Strategy
- **`[VERIFIED]`** Report types: weekly (D3.1 `[CODE-REF]`), final (D3.2 `[CODE-REF]`), bi-weekly, quarterly, priority COR status, QA scorecard; formats PDF + editable DOCX + CSV; **evidence gate** required before generation (`/qa/report-gate`); MOCK/PRODUCTION provenance stamped.
- **`[RECOMMENDATION]`** First-class **Reports Library**: filter by type/period/status/provenance; submission tracking; one-click regenerate.

## 13. Data Flow `[VERIFIED]`
```
Submitted entity (RCE dir / import) ──► Validation engine ──► 6 connectors (NPPES, PECOS,
 LEIE, SAM.gov, RCE Dir, IQVIA) ──► source cache (hash + freshness) ──► bucket + confidence
 ──► evidence record (5 elements + citations) ──► queue/disposition ──► report + audit trail
```

## 14. Connector Architecture `[VERIFIED]`
Six authoritative sources; **4 live** (NPPES · PECOS · OIG-LEIE · SAM.gov) and **2 pending API keys** (RCE Directory / FHIR · IQVIA OneKey) — health/latency logged (`TEFCAConnectorLog`, `/connectors/status`, `/qa/connector-health`). **`[RECOMMENDATION]`** uptime/latency panel + key-status + cycle-impact. *(Key provisioning is configuration, not code.)*

## 15. Role Matrix `[VERIFIED: require_role]`
| Capability | reviewer | senior_analyst | program_manager | qalead | admin/COR |
|---|:--:|:--:|:--:|:--:|:--:|
| View dashboards / findings | ✅ | ✅ | ✅ | ✅ | ✅ |
| Work T2 queue / evidence | ✅ | ✅ | ✅ | ✅ | ✅ |
| T3 escalation / supervisor review | — | ✅ | ✅ | ✅ | ✅ |
| Create/plan cycles; generate reports | — | — | ✅ | — | ✅ |
| QA actions / alerts test / methodology | — | — | — | ✅ | ✅ |
| Create priority (Task 5) cases | — | — | ✅ | — | ✅ (COR/admin) |
| Export CSV (PII-gated) | ✅* | ✅ | ✅ | ✅ | ✅ |
*PII-gated `[VERIFIED]`.

## 16. Security Model
- **`[VERIFIED]`** Every route authenticated; role-floor via `require_role`; a small set of aggregate endpoints public (`/dashboard/summary,trends`, `/status`, `/qa/health`); PII exports role-gated; MOCK vs PRODUCTION provenance labeling.
- **`[INFERRED]`** Least-privilege tiering (reviewer < senior_analyst < program_manager/qalead < admin).
- **`[RECOMMENDATION]`** Surface permission-denied UX per role; no client-side PII caching; provenance banner global. *(508/HIPAA/FISMA chips in the UI are **asserted, not verified** — treat as `[INFERRED]` pending conformance artifacts.)*

## 17. Audit Model
- **`[VERIFIED]`** Chain-of-custody primitives exist: source cache (hash + freshness + api_version), `analyst_override_reason`, supervisor review timestamps, `/qa/audit` trail + `/qa/audit/export`, evidence gate.
- **`[RECOMMENDATION]`** Surface an immutable audit/lineage timeline per entity/finding (who/what/when/source-diff) in the Trust Center.

## 18. UX Recommendations `[RECOMMENDATION]`
Role landings; the **6-source side-by-side evidence workbench** as the core screen; guided 5-element capture; consistent card/grid/blade patterns; drill-through; faceted tables + export; "as-of" context on every KPI; fix demo-data inconsistencies visible on the flagship dashboard; standardized empty/loading/error states.

## 19. UI Modernization Recommendations `[RECOMMENDATION]`
Fluent-2 design tokens; single role-aware nav (retire dual-nav); command bar; semantic status palette that passes contrast (No-Discrepancy/green · Minor/amber · Inexplicable/violet · Non-Compliant/red); virtualized dense grids; persistent MOCK/PRODUCTION banner; saved views.

## 20. Accessibility `[RECOMMENDATION]`
Target **WCAG 2.1 AA / Section 508**: keyboard-first, screen-reader labels, focus management, color-independent status (icon+label), contrast-checked palette, accessible data grids and charts (data-table fallback). *(508 chip today is asserted, not conformance-verified.)*

## 21. Mobile Strategy `[RECOMMENDATION]`
Desktop-first (dense review workflows). Provide a **responsive read-only executive/COR view** (health KPIs, per-QHIN scorecards, priority-case status, report downloads) for tablet/phone; keep the heavy review workbench desktop-only.

## 22. Future AI Opportunities `[RECOMMENDATION]`
- Assisted discrepancy triage (draft bucket + rationale for reviewer confirmation).
- Auto-drafted 5-element evidence narrative + citations from source diffs (human-approved).
- Root-cause suggestion for priority cases; anomaly/drift detection on QHIN accuracy trends.
- Natural-language query over findings ("show B4 address mismatches for QHIN X this cycle").
- Report narrative summarization for weekly/quarterly deliverables (human sign-off).
*(All AI framed as decision-support with mandatory human approval; none proposed for autonomous determinations.)*

---

## IMPLEMENTATION ROADMAP (UI only — every screen maps to existing endpoints)

| Phase | Theme | Scope | Effort | Dependencies |
|---|---|---|---|---|
| **Phase 1** | **Quick Wins** | Fix flagship data-inconsistencies + add as-of/provenance context; global MOCK/PRODUCTION banner; consolidate to single role-aware nav; retire duplicate destinations | **S–M** | Design tokens; role→route map |
| **Phase 2** | **Operational Improvements** | Reviewer My-Queue landing; **Entity Review 6-source workbench** (guided 5-element + disposition); SME Escalations; Findings faceting/export | **M–L** | Phase 1; taxonomy + validate/evidence/queue endpoints `[VERIFIED]` |
| **Phase 3** | **Enterprise UX** | Cycle Delivery (burn-down/aging/throughput); Reports Library (status/provenance/exports); QA Cockpit; Connectors panel; Data Import validate→preview→confirm | **M–L** | Phase 2; dashboard/QA/reports endpoints `[VERIFIED]` |
| **Phase 4** | **Executive Analytics** | Executive Health + COR Oversight dashboards; per-QHIN scorecards; self-serve Explore (drill-through/saved views); Trust Center (audit timeline + conformance links) | **M** | Phase 3; `/dashboard/*`, `/qa/audit` `[VERIFIED]`; 508/FISMA artifacts from AGT |
| **Phase 5** | **Future Enhancements** | AI decision-support (triage/evidence-draft/root-cause/NL-query); responsive exec/COR mobile view; accessibility hardening to AA/508 conformance | **L** | Phases 1–4; AI + accessibility standards; human-in-the-loop guardrails |

**Cross-cutting (all phases):** WCAG AA/508, Fluent design system, provenance banner, performance budget for dense grids, standardized states. **No backend/API/DB/auth/scheduler/deployment change required** — the only non-UI items are two connector API keys (config) and compliance artifacts (docs).

---

# DELIVERABLE 2 — Contract Gap Assessment

**Status: INCOMPLETE — deferred by design.**

> **Contract Traceability cannot be certified until the official HHS/ONC TEFCA procurement documents are reviewed.**

No contract requirement is inferred or invented in this blueprint. To certify contract traceability, AGT must supply the following. Each is a gate item; certification is blocked until all applicable items are received and reviewed.

### Documents Required Before Contract Traceability Can Be Certified
- ☐ **RFQ** (Request for Quotation) — full text + all sections
- ☐ **PWS / SOW** (Performance Work Statement / Statement of Work) — including numbered tasks (the code references Tasks 1/3/4/5 `[CODE-REF]`, text not available)
- ☐ **Amendments** (all modifications to the solicitation/award)
- ☐ **Questions & Answers** (offeror Q&A / clarifications)
- ☐ **Evaluation Criteria** (technical/price factors, basis of award)
- ☐ **Deliverables** (complete deliverables schedule; the code references D3.1/D3.2 `[CODE-REF]`, full list not available)
- ☐ **Acceptance Criteria** (deliverable acceptance/rejection standards, quality thresholds, SLAs)
- ☐ **Attachments** (all solicitation attachments/exhibits)
- ☐ **Sample Reports** (any government-provided report templates/formats/examples)
- ☐ **Government-Furnished Information (GFI)** (data, methodology, source lists, prior findings)
- ☐ **Government-Furnished Property (GFP)** (if applicable — systems, credentials, access)

**On receipt, this deliverable becomes:** a clause-by-clause traceability matrix mapping every contract requirement to VERIFIED implementation evidence, with each marked *fully implemented / partially / missing / UI-only / backend / documentation-only*, plus a validation of every screen against acceptance criteria.

*Reminder of scope: the "ONC CONTRACT 7571MN26Q00027" shown in the UI is a reused FCC-solicitation placeholder `[VERIFIED]`, not a confirmed TEFCA solicitation number — AGT should confirm the correct solicitation identifier with the documents.*

---

*This document modified no code, backend, API, database, authentication, scheduler, deployment, or infrastructure, and did not discuss Dev vs Production or Azure. Every `[VERIFIED]` claim is grounded in `app/Tefca/` source or the live application; `[INFERRED]` and `[RECOMMENDATION]` items are labeled as such; no contract requirement was fabricated.*
