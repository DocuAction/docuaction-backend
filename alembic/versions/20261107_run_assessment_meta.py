"""Nullable assessment_metadata JSONB on rce_ingestion_runs (D-12).

Revision ID: 20261107_run_assessment_meta
Revises: 20261106_iqvia_hcp_npi_index
Create Date: 2026-11-07

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 28
characters.

WHY THIS EXISTS
---------------
D-12 (pinned assessment-as-of proposal, 2026-11-07): every quality run should
record what date it assessed AS OF, where that date came from, and which
date-gated rule bases (BR-NPI-TRTMNT basis, 2026-10-25; six-case logic,
2026-10-26) were active — so a historical result is explainable without
re-deriving the gates from the run's wall-clock start.

WHAT IT ADDS
  rce_ingestion_runs.assessment_metadata  JSONB NULL

SAFETY
  - Additive only: one nullable column, no default, no backfill, no index.
    Existing rows read NULL (honest: they were never annotated).
  - Changes no behavior by itself. The pinned-date override that WRITES a
    non-run-start date is a separate, inactive proposal
    (RCE_PROPOSED_ASSESSMENT_AS_OF, default off, gated on the D-12 decision).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261107_run_assessment_meta"
down_revision = "20261106_iqvia_hcp_npi_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rce_ingestion_runs",
                  sa.Column("assessment_metadata", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("rce_ingestion_runs", "assessment_metadata")
