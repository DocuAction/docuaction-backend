"""rce_disposition_followups - durable delivery-level work after an analyst decision.

Revision ID: 20261118_disposition_followups
Revises: 20261108_iqvia_advisory_outcome
Create Date: 2026-11-18

WHY THIS EXISTS
---------------
A decision that releases a held record triggers whole-delivery re-promotion,
reconciliation and a snapshot. That ran inside the browser's save request
(51-96 s on DEV, 2026-11-16..18). To move it out of the request without
losing accepted work on a closed browser, a lost response or a worker
restart, the accepted work has to be a ROW before the request is answered.
Nothing existing can hold it: `rce_delivery_jobs` is the registration job
(one per delivered file, unique over active jobs) and must not be re-queued
for this, and an in-memory task dies with the process.

Job bookkeeping only (references, states, the reviewer's own decision text
already stored on the issue). The application updates progress on it, so the
runtime role gets SELECT/INSERT/UPDATE and no DELETE. The table stays owned
by the migration (owner) role - PROD-convergence FINALIZE keeps it there, as
it does `rce_delivery_jobs` - because an owning runtime role would hold DELETE
regardless of this grant.

ADDITIVE. No ROW is written unless ENABLE_DEFERRED_DISPOSITION_PROCESSING is
set. Once the table exists the application reads it on every save (is earlier
work outstanding?) and the worker polls it, flag on or off, so accepted work is
always finished. Prepared and tested against a
disposable local database only; NOT applied to any shared database.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql as pg

revision = "20261118_disposition_followups"
down_revision = "20261108_iqvia_advisory_outcome"
branch_labels = None
depends_on = None

TABLE = "rce_disposition_followups"


class DispositionFollowupsPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise DispositionFollowupsPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges on "
            f"{TABLE} to a named application role and refuses to guess which role "
            "that is. Set DB_APP_ROLE=docuaction_app."
        )
    return role


def upgrade() -> None:
    role = _app_role()
    op.create_table(
        TABLE,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "kind", sa.String(24), nullable=False, server_default=sa.text("'ISSUE_DISPOSITION'")
        ),
        sa.Column(
            "intake_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("delivery_job_id", pg.UUID(as_uuid=True)),
        sa.Column(
            "issue_id", pg.UUID(as_uuid=True), sa.ForeignKey("rce_issues.id", ondelete="RESTRICT")
        ),
        sa.Column("source_record_id", pg.UUID(as_uuid=True)),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("attribution", sa.String(8), nullable=False, server_default=sa.text("'HUMAN'")),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("payload_fingerprint", sa.String(64)),
        sa.Column("state", sa.String(16), nullable=False, server_default=sa.text("'RECEIVED'")),
        sa.Column("batch_id", pg.UUID(as_uuid=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("recovered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("record_status_before", sa.String(16)),
        sa.Column("record_status_after", sa.String(16)),
        sa.Column("decision_result", pg.JSONB()),
        sa.Column("snapshot_id", pg.UUID(as_uuid=True)),
        sa.Column("snapshot_passed", sa.Boolean()),
        sa.Column("disposition_event_id", pg.UUID(as_uuid=True)),
        sa.Column("error_code", sa.String(48)),
        sa.Column("error_detail", sa.Text()),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("build_sha", sa.String(40)),
        sa.CheckConstraint(
            "state IN ('RECEIVED','QUEUED','PROCESSING','COMPLETED','FAILED',"
            "'REFUSED','NOT_REQUIRED')",
            name="ck_rce_disp_followup_state",
        ),
        sa.CheckConstraint(
            "kind IN ('ISSUE_DISPOSITION','IDENTIFIER_DECISION')", name="ck_rce_disp_followup_kind"
        ),
        sa.CheckConstraint(
            "attribution IN ('HUMAN','SYSTEM')", name="ck_rce_disp_followup_attribution"
        ),
    )
    op.create_index(
        "uq_rce_disp_followup_issue_key",
        TABLE,
        ["issue_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL AND issue_id IS NOT NULL"),
    )
    op.create_index(
        "ix_rce_disp_followup_pending",
        TABLE,
        ["intake_id", "state", "received_at"],
        postgresql_where=sa.text("state IN ('RECEIVED','QUEUED','PROCESSING')"),
    )
    op.create_index("ix_rce_disp_followup_intake", TABLE, ["intake_id", "received_at"])
    op.create_index("ix_rce_disp_followup_issue", TABLE, ["issue_id", "received_at"])
    op.create_index("ix_rce_disp_followup_record", TABLE, ["source_record_id", "received_at"])
    # No DELETE: a row is the record of what was accepted and what became of it.
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON "{TABLE}" TO "{role}"')


def downgrade() -> None:
    """Refuses while any row exists - accepted work and its outcome are history.

    Offline (`--sql`) generation is refused outright: a generated script cannot
    look at the table, so it would drop accepted work unconditionally. Online,
    the table is locked ACCESS EXCLUSIVE before it is counted and the lock is
    held to the DROP in the same transaction, so a row committed after the
    count cannot be lost.
    """
    if _offline():
        raise DispositionFollowupsPreconditionError(
            f"Offline downgrade of {TABLE} is refused: it cannot check that the "
            "table is empty. Run the downgrade online, against an empty table."
        )
    bind = op.get_bind()
    if TABLE in sa.inspect(bind).get_table_names():
        bind.execute(sa.text(f'LOCK TABLE "{TABLE}" IN ACCESS EXCLUSIVE MODE'))
        n = bind.execute(
            sa.text(f'select count(*) from "{TABLE}"')  # noqa: S608 - constant table name
        ).scalar()
        if n:
            raise DispositionFollowupsPreconditionError(
                f"{TABLE} holds {n} row(s); downgrade refused. The table must "
                "stay: it is the record of accepted work. Turning "
                "ENABLE_DEFERRED_DISPOSITION_PROCESSING off stops NEW deferred "
                "saves only - the worker still finishes work already accepted."
            )
    op.execute(f'DROP TABLE IF EXISTS "{TABLE}"')
