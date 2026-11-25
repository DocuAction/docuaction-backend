"""Append-only supplemental NPI research, anchored to a delivered source record.

Revision ID: 20261115_rce_npi_research
Revises: 20261112_rce_record_comments
Create Date: 2026-11-15

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 25 characters.

STATUS: NOT APPLIED ANYWHERE SHARED. Writing this file applies nothing. It must be applied ONLY through
the governed DEV release/migration path with its own authorization (separate from any deployment
authorization). Until it is applied the service refuses cleanly (SUPPLEMENTAL_RESEARCH_NOT_APPLIED / HTTP
409) and no existing behaviour changes.

WHY A NEW TABLE. Manual NPPES research on a delivered record (the October Treatment set) has no persisted
home: the report only reads a caller-supplied annotation, the case importer stages notes and applies
ownership only, rce_record_comments is pinned non-decision text, and tefca_dimension_evidence is keyed by
ENTITY, not by source record. Storing research in a correction, resolution or approval would misuse them.

WHAT IT ADDS - rce_supplemental_npi_research: one row per ENTRY, anchored to the immutable
rce_source_records.id and its intake. It keeps, separately:
  * the ORIGINAL submitted NPI exactly as delivered (a snapshot; the database REFUSES a row whose snapshot
    differs from the source record - the delivered record itself is never touched);
  * the RESEARCHED NPI (10 digits, never a replacement for the original);
  * the NPPES reference, the ACTUAL research/check date, the identity-match rationale;
  * the real researcher (authenticated actor) and entry timestamp (clock_timestamp()).
An amendment or withdrawal is a NEW row naming the row it supersedes (linear chain, one root per record,
one successor per row). CHECK non_decision = true pins the row as NOT a correction, resolution, approval or
bucket change. BEFORE INSERT trigger: record must be in the named intake; source_line/hcid/tefca_id must equal
the record's; the original-NPI snapshot must equal the delivered value; a successor stays on the same record.
BEFORE UPDATE OR DELETE trigger: append-only independent of roles.

SAFETY. Additive only: one table, two functions, two triggers, four indexes, GRANT SELECT, INSERT to
DB_APP_ROLE. No existing object is altered; the classifier, QA gate, issue ledger and hold logic never read
it. downgrade() takes ACCESS EXCLUSIVE and refuses on a populated table: stored research is evidence.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "20261115_rce_npi_research"
down_revision = "20261112_rce_record_comments"
branch_labels = None
depends_on = None

TABLE = "rce_supplemental_npi_research"


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if op.get_context().as_sql:
        return role or "docuaction_app"
    if not role:
        raise RuntimeError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges on "
            "rce_supplemental_npi_research to a named application role and refuses to guess which "
            "role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_intake_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_record_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_line", sa.Integer(), nullable=False),
        sa.Column("hcid", sa.String(100)),
        sa.Column("tefca_id", sa.String(100)),
        sa.Column("original_submitted_npi", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("entry_action", sa.String(12), nullable=False),
        sa.Column("researched_npi", sa.String(10)),
        sa.Column("nppes_reference", sa.Text()),
        sa.Column("nppes_check_date", sa.Date()),
        sa.Column("match_rationale", sa.Text()),
        sa.Column("change_reason", sa.Text()),
        sa.Column("researcher", sa.String(320), nullable=False),
        sa.Column("researcher_user_id", pg.UUID(as_uuid=True)),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("supersedes_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey(f"{TABLE}.id", ondelete="RESTRICT")),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("non_decision", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("idempotency_key", name="uq_rce_npi_research_idem"),
        sa.CheckConstraint("entry_action IN ('RECORD','AMEND','WITHDRAW')", name="ck_rce_npi_research_action"),
        sa.CheckConstraint("non_decision = true", name="ck_rce_npi_research_not_a_decision"),
        sa.CheckConstraint(
            "entry_action = 'WITHDRAW' OR (researched_npi IS NOT NULL AND researched_npi ~ '^[0-9]{10}$' "
            "AND nppes_reference IS NOT NULL "
            "AND btrim(nppes_reference) <> '' AND nppes_check_date IS NOT NULL "
            "AND match_rationale IS NOT NULL AND char_length(btrim(match_rationale)) >= 20)",
            name="ck_rce_npi_research_complete"),
        sa.CheckConstraint(
            "entry_action = 'RECORD' OR (change_reason IS NOT NULL AND char_length(btrim(change_reason)) >= 10)",
            name="ck_rce_npi_research_change_reason"),
        sa.CheckConstraint("(entry_action = 'RECORD') = (supersedes_id IS NULL)",
                           name="ck_rce_npi_research_root_is_record"),
    )
    op.create_index("uq_rce_npi_research_successor", TABLE, ["supersedes_id"], unique=True,
                    postgresql_where=sa.text("supersedes_id IS NOT NULL"))
    op.create_index("uq_rce_npi_research_root", TABLE, ["source_record_id"], unique=True,
                    postgresql_where=sa.text("supersedes_id IS NULL"))
    op.create_index("ix_rce_npi_research_record", TABLE,
                    ["source_intake_id", "source_record_id", "entered_at"])
    op.create_index("ix_rce_npi_research_researcher", TABLE, ["researcher", "entered_at"])

    op.execute("""
        CREATE FUNCTION rce_npi_research_guard() RETURNS trigger AS $$
        DECLARE
            rec RECORD;
            delivered text;
            prev_record uuid;
        BEGIN
            SELECT source_intake_id, line_number, hcid, tefcaid, npi, parsed INTO rec
              FROM rce_source_records WHERE id = NEW.source_record_id;
            IF rec.source_intake_id IS DISTINCT FROM NEW.source_intake_id THEN
                RAISE EXCEPTION 'rce_supplemental_npi_research: source record % does not belong to intake %',
                    NEW.source_record_id, NEW.source_intake_id;
            END IF;
            IF rec.line_number IS DISTINCT FROM NEW.source_line
               OR rec.hcid IS DISTINCT FROM NEW.hcid OR rec.tefcaid IS DISTINCT FROM NEW.tefca_id THEN
                RAISE EXCEPTION 'rce_supplemental_npi_research: source_line/hcid/tefca_id do not match record %',
                    NEW.source_record_id;
            END IF;
            IF rec.parsed ? 'NPI' THEN delivered := coalesce(rec.parsed->>'NPI', '');
            ELSE delivered := coalesce(rec.npi, ''); END IF;
            IF NEW.original_submitted_npi IS DISTINCT FROM delivered THEN
                RAISE EXCEPTION 'rce_supplemental_npi_research: the original-NPI snapshot differs from the delivered value';
            END IF;
            IF NEW.supersedes_id IS NOT NULL THEN
                SELECT source_record_id INTO prev_record FROM rce_supplemental_npi_research WHERE id = NEW.supersedes_id;
                IF prev_record IS DISTINCT FROM NEW.source_record_id THEN
                    RAISE EXCEPTION 'rce_supplemental_npi_research: an amendment must stay on the same source record';
                END IF;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_rce_npi_research_guard BEFORE INSERT ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION rce_npi_research_guard()
    """)
    op.execute("""
        CREATE FUNCTION rce_npi_research_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'rce_supplemental_npi_research is append-only: amend with a new row (supersedes_id)';
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_rce_npi_research_append_only BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION rce_npi_research_append_only()
    """)
    op.execute(f'GRANT SELECT, INSERT ON "{TABLE}" TO "{_app_role()}"')


def downgrade() -> None:
    # Stored research is evidence: lock FIRST, check, drop under the same lock.
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    populated = op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE} LIMIT 1)")).scalar()
    if populated:
        raise RuntimeError(
            f"{TABLE} holds stored research; this downgrade refuses to drop it. "
            "Export and clear the table through the governed evidence path first.")
    op.execute(f"DROP TRIGGER IF EXISTS trg_rce_npi_research_append_only ON {TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_rce_npi_research_guard ON {TABLE}")
    op.execute("DROP FUNCTION IF EXISTS rce_npi_research_append_only()")
    op.execute("DROP FUNCTION IF EXISTS rce_npi_research_guard()")
    op.drop_table(TABLE)
