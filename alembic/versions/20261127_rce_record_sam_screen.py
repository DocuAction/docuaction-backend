"""Record-scoped SAM name-only screening results, append-only, anchored to a delivered source record.

Revision ID: 20261127_rce_record_sam_screen
Revises: 20261115_rce_npi_research
Create Date: 2026-11-27

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 30 characters.

STATUS: NOT APPLIED ANYWHERE SHARED. Writing this file applies nothing. It must be applied ONLY through the governed
DEV release/migration path. Until it is applied, a name-only screening call is refused cleanly
(NAME_ONLY_STORE_NOT_APPLIED / HTTP 409) and no existing behaviour changes.

WHY A NEW TABLE. A name-only SAM public-extract screen of a delivered record that submitted NO NPI screens THAT
record's original submitted name. The independent review of PR #258 found that writing its result to the entity
evidence store (`tefca_dimension_evidence`, keyed by ENTITY, not by source record) would let one record's name-only
result stand for the whole entity: the entity would count as covered for the edition and a sibling record that
submitted an NPI could be read as screened. The owner authorized record-scoped storage (2026-11-27).

WHAT IT ADDS - rce_record_sam_screenings: one row per (delivered record, edition, basis), anchored to the immutable
rce_source_records.id and its intake. It binds the basis (SUBMITTED_VALUES_NAME_ONLY: the record's original
submitted name, no NPI sent; the name itself is NOT copied here), identity_confirmed = false (CHECK), the edition
(filename, publication date, SHA-256), the outcome (CANDIDATE | NO_CANDIDATE | NOT_SCREENED), a CANDIDATE's open
SAM_EXTRACT_CANDIDATE finding, and the screening time (clock_timestamp()) and actor. BEFORE INSERT trigger: the
record must be in the named intake, and a linked finding must be on the same record. BEFORE UPDATE OR DELETE
trigger: append-only independent of roles.

SAFETY. Additive only: one table, two functions, two triggers, three indexes, GRANT SELECT, INSERT to DB_APP_ROLE.
No existing object is altered. Entity coverage, the classifier, the QA gate and the hold logic never read it.
downgrade() takes ACCESS EXCLUSIVE and refuses on a populated table: a screen's outcome is evidence.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "20261127_rce_record_sam_screen"
down_revision = "20261115_rce_npi_research"
branch_labels = None
depends_on = None

TABLE = "rce_record_sam_screenings"


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if op.get_context().as_sql:
        return role or "docuaction_app"
    if not role:
        raise RuntimeError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges on rce_record_sam_screenings to a "
            "named application role and refuses to guess which role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_intake_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_record_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("canonical_entity_id", pg.UUID(as_uuid=True)),
        sa.Column("screen_basis", sa.String(40), nullable=False),
        sa.Column("identity_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("extract_sha256", sa.String(64), nullable=False),
        sa.Column("extract_filename", sa.Text()),
        sa.Column("publication_date", sa.String(10)),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("candidate_count", sa.Integer()),
        sa.Column("not_screened_reason", sa.Text()),
        sa.Column("issue_id", pg.UUID(as_uuid=True), sa.ForeignKey("rce_issues.id", ondelete="RESTRICT")),
        sa.Column("screened_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("screened_by", sa.String(320), nullable=False),
        sa.Column("correlation_id", sa.String(64)),
        sa.UniqueConstraint("source_record_id", "extract_sha256", "screen_basis",
                            name="uq_rce_record_sam_screening_record_edition_basis"),
        sa.CheckConstraint("screen_basis IN ('SUBMITTED_VALUES_NAME_ONLY')", name="ck_rce_record_sam_screening_basis"),
        sa.CheckConstraint("outcome IN ('CANDIDATE','NO_CANDIDATE','NOT_SCREENED')",
                           name="ck_rce_record_sam_screening_outcome"),
        sa.CheckConstraint("identity_confirmed = false", name="ck_rce_record_sam_screening_identity_not_confirmed"),
        sa.CheckConstraint("extract_sha256 ~ '^[0-9a-f]{64}$'", name="ck_rce_record_sam_screening_sha"),
        sa.CheckConstraint("(outcome = 'CANDIDATE') = (issue_id IS NOT NULL)",
                           name="ck_rce_record_sam_screening_candidate_has_finding"),
    )
    op.create_index("ix_rce_record_sam_screening_intake_edition", TABLE, ["source_intake_id", "extract_sha256"])
    op.create_index("ix_rce_record_sam_screening_record", TABLE, ["source_record_id"])
    op.create_index("ix_rce_record_sam_screening_entity", TABLE, ["canonical_entity_id"])

    op.execute("""
        CREATE FUNCTION rce_record_sam_screening_guard() RETURNS trigger AS $$
        DECLARE
            rec_intake uuid;
            issue_record uuid;
        BEGIN
            SELECT source_intake_id INTO rec_intake FROM rce_source_records WHERE id = NEW.source_record_id;
            IF rec_intake IS DISTINCT FROM NEW.source_intake_id THEN
                RAISE EXCEPTION 'rce_record_sam_screenings: source record % does not belong to intake %',
                    NEW.source_record_id, NEW.source_intake_id;
            END IF;
            IF NEW.issue_id IS NOT NULL THEN
                SELECT source_record_id INTO issue_record FROM rce_issues WHERE id = NEW.issue_id;
                IF issue_record IS DISTINCT FROM NEW.source_record_id THEN
                    RAISE EXCEPTION 'rce_record_sam_screenings: the linked finding is not on record %',
                        NEW.source_record_id;
                END IF;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_rce_record_sam_screening_guard BEFORE INSERT ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION rce_record_sam_screening_guard()
    """)
    op.execute("""
        CREATE FUNCTION rce_record_sam_screening_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'rce_record_sam_screenings is append-only: a screen is recorded once';
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_rce_record_sam_screening_append_only BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION rce_record_sam_screening_append_only()
    """)
    op.execute(f'GRANT SELECT, INSERT ON "{TABLE}" TO "{_app_role()}"')


def downgrade() -> None:
    # A screen's outcome is evidence: lock FIRST, check, drop under the same lock.
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    populated = op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE} LIMIT 1)")).scalar()
    if populated:
        raise RuntimeError(
            f"{TABLE} holds recorded screens; this downgrade refuses to drop it. "
            "Export and clear the table through the governed evidence path first.")
    op.execute(f"DROP TRIGGER IF EXISTS trg_rce_record_sam_screening_append_only ON {TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_rce_record_sam_screening_guard ON {TABLE}")
    op.execute("DROP FUNCTION IF EXISTS rce_record_sam_screening_append_only()")
    op.execute("DROP FUNCTION IF EXISTS rce_record_sam_screening_guard()")
    op.drop_table(TABLE)
