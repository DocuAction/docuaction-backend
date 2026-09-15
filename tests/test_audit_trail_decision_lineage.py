"""The Audit & Decision History trail carries the ARC decision lineage.

QA AUD-001 (2026-09-14): filtering the trail by a case showed only the platform
log's authentication rows for that case — never the analyst determination or
the QA approve / return / escalate events, which the workflow records in the
REGISTRY audit log (tefca_reg_audit_log). `/api/tefca/audit-trail` now reads
both stores; these tests pin the registry half and its vocabulary.
"""
import uuid

import pytest

from app.services.audit import classify_event_type
from app.Tefca.routes import _registry_audit_rows


@pytest.mark.parametrize("action", [
    "analyst_determination_recorded", "qa_approve", "qa_return", "qa_escalate",
    "determination_superseded", "review_case_claimed", "review_case_released",
])
def test_decision_actions_are_filed_under_review(action):
    assert classify_event_type(action) == "review"


@pytest.mark.asyncio
async def test_registry_decision_rows_are_returned_in_the_trail_shape(db_required):
    from app.core.database import async_session_maker
    from app.tefca_registry import audit as reg_audit

    review_id = f"REV-TEST-{uuid.uuid4().hex[:8].upper()}"
    actor_id = uuid.uuid4()
    async with async_session_maker() as db:
        reg_audit.record(db, "analyst_determination_recorded", None,
                         actor_id=actor_id, actor_email="analyst@synthetic.test",
                         ip_address="127.0.0.1",
                         metadata={"review_id": review_id, "determination": "CONFIRM"})
        reg_audit.record(db, "qa_return", None,
                         actor_id=uuid.uuid4(), actor_email="qa@synthetic.test",
                         ip_address="127.0.0.1",
                         metadata={"review_id": review_id, "qa_action": "RETURN"})
        await db.commit()

        rows, total = await _registry_audit_rows(
            db, event_type=None, action=None, correlation_id=None,
            search=review_id, window=50)
        assert total == 2 and len(rows) == 2
        by_action = {r["action"]: r for r in rows}
        assert set(by_action) == {"analyst_determination_recorded", "qa_return"}
        for r in rows:
            assert r["event_type"] == "review"
            assert r["resource_type"] == "review" and r["resource_id"] == review_id
            assert r["source"] == "registry" and r["timestamp"]
        assert by_action["qa_return"]["user"] == "qa@synthetic.test"
        assert by_action["qa_return"]["details"].get("qa_action") == "RETURN"

        # The event-type filter selects them, and a correlation-id query
        # (platform-only concept) never returns registry rows.
        rows, total = await _registry_audit_rows(
            db, event_type="review", action=None, correlation_id=None,
            search=review_id, window=50)
        assert total == 2
        rows, total = await _registry_audit_rows(
            db, event_type=None, action=None, correlation_id="abc", search=review_id, window=50)
        assert rows == [] and total == 0
