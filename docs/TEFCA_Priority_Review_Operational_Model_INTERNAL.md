# TEFCA ARC — PRIORITY REVIEW OPERATIONAL MODEL

> ## INTERNAL AGT — NOT FOR CLIENT DISTRIBUTION
> **No Government row-level values.** Aggregate figures only.
> **No Government priority request, case, decision or report exists.**

**Contract:** 7571MN26F80064 · HHS/ONC ASTP · **Date:** 2026-08-30
**Master Step:** #14
**Implementation:** `app/tefca_registry/priority_review.py` ·
`app/tefca_registry/review_routes.py` (`/api/tefca/arc/priority-requests/*`) ·
`app/Tefca/entity_resolution.py` (`resolve_reference_detail`) ·
`app/reports/data/sow_report_data.py` (`priority_status`)
**Certification:** `tests/test_priority_review_operational.py` — 63 tests

---

## 1. Authority for every parameter

Task 5, ¶146–¶150. Sources: `docs/phase7_contract_reporting_matrix.md` §D5.1/D5.2,
`docs/cor_activation_package/01_Contract_Traceability_Matrix.md` (Task 5),
`docs/cor_activation_package/06_Priority_Review_Procedure.md`.

| Requirement | Value | Authority | Status |
|---|---|---|---|
| Who initiates | The COR. "Priority reviews based on issues identified by the COR" | **CONTRACT REQUIRED** ¶146 | IMPLEMENTED |
| Who names the entities | The COR. "The Participants or Subparticipants identified for review will be communicated by the COR" | **CONTRACT REQUIRED** ¶146 | IMPLEMENTED |
| Deadline | **"within the agreed upon deadline"**, set **per request by the COR** | **CONTRACT REQUIRED** ¶146 | IMPLEMENTED |
| Fixed turnaround | **NONE EXISTS** | **CONTRACT** — ¶146 sets it per request | IMPLEMENTED (nothing computes one) |
| Volume | average **20 per month**, with capability to exceed | **CONTRACT REQUIRED** ¶146 | IMPLEMENTED as capacity, not a cap |
| D5.1 content | identified issue · root cause **if determined** · severity or impact · recommendations to prevent reoccurrence · resolution · plus methodology and control-framework changes | **CONTRACT REQUIRED** ¶147 | IMPLEMENTED |
| D5.1 trigger | at the direction of the COR | **CONTRACT REQUIRED** ¶147 | IMPLEMENTED |
| D5.2 | quarterly ninety-day aggregation | **CONTRACT REQUIRED** ¶148, ¶150 | PRE-EXISTING (`generate_priority_quarterly_report`) |
| Analyst determination + independent QA | required before a result is reported | **AGT METHODOLOGY** (approved) | IMPLEMENTED |
| Acknowledgement within one business day | AGT operating commitment | **AGT METHODOLOGY** | GOVERNMENT PARAMETER — channel unconfirmed (P1) |
| Request channel | unconfirmed | — | **GOVERNMENT PARAMETER** (P1) |
| Multi-entity deadline scope | per request or per entity | — | **GOVERNMENT PARAMETER** (P3) |

### The one-hour rule is a different rule

The contract's one-hour requirement is **incident reporting**. It is not a Task 5
turnaround and is not applied here. **No 24-hour, one-business-day or next-day
service level exists anywhere in this implementation**, and a test scans the
module's compiled source to keep it that way.

---

## 2. What already existed, and what was actually missing

`tefca_priority_cases` has existed since `20260627_tefca_initial_schema`, with
`POST/GET/PATCH /api/tefca/priority-cases`, a D5.1 status report and a D5.2
quarterly aggregation. **0 rows.**

What it did not have was the certified maker-checker chain:

