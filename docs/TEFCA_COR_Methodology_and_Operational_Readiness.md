# TEFCA ARC — Methodology and Operational Readiness

**Management review document — read this before the COR meeting.**

| | |
| --- | --- |
| Contract | 7571MN26F80064 (RFQ 7571MN26Q00038) |
| Contractor | Alliance Global Tech, Inc. |
| Date | 2026-08-23 |
| Engineering baseline | commit `dd8b7b7` · 1,756 tests passed, 0 failed · **architecture frozen** |
| **Contract baseline** | **CONDITIONAL — EXECUTED AWARD VERIFICATION PENDING** |

---

## 1. Executive summary

Engineering is complete and frozen. Every requirement below is traced to the
**solicitation**, because the executed award is not available locally. Anything
the award modified would supersede this document, so requirements are marked
**AWARD_VERIFICATION_PENDING** where an award change is plausible.

Five things management needs to know before speaking to the COR:

1. **The Task 2 methodology deliverable was due approximately 09 July 2026** (two
   weeks after an award believed to be ~25 June 2026). It is not yet submitted.
   Substantially all of the content exists; §18 sets out a recovery plan.
2. **We previously mislabelled the Government's own taxonomy as ours.** The four
   discrepancy categories are defined in Section C. Only our internal shorthand
   is AGT's. Corrected in §7.
3. **Tasks 3 *and* 4 both require a statistically representative sample at ≥95%
   confidence, drawn per QHIN.** We have screened 100% of the population. A
   census is operationally stronger but it is **not** the specified deliverable,
   and screening everything does not discharge a sampling requirement. §4.
4. **The entire security and privacy requirement set was absent from our earlier
   matrix** — FIPS-199 Moderate, NIST 800-53, CUI, PTA/PIA, HSPD-12, NDAs,
   training, and a **one-hour** incident notification obligation. §15.
5. **No official finding can be issued** until the dataset's contractual
   provenance is documented and humans have actually performed reviews. Zero
   analyst determinations and zero QA approvals exist today.

Nothing in this document was implemented in code. Gaps are classified, not fixed.

## 2. Contract task crosswalk

| Task | Deliverable | Contract due | Status |
| --- | --- | --- | --- |
| 1 Administrative | 1 Meeting schedule | Kickoff within **5 business days** of award; 60-min weekly for first 90 days, then 30-min bi-weekly | Contract administration |
| 2 Review Methodology | 2 COR-accepted protocol | **Within 2 weeks of award** | **OVERDUE — see §18** |
| 3 Retrospective | 3.1 Weekly progress reports | Weekly | Template ready |
| 3 Retrospective | 3.2 Final report | **30 days after completion** of the 120-day review | Template ready; sample outstanding |
| 4 Ongoing | 4.1 Bi-weekly progress reports | Bi-weekly | Template ready |
| 4 Ongoing | 4.2 **Quarterly reports** | Every calendar quarter | **Template outstanding** |
| 5 Priority | 5.1 Status reports | **At the direction of the COR** | Template ready |
| 5 Priority | 5.2 **Quarterly reports** | Every calendar quarter | **Template outstanding** |
| 6 Closeout | 6.1 Closeout report | Date agreed with COR, within 90 days | Skeleton |
| 6 Closeout | 6.2 **Educational presentation** | Date agreed with COR, within 90 days | **Outline outstanding** |

Period of performance: base **25 Jun 2026 – 24 Jun 2027**, four 12-month options.
Retrospective 120-day window therefore closes on or about **23 Oct 2026**.

> **Contract internal inconsistency to raise with the CO/COR:** Section C heads
> Task 6 "within 90 days of contract expiration" while Section F says "within 90
> days **prior to** contract expiration". These are different dates. Ask.

## 3. Review methodology — by contract task

### Task 2 — Review Methodology and Control Framework

