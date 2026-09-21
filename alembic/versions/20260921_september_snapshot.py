"""September 2026 ONC snapshot + Release-1 source-layer foundation.

Revision ID: 20260921_september_snapshot
Revises: 20260918_pp_verification
Create Date: 2026-09-21

WHY THIS EXISTS
---------------
The September 2, 2026 ONC snapshot is the FIRST second delivery against an
existing registry (July 20, 2026: 23,566 records). `docs/monthly_delivery_model.md`
recorded the gaps before it: no persisted delivery delta, no relationship
supersession when `partOf` changes (the pipeline added edges and never ended
the old one), no per-snapshot presence history for entities absent from a
later file, and no way to mark an ARC result stale without touching the
historical report that carried it.

WHAT THIS CREATES - AND WHAT IT DOES NOT TOUCH
----------------------------------------------
Seven NEW tables and one view. No existing table, column, constraint or row is
altered (on Azure DEV the registry and Area 2 tables are owned by the runtime
role; an ALTER would need an operator ownership window). Relationship
supersession uses the `end_date` / `status` columns that
`tefca_entity_relationships` already carries; the PROVENANCE of each
supersession (which snapshot, which boundary, which edge replaced which) is
append-only evidence in `tefca_relationship_observations`.

    rce_delivery_delta               persisted per-id delta between two deliveries
    rce_entity_presence              entity x intake: present/absent + active flag
    tefca_relationship_observations  versioned relationship observation per snapshot
    arc_stale_marks                  append-only "re-evaluation required" marks
    arc_current_stale (view)         latest unresolved mark per review/entity
    source_snapshot                  Release-1 layer 1: immutable source snapshot
                                     registry with an APPROVAL gate (current view
                                     = latest APPROVED snapshot per source)
    entity_source_match              Release-1 layer 4: versioned many-to-many
                                     crosswalk (method, evidence, confidence,
                                     model version, actor, validity interval)
    arc_assessment_run               Release-1 layer 5: snapshot-specific ARC run

The IQVIA HCO / HCP / affiliation OBSERVATION tables are deliberately NOT
created here: no licensed specification, sample or data-handling approval is
on file. Their proposal is docs/architecture/iqvia_release1_schema_proposal.md.

APPEND-ONLY BY GRANT
--------------------
Runtime role: SELECT + INSERT on every new table, UPDATE on none of them
(a superseded match/snapshot is a NEW row that names what it supersedes),
DELETE on none. UPDATE (end_date, status) on tefca_entity_relationships is
granted only when this migration's role owns that table; otherwise the exact
operator GRANT is printed and the migration continues (on DEV the runtime
role owns the table and already holds it).
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20260921_september_snapshot"
down_revision = "20260918_pp_verification"
branch_labels = None
depends_on = None

OWNER = "docuaction_owner"
TABLES = (
    "rce_delivery_delta", "rce_entity_presence", "tefca_relationship_observations",
    "arc_stale_marks", "source_snapshot", "entity_source_match", "arc_assessment_run",
)
VIEW = "arc_current_stale"
REFERENCED = ("rce_source_intakes", "rce_source_records", "tefca_reg_entities",
              "tefca_entity_relationships", "review_records", "rce_delivery_jobs")


class SnapshotPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return False if _offline() else name in _inspector().get_table_names()


def _view_exists(name: str) -> bool:
    return False if _offline() else name in _inspector().get_view_names()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise SnapshotPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges to a "
            "NAMED role and will not infer one from current_user. Re-run with "
            "DB_APP_ROLE=docuaction_app.")
    return role


def _check_references_privilege() -> None:
    if _offline():
        return
    bind = op.get_bind()
    missing = []
    for table in REFERENCED:
        exists = bind.execute(sa.text(
            "select 1 from pg_tables where schemaname='public' and tablename=:t"),
            {"t": table}).scalar()
        if not exists:
            missing.append(f"{table} (table does not exist)")
            continue
        ok = bind.execute(sa.text(
            "select has_table_privilege(current_user, :t, 'REFERENCES')"),
            {"t": table}).scalar()
        if not ok:
            missing.append(table)
    if missing:
        grants = "\n".join(f'  GRANT REFERENCES ON "{t}" TO "{OWNER}";'
                           for t in missing if "(" not in t)
        raise SnapshotPreconditionError(
            "The migration role lacks REFERENCES on tables the new foreign keys "
            f"point at: {missing}. Nothing was changed. Run (as each table's owner):\n"
            + grants + "\nthen re-run this revision.")


def _check_existing_ownership() -> None:
    if _offline():
        return
    bind = op.get_bind()
    current = bind.execute(sa.text("select current_user")).scalar()
    rows = bind.execute(
        sa.text("select tablename, tableowner from pg_tables "
                "where schemaname = current_schema() and tablename in :names")
        .bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(TABLES)}).all()
    wrong = [f"{n} (owner {o})" for n, o in rows if o != current]
    if wrong:
        raise SnapshotPreconditionError(
            "Refusing to continue: evidence objects exist that are not owned by the "
            f"migration role {current!r}: {', '.join(wrong)}. The grants this revision "
            "issues would be void.")


def _uuid():
    return postgresql.UUID(as_uuid=True)


def _grant_relationship_update(role: str) -> None:
    """UPDATE (end_date, status) on tefca_entity_relationships for the runtime
    role - only if this migration's role owns the table. Otherwise print the
    operator step; on Azure DEV the runtime role owns the table already."""
    if _offline():
        op.execute(f'GRANT UPDATE (end_date, status) ON "tefca_entity_relationships" TO "{role}"')
        return
    bind = op.get_bind()
    owner = bind.execute(sa.text(
        "select tableowner from pg_tables where schemaname=current_schema() "
        "and tablename='tefca_entity_relationships'")).scalar()
    current = bind.execute(sa.text("select current_user")).scalar()
    if owner == current:
        op.execute(f'GRANT UPDATE (end_date, status) ON "tefca_entity_relationships" TO "{role}"')
        return
    has = bind.execute(sa.text(
        "select has_column_privilege(:r, 'tefca_entity_relationships', 'end_date', 'UPDATE') "
        "and has_column_privilege(:r, 'tefca_entity_relationships', 'status', 'UPDATE')"),
        {"r": role}).scalar()
    if not has:
        print(f"OPERATOR STEP (not applied - table owned by {owner!r}): "
              f'GRANT UPDATE (end_date, status) ON "tefca_entity_relationships" TO "{role}";')


def upgrade() -> None:
    role = _app_role()
    _check_references_privilege()
    _check_existing_ownership()

    # ── rce_delivery_delta ───────────────────────────────────────────────────
    if not _table_exists("rce_delivery_delta"):
        op.create_table(
            "rce_delivery_delta",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("previous_intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("current_intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("rce_org_oid", sa.Text(), nullable=False),
            sa.Column("classification", sa.String(48), nullable=False),
            sa.Column("changed_fields", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'[]'::jsonb")),
            sa.Column("field_changes", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'[]'::jsonb")),
            sa.Column("material", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("previous_sha256", sa.String(64)),
            sa.Column("current_sha256", sa.String(64)),
            sa.Column("current_source_record_id", _uuid(),
                      sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT")),
            sa.Column("delta_version", sa.String(16), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
            sa.UniqueConstraint("previous_intake_id", "current_intake_id", "rce_org_oid",
                                name="uq_rce_delivery_delta_pair_oid"),
            sa.CheckConstraint("previous_intake_id <> current_intake_id",
                               name="ck_rce_delivery_delta_pair"),
        )
        op.create_index("idx_rce_delta_current", "rce_delivery_delta", ["current_intake_id"])
        op.create_index("idx_rce_delta_class", "rce_delivery_delta",
                        ["current_intake_id", "classification"])

    # ── rce_entity_presence ──────────────────────────────────────────────────
    if not _table_exists("rce_entity_presence"):
        op.create_table(
            "rce_entity_presence",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("rce_org_oid", sa.Text(), nullable=False),
            sa.Column("present", sa.Boolean(), nullable=False),
            sa.Column("active_raw", sa.String(16)),
            sa.Column("active_normalized", sa.String(1)),
            sa.Column("source_record_id", _uuid(),
                      sa.ForeignKey("rce_source_records.id", ondelete="RESTRICT")),
            sa.Column("snapshot_received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.UniqueConstraint("entity_id", "intake_id", name="uq_rce_presence_entity_intake"),
            sa.CheckConstraint("active_normalized IS NULL OR active_normalized IN ('0','1')",
                               name="ck_rce_presence_active"),
        )
        op.create_index("idx_rce_presence_intake", "rce_entity_presence", ["intake_id"])
        op.create_index("idx_rce_presence_entity", "rce_entity_presence", ["entity_id"])

    # ── tefca_relationship_observations ──────────────────────────────────────
    if not _table_exists("tefca_relationship_observations"):
        op.create_table(
            "tefca_relationship_observations",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("relationship_id", _uuid(),
                      sa.ForeignKey("tefca_entity_relationships.id", ondelete="RESTRICT")),
            sa.Column("child_entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("parent_entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT")),
            sa.Column("relationship_type", sa.String(50), nullable=False),
            sa.Column("observation", sa.String(24), nullable=False),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("effective_boundary", sa.Date(), nullable=False),
            sa.Column("supersedes_relationship_id", _uuid(),
                      sa.ForeignKey("tefca_entity_relationships.id", ondelete="RESTRICT")),
            sa.Column("delivered_parent_oid", sa.Text()),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
            sa.CheckConstraint(
                "observation IN ('ASSERTED','SUPERSEDED','UNRESOLVED_PARENT',"
                "'CROSS_QHIN_REFUSED','SNAPSHOT_MISMATCH','ABSENT',"
                "'ROLLED_BACK','RESTORED')",
                name="ck_tefca_relobs_observation"),
        )
        op.create_index("idx_tefca_relobs_child", "tefca_relationship_observations",
                        ["child_entity_id"])
        op.create_index("idx_tefca_relobs_intake", "tefca_relationship_observations",
                        ["intake_id"])

    # ── arc_stale_marks ──────────────────────────────────────────────────────
    if not _table_exists("arc_stale_marks"):
        op.create_table(
            "arc_stale_marks",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("review_id", sa.String(20)),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("reason", sa.String(48), nullable=False),
            sa.Column("changed_fields", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'[]'::jsonb")),
            sa.Column("detail", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("resolves_mark_id", _uuid(),
                      sa.ForeignKey("arc_stale_marks.id", ondelete="RESTRICT")),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
            sa.CheckConstraint("kind IN ('STALE','RESOLVED')", name="ck_arc_stale_kind"),
        )
        op.create_index("idx_arc_stale_entity", "arc_stale_marks", ["entity_id"])
        op.create_index("idx_arc_stale_review", "arc_stale_marks", ["review_id"])

    # ── source_snapshot (Release-1 layer 1) ──────────────────────────────────
    if not _table_exists("source_snapshot"):
        op.create_table(
            "source_snapshot",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("source_system", sa.String(32), nullable=False),
            sa.Column("snapshot_label", sa.String(200), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("intake_id", _uuid(),
                      sa.ForeignKey("rce_source_intakes.id", ondelete="RESTRICT")),
            # PENDING on registration (the system never approves); APPROVED only
            # by an authorised human as an append-only successor row; FAILED
            # when the snapshot effects did not complete; ROLLED_BACK after the
            # compensating relationship rollback.
            sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
            sa.Column("approved_by", sa.String(320)),
            sa.Column("approved_role", sa.String(64)),
            sa.Column("approved_at", sa.DateTime(timezone=True)),
            sa.Column("approval_ref", sa.String(120)),
            # The reconciliation artefact the approval rests on, and where it
            # was decided (build, request) - approval evidence, not metadata.
            sa.Column("reconciliation_snapshot_id", _uuid()),
            sa.Column("reconciliation_hash", sa.String(64)),
            sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
            sa.Column("request_id", sa.String(64)),
            sa.Column("supersedes_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT")),
            sa.Column("metadata", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_by", sa.String(320), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.CheckConstraint("source_system IN ('ONC_RCE','IQVIA_HCO','IQVIA_HCP',"
                               "'IQVIA_AFFILIATION')", name="ck_source_snapshot_system"),
            sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','SUPERSEDED',"
                               "'FAILED','ROLLED_BACK')", name="ck_source_snapshot_status"),
            sa.CheckConstraint("(status <> 'APPROVED') OR (approved_by IS NOT NULL AND "
                               "approved_at IS NOT NULL AND approved_role IS NOT NULL)",
                               name="ck_source_snapshot_approval"),
        )
        # The same file is registered ONCE. Approval/rejection rows are
        # append-only successors (supersedes_snapshot_id set) of the
        # registration and carry the same identity, so the uniqueness is
        # partial: it binds original registrations only.
        op.create_index("uq_source_snapshot_identity", "source_snapshot",
                        ["source_system", "sha256", "snapshot_label"], unique=True,
                        postgresql_where=sa.text("supersedes_snapshot_id IS NULL"))
        op.create_index("idx_source_snapshot_system_status", "source_snapshot",
                        ["source_system", "status"])

    # ── entity_source_match (Release-1 layer 4) ──────────────────────────────
    if not _table_exists("entity_source_match"):
        op.create_table(
            "entity_source_match",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("entity_id", _uuid(),
                      sa.ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("source_system", sa.String(32), nullable=False),
            sa.Column("source_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("source_record_key", sa.Text(), nullable=False),
            sa.Column("match_method", sa.String(32), nullable=False),
            sa.Column("match_status", sa.String(16), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4)),
            sa.Column("evidence", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("matching_model_version", sa.String(32), nullable=False),
            sa.Column("proposed_by", sa.String(320), nullable=False),
            sa.Column("reviewed_by", sa.String(320)),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("qa_by", sa.String(320)),
            sa.Column("qa_at", sa.DateTime(timezone=True)),
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("valid_to", sa.DateTime(timezone=True)),
            sa.Column("supersedes_match_id", _uuid(),
                      sa.ForeignKey("entity_source_match.id", ondelete="RESTRICT")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.CheckConstraint("match_method IN ('NPI_EXACT_TYPE2','CCN_CANDIDATE',"
                               "'EXACT_NAME_ADDRESS_PHONE','FUZZY_DISCOVERY','ANALYST')",
                               name="ck_esm_method"),
            sa.CheckConstraint("match_status IN ('AUTO_APPROVED','CANDIDATE','ANALYST_APPROVED',"
                               "'QA_APPROVED','REJECTED','EXCEPTION','SUPERSEDED')",
                               name="ck_esm_status"),
            # Only a unique valid compatible Type-2 exact NPI may be auto-approved.
            sa.CheckConstraint("(match_status <> 'AUTO_APPROVED') OR "
                               "(match_method = 'NPI_EXACT_TYPE2')", name="ck_esm_auto_only_npi"),
            # A QA approval requires a different person than the analyst.
            sa.CheckConstraint("(match_status <> 'QA_APPROVED') OR (qa_by IS NOT NULL AND "
                               "reviewed_by IS NOT NULL AND qa_by <> reviewed_by)",
                               name="ck_esm_maker_checker"),
            sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
                               name="ck_esm_confidence"),
        )
        op.create_index("idx_esm_entity", "entity_source_match", ["entity_id"])
        op.create_index("idx_esm_source_key", "entity_source_match",
                        ["source_system", "source_record_key"])

    # ── arc_assessment_run (Release-1 layer 5) ───────────────────────────────
    if not _table_exists("arc_assessment_run"):
        op.create_table(
            "arc_assessment_run",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tefca_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("iqvia_hco_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT")),
            sa.Column("iqvia_hcp_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT")),
            sa.Column("iqvia_affiliation_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT")),
            sa.Column("matching_model_version", sa.String(32), nullable=False),
            sa.Column("rule_set_version", sa.String(16), nullable=False),
            sa.Column("delivery_job_id", _uuid(),
                      sa.ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT")),
            sa.Column("review_cycle_id", _uuid()),
            sa.Column("report_id", sa.String(64)),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'STARTED'")),
            sa.Column("summary", postgresql.JSONB(), nullable=False,
                      server_default=sa.text("'{}'::jsonb")),
            sa.Column("actor", sa.String(320), nullable=False),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.Column("build_sha", sa.String(40), nullable=False, server_default=sa.text("'unknown'")),
            sa.CheckConstraint("status IN ('STARTED','COMPLETED','FAILED')",
                               name="ck_arc_run_status"),
        )
        op.create_index("idx_arc_run_tefca_snapshot", "arc_assessment_run", ["tefca_snapshot_id"])

    # ── view: latest unresolved stale mark per (entity, review) ──────────────
    op.execute(f"""
        CREATE OR REPLACE VIEW "{VIEW}" AS
        SELECT s.*
        FROM arc_stale_marks s
        WHERE s.kind = 'STALE'
          AND NOT EXISTS (
              SELECT 1 FROM arc_stale_marks r
              WHERE r.kind = 'RESOLVED' AND r.resolves_mark_id = s.id)
          -- current views show only marks of an APPROVED snapshot: a pending,
          -- failed or rolled-back delivery never reaches the current state.
          AND EXISTS (
              SELECT 1 FROM source_snapshot ss
              WHERE ss.intake_id = s.intake_id
                AND ss.status IN ('APPROVED', 'SUPERSEDED')
                AND NOT EXISTS (
                    SELECT 1 FROM source_snapshot n
                    WHERE n.supersedes_snapshot_id = ss.id))
    """)

    # ── grants: append-only ──────────────────────────────────────────────────
    for table in TABLES:
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO "{role}"')
    op.execute(f'GRANT SELECT ON "{VIEW}" TO "{role}"')
    _grant_relationship_update(role)
    # Deliberately NOT granted: UPDATE (on the new tables), DELETE, TRUNCATE,
    # REFERENCES, TRIGGER.


def downgrade() -> None:
    """Refuses if any evidence row exists. Evidence is not un-recorded."""
    if not _offline():
        bind = op.get_bind()
        for table in TABLES:
            if _table_exists(table):
                n = bind.execute(sa.text(f'select count(*) from "{table}"')).scalar()
                if n:
                    raise SnapshotPreconditionError(
                        f"{table} holds {n} row(s); downgrade refused.")
    op.execute(f'DROP VIEW IF EXISTS "{VIEW}"')
    for table in ("arc_assessment_run", "entity_source_match", "source_snapshot",
                  "arc_stale_marks", "tefca_relationship_observations",
                  "rce_entity_presence", "rce_delivery_delta"):
        op.execute(f'DROP TABLE IF EXISTS "{table}"')
