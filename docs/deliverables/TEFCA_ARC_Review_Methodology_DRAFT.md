# TEFCA Accuracy Review and Correction — Review Methodology

**DRAFT — NOT FOR COR RELEASE**

| | |
| --- | --- |
| Contract | 7571MN26F80064 |
| Contractor | Alliance Global Tech |
| Deliverable | Task 2, Deliverable 2 — Review Methodology and Control Framework |
| Document version | 0.1 DRAFT |
| Methodology version | `arc-methodology-0.1` |
| Evidence version in force | `phase6-bulk-1.1.0` |
| Date | 2026-08-23 |
| Status | Draft for COR review. Contains decisions marked **PENDING COR DECISION** that must be resolved before findings are issued. |

---

## 1. Purpose and scope

This methodology describes how Alliance Global Tech reviews the accuracy of
entity information in the TEFCA participant directory, how discrepancies between
that directory and authoritative federal sources are identified and evidenced,
and how a discrepancy becomes — or does not become — a reportable finding.

It covers the review procedure, the sources used, the controls that make each
result reproducible, and the points at which a decision belongs to the COR
rather than to the contractor.

It does not establish policy. Where a question requires a programme decision,
this document states the question, the options and the operational consequence
of each, and marks the item **PENDING COR DECISION**. No such item is resolved
by default in either direction.

## 2. Review population

The review population is the set of organisation records in the directory
delivery accepted into the review system.

For the current delivery: **23,566 records**.

Every rate in every report divides by delivered records. The system's internal
registry also contains records created for demonstration and records synthesised
to represent parent organisations referenced but not delivered; these are
excluded from the denominator, because including them would understate every
rate reported.

The delivery is retained unaltered. Its cryptographic hash is recorded on
receipt and re-verified before each reporting run, so any report can be tied to
the exact bytes reviewed.

## 3. Review unit and entity types

The review unit is one delivered organisation record.

The delivery classifies each record as **Participant** or **Subparticipant** and
identifies the Qualified Health Information Network (QHIN) that manages it.
Current delivery: 11,077 Participants, 12,489 Subparticipants, managed across 11
QHINs. QHINs are referenced by the delivery but are not themselves delivered as
records; the review system represents each referenced QHIN so the hierarchy has
a root, and marks those representations as derived.

A record is not the same thing as an organisation. 271 organisation names appear
more than once, and identifiers are shared in ways described in §7. Counts in
this methodology are counts of records unless the text says otherwise.

## 4. Authoritative sources

| Source | Publisher | What it establishes | Edition used |
| --- | --- | --- | --- |
| NPPES | CMS | Provider identity, organisation name, practice location, taxonomy | Full dissemination, 2026-08-09 |
| PPEF — Enrollment | CMS | Medicare enrolment | Q3 2026 (2026-07-17) |
| PPEF — Practice Location | CMS | Enrolled practice city/state/ZIP | Q3 2026 |
| PPEF — Reassignment | CMS | Benefit reassignment relationships | Q3 2026 |
| PPEF — Secondary Specialty | CMS | Additional enrolled specialties | Q3 2026 |
| PPEF — Additional NPIs | CMS | Further NPIs on one enrolment | Q3 2026 |
| OIG LEIE | HHS OIG | Exclusion from federal healthcare programmes | 2026-08-10 |
| CMS Revocation | CMS | Revoked Medicare billing privileges | Q2 2026 (2026-07-30) |
| SAM.gov | GSA | Federal registration and debarment | **Not evaluated — see §23** |

Each source is retained as a dated file with its cryptographic hash and record
count recorded. Reviews are run against the retained file rather than a live
query, so a review can be repeated months later and produce the same result. A
live query cannot offer that guarantee, because the source may have changed.

## 5. Source applicability

A source is consulted only where it can meaningfully answer. Applicability is
determined per record, before any lookup, and recorded with its reason:

- **REQUIRED** — the source is authoritative for this record and must answer.
- **APPLICABLE** — the source can answer and the answer is informative.
- **CONDITIONALLY APPLICABLE** — the source can answer only if a prior lookup
  supplies the key it needs.
- **NOT APPLICABLE** — the source cannot answer for this kind of record. Absence
  of a record in that source is expected and carries no meaning.
- **UNKNOWN, PENDING METHODOLOGY** — whether the source applies is itself an
  unresolved question.

This distinction is load-bearing. Medicare enrolment is keyed on NPI; for a
record delivered without an NPI there is no key, and no enrolment can be either
established or refuted. Reporting that as a missing enrolment would assert
something the evidence does not support.

Medicare relevance depends on provider taxonomy, which NPPES supplies. The
review therefore queries NPPES first and re-determines PPEF applicability once
NPPES has answered, rather than assuming relevance in advance.