**Purpose.** Establish the COR-accepted protocol governing every later review.
**Contract requires it to:** align to the **Common Agreement, the QHIN Technical
Framework (QTF), all SOPs and any other documents shared by the COR**; include
approaches for evaluating accuracy of QHIN submissions; include methodologies for
**stratifying and prioritising**; establish a **discrepancy taxonomy**; and
**submit sampling methodology and confidence interval calculations**.

**Status.** Drafted (26 sections). **Two blockers:** AGT does not hold the Common
Agreement, QTF or SOPs, and the sampling parameters in §4 need COR confirmation.
**Dependency:** COR must supply those documents.

### Task 3 — Retrospective Review (base year)

| | |
| --- | --- |
| **Purpose** | Assess accuracy of Participant and Subparticipant information in the first 120 days |
| **Input** | Directory delivery provided by the COR, plus publicly available and contractor-owned data |
| **Population** | 23,566 delivered records — 11,077 Participants, 12,489 Subparticipants, 11 QHINs |
| **Sampling** | Statistically representative sample **from each QHIN**, at or above **95% confidence**. "The sample size will be determined by the confidence level." |
| **Sources** | NPPES, PPEF (5 components), OIG LEIE, CMS Revocation; SAM.gov unavailable |
| **Applicability** | Decided per record before any lookup; no NPI means no PPEF key |
| **Matching** | Organisation OID is the only unique key. TEFCAID is **not** unique |
| **DQ tests** | See §6 |
| **Discrepancy identification** | Difference surviving normalisation, recorded with both values |
| **Evidence** | Identifier searched, source edition, artefact hash, rule version, observation hash |
| **Analyst** | 12-step SOP; written rationale mandatory |
| **QA** | Independent reviewer; APPROVE / RETURN / ESCALATE |
| **Escalation** | Conflicting evidence the methodology does not resolve; precedent-setting determinations |
| **Reportability** | Only while a QA APPROVE stands |
| **COR output** | 3.1 weekly stratified list; 3.2 final report with aggregated 120-day data |
| **Turnaround** | Weekly; final within 30 days of completion |
| **Audit** | Append-only evidence; every figure reconstructable to a source row |
| **Acceptance evidence** | Reconciliation 18/18; 6/6 controlled reconstructions with artefacts re-hashed |
| **Dependencies** | Sample parameters (§4); COR-approved Task 2 protocol |

### Task 4 — Ongoing Review

Same method, applied bi-weekly to **new submissions from each QHIN** — and, per
the contract, again as a **statistically representative sample at ≥95%
confidence**, not as full processing of the increment.

**Net-new definition (AGT proposal, for COR confirmation):** a record whose
**organisation OID** is absent from the immediately preceding delivery. A record
whose OID is present but whose content hash differs is **changed**, not new.
AGT recommends changed records also be reviewed, because a corrected address or a
new NPI is exactly the kind of change the programme exists to catch — but the
contract says "new submissions", so this is a **COR confirmation** item.

**Status.** Delta logic implemented and unit-validated. **Only one delivery has
been received**, so it has never run against a real second delivery.

### Task 5 — Priority Reviews

| | |
| --- | --- |
| **Trigger** | Issues identified by the COR |
| **Volume** | **Anticipated average of 20 per month**, across the period of performance |
| **Surge** | Contract requires capability **beyond** the average, responding within the agreed deadline. Q&A confirms months will run above and below 20 |
| **Deadline** | **Set by the COR per review.** The contract states **no fixed turnaround** |
| **SLA clock start** | AGT proposal: when the COR communicates the request and the identified entities |
| **SLA clock stop** | AGT proposal: when the status report is delivered to the COR |
| **Output** | 5.1 status report — identified issue, root cause if determined, severity; 5.2 quarterly aggregate |
| **QA** | Same independent QA gate |
| **Source unavailable** | Disclosed in the report; never reported as an adverse result |

> **Correction for the record:** there is **no 24-hour priority-review
> requirement** in the solicitation. The 24-hour and one-hour clocks that do exist
> are **security incident** obligations (§15). If the COR expects a 24-hour
> priority turnaround, that must come from the award or written direction.

