"""DRAFT -- NOT APPLIED to any database. IQVIA advisory assessment evidence table (append-only).

Revision ID: 20261014_iqvia_advisory_assess
Revises: 20261009_iqvia_affil_org_indexes
Create Date: 2026-10-14

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 30 characters.

STATUS: draft for review. It must be applied only through the governed migration path, after the
draft PR is approved and merged. Nothing in CI applies it to a shared database. Until it is applied the
feature flag ENABLE_IQVIA_AFFILIATION_CONSUMPTION must stay OFF (the assessment routes and the report
disclosure query read/write this table only when that flag is on).

WHAT IT ADDS
  iqvia_advisory_assessment: one row per analyst assessment of an organisation-first IQVIA lookup
  (snapshot id/status/sha prefix, masked + keyed-hash query inputs, result summary, decision,
  rationale, assessor, role, timestamp). CHECK constraints pin determination to
  'ANALYST_ADVISORY_NOT_VERIFICATION' and affects_verification to false: a row can never be read as
  verification or exclusion clearance.
  A trigger rejects UPDATE and DELETE (append-only, independent of database roles).

SAFETY
  Additive only: a new table, one function, one trigger, three indexes. No existing object is altered.
  downgrade() drops them (data in the table is lost; take a copy first if any rows exist).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "20261014_iqvia_advisory_assess"
down_revision = "20261009_iqvia_affil_org_indexes"
branch_labels = None
depends_on = None

TABLE = "iqvia_advisory_assessment"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", pg.UUID(as_uuid=True), sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("snapshot_status", sa.String(16), nullable=False),
        sa.Column("snapshot_sha256_prefix", sa.String(12), nullable=False),
        sa.Column("snapshot_record_count", sa.Integer()),
        sa.Column("query_inputs", pg.JSONB(), nullable=False),
        sa.Column("result_summary", pg.JSONB(), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True)),
        sa.Column("delivery_intake_id", pg.UUID(as_uuid=True)),
        sa.Column("determination", sa.String(40), nullable=False, server_default=sa.text("'ANALYST_ADVISORY_NOT_VERIFICATION'")),
        sa.Column("affects_verification", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("assessed_by", sa.String(320), nullable=False),
        sa.Column("assessed_role", sa.String(64), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
        sa.CheckConstraint("affects_verification = false", name="ck_iqvia_assess_not_verification"),
        sa.CheckConstraint("determination = 'ANALYST_ADVISORY_NOT_VERIFICATION'", name="ck_iqvia_assess_determination"),
        sa.CheckConstraint(
            "decision IN ('IDENTITY_CORROBORATED_ADVISORY','RELATIONSHIP_CORROBORATED_ADVISORY',"
            "'NOT_FOUND_IN_EXTRACT','CONFLICT_REFERRED','INSUFFICIENT_EVIDENCE')", name="ck_iqvia_assess_decision"),
    )
    op.create_index("ix_iqvia_assess_snapshot", TABLE, ["snapshot_id", "assessed_at"])
    op.create_index("ix_iqvia_assess_intake", TABLE, ["delivery_intake_id"], postgresql_where=sa.text("delivery_intake_id IS NOT NULL"))
    op.create_index("ix_iqvia_assess_assessed_at", TABLE, ["assessed_at"])
    op.execute("""
        CREATE FUNCTION iqvia_advisory_assessment_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'iqvia_advisory_assessment is append-only';
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_iqvia_assess_append_only BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION iqvia_advisory_assessment_append_only()
    """)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_iqvia_assess_append_only ON {TABLE}")
    op.execute("DROP FUNCTION IF EXISTS iqvia_advisory_assessment_append_only()")
    op.drop_table(TABLE)
