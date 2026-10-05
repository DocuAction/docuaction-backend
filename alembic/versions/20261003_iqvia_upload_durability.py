"""IQVIA chunked-upload and import-job durability.

Revision ID: 20261003_iqvia_upload_durability
Revises: 20261002_iqvia_observations
Create Date: 2026-10-03

WHY THIS EXISTS
---------------
The chunked upload mechanism added alongside `20261002_iqvia_observations`
kept its bookkeeping (`_UPLOAD_STATE`: which chunks had arrived, the staging
job) in the serving process's own memory. That survives a retry or a
dropped connection; it does not survive the process restarting mid-upload or
mid-import -- exactly the failure a multi-gigabyte, minutes-to-hours transfer
is most likely to hit. This migration creates the three tables that move
that state into the database, the same discipline `20260831_export_jobs`
already established for controlled report exports:

    iqvia_upload_session   one row per chunked upload -- the chunk plan and
                            the on-disk temp file path
    iqvia_upload_chunk     one row per chunk actually received -- composite
                            PK makes a retried PUT idempotent
    iqvia_import_job       durable trigger for staging/import work -- claimed
                            via FOR UPDATE SKIP LOCKED, a partial unique
                            index makes at-most-one-active-job-per-snapshot a
                            database guarantee

WHAT THIS DOES NOT CHANGE
--------------------------
`iqvia_hco_observation` / `iqvia_hcp_observation` / `iqvia_affiliation_observation`
(20261002_iqvia_observations) and `source_snapshot` (20260921_september_snapshot)
are unchanged. The importer's own row-level resume
(`ON CONFLICT DO NOTHING` on `(source_snapshot_id, source_record_key)`) was
already correct and is untouched -- this migration's tables exist to make
sure the importer gets CALLED again after a restart, not to change what it
does once called.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261003_iqvia_upload_durability"
down_revision = "20261002_iqvia_observations"
branch_labels = None
depends_on = None

TABLES = ("iqvia_upload_session", "iqvia_upload_chunk", "iqvia_import_job")


class IqviaUploadDurabilityPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return False if _offline() else name in _inspector().get_table_names()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise IqviaUploadDurabilityPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges "
            "to a named application role on the tables it creates, the same "
            "discipline 20261002_iqvia_observations uses -- it refuses to "
            "guess which role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def _uuid():
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    role = _app_role()

    if not _table_exists("iqvia_upload_session"):
        op.create_table(
            "iqvia_upload_session",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("label", sa.String(200), nullable=False),
            sa.Column("total_size", sa.BigInteger(), nullable=False),
            sa.Column("chunk_size", sa.Integer(), nullable=False),
            sa.Column("total_chunks", sa.Integer(), nullable=False),
            sa.Column("temp_path", sa.Text(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False,
                     server_default="UPLOADING"),
            sa.Column("snapshot_id", _uuid(),
                     sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"),
                     nullable=True),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                     server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False,
                     server_default=sa.func.now()),
        )
        op.create_index("idx_iqvia_upload_session_status", "iqvia_upload_session",
                        ["status"])

    if not _table_exists("iqvia_upload_chunk"):
        op.create_table(
            "iqvia_upload_chunk",
            sa.Column("upload_id", _uuid(),
                     sa.ForeignKey("iqvia_upload_session.id", ondelete="CASCADE"),
                     primary_key=True),
            sa.Column("chunk_index", sa.Integer(), primary_key=True),
            sa.Column("received_at", sa.DateTime(), nullable=False,
                     server_default=sa.func.now()),
        )

    if not _table_exists("iqvia_import_job"):
        op.create_table(
            "iqvia_import_job",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("upload_id", _uuid(),
                     sa.ForeignKey("iqvia_upload_session.id", ondelete="SET NULL"),
                     nullable=True),
            sa.Column("snapshot_id", _uuid(),
                     sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"),
                     nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("file_path", sa.Text(), nullable=False),
            sa.Column("label", sa.String(200), nullable=False),
            sa.Column("created_by", sa.String(255), nullable=False),
            sa.Column("state", sa.String(20), nullable=False,
                     server_default="QUEUED"),
            sa.Column("phase", sa.String(64)),
            sa.Column("active_marker", sa.Boolean(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False,
                     server_default="0"),
            sa.Column("error_reason", sa.Text()),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                     server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime()),
            sa.Column("heartbeat_at", sa.DateTime()),
            sa.Column("completed_at", sa.DateTime()),
            sa.Column("failed_at", sa.DateTime()),
        )
        op.create_index("idx_iqvia_import_job_state", "iqvia_import_job", ["state"])
        op.create_index("idx_iqvia_import_job_state_heartbeat", "iqvia_import_job",
                        ["state", "heartbeat_at"])
        op.create_index(
            "uq_iqvia_import_job_active_snapshot", "iqvia_import_job",
            ["snapshot_id", "active_marker"], unique=True,
            postgresql_where=sa.text("active_marker IS TRUE"))

    # No DELETE: a terminal job/session keeps its row (active_marker cleared
    # via UPDATE), matching the no-delete-path shape `report_export_jobs`
    # and `rce_delivery_jobs` already established for this kind of
    # receipt/ledger table -- these hold no Government-delivered evidence,
    # just this application's own job bookkeeping.
    for table in TABLES:
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON "{table}" TO "{role}"')


def downgrade() -> None:
    """Refuses if any import job or upload session still exists -- a
    durable-job history is not silently discarded, same discipline every
    other IQVIA migration in this pass uses."""
    if not _offline():
        bind = op.get_bind()
        for table in ("iqvia_import_job", "iqvia_upload_session"):
            if _table_exists(table):
                n = bind.execute(sa.text(f'select count(*) from "{table}"')).scalar()
                if n:
                    raise IqviaUploadDurabilityPreconditionError(
                        f"{table} holds {n} row(s); downgrade refused.")
    op.execute('DROP TABLE IF EXISTS "iqvia_upload_chunk"')
    op.execute('DROP TABLE IF EXISTS "iqvia_import_job"')
    op.execute('DROP TABLE IF EXISTS "iqvia_upload_session"')
