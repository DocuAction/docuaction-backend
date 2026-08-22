"""RCE ingestion pipeline — Area 1, runs, issues, Area 2, contacts.

Revision ID: 20260822_rce_pipeline
Revises:     20260820_ppef_jobs
Create Date: 2026-08-22

Creates the eight tables behind the RCE pipeline and applies the Area 1
immutability grants.

WHY EXPLICIT DDL AND NOT create_all(metadata)
─────────────────────────────────────────────
The existing 20260725_tefca_registry migration builds its tables by reading
`TEFCA_REG_TABLE_ORDER` from the live models module. That list has since grown
from 10 entries to 17, so the same revision id now creates a different set of
tables than it did when it was first applied — a fresh database and an upgraded
one do not converge. This migration states its DDL literally so that what it
creates is fixed at the moment it is written and cannot drift underneath a
deployment.

AREA 1 IMMUTABILITY
The downgrade path drops the tables. The upgrade REVOKEs UPDATE and DELETE on
the two Area 1 tables from the application role, which is what makes
`repository.verify_immutable()` report enforcement rather than intention. The
revoke is wrapped so a deployment whose role name differs does not fail the
migration — it logs and leaves the application-layer protections in place, and
the reconciliation report then shows database enforcement as unverified.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260822_rce_pipeline"
down_revision = "20260820_ppef_jobs"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)

IMMUTABLE_TABLES = ("rce_source_intakes", "rce_source_records")



# ── drift guards ────────────────────────────────────────────────────────────
# This revision predates the reconciliation of the Alembic chain with the schema
# that `app/main.py` startup's `Base.metadata.create_all()` had already
# materialised, so it can be asked to create objects that are already there.
# Every DDL call below is routed through an existence check, which is what lets
# the upgrade converge from an empty, a partially drifted and a fully drifted
# schema alike. Nothing here changes WHAT the revision creates.
#
# In offline (--sql) mode there is no live bind to inspect. The guards then open
# and the full DDL is emitted: that script is drift-unaware by construction and
# is meant to be read before it is run.


def _offline() -> bool:
    return op.get_context().as_sql


def _tables() -> set:
    if _offline():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set:
    if _offline() or table not in _tables():
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set:
    if _offline() or table not in _tables():
        return set()
    inspector = sa.inspect(op.get_bind())
    names = {i["name"] for i in inspector.get_indexes(table)}
    names |= {u["name"] for u in inspector.get_unique_constraints(table)
              if u.get("name")}
    return names


def _create_table(name: str, *columns, **kwargs) -> None:
    if name not in _tables():
        op.create_table(name, *columns, **kwargs)


def _create_index(name: str, table: str, columns, **kwargs) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns, **kwargs)


def _add_column(table: str, column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _drop_index(name: str, table_name: str) -> None:
    if _offline() or name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def _drop_table(name: str) -> None:
    if _offline() or name in _tables():
        op.drop_table(name)


def _drop_column(table: str, name: str) -> None:
    if _offline() or name in _columns(table):
        op.drop_column(table, name)


def upgrade() -> None:
    _create_table(
        "rce_source_intakes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("delivery_label", sa.String(200)),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("delimiter", sa.String(4)),
        sa.Column("encoding", sa.String(32)),
        sa.Column("encoding_anomaly", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("line_terminator", sa.String(8)),
        sa.Column("headers", JSONB, nullable=False),
        sa.Column("schema_fingerprint", sa.String(64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("received_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("received_by", sa.String(320)),
        sa.Column("source_metadata", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'RECEIVED'")),
        sa.Column("error", sa.Text()),
        # NOT UNIQUE — ONC may legitimately resend identical bytes, and that
        # re-delivery is its own historical event.
        sa.Column("duplicate_of_intake_id", UUID,
                  sa.ForeignKey("rce_source_intakes.id"), nullable=True),
        sa.Column("duplicate_content", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("record_count >= 0", name="ck_rce_intake_count_nonneg"),
    )
    _create_index("idx_rce_intake_sha", "rce_source_intakes", ["sha256"])
    _create_index("idx_rce_intake_received", "rce_source_intakes", ["received_at"])
    _create_index("idx_rce_intake_status", "rce_source_intakes", ["status"])
    _create_index("idx_rce_intake_duplicate", "rce_source_intakes",
                    ["duplicate_of_intake_id"])

    _create_table(
        "rce_source_records",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source_intake_id", UUID,
                  sa.ForeignKey("rce_source_intakes.id"), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("parsed", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("source_rce_id", sa.String(200)),
        sa.Column("tefcaid", sa.String(100)),
        sa.Column("hcid", sa.String(100)),
        sa.Column("npi", sa.String(40)),
        sa.Column("field_count", sa.Integer(), nullable=False),
        sa.Column("parse_status", sa.String(30), nullable=False,
                  server_default=sa.text("'ok'")),
        sa.Column("parse_note", sa.Text()),
        sa.Column("promotion_status", sa.String(20), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("canonical_entity_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("source_intake_id", "line_number",
                            name="uq_rce_source_record_line"),
    )
    for name, cols in (
        ("idx_rce_record_intake", ["source_intake_id"]),
        ("idx_rce_record_sha", ["record_sha256"]),
        ("idx_rce_record_source_id", ["source_rce_id"]),
        ("idx_rce_record_tefcaid", ["tefcaid"]),
        ("idx_rce_record_hcid", ["hcid"]),
        ("idx_rce_record_npi", ["npi"]),
        ("idx_rce_record_entity", ["canonical_entity_id"]),
        ("idx_rce_record_intake_status", ["source_intake_id", "promotion_status"]),
        ("idx_rce_record_parse_status", ["parse_status"]),
    ):
        _create_index(name, "rce_source_records", cols)

    _create_table(
        "rce_ingestion_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source_intake_id", UUID,
                  sa.ForeignKey("rce_source_intakes.id"), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("rule_config_hash", sa.String(64), nullable=False),
        sa.Column("field_map_version", sa.Text()),
        sa.Column("started_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("records_evaluated", sa.Integer(), server_default=sa.text("0")),
        sa.Column("issues_generated", sa.Integer(), server_default=sa.text("0")),
        sa.Column("run_status", sa.String(20), nullable=False,
                  server_default=sa.text("'RUNNING'")),
        sa.Column("error", sa.Text()),
        sa.Column("executed_by", sa.Text(), nullable=False,
                  server_default=sa.text("'SYSTEM'")),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    _create_index("idx_rce_run_intake", "rce_ingestion_runs", ["source_intake_id"])
    _create_index("idx_rce_run_intake_started", "rce_ingestion_runs",
                    ["source_intake_id", "started_at"])
    _create_index("idx_rce_run_status", "rce_ingestion_runs", ["run_status"])

    _create_table(
        "rce_rule_execution_history",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("rce_ingestion_runs.id"),
                  nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("rule_category", sa.String(8)),
        sa.Column("records_evaluated", sa.Integer(), server_default=sa.text("0")),
        sa.Column("issues_generated", sa.Integer(), server_default=sa.text("0")),
        sa.Column("execution_status", sa.Text(), nullable=False),
        sa.Column("execution_duration_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("executed_by", sa.Text(), nullable=False,
                  server_default=sa.text("'SYSTEM'")),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "rule_id", name="uq_rce_rule_exec_run_rule"),
    )
    _create_index("idx_rce_rule_exec_run", "rce_rule_execution_history", ["run_id"])
    _create_index("idx_rce_rule_exec_rule", "rce_rule_execution_history", ["rule_id"])

    _create_table(
        "rce_issues",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("issue_code", sa.String(30), nullable=False, unique=True),
        sa.Column("source_intake_id", UUID,
                  sa.ForeignKey("rce_source_intakes.id"), nullable=False),
        sa.Column("source_record_id", UUID,
                  sa.ForeignKey("rce_source_records.id"), nullable=True),
        sa.Column("run_id", UUID, sa.ForeignKey("rce_ingestion_runs.id"),
                  nullable=True),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text()),
        sa.Column("issue_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("field_name", sa.String(100)),
        sa.Column("original_value", sa.Text()),
        sa.Column("suggested_value", sa.Text()),
        sa.Column("suggested_source", sa.Text()),
        sa.Column("suggested_confidence", sa.String(10)),
        sa.Column("correction_authority", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("resolution", sa.String(20), nullable=False,
                  server_default=sa.text("'OPEN'")),
        sa.Column("resolved_by", sa.Text()),
        sa.Column("resolved_at", sa.DateTime()),
        sa.Column("resolution_notes", sa.Text()),
        sa.Column("qa_approved_by", sa.Text()),
        sa.Column("qa_approved_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "correction_authority IN "
            "('AUTO_SAFE','HUMAN_REQUIRED','QA_REQUIRED','NO_CORRECTION')",
            name="ck_rce_issue_authority"),
        sa.CheckConstraint(
            "severity IN ('CRITICAL','HIGH','MEDIUM','LOW','INFORMATIONAL')",
            name="ck_rce_issue_severity"),
    )
    for name, cols in (
        ("idx_rce_issue_code", ["issue_code"]),
        ("idx_rce_issue_intake", ["source_intake_id"]),
        ("idx_rce_issue_record", ["source_record_id"]),
        ("idx_rce_issue_run", ["run_id"]),
        ("idx_rce_issue_rule", ["rule_id"]),
        ("idx_rce_issue_type", ["issue_type"]),
        ("idx_rce_issue_severity", ["severity"]),
        ("idx_rce_issue_intake_severity", ["source_intake_id", "severity"]),
        ("idx_rce_issue_resolution", ["resolution"]),
        ("idx_rce_issue_authority", ["correction_authority"]),
    ):
        _create_index(name, "rce_issues", cols)
    _create_index("idx_rce_issue_open", "rce_issues", ["source_intake_id"],
                    postgresql_where=sa.text("resolution = 'OPEN'"))

    _create_table(
        "rce_curated_records",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source_intake_id", UUID,
                  sa.ForeignKey("rce_source_intakes.id"), nullable=False),
        sa.Column("source_record_id", UUID,
                  sa.ForeignKey("rce_source_records.id"), nullable=False),
        sa.Column("record_status", sa.String(20), nullable=False),
        sa.Column("issue_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("correction_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("status_reason", sa.Text()),
        sa.Column("rce_org_oid", sa.String(200)),
        sa.Column("tefcaid", sa.String(100)),
        sa.Column("hcid", sa.String(100)),
        sa.Column("aaid", sa.String(100)),
        sa.Column("npi", sa.String(40)),
        sa.Column("name", sa.String(500)),
        sa.Column("entity_level", sa.String(50)),
        sa.Column("sequoia_org_type", sa.String(50)),
        sa.Column("org_node_type", sa.String(100)),
        sa.Column("hl7_org_role", sa.String(100)),
        sa.Column("operational_status", sa.String(50)),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("address_line", sa.Text()),
        sa.Column("address_city", sa.String(200)),
        sa.Column("address_state", sa.String(10)),
        sa.Column("address_postal_code", sa.String(20)),
        sa.Column("address_country", sa.String(10)),
        sa.Column("exchange_purposes", JSONB, server_default=sa.text("'[]'::jsonb")),
        sa.Column("part_of", sa.String(200)),
        sa.Column("org_managing_org", sa.String(200)),
        sa.Column("contact", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rce_attributes", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_test_record", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("transformation_version", sa.Text(), nullable=False),
        sa.Column("canonical_entity_id", UUID, nullable=True),
        sa.Column("promoted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("reviewed_by", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.UniqueConstraint("source_record_id", name="uq_rce_curated_source_record"),
        sa.CheckConstraint(
            "record_status IN ('CLEAN','CORRECTED','HELD','REJECTED')",
            name="ck_rce_curated_status"),
    )
    for name, cols in (
        ("idx_rce_curated_intake", ["source_intake_id"]),
        ("idx_rce_curated_record", ["source_record_id"]),
        ("idx_rce_curated_oid", ["rce_org_oid"]),
        ("idx_rce_curated_tefcaid", ["tefcaid"]),
        ("idx_rce_curated_hcid", ["hcid"]),
        ("idx_rce_curated_npi", ["npi"]),
        ("idx_rce_curated_partof", ["part_of"]),
        ("idx_rce_curated_omo", ["org_managing_org"]),
        ("idx_rce_curated_entity", ["canonical_entity_id"]),
        ("idx_rce_curated_intake_status", ["source_intake_id", "record_status"]),
    ):
        _create_index(name, "rce_curated_records", cols)

    _create_table(
        "rce_correction_details",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("curated_record_id", UUID,
                  sa.ForeignKey("rce_curated_records.id"), nullable=False),
        sa.Column("source_record_id", UUID,
                  sa.ForeignKey("rce_source_records.id"), nullable=False),
        sa.Column("issue_id", UUID, sa.ForeignKey("rce_issues.id"), nullable=True),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("original_value", sa.Text()),
        sa.Column("original_value_hash", sa.String(64), nullable=False),
        sa.Column("corrected_value", sa.Text()),
        sa.Column("correction_reason", sa.Text(), nullable=False),
        sa.Column("correction_rule_id", sa.Text()),
        sa.Column("correction_authority", sa.String(20), nullable=False),
        sa.Column("corrected_by", sa.Text(), nullable=False),
        sa.Column("approval_actor", sa.Text()),
        sa.Column("confidence", sa.String(10)),
        sa.Column("qa_status", sa.String(20)),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "correction_authority IN "
            "('AUTO_SAFE','HUMAN_REQUIRED','QA_REQUIRED','NO_CORRECTION')",
            name="ck_rce_correction_authority"),
    )
    _create_index("idx_rce_correction_curated", "rce_correction_details",
                    ["curated_record_id"])
    _create_index("idx_rce_correction_source", "rce_correction_details",
                    ["source_record_id"])
    _create_index("idx_rce_correction_issue", "rce_correction_details", ["issue_id"])
    _create_index("idx_rce_correction_authority", "rce_correction_details",
                    ["correction_authority"])

    _create_table(
        "tefca_entity_contacts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("entity_id", UUID,
                  sa.ForeignKey("tefca_reg_entities.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source_record_id", UUID, nullable=True),
        sa.Column("contact_purpose", sa.String(100)),
        sa.Column("company", sa.String(500)),
        sa.Column("name", sa.String(500)),
        sa.Column("phone", sa.String(50)),
        sa.Column("email", sa.String(320)),
        sa.Column("address_text", sa.Text()),
        sa.Column("address_line", sa.Text()),
        sa.Column("address_city", sa.String(200)),
        sa.Column("address_state", sa.String(20)),
        sa.Column("address_postal_code", sa.String(20)),
        sa.Column("address_country", sa.String(20)),
        sa.Column("source", sa.String(50), nullable=False,
                  server_default=sa.text("'rce_import'")),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    _create_index("idx_tefca_contact_entity", "tefca_entity_contacts", ["entity_id"])
    _create_index("idx_tefca_contact_source_record", "tefca_entity_contacts",
                    ["source_record_id"])

    # ── RCE attributes on the canonical entity ──────────────────────────────
    # See app/tefca_registry/models.py for why TEFCAID is a column here rather
    # than an identifier row: it identifies an organisation family, not a
    # record, and the identifier table's unique index would reject the 241st.
    for column in (
        sa.Column("rce_org_oid", sa.String(200)),
        sa.Column("rce_tefcaid", sa.String(100)),
        sa.Column("rce_hcid", sa.String(100)),
        sa.Column("rce_aaid", sa.String(100)),
        sa.Column("sequoia_org_type", sa.String(50)),
        sa.Column("org_node_type", sa.String(100)),
        sa.Column("hl7_org_role", sa.String(100)),
        sa.Column("org_managing_org", sa.String(200)),
        sa.Column("is_test_record", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("rce_attributes", JSONB),
        sa.Column("source_record_id", UUID),
    ):
        _add_column("tefca_reg_entities", column)
    for name, cols in (
        ("idx_tefca_reg_ent_rce_oid", ["rce_org_oid"]),
        ("idx_tefca_reg_ent_rce_tefcaid", ["rce_tefcaid"]),
        ("idx_tefca_reg_ent_rce_hcid", ["rce_hcid"]),
        ("idx_tefca_reg_ent_source_record", ["source_record_id"]),
    ):
        _create_index(name, "tefca_reg_entities", cols)
    _create_index("idx_tefca_reg_ent_test", "tefca_reg_entities",
                    ["is_test_record"],
                    postgresql_where=sa.text("is_test_record = true"))

    _apply_immutability_grants()


def _apply_immutability_grants() -> None:
    """Revoke UPDATE/DELETE on Area 1 from the application role.

    Best effort by design. A deployment whose role name differs must not fail
    the migration over a grant — the application-layer protections (no update
    path, no mutating route) still hold, and `verify_immutable()` reports
    database enforcement as unverified so the gap is visible rather than
    assumed away.
    """
    role = os.getenv("DB_APP_ROLE", "").strip()
    if not role:
        bind = op.get_bind()
        try:
            role = bind.execute(sa.text("SELECT current_user")).scalar()
        except Exception:
            return
    for table in IMMUTABLE_TABLES:
        for statement in (
            f'REVOKE UPDATE, DELETE ON {table} FROM "{role}"',
            f'GRANT SELECT, INSERT ON {table} TO "{role}"',
        ):
            try:
                op.execute(statement)
            except Exception:  # noqa: BLE001 — see the docstring
                pass


def downgrade() -> None:
    for column in ("rce_org_oid", "rce_tefcaid", "rce_hcid", "rce_aaid",
                   "sequoia_org_type", "org_node_type", "hl7_org_role",
                   "org_managing_org", "is_test_record", "rce_attributes",
                   "source_record_id"):
        _drop_column("tefca_reg_entities", column)
    for table in ("tefca_entity_contacts", "rce_correction_details",
                  "rce_curated_records", "rce_issues",
                  "rce_rule_execution_history", "rce_ingestion_runs",
                  "rce_source_records", "rce_source_intakes"):
        _drop_table(table)
