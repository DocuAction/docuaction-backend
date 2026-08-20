"""tefca_dimension_evidence — dimension-organised evidence with CMS provenance

Adds ONE table. Nothing existing is altered, dropped or backfilled: the
five-element evidence record, the source cache and the audit trail all keep
their current shape and contents. This table sits alongside them.

Append-only by contract (see the model docstring): re-running a verification
inserts a new generation rather than updating rows, so a determination stays
explicable after CMS publishes a newer quarterly extract.

Revision ID: 20260819_dim_evidence
Revises: 20260817_audit_fields
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_dim_evidence"
down_revision = "20260817_audit_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tefca_dimension_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("review_id", sa.String(255)),
        sa.Column("review_cycle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tefca_review_cycles.cycle_id")),
        sa.Column("evidence_dimension", sa.String(64), nullable=False),
        sa.Column("dimension_disposition", sa.String(32)),
        sa.Column("dimension_applicability", sa.String(32)),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_dataset", sa.String(128)),
        sa.Column("ppef_component", sa.String(64)),
        sa.Column("source_record_identifier", sa.Text()),
        sa.Column("query_identifier", sa.Text()),
        sa.Column("query_timestamp", sa.String(64)),
        sa.Column("dataset_version_anchor", sa.String(128)),
        sa.Column("http_last_modified", sa.String(64)),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("fields_evaluated", postgresql.JSONB(), server_default="[]"),
        sa.Column("field_matches", postgresql.JSONB(), server_default="[]"),
        sa.Column("field_conflicts", postgresql.JSONB(), server_default="[]"),
        sa.Column("original_values", postgresql.JSONB(), server_default="{}"),
        sa.Column("normalized_values", postgresql.JSONB(), server_default="{}"),
        sa.Column("rule_applied", sa.String(128)),
        sa.Column("note", sa.Text()),
        sa.Column("retrieved_at", sa.String(64)),
        sa.Column("generation_timestamp", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("analyst_notes", sa.Text()),
        sa.Column("reviewed_by", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime()),
    )
    op.create_index("idx_dim_evidence_entity_dimension", "tefca_dimension_evidence",
                    ["entity_id", "evidence_dimension"])
    op.create_index("idx_dim_evidence_generation", "tefca_dimension_evidence",
                    ["entity_id", "generation_timestamp"])
    op.create_index("ix_tefca_dimension_evidence_entity_id", "tefca_dimension_evidence",
                    ["entity_id"])
    op.create_index("ix_tefca_dimension_evidence_review_id", "tefca_dimension_evidence",
                    ["review_id"])
    op.create_index("ix_tefca_dimension_evidence_source", "tefca_dimension_evidence",
                    ["source"])
    op.create_index("ix_tefca_dimension_evidence_dimension", "tefca_dimension_evidence",
                    ["evidence_dimension"])


def downgrade() -> None:
    # Dropping this table destroys evidence that determinations cite. The
    # downgrade exists so the migration chain is complete and reversible in a
    # development database; it should not be run against an environment whose
    # determinations have been issued.
    op.drop_index("ix_tefca_dimension_evidence_dimension", table_name="tefca_dimension_evidence")
    op.drop_index("ix_tefca_dimension_evidence_source", table_name="tefca_dimension_evidence")
    op.drop_index("ix_tefca_dimension_evidence_review_id", table_name="tefca_dimension_evidence")
    op.drop_index("ix_tefca_dimension_evidence_entity_id", table_name="tefca_dimension_evidence")
    op.drop_index("idx_dim_evidence_generation", table_name="tefca_dimension_evidence")
    op.drop_index("idx_dim_evidence_entity_dimension", table_name="tefca_dimension_evidence")
    op.drop_table("tefca_dimension_evidence")
