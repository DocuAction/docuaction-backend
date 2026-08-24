"""Durable artifact registry for finalised reports.

Revision ID: 20260829_report_artifacts
Revises:     20260828_area1_grants
Create Date: 2026-08-24

WHY THIS EXISTS
---------------
Finalised reports lived only in `review_reports.report_html` — a column, which
can be updated. There was no content address for the delivered bytes, no way to
ask "is this still the document that was issued", and no retention metadata.

This table is the program-side index for the core artifact store
(`app/core/storage/artifact_store.py`). The store holds bytes and knows nothing
about reports; this table records which report a stored object is, which cycle
and evidence version produced it, and which human decisions stand behind it.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No retention period, and no WORM lock. The contractual retention period is open
COR decision D8. The columns exist so an approved period can be applied later
without changing report semantics, and they default to
PROGRAM_GUIDANCE_REQUESTED / NULL / false. Setting an irreversible retention
policy before the period is approved is the one decision in this area that
cannot be walked back.

IMMUTABILITY
------------
Enforced in two places, because one is not enough:

  * the unique constraint below stops the same report/format/version being
    registered twice;
  * the core store creates version directories with os.mkdir, which fails if
    the directory exists, so two writers cannot both believe they own a version.

Rows are written once at finalisation and are not updated. Regenerating
identical content deduplicates to the existing row; regenerating different
content creates a new version and leaves the old row exactly as it was.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260829_report_artifacts"
down_revision = "20260828_area1_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),

        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False,
                  server_default="1"),
        sa.Column("storage_backend", sa.String(32), nullable=False),
        sa.Column("storage_locator", sa.Text(), nullable=False),

        sa.Column("report_id", sa.String(64), nullable=False),
        sa.Column("report_type", sa.String(64), nullable=False),
        sa.Column("program", sa.String(32), nullable=False,
                  server_default="TEFCA_ARC"),
        # NOT NULL on purpose. Every report stored before Phase 7 had a null
        # cycle and could not be scoped afterwards.
        sa.Column("review_cycle_id", sa.String(128), nullable=False),

        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_by", sa.String(320)),
        sa.Column("template_version", sa.String(32)),
        sa.Column("evidence_rule_version", sa.String(64)),
        sa.Column("methodology_version", sa.String(64)),
        sa.Column("source_artifact_sha256", sa.String(64)),
        sa.Column("report_data_hash", sa.String(64)),
        sa.Column("rendered_sha256", sa.String(64), nullable=False),

        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),

        sa.Column("data_classification", sa.String(32), nullable=False,
                  server_default="DEVELOPMENT_TEST"),

        sa.Column("determination_event_ids", JSONB, server_default=sa.text("'[]'::jsonb")),
        sa.Column("qa_event_ids", JSONB, server_default=sa.text("'[]'::jsonb")),
        sa.Column("contains_reportable_findings", sa.Boolean(), nullable=False,
                  server_default="false"),

        sa.Column("retention_classification", sa.String(64), nullable=False,
                  server_default="PROGRAM_GUIDANCE_REQUESTED"),
        sa.Column("retention_period_days", sa.Integer()),
        sa.Column("retention_worm_locked", sa.Boolean(), nullable=False,
                  server_default="false"),
        sa.Column("retention_basis", sa.Text()),

        sa.Column("finalized", sa.Boolean(), nullable=False,
                  server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.UniqueConstraint("report_id", "content_type", "artifact_version",
                            name="uq_report_artifact_version"),
        # A real SHA-256 or nothing. The Phase 7A defect was a provenance field
        # holding the four-character string "cafe" and passing every non-empty
        # check; the database should refuse that shape outright.
        sa.CheckConstraint("rendered_sha256 ~ '^[0-9a-f]{64}$'",
                           name="ck_report_artifact_rendered_sha256"),
        sa.CheckConstraint(
            "source_artifact_sha256 IS NULL OR "
            "source_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_report_artifact_source_sha256"),
        # WORM may only be locked once a period is actually approved.
        sa.CheckConstraint(
            "retention_worm_locked = false OR retention_period_days IS NOT NULL",
            name="ck_report_artifact_worm_needs_period"),
    )
    op.create_index("ix_report_artifacts_artifact_id", "report_artifacts",
                    ["artifact_id"])
    op.create_index("ix_report_artifacts_report_id", "report_artifacts",
                    ["report_id"])
    op.create_index("ix_report_artifacts_rendered_sha256", "report_artifacts",
                    ["rendered_sha256"])


def downgrade() -> None:
    op.drop_index("ix_report_artifacts_rendered_sha256",
                  table_name="report_artifacts")
    op.drop_index("ix_report_artifacts_report_id", table_name="report_artifacts")
    op.drop_index("ix_report_artifacts_artifact_id", table_name="report_artifacts")
    op.drop_table("report_artifacts")
