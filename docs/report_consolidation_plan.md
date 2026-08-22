# REPORT CONSOLIDATION — PLAN

**Date:** 2026-08-22 · **Branch:** `fix/tefca-stabilization` · **Status:** DESIGN ONLY — no generator is retired, no route is changed.

---

## A. INVENTORY — THREE GENERATORS

### 1. Canonical governed engine — `app/reports/`

| | |
|---|---|
| **Files** | `generator.py`, `routes.py`, `data/report_data_service.py`, `data/rce_report_data.py`, `data/report_snapshot.py`, `engine/{template,chart,pdf,csv,accessibility}_engine.py` |
| **Reads** | **Only** through the Report Data Service. An executable read-only assertion raises if any lookup, D1–D6 evaluation or B1–B4 classification is attempted during generation |
| **Produces** | 5 report types: `verification`, `verification_brief`, `executive`, `data_quality`, `intake` — HTML, PDF, CSV |
| **Used by** | **Nothing.** No component under `frontend/src` calls it |
| **API** | `POST /api/reports/generate`, `GET /api/reports`, `/{id}`, `/{id}/html`, `/{id}/pdf`, `/{id}/csv`, `/health/engine` |
| **Report IDs** | `DA-ARC-YYYY-NNN` |
| **Templates** | `templates/{base, verification_detail, verification_brief, executive_cor, data_quality, source_intake}.html`, `styles/uswds_report.css`, Public Sans inlined as base64 |
| **Snapshot** | **Full.** `template_version`, `report_data_service_version`, `b1_b4_rule_version`, `evidence_generation`, `data_payload_hash`, accessibility result, PDF engine state, query parameters |
| **Persists to** | `review_reports` — `report_data` = `{dataset, snapshot}`, plus `report_html` verbatim |
| **Tests** | 52 |

### 2. Legacy dashboard generator — `app/Tefca/reporting.py` + `report_renderer.py`

| | |
|---|---|
| **Reads** | **Direct SQLAlchemy** over `tefca_evidence_records`, `tefca_review_cycles`, `tefca_reviews` — no data-service boundary |
| **Produces** | weekly, biweekly, quarterly, final, priority-quarterly — HTML, PDF, CSV, **DOCX** |
| **Used by** | **The frontend, exclusively** — `tefca-arc/reports/page.js` and `tefca-arc/reviews/page.js` |
| **API** | `POST /api/tefca/reports/{weekly\|biweekly\|quarterly\|final}`, `GET /api/tefca/reports`, `/{id}`, `/{id}/{csv\|pdf\|docx\|download}`, `/reports/export` |
| **Report IDs** | `tefca_reports.report_id` (UUID) |
| **Templates** | inline HTML in `report_renderer.py` |
| **Snapshot** | None. `methodology_version` string only |
| **Persists to** | `tefca_reports` |

### 3. Registry generator — `app/tefca_registry/report_generator.py` + `report_excel.py`

| | |
|---|---|
| **Reads** | **Direct SQLAlchemy** over `review_records`, `review_samples` |
| **Produces** | weekly / quarterly / priority review reports, HTML + **Excel** |
| **Used by** | **Nothing** in the frontend |
| **API** | `POST /api/tefca/arc/reports/generate`, `GET /api/tefca/arc/reports`, `/{id}`, `/{id}/excel`, `/{id}/html` |
| **Report IDs** | `WR-YYYY-Wnn`, `QR-…` |
| **Snapshot** | None |
| **Persists to** | `review_reports` — **the same table as the canonical engine** |

**No route collision.** Prefixes are `/api/reports`, `/api/tefca` and
`/api/tefca/arc`. The problem is not routing; it is that three independently
written query sets can each answer *"how many B2 entities were in this cycle"*.

---

## B. WHICH IS CANONICAL?

**Generator 1.** Not because it is newer, but on four properties the others lack:

| Property | Gen 1 | Gen 2 | Gen 3 |
|---|---|---|---|
| Single query boundary, executable read-only contract | **YES** | no | no |
| Data-service version stamped on every report | **YES** | no | no |
| `data_payload_hash` — regeneration provably yields the same numbers | **YES** | no | no |
| Rule version recorded on the report | **YES** | no | no |
| `INSUFFICIENT_DATA` rather than 0% on a zero denominator | **YES** | unknown | unknown |
| Accessibility validated on the rendered output | **YES** | no | no |
| Charts and prose read from the same objects, so a figure cannot contradict its caption | **YES** | no | no |

---

## C. WHAT HAS BEEN GENERATED, AND CAN IT BE REPRODUCED?

**Five reports exist. All five came from the canonical engine.**

| Report ID | Type | Generated | HTML stored | Rule set |
|---|---|---|---|---|
| `DA-ARC-2026-001` | verification | 2026-08-21 | 320,707 B | v2 |
| `DA-ARC-2026-002` | verification_brief | 2026-08-21 | 190,821 B | v2 |
| `DA-ARC-2026-003` | executive | 2026-08-21 | 240,469 B | v2 |
| `DA-ARC-2026-004` | data_quality | 2026-08-21 | 305,749 B | v2 |
| `DA-ARC-2026-005` | intake | 2026-08-21 | 251,112 B | v2 |

