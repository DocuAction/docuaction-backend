"""IQVIA advisory comparison outcome retention table (append-only, bulk lane). D-10.

Revision ID: 20261108_iqvia_advisory_outcome
Revises: 20261106_iqvia_hcp_npi_index
Create Date: 2026-11-08

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 31 characters.

STATUS: implements the D-10 retention decision's recommended shape (persist per-entity
advisory outcomes in an append-only table; advisory stays advisory). It must be applied
only through the governed migration path. Until it is applied, the advisory-comparison
route behaves exactly as before: the optional `persist` flag refuses with
RETENTION_STORE_NOT_APPLIED and the default (persist=false) path never touches the table.

WHAT IT ADDS
  iqvia_advisory_comparison_outcome: one row per (snapshot, entity, run) advisory
  comparison outcome -- presence + address outcome (two facts, never merged), masked
  identifier basis, snapshot provenance (id, root, sha prefix), run correlation, creator,
  timestamp. CHECK constraints pin determination to 'AUTOMATED_CANDIDATE_NOT_CONFIRMED',
  affects_verification to false and requires_analyst_review to true: a row can never be
  read as verification, clearance or a classification input.
  UNIQUE (source_snapshot_id, entity_id, run_id) makes page re-sends idempotent.
  A trigger rejects UPDATE and DELETE (append-only, independent of database roles).

SAFETY
  Additive only: a new table, one function, one trigger, two indexes, and GRANT
  SELECT, INSERT to DB_APP_ROLE (owner-owned like the other append-only evidence
  tables; the app never owns it). No existing object is altered.
  downgrade() refuses on a populated table: retained advisory outcomes are evidence.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "20261108_iqvia_advisory_outcome"
down_revision = "20261108_source_policy"  # re-parented at integration: the worktree base predated the release stack (its head was 20261106); stacking without this would give alembic two heads
branch_labels = None
depends_on = None

TABLE = "iqvia_advisory_comparison_outcome"


def _app_role() -> str:
    """The runtime role that gets SELECT+INSERT (append-only by grant, as
    20261014_iqvia_advisory_assess). Refuses to guess the role online."""
    role = os.getenv("DB_APP_ROLE", "").strip()
    if op.get_context().as_sql:
        return role or "docuaction_app"
    if not role:
        raise RuntimeError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges on "
            "iqvia_advisory_comparison_outcome to a named application role and refuses to guess "
            "which role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_snapshot_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("root_snapshot_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_sha256_prefix", sa.String(12), nullable=False),
        sa.Column("source_system", sa.String(32), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("presence_outcome", sa.String(32), nullable=False),
        sa.Column("address_outcome", sa.String(32), nullable=False),
        sa.Column("identifier_basis", pg.JSONB(), nullable=False),
        sa.Column("outcome_detail", pg.JSONB(), nullable=False),
        sa.Column("determination", sa.String(40), nullable=False,
                  server_default=sa.text("'AUTOMATED_CANDIDATE_NOT_CONFIRMED'")),
        sa.Column("requires_analyst_review", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("affects_verification", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_snapshot_id", "entity_id", "run_id",
                            name="uq_iqvia_adv_outcome_snapshot_entity_run"),
        sa.CheckConstraint("affects_verification = false", name="ck_iqvia_adv_outcome_not_verification"),
        sa.CheckConstraint("requires_analyst_review = true", name="ck_iqvia_adv_outcome_needs_review"),
        sa.CheckConstraint("determination = 'AUTOMATED_CANDIDATE_NOT_CONFIRMED'",
                           name="ck_iqvia_adv_outcome_determination"),
        sa.CheckConstraint("presence_outcome IN ('SUPPORTS','DIFFERS','NO_IQVIA_DATA')",
                           name="ck_iqvia_adv_outcome_presence"),
        sa.CheckConstraint("address_outcome IN ('ADDRESS_CONSISTENT','ADDRESS_DIFFERS',"
                           "'NO_ADDRESS_DATA','NOT_COMPARED')", name="ck_iqvia_adv_outcome_address"),
    )
    op.create_index("ix_iqvia_adv_outcome_run", TABLE, ["run_id", "created_at"])
    op.create_index("ix_iqvia_adv_outcome_entity", TABLE, ["entity_id", "created_at"])
    op.execute("""
        CREATE FUNCTION iqvia_advisory_comparison_outcome_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'iqvia_advisory_comparison_outcome is append-only';
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_iqvia_adv_outcome_append_only BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION iqvia_advisory_comparison_outcome_append_only()
    """)
    op.execute(f'GRANT SELECT, INSERT ON "{TABLE}" TO "{_app_role()}"')


def downgrade() -> None:
    # Retained advisory outcomes are evidence: never drop a populated table.
    populated = op.get_bind().execute(sa.text(
        f"SELECT EXISTS (SELECT 1 FROM {TABLE} LIMIT 1)")).scalar()
    if populated:
        raise RuntimeError(
            f"{TABLE} holds retained advisory outcomes; this downgrade refuses to drop it. "
            "Export and clear the table through the governed evidence path first.")
    op.execute(f"DROP TRIGGER IF EXISTS trg_iqvia_adv_outcome_append_only ON {TABLE}")
    op.execute("DROP FUNCTION IF EXISTS iqvia_advisory_comparison_outcome_append_only()")
    op.drop_table(TABLE)
