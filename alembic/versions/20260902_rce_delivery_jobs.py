"""rce_delivery_jobs: durable state for official ONC/RCE delivery processing.

Revision ID: 20260902_delivery_jobs
Revises: 20260831_export_jobs
Create Date: 2026-09-02

WHY THIS TABLE EXISTS
---------------------
`ingest_delivery` writes one Area 1 row per delivered line in 2,000-row batches,
and quality, curation, promotion, verification and reconciliation each walk the
population again afterwards. On the delivered 23,566-record file that is minutes
of work; on a 100K delivery it is considerably more. A browser request cannot
own it — a gateway times out, an operator refreshes, a worker recycles, and the
delivery goes with it while nothing records that it stopped.

What Data Operations needs back from registering a delivery is a receipt, not an
outcome. This table is that receipt, and it survives the process.

WHY NOT `report_export_jobs` OR `tefca_ppef_ingest_jobs`
-------------------------------------------------------
Both already run this exact discipline and this migration deliberately copies
their shape. It does not copy their ROW space: `report_export_jobs` is keyed on
an export identity and names an artifact, `tefca_ppef_ingest_jobs` is keyed on a
CMS component and quarter. A delivery is neither, and sharing a table would mean
columns that mean one thing for an export and another for an ingestion.

THE INDEX IS THE CONCURRENCY CONTROL
------------------------------------
`uq_rce_delivery_job_active_identity` is a PARTIAL unique index over `identity`
where `active_marker IS TRUE`. Two simultaneous registrations of the same bytes
under the same label produce one insert and one IntegrityError, and the loser
re-reads the winner's row. A SELECT-then-INSERT has a window between its two
statements; this has none, and it holds if the deployment ever runs more than
one worker.

`active_marker` is NULL rather than FALSE when terminal: PostgreSQL's partial
index excludes NULL rows, so any number of finished jobs may share an identity
while at most one live job may. That is exactly what lets ONC legitimately
re-deliver the same file later — the existing "a byte-identical re-delivery is
accepted as its own intake" behaviour is unchanged.

NO GOVERNMENT DATA IS TOUCHED. This creates one empty table and drops it again
on downgrade. The delivered bytes are NOT stored here; `storage_path` points at
the preserved original that `intake.preserve_original` already wrote.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260902_delivery_jobs"
down_revision = "20260831_export_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rce_delivery_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False),

        # what was registered
        sa.Column("delivery_label", sa.String(255)),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("declared_delimiter", sa.String(8)),
        sa.Column("received_date", sa.DateTime()),
        sa.Column("government_reference", sa.String(255)),
        sa.Column("notes", sa.Text()),
        sa.Column("source_name", sa.String(120)),

        # lifecycle
        sa.Column("state", sa.String(20), nullable=False,
                  server_default=sa.text("'QUEUED'")),
        sa.Column("stage", sa.String(32), nullable=False,
                  server_default=sa.text("'ACCEPTED'")),
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
        sa.Column("registered_by", sa.String(255), nullable=False),

        # what the run produced
        sa.Column("source_intake_id", postgresql.UUID(as_uuid=True)),
        sa.Column("records_received", sa.Integer()),
        sa.Column("records_processed", sa.Integer()),
        sa.Column("reconciliation_passed", sa.Boolean()),
        sa.Column("stage_detail", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb")),
    )

    op.create_index("ix_rce_delivery_jobs_identity", "rce_delivery_jobs",
                    ["identity"])
    op.create_index("ix_rce_delivery_jobs_sha256", "rce_delivery_jobs",
                    ["sha256"])
    op.create_index("ix_rce_delivery_jobs_state", "rce_delivery_jobs", ["state"])
    op.create_index("ix_rce_delivery_jobs_created_at", "rce_delivery_jobs",
                    ["created_at"])
    op.create_index("ix_rce_delivery_jobs_heartbeat_at", "rce_delivery_jobs",
                    ["heartbeat_at"])
    op.create_index("ix_rce_delivery_jobs_source_intake_id",
                    "rce_delivery_jobs", ["source_intake_id"])
    op.create_index("idx_rce_delivery_job_state_heartbeat", "rce_delivery_jobs",
                    ["state", "heartbeat_at"])
    op.create_index("idx_rce_delivery_job_registered_by", "rce_delivery_jobs",
                    ["registered_by"])
    # The concurrency guard.
    op.create_index("uq_rce_delivery_job_active_identity", "rce_delivery_jobs",
                    ["identity", "active_marker"], unique=True,
                    postgresql_where=sa.text("active_marker IS TRUE"))


def downgrade() -> None:
    op.drop_index("uq_rce_delivery_job_active_identity",
                  table_name="rce_delivery_jobs")
    op.drop_index("idx_rce_delivery_job_registered_by",
                  table_name="rce_delivery_jobs")
    op.drop_index("idx_rce_delivery_job_state_heartbeat",
                  table_name="rce_delivery_jobs")
    op.drop_index("ix_rce_delivery_jobs_source_intake_id",
                  table_name="rce_delivery_jobs")
    op.drop_index("ix_rce_delivery_jobs_heartbeat_at",
                  table_name="rce_delivery_jobs")
    op.drop_index("ix_rce_delivery_jobs_created_at",
                  table_name="rce_delivery_jobs")
    op.drop_index("ix_rce_delivery_jobs_state", table_name="rce_delivery_jobs")
    op.drop_index("ix_rce_delivery_jobs_sha256", table_name="rce_delivery_jobs")
    op.drop_index("ix_rce_delivery_jobs_identity",
                  table_name="rce_delivery_jobs")
    op.drop_table("rce_delivery_jobs")
