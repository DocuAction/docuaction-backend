# TEFCA ARC — UI/UX INVENTORY

> ## INTERNAL AGT — NOT FOR CLIENT DISTRIBUTION
> **No Government row-level values.**

**Contract:** 7571MN26F80064 · HHS/ONC ASTP · **Date:** 2026-08-30
**Master Step:** #16 · Discovered from code and from the running application

---

## 1. Routes

Discovered by walking `frontend/src/app`, not from a list. **21 TEFCA routes**,
of which 12 appear in the primary navigation.

### A. Primary operational pages (in the left navigation)

| # | Route | Navigation label | Purpose |
|---|---|---|---|
| 1 | `/tefca-arc` | Mission Control | Operations landing page |
| 2 | `/tefca-arc/import` | Data Import | Controlled intake |
| 3 | `/tefca-arc/cycles` | Review Cycles | Cycle management |
| 4 | `/tefca-arc/reviews` | Entity Reviews | Reviewer workspace |
| 5 | `/tefca-arc/validation` | Validation Queue | Validation work |
| 6 | `/tefca-arc/priority` | Priority Reviews | Task 5 |
| 7 | `/tefca-arc/findings` | Findings | Discrepancy findings |
| 8 | `/tefca-arc/reports` | Reports | Deliverables |
| 9 | `/tefca-arc/qa` | QA Operations | Quality gates |
| 10 | `/tefca-arc/operations` | Operations | Supervisor control plane (Step #15) |
| 11 | `/tefca-arc/connectors` | Connectors | Authoritative source status |
| 12 | `/tefca-arc/analytics` | Analytics | Trends |

The user's estimate of "approximately 11 main operational pages" is close: it is
**12**, the twelfth being the Operations page added by Step #15.

### B. Registry pages (navigation group "TEFCA REGISTRY")

`/tefca-registry`, `/tefca-registry/entities`, `/tefca-registry/entity`,
`/tefca-registry/verification`, `/tefca-registry/issues` — labelled QHIN
Overview, Entities, Verification, Issues.

### C. Secondary / administrative (reachable, not in the TEFCA navigation)

`/tefca-arc/administration` · `/tefca-arc/configuration` · `/tefca-arc/audit` ·
`/tefca-arc/decisions` · `/tefca-arc/insights` · `/tefca-arc/search` ·
`/tefca-arc/trust-center` · `/tefca-arc/help` · `/tefca-arc/dashboard`
(redirects to Mission Control) · `/tefca-dashboard`.

### D. Shared layouts

| Layer | File | Provides |
|---|---|---|
| Platform shell | `src/components/AppLayout.js` | Left navigation, session gate, active-page derivation |
| Module shell | `src/app/tefca-arc/layout.js` | Skip link, utility bar, demonstration banner, security summary, `<main>` landmark, **footer (added #16)** |

### E–J. Shared components

| Concern | Component |
|---|---|
| Page header | `tefca-arc/components/CommandBar.js` |
| Panels | `tefca-arc/components/Panel.js` |
| Tables | `platform/components/DataTable.js` (+ a module copy) |
| Filters | `platform/components/FilterBar.js`, `useFilters`, `useServerFilters` |
| Status | `platform/components/StatusBadge.js`, `tefca-arc/components/StatusPill.js` |
| Drawers | `platform/components/SidePanel.js` (+ a thin module adapter) |
| Evidence | `platform/components/ConfidenceLedger.js`, `tefca-arc/components/EvidenceDimensions.js` |
| KPIs | `platform/components/KPICard.js` (+ a module copy) |
| Empty / loading | `platform/components/EmptyState.js`, `LoadingSkeleton.js` |
| Entity summary | `tefca-arc/components/EntityContextBar.js` |
| Reviewer workspace | `tefca-arc/components/TefcaReviewWorkspace.js` |
| **Footer** | `tefca-arc/components/ModuleFooter.js` — **new in #16** |
| **Source list** | `tefca-arc/lib/sources.js` — **new in #16** |
| **Absent-value vocabulary** | `tefca-arc/lib/present.js` — **new in #16** |

---

## 2. The design system already existed

The most consequential finding of the inventory: **DocuAction already has one
coherent design system**, and it is Fluent-derived.

`src/platform/tokens.js` defines the palette (`#0B3C5D` primary, `#006EC3`
accent), a dark-theme derivation, Segoe UI with a system fallback, a 4/8/12/16/
24/32 spacing scale, radii, card/badge/button/input/table patterns, and a
universal operational-state vocabulary. It carries its own accessibility
amendments (`A11Y-1.0`) with measured contrast ratios and the reasoning for each.

**No new framework was introduced, and none was needed.** Installing Fluent UI
or USWDS packages would have added a second system beside a working one. Step
#16's work was to find where pages had **departed** from the system that exists,
not to build another.

Departures found, and where they were addressed, are recorded in
`TEFCA_UI_UX_Professionalization_INTERNAL.md`.

---

## 3. Deployment state

| Feature / screen | Local | DEV | PROD | Evidence |
|---|---|---|---|---|
| All Step #16 changes | **Present** | Not deployed | Not deployed | Working tree only; no build was published |
| Supervisor Operations page (#15) | Present | Not deployed | Not deployed | Untracked working-tree files |
| Everything #1–#14 | Present | Unknown | Unknown | Not verified; no deployment was performed or inspected |

**No screenshots were supplied to this session.** The rendered "before" evidence
in the professionalization document was captured from the application running
locally against the DEV database, not from PROD. Where this document refers to a
"known PROD defect", that is the defect as described in the Step #16 brief;
**it was reproduced locally and fixed locally, and PROD was not inspected,
changed or deployed.**

Frontend build: `next build` passes. Frontend has **no test runner and no
linter** configured (`package.json` declares `dev`, `build`, `start`).
