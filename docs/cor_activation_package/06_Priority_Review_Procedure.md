# Priority Review Procedure

**TEFCA ARC · Task 5 · Contract 7571MN26F80064 · Alliance Global Tech, Inc.**
Prepared for COR review · 2026-08-24

---

## What the contract establishes

> AGT shall conduct priority reviews based on issues identified by the COR, with
> an anticipated average of **twenty reviews per month**, and shall maintain the
> capability to perform additional reviews beyond that average — including the
> ability to respond **within the agreed upon deadline**. The deadline, and the
> Participants or Subparticipants identified for review, **will be communicated
> by the COR**.

Two consequences follow, and AGT has built to both.

**There is no fixed turnaround.** The contract sets the deadline per request.
AGT has therefore adopted **no standing internal service level**, and does not
report performance against one. Elapsed time is measured against the deadline
the COR set for that specific request. Any figure describing turnaround states
which deadline it was measured against.

**Urgency changes the sequence, not the standard.** A priority review passes
through the same verification, analyst determination and independent QA as any
other review. Nothing is abbreviated because a deadline is short. If a deadline
cannot be met without abbreviating the standard, AGT will say so when the
request is received rather than deliver a weakened review on time.

---

## The procedure

### 1 — Request received

The COR identifies the entities and states the deadline.

AGT logs, on receipt: the request identifier, the date and time received, the
entities named, the deadline stated by the COR, the issue or concern described,
and who made the request.

**Acknowledgement is sent to the COR within one business day**, confirming what
AGT understood and the deadline recorded. If the deadline is not stated, AGT
asks rather than assumes.

> **CONFIRMATION REQUESTED — P1.** The preferred request channel, and whether
> the COR wishes acknowledgement in a different form or timeframe.

### 2 — Assignment logged

An analyst is assigned and the assignment time recorded. A QA reviewer is
identified at the same time and **must be a different person** — priority does
not suspend segregation of duties.

Where the deadline is short, the QA reviewer is notified at assignment so review
capacity is reserved rather than sought at the end.

### 3 — Priority established

AGT records the priority basis: the deadline, and whether the request affects
other work in progress. Where a priority request displaces scheduled Task 3 or
Task 4 work, that is recorded and reported in the next progress report, so the
effect on the retrospective schedule is visible rather than silent.

### 4 — Evidence gathered

Each named entity is verified against the applicable authoritative sources —
the same sources, applicability rules and evidence recording used in every other
review. Every source answer is recorded with the source edition and the date it
was obtained.

A source that cannot answer is recorded as unavailable. **It is never treated as
an answer**, and never as evidence against the entity.

### 5 — Analyst review

The analyst examines the evidence and records a determination with a written
rationale. Where the methodology does not settle a condition, the analyst says
so; the item is reported as awaiting methodology rather than resolved by
judgement in the moment.

Analyst completion time is recorded.

### 6 — QA review

A different reviewer examines the evidence and the analyst's rationale, then
approves, returns or escalates.

- **Approve** — the determination stands and may be reported.
- **Return** — sent back with a reason; the original determination is preserved,
  not erased.
- **Escalate** — referred to a named senior reviewer with a reason.

**Only a standing approval makes a result reportable.** QA completion time is
recorded.

### 7 — Report generated

The status report contains what the contract requires, in the order the contract
names it:

1. **The identified issue**
2. **Root cause, if determined** — and explicitly "not determined" where it was not
3. **The severity or impact**
4. **Recommendations to prevent reoccurrence**
5. **Resolution**

It also carries: the request identifier and dates; the entities reviewed; the
sources consulted with their editions; the observations; any source limitations;
the analyst determination and rationale; the QA decision and reviewer; elapsed
time against the COR's deadline; any methodology question that remained open;
and the contract number.

### 8 — Delivery

Delivered to the COR by the agreed method, within the deadline the COR set.

If a deadline will not be met, AGT notifies the COR **before** it passes, with
the reason and a revised date. A missed deadline reported afterwards is a second
failure on top of the first.

### 9 — Audit and closure

The request is closed with its full history retained: every source answer, the
determination, the QA decision, the report issued and its integrity value. The
result can be reconstructed later without regenerating it, and a regenerated
report cannot be presented as the original.

Closed requests feed the quarterly aggregation (D5.2).

---

## Capacity and surge

| | |
| --- | --- |
| **Contractual expectation** | An average of twenty reviews per month, with capability to exceed |
| **Design** | Each review is independent. Nothing in the workflow serialises one review behind another, so throughput scales with reviewer availability rather than with system capacity. |
| **Constraint** | Analyst and QA availability, and the segregation-of-duties requirement — a surge needs both roles staffed, not just analysts. |
| **Verified** | The workflow, timing measurement and controls |
| **Not yet verified** | **Sustained surge throughput has not been load tested.** AGT records this as an open internal action rather than asserting a capacity figure it has not measured. |

> **CONFIRMATION REQUESTED — P2.** Does the COR anticipate surge periods AGT
> should plan staffing around? A predictable surge is straightforward to staff;
> an unpredictable one requires standing capacity.

> **CONFIRMATION REQUESTED — P3.** Where a single request names many entities,
> does the deadline apply to the request as a whole or to each entity? This
> affects sequencing when a deadline is short.

---

## What AGT will not do

- Report a priority result without analyst determination and independent QA.
- Abbreviate verification to meet a deadline.
- Assert a turnaround performance figure against a service level the contract
  does not establish.
- Treat an unavailable source as an answer because the deadline is close.
- Contact a reviewed entity without written direction from the COR.

---

## Confirmations requested

| ID | Question |
| --- | --- |
| **P1** | Preferred request channel and acknowledgement expectations |
| **P2** | Anticipated surge periods to plan staffing around |
| **P3** | For multi-entity requests, does the deadline apply per request or per entity |
| **P4** | Whether the COR wants a standing target turnaround reported in addition to per-request deadlines |
