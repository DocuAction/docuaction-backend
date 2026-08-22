"""tefca_dimension_evidence — dimension-organised evidence with CMS provenance

Adds ONE table. Nothing existing is altered, dropped or backfilled: the
five-element evidence record, the source cache and the audit trail all keep
their current shape and contents. This table sits alongside them.

Append-only by contract (see the model docstring): re-running a verification
inserts a new generation rather than updating rows, so a determination stays
explicable after CMS publishes a newer quarterly extract.

Revision ID: 20260819_dim_evidence
Revises: 20260817_audit_fields
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_dim_evidence"
down_revision = "20260817_audit_fields"
branch_labels = None
depends_on = None



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


def _drop_index(name: str, table_name: str) -> None:
    if _offline() or name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def _drop_table(name: str) -> None:
    if _offline() or name in _tables():
        op.drop_table(name)


def upgrade() -> None:
    _create_table(
        "tefca_dimension_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("review_id", sa.String(255)),
        sa.Column("review_cycle_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tefca_review_cycles.cycle_id")),
        sa.Column("evidence_dimension", sa.String(64), nullable=False),
        sa.Column("dimension_disposition", sa.String(32)),
        sa.Column("dimension_applicability", sa.String(32)),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_dataset", sa.String(128)),
        sa.Column("ppef_component", sa.String(64)),
        sa.Column("source_record_identifier", sa.Text()),
        sa.Column("query_identifier", sa.Text()),
        sa.Column("query_timestamp", sa.String(64)),
        sa.Column("dataset_version_anchor", sa.String(128)),
        sa.Column("http_last_modified", sa.String(64)),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("fields_evaluated", postgresql.JSONB(), server_default="[]"),
        sa.Column("field_matches", postgresql.JSONB(), server_default="[]"),
        sa.Column("field_conflicts", postgresql.JSONB(), server_default="[]"),
        sa.Column("original_values", postgresql.JSONB(), server_default="{}"),
        sa.Column("normalized_values", postgresql.JSONB(), server_default="{}"),
        sa.Column("rule_applied", sa.String(128)),
        sa.Column("note", sa.Text()),
        sa.Column("retrieved_at", sa.String(64)),
        sa.Column("generation_timestamp", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("analyst_notes", sa.Text()),
        sa.Column("reviewed_by", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime()),
    )
    _create_index("idx_dim_evidence_entity_dimension", "tefca_dimension_evidence",
                    ["entity_id", "evidence_dimension"])
    _create_index("idx_dim_evidence_generation", "tefca_dimension_evidence",
                    ["entity_id", "generation_timestamp"])
    _create_index("ix_tefca_dimension_evidence_entity_id", "tefca_dimension_evidence",
                    ["entity_id"])
    _create_index("ix_tefca_dimension_evidence_review_id", "tefca_dimension_evidence",
                    ["review_id"])
    _create_index("ix_tefca_dimension_evidence_source", "tefca_dimension_evidence",
                    ["source"])
    _create_index("ix_tefca_dimension_evidence_dimension", "tefca_dimension_evidence",
                    ["evidence_dimension"])


def downgrade() -> None:
    # Dropping this table destroys evidence that determinations cite. The
    # downgrade exists so the migration chain is complete and reversible in a
    # development database; it should not be run against an environment whose
    # determinations have been issued.
    _drop_index("ix_tefca_dimension_evidence_dimension", table_name="tefca_dimension_evidence")
    _drop_index("ix_tefca_dimension_evidence_source", table_name="tefca_dimension_evidence")
    _drop_index("ix_tefca_dimension_evidence_review_id", table_name="tefca_dimension_evidence")
    _drop_index("ix_tefca_dimension_evidence_entity_id", table_name="tefca_dimension_evidence")
    _drop_index("idx_dim_evidence_generation", table_name="tefca_dimension_evidence")
    _drop_index("idx_dim_evidence_entity_dimension", table_name="tefca_dimension_evidence")
    _drop_table("tefca_dimension_evidence")
