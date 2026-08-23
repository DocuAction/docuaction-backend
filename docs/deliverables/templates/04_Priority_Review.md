# Priority Review — {{ priority_review_id }}

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
> moment has passed. No monthly volume or surge target is asserted; the source
> material available does not state one.

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