## 6. Evidence collection procedure

For each record and each applicable source the review records one observation
stating what the source said, together with the identifier searched, the source
edition and file hash, the rule version applied, and a hash of the observation
itself.

Observation outcomes are deliberately qualified:

| Outcome | Meaning |
| --- | --- |
| MATCH OBSERVED | The source answered; exactly one record matched. |
| NO MATCH OBSERVED | The source answered; nothing matched. An informative negative. |
| MULTIPLE MATCHES | The source answered; more than one record matched. |
| AMBIGUOUS | Matched on supporting evidence only, with no decisive identifier. |
| SOURCE UNAVAILABLE | The source did not answer. A fact about access, not about the entity. |
| LOOKUP NOT APPLICABLE | The lookup does not apply to this record. |
| INSUFFICIENT IDENTIFIER | The record did not carry the key the lookup requires. |
| ERROR | The review system failed. A defect, not a source outage and not an entity finding. |

The last four are never reported as adverse. Conflating "we could not ask" with
"the answer was no" is the single most consequential error available in this
work, and the vocabulary is designed to make it impossible to make silently.

Current delivery: **188,528 observations**, every one carrying complete
provenance.

## 7. Identity verification

NPPES is the identity authority for a delivered NPI.

The delivery carries 18,673 distinct well-formed NPIs. Of these, **18,671
resolved in NPPES**; 2 did not, and 1 record carried two NPIs that both
resolved. Four cells did not contain a well-formed NPI: three were too short and
one contained two NPIs in a single field.

Identifier uniqueness in the delivery, established by measurement:

| Identifier | Behaviour |
| --- | --- |
| Organisation OID (`id`) | Unique — 23,566 of 23,566. The only safe business key. |
| TEFCAID | **Not unique.** 23,325 distinct; 43 shared across up to 69 records. |
| HCID | Not unique. 23,562 distinct. |

Counting entities by TEFCAID understates the population by 241. This
methodology counts by organisation OID.

## 8. Medicare enrolment verification

Where an NPI is present and taxonomy indicates Medicare relevance, the PPEF
enrolment extract is consulted.

Current delivery: 15,126 records matched a single Medicare enrolment; 2,433
matched more than one. Multiple enrolments are **normal**: PPEF is one-to-many
by design and a provider may legitimately hold several. Multiplicity is recorded
and is not an exception.

1,229 records with an applicable NPI returned no enrolment. That is an
informative negative, not a finding — many organisations legitimately hold no
Medicare enrolment.

## 9. Exclusion and revocation verification

**OIG LEIE.** Screening applies to every record that can be identified; the LEIE
is not limited to Medicare enrollees. A match on NPI is decisive. A match on
business name alone, with no NPI corroboration, is recorded as **AMBIGUOUS** and
referred for human adjudication — a name collision is not an exclusion, and
reporting it as one would be a false accusation produced by a string comparison.

Current delivery: 1 NPI match, 2 name-only matches.

**CMS Revocation.** 22 records matched the revocation extract.

None of these 25 observations is a finding. Each is an observation from an
authoritative source that requires analyst determination and QA approval before
it becomes anything at all (§14–15).

## 10. Address comparison

Delivered addresses are compared against authoritative addresses after
normalisation. Normalisation applies USPS-style street-suffix and directional
equivalences and removes case and punctuation differences.

**A formatting difference is not a discrepancy.** `123 Main St.` and `123 MAIN
STREET` are the same address written twice. Comparison is performed on
normalised values, and only a difference that survives normalisation is recorded
as a conflict.

Leading zeros are restored before ZIP comparison. Approximately 6.9% of
delivered ZIP codes lost a leading zero to a spreadsheet round-trip before
delivery; comparing `2718` to `02718` as text would manufacture a discrepancy
out of a formatting artefact.

Six outcomes are kept distinct: exact match, normalised match, conflict,
insufficient data, not applicable, source unavailable. **Insufficient data is
never counted as a conflict.**

Current delivery:

| | vs NPPES | vs PPEF |
| --- | --- | --- |
| Exact match | 7,070 | — |
| Normalised match | 3,299 | 14,807 |
| Conflict | 8,584 | 1,842 |
| Insufficient data | 23 | 6,917 |
| Source unavailable | 4,590 | 0 |

**Scope limitation.** The PPEF practice-location extract publishes enrolment
identifier, city, state and ZIP, and **no street line**. Agreement with PPEF is
therefore agreement on city, state and ZIP. It is not, and is never reported as,
complete street-address agreement. For the same reason PPEF cannot produce an
exact match.

Where a provider has several enrolled practice locations, the best result across
them is taken. An entity is not treated as disagreeing because its third
enrolled location differs.