### Task 6 — Closeout

Complete report of methodologies, framework and **all tools developed**,
including files and data produced, delivered electronically to the COR — plus a
**closeout educational presentation**.

> **Commercially material:** "The government obtains **unlimited rights** to the
> methodologies created and any adaptations of pre-existing methodologies and
> deliverables created under this contract." Management should confirm what this
> means for pre-existing DocuAction IP before closeout.

## 4. Statistical sampling method

**What the contract fixes:** representative sample **per QHIN**; **≥95%
confidence**; sample size determined by the confidence level; sampling
methodology and confidence interval calculations submitted under Task 2;
stratification and prioritisation methodology required.

**What the contract does not state, and AGT must propose:** the margin of error,
the stratification variable, randomisation method, replacement handling, and the
treatment of QHINs too small to sample meaningfully.

**AGT proposal for COR confirmation**

| Parameter | Proposal | Rationale |
| --- | --- | --- |
| Sampling unit | One delivered organisation record, keyed by organisation OID | The only unique key |
| Frame | 23,566 delivered records, partitioned by managing QHIN | Contract says "from each QHIN" |
| Confidence | **95%** (z = 1.96) | Contract floor |
| Margin of error | **±5%** | Standard; yields a workable size |
| Method | Cochran with finite-population correction, per QHIN | Two QHINs have 44 and 3 records — FPC matters |
| Selection | Simple random, seeded and recorded | Reproducible |
| Replacement | Without replacement | Finite frame |
| Small strata | Where a QHIN's population is at or below the computed size, **review the whole stratum** and disclose it | A 3-record QHIN cannot be sampled |
| Reproducibility | Persist seed, frame, size, confidence, margin and membership | Sample must be re-examinable |

**Census screening and the contractual sample are different things, and both are
kept.** AGT screens 100% of the population operationally — it is cheap once
automated and finds everything a sample would miss. The contractual deliverable
is the **sample**, reported with its confidence calculation. The census is
reported separately as supplementary population-level observation. **Screening
everything does not discharge a sampling requirement**, and the retrospective
report will not claim that it does.

**Not yet executed.** The sample will be drawn once the COR confirms margin of
error and stratification, so the drawn sample matches the approved methodology.

## 5. Authoritative source matrix

| Source | Purpose | Applicability | Identifier | Data obtained | Match logic | Update | Evidence retained | Unavailable treatment | Limitation | Report disclosure |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **RCE/TEFCA delivery** | Subject of review | All | Organisation OID | Full 41-field record | n/a — reviewed, not corroborating | Per delivery | File byte-for-byte + hash | n/a | TEFCAID/HCID not unique | Delivery id and hash on every report |
| **NPPES** | Identity, name, practice location, taxonomy | Records with an NPI | NPI | Legal name, address, taxonomy, entity type | Exact NPI | Monthly | Dated file + hash | SOURCE_UNAVAILABLE | Enumeration ≠ licensure | Edition and hash cited |
| **PECOS / PPEF** | Medicare enrolment and relationships | NPI + Medicare relevance | NPI → ENRLMT_ID | Enrolment, practice location, reassignment, specialty, additional NPIs | Exact NPI, then enrolment id | Quarterly | Dated files + hashes | SOURCE_UNAVAILABLE | **No street line**; no payment suspension | Scope stated on every address figure |
| **OIG LEIE** | Exclusion | All identifiable | NPI; business name fallback | Exclusion type, date, address | NPI decisive; **name-only = AMBIGUOUS** | ~Monthly | Dated file + hash | SOURCE_UNAVAILABLE | Most individual rows carry 0000000000 | Ambiguous results labelled as such |
| **CMS Revocation** | Revoked billing privileges | NPI + Medicare relevance | NPI | Revocation record | Exact NPI | Quarterly | Dated file + hash | SOURCE_UNAVAILABLE | Revocation ≠ TEFCA finding | Cited with edition |
| **SAM.gov** | Federal registration / debarment | All | UEI / name | — | — | — | — | **SOURCE_UNAVAILABLE — no credential** | Unevaluated for all 23,566 | Listed as a source limitation in every report |

