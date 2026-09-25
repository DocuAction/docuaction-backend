"""A settled finding accepts no further decision (2026-09-25 remediation).

`RESOLVED` has no outgoing transition in `curation._ALLOWED_TRANSITIONS`.
Before this change a decision on such a finding could still record an
identifier decision or a correction before the state walk refused it.
Pinned here, on the isolated database through the real route:

  * every decision in ISSUE_DECISIONS on a RESOLVED finding is a 409 carrying
    the one fixed message, and NOTHING moves: resolution, disposition history,
    identifier decision events and resolution notes are all unchanged;
  * the refused attempt is audited (the act, never the delivered data);
  * a non-terminal finding still takes a decision;
  * every exception-ledger row says whether it is terminal / can be disposed;
  * the fallback path refuses the same way, before touching the database.
"""

from __future__ import annotations

import uuid

import pytest

from app.tefca_registry.rce import curation
from app.tefca_registry.rce.delivery_routes import ISSUE_DECISIONS, _fallback_apply_disposition
from support_delivery_api import cleanup, headers_for, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"
MESSAGE = ("This finding is resolved and cannot be changed. Use the approved reopen "
           "workflow if further action is required.")


def test_the_terminal_set_is_derived_from_the_transition_table():
    assert curation.TERMINAL_RESOLUTIONS == frozenset(
        s for s, targets in curation._ALLOWED_TRANSITIONS.items() if not targets)
    assert "RESOLVED" in curation.TERMINAL_RESOLUTIONS
    assert curation.TERMINAL_FINDING_MESSAGE == MESSAGE
    assert curation.is_terminal_resolution("RESOLVED")
    assert not curation.is_terminal_resolution("OPEN")
    assert not curation.is_terminal_resolution(None)   # a fresh issue is OPEN


@pytest.fixture(scope="module")
def delivery():
    d = seed_delivery(state="SUCCEEDED", issues=3)   # issues 0 and 2 are NPI-003 findings

    async def _resolve_first():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(text(
                "UPDATE rce_issues SET resolution = 'RESOLVED', resolution_notes = :n "
                "WHERE id = CAST(:i AS uuid)"),
                {"i": d["issue_ids"][0], "n": "settled before the test"})
            await db.commit()
    run(_resolve_first())
    yield d
    cleanup()


async def _snapshot(issue_id: str):
    """Everything a decision could touch, read fresh."""
    from sqlalchemy import func, select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import dispositions as disp
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import traceability_models as tm

    async with async_session_maker() as db:
        issue = await db.get(m.RceIssue, uuid.UUID(issue_id))
        history = await disp.history_for_record(db, issue.source_record_id)
        events = (await db.execute(
            select(func.count()).select_from(tm.TefcaIdentifierDecisionEvent)
            .where(tm.TefcaIdentifierDecisionEvent.issue_id == issue.id))).scalar()
        curated = (await db.execute(
            select(m.RceCuratedRecord.record_status).where(
                m.RceCuratedRecord.source_record_id == issue.source_record_id))).scalar()
        return {"resolution": issue.resolution, "notes": issue.resolution_notes,
                "resolved_by": issue.resolved_by, "suggested": issue.suggested_value,
                "history_len": len(history), "identifier_events": int(events or 0),
                "record_status": curated}


@pytest.mark.parametrize("decision", ISSUE_DECISIONS)
def test_every_decision_on_a_resolved_finding_is_refused_and_changes_nothing(
        client, delivery, decision):
    issue_id = delivery["issue_ids"][0]
    before = run(_snapshot(issue_id))
    assert before["resolution"] == "RESOLVED"
    body = {"decision": decision, "reason": "trying to change a settled finding"}
    if decision == "CORRECT":
        body["corrected_value"] = "1234567893"
    r = client.post(f"{BASE}/issues/{issue_id}/dispositions", json=body,
                    headers=headers_for("reviewer"))
    assert r.status_code == 409, r.text
    payload = r.json()
    assert (payload.get("error") or payload.get("detail")) == MESSAGE
    assert run(_snapshot(issue_id)) == before


