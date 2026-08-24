# TEFCA ARC — COR Decision Register

**DRAFT — NOT FOR COR RELEASE**

| | |
| --- | --- |
| Contract | 7571MN26F80064 (HHS/ONC ASTP) |
| Prepared by | Alliance Global Tech, Inc. |
| Register version | 1.0 |
| Methodology affected | `arc-methodology-0.1` |
| Evidence version | `phase6-bulk-1.1.0` |
| Date | 2026-08-23 |

**No COR decision has been recorded on any item in this register.** Every
`COR DECISION` field reads `PENDING COR DECISION`, because none has occurred.
Where AGT has a recommendation the source material supports, it is stated; where
it does not, the entry says *program guidance requested* rather than supplying
one.

Source: `docs/cor_decision_brief_v2.md` (D1–D9), extended by
`D4_ADDRESS_MATERIALITY`, which arose during full-population address comparison
after that brief was written.

> **Naming hazard, flagged deliberately.** `D4` and `D4_ADDRESS_MATERIALITY` are
> **different questions** that share a prefix only because of how the second was
> introduced in the evidence records. D4 concerns an unavailable source;
> `D4_ADDRESS_MATERIALITY` concerns whether an address difference is material.
> They should be renumbered before this register is issued.

---

## Summary

| ID | Question | Population affected | Status |
| --- | --- | --- | --- |
| D1 | Uncorroborated NPI — how classified? | 2 records observed; latent for the rest | **PENDING COR DECISION** |
| D2 | No B1–B4 rule matches — what result? | 0 currently; path reachable, outcome undefined | **PENDING COR DECISION** |
| D3 | B3 — Reviewer or Senior Analyst tier? | Determines analyst staffing | **PENDING COR DECISION** |
| D4 | Source unavailable — classification or readiness? | **23,566 records** (SAM.gov) | **PENDING COR DECISION** |
| **D4_ADDRESS_MATERIALITY** | Which address differences are material? | **9,032 records / 10,426 observations** | **PENDING COR DECISION** |
| D5 | Which name differences are reportable? | Latent across the population | **PENDING COR DECISION** |
| D6 | "Flagged" vs "invalid" identifier | 4 malformed NPI cells observed | **PENDING COR DECISION** |
| D7 | Potential exclusion match — automated finding? | 2 name-only matches; 1 NPI match | **PENDING COR DECISION** |
| D8 | Records retention period | All evidence | **PENDING COR DECISION** |
| D9 | Official deliverable format | All deliverables | **PENDING COR DECISION** |

---

## D4_ADDRESS_MATERIALITY — address difference materiality

**Question.** Which differences between a delivered organisation address and an
authoritative address constitute a reportable discrepancy?

**Why the decision is required.** The delivery supplies a *registered* address.
NPPES and PPEF publish *practice locations*. These are different kinds of
address and a difference between them may be entirely proper — an organisation
registered at its corporate office and practising at a clinic is not in error.
No approved methodology establishes when such a difference is material.

**Observed, after normalisation** (formatting differences already excluded):

| | vs NPPES | vs PPEF | Distinct records |
| --- | --- | --- | --- |
| Conflict | 8,584 | 1,842 | **9,032** (1,394 conflict with both) |
| Normalised match | 3,299 | 14,807 | |
| Exact match | 7,070 | — | |
| Insufficient data | 23 | 6,917 | |
| Source unavailable | 4,590 | 0 | |

Conflicting fields (from persisted evidence): street line 8,330 (NPPES only — PPEF
publishes none) · city 3,185 · ZIP 1,396 · state 341.

**Alternatives.**

| | Option | Operational impact |
| --- | --- | --- |
| A | Any surviving difference is a discrepancy | 9,032 records to human review — approximately 38% of the population |
| B | Only state or ZIP differences are material; street differences are informational | ~1,750 records to review; treats a different street as expected between registered and practice addresses |
| C | Address is corroborative only and never produces a finding on its own | No address findings; address evidence supports other determinations |
| D | Materiality differs by entity type | Requires a rule per type |

**AGT recommendation.** *Program guidance requested.* AGT does not recommend an
option, because the choice sets a compliance threshold and the methodology
available to AGT does not address it. AGT does note that street-line differences
are 80% of the conflicting fields and are the category most likely to reflect a legitimate
registered-vs-practice distinction rather than an error.

**Current treatment pending decision.** All 10,426 observations are recorded as
*observed address conflicts* and held as methodology-pending. They are not
described as failed, non-compliant, invalid, inaccurate, unverified, or ARC
failures, and no address finding is produced.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —
**Methodology version affected:** `arc-methodology-0.1` §10, §24

---

## D4 — source unavailable

**Question.** When an applicable authoritative source is temporarily
unavailable, does that affect classification, or only review readiness?

**Why required.** A source that did not answer has said nothing about the
entity. Whether an unanswered source changes the classification, or only whether
the review can close, is undecided.

**Observed.** SAM.gov returned no result for **all 23,566 records** — no API
credential is configured. OIG LEIE and CMS Revocation continued to answer. This
is an access condition, not a source outage, and is recorded as such.