**SOURCE_UNAVAILABLE is never converted to NO_MATCH.** This is asserted by test,
not by convention.

## 6. Verification and testing methodology

*Government-readable. Software produces evidence; a determination is always human.*

| # | What is tested | Source(s) | Pass condition | Exception condition | Analyst action | QA action | Report treatment |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Source file integrity | Delivery | SHA-256 re-verifies | Mismatch | Stop; escalate | Halt release | Reporting suspended |
| 2 | Schema | Delivery | Header fingerprint matches locked map | Drift | Reconcile map | Hold promotion | Delivery held |
| 3 | Record/control count | Delivery | Stored rows = declared | Difference | Investigate | Hold | Disclosed |
| 4 | Identifier presence | Delivery | Organisation OID unique | Duplicate/absent | Review | Verify linkage | Disclosed |
| 5 | NPI validity | Delivery | 10 digits, valid check digit | Malformed / multi-valued | Determine, do not classify (**D6**) | Confirm no classification | Disclosed as data quality |
| 6 | Organisation relationships | Delivery | parentOf resolves to a record or a QHIN | Dangling | Review | Verify | Disclosed |
| 7 | Cross-source corroboration | NPPES, PPEF | Identifier resolves | No match / multiple | Adjudicate | Verify reasoning | Observation |
| 8 | Address comparison | NPPES, PPEF | Match after normalisation | Conflict | **Do not determine — D4_ADDRESS_MATERIALITY** | RETURN any determination | Methodology-pending |
| 9 | Exclusion / revocation | OIG, CMS | No match | Match, or name-only | Adjudicate; name-only is not an exclusion | Verify corroboration | Observation until QA approval |
| 10 | Source applicability | All | Correct per entity type | Misapplied | Correct | Verify | n/a |
| 11 | Duplicate detection | Delivery | OID unique | Repeated name/NPI | Review | Verify | Disclosed |
| 12 | Missing / inconsistent values | Delivery | Required fields present | Absent | Review | Verify | Data-quality section |
| 13 | Evidence preservation | System | Artefact re-hashes | Mismatch | Stop | Halt release | Reporting suspended |
| 14 | Analyst review | Human | Rationale addresses the evidence | Absent or unsupported | — | RETURN | Not reportable |
| 15 | Independent QA | Human | Different person; APPROVE stands | Self-review attempted | — | Refused by system | Not reportable |
| 16 | Report reconciliation | System | Every figure matches a canonical query | Difference | — | Withhold figure | Figure withheld, not the disclosure |
| 17 | Audit / reconstruction | System | Figure traces to a source row | Cannot trace | — | Withhold | Figure withheld |

Current results: reconciliation 18/18 · 0 unexplained report differences ·
6/6 controlled reconstructions with all artefacts re-hashed and matching.

## 7. Discrepancy method — the Government's taxonomy

**Correction.** Earlier AGT documents described the four-bucket taxonomy as an
AGT construct with no federal basis. **That was incorrect.** Section C specifies
it directly, in Tasks 3 and 4.

| Government term (Section C) | Contract citation | Internal representation | Analyst meaning | QA treatment | Report representation |
| --- | --- | --- | --- | --- | --- |
| 1) **No discrepancies identified** | §C Tasks 3, 4 | B1 | Applicable sources answered; nothing adverse | Confirm evidence was sufficient | Government term |
| 2) **Minor or administrative discrepancies** | §C Tasks 3, 4 | B2 | Difference not affecting identification or eligibility | Confirm "minor" is supported | Government term |
| 3) **Inexplicable discrepancies** | §C Tasks 3, 4 | B3 | Difference the evidence cannot explain | Confirm explanation was genuinely sought | Government term |
| 4) **Non-compliant discrepancies** | §C Tasks 3, 4 | B4 | Difference indicating non-conformance | **Highest scrutiny** — must be evidenced | Government term |

