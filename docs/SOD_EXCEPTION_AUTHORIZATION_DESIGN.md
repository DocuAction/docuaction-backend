# SoD exception authorization - design proposal

Status: PROPOSAL. Nothing in this document is implemented, migrated or deployed.
Companion change: `fix/sod-exception-fail-closed` (self-QA exception requests are
refused unconditionally with `SOD_EXCEPTION_NOT_AUTHORIZED`).

## 1. Problem

`POST /api/tefca/arc/reviews/{review_id}/qa` accepted `sod_exception_granted_by`
(a UUID) and `sod_exception_reason` from the request body. When the QA actor was
also the determination actor, the event was accepted if the UUID differed from the
actor. Nothing proved that the named person was an admin, was active, existed, or
had consented (CWE-863). The database trigger `trg_review_event_sod` only checks
`grantor != actor`. Verifying that the UUID names an active admin (draft #139,
flag `ENABLE_SOD_GRANTOR_VERIFICATION`) still does not prove the admin authorized
the exception, because the QA actor writes the UUID.

## 2. Interim control (this PR)

Until a genuine mechanism exists, a QA request by the determination actor is
refused with 409, `X-Denial-Code: SOD_EXCEPTION_NOT_AUTHORIZED` (exception fields
supplied) or `SOD_SELF_QA` (not supplied). Unconditional, no flag. Independent QA
is unchanged. The database trigger is unchanged and still permits the exception path at the database level (see 2.1).
The columns
`sod_exception_granted_by` / `sod_exception_reason` remain for history.

### 2.1 Scope of the interim control and the residual database-enforcement gap

The interim control is **application-level (service layer) only**. It does not
remove the exception path from the database. Verified by reading
`alembic/versions/20260825_qa_decision_events.py`:

- `trg_review_event_sod` is a `BEFORE INSERT` row trigger calling
  `review_event_enforce_sod()`. For a `QA_REVIEW` row whose actor equals the
  analyst of the preceding determination, it raises **unless**
  `sod_exception_granted_by IS NOT NULL AND sod_exception_reason IS NOT NULL AND
  sod_exception_granted_by <> actor_user_id`.
- It therefore *permits* a self-QA insert that carries any non-null UUID other than
  the actor plus any non-null reason. It does not check that the UUID exists, is an
  admin, is active, or consented. A direct database write (or any future code path
  that inserts a `QA_REVIEW` event without going through `qa_gate.submit_qa_review`)
  can still bypass segregation of duties this way.
- The trigger is `INSERT`-only. The migration contains no `UPDATE`/`DELETE` guard
  and no `REVOKE`/`GRANT` for `review_decision_events`; "append-only" is a design
  intent in the docstring, not a database-enforced property in this migration.
- Application code: the only place that constructs `ReviewDecisionEvent` rows is
  `app/tefca_registry/qa_gate.py` (determination, QA, supersede); `qa_gate`
  `submit_qa_review` is the only writer of `sod_exception_granted_by`. Other modules
  only read events. (Read of `app/` at main bc5f7d71.)
- Which DEV roles can write `review_decision_events`: UNVERIFIED. The as-built
  `dev_verified.md` records no catalog read of this table's owner/ACL or of the
  trigger's state on DEV; the application role normally writes it via the service.

Closing the gap requires a governed Alembic migration (described, NOT written or
applied here): replace `review_event_enforce_sod()` so a self-QA row is accepted
only if it references an existing, unrevoked, unexpired, unconsumed grant row
(section 3.3) whose `granted_by` is a third person; with the grants table absent,
the safest interim database change is to drop the exception branch entirely
(self-QA always raises). Add `UPDATE`/`DELETE` guards or revoke those privileges
from the application role. Needs an owner decision (section 5) and the governed
migration process.

## 3. Proposed mechanism: authenticated, scoped, single-use grant

### 3.1 Principle
The grantor is the authenticated principal of a separate request, never a value
in the QA request body. The QA event only *consumes* a pre-existing grant.

### 3.2 Grant endpoint
`POST /api/tefca/arc/reviews/{review_id}/sod-exceptions` (admin role only,
`require_role("admin")`).

Body: `determination_event_id` (UUID), `grantee_user_id` (the QA actor allowed to
use it), `reason` (min 10 chars), `expires_at` (optional; capped by policy).

Server-side:
- `granted_by` = authenticated admin id (from the token, not the body).
- Refuse if `granted_by` = grantee, or `granted_by` = the determination actor
  (grantor must be a third person).
