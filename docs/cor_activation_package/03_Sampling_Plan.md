# Sampling Plan

**TEFCA ARC · Contract 7571MN26F80064 · Alliance Global Tech, Inc.**
Prepared for COR review · 2026-08-24

---

## How to read this document

| Label | Meaning |
| --- | --- |
| **CONTRACT REQUIREMENT** | Stated in the solicitation. Not negotiable, and cited. |
| **AGT RECOMMENDATION** | AGT's proposal. Binding on AGT once confirmed; not a requirement before then. |
| **PROGRAM GUIDANCE REQUESTED** | Genuinely open. AGT will not choose these unilaterally. |

No sample has been drawn. This document describes the method AGT proposes to
apply once the population is received and the parameters are confirmed.

---

## 1. What the contract requires

> **CONTRACT REQUIREMENT.** For Task 3, AGT shall review a statistically
> representative sample, **at or above a 95% confidence level**, of Participants
> and Subparticipants **from each QHIN**. The sample size is determined by the
> confidence level.
>
> The same standard applies to Task 4, against new submissions.
>
> AGT shall submit the sampling methodology and confidence interval calculations
> as part of the Task 2 protocol.

Two elements of this are fixed and AGT treats them as such: **95% is a floor,
not a target**, and the sample must be representative **of each QHIN
individually**, not of the population as a whole.

---

## 2. Population definition

> **PROGRAM GUIDANCE REQUESTED — S1.** AGT requests written confirmation of the
> population of record and its effective date.

The sampling frame is the authorised entity population provided by the COR. AGT
proposes the following definition and asks the COR to confirm it:

| Element | AGT proposal |
| --- | --- |
| **Unit of analysis** | One Participant or Subparticipant. Per the solicitation Q&A (Q45), one priority review equals one individual Participant or Subparticipant, and AGT proposes the same unit throughout for consistency. |
| **Population of record** | The entity file as delivered by the COR, fixed at the moment of receipt and identified by a version label and an integrity value. |
| **Reference figure** | The solicitation Q&A cites **94,231 unique connections** as the total available to sample from. AGT asks whether this figure, or the count in the delivered file, is the population of record. |
| **Effective date** | The date of the delivered file, not the date of the draw. |

**Why this needs confirming.** A "connection" and an "entity" are not
necessarily the same unit. If one organisation participates through more than
one QHIN, it may appear as several connections. Whether the sample is drawn from
connections or from distinct organisations changes both the frame and what a
finding means, and it is not a question AGT should settle alone.

---

## 3. Strata

> **CONTRACT REQUIREMENT.** Representative of each QHIN.

**AGT RECOMMENDATION.** Stratify by QHIN, with each QHIN forming one stratum.
This follows directly from the requirement and needs no further justification.

**AGT RECOMMENDATION — allocation.** Proportional allocation across strata, with
a minimum per stratum so that a small QHIN is not represented by a sample too
small to say anything about. AGT will propose the stratum allocation **within
three business days of receiving the data**, and share it with the COR **before
any review begins**.

> **PROGRAM GUIDANCE REQUESTED — S2.** Is a minimum per-QHIN sample size
> required, and if so what is it? Proportional allocation alone can leave a
> small QHIN with a handful of entities.

> **PROGRAM GUIDANCE REQUESTED — S3.** Should any secondary stratification
> apply — for example Participant versus Subparticipant, or geography? AGT does
> not recommend adding strata without a stated analytic purpose, because each
> additional stratum increases the sample needed to say anything about it.

---

## 4. Sample frame and size

**CONTRACT REQUIREMENT.** ≥95% confidence; size determined by the confidence
level.

**AGT RECOMMENDATION.** Cochran's formula with a finite population correction:

| Parameter | AGT proposal | Status |
| --- | --- | --- |
| Confidence level | **95%** | Contract floor |
| Margin of error | **±5%** | **AGT recommendation** |
| Assumed proportion | 0.5 (most conservative, maximises the required size) | AGT recommendation |
| Population | 94,231 | To be confirmed — see S1 |
| Finite population correction | Applied | AGT recommendation |
| **Resulting sample size** | **383 entities** | Follows from the above |