**Government-facing documents will use the Government's wording.** B1–B4 remains
internal shorthand only. Assignment rules are AGT methodology and require COR
acceptance under Task 2.

## 8–9. Analyst and QA workflow

Analyst: open case → verify identity **by OID** → verify applicability → review
observation → review source evidence → review provenance → review related
evidence → check methodology-pending → check source limitations → record
rationale → record determination → submit to QA. **Self-approval is refused by
the system.**

QA: independently inspect the evidence before reading the rationale → work the
checklist → APPROVE / RETURN / ESCALATE with a reason. Only APPROVE creates
reportability; a later RETURN or ESCALATE withdraws it. Determinations are
append-only; a revision supersedes and never overwrites.

Full procedures: `docs/deliverables/TEFCA_ARC_Analyst_SOP_DRAFT.md`,
`TEFCA_ARC_QA_SOP_DRAFT.md`.

## 10–12. Retrospective, ongoing and priority reviews

Covered in §3. Operational cadence:
`docs/deliverables/TEFCA_ARC_Operations_Playbook_DRAFT.md`.

## 13. Reporting templates

| Template | Contract mapping | Status |
| --- | --- | --- |
| Retrospective Review Report | 3.2 | Ready — needs sample methodology section |
| Weekly Progress Report | 3.1 | Ready |
| Bi-Weekly Progress Report | 4.1 | Ready |
| **Quarterly Report** | **4.2 and 5.2** | **OUTSTANDING** — 90-day aggregate overview |
| Priority Review Report | 5.1 | Ready — concise operational format, distinct from the retrospective |
| Closeout Report | 6.1 | Skeleton |
| **Closeout Educational Presentation** | **6.2** | **OUTSTANDING** — outline needed |

Reports are **not** forced into one template: priority reviews are concise and
operational; the retrospective carries population, sample methodology and
aggregate findings.

## 14. COR decisions required — reduced

Ten questions were previously put forward. On re-reading the contract, **five are
answered or are AGT's to decide**.

| ID | Reclassified | Why |
| --- | --- | --- |
| **D2** no rule matches | **AGT_CAN_DECIDE** | An internal rule-coverage question. AGT will route to review rather than default to category 1, and disclose it |
| **D6** identifier quality states | **AGT_CAN_DECIDE** | Data-quality vocabulary, not a Government determination |
| **D8** retention | **CONTRACT_ALREADY_ANSWERS** | EO 13556 records management and closeout disposition are specified |
| **D9** deliverable format | **CONTRACT_ALREADY_ANSWERS** | Section F specifies delivery to the COR; §D/E govern marking and acceptance |
| **D7** automated exclusion finding | **COR_CONFIRMATION_RECOMMENDED** | AGT position is firm — no automated exclusion findings. Confirm only |

**Genuine COR decisions — four**

| ID | Issue | Contract language | AGT recommendation | Alternative | Operational impact | Question for the COR |
| --- | --- | --- | --- | --- | --- | --- |
| **D4-ADDR** | When is an address difference material? | §C requires accuracy assessment; silent on address materiality | Treat street-line differences between a **registered** address and a **practice location** as informational; treat state or ZIP differences as reportable | Any difference is a discrepancy | **9,032 records** (38%) move in or out of the discrepancy count | Which address differences should be reported as discrepancies? |
| **D4-SRC** | Effect of an unavailable source | §C requires thorough review; silent | Proceed with a disclosed evidence gap | Hold all records until the source answers | **23,566 records** — SAM is unavailable for the whole population | May reviews proceed with SAM disclosed as unavailable? |
| **D3** | Which tier reviews "inexplicable" discrepancies | §C defines the category, not the reviewer | Senior analyst | Reviewer | Staffing; queues cannot open until answered | Who should adjudicate category 3? |
| **D5** | Which name differences are reportable | §C silent | Report only where the difference affects identification | Report all differences | Determines whether name differences enter the count at all | Which name differences constitute a discrepancy? |