| Gap found | Consequence |
|---|---|
| `PATCH /priority-cases/{id}` sets root cause, severity, resolution and status in one call, at `senior_analyst`, and stamps `assigned_reviewer_id` to the caller | One person could determine and effectively publish a Government finding. No analyst determination event, no independent QA, no segregation of duties, no reportability gate |
| `review_engine.execute_priority_review` derives `root_cause_determination` and `severity` automatically — from connectors, or by **parsing the issue text** | An automated ARC determination |
| `review_routes POST /priority-review` runs a review and writes a report marked `"status": "complete"` | Same, with a report attached |
| Target resolution called the live RCE directory and upserted into the LEGACY `tefca_entities` (2 rows), not the canonical registry (23,756) | Priority work pointed at a different registry from the rest of ARC |
| No ambiguity state: unresolved silently became `entity_id = NULL` | An ambiguous COR reference looked identical to a resolved one |
| No uniqueness, no idempotency, no concurrency control on request creation | A transport retry doubled an analyst's workload |
| No deadline history | An amended deadline was indistinguishable from a misrecorded one |
| `sla.py` holds `"priority": 3` days and emits `overdue` | A **fixed internal SLA** — not contractual, and never consulting the COR's deadline |

Step #14 closes the first seven. The eighth is recorded in §9 and deliberately
not changed: `sla.py` serves the **sampled**-review dashboard, Step #14 does not
depend on it, and a test pins that the priority path never touches it.

---

## 3. The architecture — reuse, and one new service

```
authorized COR request
  -> TEFCAPriorityCase             the request of record  (EXISTING TABLE)
  -> resolve_reference_detail      one canonical ladder   (EXISTING, extended)
  -> review_records                the case               (EXISTING)
  -> case_assignment               claim / assign         (EXISTING, Step #10)
  -> review_decision_events        determination + QA     (EXISTING, Step #9)
  -> review_records.reportable_at  the release gate       (EXISTING)
  -> sow_report_data.priority_status   D5.1              (EXISTING, extended)
```

**New table: NO. Migration: NO.**

| Reused | From |
|---|---|
| `TEFCAPriorityCase` | `app/Tefca/models.py` — the request, and the D5.1 content columns |
| `review_records` / `review_decision_events` | Step #9 |
| `case_assignment` (claim, release, assign, derived state) | Step #10 |
| `qa_gate` (determination, QA, SoD, reportability) | Step #9 |
| `tefca_reg_audit_log` | the registry audit trail |
| `entity_resolution` ladder | `app/Tefca/entity_resolution.py` |
| `identifier_boundary` (TIN/EIN/FEIN) | `app/Tefca/identifier_boundary.py` |
| `TefcaVerification` observations | Step #6 |
| `sow_report_data.priority_status` | Phase 7.5 canonical report service |

**One new module**, `app/tefca_registry/priority_review.py`, which orchestrates
and decides nothing: it contains no formula, no connector call, no retry loop
and no determination logic.

### The resolver was extended, not duplicated

`resolve_from_db` returned `None` for "no match", "four matches" and "database
fault" alike — correct for the evidence pipeline, which can only proceed with an
entity, but useless to a caller that must act on the difference. The ladder was
lifted into `resolve_reference_detail`, which reports
`RESOLVED / AMBIGUOUS / NOT_FOUND / INSUFFICIENT_INFORMATION` with its
candidates; `resolve_from_db` now calls it and keeps its exact previous
behaviour. **One ladder, two views** — 27 existing resolver tests unchanged and
passing.

---

## 4. Invariants

**A priority review exists because the COR asked.** `receive_request` requires a
COR reference, a named requester, the issue as described and the organisation
named — none with a default. Nothing derives a request from a HIGH DQ severity,
a HELD record, a source conflict, a sample selection or a NEW/CHANGED delta
status, and a test scans the module for exactly those couplings.

**Priority is not severity.** The request's `severity` is the analyst's
assessment of the issue the COR raised. It is unrelated to DQ severity, finding
severity, incident severity and QA escalation.

**Selection is not a finding. A determination is not a finding.** Only a
standing QA approval releases content — enforced in `reportable_result`, and
again in the D5.1 report, which withholds every determination field until the
gate opens.

**Automation prepares; a human decides.** The service assembles the case,
resolves the target, collects recorded observations and computes dates. It
records no determination of its own, and no LLM writes one.

