"""ppef snapshots and records — versioned local ingestion of CMS PPEF sub-files

Adds two tables. Nothing existing is altered or backfilled.

CMS publishes the four PPEF relational sub-files as quarterly CSV downloads,
not as data-api datasets, and states that PPEF carries CURRENT enrolment
information rather than historical. So the quarter behind a determination
disappears from the source when the next one publishes — which is exactly why
the snapshot, its checksum and its record count are stored rather than fetched
on demand.

Revision ID: 20260819_ppef_snapshots
Revises: 20260819_dim_evidence
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_ppef_snapshots"
down_revision = "20260819_dim_evidence"
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
        "tefca_ppef_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("cms_title", sa.String(255)),
        sa.Column("file_name", sa.String(255)),
        sa.Column("resource_id", sa.String(64)),
        sa.Column("parent_dataset_id", sa.String(64)),
        sa.Column("download_url", sa.Text()),
        sa.Column("api_endpoint", sa.Text()),
        sa.Column("transport", sa.String(20)),
        sa.Column("resource_version", sa.String(32)),
        sa.Column("as_of_label", sa.String(64)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("schema_fields", postgresql.JSONB(), server_default="[]"),
        sa.Column("record_count", sa.Integer(), server_default="0"),
        sa.Column("rows_truncated", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("http_last_modified", sa.String(64)),
        sa.Column("retrieved_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("ingested_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("ingest_status", sa.String(20), server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("ingested_by", sa.String(255)),
    )
    _create_index("ix_tefca_ppef_snapshots_component", "tefca_ppef_snapshots", ["component"])
    _create_index("ix_tefca_ppef_snapshots_version", "tefca_ppef_snapshots", ["resource_version"])
    _create_index("ix_tefca_ppef_snapshots_sha256", "tefca_ppef_snapshots", ["sha256"])
    _create_index("idx_ppef_snapshot_component_version", "tefca_ppef_snapshots",
                    ["component", "resource_version"])

    _create_table(
        "tefca_ppef_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tefca_ppef_snapshots.id"), nullable=False),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("enrollment_id", sa.String(32)),
        sa.Column("related_enrollment_id", sa.String(32)),
        sa.Column("npi", sa.String(10)),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
    )
    _create_index("ix_tefca_ppef_records_snapshot", "tefca_ppef_records", ["snapshot_id"])
    _create_index("ix_tefca_ppef_records_component", "tefca_ppef_records", ["component"])
    _create_index("ix_tefca_ppef_records_enrollment", "tefca_ppef_records", ["enrollment_id"])
    _create_index("ix_tefca_ppef_records_related", "tefca_ppef_records", ["related_enrollment_id"])
    _create_index("ix_tefca_ppef_records_npi", "tefca_ppef_records", ["npi"])
    _create_index("idx_ppef_record_component_enrollment", "tefca_ppef_records",
                    ["component", "enrollment_id"])
    _create_index("idx_ppef_record_component_related", "tefca_ppef_records",
                    ["component", "related_enrollment_id"])
    _create_index("idx_ppef_record_snapshot_component", "tefca_ppef_records",
                    ["snapshot_id", "component"])


def downgrade() -> None:
    # Dropping these destroys the snapshots determinations were made against.
    # Present so the chain is reversible in development; not for an environment
    # whose determinations have been issued.
    for ix in ("idx_ppef_record_snapshot_component", "idx_ppef_record_component_related",
               "idx_ppef_record_component_enrollment", "ix_tefca_ppef_records_npi",
               "ix_tefca_ppef_records_related", "ix_tefca_ppef_records_enrollment",
               "ix_tefca_ppef_records_component", "ix_tefca_ppef_records_snapshot"):
        _drop_index(ix, table_name="tefca_ppef_records")
    _drop_table("tefca_ppef_records")
    for ix in ("idx_ppef_snapshot_component_version", "ix_tefca_ppef_snapshots_sha256",
               "ix_tefca_ppef_snapshots_version", "ix_tefca_ppef_snapshots_component"):
        _drop_index(ix, table_name="tefca_ppef_snapshots")
    _drop_table("tefca_ppef_snapshots")
