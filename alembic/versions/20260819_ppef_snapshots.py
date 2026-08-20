"""ppef snapshots and records — versioned local ingestion of CMS PPEF sub-files

Adds two tables. Nothing existing is altered or backfilled.

CMS publishes the four PPEF relational sub-files as quarterly CSV downloads,
not as data-api datasets, and states that PPEF carries CURRENT enrolment
information rather than historical. So the quarter behind a determination
disappears from the source when the next one publishes — which is exactly why
the snapshot, its checksum and its record count are stored rather than fetched
on demand.

Revision ID: 20260819_ppef_snapshots
Revises: 20260819_dim_evidence
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_ppef_snapshots"
down_revision = "20260819_dim_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tefca_ppef_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("cms_title", sa.String(255)),
        sa.Column("file_name", sa.String(255)),
        sa.Column("resource_id", sa.String(64)),
        sa.Column("parent_dataset_id", sa.String(64)),
        sa.Column("download_url", sa.Text()),
        sa.Column("api_endpoint", sa.Text()),
        sa.Column("transport", sa.String(20)),
        sa.Column("resource_version", sa.String(32)),
        sa.Column("as_of_label", sa.String(64)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("schema_fields", postgresql.JSONB(), server_default="[]"),
        sa.Column("record_count", sa.Integer(), server_default="0"),
        sa.Column("rows_truncated", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("http_last_modified", sa.String(64)),
        sa.Column("retrieved_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("ingested_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("ingest_status", sa.String(20), server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("ingested_by", sa.String(255)),
    )
    op.create_index("ix_tefca_ppef_snapshots_component", "tefca_ppef_snapshots", ["component"])
    op.create_index("ix_tefca_ppef_snapshots_version", "tefca_ppef_snapshots", ["resource_version"])
    op.create_index("ix_tefca_ppef_snapshots_sha256", "tefca_ppef_snapshots", ["sha256"])
    op.create_index("idx_ppef_snapshot_component_version", "tefca_ppef_snapshots",
                    ["component", "resource_version"])

    op.create_table(
        "tefca_ppef_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tefca_ppef_snapshots.id"), nullable=False),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("enrollment_id", sa.String(32)),
        sa.Column("related_enrollment_id", sa.String(32)),
        sa.Column("npi", sa.String(10)),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_tefca_ppef_records_snapshot", "tefca_ppef_records", ["snapshot_id"])
    op.create_index("ix_tefca_ppef_records_component", "tefca_ppef_records", ["component"])
    op.create_index("ix_tefca_ppef_records_enrollment", "tefca_ppef_records", ["enrollment_id"])
    op.create_index("ix_tefca_ppef_records_related", "tefca_ppef_records", ["related_enrollment_id"])
    op.create_index("ix_tefca_ppef_records_npi", "tefca_ppef_records", ["npi"])
    op.create_index("idx_ppef_record_component_enrollment", "tefca_ppef_records",
                    ["component", "enrollment_id"])
    op.create_index("idx_ppef_record_component_related", "tefca_ppef_records",
                    ["component", "related_enrollment_id"])
    op.create_index("idx_ppef_record_snapshot_component", "tefca_ppef_records",
                    ["snapshot_id", "component"])


def downgrade() -> None:
    # Dropping these destroys the snapshots determinations were made against.
    # Present so the chain is reversible in development; not for an environment
    # whose determinations have been issued.
    for ix in ("idx_ppef_record_snapshot_component", "idx_ppef_record_component_related",
               "idx_ppef_record_component_enrollment", "ix_tefca_ppef_records_npi",
               "ix_tefca_ppef_records_related", "ix_tefca_ppef_records_enrollment",
               "ix_tefca_ppef_records_component", "ix_tefca_ppef_records_snapshot"):
        op.drop_index(ix, table_name="tefca_ppef_records")
    op.drop_table("tefca_ppef_records")
    for ix in ("idx_ppef_snapshot_component_version", "ix_tefca_ppef_snapshots_sha256",
               "ix_tefca_ppef_snapshots_version", "ix_tefca_ppef_snapshots_component"):
        op.drop_index(ix, table_name="tefca_ppef_snapshots")
    op.drop_table("tefca_ppef_snapshots")
