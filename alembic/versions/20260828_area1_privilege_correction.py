"""Area 1 privileges — replace the blanket revoke with the column-level design.

Revision ID: 20260828_area1_grants
Revises:     20260827_startup_coverage
Create Date: 2026-08-22

WHAT THIS CORRECTS
------------------
`20260822_rce_pipeline` ends by applying, per Area 1 table:

    REVOKE UPDATE, DELETE ON <table> FROM <role>
    GRANT  SELECT, INSERT  ON <table> TO   <role>

A blanket `REVOKE UPDATE` on `rce_source_records` breaks the pipeline.
`promotion.promote_delivery` writes `promotion_status` and `canonical_entity_id`
on that table *after* the Area 2 entities are committed. With UPDATE revoked
table-wide the write is refused mid-transaction and Area 1's promotion markers
end up out of step with Area 2 — the failure Phase 4 identified.

The correction is not "grant UPDATE back". It is to grant UPDATE on exactly the
two workflow columns and nothing else. PostgreSQL evaluates UPDATE privilege
against the columns a statement actually names, so promotion keeps working while
all fourteen evidence columns become unwritable by the application role.

WHY A NEW REVISION INSTEAD OF EDITING 20260822
----------------------------------------------
`20260822_rce_pipeline` has already run in environments. Editing it would make
one revision id mean two different things depending on when a database applied
it, which is the property that makes a migration chain worth having. It stays
byte-frozen. This revision states the corrected end state and runs after it.

On a fresh database the two run back to back inside one `upgrade head`:
20260822 revokes broadly, this revision narrows the revoke and returns the two
workflow columns. On an existing database only this revision's end state
matters, because it is written as an absolute statement of the desired
privileges rather than as a delta.

WHAT IT DOES NOT DO
-------------------
It does not transfer ownership. `immutability_grants_sql()` names
`docuaction_owner` as a *prerequisite*, and it is right to: a table's owner can
re-grant to itself at any time, so while the application role owns Area 1 the
revoke guards against an accidental code path but not against intent. Moving
ownership is also a one-way door for this migration — once the tables belong to
another role, this role can no longer alter them, and `downgrade()` could not
put the privileges back. So ownership stays a deployment step. The revision
checks and says so in the log rather than leaving the gap silent.

Not transferring ownership has a second, sharper consequence that testing turned
up. `rce_source_records.source_intake_id` references `rce_source_intakes`, and
PostgreSQL checks that on every insert with a `SELECT ... FOR KEY SHARE` run **as
the owner of the referenced table**. A row lock needs UPDATE or DELETE. So a role
that owns `rce_source_intakes` and has had UPDATE revoked cannot insert into
`rce_source_records` at all — the ingestion pipeline stops with `permission
denied for table rce_source_intakes`. `_grant_fk_lock_column` handles that with
a single column-level grant, issued only while the application role is still the
owner. See its docstring.

It writes no row. No INSERT, UPDATE or DELETE against any table.

DOWNGRADE
---------
Restores the privilege state that `20260822_rce_pipeline` leaves behind — the
blanket revoke plus SELECT/INSERT — because that is what the revision
immediately before this one produces. It does not restore whatever a particular
database happened to have before the chain was ever run; a downgrade returns you
to the previous revision's state, not to your history.
"""
import logging
import os

from alembic import op
import sqlalchemy as sa

log = logging.getLogger("alembic.runtime.migration")

revision = "20260828_area1_grants"
down_revision = "20260827_startup_coverage"
branch_labels = None
depends_on = None

#: Kept in step with app.tefca_registry.rce.repository by
#: tests/test_migration_chain.py, which fails if the two drift apart. Not
#: imported from there: a migration that reads application code stops being a
#: fixed record of what it did.
IMMUTABLE_TABLES = ("rce_source_intakes", "rce_source_records")
MUTABLE_WORKFLOW_COLUMNS = ("promotion_status", "canonical_entity_id")
OWNER_ROLE = "docuaction_owner"

#: The one column of `rce_source_intakes` the application may update, and only
#: while it still owns the table. See `_grant_fk_lock_column`.
INTAKE_FK_LOCK_COLUMN = "status"


def _offline() -> bool:
    return op.get_context().as_sql


def _has_table(name: str) -> bool:
    if _offline():
        return True
    return name in set(sa.inspect(op.get_bind()).get_table_names())


def _role() -> str:
    """The application role whose privileges are being set."""
    role = os.getenv("DB_APP_ROLE", "").strip()
    if role:
        return role
    if _offline():
        return "docuaction"
    return op.get_bind().execute(sa.text("SELECT current_user")).scalar()