---

## 5. Request identity, idempotency and concurrency

`request_key = TEFCA_ARC_PRIORITY:{cor_reference}:{target_reference}`.

* **Same request, same target, submitted twice** → the case already made, with
  `duplicate_request: true`. A transport retry never doubles work.
* **A new COR reference, same organisation** → a new case. The Government is
  entitled to ask again, and a uniqueness rule keyed on the organisation alone
  would refuse them.
* **Two concurrent submissions** → `pg_advisory_xact_lock(hashtext(request_key))`,
  transaction-scoped. Certified with two committing sessions in a throwaway
  schema: one request, one case, zero duplicates.

The identifier is an internal `case_id` (UUID) plus the COR's own reference.
**No Government case number is invented.**

---

## 6. Target resolution, and the four outcomes

| Outcome | Behaviour |
|---|---|
| **RESOLVED** | the case is created against the canonical entity |
| **AMBIGUOUS** | the request is logged, the candidate ids are preserved, and **no case exists** until a human names the entity with a written rationale (`resolve_target_manually`). Never the first match |
| **NOT_FOUND** | the request is logged in `NEEDS_TARGET_RESOLUTION`. **No organisation is created to satisfy a request** |
| **INSUFFICIENT_INFORMATION** | an empty reference |

**The request is always logged**, even when the target is not resolvable. The
COR asked; recording nothing would be the opposite of the procedure.

**Unpromoted and HELD targets are reviewable.** Unlike statistical sampling —
where `sample_entities.entity_id` is NOT NULL, so an unpromoted record cannot be
a sampling unit — a priority case anchors to `review_records.source_record_id`,
the delivered Area 1 line. Nothing is promoted to make a request answerable.

---

## 7. The deadline

* Recorded exactly as the COR stated it; **absent means absent**.
* Status vocabulary: `NO_DEADLINE`, `ON_TRACK`, `DUE_SOON`, `PAST_DUE`.
* **`DUE_SOON` has no default threshold.** The caller must say what "soon"
  means, and the answer records the number it was given. A default would be an
  invented service level arriving through the back door.
* **`PAST_DUE` carries `compliance_conclusion: null`.** It is arithmetic on two
  timestamps. Whether a missed deadline is a contractual failure depends on what
  was agreed and what was communicated, neither of which a timestamp knows.
* **Amendment**: the column holds the current value; every value it has ever
  held lives in the append-only audit trail with the actor and a mandatory
  reason of at least 10 characters. `deadline_history()` reconstructs the whole
  sequence, original first.

Audit rows are ordered by a `recorded_at` value the service writes, **not** by
`created_at`: Postgres `now()` is transaction time, identical for two rows
written in one transaction, so ordering on it would scramble the history.

---

## 8. Capacity

20 requests a month with capability to exceed. Certified at **24 synthetic
requests** (20 + 20% surge) through creation, queue, workload and state, with no
duplicate work. Each request is independent — nothing in the workflow serialises
one behind another — so throughput is bounded by **analyst and QA staffing**,
and segregation of duties means a surge needs both roles, not just analysts.

`workload_summary` counts; it caps nothing. Twenty a month is a capacity
expectation, not a quota, and exceeding it is not a finding.

**Sustained surge throughput has not been load tested.** Recorded as an AGT
action, not asserted as a capacity figure.

---

## 9. Evidence

Priority reviews use the existing verification and evidence architecture and
**collect nothing of their own** — no connector call, no retry loop, no source
driven harder because a deadline is close. The analyst package presents the
recorded observations with their sources and dates.

* **SAM unavailable stays `unavailable`.** Never `PASS`, `CLEAR`, `NO_MATCH` or
  `not_found`, and never evidence against an entity. Certified, including under
  an expired deadline.
* **TIN / EIN / FEIN stay behind the Government boundary**, presented as
  `PENDING_GOVERNMENT_VERIFICATION` from `identifier_boundary`. Identity is
  never inferred from an NPI and no PASS/FAIL is manufactured.
