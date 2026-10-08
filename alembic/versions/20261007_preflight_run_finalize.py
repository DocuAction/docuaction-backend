"""Let the runtime role FINALIZE a preflight run (RUNNING -> COMPLETE | FAILED), and nothing else.

Revision ID: 20261007_preflight_run_finalize
Revises: 20261006_snapshot_bookkeeping
Create Date: 2026-10-07

WHY THIS EXISTS
---------------
First found on DEV on 2026-10-06: `POST /api/tefca/rce/deliveries/{intake}/preflight` returned HTTP 500 on every
call and no run was persisted. The server log read

    asyncpg.exceptions.InsufficientPrivilegeError: permission denied for table rce_preflight_run
    (raised at the final `db.commit()` of preflight.run_preflight, then masked by PendingRollbackError)

`run_preflight` inserts the run as RUNNING, inserts its findings and normalizations, and finally UPDATEs the run to
COMPLETE (or FAILED) with the counts, the classification gate and the summary. Migration
20261003_preflight_shadow_workspace granted the runtime role only SELECT, INSERT on every table it created
(append-only by grant), so that last UPDATE was refused. Every database-backed preflight test connects as a
superuser, so none could see it. Browser symptom: "Failed to fetch" (the 500 carries no CORS headers).

Only `rce_preflight_run` is ever updated by application code; the other six tables of that migration are insert-only
and stay exactly that.

WHAT THIS DOES
--------------
1. GRANT UPDATE on exactly the eight finalization columns of rce_preflight_run to the runtime role:
   status, classification_gate, records_evaluated, findings_count, normalizations_count, summary, error, completed_at.
2. Installs a row guard (`trg_rce_preflight_run_guard`), which applies to every role including the owner:
       UPDATE  only while OLD.status = 'RUNNING', only to COMPLETE or FAILED, and nothing outside the eight columns
               may differ; a finished run can never be edited again
       DELETE  refused unless the run is still RUNNING (never reached by the application)
   The comparison is `to_jsonb(NEW) - <finalization columns>`, so a column added later is protected by default.
3. Verifies after installing, and FAILS (rolling the transaction back) if the runtime role holds table-wide UPDATE, can
   UPDATE any column other than those eight, can UPDATE any of the six other preflight/shadow tables, or the trigger is
   not enabled.
Downgrade revokes the grant and drops the trigger and function. No data is touched.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op

revision = "20261007_preflight_run_finalize"
down_revision = "20261006_snapshot_bookkeeping"
branch_labels = None
depends_on = None

TABLE = "rce_preflight_run"
FINAL_COLUMNS = ("status", "classification_gate", "records_evaluated", "findings_count",
                 "normalizations_count", "summary", "error", "completed_at")
STAYS_APPEND_ONLY = ("rce_preflight_finding", "rce_preflight_normalization", "rce_shadow_comparison",
                     "rce_shadow_finding_delta", "rce_shadow_approval", "rce_successor_publication_event")
GUARD_FUNCTION = "rce_preflight_run_guard"
GUARD_TRIGGER = "trg_rce_preflight_run_guard"

_EXCLUDE_FINAL = "".join(f" - '{c}'" for c in FINAL_COLUMNS)

# Plain plpgsql, not SECURITY DEFINER; messages are concatenated (no % placeholders) so the text survives
# Alembic's statement execution unchanged.
GUARD_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'RUNNING' THEN
            RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
                'rce_preflight_run_immutable: run ' || OLD.id || ' is ' || OLD.status || ' and cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'RUNNING' THEN
        RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
            'rce_preflight_run_immutable: run ' || OLD.id || ' is ' || OLD.status || ' and cannot be changed';
    END IF;
    IF NEW.status NOT IN ('COMPLETE', 'FAILED') THEN
        RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
            'rce_preflight_run_immutable: a RUNNING run may only be finalized to COMPLETE or FAILED (run '
            || OLD.id || ')';
    END IF;
    IF (to_jsonb(NEW){_EXCLUDE_FINAL}) IS DISTINCT FROM (to_jsonb(OLD){_EXCLUDE_FINAL}) THEN
        RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
            'rce_preflight_run_immutable: finalizing a run may only set its completion columns (run '
            || OLD.id || ')';
    END IF;
    RETURN NEW;
END
$guard$
"""

