# Integration analysis — where the capability could touch Tasks 2–5

Four columns are kept strictly apart. Nothing in the third column is built; nothing in the fourth column is a decision this team can make.

| Area | CURRENT CONTRACT REQUIREMENT (as implemented in Tasks 1–6, frozen) | CURRENT CAPABILITY (on the branch, feature OFF) | PROPOSED FUTURE (engineering option, not built) | PROGRAM-METHODOLOGY DECISION REQUIRED |
|---|---|---|---|---|
| Task 2 — ingestion of ONC/RCE deliveries, 41-field processing | Deliveries are ingested, curated and reconciled by the existing `tefca_registry` code | None; no adapter reads the delivery | A `PROGRAM_DELIVERY` adapter that turns a curated entity record into NAME/IDENTIFIER/LOCATION/RELATIONSHIP observations (read-only view over existing tables) | Which delivered fields are the subject of identity review (legal name? organisation name as delivered? which address field?) |
| Task 3 — stratification, sampling, work creation, assignment | Sample and assign from the delivered population per the approved methodology | None | Optionally attach a System Evidence Assessment to each sampled entity as analyst context | Whether an assessment may influence sampling or priority at all (default answer in this design: no — it is context, not a selector) |
| Task 4 — analyst review and determination | Analyst reviews evidence and records a determination in one of the contractual categories | None (no route, no UI) | A read-only "Independent evidence" panel: comparisons, explanations, what-changed, sources and dates, prior-decision reference | Whether NPPES/IQVIA evidence is admissible in the analyst's reasoning, how it must be cited, and whether a conflict obliges the analyst to take any step |
| Task 5 — independent QA of determinations | QA re-checks determinations per the methodology | None | QA sees the same panel plus the analyst's view of it; no second assessment | Whether QA may cite system evidence as grounds to return a determination |
| Contract reports | Counts and categories per SOW from determinations | None; no report reads `ei_*` | Possibly a supplementary, clearly labelled "evidence corroboration statistics" annex | Whether any evidence statistic may appear in a Government deliverable, and with what attribution |
| Learning Center | Frozen at knowledge 1.2.0 | None | A module explaining the assessment vocabulary and the "never a category" rule | LMS impact review is mandatory for any material workflow change (existing rule) |

## Invariants any integration must keep

1. Assessment values never map to a contractual category; no join path, no lookup table, no UI affordance that pre-selects a category from an assessment.
2. The analyst's determination and QA's outcome are written exactly as today; the capability only adds read-only context.
3. Feature OFF = invisible: no route registered, no panel rendered, no job scheduled, no table read.
4. Every evidence element shown carries source, source date, authority class and provenance; conflicting evidence is shown, not suppressed.
5. A prior decision is shown as a reference, never applied.
6. Integration lands only after the Task 1–6 QA freeze lifts and the persistence ADR (gate D) is decided.

## Effort sketch (for planning only)

- Program-delivery adapter + read path: small, once the subject fields are decided.
- Flag-gated read-only route + RBAC mapping to existing roles: small; requires OpenAPI change, hence a new baseline.
- Analyst panel (frontend): medium; see `ENTITY_INTELLIGENCE_FUTURE_UI_SPEC.md`.
- Persistence: per ADR option chosen.
- LMS module: small, but blocked by the LMS freeze until a workflow change is approved.
