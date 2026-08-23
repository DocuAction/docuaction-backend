# TEFCA ARC — COR Methodology Review Package

**DRAFT — FOR METHODOLOGY REVIEW · NOT AN OFFICIAL GOVERNMENT FINDING**

| | |
| --- | --- |
| Contract | 7571MN26F80064 (RFQ 7571MN26Q00038) |
| Contractor | Alliance Global Tech, Inc. |
| Relates to | Task 2, Deliverable 2 — Review Methodology and Control Framework |
| Date | 2026-08-23 |
| Methodology version | `arc-methodology-0.1` |

This package supports COR review of the Task 2 protocol. The full methodology is
`docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md`; this document is the
COR-facing summary of **what AGT does**, plus the decisions AGT needs from the
COR. Internal platform architecture is deliberately omitted.

---

## Who decides what

| Layer | Produced by | Example |
| --- | --- | --- |
| **System observation** | Software | "OIG LEIE returned a match on NPI 1639333859" |
| **AGT analysis** | Software + rules | "This is an adverse observation requiring adjudication" |
| **Analyst determination** | A named analyst, with a written rationale | "The NPI belongs to this entity; the exclusion is current" |
| **QA approval** | A different, named QA lead | APPROVE / RETURN / ESCALATE |
| **Government decision** | COR / ONC | Whether it constitutes a compliance action |

**Software never makes a final government finding.** The system produces evidence
and observations; a finding requires an analyst determination and QA approval;
what the government does with a finding is the government's decision.

## 1. Purpose and scope
Verify that QHIN onboarding of Participants and Subparticipants produces
information that is complete, accurate and appropriately documented, per §C.

## 2. Review population
23,566 delivered organisation records — 11,077 Participants, 12,489
Subparticipants, across 11 QHINs. Every rate divides by delivered records.

## 3. Intake
The delivery is retained byte-for-byte with its SHA-256 recorded and
re-verified before each use. No delivered value is ever altered.

## 4. Authoritative source preservation
Each source is retained as a dated file with its own hash and record count, and
reviews run against the retained file rather than a live query — so a review can
be repeated months later and produce the same answer.

## 5. Data-quality review
Rules run over the delivery and record issues **without altering delivered
values**. Corrections are applied only in a curated layer, only where
deterministic and non-substantive, and always with the original value retained.

## 6. Identity and entity matching
Organisation OID is the only unique identifier in the delivery. **TEFCAID is not
unique** — 43 are shared across up to 69 records — and is never used as a match
key. Counting by TEFCAID would understate the population by 241.

## 7. Source applicability
Decided per record **before** any lookup: REQUIRED, APPLICABLE, CONDITIONALLY
APPLICABLE, NOT APPLICABLE, or UNKNOWN pending methodology. Where no NPI was
delivered there is no key, so Medicare enrolment can be neither established nor
refuted — 19.45% of records legitimately carry no NPI.

## 8. Verification sources
NPPES · CMS PPEF (enrolment, practice location, reassignment, secondary
specialty, additional NPIs) · OIG LEIE · CMS Revocation · **SAM.gov — not
evaluated, no credential**.

## 9. Evidence provenance
Every observation records the identifier searched, the source edition, that
edition's hash, the rule version, and a hash of itself. Any figure traces to the
source row that produced it; six controlled cases were reconstructed end to end
with all artefacts re-hashed and matching.

## 10. Exception identification
Deterministic triage sorts observations into: ready for analyst · methodology
pending · informational only · source limitation · duplicate. Triage assigns
work; it never assigns an answer.

## 11. Analyst review
A named analyst examines the evidence and records a determination with a
mandatory written rationale. Determinations are append-only; a revision is a new
record that references the one it supersedes.

## 12. Independent QA
A different, named QA lead records APPROVE, RETURN or ESCALATE. The system
refuses self-approval. **AGT applies independent QA although §C does not require
it**, because a determination no second person examined is not defensible.

## 13. Finding and reportability
A finding is reportable **only** while a QA APPROVE stands. A later RETURN or
ESCALATE withdraws it.

## 14. Source-unavailable treatment
A source that did not answer has said nothing about the entity. It is recorded as
unavailable and is never reported as an adverse result, a clearance, or a
discrepancy. SAM.gov is currently unavailable for all 23,566 records.

## 15–17. Priority, ongoing and retrospective reviews
Priority: COR-identified, deadline set by the COR, average 20/month with surge
capability. Ongoing: bi-weekly, each delivery compared to its predecessor.
Retrospective: 120 days, statistically representative sample per QHIN at or above
95% confidence.

