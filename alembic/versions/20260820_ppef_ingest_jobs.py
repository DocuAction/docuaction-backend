"""ppef ingestion jobs — durable job state for quarterly snapshot loads

Adds ONE table. Nothing existing is altered, dropped or backfilled, so this is
additive and rollback-safe: `downgrade()` drops only what `upgrade()` created.

WHY THE TABLE EXISTS
Ingestion previously ran in a FastAPI BackgroundTask, which lives and dies with
its worker. When the container recycled mid-load the task vanished and left the
snapshot at `pending` with `error = None` — five such rows accumulated on dev.
The failure was not that the work stopped; it was that nothing survived to say
so. Job state therefore lives in the database, where a dead process cannot take
it with it.

THE PARTIAL UNIQUE INDEX IS THE CONCURRENCY CONTROL
`uq_ppef_job_active_component` is unique over (component, resource_version)
WHERE active_marker IS TRUE. A finished job sets active_marker to NULL, and
NULLs do not collide, so history accumulates freely while at most one ACTIVE job
per component+quarter can exist. Refusing a double load with a constraint rather
than a check-then-insert closes the window between the two statements, and it
keeps holding if the deployment ever runs more than one worker.

Revision ID: 20260820_ppef_jobs
Revises: 20260819_ppef_snapshots
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260820_ppef_jobs"
down_revision = "20260819_ppef_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tefca_ppef_ingest_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("resource_version", sa.String(32)),
        sa.Column("quarter", sa.String(32)),
        sa.Column("state", sa.String(20), nullable=False),
        # Nullable BOOLEAN, not a plain flag: only TRUE participates in the
        # partial unique index, and NULL is what releases the slot on finish.
        sa.Column("active_marker", sa.Boolean()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()),
        # The liveness signal. A dead process cannot report that it died, so
        # the reaper infers death from this column ceasing to advance.
        sa.Column("heartbeat_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("failed_at", sa.DateTime()),
        sa.Column("attempt_count", sa.Integer(), server_default="0"),
        sa.Column("error_reason", sa.Text()),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tefca_ppef_snapshots.id")),
        sa.Column("checksum", sa.String(64)),
        sa.Column("row_count", sa.Integer()),
        sa.Column("requested_by", sa.String(255)),
        sa.Column("max_rows", sa.Integer()),
    )
    # Index names and columns mirror TEFCAPPEFIngestJob exactly. They must: the
    # production schema is created by metadata create_all, so a migration that
    # invented its own names would leave the two environments differing in a way
    # nothing checks.
    op.create_index("ix_tefca_ppef_ingest_jobs_component",
                    "tefca_ppef_ingest_jobs", ["component"])
    op.create_index("ix_tefca_ppef_ingest_jobs_resource_version",
                    "tefca_ppef_ingest_jobs", ["resource_version"])
    op.create_index("ix_tefca_ppef_ingest_jobs_state",
                    "tefca_ppef_ingest_jobs", ["state"])
    op.create_index("ix_tefca_ppef_ingest_jobs_created_at",
                    "tefca_ppef_ingest_jobs", ["created_at"])
    op.create_index("ix_tefca_ppef_ingest_jobs_heartbeat_at",
                    "tefca_ppef_ingest_jobs", ["heartbeat_at"])
    op.create_index("idx_ppef_job_state_heartbeat",
                    "tefca_ppef_ingest_jobs", ["state", "heartbeat_at"])
    op.create_index("idx_ppef_job_component_version",
                    "tefca_ppef_ingest_jobs", ["component", "resource_version"])
    op.create_index(
        "uq_ppef_job_active_component",
        "tefca_ppef_ingest_jobs",
        ["component", "resource_version", "active_marker"],
        unique=True,
        postgresql_where=sa.text("active_marker IS TRUE"),
    )


def downgrade() -> None:
    for name in (
        "uq_ppef_job_active_component",
        "idx_ppef_job_component_version",
        "idx_ppef_job_state_heartbeat",
        "ix_tefca_ppef_ingest_jobs_heartbeat_at",
        "ix_tefca_ppef_ingest_jobs_created_at",
        "ix_tefca_ppef_ingest_jobs_state",
        "ix_tefca_ppef_ingest_jobs_resource_version",
        "ix_tefca_ppef_ingest_jobs_component",
    ):
        op.drop_index(name, table_name="tefca_ppef_ingest_jobs")
    op.drop_table("tefca_ppef_ingest_jobs")