**Alternatives.** A: proceed with a disclosed evidence gap. B: hold affected
entities open until the source responds. C: a distinct incomplete-evidence state.

**Operational impact.** A allows deliverables to proceed with disclosure. B and C
hold the entire population open, since the gap affects every record.

**AGT recommendation.** *Program guidance requested.* AGT additionally asks
whether the programme wishes SAM.gov access to be obtained, which is a
prerequisite to any option other than A.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D1 — uncorroborated NPI

**Question.** Where an NPI is expected and supplied but cannot be corroborated in
NPPES, how should the result be classified?

**Why required.** CMS requires HIPAA-covered providers to obtain an NPI, but
NPPES states that issuance does not validate licensing or credentialing. Failure
to corroborate is an identity observation; whether it affects classification is
undecided. Applies only where an NPI was expected and supplied — not where none
was supplied, which is legitimate for payers, public health agencies and health
information networks.

**Observed.** 2 records supplied a well-formed NPI that NPPES did not resolve;
1 record carried two NPIs. 19.45% of the delivered population carries no NPI at
all, which is a different condition.

**AGT recommendation (related).** "No NPI supplied" and "NPI supplied, not
corroborated" should be distinguishable downstream regardless of how D1 is
answered. The current evidence vocabulary already separates them
(`INSUFFICIENT_IDENTIFIER` vs `NO_MATCH_OBSERVED`).

**Alternatives.** A: route to human review. B: produce a reportable finding. C: a
rule per condition.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D2 — no classification rule matches

**Question.** When evidence gathering completes but no B1–B4 rule matches, what
should be recorded?

**Why required.** Every applicable source answered; the rules do not describe the
combination observed. Something must be recorded and the available answers differ
materially.

**Constraint on all options.** "No match was observed" is a distinct evidence
state from "the source could not be reached", and **neither is evidence of
compliance**.

**Alternatives.** A: default to B1. B: route to review. C: a category outside
B1–B4.

**AGT recommendation.** *Program guidance requested.* Option A would treat the
absence of a triggered discrepancy rule as sufficient for B1; the methodology
available to AGT does not establish that this inference is intended.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D3 — B3 review tier

**Question.** Should B3 (Inexplicable) route to Tier 2 (Reviewer) or Tier 3
(Senior Analyst)?

**Why required.** The approved methodology requires three-tier routing and a
Tier-3 queue restricted to senior analysts (FR-T3-018, FR-T3-020) but does not
state which bucket routes to which tier.

**Operational impact.** Determines who performs the work and at what privilege
level. **The review queues cannot be placed in service until this is answered.**

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D5 — name discrepancy severity

**Question.** Which differences between a delivered organisation name and the
NPPES legal name constitute a reportable discrepancy?

**Why required.** Organisations routinely trade under a name differing from their
registered legal name. Treating every difference as a discrepancy generates
findings against entities that have done nothing wrong; treating none as a
discrepancy misses genuine identity questions.

**Observed.** Name comparison was not performed on the delivered population in
the current evidence version — only exact-match name screening against the OIG
exclusion list, which produced 2 AMBIGUOUS results. A population name comparison
requires this decision first, so that the comparison records the right thing.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D6 — identifier quality states

**Question.** When is an identifier "flagged" rather than "invalid"?

**Observed.** 4 NPI cells are not a bare 10-digit value: two 9-digit, one
6-digit, and one containing two NPIs separated by a comma. The multi-valued cell
is the significant case — NPI is the join key to NPPES, PECOS, OIG and CMS
Revocation, and a cell holding two identifiers cannot be resolved to one without
a rule.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D7 — exclusion evidence and classification

**Question.** May a potential exclusion match become an automated finding?

**Why required.** This gates whether the system can assert non-compliance without
a human.

**Observed.** 1 record matched the OIG LEIE on NPI. 2 records matched on business
name with no NPI corroboration. Under the current methodology all three are
observations requiring human adjudication; the name-only matches are recorded as
AMBIGUOUS and explicitly not as exclusions.

**AGT position.** AGT does not recommend automated exclusion findings. An
exclusion assertion against a named organisation is consequential and a name
collision is a realistic failure mode.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D8 — records retention

**Question.** How long must review records, evidence and source artefacts be
retained?

**Operational impact.** Retained source artefacts currently total approximately
1.7 GB per review cycle. Retention drives storage planning and the transition
package at closeout (FR-T6-006).

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## D9 — official deliverable format

**Question.** Which format constitutes the official deliverable of record?

**Operational impact.** HTML and structured export are verified. PDF generation
depends on native rendering libraries present in the deployment container and
absent on developer workstations; if PDF is the format of record, generation must
be pinned to the container.

**COR DECISION:** `PENDING COR DECISION` · **Date:** —

---

## Decisions AGT has NOT made

For completeness, the following were available to decide by default and were
deliberately left open:

- No address conflict was classified as a compliance failure.
- No exclusion or revocation match was converted into a finding.
- No entity was classified B1–B4 on the delivered population.
- No determination was recorded as reportable; `reportable_at` is NULL on all 43
  existing review records.
- No SAM.gov result was inferred from its absence.
