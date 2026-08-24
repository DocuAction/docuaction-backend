# Exception Detail — {{ exception_id }}

> ## SAMPLE / DEVELOPMENT DATA
> ## FOR METHODOLOGY REVIEW ONLY
> ## NOT AN ONC FINDING
>
> Any rendering of this template produced before the Government population has
> been received and reviewed under an accepted methodology contains development
> or placeholder values. It is provided so the COR can assess the structure,
> content and level of detail — not the numbers.
>
> Questions this sample is meant to let the COR answer: is this the information
> you expect? Is the level of detail right? Is the format acceptable? What
> should be added or removed?

**{{ release_label }}**

| | |
| --- | --- |
| Entity | {{ entity_name }} · OID `{{ org_oid }}` |
| Raised | {{ raised_at }} by triage version {{ triage_version }} |
| Disposition | {{ disposition }} |
| Priority | {{ priority }} |
| Evidence version | {{ evidence_version }} |

## Why this was raised

{{ triage_reason }}

## Observations cited

| Observation ID | Source | Edition | Result | Applicability |
| --- | --- | --- | --- | --- |
{{ observation_table }}

## Delivered values vs source values

| Field | As delivered | As published by source | Normalised comparison |
| --- | --- | --- | --- |
{{ comparison_table }}

## What this exception does NOT establish

{{ not_established }}

*An exception is a question. It becomes an answer only through analyst
determination and QA approval.*

## Current status

| | |
| --- | --- |
| Analyst determination | {{ determination }} |
| QA decision | {{ qa_decision }} |
| Reportable | {{ reportable }} |
