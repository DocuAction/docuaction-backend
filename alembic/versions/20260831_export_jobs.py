"""report_export_jobs: durable state for controlled export generation.

Revision ID: 20260831_export_jobs
Revises: 20260831_review_case
Create Date: 2026-08-31

WHY THIS TABLE EXISTS
---------------------
Step #17 measured the controlled Excel export of the delivered population at
roughly seven and a half minutes. A browser request cannot own that work: a
gateway times out, a user refreshes, a worker recycles, and the export goes with
it while nothing records that it stopped. The caller needs a receipt, not the
bytes.

WHY NOT `tefca_ppef_ingest_jobs`
--------------------------------
That table already runs the same discipline — durable state, heartbeat, reaper,
partial unique index — and this migration deliberately copies its shape. It does
not copy its ROW space: `tefca_ppef_ingest_jobs` is keyed on a CMS component and
quarter and carries a foreign key to a PPEF snapshot. An export has none of
those. Sharing the table would mean columns that mean one thing for ingestion
and another for exports.

THE INDEX IS THE CONCURRENCY CONTROL
------------------------------------
`uq_export_job_active_identity` is a PARTIAL unique index over `identity` where
`active_marker IS TRUE`. Two simultaneous requests for the same export produce
one insert and one IntegrityError, and the loser re-reads the winner's row.
A SELECT-then-INSERT has a window between its two statements; this has none, and
it holds if the deployment ever runs more than one worker.

`active_marker` is NULL rather than FALSE when terminal: PostgreSQL's partial
index excludes NULL rows, so any number of finished jobs may share an identity
while at most one live job may.

NO GOVERNMENT DATA IS TOUCHED. This creates one empty table and drops it again
on downgrade.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260831_export_jobs"
down_revision = "20260831_review_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_export_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False),
        sa.Column("export_type", sa.String(64), nullable=False),
        sa.Column("source_intake_id", postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("generator_version", sa.String(128), nullable=False),
        sa.Column("state", sa.String(20), nullable=False,
                  server_default=sa.text("'QUEUED'")),
        sa.Column("phase", sa.String(64)),
        sa.Column("active_marker", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("heartbeat_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("failed_at", sa.DateTime()),
        sa.Column("attempt_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("error_reason", sa.Text()),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("report_id", sa.String(64)),
        sa.Column("artifact_id", sa.String(128)),
        sa.Column("artifact_version", sa.Integer()),
        sa.Column("rendered_sha256", sa.String(64)),
        sa.Column("size_bytes", sa.Integer()),
    )
    op.create_index("ix_report_export_jobs_identity", "report_export_jobs",
                    ["identity"])
    op.create_index("ix_report_export_jobs_state", "report_export_jobs", ["state"])
    op.create_index("ix_report_export_jobs_created_at", "report_export_jobs",
                    ["created_at"])
    op.create_index("ix_report_export_jobs_heartbeat_at", "report_export_jobs",
                    ["heartbeat_at"])
    op.create_index("ix_report_export_jobs_source_intake_id",
                    "report_export_jobs", ["source_intake_id"])
    op.create_index("ix_report_export_jobs_report_id", "report_export_jobs",
                    ["report_id"])
    op.create_index("idx_export_job_state_heartbeat", "report_export_jobs",
                    ["state", "heartbeat_at"])
    op.create_index("idx_export_job_requested_by", "report_export_jobs",
                    ["requested_by"])
    # The concurrency guard.
    op.create_index("uq_export_job_active_identity", "report_export_jobs",
                    ["identity", "active_marker"], unique=True,
                    postgresql_where=sa.text("active_marker IS TRUE"))


def downgrade() -> None:
    op.drop_index("uq_export_job_active_identity", table_name="report_export_jobs")
    op.drop_index("idx_export_job_requested_by", table_name="report_export_jobs")
    op.drop_index("idx_export_job_state_heartbeat", table_name="report_export_jobs")
    op.drop_index("ix_report_export_jobs_report_id", table_name="report_export_jobs")
    op.drop_index("ix_report_export_jobs_source_intake_id",
                  table_name="report_export_jobs")
    op.drop_index("ix_report_export_jobs_heartbeat_at",
                  table_name="report_export_jobs")
    op.drop_index("ix_report_export_jobs_created_at", table_name="report_export_jobs")
    op.drop_index("ix_report_export_jobs_state", table_name="report_export_jobs")
    op.drop_index("ix_report_export_jobs_identity", table_name="report_export_jobs")
    op.drop_table("report_export_jobs")