GUARD_TRIGGER_SQL = (
    f'CREATE TRIGGER {GUARD_TRIGGER} BEFORE UPDATE OR DELETE ON "{TABLE}" '
    f"FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()")


class PreflightRunFinalizePreconditionError(RuntimeError):
    """A precondition failed; nothing was changed."""


def _offline() -> bool:
    return context.is_offline_mode()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise PreflightRunFinalizePreconditionError(
            "DB_APP_ROLE is not set. This migration grants a privilege to a named application role and "
            "refuses to guess which role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    role = _app_role()
    grant = f'GRANT UPDATE ({", ".join(FINAL_COLUMNS)}) ON "{TABLE}" TO "{role}"'
    if _offline():
        op.execute(grant)
        op.execute(GUARD_FUNCTION_SQL)
        op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{TABLE}"')
        op.execute(GUARD_TRIGGER_SQL)
        return
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for t in (TABLE,) + STAYS_APPEND_ONLY:
        if t not in tables:
            raise PreflightRunFinalizePreconditionError(f"{t} does not exist; nothing to grant on.")
    if not bind.execute(sa.text("select 1 from pg_roles where rolname = :r"), {"r": role}).first():
        raise PreflightRunFinalizePreconditionError(f"role {role!r} does not exist.")

    op.execute(grant)
    # The grant is only safe together with the row guard, so they are installed in one transaction.
    op.execute(GUARD_FUNCTION_SQL)
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{TABLE}"')
    op.execute(GUARD_TRIGGER_SQL)

    # Verify: exactly these columns on this table, nothing wider, and nothing on the append-only siblings.
    if bind.execute(sa.text("select has_table_privilege(:r, :t, 'UPDATE')"), {"r": role, "t": TABLE}).scalar():
        raise PreflightRunFinalizePreconditionError(
            f"{role} has table-wide UPDATE on {TABLE}; the column-level grant is not the whole story. "
            "Refusing to continue.")
    cols = [r[0] for r in bind.execute(sa.text(
        "select attname from pg_attribute where attrelid = cast(:t as regclass) and attnum > 0 "
        "and not attisdropped order by attnum"), {"t": TABLE}).fetchall()]
    allowed = {c for c in cols if bind.execute(
        sa.text("select has_column_privilege(:r, :t, :c, 'UPDATE')"), {"r": role, "t": TABLE, "c": c}).scalar()}
    if allowed != set(FINAL_COLUMNS):
        raise PreflightRunFinalizePreconditionError(
            f"{role} can UPDATE {sorted(allowed)} on {TABLE}; expected exactly {sorted(FINAL_COLUMNS)}. "
            "Refusing to continue.")
    for t in STAYS_APPEND_ONLY:
        if bind.execute(sa.text("select has_any_column_privilege(:r, :t, 'UPDATE')"), {"r": role, "t": t}).scalar():
            raise PreflightRunFinalizePreconditionError(
                f"{role} can UPDATE {t}, which must stay append-only. Refusing to continue.")
    # tgenabled is a "char"; depending on the driver it comes back as str or bytes, so cast it to text.
    enabled = bind.execute(sa.text(
        "select tgenabled::text from pg_trigger where tgrelid = cast(:t as regclass) and tgname = :g "
        "and not tgisinternal"), {"t": TABLE, "g": GUARD_TRIGGER}).scalar()
    if enabled != "O":
        raise PreflightRunFinalizePreconditionError(
            f"trigger {GUARD_TRIGGER} on {TABLE} is missing or not enabled (tgenabled={enabled!r}); the column "
            "grant must not stand without it. Refusing to continue.")


def downgrade() -> None:
    """Privilege and guard only; no data is touched. Preflight would again fail under the runtime role."""
    role = _app_role()
    op.execute(f'REVOKE UPDATE ({", ".join(FINAL_COLUMNS)}) ON "{TABLE}" FROM "{role}"')
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{TABLE}"')
    op.execute(f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}()")