**10,426 conflict observations correspond to 9,032 distinct records** — 1,394
records disagree with both sources and appear in both counts. The two numbers
are reported separately throughout.

**PENDING COR DECISION — D4_ADDRESS_MATERIALITY.** See §24. An observed address
conflict is not, in this draft, a compliance conclusion.

## 11. Organisation and relationship verification

The delivery expresses two relationships: the managing QHIN, and the parent
organisation.

Every record carries a parent pointer. 289 of the 300 distinct parent values
resolve to another delivered record; the remaining 11 are the QHIN identifiers,
which are referenced but never delivered. No record is its own parent. The
hierarchy is therefore internally consistent, and this was established by
measurement rather than assumed.

Medicare relationships are recorded from PPEF as traversals rather than
attributes, because each is one-to-many: enrolment, practice location, secondary
specialty, additional NPI, and benefit reassignment. Current delivery: 116,218
recorded traversals, each naming its component, its source edition and the
source row it came from.

## 12. Discrepancy identification

A discrepancy is a difference between the delivery and an authoritative source
that survives normalisation and is recorded with both values.

AGT operates an internal four-bucket triage classification (B1–B4) for
prioritising review work.

> **B1–B4 is Alliance Global Tech's internal operational classification.** No
> ONC, ASTP, RCE, Sequoia or other federal source establishes it as a federal
> taxonomy, and it is not presented as one. Where the COR requires a different
> classification, B1–B4 is replaced rather than mapped.

## 13. Exception handling

Not every observation warrants human attention. Observations are triaged by
deterministic rules into five dispositions:

| Disposition | Meaning | Current delivery |
| --- | --- | --- |
| Ready for analyst | Something adverse or ambiguous was observed | **28** |
| Methodology pending | Review requirement depends on an unresolved decision | 33,992 |
| Informational only | Recorded, real and expected | 154,499 |
| Source limitation | The limit is in our key or our access | 9 |
| Duplicate / consolidated | Already represented by another item | 0 |

Triage assigns work. It never assigns an answer.

## 14. Analyst review

An analyst opens a work item, examines the evidence and the sources cited, and
records a determination with a written rationale. A determination without a
rationale cannot be recorded.

Determinations are events, not fields. A revised determination is a new event
that references the one it supersedes; the superseded event retains its own
author, timestamp and rationale permanently. There is no facility to edit or
overwrite a determination.

## 15. Quality assurance review

Every analyst determination is reviewed by a QA lead, who records **APPROVE**,
**RETURN** or **ESCALATE**, with a reason.

Only APPROVE makes a determination reportable. A subsequent RETURN or ESCALATE
withdraws reportability: the determination is back in play and must not be cited
as settled. Where QA returns a determination and the analyst issues a new one,
that new determination requires fresh QA approval.

## 16. Segregation of duties

The person who made a determination may not perform its QA review. The system
refuses the attempt.

An exception requires an explicit grant from a different, more senior individual
together with a written reason; both are recorded permanently on the event and
are counted in reconciliation. The role recorded on each event is the role held
when the decision was made, so a later change of role cannot alter what a past
decision was authorised by.

## 17. Priority review process

A priority review is initiated when ONC flags a specific entity. It runs the
same evidence procedure as the population review, against the same retained
source editions, and produces a per-case report containing the request, the
entity and its identifiers, the evidence observed, the analyst assessment, the
QA decision, the disposition, the limitations, and the elapsed turnaround.

A due date is assigned to each review. A review is `at_risk` when two or fewer
days remain and `overdue` once the due moment has passed.

> Monthly priority-review volume and surge thresholds are not stated in the
> source material available to this draft and are therefore not asserted here.

## 18. Ongoing review process

Ongoing reviews process each new delivery against the preceding one, reporting
added, changed and (where applicable) removed records, and running the same
evidence procedure over the additions and changes.

Each ongoing review records both delivery identifiers and hashes, the source
editions used, and the methodology version, so any cycle can be reconstructed.

> Only one delivery has been received. The delta procedure is defined and has
> not yet been exercised against a second delivery.

## 19. Retrospective review process

The retrospective review covers the initial period and reports population
characteristics, evidence coverage, observations by source, exceptions, source
limitations, methodology-pending items, and the subset that received human
review.

**Population observations and human determinations are reported separately and
are never combined into a single total.** 23,566 records received automated
evidence collection. The number that received human review is separately stated
and is currently zero.

## 20. Sampling and statistical approach

Where a contractual deliverable requires a sample rather than the full
population, sample size is computed by the Cochran formula with a recorded
confidence level and margin of error. Sample membership is persisted so a drawn
sample can be re-examined and the same records retrieved.

The current review is a **full-population** evidence collection, not a sample.
No confidence interval is quoted, because none applies to a census.

