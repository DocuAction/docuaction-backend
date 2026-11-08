"""rce_source_policies — versioned mandatory-source policy storage (M-1..M-4).

Revision ID: 20261108_source_policy
Revises: 20261107_run_assessment_meta
Create Date: 2026-11-08

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 22
characters.

WHY THIS EXISTS
---------------
The M-decisions (which sources gate B1, D-11 Part 2) are pending. When they
are recorded, the application needs a versioned, approval-referenced place to
hold them and an enforcement hook that reads it (rce/source_policy.py). This
revision ships the STORAGE ONLY, empty: no row is seeded, `is_active`
defaults false, and with no active row the classification path is
byte-identical to before this migration.

WHAT IT ADDS
  rce_source_policies (id, version, source, role[mandatory|conditional|
  advisory], applicability JSONB, policy_reference, approval_reference,
  is_active default false, created_at) + SELECT for the runtime role — the
  app only ever reads it; rows are written by owner-side governed acts.

SAFETY
  - Additive only; empty table; no behavior change by itself.
  - Guarded for the managed-convergence fixture (models-shaped database
    already has the table), same discipline as 20261107_run_assessment_meta.
"""

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261108_source_policy"
down_revision = "20261107_run_assessment_meta"
branch_labels = None
depends_on = None

TABLE = "rce_source_policies"


def _offline() -> bool:
    return op.get_context().as_sql


def _has_table() -> bool:
    if _offline():
        return False
    return sa.inspect(op.get_bind()).has_table(TABLE)


def _role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction"
    if not role:
        role = op.get_bind().execute(sa.text("SELECT current_user")).scalar()
    return role


def upgrade() -> None:
    if not _has_table():
        op.create_table(
            TABLE,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("version", sa.Integer, nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("applicability", JSONB, nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("policy_reference", sa.Text, nullable=False),
            sa.Column("approval_reference", UUID(as_uuid=True), nullable=False),
            sa.Column("is_active", sa.Boolean, nullable=False,
                      server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime, nullable=False,
                      server_default=sa.func.now()),
            sa.CheckConstraint("role IN ('mandatory','conditional','advisory')",
                               name="ck_rce_source_policy_role"),
        )
        op.create_index("idx_rce_source_policy_active", TABLE,
                        ["is_active", "source"])
    # Read-only for the runtime role either way (idempotent grant).
    op.execute(f'GRANT SELECT ON {TABLE} TO "{_role()}"')


def downgrade() -> None:
    op.drop_table(TABLE)