def _owner_of(table: str):
    if _offline():
        return None
    return op.get_bind().execute(
        sa.text("SELECT tableowner FROM pg_tables WHERE tablename = :t"),
        {"t": table}).scalar()


def upgrade() -> None:
    role = _role()
    columns = ", ".join(MUTABLE_WORKFLOW_COLUMNS)

    for table in IMMUTABLE_TABLES:
        if not _has_table(table):
            log.warning("%s: %r is absent; Area 1 privileges not set on it.",
                        revision, table)
            continue
        # TRUNCATE is in the revoke because it is not covered by DELETE and
        # would empty Area 1 just as effectively.
        op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM "{role}"')
        op.execute(f'GRANT SELECT, INSERT ON {table} TO "{role}"')

    if _has_table("rce_source_records"):
        op.execute(
            f'GRANT UPDATE ({columns}) ON rce_source_records TO "{role}"')

    _grant_fk_lock_column(role)


def _grant_fk_lock_column(role: str) -> None:
    """Keep INSERT into rce_source_records working while the app role owns Area 1.

    `rce_source_records.source_intake_id` references `rce_source_intakes`.
    PostgreSQL enforces that on every insert with

        SELECT 1 FROM ONLY rce_source_intakes x WHERE id = $1 FOR KEY SHARE OF x

    and it runs that query **as the owner of the referenced table**
    (`ri_triggers.c` switches to `relowner` before the check). A row lock needs
    UPDATE or DELETE on top of SELECT. So revoking UPDATE and DELETE from a role
    that also OWNS `rce_source_intakes` does not merely make Area 1 unwritable —
    it makes it un-INSERT-able, and the RCE ingestion pipeline stops dead with

        permission denied for table rce_source_intakes
        CONTEXT: SQL statement "SELECT 1 FROM ONLY ... FOR KEY SHARE OF x"

    Two ways out, and which one applies depends on the deployment:

      ownership has moved to a role the application cannot authenticate as
          The check runs as that role, which kept its privileges. Nothing more
          is needed, and nothing is granted here. This is the intended state.

      the application role still owns the tables
          The check runs as the application role, so it needs UPDATE on at
          least one column. Granting it on exactly one column is the smallest
          thing that works: PostgreSQL's row-lock check is satisfied by a
          column-level UPDATE privilege, while `has_table_privilege(...,
          'UPDATE')` stays false and every other column stays unwritable.
          `status` is intake lifecycle metadata, written once at insert and
          never updated by any code path in app/tefca_registry/rce — it is the
          least consequential column to expose, and it carries no evidence.

    The grant is therefore conditional on ownership, not unconditional. Move
    ownership and it stops being issued.
    """
    if _offline() or not _has_table("rce_source_intakes"):
        return
    if _owner_of("rce_source_intakes") != role:
        log.info("%s: rce_source_intakes is owned by %r, so the foreign-key "
                 "row lock needs no grant to %r.",
                 revision, _owner_of("rce_source_intakes"), role)
        return
    op.execute(f'GRANT UPDATE ({INTAKE_FK_LOCK_COLUMN}) ON rce_source_intakes '
               f'TO "{role}"')
    log.warning(
        "%s: %r still OWNS the Area 1 tables. Two consequences. First, an owner "
        "may GRANT back to itself at any time, so the revoke holds against an "
        "accidental write but not against intent. Second, the foreign-key row "
        "lock on rce_source_intakes runs as the owner, so INSERT into "
        "rce_source_records needs UPDATE on one column of it — %r has been "
        "granted for that reason alone. Both go away when ownership moves to a "
        "role the application cannot authenticate as (%r), which is a "
        "deployment step: see "
        "app/tefca_registry/rce/repository.py::immutability_grants_sql.",
        revision, role, INTAKE_FK_LOCK_COLUMN, OWNER_ROLE)


def downgrade() -> None:
    """Restore what 20260822_rce_pipeline leaves behind."""
    role = _role()
    columns = ", ".join(MUTABLE_WORKFLOW_COLUMNS)

    if _has_table("rce_source_intakes"):
        op.execute(f'REVOKE UPDATE ({INTAKE_FK_LOCK_COLUMN}) ON '
                   f'rce_source_intakes FROM "{role}"')

    if _has_table("rce_source_records"):
        op.execute(
            f'REVOKE UPDATE ({columns}) ON rce_source_records FROM "{role}"')

    for table in IMMUTABLE_TABLES:
        if not _has_table(table):
            continue
        op.execute(f'REVOKE UPDATE, DELETE ON {table} FROM "{role}"')
        op.execute(f'GRANT SELECT, INSERT ON {table} TO "{role}"')
