"""Delivery traceability: stage events, dispositions, snapshots, identifier
decisions, report links.

Revision ID: 20260917_delivery_traceability
Revises: 20260915_curated_text_columns
Create Date: 2026-09-17

WHY THIS EXISTS
---------------
The 2026-09-16 read-only diagnostic of the official ONC/RCE delivery workflow
found that a delivery reaching PROCESSED / READY_FOR_REVIEW could not answer:
what happened to each received row (created / updated / unchanged / held /
rejected / missing key / excluded), when each stage started and finished, what
the reconciliation result was at the time, whether a submitted identifier
conflicted with a registered one, and which report was produced from which
evidence. Those facts were either computed in memory and discarded, folded
into one JSON blob that was overwritten on every heartbeat, or never
recorded.

WHAT THIS CREATES - AND WHAT IT DOES NOT TOUCH
----------------------------------------------
Five NEW tables and one view. No existing table, column, constraint or row is
altered. This is deliberate: on Azure DEV the Area 2 and registry tables are
owned by the runtime role (startup create_all made them), so any ALTER would
need an operator ownership window; a table created by the migration role is
owned correctly from the start and needs only the grants below.

    rce_delivery_stage_events         one row per stage ATTEMPT
    rce_disposition_events            append-only terminal accounting per row
    rce_reconciliation_snapshots      one row per reconciliation run
    tefca_identifier_decision_events  append-only identifier conflict/decision
    rce_delivery_report_links         job <-> intake <-> snapshot <-> report
    rce_current_dispositions (view)   highest-sequence disposition per row

APPEND-ONLY BY GRANT
--------------------
The runtime role receives SELECT and INSERT on every table, UPDATE only on
rce_delivery_stage_events (an attempt row is opened STARTED and closed
COMPLETED/FAILED - two writes to one attempt, never a rewrite of history), and
DELETE on none. Disposition and decision history therefore cannot be edited
from the application at all: the current state is derived by the view.

FOREIGN KEYS
------------
Every link to a delivery job, intake, source record, curated record, entity,
issue, entity version or audit_logs row is a declared FOREIGN KEY with
ON DELETE RESTRICT, so evidence cannot be orphaned by deleting its subject.
`report_artifacts.id` is referenced by value only: that table is created by
this chain (20260829) but its model lives in app.reports, outside the TEFCA
metadata scope, and the chain's boundary check refuses a declared FK to a
table it cannot see. Reconciliation asserts "zero orphan report links".

REALISTIC RISKS
---------------
- CREATE TABLE ... REFERENCES takes a SHARE ROW EXCLUSIVE lock on each
  referenced table for the duration of the statement. Brief, but it waits on
  any long-running write to rce_source_records or tefca_reg_entities.
- The migration role must hold REFERENCES on every referenced table. On DEV
  several of those are owned by the runtime role. upgrade() checks this first
  and raises a named error listing the exact GRANT REFERENCES statements an
  operator must run; it does not fail halfway through.
- A partial apply (tables created, grants not yet applied) is impossible
  within one transaction; Alembic runs this revision transactionally on
  PostgreSQL.

DOWNGRADE
---------
Refuses when ANY of the five tables holds a row. Dropping evidence is not a
rollback; the tables are additive and harmless to an older image, so the safe
rollback is to redeploy the older image and leave the tables in place.

NO GOVERNMENT DATA IS TOUCHED. No INSERT, UPDATE or DELETE is executed.
"""
import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260917_delivery_traceability"
down_revision = "20260915_curated_text_columns"
branch_labels = None
depends_on = None

OWNER = "docuaction_owner"

TABLES = (
    "rce_delivery_stage_events",
    "rce_disposition_events",
    "rce_reconciliation_snapshots",
    "tefca_identifier_decision_events",
    "rce_delivery_report_links",
)
VIEW = "rce_current_dispositions"

#: Tables the new foreign keys point at. The migration role needs REFERENCES on
#: each; on DEV some are owned by the runtime role.
REFERENCED = (
    "rce_delivery_jobs", "rce_source_intakes", "rce_source_records",
    "rce_curated_records", "tefca_reg_entities", "rce_issues",
    "tefca_entity_versions", "audit_logs",
)

STAGES = ("REGISTERED", "RECEIPT_PRESERVED", "SHA256", "SCHEMA_VALIDATION",
          "PARSING", "QUALITY", "CURATION", "MATCHING", "PROMOTION",
          "RELATIONSHIPS", "VERIFICATION_READINESS", "RECONCILIATION",
          "READY_FOR_REVIEW", "REPORT_GENERATION")