## 21. Evidence retention and reproducibility

Reproducibility rests on four controls:

1. The delivery is retained unaltered with its hash and re-verified before use.
2. Each authoritative source is retained as a dated file with its own hash and
   record count.
3. Every observation records the source edition, the identifier searched, the
   rule version and a hash of itself.
4. Evidence is append-only. A correction is issued as a new evidence version;
   the superseded version remains queryable and is never rewritten.

A review can therefore be repeated and will produce the same result, and any
figure in any report can be traced to the source row that produced it.

Retention period is **PENDING COR DECISION (D8)**.

## 22. Data-quality controls

Data-quality rules run over the delivery and record issues without altering the
delivered values. Current delivery: 36,916 issues across 20 types — 35,147
informational, 1,631 low, 134 medium, 4 high.

Corrections are applied only in the curated layer, never to the delivered
record, and only where the transformation is deterministic and non-substantive
(for example restoring a ZIP leading zero). Every correction records the
original value, the reason and the authority. Substantive changes require a
human.

Observed conditions of note: 1,627 delivered ZIP codes had lost a leading zero
before delivery; one NPI field contained two NPIs; nine records carry
test-styled names.

## 23. Source limitations

| Limitation | Effect | Treatment |
| --- | --- | --- |
| **SAM.gov not evaluated** — no credential is configured | Federal registration and debarment status is unknown for all 23,566 records | Recorded as SOURCE UNAVAILABLE. Never reported as an adverse result. |
| **PPEF publishes no street line** | Street-level agreement with PPEF cannot be assessed | Comparison scoped to city/state/ZIP and labelled as such |
| **PPEF publishes no payment-suspension field** | Payment suspension cannot be reported | Reported as not available, never as a clean value |
| **No source row identifiers in PPEF** | Source rows cannot be cited by publisher-issued id | Cited by a deterministic key derived from row content |
| **USPS Address API not exercised** | Addresses are not independently validated against USPS | Disclosed; comparison is source-to-source only |
| **Delivered ZIP leading zeros lost upstream** | Affects 6.9% of records | Restored before comparison; original preserved |

## 24. Methodology-pending decisions

The following require a COR decision. Each is recorded in the decision register
accompanying this methodology. None is resolved by default.

**D4_ADDRESS_MATERIALITY — the most consequential.** The delivery supplies a
*registered* address. NPPES and PPEF publish *practice locations*. These are
different kinds of address, and a difference between them may be entirely
proper. No approved rule establishes when such a difference is material.

10,426 conflict observations, across 9,032 records, are therefore held as
methodology-pending. They are **observed address conflicts**. In this draft they
are not described as failed, non-compliant, invalid, inaccurate, unverified, or
ARC failures, because no approved methodology supports any of those words.

Treating every difference as material would set the threshold at "any difference
at all". Treating none as material would set it at "never". Both are programme
decisions, and the contractor should not make either silently.

**D4 (source unavailable)** — how SAM's unavailability is to be treated for all
23,566 records.

D1, D2, D3, D5, D6, D7, D8 and D9 are set out in the decision register.

## 25. Reporting

Reports are produced from the current approved evidence version only. Superseded
evidence remains queryable but is never combined with current evidence in a
report.

Every reported figure carries its denominator, the evidence version, the source
scope and the calculation used. Observation counts and record counts are
reported as separate fields and are never interchanged.

Formats: HTML, PDF and structured export (CSV/Excel-compatible). Reports carry a
report identifier, version, generation timestamp, methodology version and
evidence version. Presentation follows Section 508 requirements: heading
hierarchy, meaningful table headers, text equivalents, and no meaning conveyed by
colour alone.

Five gates stand between a generated report and a COR deliverable: evidence
version, human QA, methodology, dataset contractual provenance, and report QA.
A report may be produced internally with gates closed, but it is then watermarked
**DRAFT — NOT FOR COR RELEASE**. Gates are not bypassed.

## 26. COR decision points

1. Approve or amend this methodology (Task 2, Deliverable 2).
2. Resolve **D4_ADDRESS_MATERIALITY**. Until then no address conclusion is
   available, for 9,032 records.
3. Resolve **D4 (source unavailable)** and direct whether SAM.gov access should
   be obtained.
4. Resolve D1, D2, D3, D5, D6, D7.
5. Confirm records retention (D8) and official deliverable format (D9).
6. Confirm whether B1–B4 is acceptable as an internal operational
   classification, or specify a required alternative.
7. Provide the dataset transmittal record and control total required to open the
   contractual provenance gate. **No COR-facing finding can be released until
   this is resolved.**

---

*Prepared by Alliance Global Tech. Figures are drawn from evidence version
`phase6-bulk-1.1.0` and are reproducible from retained source artefacts.*
