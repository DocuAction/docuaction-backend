"""TEFCA registry — Phase 1B schema (10 tefca_reg_* / tefca_entity_* tables).

Revision ID: 20260725_tefca_registry
Revises:      20260725_platform_config
Create Date:  2026-07-25

Creates the NEW normalized TEFCA registry tables ONLY. The legacy ``tefca_*``
tables (app.Tefca) and the platform_* tables are never touched. The main entity
table is ``tefca_reg_entities`` (avoids colliding with legacy ``tefca_entities``).

As with the TEFCA/platform migrations, these models live on
``app.core.database.Base`` (not the Base alembic/env.py autogenerate targets), so
this migration creates/drops directly from the metadata, scoped to exactly the 10
registry tables. ``checkfirst=True`` gives IF NOT EXISTS semantics.

Tables created (all new):
  tefca_reg_entities, tefca_entity_identifiers, tefca_entity_relationships,
  tefca_entity_versions, tefca_entity_endpoints, tefca_verification_jobs,
  tefca_verification_checks, tefca_entity_findings, tefca_import_batches,
  tefca_reg_audit_log
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260725_tefca_registry"
down_revision = "20260725_platform_config"
branch_labels = None
depends_on = None


def _registry_metadata_and_tables():
    import app.tefca_registry.models as rm  # noqa: F401  (registration side effect)
    from app.core.database import Base
    md = Base.metadata
    return md, [md.tables[name] for name in rm.TEFCA_REG_TABLE_ORDER]


def upgrade() -> None:
    bind = op.get_bind()
    md, tables = _registry_metadata_and_tables()
    md.create_all(bind=bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    md, tables = _registry_metadata_and_tables()
    md.drop_all(bind=bind, tables=tables, checkfirst=True)
