# Retrospective Review — {{ review_period_label }}

**{{ release_label }}**
*(`DRAFT — NOT FOR COR RELEASE` until all five release gates are open.)*

| | |
| --- | --- |
| Report ID | {{ report_id }} · version {{ report_version }} |
| Contract | 7571MN26F80064 · Task 3, Deliverable 3.2 |
| Generated | {{ generated_at }} |
| Methodology version | {{ methodology_version }} |
| Evidence version | {{ evidence_version }} |
| Delivery reviewed | {{ delivery_filename }} · SHA-256 `{{ delivery_sha256 }}` |

---

## 1. Executive summary

> Three paragraphs at most. State what was reviewed, what was observed, and what
> cannot yet be concluded. **Do not state a compliance conclusion that no human
> has approved.**

{{ executive_summary }}

## 2. Objective

{{ objective }}

## 3. Scope

- **In scope:** {{ scope_in }}
- **Out of scope:** {{ scope_out }}
- **Review period:** {{ period_start }} – {{ period_end }}

## 4. Population

| | Count | Definition |
| --- | --- | --- |
| Delivered source records | {{ population.delivery_records }} | The denominator for every rate in this report |
| Records with evidence collected | {{ population.entities_with_evidence }} | Automated evidence collection |
| Records that received human review | {{ population.human_reviewed }} | Analyst determination recorded |
| Records with QA-approved findings | {{ population.qa_approved }} | Reportable |

> **These four numbers are not interchangeable.** Automated evidence collection
> is not human review. This report must never imply that all
> {{ population.delivery_records }} records were reviewed by a person.

Entity types: {{ population.participants }} Participant · {{ population.subparticipants }} Subparticipant · {{ population.qhins }} QHINs referenced.

## 5. Sampling approach

{{ sampling_approach }}

*Where the review is a full-population census, state that and omit confidence
intervals — they do not apply to a census. Where a sample was drawn, state the
population, sample size, confidence level, margin of error and the sample
identifier, so the same sample can be retrieved.*

## 6. Data sources

| Source | Edition | SHA-256 | Records | Applicability |
| --- | --- | --- | --- | --- |
{{ source_table }}

## 7. Methodology and QA methodology

Methodology version {{ methodology_version }} applies. Analyst determinations and
QA decisions follow the segregation-of-duties and append-only decision-event
controls described in the methodology §14–16.

## 8. Population characteristics

{{ population_characteristics }}

## 9. Observations by dimension

| Dimension | Applicable | Match observed | No match observed | Ambiguous / multiple | Not applicable | Source unavailable |
| --- | --- | --- | --- | --- | --- | --- |
{{ observation_table }}

> Every cell is a count of **observations**. Where a record count is meant, the
> column says so.

## 10. Findings

**Findings are determinations a human made and QA approved.** If none have been
approved, this section says so and reports zero. It does not promote observations
into findings to fill the space.

{{ findings }}

## 11. Exceptions requiring review

| Disposition | Observations | Distinct records |
| --- | --- | --- |
| Ready for analyst | {{ triage.ready_obs }} | {{ triage.ready_entities }} |
| Methodology pending | {{ triage.pending_obs }} | {{ triage.pending_entities }} |
| Source limitation | {{ triage.limitation_obs }} | {{ triage.limitation_entities }} |
| Informational only | {{ triage.info_obs }} | — |

## 12. Source limitations

{{ source_limitations }}

*Each limitation states what could not be assessed and how the report treats it.
A limitation is never reported as an adverse result about an entity.*

## 13. Methodology-pending items

| Decision | Question | Observations affected | Distinct records |
| --- | --- | --- | --- |
{{ methodology_pending_table }}

> Items in this table have **no conclusion**. They are not failures, and this
> report does not describe them as failed, non-compliant, invalid, inaccurate or
> unverified.

## 14. Conclusion

{{ conclusion }}

*State what the evidence supports and — explicitly — what it does not. A
conclusion that outruns the human review performed is the failure mode this
template exists to prevent.*

## 15. Recommended follow-up

{{ recommendations }}

---

## Appendix A — Evidence traceability

For each figure in §9–§11: the query, the evidence version, the source edition
and hash, and the source rows. See the Evidence Appendix template.

## Appendix B — Release gate status

| Gate | Status | Reason |
| --- | --- | --- |
{{ gate_table }}