- Refuse if `determination_event_id` is not the latest determination of
  `review_id` (scope = exact review + exact determination event).
- Refuse if an unrevoked, unexpired, unconsumed grant already exists for the pair
  (expired ones are revoked first, in the same transaction; see 3.3).
- Write an audit row `sod_exception_granted`.

Revocation: `DELETE .../sod-exceptions/{grant_id}` (admin) sets `revoked_at`,
`revoked_by`; audit row `sod_exception_revoked`.

### 3.3 Table (described only; NOT written or applied)
`review_sod_exception_grants`:
`id uuid pk`, `review_id text fk`, `determination_event_id uuid fk
review_decision_events`, `grantee_user_id uuid fk`, `granted_by uuid fk`,
`reason text check(length>=10)`, `valid_from timestamptz default now()`,
`expires_at timestamptz not null`, `revoked_at timestamptz`, `revoked_by uuid`,
`consumed_at timestamptz`, `consumed_by_event_id uuid`, `created_at`.
Constraints: `granted_by <> grantee_user_id`; `expires_at > valid_from`; partial
unique index on `(review_id, determination_event_id, grantee_user_id)` where
`revoked_at is null and consumed_at is null`.
An expired grant is still unrevoked and unconsumed, so it would block the unique
index. Replacement is therefore transaction-safe by construction: the grant
endpoint, in one transaction and after `SELECT ... FOR UPDATE` on the matching
rows, first sets `revoked_at = now()`, `revoked_by = <system marker>` on any
existing grant for the same key whose `expires_at <= now()`, audits it as
`sod_exception_expired_revoked`, and only then inserts the new grant. A still-valid
unconsumed grant is not replaced (the request is refused; the admin must revoke it
explicitly). A periodic sweep may also revoke expired rows, but correctness does
not depend on it.

### 3.4 Consumption by the QA event
Validation order in `submit_qa_review`, all before the QA event is created:
1. Existing checks (review exists, rationale, qa_action, determination exists,
   no standing APPROVE).
2. If actor != determination actor: ordinary path, no grant needed or consulted.
3. If actor == determination actor: look up the grant by
   `(review_id, determination_event_id, grantee_user_id = actor)` `FOR UPDATE`.
   Refuse with a distinct code if none (`SOD_EXCEPTION_NOT_AUTHORIZED`), not yet
   valid, expired (`SOD_EXCEPTION_EXPIRED`), revoked (`SOD_EXCEPTION_REVOKED`),
   already consumed (`SOD_EXCEPTION_CONSUMED`), or the granting admin is no longer
   an active admin (`SOD_EXCEPTION_GRANTOR_INVALID`).
4. Create the QA event with `sod_exception_granted_by = grant.granted_by` (from
   the grant row) and `sod_exception_reason = grant.reason`; set
   `consumed_at/consumed_by_event_id` in the same transaction.
5. Audit row `sod_exception_consumed`.
The request-body fields `sod_exception_granted_by` / `sod_exception_reason` are
ignored (or rejected with 422) once this exists.

### 3.5 Database requirement (migration, not written here)
A new Alembic revision is required: the new table, grants to the application and
owner roles consistent with existing review tables, and a possible extension of
`trg_review_event_sod` so a self-QA event is only insertable when the referenced
grant exists, is unconsumed/unexpired/unrevoked and `granted_by` differs from both
actor and determination actor (defence in depth if a future code path bypasses
the service). No migration is authored or applied in this change; it needs the
governed migration process and an owner decision on the points below.

## 4. Tests required with the mechanism
Unit (DB-free): each refusal code above; grantor taken from the token; body
fields ignored; single use; scope (wrong review, wrong determination event after
supersession). DB: trigger refuses self-QA without a valid grant; concurrent
consumption races (only one succeeds).

## 5. Open decisions for the AGT owner
1. Should SoD exceptions exist at all? The fail-closed interim is a complete
   answer if the answer is no; in that case the columns and this design can be
   retired.
2. Who may grant: any admin, or a named subset? Must the grantor differ from the
   program owner / analyst's line manager?
3. Expiry: default and maximum length (suggested: hours, not days, single use).
4. Is a second approver or a post-hoc review of every consumed exception
   required? Where is the exception report surfaced?
5. Notification of a grant or consumption (no transport exists today).
6. Whether exceptions are allowed in production at all or only in DEV/QA.
