"""CodeRabbit on #233 (5e857216) for review_cycle.py: the cleanup rollback must not discard a caller's outer transaction.

A session bound to a connection that already holds an open transaction, WITHOUT join_transaction_mode="create_savepoint",
runs in SQLAlchemy's "rollback_only" mode: Session.commit() does not commit the outer transaction and Session.rollback()
rolls it back. The `finally` in `_create_review_cycle` rolled back on EVERY exit, so a successful cycle discarded the
caller's uncommitted work and the cycle's own rows while still returning a success result.

The heavy collaborators (reconciliation, plan drawing, member verification) are replaced by stubs: this test is about the
transaction boundary only. Synthetic data; nothing is committed (the outer transaction is rolled back at the end).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from rce_traceability_support import make_rows, seed_intake  # noqa: F401

pytestmark = pytest.mark.regression


@pytest.fixture
async def outer_bound_session(db_required):
    from app.core.database import _normalize_url
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    connection = await engine.connect()
    outer = await connection.begin()
    # deliberately NOT join_transaction_mode="create_savepoint": the default is "rollback_only" for this binding
    session = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        yield session, connection
    finally:
        await session.close()
        await outer.rollback()
        await connection.close()
        await engine.dispose()


def _stub_collaborators(monkeypatch, sample_id):
    import app.tefca_registry.processing_controls as pc
    import app.tefca_registry.qhin_sampling as qs
    import app.tefca_registry.rce.reconciliation as rec

    async def _enter(_db):
        return None

    async def _reconcile(_db, _intake):
        return {"passed": True, "checks": []}

    async def _finalize(_db, intake_id, **_kw):
        return {"sample_id": str(sample_id), "already_finalized": True, "sample_size": 0, "population_size": 0,
                "membership_count": 0}

    async def _no_members(_db, _sample):
        return []

    monkeypatch.setattr(pc, "enter_processing", _enter)
    monkeypatch.setattr(rec, "reconcile_delivery", _reconcile)
    monkeypatch.setattr(qs, "finalize_plan", _finalize)
    monkeypatch.setattr(rc, "_unlinked_members", _no_members)


async def test_a_successful_cycle_does_not_discard_the_callers_outer_transaction(outer_bound_session, monkeypatch):
    db, connection = outer_bound_session
    intake_id, _ = await seed_intake(db, make_rows(1, arc="9.99.783.01"), with_job=False)
    sample = reg.ReviewSample(id=uuid.uuid4(), sample_name="synthetic outer-transaction sample", review_type="quarterly",
                              population_size=1, sample_size=1, confidence_level=0.95, margin_of_error=0.05,
                              proportion=0.5)
    sample_id = sample.id                                       # read before any rollback can expire the instance
    db.add(sample)
    await db.flush()
    # A repeat call for a sample whose cycle already exists: the last thing the function does is READ (no commit
    # follows), so the unconditional cleanup rollback is what ends the transaction - and, in rollback_only mode, the
    # caller's outer one.
    db.add(reg.ReviewCycle(id=uuid.uuid4(), cycle_type="quarterly", sample_id=sample_id, status="open"))
    await db.flush()                                           # the caller's uncommitted work, in the OUTER transaction
    _stub_collaborators(monkeypatch, sample_id)

    result = await rc._create_review_cycle(db, intake_id, user=SimpleNamespace(id=None, email="cycle@synthetic-test.docuaction.invalid"))
    assert result["reconciliation_passed"] is True

    seen = (await connection.execute(text("select count(*) from review_samples where id = :s"), {"s": sample_id})).scalar()
    cycles = (await connection.execute(text("select count(*) from review_cycles where sample_id = :s"), {"s": sample_id})).scalar()
    assert seen == 1, "the caller's uncommitted work was rolled back by the cleanup"
    assert cycles == 1, "the cycle's own row was rolled back although the call reported success"
    # the advisory lock really was released (the unlock still ran)
    held = (await connection.execute(text(
        "select count(*) from pg_locks where locktype = 'advisory' and pid = pg_backend_pid()"))).scalar()
    assert held == 0
