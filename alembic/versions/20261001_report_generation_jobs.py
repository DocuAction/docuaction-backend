"""report_export_jobs: extend for async report generation (not just ONC export).

Revision ID: 20261001_report_generation_jobs
Revises: 20260930_alembic_version_read
Create Date: 2026-10-01

WHY THIS EXTENDS `report_export_jobs` RATHER THAN A NEW TABLE
--------------------------------------------------------------
`report_export_jobs` already has everything an async report generation needs:
durable QUEUED/RUNNING/SUCCEEDED/FAILED state, a heartbeat + reaper, and a
partial unique index that makes a duplicate request return the SAME job
instead of starting a second one. The ONC review workbook export is the one
thing that uses it today; report generation (`POST /api/reports/generate`)
is the second, and reuses the identical table rather than inventing a
parallel job-state model (the task's own instruction: extend an existing
structure before creating a new one).

Two gaps close here:

  * `source_intake_id` was NOT NULL because the only existing user (the ONC
    workbook) is always delivery-scoped. A period/review-cycle report
    (retrospective_weekly, ongoing_quarterly, ...) is not scoped to one
    delivery, so the column is relaxed to nullable. Every EXISTING row keeps
    its value; nothing is rewritten.
  * `request_parameters` is new: the exact `query_parameters` dict
    `generate_report()` needs to replay the request (job_id / intake_id /
    review_cycle_id / period_start / period_end / format / ...). The ONC
    workbook path needs none of this (it only ever needed `source_intake_id`
    + `classification`, both already columns), so the column is nullable and
    NULL for every row this migration touches.
  * `report_type` is new, alongside the existing `export_type` (which for the
    ONC workbook names the export product, e.g. "onc_review_workbook"; for a
    generation job it is set to "report:<report_type>" so the two job kinds
    stay distinguishable in one table without a kind column). `report_type`
    itself is the bare report_type string, for callers and admin tooling that
    want to read it without parsing `export_type`.

NO GOVERNMENT DATA IS TOUCHED. Two columns are added (both nullable, no
default rewrite) and one NOT NULL constraint is relaxed. No row's existing
data changes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261001_report_generation_jobs"
down_revision = "20260930_alembic_version_read"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("report_export_jobs", "source_intake_id",
                    existing_type=postgresql.UUID(as_uuid=True),
                    nullable=True)
    op.add_column("report_export_jobs",
                 sa.Column("report_type", sa.String(64), nullable=True))
    op.add_column("report_export_jobs",
                 sa.Column("request_parameters", postgresql.JSONB(), nullable=True))
    op.create_index("ix_report_export_jobs_report_type", "report_export_jobs",
                    ["report_type"])


def downgrade() -> None:
    op.drop_index("ix_report_export_jobs_report_type", table_name="report_export_jobs")
    op.drop_column("report_export_jobs", "request_parameters")
    op.drop_column("report_export_jobs", "report_type")
    op.alter_column("report_export_jobs", "source_intake_id",
                    existing_type=postgresql.UUID(as_uuid=True),
                    nullable=False)
