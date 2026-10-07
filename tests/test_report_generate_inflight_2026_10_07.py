"""D-07 follow-up: a retry that arrives while the first generation is still running must not create a second report.

Before: the replay lookup read the `report_generated` audit row written when a generation FINISHES, so a retry
inside the in-flight window found nothing and generated again. Now generation is serialised per (principal, key)
by a Postgres advisory lock on its own connection.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from test_report_generate_idempotency import _principal, _reports_for_job  # noqa: F401
from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    SYN, artifact_root, rolled_back_db, seed_delivery)


@pytest.mark.asyncio
async def test_a_second_holder_of_the_same_key_waits_then_gets_409_not_a_generation(monkeypatch):
    from app.reports import routes

    monkeypatch.setattr(routes, "GENERATION_LOCK_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(routes, "GENERATION_LOCK_POLL_SECONDS", 0.1)
    key, uid = f"rep-{uuid.uuid4()}", uuid.uuid4()
    async with routes._generation_key_lock(key, uid):
        with pytest.raises(HTTPException) as exc:
            async with routes._generation_key_lock(key, uid):
                raise AssertionError("must not enter while the first holder is running")
        assert exc.value.status_code == 409 and exc.value.detail["code"] == "REPORT_GENERATION_IN_PROGRESS"
        # a different key, and a different principal with the same key, are not blocked
        async with routes._generation_key_lock(f"rep-{uuid.uuid4()}", uid):
            pass
        async with routes._generation_key_lock(key, uuid.uuid4()):
            pass
    # released: the same key can be taken again
    async with routes._generation_key_lock(key, uid):
        pass


@pytest.mark.asyncio
async def test_a_waiting_retry_proceeds_once_the_first_finishes(monkeypatch):
    from app.reports import routes

    monkeypatch.setattr(routes, "GENERATION_LOCK_WAIT_SECONDS", 5.0)
    monkeypatch.setattr(routes, "GENERATION_LOCK_POLL_SECONDS", 0.05)
    key, uid = f"rep-{uuid.uuid4()}", uuid.uuid4()
    order = []

    async def first():
        async with routes._generation_key_lock(key, uid):
            order.append("first-in")
            await asyncio.sleep(0.5)
            order.append("first-out")

    async def retry():
        await asyncio.sleep(0.1)
        async with routes._generation_key_lock(key, uid):
            order.append("retry-in")

    await asyncio.gather(first(), retry())
    assert order == ["first-in", "first-out", "retry-in"]


@pytest.mark.asyncio
async def test_no_key_takes_no_lock():
    from app.reports import routes

    async with routes._generation_key_lock(None, None):
        async with routes._generation_key_lock(None, None):
            pass


@pytest.mark.asyncio
async def test_route_answers_409_while_the_same_action_runs_and_replays_after(rolled_back_db, artifact_root, monkeypatch):
    from app.reports import routes

    monkeypatch.setattr(routes, "GENERATION_LOCK_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(routes, "GENERATION_LOCK_POLL_SECONDS", 0.05)
    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} inflight")
    user = _principal()
    key = f"rep-{uuid.uuid4()}"
    request = routes.GenerateReportRequest(report_type="delivery_processing", format="json",
                                           parameters={"job_id": str(a["job_id"])}, idempotency_key=key)
    async with routes._generation_key_lock(key, user.id):       # the first call is "still running"
        with pytest.raises(HTTPException) as exc:
            await routes.generate(request, db=db, user=user)
        assert exc.value.status_code == 409
        assert await _reports_for_job(db, a["job_id"]) == 0      # the retry started nothing
    first = await routes.generate(request, db=db, user=user)     # first finishes / retry now generates once
    again = await routes.generate(request, db=db, user=user)
    assert again["replayed"] is True and again["report_id"] == first["report_id"]
    assert await _reports_for_job(db, a["job_id"]) == 1
