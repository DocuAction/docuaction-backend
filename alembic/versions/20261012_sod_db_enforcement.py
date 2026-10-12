"""Database enforcement of "no self-QA" and append-only guards on review_decision_events.

Revision ID: 20261012_sod_db_enforcement
Revises: 20261009_iqvia_affil_org_indexes
Create Date: 2026-10-12

STATUS: DRAFT. Written for review. NOT applied to DEV or PROD by this change; it must
go through the governed migration pipeline after the prerequisites in
docs/SOD_DB_ENFORCEMENT_MIGRATION_NOTES.md are verified.

WHY THIS EXISTS
---------------
Migration 20260825_qa_events created `trg_review_event_sod` / `review_event_enforce_sod()`,
which refuses a QA event written by the same person as the determination it reviews -- EXCEPT
when `sod_exception_granted_by` and `sod_exception_reason` are present and the grantor is
somebody else. Nothing proves the named grantor consented: the values are copied from the
request body (CWE-863). PR #146 closes that in the service (`qa_gate.submit_qa_review`). That is
an application-only control; a future code path, a script or a direct SQL INSERT can still skip
it and the old trigger would accept `(any other UUID, any text)` as an "exception".

WHAT THIS CHANGES
-----------------
1. `review_event_enforce_sod()` is replaced (CREATE OR REPLACE, same name, same trigger
   `trg_review_event_sod`, still BEFORE INSERT FOR EACH ROW). Behaviour is identical to the
   original except for one branch:

       original:  analyst = NEW.actor_user_id  -> refuse unless a different grantor + reason
       now:       analyst = NEW.actor_user_id  -> refuse, whatever sod_exception_* contain

   "Self-QA" means exactly what the original body meant: a row with event_type = 'QA_REVIEW'
   whose actor_user_id equals the actor of the most recent ANALYST_DETERMINATION or
   SUPERSEDING_DETERMINATION on the same review_id with a lower sequence_number. Non-QA events
   are returned untouched, and a QA event with no preceding determination is still refused.
   The exception text keeps the original prefix ("segregation of duties: ...") so existing
   assertions on it keep passing.

2. Append-only guards on `review_decision_events` (the model and migration 20260825 both document
   it as append-only; an audit of app/ and scripts/ found no UPDATE or DELETE of these rows, the
   only writers are INSERTs in `qa_gate.record_analyst_determination`, `submit_qa_review` and
   `supersede`; supersession is a NEW row naming `supersedes_decision_id`):

       trg_review_event_append_only_rows  BEFORE UPDATE OR DELETE ... FOR EACH ROW
       trg_review_event_append_only_trunc BEFORE TRUNCATE         ... FOR EACH STATEMENT

   both executing `review_event_append_only_guard()`:

       UPDATE    refused for EVERY role, the table owner and superusers included. Nothing in the
                 product ever updates an event.
       DELETE    refused unless the calling role holds the privileges of the table owner.
       TRUNCATE  refused unless the calling role holds the privileges of the table owner.

   The owner carve-out for DELETE/TRUNCATE is deliberate and narrow: the runtime role must never
   be able to remove events, but governed maintenance and the repository's own test-cleanup (which
   runs as a privileged role and deletes events before the review_records they reference) need a
   way to remove synthetic rows. The owner can in any case `ALTER TABLE ... DISABLE TRIGGER`, so
   the carve-out hands out no power that role did not already have. It is the residual risk
   documented in the notes, not a hidden one.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
* It does not read, scan, rewrite or delete a single row. The new SoD behaviour fires only on
  new INSERTs. Historical events that carry an exception remain exactly as they are, readable
  and still part of the decision history; they are not retrospectively invalidated.
* It does not GRANT or REVOKE anything. The runtime role's privileges on this table are not
  verified on DEV (see the notes); changing an ACL I have not read would be a guess, and the
  trigger is the control. Revoking UPDATE/DELETE/TRUNCATE from the runtime role is a recommended
  follow-up AFTER the catalog queries in the notes have been run.
* It does not change ownership of the table or of the function, and it does not touch any other
  table, trigger, view or function. The existing trigger `trg_review_event_sod` is not dropped
  or re-created: only the function body it already calls is replaced.
* It does not build an exception-grant feature (see docs/SOD_EXCEPTION_AUTHORIZATION_DESIGN.md).

PREFLIGHT (fails before any DDL, with a named error)
----------------------------------------------------
The statements need DDL rights the migration role may not have on a database whose objects were
created by the runtime role (as happened to other tables on DEV). `upgrade()` checks first:
  - the table exists (a missing function or trigger is created; an existing function must be replaceable);
  - the migration role holds the privileges of the function's owner (CREATE OR REPLACE FUNCTION
    needs ownership) and may CREATE TRIGGER on the table;
  - the runtime role (DB_APP_ROLE, when set and present) does NOT hold the privileges of the
    table or function owner. If it did it could drop the new guards or replace the function, and
    the migration would be installing a control it cannot rely on.
After the DDL it verifies the triggers exist and are enabled and fails (rolling the transaction
back) otherwise.

DOWNGRADE
---------
Restores the original function body (self-QA with a different grantor and a reason is accepted
again), drops the two guard triggers and the guard function. It touches no row. It cannot "undo"
anything that happened while the new version was in force: there is nothing to undo, because no
self-QA event could have been inserted. Rows are never altered by either direction.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op

revision = "20261012_sod_db_enforcement"
down_revision = "20261009_iqvia_affil_org_indexes"
branch_labels = None
depends_on = None

TABLE = "review_decision_events"
SOD_FUNCTION = "review_event_enforce_sod"
SOD_TRIGGER = "trg_review_event_sod"
GUARD_FUNCTION = "review_event_append_only_guard"
GUARD_ROW_TRIGGER = "trg_review_event_append_only_rows"
GUARD_TRUNC_TRIGGER = "trg_review_event_append_only_trunc"

# New body. Identical to the 20260825 body except the self-QA branch, which no longer consults
# sod_exception_granted_by / sod_exception_reason. Plain plpgsql, SECURITY INVOKER (default).
SOD_FUNCTION_NEW_SQL = f"""
CREATE OR REPLACE FUNCTION {SOD_FUNCTION}() RETURNS trigger AS $$
DECLARE
    analyst UUID;