STAGE_STATUSES = ("STARTED", "COMPLETED", "FAILED", "SKIPPED")
DISPOSITIONS = ("CREATED", "UPDATED", "MATCHED_UNCHANGED", "HELD", "REJECTED",
                "MISSING_KEY", "EXCLUDED")
DECISIONS = ("CONFLICT_RAISED", "CONFIRM_EXISTING", "CONFIRM_SUBMITTED",
             "CORRECTED", "REQUEST_EVIDENCE", "DEFERRED", "ESCALATED", "REJECTED")
TRIGGERS = ("PIPELINE", "DISPOSITION", "MANUAL", "RECONSTRUCTION")
ACTORS = ("SYSTEM", "HUMAN")


class TraceabilityPreconditionError(RuntimeError):
    """Raised instead of guessing or half-applying."""


class DowngradeWouldDestroyEvidenceError(RuntimeError):
    """Raised instead of dropping delivery evidence."""


def _offline() -> bool:
    return op.get_context().as_sql


def _in(values) -> str:
    return ", ".join("'%s'" % v for v in values)


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    if _offline():
        return False
    return name in _inspector().get_table_names()


def _view_exists(name: str) -> bool:
    if _offline():
        return False
    return name in _inspector().get_view_names()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise TraceabilityPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges "
            "to a NAMED role and will not infer one from current_user. Re-run "
            "with DB_APP_ROLE=docuaction_app.")
    return role


def _check_references_privilege() -> None:
    """Fail before any DDL if the connected role cannot declare the FKs."""
    if _offline():
        return
    bind = op.get_bind()
    missing = []
    for table in REFERENCED:
        exists = bind.execute(sa.text(
            "select 1 from pg_tables where schemaname='public' and tablename=:t"),
            {"t": table}).scalar()
        if not exists:
            # A referenced table that does not exist yet is a chain-order error,
            # reported plainly rather than as an FK failure mid-statement.
            missing.append(f"{table} (table does not exist)")
            continue
        ok = bind.execute(sa.text(
            "select has_table_privilege(current_user, :t, 'REFERENCES')"),
            {"t": table}).scalar()
        if not ok:
            missing.append(table)
    if missing:
        grants = "\n".join(
            f'  GRANT REFERENCES ON "{t}" TO "{OWNER}";'
            for t in missing if "(" not in t)
        raise TraceabilityPreconditionError(
            "The migration role lacks REFERENCES on tables the new foreign keys "
            f"point at: {missing}. Nothing was changed. As a recorded operator "
            "step, run (as the owner of each table):\n" + grants +
            "\nthen re-run this revision.")


