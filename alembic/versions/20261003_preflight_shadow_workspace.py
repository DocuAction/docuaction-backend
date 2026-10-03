"""Preflight automation + shadow reassessment pilot evidence tables.

Revision ID: 20261003_preflight_shadow
Revises: 20260930_alembic_version_read
Create Date: 2026-10-03

CHAIN NOTE
----------
This revision descends from `main`'s head (20260930_alembic_version_read)
and is therefore a SIBLING of the unmerged reporting
(20261001_report_generation_jobs) and IQVIA (20261002_iqvia_observations,
20261003_iqvia_upload_durability) revisions. When the branches are combined,
`down_revision` must be relinked onto whichever revision lands last -- the
same relinking the combined integration environment already performs for
the IQVIA chain. The tables created here reference only tables that exist on
`main` (rce_source_intakes, rce_source_records, tefca_reg_entities,
review_records), so the relink is purely a chain-ordering edit.

WHY THIS EXISTS
---------------
Directive items 9-10 (2026-10-02): run a PREFLIGHT over a delivery before
final classification (schema problems, invalid identifiers, conditional
blanks, missing context), keep originals untouched and record derived
normalizations SEPARATELY, and keep applicability / execution / evidence /
disposition as four separate dimensions; and pilot a SHADOW reassessment
that re-classifies persisted evidence under a pinned candidate rule set,
records every delta with its direction, binds analyst and independent-QA
approvals to the exact package hash, and designs successor publication
(predecessor links, events, stale-baseline check, idempotent retry) while
keeping official application outside authorization.

WHAT THIS DOES NOT CHANGE
--------------------------
No existing table, column, constraint, row or grant is altered. The OFFICIAL
findings (`rce_issues`, `review_records`, `tefca_dimension_evidence`) are
never written by the preflight or the shadow comparison; the only writer of a
successor `review_records` row is the publication step, and that step is
refused unless SHADOW_PUBLICATION_MODE=local_test (see
app/tefca_registry/rce/shadow_reassessment.py).

APPEND-ONLY BY GRANT, SAME AS THE SEPTEMBER MIGRATION
-------------------------------------------------------
Runtime role: SELECT + INSERT only. No UPDATE, no DELETE, no TRUNCATE.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261003_preflight_shadow"
down_revision = "20260930_alembic_version_read"
branch_labels = None
depends_on = None

TABLES = (
    "rce_preflight_run", "rce_preflight_finding", "rce_preflight_normalization",
    "rce_shadow_comparison", "rce_shadow_finding_delta", "rce_shadow_approval",
    "rce_successor_publication_event",
)


class PreflightShadowPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return False if _offline() else name in _inspector().get_table_names()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise PreflightShadowPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges to a "
            "NAMED application role on the tables it creates (same discipline as "
            "20260921_september_snapshot) and refuses to guess which role that "
            "is. Set DB_APP_ROLE=docuaction_app.")
    return role


def _uuid():
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    role = _app_role()

    # ── rce_preflight_run ────────────────────────────────────────────────────
    if not _table_exists("rce_preflight_run"):
        op.create_table(
            "rce_preflight_run",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("source_intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("field_map_version", sa.String(16), nullable=False),
            sa.Column("rule_set_version", sa.String(16), nullable=False),
            sa.Column("preflight_version", sa.String(16), nullable=False),
            sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'RUNNING'")),
            sa.Column("classification_gate", sa.String(24)),
            sa.Column("records_evaluated", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("findings_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("normalizations_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("error", sa.Text()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
            sa.CheckConstraint("status IN ('RUNNING','COMPLETE','FAILED')",
                               name="ck_rce_preflight_run_status"),
        )
        op.create_index("idx_rce_preflight_run_intake", "rce_preflight_run",
                        ["source_intake_id", "started_at"])

    # ── rce_preflight_finding ────────────────────────────────────────────────
    if not _table_exists("rce_preflight_finding"):
        op.create_table(
            "rce_preflight_finding",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("run_id", _uuid(),
                      sa.ForeignKey("rce_preflight_run.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("source_record_id", _uuid(),
                      sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT")),
            sa.Column("line_number", sa.Integer()),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(24), nullable=False),
            sa.Column("code", sa.String(32), nullable=False),
            sa.Column("rule_ref", sa.String(20)),
            sa.Column("field_name", sa.String(100)),
            sa.Column("applicability", sa.String(16), nullable=False),
            sa.Column("execution", sa.String(16), nullable=False),
            sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("disposition", sa.String(16), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("original_value", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("run_id", "sequence", name="uq_rce_preflight_finding_seq"),
            sa.CheckConstraint(
                "category IN ('SCHEMA','IDENTIFIER','CONDITIONAL_BLANK','MISSING_CONTEXT')",
                name="ck_rce_preflight_finding_category"),
            sa.CheckConstraint("applicability IN ('applies','does_not_apply','unresolved')",
                               name="ck_rce_preflight_finding_applicability"),
            sa.CheckConstraint("execution IN ('done','unavailable','insufficient')",
                               name="ck_rce_preflight_finding_execution"),
            sa.CheckConstraint("disposition IN ('open','informational','blocked')",
                               name="ck_rce_preflight_finding_disposition"),
        )
        op.create_index("idx_rce_preflight_finding_run", "rce_preflight_finding", ["run_id"])
        op.create_index("idx_rce_preflight_finding_record", "rce_preflight_finding",
                        ["source_record_id"])
        op.create_index("idx_rce_preflight_finding_cat", "rce_preflight_finding",
                        ["run_id", "category", "disposition"])

    # ── rce_preflight_normalization ──────────────────────────────────────────
    if not _table_exists("rce_preflight_normalization"):
        op.create_table(
            "rce_preflight_normalization",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("run_id", _uuid(),
                      sa.ForeignKey("rce_preflight_run.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("source_record_id", _uuid(),
                      sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("line_number", sa.Integer(), nullable=False),
            sa.Column("field_name", sa.String(100), nullable=False),
            sa.Column("original_value", sa.Text()),
            sa.Column("derived_value", sa.Text()),
            sa.Column("method", sa.String(40), nullable=False),
            sa.Column("rule_ref", sa.String(20)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_rce_preflight_norm_run", "rce_preflight_normalization", ["run_id"])
        op.create_index("idx_rce_preflight_norm_record", "rce_preflight_normalization",
                        ["source_record_id"])

    # ── rce_shadow_comparison ────────────────────────────────────────────────
    if not _table_exists("rce_shadow_comparison"):
        op.create_table(
            "rce_shadow_comparison",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("baseline_rule_version", sa.Integer(), nullable=False),
            sa.Column("candidate_rule_version", sa.Integer()),
            sa.Column("candidate_rules", postgresql.JSONB()),
            sa.Column("candidate_rules_hash", sa.String(64), nullable=False),
            sa.Column("evaluation_date", sa.Date(), nullable=False),
            sa.Column("evidence_pinned", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("official_baseline_hash", sa.String(64), nullable=False),
            sa.Column("package_hash", sa.String(64), nullable=False),
            sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("built_by", sa.String(320), nullable=False),
            sa.Column("built_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
        )
        op.create_index("idx_rce_shadow_cmp_intake", "rce_shadow_comparison", ["intake_id", "built_at"])
        op.create_index("idx_rce_shadow_cmp_hash", "rce_shadow_comparison", ["package_hash"])

    # ── rce_shadow_finding_delta ─────────────────────────────────────────────
    if not _table_exists("rce_shadow_finding_delta"):
        op.create_table(
            "rce_shadow_finding_delta",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("comparison_id", _uuid(),
                      sa.ForeignKey("rce_shadow_comparison.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT")),
            sa.Column("predecessor_review_record_id", _uuid(),
                      sa.ForeignKey("review_records.id", ondelete="RESTRICT")),
            sa.Column("predecessor_review_id", sa.String(20)),
            sa.Column("delta_kind", sa.String(20), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False),
            sa.Column("baseline_bucket", sa.String(2)),
            sa.Column("baseline_rule", sa.String(20)),
            sa.Column("baseline_rule_version", sa.Integer()),
            sa.Column("candidate_bucket", sa.String(2)),
            sa.Column("candidate_rule", sa.String(20)),
            sa.Column("candidate_rule_version", sa.Integer()),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("manual_review_required", sa.Boolean(), nullable=False,
                      server_default=sa.text("false")),
            sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "delta_kind IN ('NEW','REMOVED','CHANGED','UNCHANGED','NOT_REPRODUCIBLE')",
                name="ck_rce_shadow_delta_kind"),
            sa.CheckConstraint("direction IN ('STRICTER','MORE_PERMISSIVE','NEUTRAL')",
                               name="ck_rce_shadow_delta_direction"),
        )
        op.create_index("idx_rce_shadow_delta_cmp", "rce_shadow_finding_delta",
                        ["comparison_id", "delta_kind"])

    # ── rce_shadow_approval ──────────────────────────────────────────────────
    if not _table_exists("rce_shadow_approval"):
        op.create_table(
            "rce_shadow_approval",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("comparison_id", _uuid(),
                      sa.ForeignKey("rce_shadow_comparison.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("package_hash", sa.String(64), nullable=False),
            sa.Column("approval_role", sa.String(16), nullable=False),
            sa.Column("actor_id", _uuid()),
            sa.Column("actor_email", sa.String(320), nullable=False),
            sa.Column("actor_role", sa.String(64), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.UniqueConstraint("comparison_id", "package_hash", "approval_role",
                                name="uq_rce_shadow_approval_role"),
            sa.CheckConstraint("approval_role IN ('ANALYST','INDEPENDENT_QA')",
                               name="ck_rce_shadow_approval_role"),
        )
        op.create_index("idx_rce_shadow_approval_cmp", "rce_shadow_approval", ["comparison_id"])

    # ── rce_successor_publication_event ──────────────────────────────────────
    if not _table_exists("rce_successor_publication_event"):
        op.create_table(
            "rce_successor_publication_event",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("comparison_id", _uuid(),
                      sa.ForeignKey("rce_shadow_comparison.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("package_hash", sa.String(64), nullable=False),
            sa.Column("event_type", sa.String(20), nullable=False),
            sa.Column("mode", sa.String(24), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("predecessor_review_ids", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'[]'::jsonb")),
            sa.Column("successor_review_ids", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'[]'::jsonb")),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.CheckConstraint("event_type IN ('REFUSED','PUBLISHED','ALREADY_PUBLISHED')",
                               name="ck_rce_successor_pub_event_type"),
        )
        op.create_index("idx_rce_successor_pub_cmp", "rce_successor_publication_event",
                        ["comparison_id"])
        op.create_index("idx_rce_successor_pub_hash", "rce_successor_publication_event",
                        ["package_hash", "event_type"])

    # ── grants: append-only ──────────────────────────────────────────────────
    for table in TABLES:
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO "{role}"')
    # Deliberately NOT granted: UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER.


def downgrade() -> None:
    """Refuses if any evidence row exists. Evidence is not un-recorded."""
    if not _offline():
        bind = op.get_bind()
        for table in TABLES:
            if _table_exists(table):
                n = bind.execute(sa.text(f'select count(*) from "{table}"')).scalar()
                if n:
                    raise PreflightShadowPreconditionError(
                        f"{table} holds {n} evidence rows; downgrade refused. "
                        f"Evidence is not un-recorded.")
    for table in reversed(TABLES):
        if _table_exists(table):
            op.drop_table(table)
