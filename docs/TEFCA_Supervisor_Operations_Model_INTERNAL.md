# TEFCA ARC — SUPERVISOR OPERATIONS MODEL

> ## INTERNAL AGT — NOT FOR CLIENT DISTRIBUTION
> **No Government row-level values.** Aggregate figures only.
> **No Government case was assigned, decided, sampled or reported.**

**Contract:** 7571MN26F80064 · HHS/ONC ASTP · **Date:** 2026-08-30
**Master Step:** #15
**Implementation:** `app/tefca_registry/supervisor_ops.py` ·
`app/tefca_registry/review_routes.py` (`/api/tefca/arc/operations/*`) ·
`app/tefca_registry/case_assignment.py` (assignment hardening) ·
`frontend/src/app/tefca-arc/operations/page.js`
**Certification:** `tests/test_supervisor_operations.py` — 50 tests, 12/12 mutations detected

---

## 1. Purpose, and the line it does not cross

A supervisor needs to see the whole estate of work, know why each case exists,
and move it to the right person. That is **management authority**. It is not
review authority, and this layer is built so the two cannot be confused:

| A supervisor may | A supervisor may not |
|---|---|
| see every case, its reason, its holder and its state | record an analyst determination |
| see analyst and QA workload | approve a QA review |
| see COR deadlines and how they stand | set `reportable_at` |
| see source limitations and blocked work | edit Government source or evidence |
| assign and reassign work | bypass segregation of duties |
| read the full audit timeline | resolve an escalation on the Government's behalf |

Two tests enforce this from opposite directions: one scans the module for any
determination or QA call and any write statement at all, and one asserts every
`/operations/*` route is `GET` only. **The single write a supervisor owns is
assignment**, and it stays in `case_assignment` where the audit trail and role
checks already live.

---

## 2. Authority — existing roles, no new one

| Act | Role required | Where it comes from |
|---|---|---|
| Every operations read | `viewer` (1) | The TEFCA read floor, asserted by `test_rbac_roles::test_no_tefca_read_endpoint_sits_above_the_viewer_floor` |
| Claim / release own case | `reviewer` (4) | Step #10 |
| Assign / reassign another person's case | `senior_analyst` (5) — `case_assignment.ROLE_SUPERVISOR` | Step #10 |
| Analyst determination | `reviewer` (4) | Step #9 |
| QA approve / return / escalate | `qalead` (6) | Step #9 |

**No new role was created.** "Supervisor" is `senior_analyst`, which already
existed and already carried exactly this authority. Inventing a role would have
meant a migration, a re-grant and a second answer to who may move work.

Segregation survives dual roles: a principal holding both analyst and QA
authority still cannot QA their own determination, and a test proves it.

---

## 3. A read model, not a second source of truth

```
DQ exceptions ┐
sampling      ├─> review_records ─> case_assignment ─> qa_gate ─> reportable
priority      ┘        │                  │              │
                       └──────────────────┴──────────────┘
                                   │
                       supervisor_ops READS it
```

**New table: NO. Migration: NO. Cache: NO.**

Every figure is derived at read time from `review_records`,
`review_decision_events`, `sample_entities`, `tefca_priority_cases`,
`tefca_verifications` and `tefca_entity_relationships`. A control plane that
persisted its own copy would become a second source of truth, and the first
time the two disagreed nobody could say which was right.

State is computed by the **same ladder** `case_assignment.case_state` uses, from
the same pure `qa_gate` functions — and a test asserts the two agree case by
case across all six states.

---

## 4. Work provenance — every reason, never one

`HUMAN_REQUIRED` · `STATISTICAL_SAMPLE` · `PRIORITY_REQUEST` · `QA_RETURN` ·
`QA_ESCALATION`

A case carries **all** the reasons that apply. A sampled organisation that also
has a data-quality finding and has just come back from QA shows three, because
they are three different facts about why it is on someone's desk. Collapsing
them to "exception" would erase the difference between a Government request and
a formatting finding — and a mutation that keeps only the first reason fails
four tests.

