# COR Decision Log

**TEFCA ARC · Contract 7571MN26F80064**
For use during and after the methodology review meeting.

---

## How this log is used

One row per decision. AGT completes the left-hand columns before the meeting;
**the Government columns are completed by the COR**, in the meeting or in
writing afterwards.

**No Government decision is pre-populated.** A blank decision column means the
decision has not been made, and AGT will continue to report the affected
condition as awaiting methodology until it is.

Once a decision is recorded, AGT issues the methodology version that reflects
it, and every subsequent deliverable states which version was applied.

---

## Legend

| Column | Meaning |
| --- | --- |
| **Effective version** | The methodology version from which the decision applies. AGT assigns this once the decision is recorded. |
| **Implementation required** | Whether AGT must change configuration or software before the decision takes effect. |

---

## Methodology decisions

| ID | Question | AGT recommendation | COR decision | Date | Decision authority | Affected workflow | Affected report | Implementation required | Effective version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **D4-ADDR** | When is an address difference material? | Street-line difference informational; state or ZIP difference is minor/administrative | | | | Discrepancy classification; analyst queue volume | D3.1, D3.2, D4.1 | Configuration | |
| **D1** | Uncorroborated provider identifier — how classified? | Minor or administrative | | | | Discrepancy classification | D3.1, D3.2, D4.1 | Configuration | |
| **D2** | No classification rule matches — what result? | Route to analyst; report as a proposed methodology change | | | | Analyst queue | D3.1, D4.1 | None | |
| **D3** | Category 3 adjudication tier | Reviewer, with mandatory independent QA | | | | Analyst assignment | None | None | |
| **D4** | Unavailable source — classification or readiness? | Readiness matter; disclose, never infer | | | | All verification | All reports | None | |
| **D5** | Which name differences are reportable? | Survives normalisation = minor/administrative; different organisation = inexplicable | | | | Discrepancy classification | D3.1, D3.2, D4.1 | Configuration | |
| **D6** | "Flagged" or "invalid" identifier? | Flagged, with the defect stated | | | | Evidence presentation | All reports | None | |
| **D7** | Name-only exclusion match — reportable? | No. Requires a decisive identifier match plus determination and QA | | | | Analyst queue; category 4 | All reports | None | |
| **D8** | Records retention period and disposition | Contract life plus a COR-specified period; no period proposed by AGT | | | | Evidence and report storage | None | Configuration | |
| **D9** | Deliverable format and 508 checklist | Accessible HTML with PDF companion, plus the HHS checklist | | | | Deliverable production | All deliverables | Configuration | |

---

## Sampling parameters

| ID | Question | AGT recommendation | COR decision | Date | Decision authority | Affected workflow | Affected report | Implementation required | Effective version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **S1** | Population of record — connections or distinct entities? Effective date? | The delivered file, fixed at receipt | | | | Sample frame | D3.2 | None | |
| **S2** | Minimum per-QHIN sample size? | Yes; AGT proposes a floor with the allocation | | | | Sample allocation | D3.2 | None | |
| **S3** | Secondary stratification beyond QHIN? | No, absent a stated analytic purpose | | | | Sample allocation | D3.2 | None | |
| **S4** | Margin of error | ±5%, giving 383 entities | | | | Sample size | D3.2 | None | |
| **S5** | Replacement policy for unreviewable entities | Replace within stratum in seeded order; disclose every substitution | | | | Sample execution | D3.2 | None | |
| **S6** | Exclusions from the frame; duplicate handling | No exclusions; duplicates identified and reported | | | | Sample frame | D3.2 | None | |

---

## Data transfer

| ID | Question | AGT recommendation | COR decision | Date | Decision authority | Affected workflow | Affected report | Implementation required | Effective version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **T1** | Transfer mechanism | Any Government-designated secure mechanism; AGT has no preference | | | | Population intake | None | Possibly | |
| **T2** | Does the HHS Data Access Agreement gate delivery? | AGT personnel have signed; AGT asks what remains outstanding | | | | Population intake | None | None | |
| **T3** | Treatment of subsequent deliveries | New version; do not overwrite; disclose corrections | | | | Population intake | All reports | None | |
| **T4** | Is the population the Q&A figure or the delivered file? | The delivered file defines it | | | | Sample frame | D3.2 | None | |
| **T5** | Entities to be excluded from review | None, absent direction | | | | Sample frame | D3.2 | None | |

---

## Priority review operations

| ID | Question | AGT recommendation | COR decision | Date | Decision authority | Affected workflow | Affected report | Implementation required | Effective version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **P1** | Request channel and acknowledgement | Acknowledge within one business day | | | | Priority intake | D5.1 | None | |
| **P2** | Anticipated surge periods | AGT asks so staffing can be planned | | | | Staffing | None | None | |
| **P3** | Multi-entity requests — deadline per request or per entity? | AGT asks; affects sequencing | | | | Priority scheduling | D5.1 | None | |
| **P4** | Standing target turnaround in addition to per-request deadlines? | AGT proposes none; the contract sets deadlines per request | | | | Priority reporting | D5.1, D5.2 | None | |

---

## Contract administration

| ID | Question | AGT recommendation | COR decision | Date | Decision authority | Affected workflow | Affected report | Implementation required | Effective version |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **A1** | Written acceptance of the D2 methodology | Requested | | | | All review tasks | All deliverables | None | |
| **A2** | Issue of the Government assignment authorising Task 3 | Requested | | | | Task 3 start | All Task 3 | None | |
| **A3** | Target date for entity data delivery | Requested | | | | Task 3 start | All Task 3 | None | |
| **A4** | Route to SAM.gov access for the verification account | Requested | | | | One verification source | All reports | Configuration | |
| **A5** | Agreed delivery date for the closeout deliverables | To be agreed; the schedule requires delivery within 90 days **prior to** expiration | | | | Task 6 | D6.1, D6.2 | None | |

---

## Summary for the record

| | |
| --- | --- |
| Decisions requested | **25** |
| Recorded as at 2026-08-24 | **0** |
| With a safe conservative default AGT can apply meanwhile | 6 |
| **With no safe default — affected conditions held and disclosed** | **D4-ADDR, D1, D5** |

AGT will not resolve any open decision by choosing a default in software.
