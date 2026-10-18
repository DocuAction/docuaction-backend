"""Controlled recheck jobs and items.

Revision ID: 20261004_recheck_jobs
Revises: 20261004_stage_event_preflight
Create Date: 2026-10-04

WHY THIS EXISTS
---------------
Automated coverage is first-attempt-only: an entity counts as covered the
moment it has any evidence row, so a source that was UNAVAILABLE when it was
looked up is never looked up again. `rechecks.py` adds a bounded,
maker/checker-approved, idempotent re-evaluation for three triggers (source
recovery, a new approved snapshot, an approved mapping change). These two
tables are its durable state -- see `recheck_models.py`.

Job bookkeeping only (references, states, counts, hashes); the application
updates progress on them, so the runtime role gets SELECT/INSERT/UPDATE and
no DELETE, the same shape as `iqvia_import_job` and `report_export_jobs`.

Local/disposable databases only under this round's authorization. NOT
dispatched against any shared or production database.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261004_recheck_jobs"
down_revision = "20261004_stage_event_preflight"
branch_labels = None
depends_on = None

TABLES = ("rce_recheck_job", "rce_recheck_item")


class RecheckJobsPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _table_exists(name: str) -> bool:
    return False if _offline() else name in sa.inspect(op.get_bind()).get_table_names()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise RecheckJobsPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges to a "
            "named application role and refuses to guess which role that is. Set "
            "DB_APP_ROLE=docuaction_app.")
    return role


def _uuid():
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    role = _app_role()

    if not _table_exists("rce_recheck_job"):
        op.create_table(
            "rce_recheck_job",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("trigger_kind", sa.String(40), nullable=False),
            sa.Column("source_id", sa.String(64), nullable=False),
            sa.Column("trigger_ref", sa.String(255), nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column("state", sa.String(40), nullable=False,
                      server_default="PENDING_APPROVAL"),
            sa.Column("requested_by", sa.String(320), nullable=False),
            sa.Column("requested_by_id", sa.String(64)),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("approved_by", sa.String(320)),
            sa.Column("approved_by_id", sa.String(64)),
            sa.Column("approved_at", sa.DateTime()),
            sa.Column("pinned", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("baseline_hash", sa.String(64), nullable=False),
            sa.Column("target_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("untargeted_remaining", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("batch_size", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("summary", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("error_reason", sa.Text()),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime()),
            sa.Column("heartbeat_at", sa.DateTime()),
            sa.Column("completed_at", sa.DateTime()),
            sa.Column("correlation_id", sa.String(64)),
            sa.UniqueConstraint("idempotency_key", name="uq_rce_recheck_job_idempotency"),
            sa.CheckConstraint(
                "trigger_kind IN ('SOURCE_RECOVERY','NEW_APPROVED_SNAPSHOT',"
                "'APPROVED_MAPPING_CHANGE')", name="ck_rce_recheck_job_trigger"),
            sa.CheckConstraint(
                "state IN ('PENDING_APPROVAL','QUEUED','RUNNING','SUCCEEDED',"
                "'STOPPED_SOURCE_UNAVAILABLE','REFUSED_STALE','FAILED')",
                name="ck_rce_recheck_job_state"),
        )
        op.create_index("idx_rce_recheck_job_state", "rce_recheck_job",
                        ["state", "heartbeat_at"])
        op.create_index("idx_rce_recheck_job_intake", "rce_recheck_job", ["intake_id"])

    if not _table_exists("rce_recheck_item"):
        op.create_table(
            "rce_recheck_item",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("job_id", _uuid(),
                      sa.ForeignKey("rce_recheck_job.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("entity_id", _uuid(), nullable=False),
            sa.Column("entity_ref", sa.String(255), nullable=False),
            sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
            sa.Column("prior_evidence_id", _uuid()),
            sa.Column("prior_disposition", sa.String(32)),
            sa.Column("new_disposition", sa.String(32)),
            sa.Column("outcome", sa.String(32)),
            sa.Column("detail", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("processed_at", sa.DateTime()),
            sa.UniqueConstraint("job_id", "entity_id", name="uq_rce_recheck_item_job_entity"),
            sa.CheckConstraint("state IN ('PENDING','DONE','ERROR')",
                               name="ck_rce_recheck_item_state"),
        )
        op.create_index("idx_rce_recheck_item_job_state", "rce_recheck_item",
                        ["job_id", "state"])

    # No DELETE: a job and its items are a permanent record of what was
    # re-evaluated, on whose approval, against which baseline.
    for table in TABLES:
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON "{table}" TO "{role}"')


def downgrade() -> None:
    """Refuses while any recheck job exists -- its history is not discarded."""
    if not _offline() and _table_exists("rce_recheck_job"):
        n = op.get_bind().execute(sa.text('select count(*) from "rce_recheck_job"')).scalar()
        if n:
            raise RecheckJobsPreconditionError(
                f"rce_recheck_job holds {n} row(s); downgrade refused.")
    op.execute('DROP TABLE IF EXISTS "rce_recheck_item"')
    op.execute('DROP TABLE IF EXISTS "rce_recheck_job"')
