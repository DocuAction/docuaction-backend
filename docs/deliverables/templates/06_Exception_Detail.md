# Exception Detail — {{ exception_id }}

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