Plus two **information requests**: the Common Agreement / QTF / SOPs, and the
dataset transmittal and control total.

## 15. Security and privacy readiness

**This was absent from earlier analysis and is now the largest workstream.**

| Requirement | Contract | Implemented | Status | Owner | Before DEV | Before PROD | Before delivery |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **FIPS-199 Moderate** (C/I/A all Moderate) | §C | Not assessed | **GAP** | Security | Categorisation memo | Full assessment | — |
| **NIST SP 800-53 Moderate** | §C | Application controls only | **GAP** | Security | Control mapping | Assessment + POA&M | — |
| FIPS 140 / CMVP encryption | §C | TLS + Azure at rest | **PARTIAL** | Eng | Confirm CMVP validation | Attestation | — |
| Privacy Act | §C | n/a — no SORN yet | **PARTIAL** | PM | — | Monitor SORN | — |
| **CUI** (EO 13556) | §C | Not marked | **GAP** | PM | Marking scheme | Handling procedure | **Mark deliverables** |
| **PTA / PIA** | §C | Not started | **GAP** | PM | Support PTA | PIA if required | — |
| **Incident reporting — ONE HOUR** | §C | Technical playbook only | **GAP** | Ops | Federal reporting path | Rehearse | — |
| IOC response within 24 hours | §C | None | **GAP** | Ops | Define | Rehearse | — |
| Do **not** notify individuals unless instructed | §C | Not documented | **GAP** | PM | Add to playbook | — | — |
| Personnel security / HSPD-12 Tier 1 | §C | None | **GAP** | HR | Start investigations | Complete | — |
| Annual + role-based training | §C | None | **GAP** | HR | Enrol | Records | — |
| **NDAs** (OpDiv form, per employee) | §C | None | **GAP** | HR | Execute | Submit copies | — |
| Rules of Behavior | §C | None | **GAP** | HR | Distribute | Signed | — |
| Access control / least privilege | §C | 4-role RBAC, SoD, Area-1 DB refusal | **PASS** | Eng | — | Owner-role transfer | — |
| Logging / audit retention | §C | Append-only, 23,812 rows | **PASS** | Eng | — | Retention config | — |
| Backup / contingency | §C | Documented, not rehearsed | **PARTIAL** | Eng | Rehearse restore | Rehearse | — |
| **Section 508** — demonstrate conformance **before acceptance**; HHS checklist or ACR/VPAT | §C, clause 352.239-79 | Automated checks pass | **PARTIAL** | Eng | — | — | **Checklist/ACR per deliverable** |
| Records disposition / sanitisation | §C | Not defined | **GAP** | PM | — | Procedure | Closeout |
| **Section 889** Parts A and B | §C | Not attested | **GAP** | Contracts | Attestation | — | — |
| **OCI / independence** | §C post-award clause | Affirmation submitted with quote | **PARTIAL** | Contracts | Confirm on file | Monitor | — |
| Staff roster | §C | None | **GAP** | PM | Produce | Maintain | — |
| Comptroller General records access | §C | Retained | **PASS** | Eng | — | — | — |

**Highest risk:** the one-hour incident notification with no defined federal
reporting path; NDAs, training and HSPD-12 not started for staff who will handle
government information; and no 800-53 control assessment.

## 16. Five-case human pilot

Five cases selected from real evidence, ready for manual review. Worksheets:
`docs/analyst_qa_pilot_package.md`.

| Case | Category | Entity | Line |
| --- | --- | --- | --- |
| 1 | Clean identity | MI - Allergy Asthma and Pulmonary Center | 11,927 |
| 2 | Two NPIs in one field; record **held** at NPI-002 | El Dorado Clinic, P.A. | 12,684 |
| 3 | OIG exclusion matched on NPI | FL - The Pain Management Institute LLC | 11,189 |
| 4 | Three-field address conflict | AA - Humana PCO | 11,174 |
| 5 | Name-only OIG hit | Family Medical Clinic | 1,504 |

**No determination and no QA decision is pre-filled.** `review_decision_events`
holds 0 rows and must still hold 0 when the pilot begins.

