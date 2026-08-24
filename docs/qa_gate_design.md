# QA GATE — DESIGN

**Date:** 2026-08-22 · **Branch:** `fix/tefca-stabilization` · **Status:** DESIGN ONLY — nothing here is implemented.

---

## 0. WHAT EXISTS TODAY

| Fact | Evidence |
|---|---|
| No QA mechanism for determinations | `review_records` has 16 columns; none relates to QA |
| A determination is final after one `reviewer`-level action | The resolve endpoint writes the resolution and commits. No second actor, no second step |
| 0 of 43 determinations have any human resolution at all | `reviewer_resolution` is NULL on every row |
| A QA gate endpoint exists and **nothing calls it** | Grep across the application for its name returns only its own definition and the QA engine |
| `qalead` (privilege level 6) gates **nothing** anywhere | Role ladder: viewer 1 · contributor 2 · manager 3 · reviewer 4 · senior_analyst 5 · qalead 6 · program_manager 7 · admin 8 |
| A working QA precedent **does** exist, at a different layer | Issue-correction approval enforces a QA actor distinct from the reviewer and records both. This is the pattern to copy |

**QA is independent of D1–D7.** No methodology decision changes any of this design.

---

## 1. TARGET FLOW

```
  SYSTEM RECOMMENDATION          bucket + rule_code + rule_version + rationale
        |                        (never edited by a human, ever)
        v
  ANALYST DETERMINATION          decision event #1   actor: reviewer (4+)
        |
        v
  QA REVIEW                      decision event #2   actor: qalead (6+), != analyst
        |
        +-- APPROVE   --> determination stands ------------> REPORTABLE
        |
        +-- RETURN    --> back to the analyst
        |                 analyst issues a NEW determination (event #3)
        |                 event #1 is preserved, unchanged
        |                 re-enters QA
        |
        +-- ESCALATE  --> program_manager (7+) or admin (8)
                          who issues a SUPERSEDING determination (event #3)
                          supersedes_decision_id = event #1
                          supersession_reason    = mandatory
                          event #1 is preserved, unchanged
                          --------------------------------> REPORTABLE
```

**Nothing in this flow overwrites anything.** Every actor's decision is an append.
A superseding decision points at what it supersedes; the superseded decision keeps
its own text, actor and timestamp forever.

---

## 2. SCHEMA — TWO OPTIONS EVALUATED

### Option 1 — QA columns on `review_records`

```
review_records  (additions)
  qa_action            VARCHAR(10)   -- APPROVE | RETURN | ESCALATE
  qa_reviewer_id       UUID
  qa_reviewed_at       TIMESTAMPTZ
  qa_reason            TEXT
  escalated_to_user_id UUID
  escalation_reason    TEXT
  reportable_at        TIMESTAMPTZ
```

| Pros | Cons |
|---|---|
| Smallest migration; one table to query | **Cannot represent history.** A RETURN followed by a new determination followed by an APPROVE is three events; seven columns hold one |
| Report queries need no join | A second QA pass **overwrites** the first — precisely the requirement this design exists to prevent |
| | No place for a superseding decision that is a *different actor's* determination |
| | `reviewer_resolution` and the QA columns drift out of step with no constraint able to detect it |

**Option 1 fails requirement D (immutable history) structurally, not incidentally.**
The RETURN path is a loop, and a fixed column set cannot hold a loop.

### Option 2 — a decision-event table (**RECOMMENDED**)

One append-only table holding every human act on a determination. `review_records`
keeps the *system* recommendation and gains one derived pointer.

```
review_decision_events                      APPEND-ONLY. No UPDATE. No DELETE.
  id                      UUID    PK
  review_id               VARCHAR(20) NOT NULL  FK -> review_records.review_id
  sequence_number         INT     NOT NULL      -- 1,2,3… per review; ordering is data
  event_type              VARCHAR(24) NOT NULL  -- ANALYST_DETERMINATION
                                                -- QA_REVIEW
                                                -- SUPERSEDING_DETERMINATION
  -- who
  actor_user_id           UUID    NOT NULL
  actor_email             VARCHAR(320) NOT NULL
  actor_role              VARCHAR(30)  NOT NULL -- role AT THE TIME OF THE ACT
  occurred_at             TIMESTAMPTZ NOT NULL DEFAULT now()

  -- what (analyst / superseding determinations)
  determination           VARCHAR(12)           -- CONFIRM | RECLASSIFY
  determined_bucket       VARCHAR(2)            -- B1..B4 when RECLASSIFY
  rationale               TEXT                  -- mandatory for every event

  -- what (QA reviews)
  qa_action               VARCHAR(10)           -- APPROVE | RETURN | ESCALATE
  qa_reason               TEXT
  escalated_to_user_id    UUID
  escalation_reason       TEXT

  -- supersession
  supersedes_decision_id  UUID  FK -> review_decision_events.id
  supersession_reason     TEXT

  ip_address              VARCHAR(45)
  correlation_id          UUID

  UNIQUE (review_id, sequence_number)
  CHECK (event_type <> 'QA_REVIEW' OR qa_action IS NOT NULL)
  CHECK (qa_action <> 'ESCALATE' OR (escalated_to_user_id IS NOT NULL
                                     AND escalation_reason IS NOT NULL))
  CHECK (supersedes_decision_id IS NULL OR supersession_reason IS NOT NULL)
  CHECK (rationale IS NOT NULL AND length(btrim(rationale)) >= 10)
```

