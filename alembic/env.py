import asyncio
from logging.config import fileConfig
from sqlalchemy import MetaData, pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── model registries ────────────────────────────────────────────────────────
# The project declares two independent declarative Bases:
#
#   app.database.Base        47 tables — the ERP/business models
#   app.core.database.Base   89 tables — core, TEFCA, platform, registry, RCE
#
# Only the first was a target here, and only `app.models` was imported, so
# Alembic could see 47 of the 136 modelled tables. Everything the TEFCA work
# built was invisible to `--autogenerate` and to `alembic check`, which is why
# that check proposed dropping most of the database.
#
# Metadata is populated by IMPORTING the module that declares the model, not by
# declaring the Base. Every module below registers tables on one of the two
# Bases; drop one and its tables go invisible again. `app.main` is deliberately
# NOT imported — it registers routers and loads network feed configuration, and
# a migration run must not do either.
from app.database import Base as AppBase
from app.core.database import Base as CoreBase

from app.models import *  # noqa: F401,F403  ERP models -> AppBase (+ core models)
import app.tefca_registry.models       # noqa: F401,E402  registry
import app.tefca_registry.rce.models   # noqa: F401,E402  RCE pipeline, Area 1/2
import app.platform_config.models      # noqa: F401,E402  platform_*
import app.Tefca.models                # noqa: F401,E402  review, evidence, PPEF
import app.case_management.models      # noqa: F401,E402
import app.models.migration_models     # noqa: F401,E402
import app.api.templates               # noqa: F401,E402  declares output_templates
import app.api.validation_routes       # noqa: F401,E402  declares validation_queue

config = context.config

# Override URL from environment if set
db_url = os.getenv("DATABASE_URL")
if db_url:
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── the one name that collides across the two Bases ─────────────────────────
# `users` is declared twice: app/models/__init__.py (9 columns, on AppBase) and
# app/models/database.py (16 columns, on CoreBase). Alembic refuses a duplicate
# table key across target metadata collections — `ValueError: Duplicate table
# keys across multiple MetaData objects: "users"` — and it is right to, because
# the two definitions disagree about what the table is.
#
# The live table has 16 columns and matches the CoreBase definition exactly,
# column for column, with nothing left over on either side. CoreBase is
# authoritative; the AppBase copy is stale. The stale one is dropped from the
# comparison rather than deleted from the codebase: removing a model is an
# application change, and this file's job is to describe the schema, not edit it.
DUPLICATE_TABLES_ON_APP_BASE = {"users"}


def _merged_metadata():
    """One MetaData holding both Bases, with the collision resolved.

    A list of MetaData would be the obvious shape, but it cannot express "these
    two collections share a name and this is the definition that wins". Copying
    into a single collection can: `saved_searches` (AppBase) has a foreign key to
    `users`, and in a merged collection that key resolves against the authoritative
    CoreBase definition instead of dangling.
    """
    merged = MetaData()
    for table in CoreBase.metadata.sorted_tables:
        table.to_metadata(merged)
    for table in AppBase.metadata.sorted_tables:
        if table.name in DUPLICATE_TABLES_ON_APP_BASE:
            continue
        table.to_metadata(merged)
    return merged


target_metadata = _merged_metadata()

# ── tables that exist by design without an ORM model ────────────────────────
# Autogenerate compares the database against the models, so a table no model
# declares looks like something to drop. For these twelve that would be wrong,
# and each is here for a stated reason rather than to quieten the tool.
#
# `area1_mutation_log` is written by database triggers and read by auditors.
# Giving it a model would put the Area 1 mutation log within reach of an ORM
# session, which is the one thing it must not be.
#
# The rest are created by hand-written `CREATE TABLE IF NOT EXISTS` in
# application code — app/bulletin_intelligence/bulletin_store.py and
# app/Tefca/qa_engine.py — at startup. There is no model to compare against, so
# no comparison can be meaningful. Bringing them under Alembic means writing
# models or migrations inside two subsystems this work is not authorised to
# touch, so it is named as outstanding rather than done badly. Until then this
# is the honest description: Alembic does not own these, and says so.
#
# `articles` and `briefings` are deliberately NOT here. They look like
# candidates — app/bulletin_intelligence/story_repository.py creates them with
# CREATE TABLE IF NOT EXISTS — but that file drives a local SQLite store, not
# this database. They never appear in PostgreSQL, and naming them would imply
# Alembic is choosing to ignore tables that exist.
UNMODELLED_TABLES = {
    "area1_mutation_log",
    "bulletin_articles",
    "bulletin_audit_log",
    "bulletin_briefings",
    "bulletin_cost_logs",
    "bulletin_delivery_log",
    "bulletin_recipients",
    "bulletin_run_log",
    "bulletin_search_profiles",
    "bulletin_source_outcome",
    "bulletin_source_registry",
    "tefca_qa_audit",
}

# Hand-written indexes the models cannot express — a partial index, or a shape
# chosen for one query. Saying "a migration owns this" beats letting
# `alembic check` carry a permanent false positive that teaches people to
# ignore it.
MIGRATION_OWNED_INDEXES = {
    "idx_area1_mutation_occurred",
    "idx_area1_mutation_row",
    "idx_area1_mutation_table",
    # 20260824_evidence_prov wrote this for the provenance join; the model
    # declares the column without index=True.
    "idx_dim_evidence_source_version",
}


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table" and name in UNMODELLED_TABLES:
        return False
    if type_ == "index":
        if name in MIGRATION_OWNED_INDEXES:
            return False
        table = getattr(object_, "table", None)
        if table is not None and table.name in UNMODELLED_TABLES:
            return False
    return True


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