> **PROGRAM GUIDANCE REQUESTED — S4.** Confirmation of the ±5% margin.
>
> The contract fixes the confidence level and is silent on the margin. The
> margin is what actually determines the workload: at 95% confidence, ±5% gives
> 383 entities and ±3% gives approximately 1,060. AGT recommends ±5% as
> proportionate to the base-year period, and will apply whatever the COR
> confirms.

---

## 5. Reproducible random selection

**AGT RECOMMENDATION.** Selection is pseudo-random and reproducible. AGT records:

- the population file version and its integrity value;
- the stratum boundaries and the allocation applied;
- the random seed;
- the selection algorithm and its version;
- the resulting entity list, fixed at the moment of draw.

Anyone holding the population file, the seed and the recorded parameters can
re-run the selection and obtain the identical sample. This is what allows the
Government to audit the selection rather than take it on trust.

**The seed is recorded before the draw, not chosen after it.** A seed selected
once results are known is not a seed; it is a choice of results.

---

## 6. Replacement handling

> **PROGRAM GUIDANCE REQUESTED — S5.** How should a selected entity that cannot
> be reviewed be handled?

An entity may be unreviewable for reasons that have nothing to do with its
compliance — a duplicate record, an entity withdrawn between the file date and
the review, or a record carrying no usable identifier.

AGT sets out the options rather than choosing:

| Option | Effect |
| --- | --- |
| **Replace** from the same stratum, using the next entry in the seeded order | Preserves the achieved sample size and the confidence level. Requires that replacements are recorded and disclosed. |
| **Do not replace**; report the entity as unreviewable with its reason | Preserves the integrity of the original draw. Slightly reduces achieved precision. |

**AGT RECOMMENDATION.** Replace from the same stratum in seeded order, and
disclose every replacement with its reason in the report. This holds the
contractual confidence level while keeping the substitution visible.

---

## 7. Exclusions

> **PROGRAM GUIDANCE REQUESTED — S6.** Are any entities excluded from the frame
> before the draw?

AGT proposes **no exclusions** unless the COR directs otherwise. Excluding
records before selection changes what the sample represents, and any exclusion
rule should be a Government decision recorded in the methodology.

Records that appear duplicated will be **identified and reported**, not silently
removed. Whether duplicates are consolidated before the draw is part of S6.

---

## 8. QHIN representation

Each report states, per QHIN:

- the population in that stratum;
- the number sampled;
- the number reviewed;
- the number unreviewable, with reasons;
- results across the four Government discrepancy categories;
- any source limitation affecting that stratum.

A QHIN whose stratum could not be fully reviewed is reported as such. A gap in
coverage is disclosed, never averaged away.

---

## 9. Documentation and reproducibility

Each sampling event produces a record containing the population file version and
integrity value, the parameters applied, the seed, the algorithm version, the
stratum allocation, the selected entities, any replacements with reasons, the
date, and the person who authorised the draw.

That record accompanies the deliverable, so a reader can confirm that the sample
reported is the sample drawn.

---

## 10. Census screening — what it is and what it is not

During development, AGT exercised its verification pipeline against **all**
records in a development dataset rather than a sample. This is described here to
prevent a misunderstanding, not to claim credit for it.

> **A census of development data does not satisfy the contractual sampling
> requirement, and AGT does not represent it as doing so.**

The contractual requirement is a statistically representative sample, drawn per
QHIN, from the **authorised Government population**. That population has not been
received and no sample has been drawn.

Once operations begin, AGT expects to screen the full authorised population
where capacity allows, **in addition to** the contractual sample. Screening
provides broader coverage; the sample provides the statistical basis. If AGT
proposes to rely on census screening for any contractual conclusion, it will say
so explicitly and seek the COR's agreement first.

---

## 11. Summary of confirmations requested

| ID | Question | AGT recommendation |
| --- | --- | --- |
| **S1** | What is the population of record — connections or distinct entities — and its effective date? | The delivered file, fixed at receipt |
| **S2** | Is a minimum per-QHIN sample size required? | Yes; AGT will propose a floor with the allocation |
| **S3** | Any secondary stratification beyond QHIN? | No, absent a stated analytic purpose |
| **S4** | Confirm the ±5% margin of error | ±5%, giving 383 entities |
| **S5** | Replacement policy for unreviewable entities | Replace within stratum, disclose every substitution |
| **S6** | Any exclusions from the frame, and duplicate handling | No exclusions; duplicates identified and reported |

AGT will apply the confirmed parameters and record them in the methodology
version applied to each deliverable.
