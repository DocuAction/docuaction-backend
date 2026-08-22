"""ppef ingestion jobs — durable job state for quarterly snapshot loads

Adds ONE table. Nothing existing is altered, dropped or backfilled, so this is
additive and rollback-safe: `downgrade()` drops only what `upgrade()` created.

WHY THE TABLE EXISTS
Ingestion previously ran in a FastAPI BackgroundTask, which lives and dies with
its worker. When the container recycled mid-load the task vanished and left the
snapshot at `pending` with `error = None` — five such rows accumulated on dev.
The failure was not that the work stopped; it was that nothing survived to say
so. Job state therefore lives in the database, where a dead process cannot take
it with it.

THE PARTIAL UNIQUE INDEX IS THE CONCURRENCY CONTROL
`uq_ppef_job_active_component` is unique over (component, resource_version)
WHERE active_marker IS TRUE. A finished job sets active_marker to NULL, and
NULLs do not collide, so history accumulates freely while at most one ACTIVE job
per component+quarter can exist. Refusing a double load with a constraint rather
than a check-then-insert closes the window between the two statements, and it
keeps holding if the deployment ever runs more than one worker.

Revision ID: 20260820_ppef_jobs
Revises: 20260819_ppef_snapshots
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260820_ppef_jobs"
down_revision = "20260819_ppef_snapshots"
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
        "tefca_ppef_ingest_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("resource_version", sa.String(32)),
        sa.Column("quarter", sa.String(32)),
        sa.Column("state", sa.String(20), nullable=False),
        # Nullable BOOLEAN, not a plain flag: only TRUE participates in the
        # partial unique index, and NULL is what releases the slot on finish.
        sa.Column("active_marker", sa.Boolean()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()),
        # The liveness signal. A dead process cannot report that it died, so
        # the reaper infers death from this column ceasing to advance.
        sa.Column("heartbeat_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("failed_at", sa.DateTime()),
        sa.Column("attempt_count", sa.Integer(), server_default="0"),
        sa.Column("error_reason", sa.Text()),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tefca_ppef_snapshots.id")),
        sa.Column("checksum", sa.String(64)),
        sa.Column("row_count", sa.Integer()),
        sa.Column("requested_by", sa.String(255)),
        sa.Column("max_rows", sa.Integer()),
    )
    # Index names and columns mirror TEFCAPPEFIngestJob exactly. They must: the
    # production schema is created by metadata create_all, so a migration that
    # invented its own names would leave the two environments differing in a way
    # nothing checks.
    _create_index("ix_tefca_ppef_ingest_jobs_component",
                    "tefca_ppef_ingest_jobs", ["component"])
    _create_index("ix_tefca_ppef_ingest_jobs_resource_version",
                    "tefca_ppef_ingest_jobs", ["resource_version"])
    _create_index("ix_tefca_ppef_ingest_jobs_state",
                    "tefca_ppef_ingest_jobs", ["state"])
    _create_index("ix_tefca_ppef_ingest_jobs_created_at",
                    "tefca_ppef_ingest_jobs", ["created_at"])
    _create_index("ix_tefca_ppef_ingest_jobs_heartbeat_at",
                    "tefca_ppef_ingest_jobs", ["heartbeat_at"])
    _create_index("idx_ppef_job_state_heartbeat",
                    "tefca_ppef_ingest_jobs", ["state", "heartbeat_at"])
    _create_index("idx_ppef_job_component_version",
                    "tefca_ppef_ingest_jobs", ["component", "resource_version"])
    _create_index(
        "uq_ppef_job_active_component",
        "tefca_ppef_ingest_jobs",
        ["component", "resource_version", "active_marker"],
        unique=True,
        postgresql_where=sa.text("active_marker IS TRUE"),
    )


def downgrade() -> None:
    for name in (
        "uq_ppef_job_active_component",
        "idx_ppef_job_component_version",
        "idx_ppef_job_state_heartbeat",
        "ix_tefca_ppef_ingest_jobs_heartbeat_at",
        "ix_tefca_ppef_ingest_jobs_created_at",
        "ix_tefca_ppef_ingest_jobs_state",
        "ix_tefca_ppef_ingest_jobs_resource_version",
        "ix_tefca_ppef_ingest_jobs_component",
    ):
        _drop_index(name, table_name="tefca_ppef_ingest_jobs")
    _drop_table("tefca_ppef_ingest_jobs")
