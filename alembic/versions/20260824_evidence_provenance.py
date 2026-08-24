"""evidence provenance — source versions, observation provenance, PPEF lineage

Revision ID: 20260824_evidence_prov
Revises:      20260823_vocab_version
Create Date:  2026-08-24

WHAT THIS ADDS
──────────────
  source_version_snapshots     NEW  which edition of a source answered
  evidence_relationship_path   NEW  the traversal that produced one observation
  tefca_dimension_evidence     11 ADDITIVE NULLABLE columns

NO BACKFILL, AND IT IS NOT AN OVERSIGHT
The 1,984 existing evidence rows keep NULL in every new column. The NPPES and
OIG LEIE editions consulted on 2026-08-21 were never recorded and cannot be
recovered; writing a synthesised version would assert a reproducibility that
does not exist. A NULL says "we do not know". A manufactured value says "we do",
and that claim cannot be withdrawn later.

Every added column is nullable with NO server_default, so PostgreSQL performs a
catalogue-only change on `tefca_dimension_evidence` — no table rewrite, and the
digest of the existing rows is unchanged by construction.

REVERSIBLE
`downgrade()` drops both tables and all eleven columns. Nothing outside the
provenance code reads them.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260824_evidence_prov"
down_revision = "20260823_vocab_version"
branch_labels = None
depends_on = None

EVIDENCE = "tefca_dimension_evidence"

NEW_COLUMNS = [
    ("source_version_id", postgresql.UUID(as_uuid=True)),
    ("observation_result", sa.String(24)),
    ("identifier_searched", sa.String(200)),
    ("identifier_type", sa.String(24)),
    ("observation_hash", sa.String(64)),
    ("raw_observation_ref", sa.Text()),
    ("match_method", sa.String(20)),
    ("match_level", sa.Integer()),
    ("match_version", sa.String(20)),
    ("rule_version", sa.String(20)),
    ("correlation_id", postgresql.UUID(as_uuid=True)),
]



# ── offline (--sql) tolerance ───────────────────────────────────────────────
# `alembic upgrade --sql` binds a MockConnection, which sa.inspect() cannot
# read. The guards below would raise NoInspectionAvailable and no reviewable
# script could be produced. Offline they are handed an inspector that reports an
# empty database, so upgrade() emits its full DDL — drift-unaware by
# construction, which is what an offline script is. downgrade() renders as a
# no-op offline for the same reason, and is not offered as a review artefact.


class _OfflineInspector:
    """Reports an empty schema so every create guard opens."""

    @staticmethod
    def get_table_names():
        return []

    @staticmethod
    def get_columns(table):
        return []

    @staticmethod
    def get_indexes(table):
        return []

    @staticmethod
    def get_unique_constraints(table):
        return []

    @staticmethod
    def get_foreign_keys(table):
        return []


def _inspect(bind):
    return _OfflineInspector() if op.get_context().as_sql else sa.inspect(bind)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspect(bind)
    tables = set(inspector.get_table_names())

    if "source_version_snapshots" not in tables:
        op.create_table(
            "source_version_snapshots",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("source", sa.String(40), nullable=False),
            # The SOURCE's own edition label, or 'UNKNOWN'. Never an API version.
            sa.Column("version_label", sa.String(120), nullable=False),
            sa.Column("source_as_of", sa.String(32)),
            sa.Column("source_file_hash", sa.String(64)),
            sa.Column("dataset_identifier", sa.String(120)),
            # Separate from version_label so one can never stand in for the other.
            sa.Column("api_version", sa.String(32)),
            sa.Column("http_last_modified", sa.String(64)),
            sa.Column("record_count", sa.Integer()),
            sa.Column("retrieved_at", sa.String(64), nullable=False),
            sa.Column("retrieval_method", sa.String(20), nullable=False),
            sa.Column("storage_uri", sa.Text()),
            # False means: this observation cannot be reproduced from the source.
            sa.Column("is_point_in_time", sa.Boolean(), nullable=False,
                      server_default=sa.text("false")),
            sa.Column("note", sa.Text()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("idx_source_version_source_retrieved",
                        "source_version_snapshots", ["source", "retrieved_at"])
        op.create_index("idx_source_version_pit",
                        "source_version_snapshots", ["source", "is_point_in_time"])
        op.create_index("ix_source_version_hash",
                        "source_version_snapshots", ["source_file_hash"])

    existing = {c["name"] for c in inspector.get_columns(EVIDENCE)}
    for name, coltype in NEW_COLUMNS:
        if name not in existing:
            # Nullable, no server_default — catalogue-only, no row rewrite.
            op.add_column(EVIDENCE, sa.Column(name, coltype, nullable=True))

    indexes = {i["name"] for i in inspector.get_indexes(EVIDENCE)}
    for idx, cols in (
        ("idx_dim_evidence_observation_result", ["observation_result"]),
        ("idx_dim_evidence_observation_hash", ["observation_hash"]),
        ("idx_dim_evidence_correlation", ["correlation_id"]),
        ("idx_dim_evidence_source_version", ["source_version_id"]),
    ):
        if idx not in indexes:
            op.create_index(idx, EVIDENCE, cols)

    if "evidence_relationship_path" not in tables:
        op.create_table(
            "evidence_relationship_path",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("hop_sequence", sa.Integer(), nullable=False),
            sa.Column("from_identifier_type", sa.String(30), nullable=False),
            sa.Column("from_identifier_value", sa.String(120), nullable=False),
            sa.Column("relationship_type", sa.String(40), nullable=False),
            sa.Column("to_identifier_type", sa.String(30)),
            sa.Column("to_identifier_value", sa.String(200)),
            sa.Column("ppef_component", sa.String(40)),
            sa.Column("source_row_key", sa.String(160)),
            sa.Column("source_version_id", postgresql.UUID(as_uuid=True)),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["evidence_id"], [f"{EVIDENCE}.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_version_id"],
                                    ["source_version_snapshots.id"]),
            sa.UniqueConstraint("evidence_id", "hop_sequence", name="uq_evidence_hop"),
        )
        op.create_index("idx_evidence_hop_from", "evidence_relationship_path",
                        ["from_identifier_type", "from_identifier_value"])
        op.create_index("idx_evidence_hop_to", "evidence_relationship_path",
                        ["to_identifier_type", "to_identifier_value"])
        op.create_index("idx_evidence_hop_rel", "evidence_relationship_path",
                        ["relationship_type"])
        op.create_index("ix_evidence_hop_evidence", "evidence_relationship_path",
                        ["evidence_id"])

    # FK from the evidence row to the version row, added after both exist.
    fks = {fk.get("name") for fk in inspector.get_foreign_keys(EVIDENCE)}
    if "fk_dim_evidence_source_version" not in fks:
        op.create_foreign_key("fk_dim_evidence_source_version", EVIDENCE,
                              "source_version_snapshots",
                              ["source_version_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = _inspect(bind)

    fks = {fk.get("name") for fk in inspector.get_foreign_keys(EVIDENCE)}
    if "fk_dim_evidence_source_version" in fks:
        op.drop_constraint("fk_dim_evidence_source_version", EVIDENCE,
                           type_="foreignkey")

    if "evidence_relationship_path" in set(inspector.get_table_names()):
        op.drop_table("evidence_relationship_path")

    indexes = {i["name"] for i in inspector.get_indexes(EVIDENCE)}
    for idx in ("idx_dim_evidence_observation_result",
                "idx_dim_evidence_observation_hash",
                "idx_dim_evidence_correlation",
                "idx_dim_evidence_source_version"):
        if idx in indexes:
            op.drop_index(idx, table_name=EVIDENCE)

    existing = {c["name"] for c in inspector.get_columns(EVIDENCE)}
    for name, _ in NEW_COLUMNS:
        if name in existing:
            op.drop_column(EVIDENCE, name)

    if "source_version_snapshots" in set(inspector.get_table_names()):
        op.drop_table("source_version_snapshots")