* Applicability controls are unchanged; not every source is called for every
  entity.

---

## 10. Overlaps

| Overlap | Behaviour |
|---|---|
| **Priority + statistical sample** | Separate cases, separate provenance. `selection_reason` is `PRIORITY_REQUEST`, never `STATISTICAL_SAMPLE`, and the sample's frozen membership is untouched. A COR request is never presented as a statistical selection |
| **Priority + HUMAN_REQUIRED** | Both cases exist, both `queue_source` values survive. The scopes differ, so folding them would force one answer onto two questions |
| **Priority + prior review** | Prior reviews are shown as context and explicitly are not a current answer. A new Government request requires its own determination and QA |
| **Priority + monthly delta** | Independent. A request does not depend on NEW/CHANGED status, and a later delivery does not disturb a request already made |
| **NOT_PRESENT in the current delivery** | Not grounds to refuse. The canonical record is reviewable against the request's scope |

---

## 11. Security

| Control | Position |
|---|---|
| Authentication | every new endpoint refuses unauthenticated callers (13 endpoints certified) |
| RBAC | logging a COR request, amending its deadline and withdrawing it require `program_manager`; human target resolution and supervisor assignment `senior_analyst`; recording a finding, claiming and releasing `reviewer`; QA `qalead`. **Reads sit at the viewer floor** like every other TEFCA read — the authority is on the writes. Asserted against the declared route dependencies, and against `test_rbac_roles::test_no_tefca_read_endpoint_sits_above_the_viewer_floor` |
| IDOR | only the case owner may record its finding (`require_owner`); claim is an atomic conditional update |
| Mass assignment | asserted against the actual request models: no field for reportability, the reviewer, the audit actor, the case status, the entity or the review id |
| Replay | idempotent on the request key; a new COR reference is a new request |
| Concurrency | advisory lock on the request key; atomic claim; append-only decision events |
| Audit | receipt, target resolution, deadline amendments, withdrawal, assignment, determination, QA and reportability all on the trail |

**No new role was created.** The existing ladder represents every authority
Task 5 needs.

---

## 12. Withdrawal

Recorded as an append-only fact, derived thereafter. **Nothing is deleted** —
the request, the case, the observations and every decision event stay. A
withdrawn request refuses new findings.

---

## 13. Section 508

The Entity Review side panel was widened under reader control
(`src/platform/components/SidePanel.js`): a labelled Expand/Collapse button,
`aria-expanded`, an accessible name that states the action, an icon marked
`aria-hidden` so meaning never rests on the glyph, native keyboard operation
because it is a `<button>` inside the existing focus trap, `max-width: 100vw`
for small viewports, and the choice remembered per browser in `localStorage`
inside `try/catch` (storage throws in a private window).

Two states rather than a drag handle: a draggable edge needs pointer precision,
has no keyboard equivalent without inventing one, and would be the only
resizable surface in the product.

**Automated accessibility check: NOT AVAILABLE.** The frontend has no test
harness (`package.json` declares `dev`, `build`, `start` only), so this is a
**structural code-level verification**, and the production build passes.
**Manual and Government 508 review is still required.**

---

## 14. Test evidence — 63 tests

Request authority mandatory · no rule can manufacture a request · no standing
turnaround anywhere in the module · absent deadline is a state · status measured
against the COR's deadline · `DUE_SOON` only when the caller defines it ·
`PAST_DUE` draws no compliance conclusion · amendment keeps the original ·
amendment must name its authority · replay is one case · a new reference is a
new case · ambiguity routes to a human with candidates · unknown organisation
logged and none invented · a target cannot be resolved to a non-existent entity
· a resolved target cannot be switched after review begins · HELD/unpromoted
reviewable without promotion · priority and statistical provenance both survive
· HUMAN_REQUIRED and priority coexist · prior review is context not an answer ·
absent-from-current-delivery is not refused · analyst package is coherent ·
finding requires a determination and is not reportable · only independent QA
releases the result · self-approval blocked · "root cause not determined" is
legitimate · QA RETURN keeps the same case, deadline and evidence · QA ESCALATE
invents no disposition · approved content cannot be edited · one analyst's case
is not another's · multi-entity request · withdrawal preserves everything ·
audit reconstruction · 24-request surge · concurrent submission is one case ·
13 endpoints require authentication · role ladder asserted · no protected field
settable · no deadline default in the transport layer · SAM stays unavailable ·
TIN behind the boundary · no evidence collected by this path · no determination
computed from issue text · a second QA action cannot stand beside a standing approval · reading the report twice creates nothing · D5.1 withholds until QA · D5.1 reports the COR's
deadline and draws no conclusion · the report family still works without a case.

