"""Platform configuration — Phase 1A schema (12 platform_* config tables).

Revision ID: 20260725_platform_config
Revises:      20260627_tefca_dashboard
Create Date:  2026-07-25

Creates the ``platform_*`` configuration tables ONLY. Existing application and
TEFCA tables are never touched.

Ordering note
-------------
Conceptually the platform tables are the foundation and sit *before* the TEFCA
tables. In this repo the TEFCA migrations already exist and were authored first,
so this migration is chained after the current head. That is safe: no TEFCA
table has any foreign key into a platform table (and vice-versa), so the two
sets are physically independent and creation order between them is irrelevant.

Implementation note
-------------------
The platform models live on ``app.core.database.Base`` (the shared runtime Base),
while alembic/env.py targets a different Base (``app.database.Base``), so Alembic
autogenerate cannot see these tables. As with the TEFCA migrations, this file
creates/drops the tables directly from the platform metadata, scoped to exactly
the platform tables. ``checkfirst=True`` makes it a safe no-op (IF NOT EXISTS
semantics) for anything already present.

Tables created (all new):
  platform_tenants, platform_agencies, platform_programs, platform_modules,
  platform_workspaces, platform_pages, platform_features,
  platform_workspace_features, platform_data_sources, platform_themes,
  platform_jurisdictions, platform_import_formats, platform_identifier_types
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260725_platform_config"
down_revision = "20260627_tefca_dashboard"
branch_labels = None
depends_on = None


def _platform_metadata_and_tables():
    # Importing the models registers all platform tables on the shared Base.
    import app.platform_config.models as pm  # noqa: F401  (registration side effect)
    from app.core.database import Base
    md = Base.metadata
    return md, [md.tables[name] for name in pm.PLATFORM_TABLE_ORDER]


def upgrade() -> None:
    bind = op.get_bind()
    md, tables = _platform_metadata_and_tables()
    # create_all(tables=...) resolves FK ordering (incl. the tenants<->agencies
    # use_alter cycle) and, with checkfirst=True, skips anything already present.
    md.create_all(bind=bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    md, tables = _platform_metadata_and_tables()
    md.drop_all(bind=bind, tables=tables, checkfirst=True)
