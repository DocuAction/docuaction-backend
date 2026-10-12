"""Let the runtime role update the two import-bookkeeping columns of source_snapshot.

Revision ID: 20261006_snapshot_bookkeeping
Revises: 20261004_recheck_jobs
Create Date: 2026-10-06

WHY THIS EXISTS
---------------
First found on DEV on 2026-10-06 (QA case SUN-08): an IQVIA upload never finished. The import
worker logged

    permission denied for table source_snapshot
    [SQL: UPDATE source_snapshot SET metadata=$1::JSONB WHERE source_snapshot.id = $2::UUID]

and the job stayed RUNNING. Migration 20260921_september_snapshot grants the runtime role only
SELECT and INSERT on source_snapshot (append-only by grant), but the importer
(`iqvia_import._import_csv`, `iqvia_routes.run_import_job`) updates a PENDING snapshot's
bookkeeping while it stages: the reference-preflight record and the rejected-row count in
`metadata`, the progress/status/summary keys in `metadata`, and `record_count` ("corrected to the
real total once the file has been read in full"). Every database-backed IQVIA test connects as a
superuser, so nothing ever ran these statements as the runtime role.

WHAT THIS GRANTS, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------
    GRANT UPDATE (record_count, metadata) ON source_snapshot TO <runtime role>

Column-level. Everything that is provenance stays un-updatable by the runtime role: sha256,
source_system, snapshot_label, received_at, intake_id, created_by/at, build_sha, correlation_id,
and the whole approval lifecycle (status, approved_*, approval_ref, reconciliation_*,
supersedes_snapshot_id). Approval, rejection, supersession and rollback remain NEW ROWS that name
what they supersede, exactly as before: the importer never edits an approved snapshot and refuses
to resume into anything but PENDING. No table-wide UPDATE, no DELETE, no TRUNCATE.

A column grant cannot tell a PENDING staging row from an APPROVED one, so on its own it would let the
runtime role rewrite `metadata` / `record_count` of an APPROVED snapshot after the approval. The same
revision therefore adds a database guard trigger (`trg_source_snapshot_guard`, function
`source_snapshot_guard()`), which applies to every role including the owner:

    UPDATE  allowed only when OLD.status = NEW.status = 'PENDING' and nothing except record_count and
            metadata differs; anything else raises `source_snapshot_immutable`
    DELETE  refused for any row that is not PENDING
    INSERT  untouched: approval, rejection, supersession and rollback stay NEW rows

Only an explicit `ALTER TABLE source_snapshot DISABLE TRIGGER` bypasses it, which is deliberate DDL by
the table owner and shows up in the migration/DBA record, not an application code path.

The migration then verifies the result and FAILS (rolling the transaction back) if the runtime
role holds table-wide UPDATE, or column UPDATE on anything but those two columns, so a later
over-broad grant cannot hide behind this one.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op

revision = "20261006_snapshot_bookkeeping"
down_revision = "20261004_recheck_jobs"
branch_labels = None
depends_on = None

TABLE = "source_snapshot"
GRANTED_COLUMNS = ("record_count", "metadata")
GUARD_FUNCTION = "source_snapshot_guard"
GUARD_TRIGGER = "trg_source_snapshot_guard"

# Plain plpgsql, not SECURITY DEFINER: it runs with the caller's rights and only ever reads OLD/NEW.
# `to_jsonb(NEW) - 'a' - 'b'` compares every column except the two bookkeeping ones, so a column added
# later is protected by default instead of silently exempt.
GUARD_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'PENDING' THEN
            RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
                'source_snapshot_immutable: snapshot ' || OLD.id || ' is ' || OLD.status || ' and cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'PENDING' THEN
        RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
            'source_snapshot_immutable: snapshot ' || OLD.id || ' is ' || OLD.status
            || ' and cannot be changed; record a successor row instead';
    END IF;
    IF NEW.status <> OLD.status
       OR (to_jsonb(NEW) - 'record_count' - 'metadata') IS DISTINCT FROM (to_jsonb(OLD) - 'record_count' - 'metadata') THEN
        RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE =
            'source_snapshot_immutable: a PENDING snapshot may only change record_count and metadata (snapshot '
            || OLD.id || ')';
    END IF;
    RETURN NEW;
END
$guard$
"""

