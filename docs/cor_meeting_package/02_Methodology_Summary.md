# Methodology Summary

**Supports D2 (submitted 9 July 2026, awaiting written COR acceptance)**

## What AGT reviews

Whether QHIN submissions of Participants and Sub-participants to the RCE
Directory Service are accurate and appropriately documented. For each entity:

| Check | Why it matters | Source |
| --- | --- | --- |
| Identity accuracy — name, NPI, address | Correct identification in the TEFCA network | NPPES |
| Exclusion and debarment | Excluded entities should not participate in federally connected networks | OIG LEIE, SAM.gov |
| Enrolment and payment status | Suspended or lapsed enrolment may indicate a compliance issue | PECOS / PPEF |

All verification sources are **publicly available**. AGT does not use
government-provided data for source validation; COR-provided entity data is the
subject of review, not a corroborating source.

## How a review reaches a conclusion

```
COR entity data
  → preserved unaltered, hash recorded
  → data-quality review (delivered values never altered)
  → identity and applicability determined per entity
  → authoritative source checks
  → observations recorded with full provenance
  → exceptions identified
  → ANALYST determination, with written rationale
  → INDEPENDENT QA: approve, return or escalate
  → reportable finding
```

**Automation produces evidence. A finding requires a named analyst and a
different named QA reviewer.** Software never issues a Government determination.

## Controls that make a review defensible

- Source files retained with SHA-256; reviews run against retained editions, so
  a result can be reproduced months later.
- Every observation records the identifier searched, the source edition and its
  hash, and the rule version applied.
- Determinations are append-only. A revision supersedes and never overwrites.
- A reviewer cannot approve their own determination; the system refuses.
- A source that did not answer is recorded as unavailable and never as an
  adverse result.

## Governing documents

Common Agreement V2.1 · QHIN Technical Framework V2.1 · Participant/Sub-participant
Terms of Participation V1.0 · ONC SOPs — **to be provided by the COR**; AGT will
align on receipt.