def _uuid():
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    role = _app_role()
    _check_references_privilege()

    # ── rce_delivery_stage_events ────────────────────────────────────────────
    if not _table_exists("rce_delivery_stage_events"):
        op.create_table(
            "rce_delivery_stage_events",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("job_id", _uuid(),
                      sa.ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT")),
            sa.Column("stage", sa.String(32), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False,
                      server_default=sa.text("1")),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("input_count", sa.Integer()),
            sa.Column("output_count", sa.Integer()),
            sa.Column("warning_count", sa.Integer()),
            sa.Column("held_count", sa.Integer()),
            sa.Column("rejected_count", sa.Integer()),
            sa.Column("failure_class", sa.String(128)),
            sa.Column("failure_reason", sa.Text()),
            sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()),
                      nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("worker_id", sa.String(128)),
            sa.Column("build_sha", sa.String(40), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.UniqueConstraint("job_id", "stage", "attempt",
                                name="uq_rce_stage_event_attempt"),
            sa.CheckConstraint("stage IN (%s)" % _in(STAGES),
                               name="ck_rce_stage_event_stage"),
            sa.CheckConstraint("status IN (%s)" % _in(STAGE_STATUSES),
                               name="ck_rce_stage_event_status"),
            sa.CheckConstraint("attempt > 0", name="ck_rce_stage_event_attempt_pos"),
            sa.CheckConstraint("completed_at IS NULL OR completed_at >= started_at",
                               name="ck_rce_stage_event_times"),
            sa.CheckConstraint(
                "coalesce(input_count,0) >= 0 AND coalesce(output_count,0) >= 0 "
                "AND coalesce(warning_count,0) >= 0 AND coalesce(held_count,0) >= 0 "
                "AND coalesce(rejected_count,0) >= 0",
                name="ck_rce_stage_event_counts_nonneg"),
        )
        op.create_index("ix_rce_delivery_stage_events_job_id",
                        "rce_delivery_stage_events", ["job_id"])
        op.create_index("ix_rce_delivery_stage_events_intake_id",
                        "rce_delivery_stage_events", ["intake_id"])
        op.create_index("idx_rce_stage_event_job_started",
                        "rce_delivery_stage_events", ["job_id", "started_at"])

    # ── rce_disposition_events ───────────────────────────────────────────────
    if not _table_exists("rce_disposition_events"):
        op.create_table(
            "rce_disposition_events",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("source_record_id", _uuid(),
                      sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("curated_record_id", _uuid(),
                      sa.ForeignKey("rce_curated_records.id", ondelete="RESTRICT")),
            sa.Column("job_id", _uuid(),
                      sa.ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT")),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("disposition", sa.String(20), nullable=False),
            sa.Column("reason_code", sa.String(64), nullable=False),
            sa.Column("reason", sa.Text()),
            sa.Column("entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT")),
            sa.Column("changed_fields", postgresql.JSONB(astext_type=sa.Text()),
                      nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("actor_type", sa.String(8), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column("reconstructed", sa.Boolean(), nullable=False,
                      server_default=sa.text("false")),
            sa.Column("reconstruction", postgresql.JSONB(astext_type=sa.Text())),
            sa.UniqueConstraint("source_record_id", "sequence",
                                name="uq_rce_disposition_sequence"),
            sa.CheckConstraint("disposition IN (%s)" % _in(DISPOSITIONS),
                               name="ck_rce_disposition_value"),
            sa.CheckConstraint("actor_type IN (%s)" % _in(ACTORS),
                               name="ck_rce_disposition_actor_type"),
            sa.CheckConstraint("sequence > 0", name="ck_rce_disposition_seq_pos"),
        )
        op.create_index("ix_rce_disposition_events_intake_id",
                        "rce_disposition_events", ["intake_id"])
        op.create_index("ix_rce_disposition_events_job_id",
                        "rce_disposition_events", ["job_id"])
        op.create_index("idx_rce_disposition_intake_disp",
                        "rce_disposition_events", ["intake_id", "disposition"])
        op.create_index("idx_rce_disposition_record_seq",
                        "rce_disposition_events",
                        ["source_record_id", sa.text("sequence DESC")])

    # ── rce_reconciliation_snapshots ─────────────────────────────────────────
    if not _table_exists("rce_reconciliation_snapshots"):
        op.create_table(
            "rce_reconciliation_snapshots",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("job_id", _uuid(),
                      sa.ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("passed", sa.Boolean(), nullable=False),
            sa.Column("failure_reason", sa.Text()),
            sa.Column("received", sa.Integer(), nullable=False),
            sa.Column("created", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("updated", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("matched_unchanged", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("held", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("rejected", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("missing_key", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("excluded", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()),
                      nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()),
                      nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("source_evidence", postgresql.JSONB(astext_type=sa.Text()),
                      nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("trigger", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("hash", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column("migration_revision", sa.String(64), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("reconstructed", sa.Boolean(), nullable=False,
                      server_default=sa.text("false")),
            sa.UniqueConstraint("job_id", "sequence", name="uq_rce_snapshot_sequence"),
            sa.CheckConstraint("trigger IN (%s)" % _in(TRIGGERS),
                               name="ck_rce_snapshot_trigger"),
            sa.CheckConstraint("sequence > 0", name="ck_rce_snapshot_seq_pos"),
            sa.CheckConstraint(
                "received >= 0 AND created >= 0 AND updated >= 0 AND "
                "matched_unchanged >= 0 AND held >= 0 AND rejected >= 0 AND "
                "missing_key >= 0 AND excluded >= 0",
                name="ck_rce_snapshot_counts_nonneg"),
            sa.CheckConstraint(
                "passed = false OR received = created + updated + "
                "matched_unchanged + held + rejected + missing_key + excluded",
                name="ck_rce_snapshot_equation"),
        )
        op.create_index("ix_rce_reconciliation_snapshots_job_id",
                        "rce_reconciliation_snapshots", ["job_id"])
        op.create_index("idx_rce_snapshot_intake_created",
                        "rce_reconciliation_snapshots",
                        ["intake_id", sa.text("created_at DESC")])

    # ── tefca_identifier_decision_events ─────────────────────────────────────
    if not _table_exists("tefca_identifier_decision_events"):
        op.create_table(
            "tefca_identifier_decision_events",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("source_record_id", _uuid(),
                      sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT")),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT")),
            sa.Column("issue_id", _uuid(),
                      sa.ForeignKey("rce_issues.id", ondelete="RESTRICT")),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("identifier_type", sa.String(32), nullable=False),
            sa.Column("submitted_value", sa.Text()),
            sa.Column("existing_value", sa.Text()),
            sa.Column("verified_value", sa.Text()),
            sa.Column("selected_value", sa.Text()),
            sa.Column("decision", sa.String(32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("evidence_source", sa.String(64)),
            sa.Column("confidence", sa.String(10)),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("actor_type", sa.String(8), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("version_id", _uuid(),
                      sa.ForeignKey("tefca_entity_versions.id", ondelete="RESTRICT")),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.UniqueConstraint("entity_id", "identifier_type", "sequence",
                                name="uq_tefca_identifier_decision_sequence"),
            sa.CheckConstraint("decision IN (%s)" % _in(DECISIONS),
                               name="ck_tefca_identifier_decision_value"),
            sa.CheckConstraint("actor_type IN (%s)" % _in(ACTORS),
                               name="ck_tefca_identifier_decision_actor_type"),
            sa.CheckConstraint("sequence > 0",
                               name="ck_tefca_identifier_decision_seq_pos"),
        )
        op.create_index("ix_tefca_identifier_decision_events_entity_id",
                        "tefca_identifier_decision_events", ["entity_id"])
        op.create_index("ix_tefca_identifier_decision_events_source_record_id",
                        "tefca_identifier_decision_events", ["source_record_id"])
        op.create_index("ix_tefca_identifier_decision_events_intake_id",
                        "tefca_identifier_decision_events", ["intake_id"])
        op.create_index("ix_tefca_identifier_decision_events_issue_id",
                        "tefca_identifier_decision_events", ["issue_id"])

    # ── rce_delivery_report_links ────────────────────────────────────────────
    if not _table_exists("rce_delivery_report_links"):
        op.create_table(
            "rce_delivery_report_links",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("job_id", _uuid(),
                      sa.ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("snapshot_id", _uuid(),
                      sa.ForeignKey("rce_reconciliation_snapshots.id",
                                    ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("report_id", sa.String(64), nullable=False),
            sa.Column("report_type", sa.String(64), nullable=False),
            sa.Column("artifact_id", _uuid()),
            sa.Column("template_version", sa.String(32), nullable=False),
            sa.Column("generation_audit_id", _uuid(),
                      sa.ForeignKey("audit_logs.id", ondelete="RESTRICT")),
            sa.Column("generated_by", sa.String(320), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("build_sha", sa.String(40), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.UniqueConstraint("report_id", "artifact_id",
                                name="uq_rce_report_link_artifact"),
        )
        op.create_index("ix_rce_delivery_report_links_job_id",
                        "rce_delivery_report_links", ["job_id"])
        op.create_index("ix_rce_delivery_report_links_snapshot_id",
                        "rce_delivery_report_links", ["snapshot_id"])
        op.create_index("ix_rce_delivery_report_links_report_id",
                        "rce_delivery_report_links", ["report_id"])
        op.create_index("ix_rce_delivery_report_links_artifact_id",
                        "rce_delivery_report_links", ["artifact_id"])
        op.create_index("idx_rce_report_link_intake_generated",
                        "rce_delivery_report_links",
                        ["intake_id", sa.text("generated_at DESC")])

    # ── the derived "current disposition" view ───────────────────────────────
    op.execute(
        f"CREATE OR REPLACE VIEW {VIEW} AS "
        "SELECT DISTINCT ON (source_record_id) "
        "  id, intake_id, source_record_id, curated_record_id, job_id, sequence, "
        "  disposition, reason_code, reason, entity_id, changed_fields, actor, "
        "  actor_type, decided_at, correlation_id, build_sha, reconstructed "
        "FROM rce_disposition_events "
        "ORDER BY source_record_id, sequence DESC")

    # ── runtime grants: append-only ──────────────────────────────────────────
    for table in TABLES:
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO "{role}"')
    # An attempt row is opened STARTED and closed COMPLETED/FAILED.
    op.execute(f'GRANT UPDATE ON "rce_delivery_stage_events" TO "{role}"')
    op.execute(f'GRANT SELECT ON "{VIEW}" TO "{role}"')
    # Deliberately NOT granted anywhere: DELETE, TRUNCATE, REFERENCES, TRIGGER.


def downgrade() -> None:
    role = _app_role()
    if not _offline():
        bind = op.get_bind()
        populated = []
        for table in TABLES:
            if _table_exists(table):
                n = bind.execute(sa.text(f'select count(*) from "{table}"')).scalar()
                if n:
                    populated.append(f"{table} ({n} rows)")
        if populated:
            raise DowngradeWouldDestroyEvidenceError(
                "Downgrade refused: these tables hold delivery evidence that "
                f"reports and reconciliation depend on: {populated}. The tables "
                "are additive and harmless to an older application image; roll "
                "back by redeploying that image and leave the evidence in place.")
    if _view_exists(VIEW):
        op.execute(f"DROP VIEW {VIEW}")
    for table in reversed(TABLES):
        if _table_exists(table):
            op.execute(f'REVOKE ALL ON "{table}" FROM "{role}"')
            op.drop_table(table)
