"""Two partial expression indexes for organisation-first IQVIA affiliation lookups.

Revision ID: 20261009_iqvia_affil_org_indexes
Revises: 20261008_record_check_results
Create Date: 2026-10-08

REVISION ID LENGTH: alembic_version.version_num is VARCHAR(32); the id is shortened to exactly 32
characters ("20261009_iqvia_affil_org_indexes").

WHY THIS EXISTS
---------------
The advisory organisation lookup (app/tefca_registry/rce/iqvia_affiliation_consumer.py,
organisation_relationships) resolves an organisation identifier to its HCO keys with

    WHERE source_snapshot_id = :s
      AND nullif(btrim(payload->>'ORG_NPI'), '') IN (...)          -- or ORG_CCN_ID

The identifiers live inside the JSONB payload and no existing index covers them, so on
DEV (about 7.55M rows in iqvia_affiliation_observation) the lookup is a sequential scan and
took about 28 s. Blank identifiers are stored as empty strings, hence nullif(btrim()).

WHAT IT ADDS (approved design, Option A in docs/architecture/iqvia_org_first_design.md)
  ix_iqvia_affil_snapshot_org_npi  (source_snapshot_id, nullif(btrim(payload->>'ORG_NPI'), ''))
                                   WHERE nullif(btrim(payload->>'ORG_NPI'), '') IS NOT NULL
  ix_iqvia_affil_snapshot_org_ccn  (source_snapshot_id, nullif(btrim(payload->>'ORG_CCN_ID'), ''))
                                   WHERE nullif(btrim(payload->>'ORG_CCN_ID'), '') IS NOT NULL

The expression text matches the consumer's _org_npi_expr()/_org_ccn_expr() exactly, which is
what lets the planner use them. Partial, so rows with a blank identifier are not indexed.

SIZE AND COST
  Measured on local synthetic data (see the design doc): about 69 MB (NPI) + 41 MB (CCN),
  roughly 15 s build each. DEV estimate 130-155 MB total at 7.55M rows.

SAFETY
  - Additive only. No column is altered and no row is read for change or written.
  - CREATE INDEX CONCURRENTLY takes no lock that blocks reads or writes of the table. It
    runs outside a transaction (autocommit_block), so a failure part-way can leave an
    INVALID index: the upgrade drops an invalid leftover of the same name and rebuilds it.
  - Ownership: env.py sets the session role (DB_MIGRATION_ROLE, e.g. docuaction_owner) in
    the connection startup packet, so these indexes are owned by the table owner exactly as
    every other object this chain creates. No GRANT is needed (indexes carry no privileges).
  - Run it in a quiet window (the build competes for I/O with ingestion) and do not run it
    while an IQVIA import is loading the table.
  - NOT applied by CI to any shared database. It is applied only through the governed
    migration path; local test databases exercise it.
"""
from __future__ import annotations

from alembic import context, op

revision = "20261009_iqvia_affil_org_indexes"
down_revision = "20261008_record_check_results"
branch_labels = None
depends_on = None

TABLE = "iqvia_affiliation_observation"
NPI_EXPR = "nullif(btrim(payload->>'ORG_NPI'), '')"
CCN_EXPR = "nullif(btrim(payload->>'ORG_CCN_ID'), '')"
INDEXES = (
    ("ix_iqvia_affil_snapshot_org_npi", NPI_EXPR),
    ("ix_iqvia_affil_snapshot_org_ccn", CCN_EXPR),
)


def _drop_invalid_leftover(name: str) -> None:
    """A failed CONCURRENTLY build leaves an INVALID index that IF NOT EXISTS would skip."""
    op.execute(f"""
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT n.nspname FROM pg_class c
                   JOIN pg_index i ON i.indexrelid = c.oid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.relname = '{name}' AND NOT i.indisvalid
                     AND n.nspname = ANY (current_schemas(false))
          LOOP
            EXECUTE format('DROP INDEX %I.%I', r.nspname, '{name}');
          END LOOP;
        END $$;
    """)


def upgrade() -> None:
    with context.get_context().autocommit_block():
        for name, expr in INDEXES:
            _drop_invalid_leftover(name)
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {TABLE} "
                f"(source_snapshot_id, {expr}) WHERE {expr} IS NOT NULL"
            )


def downgrade() -> None:
    with context.get_context().autocommit_block():
        for name, _expr in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
