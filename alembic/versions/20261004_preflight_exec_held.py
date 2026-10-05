"""Add 'held' to rce_preflight_finding.execution's allowed values.

Revision ID: 20261004_preflight_exec_held
Revises: 20261003_preflight_shadow
Create Date: 2026-10-04

WHY THIS EXISTS
----------------
docs/review/DELTA-2026-10-04.md (Round 22, Part A) adds EXEC_HELD to
preflight_shadow_models.EXECUTION -- additive in Python, but
rce_preflight_finding's execution column carries a DB-level CHECK
constraint (`ck_rce_preflight_finding_execution`) that did not include
'held'. Without this migration, the Python-side tuple would accept the
value while a real INSERT using it would fail with a CheckViolation --
the exact kind of code/schema mismatch this architecture exists to avoid.

Purely additive: widens the allowed set, does not remove or rename any
existing value, and no existing row's `execution` value changes (none
of them are 'held' today -- nothing in this round's code writes it yet;
see the delta's own P1 note that EXEC_HELD is "not yet wired to any new
code path"). Local/disposable-database use only, consistent with this
round's "local development and disposable databases only" boundary --
NOT dispatched against any shared or production database.
"""
from __future__ import annotations

from alembic import op

revision = "20261004_preflight_exec_held"
down_revision = "20261003_preflight_shadow"
branch_labels = None
depends_on = None

_OLD = "execution IN ('done','unavailable','insufficient')"
_NEW = "execution IN ('done','unavailable','insufficient','held')"
_NAME = "ck_rce_preflight_finding_execution"


def upgrade() -> None:
    op.drop_constraint(_NAME, "rce_preflight_finding", type_="check")
    op.create_check_constraint(_NAME, "rce_preflight_finding", _NEW)


def downgrade() -> None:
    op.drop_constraint(_NAME, "rce_preflight_finding", type_="check")
    op.create_check_constraint(_NAME, "rce_preflight_finding", _OLD)
