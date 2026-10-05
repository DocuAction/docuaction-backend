"""Add 'PREFLIGHT' to rce_delivery_stage_events.stage's allowed values.

Revision ID: 20261004_stage_event_preflight
Revises: 20261004_preflight_exec_held
Create Date: 2026-10-04

WHY THIS EXISTS
----------------
delivery_runner._stage_preflight (Round 22) opens a stage event named
"PREFLIGHT" -- but only when ENABLE_PREFLIGHT_ENFORCEMENT is on; see
docs/review/DELTA-2026-10-04.md and traceability_models.STAGE_EVENT_STAGES,
which already lists "PREFLIGHT" in Python. rce_delivery_stage_events.stage
carries its own DB-level CHECK constraint
(`ck_rce_stage_event_stage`) that this migration widens to match --
found the hard way, via a real CheckViolationError raised by a real
local test run against a disposable database (not caught by code
reading alone), confirming the migration is actually necessary and not
just defensive.

Purely additive: no existing value removed or renamed, no existing row
changes (nothing wrote 'PREFLIGHT' before this round, and nothing can
until ENABLE_PREFLIGHT_ENFORCEMENT is explicitly turned on). Local/
disposable-database use only -- NOT dispatched against any shared or
production database.
"""
from __future__ import annotations

from alembic import op

revision = "20261004_stage_event_preflight"
down_revision = "20261004_preflight_exec_held"
branch_labels = None
depends_on = None

_NAME = "ck_rce_stage_event_stage"
_STAGES_OLD = (
    "REGISTERED", "RECEIPT_PRESERVED", "SHA256", "SCHEMA_VALIDATION", "PARSING",
    "QUALITY", "CURATION", "MATCHING", "PROMOTION", "RELATIONSHIPS",
    "VERIFICATION_READINESS", "RECONCILIATION", "READY_FOR_REVIEW",
    "REPORT_GENERATION",
)
_STAGES_NEW = (
    "REGISTERED", "RECEIPT_PRESERVED", "SHA256", "SCHEMA_VALIDATION", "PARSING",
    "PREFLIGHT",
    "QUALITY", "CURATION", "MATCHING", "PROMOTION", "RELATIONSHIPS",
    "VERIFICATION_READINESS", "RECONCILIATION", "READY_FOR_REVIEW",
    "REPORT_GENERATION",
)


def _in_list(values) -> str:
    return ", ".join("'%s'" % v for v in values)


def upgrade() -> None:
    op.drop_constraint(_NAME, "rce_delivery_stage_events", type_="check")
    op.create_check_constraint(_NAME, "rce_delivery_stage_events",
                               "stage IN (%s)" % _in_list(_STAGES_NEW))


def downgrade() -> None:
    op.drop_constraint(_NAME, "rce_delivery_stage_events", type_="check")
    op.create_check_constraint(_NAME, "rce_delivery_stage_events",
                               "stage IN (%s)" % _in_list(_STAGES_OLD))