```
review_records  (one addition only)
  reportable_at   TIMESTAMPTZ   -- NULL until a QA APPROVE event exists.
                                -- Derived, written only by the QA approve path.
```

| Pros | Cons |
|---|---|
| History is the table. RETURN loops are just more rows | One join for the current state of a review |
| A superseding determination is an ordinary event with a pointer — no special column set | Report queries need a "latest effective determination" view |
| Actor role is captured **at the time of the act**, so a later role change cannot rewrite what authority a decision was made under | Slightly more code than seven columns |
| The database enforces the invariants: escalation must name a target and a reason; supersession must state why; every event needs a rationale | |
| Extends to the analyst queue and future workflow without further migrations | |

### 2.1 Recommendation — **Option 2**

Requirement D is the deciding factor. An append-only event table is the only shape
that satisfies "QA returning a determination does not overwrite the original
analyst decision" without special-casing. It also matches the pattern the codebase
already trusts elsewhere — dimension evidence is append-only with a generation
stamp, and PPEF snapshots are append-only with a version anchor.

**A supporting view, not a table:**

```sql
CREATE VIEW review_effective_determination AS
SELECT DISTINCT ON (e.review_id)
       e.review_id, e.id AS decision_event_id, e.determination,
       e.determined_bucket, e.actor_email, e.occurred_at
FROM   review_decision_events e
WHERE  e.event_type IN ('ANALYST_DETERMINATION','SUPERSEDING_DETERMINATION')
  AND  NOT EXISTS (SELECT 1 FROM review_decision_events s
                   WHERE s.supersedes_decision_id = e.id)
ORDER BY e.review_id, e.sequence_number DESC;
```

Superseded events are excluded from *effective* state and remain fully readable in
the table. **Nothing is hidden; only precedence is expressed.**

### 2.2 What happens to the existing columns

`reviewer_resolution`, `reclassified_to`, `reclassified_by`, `reclassified_at`,
`resolution_rationale`, `reviewed_at` on `review_records` are **left in place and
left populated**. They are NULL on all 43 current rows, so there is no data to
migrate. New determinations write an event **and** mirror into those columns
during a transition period, so existing report queries keep working. The columns
are retired only after the report layer reads the view — a separate change, and
not part of this design.

---

## 3. SEGREGATION OF DUTIES

**Enforced in three places**, because one is not enough:

| Layer | Enforcement |
|---|---|
| Application | `qa_reviewer_id != analyst_user_id` checked before the event is written; refuses with HTTP 409 and a message naming the analyst |
| Database | A trigger on insert of a `QA_REVIEW` event resolves the analyst for that `review_id` and raises if they match. Catches any future code path that bypasses the service |
| Audit | Both actor ids on both events; a mismatch is detectable retrospectively even if both above failed |

**Emergency exception.** The prompt permits a documented exception. Design:

```
  event_type = 'QA_REVIEW'
  sod_exception_granted_by  UUID          -- admin (8) only, never self
  sod_exception_reason      TEXT          -- mandatory, minimum length enforced
```

The trigger permits the self-review **only** when both fields are populated and
`sod_exception_granted_by` differs from `actor_user_id`. Every such event is
counted in the reconciliation gate, so an exception cannot become routine
unnoticed. **AGT recommends this be disabled by configuration in production and
enabled only by deliberate act.**

---

## 4. API

| Endpoint | Method | Role | Purpose |
|---|---|---|---|
| `/api/tefca/reviews/{review_id}/qa` | POST | `qalead` (6) | Submit a QA decision: APPROVE, RETURN or ESCALATE |
| `/api/tefca/reviews/qa-queue` | GET | `qalead` (6) | Determinations awaiting QA — analyst determination exists, no QA event |
| `/api/tefca/reviews/{review_id}/supersede` | POST | `program_manager` (7) | Issue a superseding determination after escalation |
| `/api/tefca/reviews/{review_id}/history` | GET | `viewer` (1) | The full ordered event chain, superseded events included |

**Request — POST `/qa`**

```json
{ "qa_action": "APPROVE" | "RETURN" | "ESCALATE",
  "qa_reason": "…",                       // mandatory, all three actions
  "escalated_to_user_id": "uuid",         // mandatory when ESCALATE
  "escalation_reason": "…" }              // mandatory when ESCALATE
```

**Refusals** — each returns 409 with a reason, never a silent no-op:

