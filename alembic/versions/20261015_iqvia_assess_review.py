"""DRAFT -- NOT APPLIED to any database. Optional review link on IQVIA advisory assessments.

Revision ID: 20261015_iqvia_assess_review
Revises: 20261014_iqvia_advisory_assess
Create Date: 2026-10-15

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 28 characters.

WHAT IT ADDS (and only this)
  iqvia_advisory_assessment.review_id VARCHAR(20) NULL, plus one partial index on it. The written workflow (WP6-C
  specification) attaches advisory evidence to the REVIEW it informs; the released table carried only the delivery
  and an optional entity. The column is nullable on purpose: delivery-level evidence stays permitted, and no entity
  is ever required. Existing rows (none on DEV at the time of writing) read as review_id NULL.

WHY A FOREIGN KEY, AND ITS PREREQUISITE
  review_id references review_records(review_id) (UNIQUE, NOT NULL) ON DELETE RESTRICT: the database, not only the
  application, refuses an assessment that names a review that does not exist, and a referenced review cannot be
  deleted. review_records is owned by the application role, so creating the key needs the REFERENCES privilege on it
  for the role running this migration (docuaction_owner). A foreign key checks existence ONLY; that the review belongs
  to the same delivery and that the entity is part of that review stay server-side checks (REVIEW_NOT_FOUND,
  REVIEW_NOT_IN_DELIVERY, ENTITY_NOT_IN_REVIEW) because a column-level constraint cannot express them without a
  composite key on review_records that this change must not add.
  upgrade() therefore checks the prerequisite FIRST and, when it is missing, stops before changing anything with the
  exact statement an administrator must run once (it grants nothing itself):
      GRANT REFERENCES ON TABLE public.review_records TO <migration role>;
  Any existing review_id value that matches no review also stops the migration (none can exist: the column is new).

SAFETY
  Additive: one nullable column, one index and one foreign key on an append-only table. No row is touched (the UPDATE/DELETE trigger
  is row-level and is not involved in ALTER TABLE). Table-level grants (SELECT, INSERT to the app role) cover the new
  column, so no GRANT is issued here and the app role still has no UPDATE/DELETE/TRUNCATE. downgrade() drops the
  constraint, the index and the column (the review links recorded in the meantime are lost; copy them first if any rows exist).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_iqvia_assess_review"
down_revision = "20261014_iqvia_advisory_assess"
branch_labels = None
depends_on = None

TABLE = "iqvia_advisory_assessment"


FK_NAME = "fk_iqvia_assess_review"
REFERENCED = "review_records"


def _require_references_privilege() -> None:
    """Stop with the exact remedy BEFORE any change when the migration role cannot reference review_records."""
    bind = op.get_bind()
    row = bind.execute(sa.text(
        "SELECT current_user::text, pg_get_userbyid(c.relowner)::text, "
        "has_table_privilege(current_user, c.oid, 'REFERENCES') "
        "FROM pg_class c WHERE c.oid = to_regclass('public.review_records')")).one_or_none()
    if row is None:
        raise RuntimeError("review_records does not exist; the review link cannot be added.")
    role, owner, can_reference = row
    if not can_reference:
        raise RuntimeError(
            f"PREREQUISITE MISSING: {role} lacks REFERENCES on public.review_records (owner {owner}). Nothing was changed. "
            f"An administrator must run once: GRANT REFERENCES ON TABLE public.review_records TO {role}; "
            "then re-run this migration.")


def upgrade() -> None:
    _require_references_privilege()
    op.add_column(TABLE, sa.Column("review_id", sa.String(20), nullable=True))
    op.create_foreign_key(FK_NAME, TABLE, REFERENCED, ["review_id"], ["review_id"], ondelete="RESTRICT")
    op.create_index("ix_iqvia_assess_review", TABLE, ["review_id"], postgresql_where=sa.text("review_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_iqvia_assess_review", table_name=TABLE)
    op.drop_constraint(FK_NAME, TABLE, type_="foreignkey")
    op.drop_column(TABLE, "review_id")
