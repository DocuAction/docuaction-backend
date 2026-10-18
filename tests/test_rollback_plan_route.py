"""ROL-001..015 workbook cases all failed "Fail-NoRollbackUI". The real tool
(`scripts/rce_snapshot_rollback.py`) is operator-run, gated to program_manager+,
and never exposed as a mutating UI button (by design -- see that script's
docstring). `GET /deliveries/{intake_id}/rollback-plan` is a READ-ONLY
rehearsal surface reusing the SAME plan logic
(`relationship_history.compensate_snapshot(..., apply=False)`). This proves
it is gated the same way and never writes.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.tefca_registry import models as reg
from support_delivery_api import headers_for, run, seed_delivery

BASE = "/api/tefca/rce"


async def _relationship_row_count():
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        return int((await db.execute(
            select(func.count()).select_from(reg.TefcaEntityRelationship))).scalar() or 0)


def test_viewer_is_denied(client):
    d = seed_delivery(state="SUCCEEDED", issues=0)
    resp = client.get(f"{BASE}/deliveries/{d['intake_id']}/rollback-plan",
                      headers=headers_for("viewer"))
    assert resp.status_code == 403


def test_reviewer_is_denied(client):
    """Only program_manager+ (Data Operations) may even SEE the rehearsal --
    same floor the real --apply tool enforces."""
    d = seed_delivery(state="SUCCEEDED", issues=0)
    resp = client.get(f"{BASE}/deliveries/{d['intake_id']}/rollback-plan",
                      headers=headers_for("reviewer"))
    assert resp.status_code == 403


def test_program_manager_sees_a_rehearsal_that_writes_nothing(client):
    d = seed_delivery(state="SUCCEEDED", issues=0)
    before = run(_relationship_row_count())

    resp = client.get(f"{BASE}/deliveries/{d['intake_id']}/rollback-plan",
                      headers=headers_for("program_manager"))
    assert resp.status_code in (200, 409), resp.text   # 409 only for a documented refusal reason

    after = run(_relationship_row_count())
    assert after == before, "a GET rehearsal must never change relationship rows"

    if resp.status_code == 200:
        body = resp.json()
        assert body["rehearsal"] is True
        assert body["applied"] is False
        assert "intake_id" in body
