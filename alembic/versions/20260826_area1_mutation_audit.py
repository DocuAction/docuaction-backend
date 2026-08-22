"""Area 1 mutation audit — record any UPDATE or DELETE on delivered evidence

Revision ID: 20260826_area1_audit
Revises:      20260825_qa_events
Create Date:  2026-08-26

WHAT THIS ADDS
──────────────
  area1_mutation_log   NEW  append-only; before/after image of any Area 1 write
  trg_area1_*_mutation      BEFORE UPDATE OR DELETE on both Area 1 tables

WHY A TRIGGER AND NOT ONLY APPLICATION LOGGING
`repository.record_mutation_attempt()` fires only when application code calls
it. A direct psql session — which the application role can open today, because
it OWNS both tables — bypasses it entirely. A trigger cannot be bypassed by any
statement that reaches the table.

THE TRIGGER FIRES ON THE LEGITIMATE PROMOTION WRITE TOO, DELIBERATELY
`promotion.promote_delivery` updates `promotion_status` and
`canonical_entity_id` on 23,562 rows. Those writes are recorded like any other.
A trigger with an exception for the application role would be a trigger that
stops recording exactly when the application misbehaves, so the exemption is
by COLUMN, not by role: the WHEN clause skips rows where no evidence column
actually changed, so a promotion marker costs nothing and an edit to `raw_line`
is always captured.

WHAT THIS DOES NOT DO
It does not revoke anything. Applying the grants requires transferring table
ownership off the application role, which needs a superuser this deployment's
credentials do not include — see `immutability_grants_sql()` for the exact
statements and `docs/area1_immutability_design.md` for why ownership must move
first.

REVERSIBLE — `downgrade()` drops the triggers, the function and the table.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260826_area1_audit"
down_revision = "20260825_qa_events"
branch_labels = None
depends_on = None

LOG_FUNCTION = """
CREATE OR REPLACE FUNCTION area1_log_mutation() RETURNS trigger AS $$
BEGIN
    INSERT INTO area1_mutation_log
      (table_name, operation, row_id, db_role, application,
       before_image, after_image, justification)
    VALUES (
      TG_TABLE_NAME,
      TG_OP,
      OLD.id,
      current_user,
      current_setting('application_name', true),
      to_jsonb(OLD),
      CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) ELSE NULL END,
      current_setting('area1.justification', true)
    );
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$ LANGUAGE plpgsql SECURITY DEFINER;
"""

#: Skip the promotion marker write. Exempting by COLUMN rather than by role is
#: deliberate: a role-based exemption stops recording precisely when the
#: application is the thing doing something wrong.
#:
#: TWO TRIGGERS, NOT ONE. `TG_OP` is not available inside a trigger WHEN clause
#: — only in the function body — so the UPDATE case carries the column filter
#: and the DELETE case carries none. Every DELETE is recorded unconditionally,
#: which is correct: there is no such thing as a routine Area 1 delete.
RECORD_UPDATE_TRIGGER = """
CREATE TRIGGER trg_area1_record_mutation
BEFORE UPDATE ON rce_source_records
FOR EACH ROW
WHEN (
  OLD.raw_line       IS DISTINCT FROM NEW.raw_line
  OR OLD.parsed        IS DISTINCT FROM NEW.parsed
  OR OLD.record_sha256 IS DISTINCT FROM NEW.record_sha256
  OR OLD.line_number   IS DISTINCT FROM NEW.line_number
  OR OLD.field_count   IS DISTINCT FROM NEW.field_count
  OR OLD.parse_status  IS DISTINCT FROM NEW.parse_status
  OR OLD.source_rce_id IS DISTINCT FROM NEW.source_rce_id
)
EXECUTE FUNCTION area1_log_mutation();
"""

RECORD_DELETE_TRIGGER = """
CREATE TRIGGER trg_area1_record_delete
BEFORE DELETE ON rce_source_records
FOR EACH ROW EXECUTE FUNCTION area1_log_mutation();
"""

INTAKE_TRIGGER = """
CREATE TRIGGER trg_area1_intake_mutation
BEFORE UPDATE OR DELETE ON rce_source_intakes
FOR EACH ROW EXECUTE FUNCTION area1_log_mutation();
"""



# ── offline (--sql) tolerance ───────────────────────────────────────────────
# `alembic upgrade --sql` binds a MockConnection, which sa.inspect() cannot
# read. The guards below would raise NoInspectionAvailable and no reviewable
# script could be produced. Offline they are handed an inspector that reports an
# empty database, so upgrade() emits its full DDL — drift-unaware by
# construction, which is what an offline script is. downgrade() renders as a
# no-op offline for the same reason, and is not offered as a review artefact.


class _OfflineInspector:
    """Reports an empty schema so every create guard opens."""

    @staticmethod
    def get_table_names():
        return []

    @staticmethod
    def get_columns(table):
        return []

    @staticmethod
    def get_indexes(table):
        return []

    @staticmethod
    def get_unique_constraints(table):
        return []

    @staticmethod
    def get_foreign_keys(table):
        return []


def _inspect(bind):
    return _OfflineInspector() if op.get_context().as_sql else sa.inspect(bind)


def upgrade() -> None:
    bind = op.get_bind()
    if "area1_mutation_log" not in set(_inspect(bind).get_table_names()):
        op.create_table(
            "area1_mutation_log",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("db_role", sa.Text(), nullable=False),
            sa.Column("application", sa.Text()),
            sa.Column("table_name", sa.Text(), nullable=False),
            sa.Column("operation", sa.Text(), nullable=False),
            sa.Column("row_id", postgresql.UUID(as_uuid=True)),
            sa.Column("before_image", postgresql.JSONB(), nullable=False),
            sa.Column("after_image", postgresql.JSONB()),
            # Set by the break-glass operator via SET LOCAL area1.justification.
            sa.Column("justification", sa.Text()),
        )
        op.create_index("idx_area1_mutation_occurred", "area1_mutation_log",
                        ["occurred_at"])
        op.create_index("idx_area1_mutation_table", "area1_mutation_log",
                        ["table_name", "operation"])
        op.create_index("idx_area1_mutation_row", "area1_mutation_log", ["row_id"])

    op.execute(LOG_FUNCTION)
    op.execute("DROP TRIGGER IF EXISTS trg_area1_record_mutation ON rce_source_records")
    op.execute(RECORD_UPDATE_TRIGGER)
    op.execute("DROP TRIGGER IF EXISTS trg_area1_record_delete ON rce_source_records")
    op.execute(RECORD_DELETE_TRIGGER)
    op.execute("DROP TRIGGER IF EXISTS trg_area1_intake_mutation ON rce_source_intakes")
    op.execute(INTAKE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_area1_record_mutation ON rce_source_records")
    op.execute("DROP TRIGGER IF EXISTS trg_area1_record_delete ON rce_source_records")
    op.execute("DROP TRIGGER IF EXISTS trg_area1_intake_mutation ON rce_source_intakes")
    op.execute("DROP FUNCTION IF EXISTS area1_log_mutation()")
    bind = op.get_bind()
    if "area1_mutation_log" in set(_inspect(bind).get_table_names()):
        op.drop_table("area1_mutation_log")