`tefca_reports` (legacy) — **0 rows.** No legacy report has ever been generated on
this database.

> **Caveat that must be discharged before anything is retired.** This is the local
> development database. **Azure dev and Azure prod have not been queried.**
> `tefca_reports` on both must be confirmed empty before concluding that no legacy
> report was ever delivered to ONC/COR.

### Reproducibility of the five

| Question | Answer |
|---|---|
| Is the delivered artefact preserved? | **YES** — `report_html` holds the complete self-contained document; CSS and fonts inlined, no remote assets. Served verbatim |
| Can the numbers be re-derived? | **PARTIAL.** `data_payload_hash` proves whether a re-derivation matches, and `report_data.dataset` holds the figures. But `review_cycle_id` is **NULL on all five**, so no snapshot names the population it counted — a re-run would read whatever `review_records` holds at that time |
| Is the template version stored? | **YES** — `template_version: "1.0.0"` |
| Is the rule version stored? | **YES** — `b1_b4_rule_version: "2"` |
| Is the evidence generation stored? | **YES** |
| Is a PDF preserved? | **NO.** All five are HTML-only — WeasyPrint's native stack is absent on the generating host, and the snapshot records that honestly rather than silently omitting the PDF |

### Two defects that must be fixed before Phase 3

1. **`rce_source_file_sha256 = "cafe"`** in all five snapshots — a placeholder. The
   real delivery hash is `689472073480b1cc…9e8d`. **The one field tying a report to
   the ONC bytes it describes is a test literal.**
2. **`review_cycle_id = NULL`** in all five — the population is unscoped.

Neither is a consolidation problem. Both are defects in the canonical engine, and
consolidating onto it without fixing them would propagate them.

---

## D. CONSOLIDATION PHASES

### PHASE 1 — Route all NEW generation through the canonical path

Legacy generators remain callable and are not modified. The frontend is repointed.

| Work | Detail |
|---|---|
| Fix the two defects above | Prerequisite. `rce_source_file_sha256` from the intake; require a non-null `review_cycle_id` |
| Repoint `tefca-arc/reports/page.js` | `/api/tefca/reports*` → `/api/reports*` |
| Repoint `tefca-arc/reviews/page.js` | the `/reports/export` call |
| Map report types | Legacy generates by **period** (weekly / biweekly / quarterly / final); canonical generates by **content type** with an optional cycle. **These are different axes and the mapping is not one-to-one** — see §D.5 |
| Deprecation notice | Legacy endpoints marked deprecated in their OpenAPI summary; a warning is logged on use |

**Exit:** every new report carries a snapshot. Legacy endpoints still answer.

### PHASE 2 — Verify numeric equivalence

Run both generators over identical inputs and compare. **"A PDF was produced" is
not equivalence.** All of the following must match:

| # | Must match |
|---|---|
| 1 | population counts |
| 2 | B1 / B2 / B3 / B4 counts |
| 3 | D1–D6 results **per entity**, not in aggregate |
| 4 | tier counts T1 / T2 / T3 |
| 5 | issue counts |
| 6 | percentages, including the `INSUFFICIENT_DATA` case where the denominator is zero |
| 7 | **entity inclusion** — the same entity set, compared as sets, not as counts |
| 8 | source snapshot / dataset version referenced |
| 9 | report date and reporting period |
| 10 | key narrative facts — the sentences that state a number |

**Design of the comparison harness**

- A test fixture seeds a known population and runs both generators.
- Comparison is on the **structured dataset**, not on rendered HTML. Rendering
  differences are expected and irrelevant; number differences are the point.
- Item 7 is a set comparison and reported as a symmetric difference, so
  *"the same count of different entities"* fails.
- Item 10 is extracted by pinning the numeric spans in each template and comparing
  the values, not the prose.
- **Any mismatch is a finding, not a tolerance.** A generator that differs is
  either wrong or is answering a different question, and both must be resolved
  before Phase 3.

**Expected outcome, stated in advance:** Generator 2 reads
`tefca_evidence_records` (**0 rows**) and Generator 1 reads `review_records`
(**43 rows**). On the current database they will not agree, because **they read
different populations entirely.** Phase 2 must therefore run against a seeded
fixture that populates both, and the divergence in production data is itself the
finding: the legacy generator would report on an empty population today.

### PHASE 3 — Deprecate with redirects

Only after Phase 2 passes.

- Legacy `GET` endpoints return `308 Permanent Redirect` to the canonical
  equivalent where one exists.
- Legacy `POST` generation endpoints return `410 Gone` with a message naming the
  replacement. **A redirect is wrong for generation** — the payloads and the
  semantics differ, so a silent redirect would produce a different report than the
  caller asked for.
- The two unresolved capability gaps (§D.5) must be closed or explicitly descoped
  before this phase.

