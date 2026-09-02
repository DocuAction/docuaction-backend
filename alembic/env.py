import asyncio
from logging.config import fileConfig
from sqlalchemy import MetaData, pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── this is the TEFCA program chain ─────────────────────────────────────────
# DocuAction is a platform of program modules that share a Core, and Option D
# of docs/database_domain_architecture.md makes each module the owner of its own
# schema and its own migration chain. This environment owns TEFCA.
#
# Two problems had to be fixed in sequence to get here, and both matter.
#
# First, `target_metadata` pointed at one of the project's two declarative Bases
# and imported one of the eight modules that populate them, so Alembic could see
# 47 of the 135 modelled tables and `alembic check` proposed dropping most of the
# database. Metadata is populated by IMPORTING the module that declares the
# model, not by declaring the Base.
#
# Second, targeting *everything* over-corrected. It made this chain responsible
# for 61 tables belonging to four other products — ERP, case management,
# migration tooling and the enterprise core — and `upgrade head` would have
# created all of them in a database holding federal contract evidence.
#
# So the input is deliberately narrow: TEFCA's own models, plus the Core tables
# a TEFCA deployment genuinely needs. `app.main` is not imported — it registers
# routers and reads network feed configuration, and a migration run must do
# neither.
from app.core.database import Base as CoreBase

import app.models.database              # noqa: F401,E402  users, audit_logs
import app.platform_config.models       # noqa: F401,E402  platform_*
import app.tefca_registry.models        # noqa: F401,E402  registry
import app.tefca_registry.rce.models    # noqa: F401,E402  RCE pipeline, Area 1/2
import app.Tefca.models                 # noqa: F401,E402  review, evidence, PPEF

config = context.config

# Override URL from environment if set
db_url = os.getenv("DATABASE_URL")
if db_url:
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    # Escape '%' for configparser.
    #
    # set_main_option() stores the value in a ConfigParser, which treats '%' as
    # interpolation syntax and raises on anything that is not '%%' or a valid
    # '%(name)s'. A URL-encoded password makes that certain: '@' becomes '%40',
    # and configparser then fails with "invalid interpolation syntax" before a
    # single migration runs.
    #
    # Found while rehearsing the production baseline against a copy of the
    # production schema. Without this the whole cutover would have stopped at
    # `alembic upgrade head` -- not on a schema problem, but on a punctuation
    # character in a password, at the point in the sequence where the database
    # is already half-migrated.
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── what this chain owns ────────────────────────────────────────────────────
# Ownership is decided by the module that declares the model, not by the table
# name, and the classification is the one in docs/database_domain_architecture.md.
TEFCA_MODULE_PREFIXES = ("app.Tefca", "app.tefca_registry")

#: Core tables a TEFCA deployment genuinely needs. Traced from `ast` import
#: statements, not by matching names — `app/Tefca/routes.py` joins `audit_logs`
#: to `users` for the audit trail, and `ppef_scheduler.py` resolves
#: `job.requested_by` by email. Both are read-only from TEFCA and neither is
#: foreign-keyed from a TEFCA table. Two other apparent dependencies were false:
#: `documents` came from `from docx import Document` (python-docx, not the ORM
#: model) and `audit_log` from a class-name collision with `audit_logs`.
CORE_DEPENDENCIES = {"users", "audit_logs"}

#: Program configuration. Core-owned under Option D, but `20260725_platform_config`
#: — a released revision that cannot be rewritten — creates it, so this chain
#: still owns it until Stage 2 moves Core to its own chain. Describing that
#: honestly keeps `alembic check` meaningful; pretending otherwise would leave
#: thirteen live tables managed by nobody.
CORE_OWNED_UNTIL_STAGE_2 = set(app.platform_config.models.PLATFORM_TABLE_ORDER)


def _tefca_tables():
    """Table names declared by a TEFCA module."""
    names = set()
    for mapper in CoreBase.registry.mappers:
        cls = mapper.class_
        table = getattr(cls, "__tablename__", None)
        if table and cls.__module__.startswith(TEFCA_MODULE_PREFIXES):
            names.add(table)
    return names


def _scoped_metadata():
    """Only what the TEFCA chain owns.

    Copying into a fresh MetaData rather than filtering at comparison time is
    deliberate: it makes the scope a property of the environment instead of a
    rule applied afterwards, so a model imported by accident cannot leak into a
    migration.
    """
    owned = _tefca_tables() | CORE_DEPENDENCIES | CORE_OWNED_UNTIL_STAGE_2
    scoped = MetaData()
    for table in CoreBase.metadata.sorted_tables:
        if table.name in owned:
            table.to_metadata(scoped)
    missing = owned - set(scoped.tables)
    if missing:
        raise RuntimeError(
            f"TEFCA chain claims tables that no imported model declares: "
            f"{sorted(missing)}. Add the module that declares them to the "
            f"imports above, or remove them from the owned set.")
    # A foreign key pointing outside the scope would mean the boundary is wrong.
    for table in scoped.tables.values():
        for fk in table.foreign_keys:
            target = (fk._colspec.split(".")[0] if isinstance(fk._colspec, str)
                      else fk.column.table.name)
            if target not in owned:
                raise RuntimeError(
                    f"{table.name} has a foreign key to {target}, which this "
                    f"chain does not own. Either {target} belongs in the TEFCA "
                    f"scope or the model does not belong to TEFCA.")
    return scoped


target_metadata = _scoped_metadata()

#: Everything this chain owns, for `include_object`. A table outside it is
#: another module's business and must not be reported as drift here.
TEFCA_CHAIN_TABLES = set(target_metadata.tables)

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
    """Compare only what this chain owns.

    A scoped chain shares its database with other modules' tables, and every one
    of them would otherwise look like something to drop. Restricting the
    comparison is what makes `alembic check` mean "is the TEFCA schema in sync"
    rather than "does this database contain anything else".
    """
    if type_ == "table":
        return name in TEFCA_CHAIN_TABLES and name not in UNMODELLED_TABLES
    if type_ == "index":
        if name in MIGRATION_OWNED_INDEXES:
            return False
        table = getattr(object_, "table", None)
        if table is not None and (table.name not in TEFCA_CHAIN_TABLES
                                  or table.name in UNMODELLED_TABLES):
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
    # -- run as the owner role ---------------------------------------------
    # docuaction_owner has no LOGIN. Migrations connect as a MEMBER of it (the
    # dedicated migration identity) and must assume it BEFORE any DDL, or every
    # object is created owned by the connecting principal instead - which is
    # what happened to rce_delivery_jobs in Azure DEV on 2026-09-02 and needed
    # a recorded ALTER OWNER afterwards. asyncpg sends server_settings in the
    # startup packet, so `role` is in effect before Alembic issues a statement.
    # Opt-in via DB_MIGRATION_ROLE; unset leaves behaviour exactly as before.
    connect_args = {}
    migration_role = os.getenv("DB_MIGRATION_ROLE", "").strip()
    if migration_role:
        connect_args["server_settings"] = {"role": migration_role}
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
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