> **AGT must flag one thing here.** The evidence collection completed to date is a
> **full-population census** of all 23,566 records, not a sample. A census is a
> superset of any sample, but the Task 3 deliverable is specified as a sample with
> a confidence calculation. AGT will draw and report the per-QHIN sample so the
> deliverable matches §C. No conclusion drawn from the census is offered as a
> sampled result.

## 18. Reporting
Weekly (3.1), final (3.2), bi-weekly (4.1), quarterly (4.2, 5.2), status (5.1),
closeout (6.1) and educational presentation (6.2). Reports use the four
contractual strata. Every figure carries its denominator and the source and
evidence versions it came from.

## 19. Audit and reconstruction
Evidence is append-only. Where a defect is found, a corrected version is issued
and the original remains queryable — reports read the current version only.

## 20. Decisions requested from the COR

| ID | Decision | What moves |
| --- | --- | --- |
| **D4_ADDRESS_MATERIALITY** | When is an address difference material? | 10,426 observations / **9,032 records** |
| **D4** | Does an unavailable source affect classification, or only readiness? | **23,566 records** (SAM) |
| **D3** | Does category 3 ("inexplicable") route to Reviewer or Senior Analyst? | Staffing; queues cannot open until answered |
| **D5** | Which name differences are reportable? | Whether name differences enter the count at all |
| **D7** | May an exclusion match become an automated finding? | AGT recommends **no** |
| **D1** | How is an uncorroborated NPI classified? | 2 records observed |
| **D2** | What is recorded when no rule matches? | Default category |
| **D6** | "Flagged" vs "invalid" identifier | 4 malformed NPI cells, incl. one two-NPI cell |
| **D8** | Records retention period | Storage and closeout |
| **D9** | Official deliverable format | PDF must be container-pinned if it is the format of record |

**No COR decision has been recorded on any of these.**

## Also requested from the COR

1. The **Common Agreement, QTF and SOPs** referenced in §C Task 2 — the
   methodology must align to them and AGT does not hold them.
2. The **dataset transmittal and control total** for the delivery under review.
   Its schema, lineage and content are verified; its contractual chain of custody
   is not documented, and no COR-facing finding can be released until it is.

---

# Appendix — proposed report format (worked example)

**DRAFT — FOR METHODOLOGY REVIEW · NOT AN OFFICIAL GOVERNMENT FINDING**

*This shows the format AGT proposes, populated with real current data.*

### Executive summary
Evidence has been collected for all 23,566 delivered records against five
authoritative sources. **No finding is reported**, because no analyst
determination has been made and no QA approval recorded. What follows is
observation, not conclusion.

### Population and scope
| | |
| --- | --- |
| Delivered records (denominator) | 23,566 |
| Participants / Subparticipants | 11,077 / 12,489 |
| QHINs referenced | 11 |
| Records with evidence collected | 23,566 (100%) |
| Records reviewed by a person | **0** |

### Sources
| Source | Edition | Records | Applicable |
| --- | --- | --- | --- |
| NPPES | 2026-08-09 | 9,726,865 scanned | 18,982 |
| PPEF Enrolment | Q3 2026 | 2,978,925 | 18,791 |
| OIG LEIE | 2026-08-10 | 83,842 | 18,982 |
| CMS Revocation | Q2 2026 | 8,136 | 18,791 |
| SAM.gov | — | — | **Not evaluated** |

### Results structure — the four contractual strata
| Stratum | Count | Note |
| --- | --- | --- |
| 1) No discrepancies identified | *pending analyst review* | Cannot be asserted before human review |
| 2) Minor / administrative | *pending* | |
| 3) Inexplicable | *pending* | Tier routing is **D3** |
| 4) Non-compliant | *pending* | |

> Stratification into the four contractual categories requires analyst
> determination. Reporting automated observations in these strata would present
> software output as a review finding.

### Observations (not findings)
| Observation | Count | Distinct records |
| --- | --- | --- |
| NPPES identity resolved | 18,976 | 18,976 |
| Medicare enrolment matched | 15,126 | 15,126 |
| OIG exclusion match (NPI) | 1 | 1 |
| OIG name-only (ambiguous) | 2 | 2 |
| CMS revocation match | 22 | 22 |
| Address conflict | **10,426** | **9,032** |

### Exceptions requiring review
28 items across 28 entities.

### Source limitations
SAM.gov unevaluated (23,566) · PPEF publishes no street line · payment suspension
not published · USPS Address API not exercised.

### Methodology decisions pending
10 (see §20). 33,992 observations are methodology-pending.

### Analyst / QA status
0 determinations · 0 QA decisions · 0 reportable findings.

### Evidence references
Delivery SHA-256 `689472073480b1cc…` · evidence version `phase6-bulk-1.1.0` ·
every figure traceable to a retained, re-hashed source artefact.
