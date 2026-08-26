"""rce_ingestion_runs lifecycle columns — column-level UPDATE for the app role.

Revision ID: 20260830_run_lifecycle
Revises: 20260829_report_artifacts
Create Date: 2026-08-26

WHAT THIS FIXES
---------------
Ingesting the real August 21 ONC delivery into dev surfaced this: the quality
engine evaluated all 23,566 source records and produced 36,916 issues, then the
entire transaction rolled back on

    permission denied for table rce_ingestion_runs

`rce_ingestion_runs` is one of the four Area 1 tables owned by
`docuaction_owner`, so `docuaction_app` holds SELECT and INSERT only. But the
quality run has a lifecycle: it INSERTs a row as RUNNING and, at the end,
records what happened. `quality_engine.run_quality_engine` mutates exactly four
columns and nothing else:

    run.completed_at       = <utcnow>
    run.records_evaluated  = <total>
    run.issues_generated   = <sequence>
    run.run_status         = "COMPLETE"

Without them the engine can never mark a run finished, so no run can complete
and no issue can be committed. Every stage after intake was blocked.

WHY THIS DOES NOT WEAKEN AREA 1
-------------------------------
Area 1 protects the GOVERNMENT SOURCE RECORD. That content lives in
`rce_source_records.raw_line` (the delivered line, verbatim) and `.parsed`, and
in `rce_source_intakes` (filename, checksum, receipt). None of it is in this
table.

`rce_ingestion_runs` holds DocuAction's own bookkeeping about a processing run:
which rule set ran, when it started, when it finished, how many records it saw,
how many issues it raised. The worst a compromised app role could do with these
four columns is misreport the status or the counts of one of our own runs. It
cannot alter, delete or hide a single delivered Government value, and the issues
themselves are separate rows in `rce_issues` carrying their own provenance.

The columns deliberately NOT granted are the ones that would let a run
misrepresent its own provenance: `source_intake_id`, `rule_set_version`,
`rule_config_hash`, `field_map_version`, `started_at`, `executed_by`. Those are
written once at INSERT and must stay as written — a run that could rewrite which
rule set it ran under would make its own findings unfalsifiable.

This is the same shape as the existing exception for
`rce_source_records.promotion_status` and `.canonical_entity_id` in
`20260828_area1_grants`: a narrow, column-scoped write for a workflow that
genuinely needs it, rather than table-level UPDATE.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "20260830_run_lifecycle"
down_revision = "20260829_report_artifacts"
branch_labels = None
depends_on = None

TABLE = "rce_ingestion_runs"

#: The only columns the quality run mutates after INSERT. Kept as a tuple so the
#: test can assert the grant and the code agree.
LIFECYCLE_COLUMNS = (
    "run_status",
    "completed_at",
    "records_evaluated",
    "issues_generated",
)


class RunLifecycleTargetError(RuntimeError):
    """The target role owns the table, so a grant would prove nothing."""


def _offline() -> bool:
    return op.get_context().as_sql


def _owner_of(table: str):
    return op.get_bind().execute(
        sa.text("select tableowner from pg_tables where schemaname='public' "
                "and tablename=:t"), {"t": table}).scalar()


def _role() -> str:
    """The application role being granted. Fails closed, as 20260828 does.

    If the target role OWNS the table, granting it column-level UPDATE is
    meaningless — an owner can already update every column — and the migration
    would report success while enforcing nothing. That exact failure was
    measured on this codebase before, so it raises rather than proceeds.
    """
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
    role = _role()
    columns = ", ".join(LIFECYCLE_COLUMNS)
    op.execute(f'GRANT UPDATE ({columns}) ON {TABLE} TO "{role}"')
    # Deliberately NOT granted: table-level UPDATE, DELETE, TRUNCATE, or any
    # privilege on the provenance columns.


def downgrade() -> None:
    role = _role()
    columns = ", ".join(LIFECYCLE_COLUMNS)
    op.execute(f'REVOKE UPDATE ({columns}) ON {TABLE} FROM "{role}"')