BEGIN
    IF NEW.event_type <> 'QA_REVIEW' THEN
        RETURN NEW;
    END IF;

    -- The analyst whose determination this QA event reviews: the most recent
    -- determination event on this review before this one.
    SELECT e.actor_user_id INTO analyst
    FROM   review_decision_events e
    WHERE  e.review_id = NEW.review_id
      AND  e.event_type IN ('ANALYST_DETERMINATION','SUPERSEDING_DETERMINATION')
      AND  e.sequence_number < NEW.sequence_number
    ORDER  BY e.sequence_number DESC
    LIMIT  1;

    IF analyst IS NULL THEN
        RAISE EXCEPTION
          'QA event on review % has no preceding determination to review',
          NEW.review_id;
    END IF;

    -- 2026-10-12: no self-QA, whatever the exception columns contain. They are
    -- self-attested request data; nothing proves the named grantor consented.
    IF analyst = NEW.actor_user_id THEN
        RAISE EXCEPTION
          'segregation of duties: % may not QA their own determination on % (self-attested exception fields are not accepted)',
          NEW.actor_email, NEW.review_id;
    END IF;

    RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

# The body installed by 20260825_qa_events, restored verbatim by downgrade().
SOD_FUNCTION_OLD_SQL = f"""
CREATE OR REPLACE FUNCTION {SOD_FUNCTION}() RETURNS trigger AS $$
DECLARE
    analyst UUID;
BEGIN
    IF NEW.event_type <> 'QA_REVIEW' THEN
        RETURN NEW;
    END IF;

    -- The analyst whose determination this QA event reviews: the most recent
    -- determination event on this review before this one.
    SELECT e.actor_user_id INTO analyst
    FROM   review_decision_events e
    WHERE  e.review_id = NEW.review_id
      AND  e.event_type IN ('ANALYST_DETERMINATION','SUPERSEDING_DETERMINATION')
      AND  e.sequence_number < NEW.sequence_number
    ORDER  BY e.sequence_number DESC
    LIMIT  1;

    IF analyst IS NULL THEN
        RAISE EXCEPTION
          'QA event on review % has no preceding determination to review',
          NEW.review_id;
    END IF;

    IF analyst = NEW.actor_user_id THEN
        -- Permitted ONLY under an explicit, separately-granted exception.
        IF NEW.sod_exception_granted_by IS NULL
           OR NEW.sod_exception_reason IS NULL
           OR NEW.sod_exception_granted_by = NEW.actor_user_id THEN
            RAISE EXCEPTION
              'segregation of duties: % may not QA their own determination on %',
              NEW.actor_email, NEW.review_id;
        END IF;
    END IF;

    RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

# UPDATE: never. DELETE / TRUNCATE: only for a role holding the table owner's privileges (which
# includes superusers). `current_user` is the effective role, so SET ROLE / the migration role's
# startup `role` setting are honoured.
GUARD_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $guard$
DECLARE
    owner_ok boolean;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
            'review_decision_events_append_only: events are never updated; record a new event instead (event '
            || OLD.id || ')';
    END IF;

    SELECT pg_has_role(current_user, c.relowner, 'USAGE') INTO owner_ok
    FROM   pg_class c WHERE c.oid = TG_RELID;

    IF NOT COALESCE(owner_ok, false) THEN
        RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
            'review_decision_events_append_only: ' || TG_OP
            || ' is not permitted for role ' || current_user;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NULL;  -- TRUNCATE (statement level): return value is ignored
END
$guard$
"""