## 17. Known external dependencies

1. **Executed award 7571MN26F80064** — not held. §19.
2. Common Agreement, QTF, SOPs — required by Task 2.
3. Dataset transmittal and control total.
4. Four COR decisions (§14).
5. SAM.gov credential.
6. Analyst and QA staffing.
7. Sampling parameters — margin of error and stratification.

## 18. Task 2 recovery plan

| | |
| --- | --- |
| Contractual due date | Within 2 weeks of award — approximately **09 July 2026** if award was 25 June 2026 |
| Current date | 23 August 2026 |
| Current status | Draft complete (26 sections); not yet submitted |
| Material already complete | Methodology draft, discrepancy taxonomy crosswalk, source matrix, verification matrix, analyst and QA SOPs, report templates, decision brief |
| Remaining gaps | Alignment to Common Agreement/QTF/SOPs (documents not held); sampling methodology and confidence interval calculations pending two parameters; Government terminology substituted throughout |
| Recommended immediate action | Request the Common Agreement, QTF and SOPs from the COR **today**; submit the methodology as an initial draft **for COR review** rather than waiting for completeness |
| Proposed COR communication | *"AGT has completed a draft Review Methodology and Control Framework and is ready to submit it for COR review. To align the protocol to the Common Agreement, QHIN Technical Framework and applicable SOPs as Task 2 requires, we request copies of those documents. We also request confirmation of two sampling parameters — margin of error and stratification variable — so the sampling methodology and confidence interval calculations we submit match the approach the COR expects. We propose submitting the draft within five business days of receiving those materials, and are available to review it at the COR's convenience."* |
| Proposed delivery date | **Within 5 business days of receiving the Common Agreement, QTF and SOPs** |

Neutral, factual, no admission. The award date and any COR-agreed extension are
unknown to engineering — **confirm the actual award date before sending
anything**, since the entire schedule depends on it.

## 19. Executed award gap

> ## ACTION: OBTAIN EXECUTED AWARD 7571MN26F80064
>
> Not present on this filesystem. Every requirement in this document is traced to
> the **solicitation**. On receipt, compare award vs solicitation vs SOW vs
> amendments vs Q&A and record any award-specific change.
>
> **CONTRACT BASELINE: CONDITIONAL — EXECUTED AWARD VERIFICATION PENDING.**
> Requirements most likely to have been modified at award: period of performance
> dates, priority-review volume, deliverable schedule, and the COR designation.

## 20. Management recommendations

**Gap classification** — nothing was coded in response to any of these:

| Class | Items |
| --- | --- |
| **PROCESS_FIX** | Incident reporting path; staff roster; records disposition; NDAs; training; Rules of Behavior |
| **CONFIGURATION_FIX** | 508 checklist per deliverable; CUI marking; retention settings |
| **DOCUMENTATION_FIX** | Government terminology substitution; quarterly report templates; presentation outline; sampling methodology write-up |
| **COR_DECISION** | D4-ADDR, D4-SRC, D3, D5 (+ D7 confirmation) |
| **CREDENTIAL_DEPENDENCY** | SAM.gov API key |
| **SECURITY_ACTION** | FIPS-199 categorisation; 800-53 assessment; PTA/PIA; HSPD-12; Section 889 attestation |
| **CODE_CHANGE_REQUIRED** | Draw and persist the per-QHIN sample; quarterly report generation; contract-number citation in report headers. **Each requires separate authorisation after management review.** |

**Recommendations**

1. Confirm the actual award date before any COR communication — the schedule
   depends on it.
2. Request the Common Agreement, QTF and SOPs today; they gate Task 2.
3. Submit the methodology draft for review rather than holding it for perfection.
4. Treat security and privacy as a staffed workstream, not an engineering task.
5. Run the five-case pilot this week — it costs little and is the only thing that
   converts a certified system into a working review capability.
6. Do not issue any COR-facing finding until provenance is documented and humans
   have reviewed.
