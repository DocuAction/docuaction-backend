"""Source-derived name columns: VARCHAR(500) -> TEXT.

Revision ID: 20260915_curated_text_columns
Revises: 20260903_delivery_grants
Create Date: 2026-09-15

WHY THIS EXISTS
---------------
On 2026-09-15 a 184-record DEV delivery (intake ec75d5f2) landed in Area 1
completely, passed the quality run, and then FAILED at CURATION:

    asyncpg.exceptions.StringDataRightTruncationError:
    value too long for type character varying(500)
    INSERT INTO rce_curated_records (...)

The offending parameter was `name`, a delivered organisation name of 580
characters. Area 1 holds that value unbounded (`rce_source_records.raw_line`
is TEXT and `parsed` is JSONB) and Area 2 is, by the P6 contract, a 1:1
projection of Area 1. `curation.build_curated_row` copies the field with an
outer-whitespace strip only; no transformation shortens it, and none may:
silently truncating a delivered value would make the curated representation
disagree with the immutable source it claims to mirror.

There is no contractual maximum length for an organisation name (the locked
41-field map documents none; the official profile observed 4-99), so the
500 was an arbitrary cap that turned one over-long delivered value into the
loss of the whole curated stage (0 of 184 rows curated). TEXT removes the cap
without inventing a different arbitrary number.

WHICH COLUMNS, AND WHY EXACTLY THESE
------------------------------------
The same delivered value flows on unchanged from Area 2 into the canonical
registry, so lifting the cap on the curated column alone would move the
failure one stage later, to PROMOTION:

    rce_curated_records.name        curation   <- source `name`
    tefca_reg_entities.name         promotion  <- curated `name`
    tefca_reg_entities.display_name promotion  <- curated `name`
    tefca_entity_contacts.company   promotion  <- source `contact_company`
    tefca_entity_contacts.name      promotion  <- source `contact_name`

The two contact columns are the same class of value - delivered free text
projected 1:1 - written by the same promotion pass under the same VARCHAR(500)
cap, and the same padding artefact that produced the 580-character name is
present in the delivery's contact_name column. Every other length-constrained
column on these tables holds a code, an identifier or a bounded address
component whose observed maximum is a small fraction of its limit; they are
deliberately left alone.

SAFETY
------
VARCHAR(n) -> TEXT is a catalogue-only change in PostgreSQL: no table rewrite,
no row is touched, every existing value is preserved exactly, and it is
compatible with every row already present. Nothing is deleted. Area 1 is not
referenced.

The downgrade is refused, rather than run, if any row now holds a value longer
than 500 characters, because reinstating the cap would have to truncate or
delete delivered data to succeed - exactly what this chain exists to prevent.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260915_curated_text_columns"
down_revision = "20260903_delivery_grants"
branch_labels = None
depends_on = None

#: (table, column, nullable) - nullable is passed through unchanged; only the
#: type changes.
COLUMNS = (
    ("rce_curated_records", "name", True),
    ("tefca_reg_entities", "name", False),
    ("tefca_reg_entities", "display_name", True),
    ("tefca_entity_contacts", "company", True),
    ("tefca_entity_contacts", "name", True),
)

OLD_LIMIT = 500


class DowngradeWouldTruncateError(RuntimeError):
    """Raised instead of shortening or deleting a delivered value."""


def _offline() -> bool:
    return op.get_context().as_sql


def upgrade() -> None:
    for table, column, nullable in COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.String(OLD_LIMIT),
            type_=sa.Text(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    if not _offline():
        bind = op.get_bind()
        for table, column, _nullable in COLUMNS:
            over = bind.execute(sa.text(
                f'select count(*) from "{table}" '
                f'where length("{column}") > :n'), {"n": OLD_LIMIT}).scalar()
            if over:
                raise DowngradeWouldTruncateError(
                    f"{table}.{column} holds {over} value(s) longer than "
                    f"{OLD_LIMIT} characters. Reinstating VARCHAR({OLD_LIMIT}) "
                    f"would require truncating or deleting delivered data, "
                    f"which this migration refuses to do. Resolve those rows "
                    f"through the governed process first.")
    for table, column, nullable in COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.Text(),
            type_=sa.String(OLD_LIMIT),
            existing_nullable=nullable,
        )
