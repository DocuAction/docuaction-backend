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
  + column-level UPDATE on exactly that column for the runtime role, same
  fail-closed discipline as 20260830_run_lifecycle_grant (the quality engine
  writes the annotation on the run row it is finishing; the grant test
  asserts code and grants agree column-for-column).

SAFETY
  - Additive only: one nullable column, no default, no backfill, no index.
    Existing rows read NULL (honest: they were never annotated).
  - Changes no behavior by itself. The pinned-date override that WRITES a
    non-run-start date is a separate, inactive proposal
    (RCE_PROPOSED_ASSESSMENT_AS_OF, default off, gated on the D-12 decision).
"""

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261107_run_assessment_meta"
down_revision = "20261106_iqvia_hcp_npi_index"
branch_labels = None
depends_on = None

TABLE = "rce_ingestion_runs"

#: The lifecycle column this revision adds and grants. Kept as a tuple so the
#: grant test can assert the grants and the code agree (union with
#: 20260830_run_lifecycle_grant.LIFECYCLE_COLUMNS).
GRANTED_COLUMNS = ("assessment_metadata",)


class RunLifecycleTargetError(RuntimeError):
    """The target role owns the table, so a grant would prove nothing."""


def _offline() -> bool:
    return op.get_context().as_sql


def _has_column(table: str, column: str) -> bool:
    """Same guard as 20260918_post_promotion_verification: the managed
    convergence path replays this chain against a models-shaped fixture
    database stamped at the legacy baseline, where the column already exists.
    ADD COLUMN must therefore be conditional; the GRANT below is applied
    unconditionally either way (re-granting is a no-op)."""
    if _offline():
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _owner_of(table: str):
    return op.get_bind().execute(
        sa.text("select tableowner from pg_tables where schemaname='public' "
                "and tablename=:t"), {"t": table}).scalar()


def _role() -> str:
    """Same fail-closed role resolution as 20260830_run_lifecycle_grant."""
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction"
    if not role:
        role = op.get_bind().execute(sa.text("SELECT current_user")).scalar()
    if _owner_of(TABLE) == role:
        raise RunLifecycleTargetError(
            f"{role!r} OWNS {TABLE}, so a column-level UPDATE grant would be a "
            f"no-op that looks applied. Set DB_APP_ROLE to the non-owning "
            f"runtime role (docuaction_app).")
    return role


def upgrade() -> None:
    if not _has_column(TABLE, "assessment_metadata"):
        op.add_column(TABLE,
                      sa.Column("assessment_metadata", JSONB(), nullable=True))
    role = _role()
    columns = ", ".join(GRANTED_COLUMNS)
    op.execute(f'GRANT UPDATE ({columns}) ON {TABLE} TO "{role}"')


def downgrade() -> None:
    # Dropping the column removes its column-level grant with it.
    op.drop_column(TABLE, "assessment_metadata")
