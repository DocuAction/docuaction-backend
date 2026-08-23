# Evidence Appendix — {{ report_id }}

**{{ release_label }}**

Every figure in the parent report traces to source rows through this appendix.
No number appears in a report without a path recorded here.

| | |
| --- | --- |
| Parent report | {{ report_id }} version {{ report_version }} |
| Evidence version | {{ evidence_version }} |
| Generated | {{ generated_at }} |

## A — Source artefacts relied upon

| Source | Edition | SHA-256 | Records | Retrieved | Point in time |
| --- | --- | --- | --- | --- | --- |
{{ artefact_table }}

*Each artefact is retained, so a review run against it can be repeated and will
produce the same result.*

## B — Figure traceability

| Figure | Value | Denominator | Calculation | Evidence version | Query |
| --- | --- | --- | --- | --- | --- |
{{ figure_table }}

## C — Worked traces

{{ traces }}

*Each trace runs: reported value → query → observation rows → source edition and
hash → source row key → the row in the retained artefact.*

## D — Determination traceability

| Finding | Review ID | Analyst | Determination | QA lead | QA action | Reportable since |
| --- | --- | --- | --- | --- | --- | --- |
{{ determination_table }}

## E — Excluded evidence

Evidence versions superseded by the current version remain queryable and were
**not** included in any figure above:

{{ excluded_versions }}
