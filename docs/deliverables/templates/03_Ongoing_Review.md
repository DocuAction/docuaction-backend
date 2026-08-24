# Ongoing Review — cycle {{ cycle_id }}

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
| Report ID | {{ report_id }} · version {{ report_version }} |
| Contract | 7571MN26F80064 · Task 4, Deliverable 4.1 |
| Cycle type | `TASK4_ONGOING` |
| Generated | {{ generated_at }} |
| Methodology version | {{ methodology_version }} |
| Evidence version | {{ evidence_version }} |

## 1. Deliveries compared

| | Prior | Current |
| --- | --- | --- |
| Delivery identifier | {{ prior.intake_id }} | {{ current.intake_id }} |
| Filename | {{ prior.filename }} | {{ current.filename }} |
| Received | {{ prior.received_at }} | {{ current.received_at }} |
| SHA-256 | `{{ prior.sha256 }}` | `{{ current.sha256 }}` |
| Schema fingerprint | `{{ prior.schema_fingerprint }}` | `{{ current.schema_fingerprint }}` |
| Records | {{ prior.record_count }} | {{ current.record_count }} |

> If the schema fingerprints differ the deliveries are not comparable field for
> field. Say so here and hold the cycle rather than diffing across a changed
> schema.

## 2. Change summary

| | Records | Definition |
| --- | --- | --- |
| Added | {{ delta.added }} | Business key present in current, absent in prior |
| Changed | {{ delta.changed }} | Same business key, different content hash |
| Unchanged | {{ delta.unchanged }} | Same business key, same content hash |
| Removed | {{ delta.removed }} | Present in prior, absent in current |

Business key: organisation OID. It is the only unique identifier in the delivery
— TEFCAID and HCID are not unique and must not be used to match records across
deliveries.

## 3. Evidence collected on added and changed records

Only added and changed records are re-evaluated. Unchanged records retain prior
evidence, which stays valid because the source editions used are recorded and
retained.

| Source | Edition | Applicable | Observations |
| --- | --- | --- | --- |
{{ source_table }}

## 4. Exceptions raised this cycle

{{ exceptions }}

## 5. Analyst and QA activity this cycle

| | Count |
| --- | --- |
| Work items created | {{ qa.created }} |
| Analyst determinations | {{ qa.determinations }} |
| QA approved | {{ qa.approved }} |
| QA returned | {{ qa.returned }} |
| QA escalated | {{ qa.escalated }} |

## 6. Reportable findings

{{ findings }}

## 7. Source and methodology versions in force

{{ versions }}

## 8. Release gate status

{{ gate_table }}