SOD_TRIGGER_SQL = (
    f'CREATE TRIGGER {SOD_TRIGGER} BEFORE INSERT ON "{TABLE}" '
    f"FOR EACH ROW EXECUTE FUNCTION {SOD_FUNCTION}()")
GUARD_ROW_TRIGGER_SQL = (
    f'CREATE TRIGGER {GUARD_ROW_TRIGGER} BEFORE UPDATE OR DELETE ON "{TABLE}" '
    f"FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()")
GUARD_TRUNC_TRIGGER_SQL = (
    f'CREATE TRIGGER {GUARD_TRUNC_TRIGGER} BEFORE TRUNCATE ON "{TABLE}" '
    f"FOR EACH STATEMENT EXECUTE FUNCTION {GUARD_FUNCTION}()")


class SodDbEnforcementPreconditionError(RuntimeError):
    """A precondition failed; nothing was changed."""


def _offline() -> bool:
    return context.is_offline_mode()


def _preflight(bind) -> None:
    """Fail before any DDL, with the exact remedy, when the objects are not ours to change."""
    if TABLE not in sa.inspect(bind).get_table_names():
        raise SodDbEnforcementPreconditionError(f"{TABLE} does not exist; run the chain from 20260825_qa_events.")
    proc_owner = bind.execute(sa.text(
        "select p.proowner::regrole::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where p.proname = :f and n.nspname = current_schema()"), {"f": SOD_FUNCTION}).scalar()
    table_owner = bind.execute(sa.text(
        "select c.relowner::regrole::text from pg_class c where c.oid = cast(:t as regclass)"),
        {"t": TABLE}).scalar()
    # A missing function/trigger (a database built from the models and stamped, never run through
    # 20260825) is not an error: this revision creates what is missing, which only strengthens the
    # control. An EXISTING function must be one the migration role may replace.
    if proc_owner is not None and not bind.execute(
            sa.text("select pg_has_role(current_user, cast(:o as regrole), 'USAGE')"), {"o": proc_owner}).scalar():
        raise SodDbEnforcementPreconditionError(
            f"{SOD_FUNCTION}() is owned by {proc_owner}, and the migration role ({bind.execute(sa.text('select current_user')).scalar()}) "
            f"does not hold that role's privileges, so CREATE OR REPLACE FUNCTION would fail. An operator "
            f"must run ALTER FUNCTION {SOD_FUNCTION}() OWNER TO <migration role> (see the notes). Nothing was changed.")
    if not bind.execute(sa.text(
            "select pg_has_role(current_user, cast(:o as regrole), 'USAGE') "
            "or has_table_privilege(current_user, cast(:t as regclass), 'TRIGGER')"),
            {"o": table_owner, "t": TABLE}).scalar():
        raise SodDbEnforcementPreconditionError(
            f"{TABLE} is owned by {table_owner}; the migration role can neither act as that role nor hold "
            f"TRIGGER on the table, so CREATE TRIGGER would fail. Nothing was changed.")
    app_role = os.getenv("DB_APP_ROLE", "").strip()
    if app_role and bind.execute(sa.text("select 1 from pg_roles where rolname = :r"), {"r": app_role}).first():
        for what, owner in (("table", table_owner), ("function", proc_owner)):
            if owner is not None and bind.execute(sa.text("select pg_has_role(cast(:a as regrole), cast(:o as regrole), 'USAGE')"),
                            {"a": app_role, "o": owner}).scalar():
                raise SodDbEnforcementPreconditionError(
                    f"the runtime role {app_role} holds the privileges of the {what} owner ({owner}) of "
                    f"{TABLE}/{SOD_FUNCTION}(). It could disable the guards or replace the function, so this "
                    f"migration would install a control it cannot rely on. Transfer ownership to the owner "
                    f"role through the DBA process first (see the notes). Nothing was changed.")


