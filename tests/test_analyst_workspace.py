"""Analyst workspace — consolidated findings, evidence freshness, governing
requirements, unanswered questions, context patterns, and Epic's-lessons
assessment slots that read ONLY authorized traffic evidence.

Seeds one real delivery through the real pipeline with a confirmed SAM
exclusion (connector methods patched, no network), so the workspace has a
real review record, real dimension evidence and a real open question.
"""
from __future__ import annotations

import uuid

import pytest

import test_sam_e2e_delivery_path as sam
import test_shadow_reassessment as shadow_tests



@pytest.mark.asyncio
async def test_workspace_consolidates_findings_evidence_requirements_and_questions(
        db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import analyst_workspace as ws

    intake_id, entity_ids, result = await shadow_tests._seed(monkeypatch, n=2)
    monkeypatch.delenv(ws.FRESHNESS_ENV, raising=False)
    ws.set_traffic_evidence_provider(None)

    async with async_session_maker() as db:
        page = await ws.delivery_workspace(db, intake_id, limit=50)
    assert page["total_entities"] == 2 and page["count"] == 2
    assert page["freshness_window_days"] == ws.DEFAULT_FRESHNESS_DAYS
    assert "no traffic-monitoring obligation is asserted" in page["scope_note"]

    e = next(x for x in page["entities"] if x["entity"]["entity_id"] == str(entity_ids[0]))
    # Review block with the governing rule row behind the classification.
    assert e["review"]["bucket"] == result["outcomes"][0]["bucket"]
    assert e["review"]["rule_version"] is not None
    gr = e["review"]["governing_requirement"]
    assert gr["rule_code"] == e["review"]["rule"] and gr["version"] == e["review"]["rule_version"]
    assert gr["conditions"]
    assert e["review"]["actions"]["determination"].endswith("/determination")
    assert e["review"]["actions"]["independent_qa"].endswith("/qa")

    # Evidence: the latest generation only, each item fresh (just written).
    assert e["evidence"]["items"], "no persisted dimension evidence surfaced"
    assert all(i["freshness"]["status"] == ws.REUSED_FRESH for i in e["evidence"]["items"])
    assert e["evidence"]["reused_fresh"] == len(e["evidence"]["items"])
    sam_item = next(i for i in e["evidence"]["items"]
                    if i["source"] == "SAM_GOV" and i["dimension"] == "EXCLUSION_REVOCATION")
    assert sam_item["disposition"] == "REVIEW"

    # The precise unanswered question for an unconfirmed exclusion match.
    qs = [q["question"] for q in e["unanswered_questions"]]
    assert any("exclusion/debarment record is this organisation" in q for q in qs)
    if e["review"]["bucket"] in ("B2", "B3", "B4"):
        assert any(q["about"] == e["review"]["review_id"] for q in e["unanswered_questions"])

    # Quality findings carry their governing requirement and a disposition link.
    for f in e["findings"]:
        g = f["governing_requirement"]
        assert g["rule_id"] == f["rule_id"]
        assert "field_documented" in g and "field_necessity" in g
        assert f["actions"]["disposition"] == f"/api/tefca/rce/issues/{f['issue_id']}/dispositions"
        assert f["cause_group"] in {c["group_key"] for c in page["cause_groups"]}
    # Cause groups span the delivery; a cause shared by both records says "investigate once".
    shared = [c for c in page["cause_groups"] if c["records"] > 1]
    for c in shared:
        assert c["investigate_once"] is True and "per record" in c["note"]

    # Epic's lessons: every assessment slot is UNAVAILABLE without authorized evidence.
    for slot in ("traffic_volume", "exchange_balance", "geographic_plausibility"):
        assert e["contextual_assessments"][slot] == {
            "assessment": ws.ASSESSMENT_UNAVAILABLE,
            "reason": ws.NO_TRAFFIC_EVIDENCE_REASON, "basis": None}
    # Context patterns never auto-reject.
    assert all(p["auto_reject"] is False for p in e["context_patterns"])

    # Per-entity filter and pagination.
    async with async_session_maker() as db:
        one = await ws.delivery_workspace(db, intake_id, entity_id=entity_ids[1])
    assert one["count"] == 1 and one["entities"][0]["entity"]["entity_id"] == str(entity_ids[1])


@pytest.mark.asyncio
async def test_assessment_slots_consume_only_authorized_evidence(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import analyst_workspace as ws

    intake_id, entity_ids, _ = await shadow_tests._seed(monkeypatch, n=1)

    async def provider(db, entity_id):
        assert entity_id == entity_ids[0]
        return {"evidence_id": "AUTH-TRAFFIC-0001", "authorized_by": "COR",
                "period": "2026-09", "traffic_volume": {"assessment": "within_expected_range",
                                                        "reason": "authorized monthly summary"},
                "exchange_balance": {"assessment": "balanced"}}
        # geographic_plausibility deliberately absent -> stays unavailable
    ws.set_traffic_evidence_provider(provider)
    try:
        async with async_session_maker() as db:
            page = await ws.delivery_workspace(db, intake_id)
    finally:
        ws.set_traffic_evidence_provider(None)
    a = page["entities"][0]["contextual_assessments"]
    assert a["traffic_volume"]["assessment"] == "within_expected_range"
    assert a["traffic_volume"]["basis"]["evidence_id"] == "AUTH-TRAFFIC-0001"
    assert a["exchange_balance"]["assessment"] == "balanced"
    assert a["geographic_plausibility"]["assessment"] == ws.ASSESSMENT_UNAVAILABLE
    assert a["geographic_plausibility"]["reason"] == ws.NO_TRAFFIC_EVIDENCE_REASON

    # A failing provider never fails the workspace; the slots fall back to unavailable.
    async def broken(db, entity_id):
        raise RuntimeError("boom")
    ws.set_traffic_evidence_provider(broken)
    try:
        async with async_session_maker() as db:
            page = await ws.delivery_workspace(db, intake_id)
    finally:
        ws.set_traffic_evidence_provider(None)
    assert page["entities"][0]["contextual_assessments"]["traffic_volume"]["assessment"] == "unavailable"


@pytest.mark.asyncio
async def test_evidence_is_marked_stale_outside_the_window(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import analyst_workspace as ws

    intake_id, _, _ = await shadow_tests._seed(monkeypatch, n=1)
    monkeypatch.setenv(ws.FRESHNESS_ENV, "0")
    async with async_session_maker() as db:
        page = await ws.delivery_workspace(db, intake_id)
    ev = page["entities"][0]["evidence"]
    assert ev["freshness_window_days"] == 0
    assert ev["items"] and all(i["freshness"]["status"] == ws.STALE for i in ev["items"])
    assert ev["stale"] == len(ev["items"]) and ev["reused_fresh"] == 0


def test_mask_identifier():
    from app.tefca_registry.rce.analyst_workspace import mask_identifier

    assert mask_identifier("1982916079") == "******6079"
    assert mask_identifier("") == ""
    assert mask_identifier(None) is None


def test_workspace_route_floor(db_required, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from support_delivery_api import headers_for, run

    async def seed():
        intake_id, _, _ = await shadow_tests._seed(monkeypatch, n=1)
        return intake_id
    intake_id = run(seed())
    client = TestClient(app)
    assert client.get(f"/api/tefca/rce/deliveries/{intake_id}/workspace",
                      headers=headers_for("viewer")).status_code == 403
    r = client.get(f"/api/tefca/rce/deliveries/{intake_id}/workspace", headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    assert r.json()["total_entities"] == 1
    assert client.get(f"/api/tefca/rce/deliveries/{uuid.uuid4()}/workspace",
                      headers=headers_for("reviewer")).status_code == 404
    assert client.get(f"/api/tefca/rce/deliveries/{intake_id}/workspace?entity_id=nope",
                      headers=headers_for("reviewer")).status_code == 422
