"""20261112_rce_record_comments downgrade: the emptiness decision must be made under an ACCESS EXCLUSIVE
lock held through the drop (CodeRabbit Major on #225). Disposable database; everything is rolled back."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from rce_traceability_support import rolled_back_db  # noqa: F401

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20261112_rce_record_comments.py"
pytestmark = pytest.mark.regression


def _engine():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL not set")
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg2://").replace("postgresql://", "postgresql+psycopg2://", 1)
    return sa.create_engine(url)


def _module():
    spec = importlib.util.spec_from_file_location("mig_20261112", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_downgrade_locks_the_table_exclusively_before_it_checks_emptiness_and_holds_it_through_the_drop(db_required):
    mig = _module()
    engine = _engine()
    with engine.connect() as conn:
        tx = conn.begin()
        if conn.execute(sa.text("select to_regclass('public.rce_record_comments')")).scalar() is None:
            tx.rollback()
            pytest.skip("rce_record_comments is not applied in this database")
        statements = []
        sa.event.listen(conn, "before_cursor_execute",
                        lambda c, cur, stmt, params, ctx, many: statements.append(" ".join(stmt.split())))
        try:
            with Operations.context(MigrationContext.configure(conn)):
                mig.downgrade()
            table_stmts = [s for s in statements if "rce_record_comments" in s]
            lock_at = next(i for i, s in enumerate(table_stmts) if s.upper().startswith("LOCK TABLE") and "ACCESS EXCLUSIVE" in s.upper())
            check_at = next(i for i, s in enumerate(table_stmts) if "SELECT EXISTS" in s.upper())
            drop_at = next(i for i, s in enumerate(table_stmts) if s.upper().startswith("DROP TABLE"))
            assert lock_at < check_at < drop_at, table_stmts
            # held through the drop: the same transaction, no commit between the three
            assert tx.is_active
        finally:
            tx.rollback()


async def test_downgrade_refuses_a_populated_table_and_leaves_the_comment_in_place(rolled_back_db):
    """Rehearsal with the REAL writer: one stored comment makes the downgrade refuse, under the same lock."""
    import uuid
    from sqlalchemy import select, text
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import record_comments as rcm
    from rce_traceability_support import make_rows, seed_intake

    db = rolled_back_db
    if (await db.execute(text("select to_regclass('public.rce_record_comments')"))).scalar() is None:
        pytest.skip("rce_record_comments is not applied in this database")
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.90"), with_job=False)
    rec = (await db.execute(select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().one()
    await rcm.import_comments(db, intake_id=intake_id, actor="q@synthetic-test.docuaction.invalid", entries=[{
        "source_record_id": str(rec.id), "comment_kind": "QA_ORIGINAL", "comment_text": "synthetic comment",
        "expected": {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid},
        "source": {"file_name": "c.xlsx", "file_sha256": "ab" * 32, "sheet": "S", "column": "QA Comments"}}])
    mig = _module()

    def run(sync_conn):
        with Operations.context(MigrationContext.configure(sync_conn)):
            mig.downgrade()

    conn = await db.connection()
    with pytest.raises(Exception) as exc:
        await conn.run_sync(run)
    assert "holds stored comments" in str(exc.value)
