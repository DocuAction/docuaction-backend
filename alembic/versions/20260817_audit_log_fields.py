"""AT-001 / AT-009 — audit_logs: event_type, outcome, correlation_id.

Revision ID: 20260817_audit_fields
Revises:      20260725_tefca_registry
Create Date:  2026-08-17

WHAT THIS FIXES
---------------
`audit_logs` carried action, resource_type, resource_id, details, ip_address and
created_at. It did NOT carry the event's category, its outcome, or the id tying
together the events of one business transaction. Those three facts were either
absent entirely or buried inside the `details` JSON blob, so the two questions an
auditor asks first —

    "show me every failed authentication"
    "show me everything that happened during this import"

— could not be expressed in SQL. They required scanning the table and re-parsing
JSON per row, and the Audit Trail UI had no column to filter on (AT-007).

SCOPE: STRUCTURE ONLY
---------------------
This revision adds three nullable columns and their three indexes. It writes no
row. Historical rows keep whatever they have — which today is NULL.

The original version of this migration also ran three UPDATE statements to
backfill the 251 pre-existing rows. That backfill has been removed from the
migration and now lives in

    scripts/remediate_audit_log_classification.py

as a separately-authorized data-remediation operation. Three reasons:

  1. It was unexecutable. The first statement filtered on `details ? 'x'`. The
     `?` key-existence operator is defined for `jsonb` only, and
     `audit_logs.details` is `json`. Every run aborted with
     `operator does not exist: json ? unknown`, taking the whole revision — and
     therefore the rest of the chain — down with it.
  2. Schema migration and history rewriting are different risk classes. A
     structural migration must be safe to run unattended on any environment; a
     statement that assigns an audit classification to 251 existing audit
     records is a records-management action and needs its own authorization,
     its own dry run and its own reversibility story.
  3. Coupling them made the structural fix hostage to the data decision. The
     three indexes below could not be created until somebody was ready to
     rewrite history.

DRIFT
-----
`app/main.py` startup calls `Base.metadata.create_all()`, so on an existing
deployment the three columns are already present while none of the three
indexes are — `create_all()` builds columns from the model but this revision's
indexes are declared here, not on the model. The original code nested each
`create_index` inside its column's `if not present` branch, so on exactly that
schema the indexes could never be created: the guard that correctly skipped the
column silently skipped the index too. Column creation and index creation are
now independent, which is the only arrangement that converges from a partially
drifted schema.

Offline (`--sql`) mode has no bind to inspect, so the guards open and the full
DDL is emitted. That script is drift-unaware by construction and is meant to be
read before it is run.

`audit_logs` IS NOT OWNED BY THIS CHAIN
---------------------------------------
The project declares two independent SQLAlchemy Bases:

    app.database.Base       47 tables — what alembic/env.py uses as
                            target_metadata, and what the chain builds
    app.core.database.Base  16 tables — includes audit_logs, and is what
                            app/main.py startup calls create_all() on

They share no registry. No revision in the chain creates `audit_logs`; only
application startup does. So on a database built from `alembic upgrade head`
alone, this revision has no table to alter. It therefore checks first and, if
the table is absent, logs a warning and adds nothing rather than aborting the
chain on a table it does not own. Bringing the sixteen `app.core.database.Base`
tables under Alembic is a separate change: it needs its own revision, and it
needs env.py to point at a metadata that covers both Bases.

SAFETY
------
Additive only — three nullable columns and three indexes. No existing column is
altered or dropped, and no row is inserted, updated or deleted. The downgrade
drops exactly what the upgrade added, and drops each object only if it is there.
"""
import logging

from alembic import op
import sqlalchemy as sa

log = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision = "20260817_audit_fields"
down_revision = "20260725_tefca_registry"
branch_labels = None
depends_on = None

TABLE = "audit_logs"

COLUMNS = (
    ("event_type", sa.String(50)),
    ("outcome", sa.String(20)),
    ("correlation_id", sa.String(64)),
)

INDEXES = (
    ("ix_audit_logs_event_type", ["event_type"]),
    ("ix_audit_logs_outcome", ["outcome"]),
    ("ix_audit_logs_correlation_id", ["correlation_id"]),
)


# ── drift guards ────────────────────────────────────────────────────────────


def _offline() -> bool:
    return op.get_context().as_sql


def _table_present() -> bool:
    if _offline():
        return True
    return TABLE in set(sa.inspect(op.get_bind()).get_table_names())


def _columns() -> set:
    if _offline():
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def _indexes() -> set:
    if _offline():
        return set()
    inspector = sa.inspect(op.get_bind())
    names = {i["name"] for i in inspector.get_indexes(TABLE)}
    names |= {u["name"] for u in inspector.get_unique_constraints(TABLE)
              if u.get("name")}
    return names


def _skip_if_table_absent() -> bool:
    """`audit_logs` is not owned by this migration chain — see the docstring."""
    if _table_present():
        return False
    log.warning(
        "%s: table %r is absent, so this revision added nothing. %r belongs to "
        "app.core.database.Base, which alembic/env.py does not use as "
        "target_metadata and which no revision creates; it is materialised by "
        "app/main.py startup. On this database the three audit columns and "
        "their indexes are therefore still missing. Re-run this revision after "
        "the table exists, or bring %r into the chain.",
        revision, TABLE, TABLE, TABLE)
    return True


def upgrade() -> None:
    if _skip_if_table_absent():
        return
    existing_columns = _columns()
    for name, coltype in COLUMNS:
        if name not in existing_columns:
            op.add_column(TABLE, sa.Column(name, coltype, nullable=True))

    # Re-read after the ADD COLUMNs, and read indexes separately from columns:
    # the drifted schema has all three columns and none of the three indexes.
    existing_indexes = _indexes()
    for name, columns in INDEXES:
        if name not in existing_indexes:
            op.create_index(name, TABLE, columns)


def downgrade() -> None:
    if not _table_present():
        return
    existing_indexes = _indexes()
    for name, _columns_ in reversed(INDEXES):
        if _offline() or name in existing_indexes:
            op.drop_index(name, table_name=TABLE)

    existing_columns = _columns()
    for name, _coltype in reversed(COLUMNS):
        if _offline() or name in existing_columns:
            op.drop_column(TABLE, name)