- no analyst determination exists yet for this review
- the QA actor is the analyst (unless a valid SoD exception is supplied)
- an APPROVE already stands and has not been returned or superseded
- the review does not exist

**Request — POST `/supersede`** requires `determination`, `determined_bucket` when
reclassifying, `rationale`, and `supersedes_decision_id`. It is rejected unless the
named prior event exists, belongs to this review, and is not already superseded.

---

## 5. RBAC

| Action | Minimum role | Level |
|---|---|---|
| View a determination and its history | `viewer` | 1 |
| Issue an analyst determination | `reviewer` | 4 |
| Perform QA (approve / return / escalate) | **`qalead`** | **6** |
| Receive an escalation | `program_manager` | 7 |
| Issue a superseding determination | **`program_manager`** | **7** |
| Grant a segregation-of-duties exception | `admin` | 8 |

`senior_analyst` (5) sits between analyst and QA and is deliberately **not** given
QA authority — QA is an independent function, and level 5 is the escalation tier
for T3 work, which is analyst work.

**Note on role capture.** `actor_role` records the role held *at the time of the
act*. The authorisation check reads the live database role — a role change takes
effect on the next request — but the event records what authority the decision was
actually made under, which is what an auditor needs.

---

## 6. THE REPORTABLE GATE

```
A determination is REPORTABLE if and only if:
  an ANALYST_DETERMINATION or SUPERSEDING_DETERMINATION event exists
  AND its effective successor is not superseded
  AND a QA_REVIEW event with qa_action = 'APPROVE' exists after it
  AND no later QA_REVIEW with RETURN or ESCALATE exists
```

**Enforcement point.** Report generation must consult this, and must **refuse**
rather than report. Today the equivalent gate is advisory and uncalled. Two
behaviours are needed and they are different:

- **Refuse** to generate a deliverable report over unapproved determinations.
- **Label** an internal or draft report that deliberately includes them, stating
  the count in the report itself.

Which reports are "deliverable" is a program question; the mechanism is not.

---

## 7. AUDIT

Every QA action writes **two** records, deliberately:

1. The `review_decision_events` row — the decision itself, append-only, the
   authoritative record.
2. A TEFCA registry audit row — `qa_approved` / `qa_returned` / `qa_escalated` /
   `determination_superseded`, carrying actor id, actor email, IP address and the
   decision event id in its metadata.

The second exists so that "show me everything this user did" is answerable without
knowing which domain tables to join, which is the question an auditor asks first.
This mirrors the existing pattern — the resolve endpoint already writes a
`review_resolved` audit row.

---

## 8. LOC ESTIMATE

| Work item | Production | Test |
|---|---|---|
| Migration: `review_decision_events` + constraints + indexes | 95 | 45 |
| Migration: `reportable_at` on `review_records` + the effective-determination view | 35 | 25 |
| SoD trigger + function | 45 | 55 |
| QA service: submit, refusal cases, event sequencing | 110 | 130 |
| Supersession service | 55 | 70 |
| Four endpoints + schemas + role gates | 120 | 110 |
| Analyst determination writes an event (alongside existing columns) | 45 | 50 |
| Reportable gate + report-layer enforcement | 50 | 65 |
| Audit integration | 25 | 30 |
| Frontend: QA queue page, decision form, history timeline | 180 | — |
| **TOTAL** | **~760** | **~580** |

Excluding the frontend: **~580 production / ~580 test.**

---

## 9. RISKS

| Risk | Severity | Mitigation |
|---|---|---|
| QA becomes a rubber stamp under deliverable pressure | HIGH | Not solvable in software. Report QA approval rate, median time-to-approve, and return rate as operational metrics so the pattern is visible |
| A RETURN loop never terminates | MEDIUM | `sequence_number` makes loop depth queryable; surface reviews exceeding N cycles in the QA queue |
| The SoD emergency exception becomes routine | MEDIUM | Requires an admin grant, is counted in reconciliation, and should be disabled by configuration in production |
| The 43 existing determinations have no analyst event, so none can ever become reportable | **MEDIUM** | Correct and intended. They are system recommendations, not determinations. They must pass through analyst and QA like any other — the gate should not be back-dated |
| Two sources of truth during the transition (columns + events) | MEDIUM | Mirror-write for one release; a test asserts the two agree; retire the columns in a separate change |
| Report layer forgets to consult the gate | HIGH | The gate belongs inside the report data service, not at the route — the same placement that makes the read-only contract enforceable there today |

---

## 10. DEPENDENCIES

- **Independent of D1–D7.** QA is a process control; it does not depend on what
  the buckets mean.
- **Interacts with the analyst queue** (`docs/analyst_queue_wiring_plan.md`), which
  is blocked on **D3**. QA can be built first: the queue determines *who is
  assigned* work; QA determines *what happens after* the work is done. They meet
  only at the analyst determination event, which QA defines.
- **Recommended sequence:** QA gate first. It is unblocked, and it establishes the
  decision-event table that the queue will later reference.
