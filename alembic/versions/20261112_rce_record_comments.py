"""Append-only, non-decision record comments (QA original / second reviewer / report clarification).

Revision ID: 20261112_rce_record_comments
Revises: 20261108_iqvia_advisory_outcome
Create Date: 2026-11-12

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 28 characters.

STATUS: implements the comment-storage approach the owner selected on 2026-11-12 (a
dedicated append-only, non-decision comment model). It must be applied ONLY through
the governed DEV release/migration path with its required authorization; writing this
file applies nothing. Until it is applied, the comment service refuses cleanly
(COMMENT_STORE_NOT_APPLIED) and no existing behaviour changes.

WHAT IT ADDS
  rce_record_comments: one row per comment VERSION, anchored to the immutable
  rce_source_records.id and its intake; three kinds (QA_ORIGINAL, QA_IZAAN,
  REPORT_CLARIFICATION) kept separate; verbatim text + sha256; source
  file/sha256/sheet/column/row; available author attribution (NULL = unknown);
  authored_at (NULL = unknown, never defaulted); the real import actor and
  timestamp; a batch id; an idempotency key (UNIQUE); an optional
  supersedes_comment_id for amendments (linear: at most one successor).
  CHECK non_decision = true pins the row as NOT a determination / resolution / QA
  approval.
  A BEFORE INSERT trigger refuses a row whose source record is not in the named
  intake, and an amendment that changes record or kind. A BEFORE UPDATE OR DELETE
  trigger makes the table append-only independent of database roles.

SAFETY
  Additive only: one table, two functions, two triggers, four indexes, and GRANT
  SELECT, INSERT to DB_APP_ROLE (owner-owned like the other append-only evidence
  tables; the app never owns it, never UPDATEs, never DELETEs). No existing object
  is altered; the classifier, QA gate, issue ledger and hold logic never read it.
  downgrade() refuses on a populated table: stored comments are evidence.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "20261112_rce_record_comments"
down_revision = "20261108_iqvia_advisory_outcome"
branch_labels = None
depends_on = None

TABLE = "rce_record_comments"


def _app_role() -> str:
    """The runtime role that gets SELECT+INSERT (append-only by grant, as
    20261108_iqvia_advisory_outcome). Refuses to guess the role online."""
    role = os.getenv("DB_APP_ROLE", "").strip()
    if op.get_context().as_sql:
        return role or "docuaction_app"
    if not role:
        raise RuntimeError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges on "
            "rce_record_comments to a named application role and refuses to guess "
            "which role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_intake_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_record_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("comment_kind", sa.String(24), nullable=False),
        sa.Column("comment_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("source_file_name", sa.Text(), nullable=False),
        sa.Column("source_file_sha256", sa.String(64), nullable=False),
        sa.Column("source_sheet", sa.Text()),
        sa.Column("source_column", sa.Text()),
        sa.Column("source_row", sa.Integer()),
        sa.Column("author_attribution", sa.Text()),
        sa.Column("attribution_basis", sa.Text()),
        sa.Column("authored_at", sa.DateTime(timezone=True)),
        sa.Column("identity_check", pg.JSONB(), nullable=False),
        sa.Column("imported_by", sa.String(320), nullable=False),
        # clock_timestamp(), NOT now(): now() is the TRANSACTION start, so every row of
        # a batch would tie and the version history could not be ordered by time.
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("import_batch_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("supersedes_comment_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey(f"{TABLE}.id", ondelete="RESTRICT")),
        sa.Column("non_decision", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("idempotency_key", name="uq_rce_record_comment_idem"),
        sa.CheckConstraint(
            "comment_kind IN ('QA_ORIGINAL','QA_IZAAN','REPORT_CLARIFICATION')",
            name="ck_rce_record_comment_kind"),
        sa.CheckConstraint("non_decision = true", name="ck_rce_record_comment_not_a_decision"),
        sa.CheckConstraint("comment_text <> ''", name="ck_rce_record_comment_text"),
        sa.CheckConstraint(
            "char_length(text_sha256) = 64 AND char_length(source_file_sha256) = 64",
            name="ck_rce_record_comment_hashes"),
    )
    op.create_index("uq_rce_record_comment_successor", TABLE, ["supersedes_comment_id"],
                    unique=True, postgresql_where=sa.text("supersedes_comment_id IS NOT NULL"))
    # ONE ROOT per (record, kind). The successor index alone bounds each node to one
    # child but does not stop two concurrent first imports from each inserting a
    # different ROOT (both saw "no existing comment"). With exactly one root and at
    # most one successor per node, the set of rows for a record+kind is one linear
    # chain. The loser of a race gets a unique violation, which the service reports
    # as needs_review (different text) or already_present (same text).
    op.create_index("uq_rce_record_comment_root", TABLE, ["source_record_id", "comment_kind"],
                    unique=True, postgresql_where=sa.text("supersedes_comment_id IS NULL"))
    op.create_index("ix_rce_record_comment_record", TABLE,
                    ["source_intake_id", "source_record_id", "comment_kind", "imported_at"])
    op.create_index("ix_rce_record_comment_batch", TABLE, ["import_batch_id"])

    op.execute("""
        CREATE FUNCTION rce_record_comments_guard() RETURNS trigger AS $$
        DECLARE
            rec_intake uuid;
            prev_record uuid;
            prev_kind text;
        BEGIN
            SELECT source_intake_id INTO rec_intake
              FROM rce_source_records WHERE id = NEW.source_record_id;
            IF rec_intake IS DISTINCT FROM NEW.source_intake_id THEN
                RAISE EXCEPTION 'rce_record_comments: source record % does not belong to intake %',
                    NEW.source_record_id, NEW.source_intake_id;
            END IF;
            IF NEW.supersedes_comment_id IS NOT NULL THEN
                SELECT source_record_id, comment_kind INTO prev_record, prev_kind
                  FROM rce_record_comments WHERE id = NEW.supersedes_comment_id;
                IF prev_record IS DISTINCT FROM NEW.source_record_id
                   OR prev_kind IS DISTINCT FROM NEW.comment_kind THEN
                    RAISE EXCEPTION 'rce_record_comments: an amendment must keep the same source record and kind';
                END IF;
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_rce_record_comments_guard BEFORE INSERT ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION rce_record_comments_guard()
    """)
    op.execute("""
        CREATE FUNCTION rce_record_comments_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'rce_record_comments is append-only: amend with a new row (supersedes_comment_id)';
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER trg_rce_record_comments_append_only BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION rce_record_comments_append_only()
    """)
    op.execute(f'GRANT SELECT, INSERT ON "{TABLE}" TO "{_app_role()}"')


def downgrade() -> None:
    # Stored comments are evidence: never drop a populated table.
    populated = op.get_bind().execute(sa.text(
        f"SELECT EXISTS (SELECT 1 FROM {TABLE} LIMIT 1)")).scalar()
    if populated:
        raise RuntimeError(
            f"{TABLE} holds stored comments; this downgrade refuses to drop it. "
            "Export and clear the table through the governed evidence path first.")
    op.execute(f"DROP TRIGGER IF EXISTS trg_rce_record_comments_append_only ON {TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_rce_record_comments_guard ON {TABLE}")
    op.execute("DROP FUNCTION IF EXISTS rce_record_comments_append_only()")
    op.execute("DROP FUNCTION IF EXISTS rce_record_comments_guard()")
    op.drop_table(TABLE)
