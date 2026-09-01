"""Ownership of a review case: claim, release, assign, reassign.

THE INVARIANT
─────────────
    ONE CASE -> AT MOST ONE ACTIVE OWNER.

    Enforced by the database, in one statement:

        UPDATE review_records
           SET assigned_to_user_id = :me, assigned_at = now()
         WHERE review_id = :id AND assigned_to_user_id IS NULL
        RETURNING review_id

    Of two concurrent claimers exactly one matches `assigned_to_user_id IS
    NULL`; the other updates zero rows and is told so. A read-then-write would
    let both read NULL and both write — that is the race this avoids, and it is
    avoided by PostgreSQL's row lock rather than by a process-local lock, a
    retry, a sleep or a disabled button.

WHY THERE IS NO `case_status` COLUMN
────────────────────────────────────
    Ownership is `assigned_to_user_id`. Everything else — submitted, returned,
    escalated, approved — is already determined by `review_decision_events` and
    read through `qa_gate`. `case_assignment.case_state()` derives it. A stored
    status would be a second answer to the same question, and the day it drifted
    from the events nothing could say which was right.

TRUE CONCURRENCY
    `test_two_analysts_claiming_at_once_produce_exactly_one_owner` needs two
    independent committing transactions, so it runs in a throwaway schema in a
    separate database rather than inside the rolled-back session the other tests
    use. Government data is never opened by it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import Base, _normalize_url
from app.tefca_registry import case_assignment as assignment
from app.tefca_registry import models as reg

SYN = "SYNTHETIC-ASSIGN"


def principal(email, role):
    return SimpleNamespace(id=uuid.uuid4(), email=email, role=role)


ANALYST_A = principal("analyst.a@synthetic.test", "reviewer")
ANALYST_B = principal("analyst.b@synthetic.test", "reviewer")
SUPERVISOR = principal("supervisor@synthetic.test", "senior_analyst")
QA = principal("qa@synthetic.test", "qalead")
OUTSIDER = principal("viewer@synthetic.test", "viewer")


@pytest.fixture
async def rolled_back_db(db_required):
    engine = create_async_engine(
        _normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    connection = await engine.connect()
    outer = await connection.begin()
    session = AsyncSession(bind=connection,
                           join_transaction_mode="create_savepoint",
                           expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()
        await connection.close()
        await engine.dispose()


async def _make_case(db, *, with_entity: bool = True, n: int = 1):
    """A minimal synthetic review case. No Government data involved."""
    ids = []
    for i in range(n):
        entity_id = None
        if with_entity:
            entity_id = uuid.uuid4()
            db.add(reg.TefcaRegEntity(
                id=entity_id, name=f"{SYN} ORG {i}", display_name=f"{SYN} ORG {i}",
                entity_level="participant", entity_type="provider",
                operational_status="active", verification_status="not_verified",
                current_version=1, is_active=True))
            await db.flush()
        review_id = f"REV-9000-{uuid.uuid4().int % 1000000:06d}"
        db.add(reg.ReviewRecord(
            id=uuid.uuid4(), review_id=review_id,
            entity_id=entity_id, source_record_id=uuid.uuid4(),
            verification_results={"queue_source": "TEST", "priority": 50,
                                  "case_classification": "DQ",
                                  "severity": "MEDIUM"}))
        ids.append(review_id)
    await db.commit()
    return ids if n > 1 else ids[0]


# ── TEST 13 / 14 — availability and claim ────────────────────────────────────

async def test_an_available_case_can_be_claimed(rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)

    assert await assignment.case_state(db, review_id) == assignment.AVAILABLE
    available = await assignment.available_cases(db, queue_source="TEST")
    assert review_id in [c["review_id"] for c in available]

    result = await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    assert result["assigned_to_user_id"] == str(ANALYST_A.id)
    assert await assignment.case_state(db, review_id) == assignment.CLAIMED
    # It leaves the available queue and enters the claimer's.
    assert review_id not in [
        c["review_id"] for c in await assignment.available_cases(
            db, queue_source="TEST")]
    assert review_id in [
        c["review_id"] for c in await assignment.my_work(
            db, user=ANALYST_A, queue_source="TEST")]
    assert review_id not in [
        c["review_id"] for c in await assignment.my_work(
            db, user=ANALYST_B, queue_source="TEST")]


# ── TEST 16 — the second claimer gets a controlled refusal ───────────────────

async def test_claiming_an_already_held_case_is_refused(rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)
    await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    with pytest.raises(assignment.AssignmentRefused,
                       match="already held by another reviewer"):
        await assignment.claim(db, review_id, user=ANALYST_B)
    await db.rollback()

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.assigned_to_user_id == ANALYST_A.id


async def test_an_unauthorized_role_cannot_claim(rolled_back_db):
    """TEST 11: below the analyst level, a case cannot be held at all."""
    db = rolled_back_db
    review_id = await _make_case(db)

    with pytest.raises(assignment.AssignmentRefused, match="requires at least"):
        await assignment.claim(db, review_id, user=OUTSIDER)
    await db.rollback()

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.assigned_to_user_id is None


# ── TEST 17 / 18 — release ───────────────────────────────────────────────────

async def test_the_holder_can_release_and_the_case_returns_to_available(
        rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)
    await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    result = await assignment.release(db, review_id, user=ANALYST_A,
                                      reason="synthetic release")
    await db.commit()

    assert result["assigned_to_user_id"] is None
    assert result["previous_owner"] == str(ANALYST_A.id)
    assert await assignment.case_state(db, review_id) == assignment.AVAILABLE
    assert review_id in [
        c["review_id"] for c in await assignment.available_cases(
            db, queue_source="TEST")]
    # And it can be claimed again, by someone else.
    await assignment.claim(db, review_id, user=ANALYST_B)
    await db.commit()
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.assigned_to_user_id == ANALYST_B.id


async def test_another_analyst_cannot_release_someone_elses_case(rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)
    await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    with pytest.raises(assignment.AssignmentRefused,
                       match="held by another reviewer"):
        await assignment.release(db, review_id, user=ANALYST_B)
    await db.rollback()

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.assigned_to_user_id == ANALYST_A.id


async def test_a_supervisor_can_release_on_behalf_of_the_holder(rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)
    await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    await assignment.release(db, review_id, user=SUPERVISOR,
                             reason="synthetic: analyst unavailable")
    await db.commit()
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.assigned_to_user_id is None


# ── TEST 19 / 20 — supervisor assignment and reassignment ────────────────────

async def test_a_supervisor_can_assign_and_reassign_with_an_audit_trail(
        rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)

    first = await assignment.assign(db, review_id, user=SUPERVISOR,
                                    to_user_id=ANALYST_A.id,
                                    reason="synthetic initial assignment")
    await db.commit()
    assert first["assigned_to_user_id"] == str(ANALYST_A.id)
    assert first["previous_owner"] is None

    # Taking a case off a live holder is a different act from assigning an
    # unheld one, and needs a stated reason. Without it, a handover is
    # indistinguishable in the trail from a case that was never claimed.
    with pytest.raises(assignment.AssignmentRefused, match="override_reason"):
        await assignment.assign(db, review_id, user=SUPERVISOR,
                                to_user_id=ANALYST_B.id,
                                reason="synthetic handover")
    await db.rollback()

    second = await assignment.assign(
        db, review_id, user=SUPERVISOR, to_user_id=ANALYST_B.id,
        reason="synthetic handover",
        override_reason="Analyst A is on leave; synthetic handover for cover.")
    await db.commit()
    assert second["assigned_to_user_id"] == str(ANALYST_B.id)
    assert second["previous_owner"] == str(ANALYST_A.id)

    actions = [a.action for a in (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action.in_(
            ("review_case_assigned", "review_case_reassigned"))))).scalars().all()]
    assert "review_case_assigned" in actions
    assert "review_case_reassigned" in actions


async def test_an_analyst_cannot_assign_work_to_someone_else(rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)

    with pytest.raises(assignment.AssignmentRefused, match="requires at least"):
        await assignment.assign(db, review_id, user=ANALYST_A,
                                to_user_id=ANALYST_B.id)
    await db.rollback()


# ── TEST 20 — assignment history ─────────────────────────────────────────────

async def test_ownership_changes_are_audited(rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)

    await assignment.claim(db, review_id, user=ANALYST_A)
    await assignment.release(db, review_id, user=ANALYST_A, reason="synthetic")
    await assignment.claim(db, review_id, user=ANALYST_B)
    await db.commit()

    rows = (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action.in_(
            ("review_case_claimed", "review_case_released"))))).scalars().all()
    mine = [r for r in rows
            if (r.metadata_ or {}).get("review_id") == review_id]
    assert len(mine) == 3
    assert sum(1 for r in mine if r.action == "review_case_claimed") == 2
    assert sum(1 for r in mine if r.action == "review_case_released") == 1
    for row in mine:
        assert row.actor_email and row.actor_id
        assert row.created_at is not None


# ── ownership gates the work, not the role ───────────────────────────────────

async def test_only_the_holder_may_act_on_a_claimed_case(rolled_back_db):
    """TEST 22: two analysts share a role; only one holds the case."""
    db = rolled_back_db
    review_id = await _make_case(db)
    await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()

    assignment.require_owner(record, ANALYST_A)          # holder: allowed
    with pytest.raises(assignment.AssignmentRefused,
                       match="held by another reviewer"):
        assignment.require_owner(record, ANALYST_B)


async def test_an_unheld_case_cannot_be_worked(rolled_back_db):
    db = rolled_back_db
    review_id = await _make_case(db)
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    with pytest.raises(assignment.AssignmentRefused, match="claim it first"):
        assignment.require_owner(record, ANALYST_A)


# ── pre-promotion case ownership ─────────────────────────────────────────────

async def test_a_pre_promotion_case_can_be_claimed_and_listed(rolled_back_db):
    """A case with no entity must behave exactly like any other in the queue."""
    db = rolled_back_db
    review_id = await _make_case(db, with_entity=False)

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.entity_id is None and record.source_record_id is not None

    listed = [c for c in await assignment.available_cases(db, queue_source="TEST")
              if c["review_id"] == review_id]
    assert listed, "a pre-promotion case must appear in the available queue"
    assert listed[0]["entity_id"] is None, "must be null, not the string 'None'"
    assert listed[0]["source_record_id"] is not None

    await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()
    mine = [c for c in await assignment.my_work(db, user=ANALYST_A,
                                                queue_source="TEST")
            if c["review_id"] == review_id]
    assert mine and mine[0]["entity_id"] is None


# ── supervisor workload ──────────────────────────────────────────────────────

async def test_workload_by_analyst_is_available(rolled_back_db):
    db = rolled_back_db
    ids = await _make_case(db, n=3)
    await assignment.claim(db, ids[0], user=ANALYST_A)
    await assignment.claim(db, ids[1], user=ANALYST_A)
    await assignment.claim(db, ids[2], user=ANALYST_B)
    await db.commit()

    summary = await assignment.workload_by_analyst(db, queue_source="TEST")
    assert summary["by_analyst"][str(ANALYST_A.id)] == 2
    assert summary["by_analyst"][str(ANALYST_B.id)] == 1
    assert summary["by_state"][assignment.CLAIMED] == 3
    assert summary["operational_age_days"]["oldest"] is not None
    assert "not a contractual SLA" in summary["note"]


# ── TEST 15 — TRUE CONCURRENCY, separate committing transactions ─────────────

SCHEMA = "assign_gate_tmp"


@pytest.fixture
async def sandbox_engine(db_required):
    """A throwaway schema in the SEPARATE `docuaction` database.

    Real concurrency needs two committing transactions, which the rolled-back
    session cannot provide. `docuaction-db`, which holds the Government
    delivery, is never opened here.
    """
    import urllib.parse as up

    parsed = up.urlparse(_normalize_url(os.environ["DATABASE_URL"]))
    url = up.urlunparse(parsed._replace(path="/docuaction"))
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:                                  # noqa: BLE001
        await admin.dispose()
        pytest.skip(f"no sandbox database available for a concurrency test: {exc}")

    engine = create_async_engine(
        url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": SCHEMA}})
    tables = [Base.metadata.tables[t] for t in
              ("tefca_reg_entities", "review_samples", "review_records",
               "review_decision_events", "tefca_reg_audit_log")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda s: Base.metadata.create_all(
            s, tables=tables, checkfirst=True))
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await admin.dispose()


async def test_two_analysts_claiming_at_once_produce_exactly_one_owner(
        sandbox_engine):
    """TEST 15: the race, run for real."""
    review_id = "REV-9000-CONCUR"
    async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
        db.add(reg.ReviewRecord(
            id=uuid.uuid4(), review_id=review_id,
            entity_id=None, source_record_id=uuid.uuid4(),
            verification_results={"queue_source": "TEST", "priority": 50}))
        await db.commit()

    async def claimer(user):
        async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
            try:
                result = await assignment.claim(db, review_id, user=user)
                await db.commit()
                return "WON", result["assigned_to_user_id"]
            except assignment.AssignmentRefused as exc:
                await db.rollback()
                return "REFUSED", str(exc)
            except Exception as exc:                          # noqa: BLE001
                await db.rollback()
                return f"RAISED {type(exc).__name__}", str(exc)[:120]

    results = await asyncio.gather(claimer(ANALYST_A), claimer(ANALYST_B))
    outcomes = [r[0] for r in results]
    assert outcomes.count("WON") == 1, f"expected exactly one winner: {results}"
    assert outcomes.count("REFUSED") == 1, f"expected one refusal: {results}"

    refusal = next(detail for status, detail in results if status == "REFUSED")
    # A controlled message, not a database error.
    assert "already held" in refusal
    for leak in ("psycopg", "asyncpg", "Traceback", "SELECT", "UPDATE",
                 "review_records_", "ck_review_record"):
        assert leak not in refusal, f"refusal leaked internals: {refusal}"

    async with AsyncSession(sandbox_engine) as db:
        record = (await db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
        assert record.assigned_to_user_id is not None
        owners = {str(record.assigned_to_user_id)}
        assert owners == {next(d for s, d in results if s == "WON")}
        # Exactly one successful claim event.
        claims = (await db.execute(
            select(func.count()).select_from(reg.TefcaRegAuditLog)
            .where(reg.TefcaRegAuditLog.action == "review_case_claimed"))).scalar()
        assert claims == 1


def test_synthetic_identities_only():
    for actor in (ANALYST_A, ANALYST_B, SUPERVISOR, QA, OUTSIDER):
        assert actor.email.endswith("@synthetic.test")