### Mutation-tested — 10 mutations, all detected

| Mutation | Result |
|---|---|
| A a request can be manufactured without COR authority | DETECTED |
| B a 24-hour deadline is supplied when the COR stated none | DETECTED |
| C the same request creates a second case | DETECTED |
| D an ambiguous target resolves to the first candidate | DETECTED |
| E an analyst may approve their own determination | DETECTED |
| F an unavailable source is reported as a clear result | DETECTED |
| G a determination is reportable before QA | DETECTED |
| H a deadline amendment leaves no history | DETECTED |
| I two analysts may claim one case | DETECTED |
| J priority provenance is replaced by statistical provenance | DETECTED |

Production code restored byte-identically after each.

---

## 15. Government readiness — READ ONLY, nothing created

**Priority selection is the COR's. AGT does not choose targets**, and no
Government entity was assessed for suitability as a priority review.

| | |
|---|---|
| Government priority requests created | **0** |
| Government priority cases created | **0** |
| Government assignments / decisions / QA events / reports created | **0 / 0 / 0 / 0** |
| Entities resolvable through the canonical ladder | 23,756 registry entities, 96,803 identifiers |
| QHIN attribution available | 23,562 `managed_by_qhin` edges across 11 QHINs |
| Delivered records reviewable pre-promotion | 4 HELD, all unpromoted |
| Recorded verification observations available to an analyst | 43 |
| Analyst / QA queue | 43 historical review records, 0 assigned, 0 reportable, 0 decision events |
| Existing report artifacts | 4 |
| D5.1 template | present, and now gated |
| D5.2 template | present (pre-existing) |

---

## 16. Open Government questions

Carried from the COR activation package, still unanswered:

1. **P1** — preferred request channel and acknowledgement expectations.
2. **P2** — anticipated surge periods to plan staffing around.
3. **P3** — for a multi-entity request, does the deadline apply per request or
   per entity.
4. **P4** — whether the COR wants a standing target turnaround reported in
   addition to per-request deadlines.

Arising from this gate:

5. **Withdrawal authority** — the procedure does not state who may withdraw a
   request, or what becomes of work already done. Implemented as a recorded,
   attributable act that preserves everything; the authority is unconfirmed.

Nothing else was manufactured, and the Step #13 sampling questions are unchanged.

---

## 17. Known limitations, stated rather than implied

1. **Request-level provenance is duplicated per target.** `tefca_priority_cases`
   is one row per (request, organisation), so a three-organisation request
   repeats the COR reference, requester, receipt time and deadline three times.
   A separate request table would remove the duplication and would require a
   migration; that was not judged to be worth one. Rows sharing a `cor_reference`
   are consistent because they are written by one call each with the same inputs.
2. **The legacy priority surface is unchanged.** `PATCH /api/tefca/priority-cases/{id}`,
   `execute_priority_review` and `POST /api/tefca/arc/priority-review` still
   exist and still bypass the QA gate. They are not the operational path, they
   have no rows, and Step #14 did not depend on them — but they are reachable,
   and that is recorded here rather than left to be discovered.
3. **`sla.py` keeps a 3-day "priority" window** for the sampled-review
   dashboard. It is not contractual and must never be applied to a Task 5
   request. A test pins that the priority path does not use it.
4. **Surge throughput is not load tested.**
5. **No automated accessibility harness exists** in the frontend.
