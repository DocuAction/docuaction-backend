"""rce_delivery_jobs: runtime privileges for the application role.

Revision ID: 20260903_delivery_grants
Revises: 20260902_delivery_jobs
Create Date: 2026-09-03

WHY THIS EXISTS
---------------
20260902_delivery_jobs creates `rce_delivery_jobs` and grants nothing. In
development that went unnoticed for every earlier job table because startup
`create_all()` (app/main.py, non-production only) had already created them AS
the runtime role, which owns them and therefore needs no grant. This table was
created by a migration first - the safer order - so `create_all()` found it
existing, skipped it, and the runtime role was left with no privilege at all.
Measured in Azure DEV on 2026-09-02:

    has_table_privilege('docuaction_app', 'rce_delivery_jobs', SELECT)  false
    has_table_privilege('docuaction_app', 'rce_delivery_jobs', INSERT)  false
    has_table_privilege('docuaction_app', 'rce_delivery_jobs', UPDATE)  false
    has_table_privilege('docuaction_app', 'rce_delivery_jobs', DELETE)  false
    relacl                                                              NULL
    pg_default_acl                                                      (none)

Production never runs create_all(), so there this migration is the ONLY way
the runtime role ever gets access.

WHAT IS GRANTED, AND WHY EXACTLY THIS
-------------------------------------
delivery_jobs.py registers a job (INSERT), lists / claims / reads it (SELECT),
and moves it QUEUED -> RUNNING -> SUCCEEDED | FAILED with heartbeats (UPDATE).
There is no delete path: a terminal job keeps its row and clears
`active_marker` (an UPDATE) so the partial unique index releases the identity.
DELETE is therefore NOT granted. No column-level restriction is applied:
unlike Area 1, this table holds no Government-delivered evidence - it is a
receipt/ledger the application owns end to end - so table-level
SELECT/INSERT/UPDATE matches `report_export_jobs`, whose shape this table
deliberately copies.

OWNERSHIP IS ASSERTED, NOT FIXED
--------------------------------
The table must be owned by `docuaction_owner` (the non-login owner role).
This migration refuses to run if it is not: an owner can grant to itself, so
granting to a role that already owns the table would report success while
enforcing nothing (the fail-closed rule 20260828 and 20260830 already apply),
and a table owned by whoever happened to run Alembic is exactly the drift this
chain exists to prevent. Correcting ownership is an explicit, recorded operator
step - never something a migration does silently.
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "20260903_delivery_grants"
down_revision = "20260902_delivery_jobs"
branch_labels = None
depends_on = None

TABLE = "rce_delivery_jobs"
OWNER = "docuaction_owner"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE")


class DeliveryGrantTargetError(RuntimeError):
    """Raised instead of guessing. Same discipline as RunLifecycleTargetError."""


def _offline() -> bool:
    return op.get_context().as_sql


def _owner_of(table: str):
    return op.get_bind().execute(
        sa.text("select tableowner from pg_tables where schemaname='public' "
                "and tablename=:t"), {"t": table}).scalar()


def _role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise DeliveryGrantTargetError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges to "
            "a NAMED role and will not infer one from current_user - run as the "
            "owner, that would grant the owner to itself and enforce nothing. "
            "Re-run with DB_APP_ROLE=docuaction_app.")
    owner = _owner_of(TABLE)
    if owner != OWNER:
        raise DeliveryGrantTargetError(
            f"{TABLE} is owned by {owner!r}, expected {OWNER!r}. Correct "
            f"ownership as a recorded operator step (ALTER TABLE {TABLE} OWNER "
            f"TO {OWNER}) and re-run; this migration does not change ownership "
            f"silently.")
    if role == owner:
        raise DeliveryGrantTargetError(
            f"DB_APP_ROLE={role!r} owns {TABLE}; a grant to the owner is a no-op "
            f"that looks applied. Set DB_APP_ROLE to the non-owning runtime role.")
    return role


def upgrade() -> None:
    role = _role()
    op.execute(f'GRANT {", ".join(PRIVILEGES)} ON {TABLE} TO "{role}"')
    # Deliberately NOT granted: DELETE, TRUNCATE, REFERENCES, TRIGGER.


def downgrade() -> None:
    role = _role()
    op.execute(f'REVOKE {", ".join(PRIVILEGES)} ON {TABLE} FROM "{role}"')
