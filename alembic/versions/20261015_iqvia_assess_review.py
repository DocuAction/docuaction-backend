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

WHY NO FOREIGN KEY
  review_records is owned by the application role. A foreign key from this OWNER-owned append-only table to it would
  need a REFERENCES grant for the migration role on an app-owned table, a prerequisite this change must not introduce
  (the governed path would stop on it). The link is validated server-side at write time (REVIEW_NOT_FOUND,
  REVIEW_NOT_IN_DELIVERY, ENTITY_NOT_IN_REVIEW) and rows are never edited, so the value cannot drift; a later
  correction is a NEW row. If a database-level constraint is wanted later, it needs the REFERENCES grant checked first.

SAFETY
  Additive: one nullable column and one index on an append-only table. No row is touched (the UPDATE/DELETE trigger
  is row-level and is not involved in ALTER TABLE). Table-level grants (SELECT, INSERT to the app role) cover the new
  column, so no GRANT is issued here and the app role still has no UPDATE/DELETE/TRUNCATE. downgrade() drops the
  index and the column (the review links recorded in the meantime are lost; copy them first if any rows exist).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_iqvia_assess_review"
down_revision = "20261014_iqvia_advisory_assess"
branch_labels = None
depends_on = None

TABLE = "iqvia_advisory_assessment"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("review_id", sa.String(20), nullable=True))
    op.create_index("ix_iqvia_assess_review", TABLE, ["review_id"], postgresql_where=sa.text("review_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_iqvia_assess_review", table_name=TABLE)
    op.drop_column(TABLE, "review_id")