GUARD_TRIGGER_SQL = (
    f'CREATE TRIGGER {GUARD_TRIGGER} BEFORE UPDATE OR DELETE ON "{TABLE}" '
    f"FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()")


class SnapshotBookkeepingPreconditionError(RuntimeError):
    """A precondition failed; nothing was changed."""


def _offline() -> bool:
    return context.is_offline_mode()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise SnapshotBookkeepingPreconditionError(
            "DB_APP_ROLE is not set. This migration grants a privilege to a named application "
            "role and refuses to guess which role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    role = _app_role()
    if _offline():
        op.execute(f'GRANT UPDATE ({", ".join(GRANTED_COLUMNS)}) ON "{TABLE}" TO "{role}"')
        op.execute(GUARD_FUNCTION_SQL)
        op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{TABLE}"')
        op.execute(GUARD_TRIGGER_SQL)
        return
    bind = op.get_bind()
    if TABLE not in sa.inspect(bind).get_table_names():
        raise SnapshotBookkeepingPreconditionError(f"{TABLE} does not exist; nothing to grant on.")
    if not bind.execute(sa.text("select 1 from pg_roles where rolname = :r"), {"r": role}).first():
        raise SnapshotBookkeepingPreconditionError(f"role {role!r} does not exist.")

    op.execute(f'GRANT UPDATE ({", ".join(GRANTED_COLUMNS)}) ON "{TABLE}" TO "{role}"')
    # The grant is only safe together with the row-state guard, so they are installed in one transaction.
    op.execute(GUARD_FUNCTION_SQL)
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{TABLE}"')
    op.execute(GUARD_TRIGGER_SQL)

    # Verify: exactly these columns, nothing wider.
    if bind.execute(sa.text("select has_table_privilege(:r, :t, 'UPDATE')"),
                    {"r": role, "t": TABLE}).scalar():
        raise SnapshotBookkeepingPreconditionError(
            f"{role} has table-wide UPDATE on {TABLE}; the column-level grant is not the whole story. "
            "Refusing to continue.")
    cols = [r[0] for r in bind.execute(sa.text(
        "select attname from pg_attribute where attrelid = cast(:t as regclass) and attnum > 0 "
        "and not attisdropped order by attnum"), {"t": TABLE}).fetchall()]
    allowed = {c for c in cols if bind.execute(
        sa.text("select has_column_privilege(:r, :t, :c, 'UPDATE')"),
        {"r": role, "t": TABLE, "c": c}).scalar()}
    if allowed != set(GRANTED_COLUMNS):
        raise SnapshotBookkeepingPreconditionError(
            f"{role} can UPDATE {sorted(allowed)} on {TABLE}; expected exactly {sorted(GRANTED_COLUMNS)}. "
            "Refusing to continue.")
    # tgenabled is a "char"; depending on the driver it comes back as str or bytes, so cast it to text.
    enabled = bind.execute(sa.text(
        "select tgenabled::text from pg_trigger where tgrelid = cast(:t as regclass) and tgname = :g "
        "and not tgisinternal"), {"t": TABLE, "g": GUARD_TRIGGER}).scalar()
    if enabled != "O":  # 'O' = fires in origin and local modes (the normal, enabled state)
        raise SnapshotBookkeepingPreconditionError(
            f"trigger {GUARD_TRIGGER} on {TABLE} is missing or not enabled (tgenabled={enabled!r}); "
            "the column grant must not stand without it. Refusing to continue.")


def downgrade() -> None:
    """Privilege and guard only; no data is touched. The importer would again fail under the runtime role."""
    role = _app_role()
    op.execute(f'REVOKE UPDATE ({", ".join(GRANTED_COLUMNS)}) ON "{TABLE}" FROM "{role}"')
    op.execute(f'DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON "{TABLE}"')
    op.execute(f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}()")
