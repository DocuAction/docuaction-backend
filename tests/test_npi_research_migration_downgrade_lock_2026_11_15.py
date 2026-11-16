"""20261115_rce_npi_research downgrade: the emptiness decision is made under an ACCESS EXCLUSIVE lock held
through the drop, and stored research is never silently dropped (mirror of the 20261112 proof).
Disposable database; everything is rolled back. Synthetic data only."""
from __future__ import annotations

import importlib.util
import os
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from rce_traceability_support import rolled_back_db  # noqa: F401

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20261115_rce_npi_research.py"
TABLE = "rce_supplemental_npi_research"
pytestmark = pytest.mark.regression


def _module():
    spec = importlib.util.spec_from_file_location("mig_20261115", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_downgrade_locks_the_table_exclusively_before_it_checks_emptiness_and_holds_it_through_the_drop(db_required):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from app.core.database import _normalize_url

    mig = _module()
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            if (await conn.execute(sa.text(f"select to_regclass('public.{TABLE}')"))).scalar() is None:
                await tx.rollback()
                pytest.skip(f"{TABLE} is not applied in this database")
            if (await conn.execute(sa.text(f"select exists (select 1 from public.{TABLE})"))).scalar():
                await tx.rollback()
                # Retained research is never deleted to prepare a test; the populated-table refusal has its own test.
                pytest.skip(f"{TABLE} holds rows; the empty-table ordering check needs an empty table")
            statements = []
            sa.event.listen(conn.sync_connection, "before_cursor_execute",
                            lambda c, cur, stmt, params, ctx, many: statements.append(" ".join(stmt.split())))

            def run(sync_conn):
                with Operations.context(MigrationContext.configure(sync_conn)):
                    mig.downgrade()

            try:
                await conn.run_sync(run)
                table_stmts = [s for s in statements if TABLE in s]
                lock_at = next(i for i, s in enumerate(table_stmts) if s.upper().startswith("LOCK TABLE") and "ACCESS EXCLUSIVE" in s.upper())
                check_at = next(i for i, s in enumerate(table_stmts) if "SELECT EXISTS" in s.upper())
                drop_at = next(i for i, s in enumerate(table_stmts) if s.upper().startswith("DROP TABLE"))
                assert lock_at < check_at < drop_at, table_stmts
                assert tx.is_active                      # one transaction: the lock is held through the drop
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()


async def test_downgrade_refuses_a_populated_table_and_leaves_the_research_in_place(rolled_back_db):
    """Rehearsal with the REAL writer: one stored research entry makes the downgrade refuse, under the same lock."""
    from sqlalchemy import select, text
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import supplemental_research as sr
    from rce_traceability_support import make_rows, seed_intake

    db = rolled_back_db
    if (await db.execute(text(f"select to_regclass('public.{TABLE}')"))).scalar() is None:
        pytest.skip(f"{TABLE} is not applied in this database")
    rows = make_rows(1, arc="9.99.777.91")
    for r in rows:
        r["NPI"] = ""
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    rec = (await db.execute(select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().one()
    await sr.record_entry(
        db, intake_id=intake_id, source_record_id=rec.id,
        expected={"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid},
        entry={"action": "RECORD", "researched_npi": "1234567893",
               "nppes_reference": "NPPES NPI Registry lookup (synthetic reference)", "nppes_check_date": "2026-11-14",
               "match_rationale": "Synthetic downgrade rehearsal entry; no real data"},
        actor_email="a@synthetic-test.docuaction.invalid", today=date(2026, 11, 15), commit=False)
    mig = _module()

    def run(sync_conn):
        with Operations.context(MigrationContext.configure(sync_conn)):
            mig.downgrade()

    conn = await db.connection()
    with pytest.raises(Exception) as exc:
        await conn.run_sync(run)
    assert "holds stored research" in str(exc.value)
