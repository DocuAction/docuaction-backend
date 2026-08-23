# QA Review Checklist

**Operational — internal use.** Completed by the QA lead before recording
APPROVE, RETURN or ESCALATE. This is not a separate QA process: every item maps
to a control already implemented in the decision-event workflow, and the outcome
is recorded as a decision event, not as a form.

| | |
| --- | --- |
| Review ID | {{ review_id }} |
| Entity | {{ entity_name }} · OID `{{ org_oid }}` |
| Analyst | {{ analyst_email }} |
| QA lead | {{ qa_email }} |
| Date | {{ qa_date }} |

## A — Subject and scope

| # | Check | Pass |
| --- | --- | --- |
| A1 | Evidence cited belongs to **this** entity (organisation OID matches; TEFCAID alone is insufficient — it is not unique) | [ ] |
| A2 | Source applicability is correct for this entity type | [ ] |
| A3 | A source recorded NOT APPLICABLE is genuinely inapplicable, not merely unanswered | [ ] |

## B — Evidence quality

| # | Check | Pass |
| --- | --- | --- |
| B1 | Every cited observation names its source edition | [ ] |
| B2 | Every cited source artefact hash is recorded | [ ] |
| B3 | Evidence is complete for every REQUIRED source, or the gap is disclosed | [ ] |
| B4 | Conflicting evidence between sources is addressed, not ignored | [ ] |
| B5 | Evidence is from the **current** approved evidence version | [ ] |

## C — Methodology

| # | Check | Pass |
| --- | --- | --- |
| C1 | The methodology version applied is stated | [ ] |
| C2 | No methodology-pending condition is treated as decided | [ ] |
| C3 | **No unsupported conclusion.** Nothing is called failed, non-compliant, invalid, inaccurate or unverified without an approved rule supporting that word | [ ] |
| C4 | An unavailable source is not reported as an adverse result | [ ] |
| C5 | Observation counts are not presented as entity counts | [ ] |

## D — Analyst work

| # | Check | Pass |
| --- | --- | --- |
| D1 | A written rationale is present and addresses the evidence | [ ] |
| D2 | The determination follows from the evidence cited | [ ] |

## E — Controls

| # | Check | Pass |
| --- | --- | --- |
| E1 | **Segregation of duties:** the QA lead did not make this determination | [ ] |
| E2 | Any segregation exception is granted by a different, senior individual with a written reason | [ ] |
| E3 | No prior decision has been overwritten; a change is a new event | [ ] |

## F — Decision

- [ ] **APPROVE** — determination stands and becomes reportable
- [ ] **RETURN** — back to the analyst; reason required
- [ ] **ESCALATE** — to a named individual; recipient and reason required

Reason: {{ qa_reason }}

## G — Release gates (report level, not finding level)

| # | Gate | Status |
| --- | --- | --- |
| G1 | Evidence version — current approved only | {{ gate_evidence }} |
| G2 | Human QA — every asserted finding approved | {{ gate_qa }} |
| G3 | Methodology — no conclusion on a pending decision | {{ gate_methodology }} |
| G4 | Dataset contractual provenance | {{ gate_provenance }} |
| G5 | Report QA — rendered completely | {{ gate_report }} |

> G4 is not an engineering gate and cannot be cleared by testing. Until the
> dataset sender, transmittal and control total are documented, every report is
> watermarked `DRAFT — NOT FOR COR RELEASE`.
