"""evidence vocabulary version — additive nullable stamp on dimension evidence

Revision ID: 20260823_vocab_version
Revises:      20260822_rce_pipeline
Create Date:  2026-08-23

WHAT THIS ADDS, AND WHAT IT DELIBERATELY DOES NOT
─────────────────────────────────────────────────
ONE nullable column. No default, no backfill, no rewrite.

    tefca_dimension_evidence.vocabulary_version VARCHAR(10) NULL

NULL IS MEANINGFUL AND MUST STAY NULL
The 1,984 existing rows were written before the evidence vocabulary was
versioned. A NULL records exactly that, and it is derived to "LEGACY" at read
time by `app.core.evidence_vocabulary.vocabulary_of`. Backfilling it — even with
the string "LEGACY" — would destroy the distinction between "this row predates
versioning" and "somebody decided retrospectively what vocabulary it used".

That is the same reasoning already applied twice in this schema:
`tefca_reg_entities.confidence_score` is nullable with no default because NULL
means "never verified", and the ambiguous historical `pecos` source key was left
unrenamed because an audit trail edited to look correct cannot be relied on.

A server_default would have PostgreSQL rewrite every row. There is none, so this
is a catalogue-only change: no table rewrite, no lock of consequence, and the
digest of the existing rows is unchanged by construction.

REVERSIBLE
`downgrade()` drops the index and the column. No data is lost, because nothing
except the new vocabulary code reads it.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260823_vocab_version"
down_revision = "20260822_rce_pipeline"
branch_labels = None
depends_on = None

TABLE = "tefca_dimension_evidence"
COLUMN = "vocabulary_version"
INDEX = "idx_dim_evidence_vocab_version"



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
    existing = {c["name"] for c in _inspect(bind).get_columns(TABLE)}
    if COLUMN not in existing:
        # Nullable, no server_default — see the module docstring.
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(10), nullable=True))
    indexes = {i["name"] for i in _inspect(bind).get_indexes(TABLE)}
    if INDEX not in indexes:
        op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {i["name"] for i in _inspect(bind).get_indexes(TABLE)}
    if INDEX in indexes:
        op.drop_index(INDEX, table_name=TABLE)
    existing = {c["name"] for c in _inspect(bind).get_columns(TABLE)}
    if COLUMN in existing:
        op.drop_column(TABLE, COLUMN)