def test_the_refused_attempt_is_audited_without_touching_the_finding(client, delivery):
    issue_id = delivery["issue_ids"][0]
    r = client.post(f"{BASE}/issues/{issue_id}/dispositions",
                    json={"decision": "ACCEPT", "reason": "audit me"},
                    headers=headers_for("reviewer"))
    assert r.status_code == 409
    audit = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/audit",
                       headers=headers_for("reviewer")).json()
    refused = [e for e in audit["items"] if e["action"] == "analyst_disposition_refused"]
    assert refused, [e["action"] for e in audit["items"]]
    meta = refused[0]["detail"]["metadata"]
    assert meta["issue_id"] == issue_id and meta["refusal"] == "terminal_finding"
    assert meta["resolution"] == "RESOLVED"
    assert not any(e["action"] == "analyst_disposition"
                   and e["detail"]["metadata"].get("issue_id") == issue_id
                   for e in audit["items"])


def test_the_ledger_marks_terminal_rows_and_the_ui_can_disable_controls(client, delivery):
    ledger = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions",
                        headers=headers_for("reviewer")).json()
    by_id = {row["issue_id"]: row for row in ledger["items"]}
    assert set(delivery["issue_ids"]) <= set(by_id)
    for row in ledger["items"]:
        assert isinstance(row["terminal"], bool) and isinstance(row["can_dispose"], bool)
        assert row["can_dispose"] is (not row["terminal"])
        assert row["terminal"] is (row["status"] in curation.TERMINAL_RESOLUTIONS)
    resolved = by_id[delivery["issue_ids"][0]]
    assert resolved["status"] == "RESOLVED"
    assert resolved["terminal"] is True and resolved["can_dispose"] is False
    assert by_id[delivery["issue_ids"][2]]["terminal"] is False


def test_a_non_terminal_finding_still_takes_a_decision(client, delivery):
    issue_id = delivery["issue_ids"][2]
    before = run(_snapshot(issue_id))
    assert before["resolution"] == "OPEN"
    r = client.post(f"{BASE}/issues/{issue_id}/dispositions",
                    json={"decision": "REJECT", "reason": "known vendor test record"},
                    headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    after = run(_snapshot(issue_id))
    assert after["resolution"] != "OPEN"
    assert after["resolution"] not in curation.TERMINAL_RESOLUTIONS


async def test_the_fallback_path_refuses_before_touching_the_database():
    from app.tefca_registry.rce import models as m

    issue = m.RceIssue(id=uuid.uuid4(), issue_code="DQ-TEST", resolution="RESOLVED",
                       source_intake_id=uuid.uuid4(), source_record_id=uuid.uuid4())
    with pytest.raises(curation.CorrectionRefused) as refused:
        await _fallback_apply_disposition(None, issue, decision="ACCEPT", reason="x",
                                          actor="t@test.local", corrected_value=None)
    assert str(refused.value) == MESSAGE


async def test_apply_disposition_refuses_a_resolved_issue_before_any_side_effect(db_required):
    """Direct call: the refusal happens right after the issue is loaded, before
    the disposition-history read, the identifier decision or the state walk."""
    from unittest.mock import AsyncMock, patch

    from app.tefca_registry.rce import models as m

    issue = m.RceIssue(id=uuid.uuid4(), issue_code="DQ-TEST", resolution="RESOLVED",
                       source_intake_id=uuid.uuid4(), source_record_id=uuid.uuid4(),
                       issue_type="NPI_EXISTING_VALUE_CONFLICT")
    db = AsyncMock()
    db.get = AsyncMock(return_value=issue)
    with patch("app.tefca_registry.rce.identifier_decisions.decide", new=AsyncMock()) as decide, \
            patch.object(curation, "_walk", new=AsyncMock()) as walk, \
            patch.object(curation, "apply_correction", new=AsyncMock()) as correct:
        for decision in ISSUE_DECISIONS:
            with pytest.raises(curation.CorrectionRefused) as refused:
                await curation.apply_disposition(
                    db, issue.id, decision=decision.lower(), reason="x", actor="t@test.local",
                    corrected_value="1234567893" if decision == "CORRECT" else None)
            assert str(refused.value) == MESSAGE
    assert decide.await_count == walk.await_count == correct.await_count == 0
    assert db.execute.await_count == 0          # not even the history read
