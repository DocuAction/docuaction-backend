"""Partial index for NPI-keyed advisory address lookups over HCP_ADDR rows.

Revision ID: 20261106_iqvia_hcp_npi_index
Revises: 20261030_default_routing_prov
Create Date: 2026-11-06

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); this id is 29
characters.

WHY THIS EXISTS
---------------
The advisory address comparison (app/tefca_registry/rce/
iqvia_advisory_comparison.py, compare_hcp_address_entity) reads

    WHERE source_snapshot_id = :s AND npi IN (...)

over `iqvia_hcp_observation` (~6.85M rows on DEV). `npi` is a lifted plain
column (set at import) but carries no index, so each per-entity lookup is a
sequential scan. Same pattern, same template and same safety notes as
20261009_iqvia_affil_org_indexes — simpler here because the column is plain,
not a JSONB expression.

WHAT IT ADDS
  ix_iqvia_hcp_snapshot_npi  (source_snapshot_id, npi) WHERE npi IS NOT NULL

Partial: rows without an NPI (importer stores NULL for blank) are not indexed.

SAFETY
  - Additive only; CREATE INDEX CONCURRENTLY (no read/write lock), run in an
    autocommit block; a failed build's INVALID leftover is dropped and rebuilt.
  - Owned by the table owner via env.py's DB_MIGRATION_ROLE, like every other
    object in this chain.
  - NOT applied by CI to any shared database; applied only through the
    governed migration path in a quiet window, never during an IQVIA import.
"""
from __future__ import annotations

from alembic import context, op

revision = "20261106_iqvia_hcp_npi_index"
down_revision = "20261030_default_routing_prov"
branch_labels = None
depends_on = None

TABLE = "iqvia_hcp_observation"
NAME = "ix_iqvia_hcp_snapshot_npi"

#: Operator query: present-but-INVALID leftover of an interrupted build.
INVALID_INDEX_SQL = (
    "SELECT c.relname FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
    f"WHERE c.relname = '{NAME}' AND NOT i.indisvalid"
)


def _verify_built() -> None:
    bind = op.get_bind()
    row = bind.exec_driver_sql(
        "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
        f"WHERE c.relname = '{NAME}'").fetchone()
    if row is None or row[0] is not True:
        raise RuntimeError(
            f"{NAME} build did not complete cleanly (missing or INVALID). The revision is NOT "
            "recorded. Re-run the same upgrade: an INVALID leftover is dropped and rebuilt.")


def _drop_invalid_leftover() -> None:
    op.execute(f"""
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT n.nspname FROM pg_class c
                   JOIN pg_index i ON i.indexrelid = c.oid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.relname = '{NAME}' AND NOT i.indisvalid
                     AND n.nspname = ANY (current_schemas(false))
          LOOP
            EXECUTE format('DROP INDEX %I.%I', r.nspname, '{NAME}');
          END LOOP;
        END $$;
    """)


def upgrade() -> None:
    with context.get_context().autocommit_block():
        _drop_invalid_leftover()
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} ON {TABLE} "
            f"(source_snapshot_id, npi) WHERE npi IS NOT NULL"
        )
        _verify_built()


def downgrade() -> None:
    with context.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NAME}")