### PHASE 4 — Archive, do not delete

- Move `app/Tefca/reporting.py`, `report_renderer.py`,
  `tefca_registry/report_generator.py`, `report_excel.py` to
  `app/_archive/reports_legacy/` with a README naming the commit that retired them
  and the equivalence evidence.
- Routers are unregistered; the modules remain importable for forensic
  reconstruction.
- **`tefca_reports` and every row in it are preserved.** A previously delivered
  report must remain explainable, and deleting the table would destroy the record
  of what a recipient received.

### D.5 — The two blockers that are not wiring

| Gap | Detail | Options |
|---|---|---|
| **DOCX** | The legacy generator serves `/{id}/docx`. The canonical engine produces HTML, PDF and CSV — **no DOCX path exists**. A Word generator exists in `bulletin_intelligence`, which is a protected module and out of scope | (a) add DOCX to the canonical engine; (b) confirm with the program that PDF satisfies the deliverable and descope DOCX |
| **Periodicity** | Legacy generates by period; canonical by content type. `weekly` is not a canonical report type, and `verification` is not a period | Introduce an explicit reporting-period parameter on the canonical engine and define the mapping, **or** confirm the deliverable is defined by content type |

**Both are program questions, not engineering ones.** Neither should be resolved by
inference.

---

## E. FRONTEND CHANGES

| Page | Current calls | Change |
|---|---|---|
| `tefca-arc/reports/page.js` (330 LOC) | `GET /api/tefca/reports`, `POST /api/tefca/reports/{type}`, `GET /api/tefca/reports/{id}/{pdf\|docx\|csv}` | Repoint to `/api/reports*`; report-type selector changes from period to content type; DOCX option removed or retained per §D.5 |
| `tefca-arc/reviews/page.js` | `GET /api/tefca/reports/export` | Repoint to the canonical CSV endpoint |

The API layer already handles auth, 401 redirect, 403 permission events and
timeouts uniformly, so no change is needed there — only the URLs and the
report-type vocabulary.

**One behavioural difference the UI must surface.** The canonical PDF endpoint
returns **503 with a reason** when the native rendering stack is unavailable
(as it is on Windows). The current page treats a failed download as a generic
failure. It should render the reason — *"PDF generation is unavailable: …"* — which
is the honest message and matches the engine's deliberate refusal to silently fall
back to a different renderer.

---

## F. LOC ESTIMATE

| Phase | Work | Production | Test |
|---|---|---|---|
| **0** | Fix `"cafe"` placeholder; require non-null `review_cycle_id` | 25 | 40 |
| **1** | Repoint two frontend pages; report-type mapping; deprecation notices | 130 | 55 |
| **2** | Equivalence harness — seeded fixture, 10-point dataset comparison, set-difference reporting | 60 | 260 |
| **3** | Redirects, 410 responses, capability-gap resolution | 55 | 70 |
| **4** | Archive move, router unregistration, README | 30 | 25 |
| — | DOCX in the canonical engine (**only if not descoped**) | 180 | 90 |
| — | Reporting-period parameter (**only if not descoped**) | 90 | 70 |
| **TOTAL excluding the two optional items** | | **~300** | **~450** |
| **TOTAL including both** | | **~570** | **~610** |

---

## G. RISKS

| Risk | Severity | Mitigation |
|---|---|---|
| A legacy report was delivered to ONC from Azure dev or prod and is unaccounted for | **HIGH** | Query `tefca_reports` on both environments before Phase 3. This is the one open fact in this plan |
| DOCX is a contractual deliverable format | **HIGH** | Resolve §D.5 before Phase 3. Descoping a deliverable format by omission would be discovered at submission |
| Phase 2 cannot run because the two generators read disjoint populations | **HIGH** | Anticipated. Use a seeded fixture that populates both `tefca_evidence_records` and `review_records`; treat the production divergence as a documented finding rather than a test failure |
| The `"cafe"` placeholder propagates into consolidated reports | MEDIUM | Phase 0 is a prerequisite, not an improvement |
| Frontend repointed before the canonical engine supports the periodicity the operator expects | MEDIUM | Phase 1 ships the mapping or does not ship |
| Historical reports become unexplainable after archival | MEDIUM | `review_reports` and `tefca_reports` are both preserved with their stored HTML; archived modules remain importable |
| PDF unavailable on the deployment host | MEDIUM | Already surfaced honestly by the engine. Confirm the Linux container image carries the Pango/Cairo/GObject stack before any PDF deliverable is promised |

---

## H. DEPENDENCIES

- **Independent of D1–D7.** Consolidation concerns which code produces a number,
  not what the number means.
- **Interacts with `docs/qa_gate_design.md`:** once a QA gate exists, report
  generation must consult the reportable gate. Building that logic **once**, in the
  canonical engine, is a direct argument for consolidating first — otherwise the
  gate must be implemented in three places.
- **Blocks nothing.** Can proceed in parallel with B1, B2 and B3.
