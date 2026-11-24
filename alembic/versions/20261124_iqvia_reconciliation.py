"""IQVIA exact snapshot reconciliation as a durable background job.

Revision ID: 20261124_iqvia_reconciliation
Revises: 20261118_disposition_followups
Create Date: 2026-11-24

WHY THIS EXISTS
---------------
`GET /iqvia/snapshots/{id}` and the approval route ran `SELECT count(*)` over the
observation table inline. On DEV that took 42-181 s for a snapshot that had just
finished a multi-hour import, so the portal timed out. The exact count now runs
as a durable job whose row lives here:

    iqvia_snapshot_reconciliation   one row per exact-count attempt.
        QUEUED -> CHECKING -> PASSED | FAILED. A partial unique index over
        (snapshot_id) WHERE active_marker IS TRUE makes "one active
        reconciliation per snapshot" a database guarantee. `staging_token` binds
        a result to the staged data it counted, so a changed snapshot invalidates
        it. Counts and ids only: no IQVIA row content.

The table is TERMINAL-IMMUTABLE, not append-only: the application updates a row
while it is QUEUED/CHECKING (claim, heartbeat, finish) and never after.
SELECT, INSERT, UPDATE are granted to the application role; there is NO DELETE.
It could not live in `source_snapshot.metadata_`: the importer rewrites that JSON
whole at several points (lost-update risk) and `approve_snapshot` copies it into
the approved successor row.

MIGRATION ORDER
---------------
Parent is `20261118_disposition_followups`, the head that is on `main` and applied
to DEV. Open draft PR #233 also parents two revisions on that head; whichever of
the two lands second must re-parent in its own reviewed commit (owner decision
2026-11-19, ARCHITECTURE_AND_DECISIONS.md). The single-head gate will refuse the
second one until that is done.

Not applied to any shared database by this change.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261124_iqvia_reconciliation"
down_revision = "20261118_disposition_followups"
branch_labels = None
depends_on = None

TABLE = "iqvia_snapshot_reconciliation"


class IqviaReconciliationPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise IqviaReconciliationPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges to a named "
            "application role on the table it creates and refuses to guess which role "
            "that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    role = _app_role()
    if not _offline() and TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("failure_kind", sa.String(32)),
        sa.Column("staging_token", sa.String(64), nullable=False),
        sa.Column("active_marker", sa.Boolean()),
        sa.Column("requested_by", sa.String(320), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("staged_count", sa.Integer()),
        sa.Column("expected_count", sa.Integer()),
        sa.Column("declared_record_count", sa.Integer()),
        sa.Column("rows_rejected", sa.Integer()),
        sa.Column("refusal_reasons", postgresql.JSONB()),
        sa.Column("error_reason", sa.Text()),
        sa.CheckConstraint("state IN ('QUEUED','CHECKING','PASSED','FAILED')",
                           name="ck_iqvia_reconciliation_state"),
    )
    op.create_index("uq_iqvia_reconciliation_active_snapshot", TABLE,
                    ["snapshot_id", "active_marker"], unique=True,
                    postgresql_where=sa.text("active_marker IS TRUE"))
    op.create_index("idx_iqvia_reconciliation_snapshot_started", TABLE,
                    ["snapshot_id", "started_at"])
    # No DELETE: a row is the record of a check and what it found.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON "{TABLE}" TO "{role}"')


def downgrade() -> None:
    """Refuses while any row exists: a check's outcome is history. Offline
    generation is refused for the same reason the disposition-followups
    revision refuses it -- a generated script cannot look at the table."""
    if _offline():
        raise IqviaReconciliationPreconditionError(
            "offline downgrade generation is refused: it cannot check the table is empty")
    bind = op.get_bind()
    if TABLE not in sa.inspect(bind).get_table_names():
        return
    bind.execute(sa.text(f'LOCK TABLE "{TABLE}" IN ACCESS EXCLUSIVE MODE'))
    n = bind.execute(sa.text(f'SELECT count(*) FROM "{TABLE}"')).scalar()
    if n:
        raise IqviaReconciliationPreconditionError(
            f"{TABLE} holds {n} row(s); downgrade refused.")
    op.execute(f'DROP TABLE "{TABLE}"')
