"""SQLAlchemy Core definition of `rce_record_check_results` (read side).

DELIBERATELY NOT A DECLARATIVE MODEL AND NOT IN `Base.metadata`.

The table is created ONLY by the governed migration
`20261008_record_check_results`. Registering it on `Base.metadata` would let the
startup `create_all()` create it (owned by the application role, outside the
migration and its append-only grant) in any database that lacks it, whatever the
flags say, and would add a table to the production-convergence candidate set.
A Core `Table` on a private `MetaData` is invisible to both, and is all the
history reader needs: the writer is plain SQL in `quality_engine`, and the
foreign keys exist in the database from the migration.
"""

from sqlalchemy import Column, DateTime, MetaData, PrimaryKeyConstraint, SmallInteger, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID

_metadata = MetaData()

RECORD_CHECK_RESULTS = Table(
    "rce_record_check_results", _metadata,
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("source_record_id", UUID(as_uuid=True), nullable=False),
    Column("map_version", SmallInteger, nullable=False),
    Column("rule_count", SmallInteger, nullable=False),
    Column("outcomes", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("run_id", "source_record_id", name="pk_rce_record_check_results"),
)
