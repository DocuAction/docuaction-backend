"""TEFCA Review Protocol — initial schema (7 tables).

Creates the TEFCA tables ONLY. Existing application tables are never touched.

Revision ID: 20260627_tefca_initial
Revises:      (none — first migration)
Create Date:  2026-06-27

ONC TEFCA Review Protocol — Contract No. 7571MN26F80064 (HHS/ONC)

Implementation note
-------------------
The TEFCA models live on `app.core.database.Base`, while alembic/env.py targets a
different Base (`app.database.Base`), so Alembic autogenerate cannot see these
tables. To guarantee the migration stays byte-for-byte in sync with the ORM
models (including native PostgreSQL ENUM types), this migration creates/drops the
tables directly from the TEFCA metadata, scoped to the seven TEFCA tables only.
It is explicit about exactly which tables it manages and touches nothing else.

Tables created (all new — none pre-exist):
  1. tefca_review_cycles
  2. tefca_entities
  3. tefca_evidence_records
  4. tefca_source_cache
  5. tefca_priority_cases
  6. tefca_reports
  7. tefca_analyst_queue
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260627_tefca_initial"
down_revision = None
branch_labels = None
depends_on = None


# Parent tables first so foreign keys resolve. All FKs are intra-TEFCA; no TEFCA
# table references any non-TEFCA table, so the set is self-contained.
TEFCA_TABLE_ORDER = [
    "tefca_review_cycles",
    "tefca_entities",
    "tefca_evidence_records",
    "tefca_source_cache",
    "tefca_priority_cases",
    "tefca_reports",
    "tefca_analyst_queue",
]


def _tefca_metadata_and_tables():
    # Importing the models registers all seven tables (and their enum types) on
    # the shared Base metadata.
    import app.Tefca.models  # noqa: F401  (registration side effect)
    from app.core.database import Base
    md = Base.metadata
    return md, [md.tables[name] for name in TEFCA_TABLE_ORDER]


def upgrade() -> None:
    bind = op.get_bind()
    md, tables = _tefca_metadata_and_tables()
    # create_all(tables=...) creates the tables AND their native ENUM types,
    # ordering by FK and creating each shared enum type exactly once.
    # checkfirst=True makes it a safe no-op for anything already present.
    md.create_all(bind=bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    md, tables = _tefca_metadata_and_tables()
    # drop_all(tables=...) drops all tables first, THEN the shared ENUM types —
    # avoiding the "cannot drop type, other objects depend on it" error that a
    # naive per-table drop hits when an enum (e.g. bucketclassification) is used
    # by multiple TEFCA tables.
    md.drop_all(bind=bind, tables=tables, checkfirst=True)
