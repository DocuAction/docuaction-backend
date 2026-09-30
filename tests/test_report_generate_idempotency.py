"""`POST /api/reports/generate` with an idempotency key: same authorized
scope + same key + same principal → the SAME report, exactly one report row
and exactly one `report_generated` audit event; a different principal never
gets the replay.

Release gate 2026-09-30: the Contract Reports GeneratePanel sent no key and a
raw replay of one action produced a second synthetic draft (DA-ARC-2026-039).
The server side of the rule already existed (QA-031, `_replay_for_key`); this
pins it at the ROUTE — the surface both generation panels call — including the
audit-event count the earlier unit test did not assert.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    SYN, artifact_root, rolled_back_db, seed_delivery)


async def _generated_events(db, key: str) -> int:
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import ACTION_GENERATED

    return int((await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == ACTION_GENERATED,
               AuditLog.details["idempotency_key"].as_string() == key))).scalar() or 0)


async def _reports_for_job(db, job_id) -> int:
    """Distinct reports linked to the job (one link per registered artifact)."""
    from app.reports.data.delivery_report_links import links_for_job

    return len({link["report_id"] for link in await links_for_job(db, job_id)})


# The route audits under the caller's user id, which is a foreign key to
# `users`; a synthetic principal without a users row uses id=None (the audit
# row is then user-less, exactly as the SYSTEM generation path records it).
def _principal():
    return SimpleNamespace(id=None, email="reviewer@synthetic.invalid", role="reviewer")


@pytest.mark.asyncio
async def test_same_scope_and_key_replays_one_report_with_one_audit_event(rolled_back_db, artifact_root):
    from app.reports import routes

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} idempotent")
    user = _principal()
    key = f"rep-{uuid.uuid4()}"
    request = routes.GenerateReportRequest(
        report_type="delivery_processing", format="json",
        parameters={"job_id": str(a["job_id"])}, idempotency_key=key)

    first = await routes.generate(request, db=db, user=user)
    assert first["report_id"] and not first.get("replayed")
    assert await _generated_events(db, key) == 1

    second = await routes.generate(request, db=db, user=user)
    assert second["replayed"] is True
    assert second["idempotency_key"] == key
    assert second["report_id"] == first["report_id"]
    assert second["stored_id"] == first["stored_id"]

    # Exactly one report and one generation event for this action.
    assert await _generated_events(db, key) == 1
    assert await _reports_for_job(db, a["job_id"]) == 1

    # The key is per principal: another user with the same key gets no replay.
    assert await routes._replay_for_key(db, key, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_a_new_key_is_a_new_generation(rolled_back_db, artifact_root):
    from app.reports import routes

    db = rolled_back_db
    a = await seed_delivery(db, label=f"{SYN} new-key")
    user = _principal()
    mk = lambda key: routes.GenerateReportRequest(  # noqa: E731
        report_type="delivery_processing", format="json",
        parameters={"job_id": str(a["job_id"])}, idempotency_key=key)
    first = await routes.generate(mk(f"rep-{uuid.uuid4()}"), db=db, user=user)
    second = await routes.generate(mk(f"rep-{uuid.uuid4()}"), db=db, user=user)
    assert not second.get("replayed") and second["report_id"] != first["report_id"]
    assert await _reports_for_job(db, a["job_id"]) == 2
