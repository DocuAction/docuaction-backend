# Priority Review — {{ priority_review_id }}

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
| Contract | 7571MN26F80064 · Task 5, Deliverable 5.1 |
| Cycle type | `TASK5_PRIORITY` |
| Request received | {{ received_at }} |
| Requester | {{ requester }} |
| Due | {{ due_at }} · SLA state {{ sla_state }} |
| Report generated | {{ generated_at }} |
| Turnaround | {{ turnaround }} |
| Methodology version | {{ methodology_version }} |
| Evidence version | {{ evidence_version }} |

> `at_risk` is set at two or fewer days remaining and `overdue` once the due
> moment has passed, both measured against the deadline the COR set for this
> request.
>
> **Volume.** The contract anticipates an average of twenty priority reviews per
> month and requires the capability to exceed that average. That figure is the
> Government's expectation of volume; it is not a turnaround target and is not
> reported as performance against one.
>
> **Turnaround.** The contract sets the deadline per request, communicated by
> the COR. No standing service level is asserted, because none is established.

## 1. Executive output

*One page. A reader who stops here must not be misled.*

| | |
| --- | --- |
| Entity | {{ entity.name }} |
| Organisation OID | `{{ entity.org_oid }}` |
| TEFCAID | `{{ entity.tefcaid }}` *(not unique — methodology §7)* |
| NPI | {{ entity.npi }} |
| Managing QHIN | {{ entity.qhin }} |
| Reason for priority | {{ reason }} |
| **Disposition** | {{ disposition }} |
| **QA decision** | {{ qa_decision }} |

{{ executive_narrative }}

## 2. Evidence observed

| Source | Edition | Applicability | Observation | Detail |
| --- | --- | --- | --- | --- |
{{ evidence_table }}

## 3. Discrepancies observed

{{ discrepancies }}

*A discrepancy is a difference that survives normalisation. Formatting
differences are not listed here.*

## 4. Analyst assessment

| | |
| --- | --- |
| Analyst | {{ analyst.email }} · role {{ analyst.role }} |
| Determination | {{ analyst.determination }} |
| Recorded | {{ analyst.occurred_at }} |

{{ analyst.rationale }}

## 5. QA decision

| | |
| --- | --- |
| QA lead | {{ qa.email }} · role {{ qa.role }} |
| Action | {{ qa.action }} |
| Recorded | {{ qa.occurred_at }} |

{{ qa.reason }}

*Segregation of duties: analyst and QA lead are different individuals. Any
exception is recorded with its grantor and reason.*

## 6. Limitations

{{ limitations }}

## 7. Reportability

{{ reportability }}

*A determination is reportable only while a QA APPROVE stands. A later RETURN or
ESCALATE withdraws it.*

---

## Appendix — Evidence detail

{{ evidence_appendix }}
