# Phase 8 — Learning Center inventory

**Classification:** INTERNAL ENGINEERING
**Date:** 2026-08-23 · **Branch:** `fix/tefca-stabilization` · **Commit at survey:** `4bcf74f`

Surveyed before building anything, so that Phase 8 extends what exists rather
than shipping a second version of it.

---

## Finding 1 — substantial operational guidance already exists

`docs/TEFCA_USER_OPERATIONS_GUIDE.md` is **2,252 lines across 24 sections** and
already covers most of the requested Learning Center navigation:

| Phase-8 navigation item | Already covered by |
| --- | --- |
| Getting Started | §7 Logging in · §2 One-page daily quick start |
| Program Overview | §3 What DocuAction TEFCA does |
| Review Process | §4 End-to-end workflow · §10 Entity review step-by-step |
| Understanding Evidence | §12 Understanding results and statuses |
| Authoritative Sources | §5 Data lineage · §11 Verification services and data sources |
| Analyst Guide | §10, §13 Exceptions and human review |
| QA Reviewer Guide | §15 QA and approval |
| Reports & Deliverables | §16 Reports and outputs |
| Troubleshooting | §20 |
| Program Manager Guide | §18 Administrator operations · §23 Checklists |

`docs/TEFCA_STAFF_GUIDE.md` (185 lines) is a shorter operational companion.

**Consequence:** duplicating this content would create two guides that disagree
the first time either is edited. Phase 8 therefore cross-references it and adds
only what it does not and cannot cover.

## Finding 2 — the existing guide predates the current evidence model

It was written before Phases 6, 6.5 and 7. It does not describe:

- The Layer-1 observation vocabulary (`MATCH_OBSERVED`, `NO_MATCH_OBSERVED`,
  `MULTIPLE_MATCHES`, `AMBIGUOUS`, `SOURCE_UNAVAILABLE`,
  `LOOKUP_NOT_APPLICABLE`, `INSUFFICIENT_IDENTIFIER`, `ERROR`)
- Source applicability (`REQUIRED` / `APPLICABLE` / `CONDITIONALLY_APPLICABLE` /
  `NOT_APPLICABLE` / `UNKNOWN_PENDING_METHODOLOGY`)
- Evidence versioning — `phase6-bulk-1.0.0` superseded by `phase6-bulk-1.1.0`
- Triage dispositions and the 28 analyst-ready items
- Address comparison verdicts and `D4_ADDRESS_MATERIALITY`
- The canonical `review_records` → `review_decision_events` path
- The five report release gates

This is the actual gap Phase 8 fills.

## Finding 3 — no Learning Center framework exists

Searched `app/` for help, training, glossary, tooltip and onboarding components.
The only hit is `/learning-stats` in `app/routers/ats_agent.py`, which is an
unrelated ATS feature. There is **no** content registry, no glossary service, no
contextual-help mechanism.

## Finding 4 — the UI is not in this repository

`git rev-parse --show-toplevel` is the backend directory. A sibling `../frontend`
exists on disk but is **not tracked by this repository**.

**Consequence:** Phase 8 delivers contextual help as a **backend content API**
that a frontend consumes. Rendering, navigation chrome and tooltip placement are
frontend concerns and are out of scope here. Claiming otherwise would be claiming
work this repository cannot contain.

---

## Classification

| Asset | Class | Action |
| --- | --- | --- |
| `TEFCA_USER_OPERATIONS_GUIDE.md` | **REUSE** | Referenced, not duplicated |
| `TEFCA_STAFF_GUIDE.md` | **REUSE** | Referenced |
| `app/reports/engine/accessibility.py` | **REUSE** | Serves Phase-8 accessibility checks |
| `app/Tefca/evidence_version.py`, `exception_triage.py`, `release_gates.py` | **REUSE** | Content is generated from these, so guidance cannot drift from code |
| Learning Center content framework | **GAP → CORE** | `app/core/learning/` — registry, module, glossary, contextual help |
| TEFCA Learning Center content | **GAP → TEFCA MODULE** | `app/Tefca/learning_content.py` |
| Analyst SOP | **GAP** | `docs/deliverables/TEFCA_ARC_Analyst_SOP_DRAFT.md` |
| QA SOP | **GAP** | `docs/deliverables/TEFCA_ARC_QA_SOP_DRAFT.md` |
| Operations Playbook | **GAP** | `docs/deliverables/TEFCA_ARC_Operations_Playbook_DRAFT.md` |
| Glossary | **GAP → CORE framework + TEFCA terms** | |
| 7 training modules | **GAP → TEFCA MODULE** | |
| Contextual help UI | **OUT OF SCOPE** | Frontend; backend supplies the API |
| LMS | **NOT BUILT** | Explicitly excluded by the instruction |

## Boundary applied

**CORE** (`app/core/learning/`) — content registry, module/lesson/glossary types,
contextual-help lookup, knowledge checks. No TEFCA vocabulary.

**TEFCA MODULE** (`app/Tefca/learning_content.py`) — the ARC curriculum, the
TEFCA glossary, source guides, and the prohibited-conclusion rules. Imports the
live vocabulary from `evidence_vocabulary`, `source_applicability`,
`exception_triage` and `address_comparison`, so training and code cannot
disagree.

**NOT BUILT** — no LMS, no progress tracking, no certification records, no
agency-specific module for an agency that has not asked for one.
