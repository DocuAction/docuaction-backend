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
thing that uses it today; report generation (`POST /api/reports/generate/jobs`)
is the second, and reuses the identical table rather than inventing a
parallel job-state model (the task's own instruction: extend an existing
structure before creating a new one).

One column stays NOT NULL on purpose (migration review,
qa-evidence/2026-10-01-reporting-architecture/MIGRATION-REVIEW-20261001-report-generation-jobs.md):
an earlier draft of this migration relaxed `source_intake_id` to nullable, on
the reasoning that a period/review-cycle report has no one delivery to scope
it to. That capability has no caller yet -- the report type this migration
exists to serve, `delivery_processing`, is unconditionally delivery-scoped
(`generator.py`'s `RCE_TYPES`; every path through it calls `resolve_delivery`
and refuses without a `job_id`/`intake_id`) -- so the constraint is left in
place and the route requires the same scope `delivery_processing` already
requires. Loosening a real, currently-enforced invariant for a capability
nothing exercises is backwards; if a period-scoped report type is ever wired
to this async path, that is a separate, later migration decision.

Two columns are added, both nullable, both NULL for every existing row:

  * `request_parameters` -- the exact `query_parameters` dict
    `generate_report()` needs to replay the request (job_id / intake_id /
    review_cycle_id / format / ...). The ONC workbook path needs none of this
    (it only ever needed `source_intake_id` + `classification`, both already
    columns).
  * `report_type` -- alongside the existing `export_type` (which for the ONC
    workbook names the export product, e.g. "onc_review_workbook"; for a
    generation job it is set to "report:<report_type>" so the two job kinds
    stay distinguishable in one table without a kind column). `report_type`
    itself is the bare report_type string, for callers and admin tooling that
    want to read it without parsing `export_type`.

NO GOVERNMENT DATA IS TOUCHED. Two nullable columns are added; no existing
column, constraint or row is altered.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261001_report_generation_jobs"
down_revision = "20260930_alembic_version_read"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
