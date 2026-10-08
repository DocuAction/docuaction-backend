"""Per-record check results (issue-history minimum slice).

Revision ID: 20261008_record_check_results
Revises: 20261006_snapshot_bookkeeping
Create Date: 2026-10-08

WHY THIS EXISTS
---------------
"No finding" is not "passed": a rule that did not apply, was skipped, raised, or
was never declared also writes no issue. To answer "did this record pass rule X
in delivery D, and is that comparable to delivery E?" the engine must persist an
OUTCOME per record per rule. This adds:

  rce_record_check_results   one row per (run, record); a versioned JSONB map
                             {rule_id: one-letter code}. APPEND-ONLY: the
                             runtime role gets INSERT and SELECT, never UPDATE,
                             DELETE or TRUNCATE.
  rce_rule_execution_history three NULLABLE columns (requires_hash, scope,
                             coverage JSONB) so a stored map can always be
                             interpreted without the current code.

Additive only. Nothing is backfilled and no existing row is touched; older runs
simply have no result rows and are read as "check result not persisted". The
writer is behind ENABLE_RECORD_CHECK_RESULTS (default off), so applying this
migration changes no behaviour by itself.

PARENTING NOTE: re-parented on 20261006_snapshot_bookkeeping (origin/main 0cef73b5 head).
If another migration merges first, re-parent again.

Applied to a local disposable database only under this round's authorization.
NOT dispatched against any shared or production database.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261008_record_check_results"
down_revision = "20261006_snapshot_bookkeeping"
branch_labels = None
depends_on = None

TABLE = "rce_record_check_results"
HISTORY = "rce_rule_execution_history"
NEW_COLUMNS = ("requires_hash", "scope", "coverage")


class RecordCheckResultsPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _table_exists(name: str) -> bool:
    return False if _offline() else name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set:
    if _offline():
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise RecordCheckResultsPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges to a "
            "named application role and refuses to guess which role that is. Set "
            "DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    role = _app_role()

    if not _table_exists(TABLE):
        op.create_table(
            TABLE,
            sa.Column("run_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("rce_ingestion_runs.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("source_record_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("map_version", sa.SmallInteger(), nullable=False),
            sa.Column("rule_count", sa.SmallInteger(), nullable=False),
            sa.Column("outcomes", postgresql.JSONB(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("run_id", "source_record_id",
                                    name="pk_rce_record_check_results"),
        )
        # The primary key serves the history read (run, record). The record
        # side has no separate index: the history always reads by both.

    existing = _columns(HISTORY)
    if "requires_hash" not in existing:
        op.add_column(HISTORY, sa.Column("requires_hash", sa.String(64), nullable=True))
    if "scope" not in existing:
        op.add_column(HISTORY, sa.Column("scope", sa.String(16), nullable=True))
    if "coverage" not in existing:
        op.add_column(HISTORY, sa.Column("coverage", postgresql.JSONB(), nullable=True))

    # Append-only: INSERT and SELECT. Deliberately NOT granted: UPDATE, DELETE,
    # TRUNCATE, REFERENCES, TRIGGER.
    op.execute(f'GRANT SELECT, INSERT ON "{TABLE}" TO "{role}"')


def downgrade() -> None:
    """Refuses while any result row exists -- recorded outcomes are evidence."""
    if not _offline() and _table_exists(TABLE):
        n = op.get_bind().execute(sa.text(f'select count(*) from "{TABLE}"')).scalar()
        if n:
            raise RecordCheckResultsPreconditionError(
                f"{TABLE} holds {n} row(s); downgrade refused. Roll back by "
                f"redeploying the previous image and leave the evidence in place.")
    op.execute(f'DROP TABLE IF EXISTS "{TABLE}"')
    for column in NEW_COLUMNS:
        op.execute(f'ALTER TABLE "{HISTORY}" DROP COLUMN IF EXISTS "{column}"')