def _verify(bind) -> None:
    for trg in (SOD_TRIGGER, GUARD_ROW_TRIGGER, GUARD_TRUNC_TRIGGER):
        # tgenabled is a "char"; cast to text so the driver returns a str.
        enabled = bind.execute(sa.text(
            "select tgenabled::text from pg_trigger where tgrelid = cast(:t as regclass) and tgname = :g "
            "and not tgisinternal"), {"t": TABLE, "g": trg}).scalar()
        if enabled != "O":
            raise SodDbEnforcementPreconditionError(
                f"trigger {trg} on {TABLE} is missing or not enabled (tgenabled={enabled!r}). Refusing to continue.")
    body = bind.execute(sa.text(
        "select p.prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where p.proname = :f and n.nspname = current_schema()"), {"f": SOD_FUNCTION}).scalar() or ""
    if "sod_exception_granted_by" in body or "sod_exception_reason" in body:
        raise SodDbEnforcementPreconditionError(
            f"{SOD_FUNCTION}() still consults the sod_exception columns; the replacement did not take effect.")


def upgrade() -> None:
    if _offline():
        for stmt in (SOD_FUNCTION_NEW_SQL, f'DROP TRIGGER IF EXISTS {SOD_TRIGGER} ON "{TABLE}"', SOD_TRIGGER_SQL,
                     GUARD_FUNCTION_SQL,
                     f'DROP TRIGGER IF EXISTS {GUARD_ROW_TRIGGER} ON "{TABLE}"', GUARD_ROW_TRIGGER_SQL,
                     f'DROP TRIGGER IF EXISTS {GUARD_TRUNC_TRIGGER} ON "{TABLE}"', GUARD_TRUNC_TRIGGER_SQL):
            op.execute(stmt)
        return
    bind = op.get_bind()
    _preflight(bind)
    # DDL only. No SELECT/UPDATE/DELETE/INSERT on review_decision_events rows is issued.
    sod_trigger_present = bool(bind.execute(sa.text(
        "select 1 from pg_trigger where tgrelid = cast(:t as regclass) and tgname = :g and not tgisinternal"),
        {"t": TABLE, "g": SOD_TRIGGER}).first())
    op.execute(SOD_FUNCTION_NEW_SQL)
    if not sod_trigger_present:
        op.execute(SOD_TRIGGER_SQL)   # same definition 20260825 used; only when it is missing
    op.execute(GUARD_FUNCTION_SQL)
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_ROW_TRIGGER} ON "{TABLE}"')
    op.execute(GUARD_ROW_TRIGGER_SQL)
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRUNC_TRIGGER} ON "{TABLE}"')
    op.execute(GUARD_TRUNC_TRIGGER_SQL)
    _verify(bind)


def downgrade() -> None:
    """Restore the 20260825 function body; drop the guards. No row is read or changed."""
    op.execute(SOD_FUNCTION_OLD_SQL)
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRUNC_TRIGGER} ON "{TABLE}"')
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_ROW_TRIGGER} ON "{TABLE}"')
    op.execute(f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}()")
