"""alembic_version — SELECT for the app role, so the deployed revision is reportable.

Revision ID: 20260930_alembic_version_read
Revises: 20260921_september_snapshot
Create Date: 2026-09-30

WHAT THIS FIXES (MQA-2026-014 / SMK-007)
----------------------------------------
Every build/provenance surface — the delivery detail "Build and correlation"
panel, /api/admin/health, the CSV provenance header, the reconciliation
snapshot's `migration_revision` column — reads

    SELECT version_num FROM alembic_version

as the runtime role. `alembic_version` is owned by the migration role
(`docuaction_owner`) and the app role was never granted anything on it, so on
DEV the read fails with `permission denied for table alembic_version` and the
code — correctly — reports `unknown` rather than guessing. QA reproduced this
on the locked build (2026-09-30) and on an isolated database at the same head.

This grants exactly one privilege: SELECT on that one-row table. No INSERT,
UPDATE or DELETE: the app must be able to SAY which revision it runs against,
never to change it.

WHY THIS DOES NOT WEAKEN ANYTHING
---------------------------------
`alembic_version` holds one value: the current schema revision id, which is
already public in the repository's migration chain and in every governed
deploy log. Reading it exposes no Government data and no credential.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "20260930_alembic_version_read"
down_revision = "20260921_september_snapshot"
branch_labels = None
depends_on = None

TABLE = "alembic_version"


class AlembicVersionGrantTargetError(RuntimeError):
    """The target role owns the table, so a grant would prove nothing."""


def _offline() -> bool:
    return op.get_context().as_sql


def _owner_of(table: str):
    return op.get_bind().execute(
        sa.text("select tableowner from pg_tables where schemaname='public' "
                "and tablename=:t"), {"t": table}).scalar()


def _role() -> str:
    """The application role being granted. Fails closed, as 20260830 does:
    granting the OWNER a privilege it already holds would look applied while
    enforcing nothing."""
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction"
    if not role:
        raise AlembicVersionGrantTargetError(
            f"{revision}: DB_APP_ROLE is not set. This migration grants a runtime "
            f"privilege to the non-owning application role; without a named role "
            f"it would silently target current_user. Re-run with "
            f"DB_APP_ROLE=docuaction_app.")
    if _owner_of(TABLE) == role:
        raise AlembicVersionGrantTargetError(
            f"{role!r} OWNS {TABLE}, so a SELECT grant would be a no-op that "
            f"looks applied. Set DB_APP_ROLE to the non-owning runtime role.")
    return role


def upgrade() -> None:
    role = _role()
    op.execute(f'GRANT SELECT ON {TABLE} TO "{role}"')
    # Deliberately NOT granted: INSERT, UPDATE, DELETE, TRUNCATE.


def downgrade() -> None:
    role = _role()
    op.execute(f'REVOKE SELECT ON {TABLE} FROM "{role}"')
