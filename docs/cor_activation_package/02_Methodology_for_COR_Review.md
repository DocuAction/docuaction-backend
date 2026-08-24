# Methodology for COR Review

**TEFCA ARC · Contract 7571MN26F80064 · Alliance Global Tech, Inc.**
Prepared for COR review · 2026-08-24

---

## The methodology document

The full methodology, written in program language, is:

**`../deliverables/TEFCA_ARC_Methodology_for_COR.md`**

It covers, in order: what AGT receives; what AGT checks; which authoritative
sources are consulted and what each does and does not establish; how evidence is
preserved; how discrepancies are identified and categorised; how analysts
review; how independent QA works; how reports are produced; how priority reviews
work; how unresolved methodology questions are handled; what AGT will not do;
and what AGT needs in order to begin.

It contains no database names, class names, migration references or internal
engineering vocabulary. It is written to be read by the COR, not by an engineer.

This cover sheet adds the two statements the COR should be able to quote back,
and the traceability position.

---

## The reportability statement

> **System observations and automated indicators are not Government findings.**
>
> The platform records what each authoritative source said about each entity.
> That record is evidence, not a conclusion. No result becomes reportable to the
> Government until:
>
> 1. a named analyst has recorded a determination with a written rationale; and
> 2. a **different** named reviewer has approved that determination through
>    quality assurance; and
> 3. that approval still stands — a later return or escalation withdraws it; and
> 4. the applicable methodology settles the question, or the item is reported as
>    awaiting a methodology decision rather than resolved.
>
> An approval is not permanent, and the system holds nothing that can bypass
> this sequence.

This is the sentence AGT would want quoted if a reported figure is ever
questioned.

---

## Source limitations — the four states, and why they stay separate

The most consequential thing the methodology does is refuse to collapse four
different situations into one.

| State | What it means | What it does **not** mean |
| --- | --- | --- |
| **Source unavailable** | An applicable source could not be reached or is not accessible to AGT | It does **not** mean the source found nothing. Nothing was asked. |
| **Not applicable** | The question is meaningless for this entity — for example a Medicare enrolment check for an entity that does not bill Medicare | It is **not** a finding against the entity |
| **Insufficient evidence** | The check could be attempted but the available information does not support a conclusion either way | It is **not** a pass, and **not** a failure |
| **Methodology pending** | The comparison produced a factual result, and the methodology does not yet settle what that result means | It is **not** "no problem", and **not** "problem" |

**How false adverse findings are prevented.** Each of the four is recorded as
itself. None can become a discrepancy category on its own. Reports state the
count in each state, so a reader sees the limits of the review alongside its
results — a review with an unreachable source is reported as a review with an
unreachable source.

**Two current examples.** SAM.gov cannot be queried because AGT holds no
credential, so it is *source unavailable* across the whole population.
Confirming that a taxpayer identification number belongs to a named organisation
requires IRS authority AGT does not hold and will not acquire under this
contract; those checks remain explicitly unresolved and pending Government
verification. **Neither is reported as anything adverse about any entity.**

---

## Traceability

Any figure in any report can be reconstructed, without regenerating it, to:

| Level | What can be shown |
| --- | --- |
| **Entity** | Which entities the figure counted |
| **Source** | Which authoritative sources were consulted for each |
| **Source version** | Which edition of each source answered, and when |
| **Source record** | The specific record the answer came from |
| **Observation** | What was compared and what the comparison found |
| **Analyst action** | Who determined what, when, and on what written rationale |
| **QA action** | Who approved, returned or escalated, when, and why |
| **Report version** | Which issued version of the report carried the figure |

The delivered population is preserved exactly as received with a recorded
integrity value, so a report issued months later can state precisely which
population it covered and prove the file has not changed.

Reports are never regenerated in place. A report issued in one week continues to
say what its recipient received, even after the underlying data moves on, and a
regenerated document cannot be presented as the original.

---

## Verification process at a glance

```
   Government-authorised population
                 |
        preserved unaltered, integrity value recorded
                 |
        applicability decided per entity, per source
                 |
        authoritative source verification
                 |
        observations recorded with source edition and date
                 |
        discrepancy identification
                 |
        +--------+--------+
        |                 |
   settled by      awaiting a methodology
   methodology         decision -> counted and
        |              disclosed, not categorised
        |
   ANALYST DETERMINATION  (named person, written rationale)
        |
   INDEPENDENT QA  (different named person)
        |
   approve --> reportable      return / escalate --> not reportable
        |
   COR deliverable
```

Every step above the analyst line is automated and produces evidence. Nothing
above that line produces a finding.

---

## What the COR is asked to confirm

1. That the verification workflow matches the Government's expectation.
2. That the reportability statement is the standard the Government expects.
3. That the treatment of the four source-limitation states is acceptable.
4. That the traceability available is sufficient for audit purposes.

Points requiring a decision rather than confirmation are in **04 — Methodology
Decisions Requested**.
