# A3 - QA independence, maker/checker and deadline/notification controls

Status: DRAFT / PROPOSAL. Every behaviour change below is behind a flag that
defaults OFF. No migration. Nothing is sent, scheduled or stored by the
deadline or notification code. Baseline: origin/main 0cef73b5.

Sources: Task 2 methodology (10/07/2026) classification table and taxonomy;
Review_Output_2026-10-07 `01_Requirement_to_Code_Matrix_source.md` (rows A.2
Adjudication, B3, B4, Indeterminate, taxonomy; items 6 and 12);
`qa-evidence/2026-10-06-report-deploy-verification/EVIDENCE-RECORD-2026-10-06.md`
(release maker/checker, open decision O-01).

## 1. Implemented vs proposal

| Item | Matrix / document basis | State | Flag (default OFF) |
|---|---|---|---|
| SoD exception grantor must be a real, active admin, not the QA actor, not the analyst | Matrix A.2 gap "SoD exception self-attestable"; item 12 | Implemented, flag-gated. Closes a verification gap in an existing approved control (analyst != QA); not a new policy | `ENABLE_SOD_GRANTOR_VERIFICATION` |
| Machine denial codes on QA / determination / supersede refusals (`X-Denial-Code` header; `QaGateRefused.code`) | "every denial auditable" | Implemented. Codes are additive; status (409) and message text unchanged | always on (header only) |
| Durable audit row for each denial with its code | same | Implemented | `ENABLE_DENIAL_AUDIT` |
| Report generator != releaser (PM_REVIEWED, READY_FOR_DELIVERY) | Evidence record: no approved requirement; owner decision O-01 | PROPOSAL, implemented flag-gated. Fails closed if generator unknown | `ENABLE_RELEASE_GENERATOR_SEPARATION` |
| Read-before-release: READY_FOR_DELIVERY requires `acknowledge_read=true`, stored in history | Matrix item 12 (release weaker than document implies) | PROPOSAL. An attestation only: it does not prove the report was opened | `ENABLE_RELEASE_READ_ACK` |
| Deadline config model, business-day math, due-time calculator, dry-run register + admin endpoint | Task 2 B3/B4/Indeterminate; matrix item 6 "no deadlines" | Implemented as pure code; endpoint `POST /api/tefca/arc/deadlines/dry-run` | `ENABLE_DEADLINE_DRY_RUN` |
| Notification templates + transport interface | Task 2 B4 "agreed template" | INACTIVE. Templates are drafts; no concrete transport exists | `ENABLE_NOTIFICATIONS` + `NOTIFICATION_TRANSPORT` |

The DB trigger `trg_review_event_sod` is unchanged. It still only checks
grantor != QA actor; the admin check is application-level, because a trigger
change is a governed migration (recommended follow-up if the owner wants
defence in depth).

Not changed: B1/B4 policy, classification rules, the existing QA state machine,
`release_gates` (still test-only; wiring it is a separate decision), PM release
role floor.

## 2. Deadlines: what the document fixes and what it leaves open

Stated (carried as `RULE_DEFAULTS` in `app/tefca_registry/deadlines.py`):

| Rule | Document text | Amount / unit |
|---|---|---|
| B4_NOTIFY_ONC | B4: "notified by email within 24 hours (1 business day)"; taxonomy "Within 24 hrs (1 business day)" | 24 hours OR 1 business day: **unit unresolved, no default** |
| B3_AGT_RESEARCH | B3: "research within one business day"; taxonomy "days 1-2 ... research" | 1 business day (taxonomy implies up to 2) |
| B3_ONC_DATA_RESPONSE | B3: "respond within 8 business days"; taxonomy "days 3-10 ... response window" | 8 business days |
| B3_TOTAL_CYCLE | taxonomy "10 business days" | 10 business days |
| INDETERMINATE_RE_REVIEW | "Reviewed once the source is restored, normally within 1 business day" | 1 business day |
| SOURCE_OUTAGE_ESCALATION | "unavailable for more than 3 business days ... escalate to the COR" | 3 business days |

Open (no default chosen; each rule reports `UNCONFIGURED` until set):
1. B4: clock hours vs one business day.
2. The clock-start event for EVERY rule (classification, analyst finalisation,
   QA approval, finding confirmed, notification sent, source restored...).
   Closed vocabulary `CLOCK_START_EVENTS`; mapping to app events is a later step.
3. 8 vs 10 business days: 8 equals the 10-day cycle only if the response clock
   starts after 2 days of research; depends on item 2.
4. Business calendar: weekends only built in; holidays, time zone and cut-off
   hour are configuration (`holidays` in `DEADLINE_CONFIG_JSON`).
5. "more than 3 business days" (strict) vs "3 business days" (due at 3).
6. Whether B4 timing runs from an unconfirmed hit or only a confirmed finding
   (matrix B4 row: "B4 candidate" vs "confirmed").

Config shape (`DEADLINE_CONFIG_JSON`, validated; unknown keys raise):
`{"holidays":["YYYY-MM-DD"],"rules":{"B4_NOTIFY_ONC":{"unit":"clock_hours","clock_start_event":"FINDING_CONFIRMED","amount":24}}}`

## 3. Conflicts with existing display-only numbers (not changed)

| Code | Values | Conflict with the document |
|---|---|---|
| `app/tefca_registry/sla.py` `REVIEW_SLA_DAYS` | weekly 7, quarterly 90, priority 3 (calendar days from sample draw) | Document has no such windows; it uses business days and bucket-based timings |
| `app/Tefca/qa_engine.py` `SLA_TARGETS` | critical 2, high 5, medium 10, low 21 (calendar days) | No match to 24h / 1 / 3 / 8 / 10 business days; severity scale is not the B1-B4 taxonomy |
| `app/Tefca/validation_engine.py` `recommended_deadline` | B2 30, B3 21, B4 10 (calendar days) | Document: B2 none, B3 8-10 business days, B4 24h/1 business day. Matrix: display data only |

`deadlines.conflicts_with_existing_display_code()` reads the live constants so
this table cannot drift silently. The new calculator does not read or replace
those values; they continue to drive only their current displays.

## 4. Notifications

Templates (all `[DRAFT]`, placeholders only): B4 ONC notification, B3
participant response request, outage escalation to COR, Indeterminate
re-review reminder. The document refers to an "agreed template" for B4; none
exists in the repository, so these are for owner review.

`notifications.dispatch()` sends only if `ENABLE_NOTIFICATIONS` is true AND
`NOTIFICATION_TRANSPORT` names a transport registered in-process AND not under
pytest. No SMTP/API transport is implemented. B4 notification therefore remains
a manual human act, consistent with matrix B4 row.

## 5. Decisions needed from the owner

1. O-01: adopt generator != releaser and/or read-acknowledgement (turn on the flags).
2. B4 unit (24 clock hours vs 1 business day) and each rule's clock-start event.
3. Holiday calendar, time zone and cut-off hour.
4. Approve a B4 notification template and, separately, any real transport.
5. Whether to add the admin-grantor check to the DB trigger (migration).

## 6. Tests

`tests/test_a3_qa_independence_and_deadlines.py` (34, DB-free): flags default
off; legacy behaviour unchanged when off; grantor machine codes; release
separation/ack with codes and history; denial audit off/on; business-day math;
unresolved config yields UNCONFIGURED; configured computation; strict config
validation; dry-run sends nothing; notifications suppressed in every gate.