`QA_RETURN` and `QA_ESCALATION` are **additional** reasons, never replacements:
a returned case is still the case it was, still held by the analyst who must
revise it, and still one case (Step #37 — proven: no duplicate work item).

---

## 5. Queue states

Derived, using the existing vocabulary — `AVAILABLE`, `CLAIMED`,
`SUBMITTED_FOR_QA`, `RETURNED`, `ESCALATED`, `APPROVED`. The UI shows friendly
labels (Unassigned, In progress, Awaiting QA, Returned by QA, Escalated,
Approved) and **filters on the canonical value**. There is no second status
machine.

---

## 6. Deadlines — only the Government's

| Rule | Behaviour |
|---|---|
| A deadline exists only where the COR supplied one (Task 5, ¶146) | every other case reports `NO_DEADLINE` |
| No standing turnaround exists | nothing computes one; a test scans the module's compiled source for `timedelta(hours=24)`, `timedelta(days=1)`, `REVIEW_SLA_DAYS` and any `sla` import |
| `DUE_SOON` needs a threshold the contract does not set | `due_soon_within_hours` has **no default**; without it the band does not exist |
| `PAST_DUE` is arithmetic | `compliance_conclusion` is always `null`, on every case, at every status |

The status computation **delegates to `priority_review.deadline_status`** rather
than restating the rule, so the supervisor screen and the Task 5 report can
never disagree about what `PAST_DUE` means.

---

## 7. Three clocks, each named

| Field | Measured from | The question it answers |
|---|---|---|
| `age_days` | case creation | how long has this work existed |
| `held_days` | current assignment | how long has this person had it |
| `idle_days` | the last thing that actually happened | how long has nobody touched it |

Reporting one number would hide whichever case is actually stuck: an unassigned
queue is old in the first sense, a case parked with an analyst in the second, an
abandoned one in the third.

**Attention is internal, and opt-in.** `BLOCKED` where a real limitation exists,
`ATTENTION` on a genuine past-due COR deadline or beyond a caller-supplied
`stale_after_days`, otherwise `NORMAL`. `stale_after_days` has **no default**:
no approved threshold exists for how long a review case may sit, and hard-coding
one would be an AGT service level invented on a dashboard and then reported
against.

---

## 8. Source limitations

`SOURCE_UNAVAILABLE` (per source) · `ENTITY_RESOLUTION` (ambiguous / not found /
insufficient information) · `GOVERNMENT_VERIFICATION_PENDING`.

`unavailable` is carried through unchanged and each entry states what it means:
*"The source could not answer. This is not evidence for or against the entity."*
It is never rendered as a pass, a clear or a no-match — a supervisor screen is
precisely where that distinction gets quietly lost, and a mutation that hides it
fails a test.

---

## 9. Sampling and priority summaries

**Sampling.** Where no `ReviewSample` exists the status is `NOT_YET_CREATED`
with a sentence, and **no number at all** — no percentage, no "0% complete", no
progress bar. A plan nobody drew is not a plan running behind. A test asserts
the payload contains no `%`, no "overdue", and none of `completion`, `progress`,
`selected`, `remaining`, `sample_size` or `population_size`. Where plans exist,
counts reconcile exactly to the frozen `SampleEntity` membership.

The Step #13 read-only forecast of **1,967** is a planning figure and appears
nowhere in this layer. A mutation that presents it as an official plan fails two
tests.

**Priority.** Counts by state and by deadline status, measured against the COR's
own per-request deadlines.

---

## 10. Two defects this gate found in Step #10's `assign`

Both proven, both fixed, both pinned by a mutation.

**A lost update.** `assign` was a read-modify-write: it read the record, set
`assigned_to_user_id` and flushed. Two supervisors assigning the same case
concurrently therefore **both succeeded** — one assignment vanished, and **both
audit rows recorded `previous_owner: None`**, so the trail said the case had
been assigned twice from nobody. `claim` and `release` had used a conditional
`UPDATE … RETURNING` since Step #10; `assign` had not. It now does, compared
against the owner the call actually observed, and the loser is refused with a
stated reason.

**A silent handover.** `assign` took a case off a live holder with nothing
recorded but the fact. Taking work from someone part way through it now requires
`override_reason` of at least 10 characters, recorded in the audit row alongside
both owners, the actor, the role and the previous state. Assigning **unheld**
work still needs nothing — the point is to make a handover visible, not to make
assignment hard.

The Step #10 test that asserted the old silent behaviour was updated to assert
the refusal and then the explicit override. That is a tightening of a contract
this gate was told to prove, not a regression.

---

## 11. Filtering, search, sorting, pagination

**Filters:** queue source · work reason · state · assignee · unassigned only ·
QHIN · limited only · reportable · deadline state. Column-backed filters are
pushed into SQL; derived ones (state, reason, limitation, deadline band) are
applied after derivation over a bounded candidate set, and the payload reports
`candidate_ceiling` and `truncated` rather than presenting a cut-off queue as
complete.

**Search** is anchored (`term%`), never `%term%`: a leading wildcard cannot use
an index, and a supervisor search box is not a reason to scan 23,566 rows. It
matches the case reference, the COR reference and the organisation name.

**Sorting** by age, deadline, idle, created or reference. A case with **no
deadline sorts last**, not first.

**Pagination is deterministic.** Every ordering is tie-broken by `review_id`,
the one unique column, and this is asserted on the compiled `ORDER BY` clause —
with equal sort keys Postgres *may* happen to return a stable order, so a
behavioural test can pass while the ordering is undefined. A mutation removing
the tie-break fails.

---

## 12. Performance

A page of work costs a **fixed** number of queries regardless of page size:
decision events, priority context, sample membership, unavailable sources,
entity names and QHIN edges are each fetched once for the whole page. The
per-case `case_assignment.case_state` is right for one case and would have been
50 queries for a page of 50, plus 50 more for reportability.

Certified: a 25-row page issues **≤ 12** queries, asserted by counting
`db.execute` calls. No list view loads evidence; evidence is a drill-down.

---

## 13. UI

`/tefca-arc/operations`, added to the existing ARC navigation after QA
Operations. Composition only — `CommandBar`, `Panel`, `KPICard`, `DataTable`,
`StatusBadge`, `EmptyState` and the **shared platform `SidePanel`** (Step #14's
expand/collapse, unchanged — no second entity panel was created).

Accessibility: status is text plus badge, never colour alone; the queue is a
real table with a caption and header scopes; filters are labelled `<select>` and
`<input>` elements with explicit `<label for>`; the pager announces its range
through `aria-live`; the detail surface inherits the platform dialog semantics
and focus trap. A failed read is announced as a failed read — *"the list below
is not a statement that there is no work"* — because rendering a fetch failure
as an empty queue would hide the entire estate.

**Automated accessibility check: NOT AVAILABLE.** The frontend has no test
harness (`package.json` declares `dev`, `build`, `start` only), so this is a
structural code-level verification and the production build passes. **Manual and
Government 508 review is still required.**

---

## 14. Security

| Control | Position |
|---|---|
| Authentication | all 9 operations endpoints refuse unauthenticated callers |
| RBAC | reads at `viewer`; assignment at `senior_analyst`; asserted against the declared route dependencies |
| Authenticated actor | the audit actor comes from the token. A test parses the handler's AST and refuses any **rebinding of `user`** — checking for `user=user` alone was not enough, because a handler can reassign `user` from the body and still pass it |
| Mass assignment | `CaseAssign` exposes exactly `{to_user_id, reason, override_reason}`; no determination, QA, reportability, actor or approver field exists |
| IDOR | authorization is **program-wide, not QHIN-scoped** — see §17 |
| Concurrency | conditional `UPDATE … RETURNING` on assign, claim and release; append-only decision events |
| Data leakage | a test scans the module for `raw_line`, `parsed[`, `original_value`, `suggested_value` and credential names — no delivered Government field content reaches a list view |
| Audit | creation, assignment, reassignment, release, determination, QA and reportability, reconstructed chronologically |

---

## 15. Test evidence — 50 tests

Every state derived and agreeing with the case service · unassigned queue exact
· provenance keeps sample + DQ + QA-return together · a priority case is never
shown as a statistical selection · only a COR deadline is a deadline ·
`DUE_SOON` only when the caller defines it · no SLA anywhere in the module · an
amendment moves the dashboard and keeps the original · unavailable stays
unavailable · an ambiguous target is a reported limitation · analyst workload
scores nobody · QA workload is separate and names whose determination waits ·
taking a case off a live holder needs a reason · reassignment keeps the whole
handover history · an approved case is not reassignable · the control plane
cannot decide anything · no operations endpoint can write · a dual-role
principal still cannot self-approve · pagination is deterministic and loses
nothing · identical timestamps still paginate · every sort is tie-broken ·
filters · search by reference, COR reference and organisation · sorting puts no
deadline last · no plan is a zero state not late work · an empty estate is an
honest dashboard · dashboard reconciles with the queue · sampling reconciles to
frozen membership · end-to-end intake → reportable · QA return stays one case ·
escalation resolves nothing · a DQ finding is not an analyst case · a page costs
a fixed number of queries · 24 priority requests stay coherent · two supervisors
assigning at once produce one owner · a claim racing an assignment leaves one
owner · 9 endpoints require authentication · role ladder · no protected field ·
no Government values on a list view · case detail is management facts only.

### Mutation-tested — 12 mutations, all detected

| Mutation | Result |
|---|---|
| A the control plane can record a determination | DETECTED |
| B an analyst may approve their own determination | DETECTED |
| C assignment is a read-modify-write again | DETECTED |
| D a reassignment leaves no history | DETECTED |
| E a priority deadline defaults to 24 hours | DETECTED |
| F an unavailable source is not shown at all | DETECTED |
| G a forecast is presented as an official plan | DETECTED |
| H a case with no deadline is called past due | DETECTED |
| I a case keeps only its first reason | DETECTED |
| J the audit actor is taken from the request body | DETECTED |
| K pagination has no stable tie-break | DETECTED |
| L a returned case appears as a second work item | DETECTED |

Production code restored byte-identically after each.

**Two mutations initially escaped and the tests were strengthened, not the
mutations weakened.** J passed because the test only looked for the string
`user=user`, which the mutated handler still contained; it now parses the AST
and refuses any rebinding of `user`. K passed because the pagination fixtures
had distinct timestamps, so the ordering was total by accident; the tie-break is
now asserted on the compiled SQL.

---

## 16. Government read-only forecast — nothing created

| | |
|---|---|
| DQ HUMAN_REQUIRED findings | **138** |
| Operational DQ review cases | **0** |
| Unoperationalized findings | **138** |
| `review_records` total | 43 (historical) |
| Assigned · reportable · decision events | **0 · 0 · 0** |
| Official samples · membership | **0 · 0** |
| Priority review cases | **0** |
| QHIN-attributed entities | 23,562 |
| Sampling status | `NOT_YET_CREATED` |
| Source limitations | none recorded |

**A HUMAN_REQUIRED finding is not an analyst case.** The two are reported
separately and the difference — 138 — is stated rather than closed. Creating
those cases is a separate authorized act, not a reconciliation this view may
perform, and nothing in this gate created one.

**The 43 historical review records carry no `queue_source`**, so they appear as
unassigned work with **no recorded reason**. That is the truth about them: they
predate the provenance convention introduced in Step #8. They are shown as they
are rather than being back-filled with a reason nobody recorded.

---

## 17. Known limitations, stated rather than implied

1. **Authorization is program-wide, not QHIN-scoped.** Any authorized viewer
   sees the whole estate. The contract establishes no per-QHIN tenancy and AGT
   is one contractor reviewing all of it, so manufacturing QHIN-level isolation
   would be inventing a security boundary the contract does not ask for. Recorded
   as a fact, not implemented as a feature.
2. **Derived filters work over a bounded candidate set** (2,000 cases) because
   state, provenance, limitation and deadline band are not columns. The payload
   reports `candidate_ceiling` and `truncated`. At the current estate (43 cases)
   this is never reached; if the DQ bridge is ever run over the full 138 it is
   still not reached.
3. **Bulk assignment was assessed and NOT BUILT.** Expected volume is ~20
   priority reviews a month plus a DQ queue in the low hundreds, and individual
   assignment with a stated reason is both sufficient and safer. A bulk path
   would have to reproduce RBAC, eligibility, the live-holder override and the
   per-case audit row; nothing observed justifies that.
4. **Export was not built.** The mandate defers it, and no CSV/export exists on
   this surface. `/api/tefca/reports/export` remains the existing, separately
   gated path.
5. **No automated accessibility harness** exists in the frontend.
6. **Surge throughput is not load tested** (carried from Step #14).

---

## 18. Open governance questions

Carried unchanged from Step #13: sampling margin of error · stratification
confirmation · Task 3 / Task 4 cadence · sampling HELD eligibility ·
repeat-selection policy.

Carried unchanged from Step #14: priority request channel and acknowledgement ·
anticipated surge periods · multi-entity deadline packaging · whether a standing
turnaround should be reported · withdrawal authority.

**Arising from Step #15 — one, and only because a real gap exists:**

1. **Operationalizing the 138 HUMAN_REQUIRED findings.** Whether, when and under
   whose authority the DQ bridge should be run over the delivered population to
   create analyst cases. The engineering exists and is certified; the decision to
   start Government analyst work does not belong to this layer.

No operational staleness threshold is proposed, because none is needed: the
parameter exists and is unset until someone with authority sets it.
