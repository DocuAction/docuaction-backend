# Official Finding Release Gate

**Governance document.** Not a new architecture — it names, in one place, the
conditions under which AGT may represent a review result as an **official finding
delivered to the Government**.

**Contract 7571MN26F80064** · Version 1.0 · 2026-08-23

---

## The rule

> No result may be represented to the COR as an official finding unless **every**
> condition below is satisfied. A single unsatisfied condition means the output
> is internal, is watermarked as a draft, and is not delivered as a finding.

## Conditions

| # | Condition | Satisfied when | Status today |
| --- | --- | --- | --- |
| 1 | **Authoritative input lineage** | The reviewed data is COR-provided entity data, received through the agreed channel, with its receipt and integrity recorded | **NOT MET** — COR entity data not yet delivered |
| 2 | **Approved methodology** | D2 has written COR acceptance | **NOT MET** — submitted 9 Jul, resubmitted 27 Jul, awaiting written acceptance |
| 3 | **Government assignment** | The assignment authorising Task 3 review has been issued | **NOT MET** |
| 4 | **Applicable source checks complete** | Every applicable source has answered, or its non-answer is recorded and disclosed | Capability ready |
| 5 | **Source limitations disclosed** | Every limitation affecting the result appears in the report | Capability ready |
| 6 | **Analyst determination** | A named analyst has recorded a determination with a written rationale | **NOT MET** — 0 determinations |
| 7 | **Independent QA approval** | A different named reviewer has recorded an approval that still stands | **NOT MET** — 0 QA decisions |
| 8 | **Sampling satisfied** | Where the deliverable is sampled, the sample was drawn from the approved frame and its parameters are recorded | **NOT MET** — sample not drawn |
| 9 | **COR decisions resolved** | Decisions that change how a result is classified have been answered | **NOT MET** — 4 open |
| 10 | **Report generated from canonical evidence** | Every figure comes from the current approved evidence, through the standard reporting path | Capability ready |
| 11 | **Reconstruction successful** | Every reported figure traces to the source record that produced it | Capability ready |
| 12 | **Security and privacy conditions** | CUI marking applied; 508 conformance demonstrated for the deliverable format | Partially ready |

## Current position

**No condition set is satisfied, and no official finding may be issued.** Six of
the twelve are outside AGT's control: the Government assignment, the entity data,
written D2 acceptance, and the four methodology decisions.

This is the expected position for a contract awaiting its data delivery. It is
not a defect in the platform, which is certified and idle.

## Who may declare the gate open

The Program Manager, on written confirmation from the Technical Lead that
conditions 4, 5, 10, 11 and 12 are met, and on documentary evidence for
conditions 1, 2, 3, 6, 7, 8 and 9. **No automated process may open this gate.**

## What a closed gate does not prevent

Internal review, quality control, platform validation, and reporting to AGT
management. Those continue. What the gate controls is the single act of telling
the Government that something is a finding.
