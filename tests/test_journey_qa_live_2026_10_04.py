"""Analyst + Independent QA journey, route-level against the LIVE running
server (http://127.0.0.1:8103, DATABASE_URL=test_journey_1003), driven from
inside pytest so entity seeding reuses the exact proven pattern from
test_qa_approval_route_level_2026_10_03.py (ASGI in-process) without the
session/pooling quirks of a bare asyncio script. The claim/determination/qa
calls go over real HTTP to the live server, not the ASGI transport, which is
the part this round's directive asked to exercise live.
"""
from __future__ import annotations

import httpx
import pytest

import test_sam_e2e_delivery_path as sam

pytestmark = pytest.mark.asyncio

LIVE = "http://127.0.0.1:8103"


async def _seed_entity(monkeypatch, label):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    intake = await sam._seed_promoted_delivery(n=1)
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    sam._clean_nppes_leie(monkeypatch)

    def clean_sam(*, uei, legal_name):
        from app.Tefca.connectors import SourceResult
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": False,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True,
        }, {"uei": uei})
    sam._patch_sam_verify(monkeypatch, clean_sam)

    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake, 1)
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake, actor=f"journey-live-{label}")
    assert result["outcomes"], f"[{label}] seeding produced no outcomes: {result}"
    o = result["outcomes"][0]
    print(f"[{label}] review_id={o['review_id']} entity_id={o['entity_id']} bucket={o['bucket']}")
    return o["review_id"], o["entity_id"]


async def test_journey_qa_live():
    mp = pytest.MonkeyPatch()
    try:
        review_a, entity_a = await _seed_entity(mp, "A")
        review_b, entity_b = await _seed_entity(mp, "B")
        review_c, entity_c = await _seed_entity(mp, "C")
        review_d, entity_d = await _seed_entity(mp, "D")
    finally:
        mp.undo()

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import post_promotion_verification as ppv
    async with async_session_maker() as db:
        await ppv.record_finding(
            db, entity_id=entity_b, outcome="NPI_DEACTIVATED",
            detail={"source": "journey-live-test"}, actor="journey-live-seed")
        await db.commit()
    print(f"[B] genuine blocking finding recorded against {entity_b}")

    with httpx.Client(timeout=20) as client:
        analyst_tok = client.post(f"{LIVE}/api/auth/login", json={
            "email": "journey-analyst@synthetic-test.docuaction.invalid",
            "password": "JourneyAnalyst!2026"}).json()["access_token"]
        qalead_tok = client.post(f"{LIVE}/api/auth/login", json={
            "email": "journey-qalead@synthetic-test.docuaction.invalid",
            "password": "JourneyQALead!2026"}).json()["access_token"]
        HA = {"Authorization": f"Bearer {analyst_tok}"}
        HQ = {"Authorization": f"Bearer {qalead_tok}"}

        def claim_and_determine(review_id, label):
            r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_id}/claim", headers=HA)
            print(f"[{label}] claim: {r.status_code}")
            r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_id}/determination", headers=HA,
                            json={"determination": "CONFIRM", "rationale": f"journey-live determination for {label}, confirming classification"})
            print(f"[{label}] determination: {r.status_code} {r.text[:300]}")
            return r

        # ---- A: eligible, different-person QA -> expect success ----
        claim_and_determine(review_a, "A")
        r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_a}/qa", headers=HQ,
                        json={"qa_action": "APPROVE", "qa_reason": "journey-live QA approval for entity A, eligible case"})
        print(f"[A] qa (expect 200/reportable): {r.status_code} {r.text[:400]}")
        assert r.status_code == 200, f"[A] eligible QA approval should succeed: {r.text}"
        body_a = r.json()
        assert body_a.get("reportable") or body_a.get("reportable_at"), f"[A] not marked reportable: {body_a}"

        # ---- B: genuine blocking finding -> expect refusal ----
        claim_and_determine(review_b, "B")
        r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_b}/qa", headers=HQ,
                        json={"qa_action": "APPROVE", "qa_reason": "journey-live QA approval attempt for entity B, has genuine finding"})
        print(f"[B] qa (expect refusal): {r.status_code} {r.text[:400]}")
        assert r.status_code in (409, 400, 422), f"[B] genuine finding should refuse QA approval: {r.text}"
        assert "unresolved" in r.text.lower() or "finding" in r.text.lower(), \
            f"[B] refusal message should name the finding: {r.text}"

        # ---- C: SAME user does determination AND qa -> expect self-approval denial ----
        claim_and_determine(review_c, "C")
        r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_c}/qa", headers=HA,
                        json={"qa_action": "APPROVE", "qa_reason": "self-approval attempt by the same analyst, should be refused"})
        print(f"[C] self-approval qa (role-gate, expect refusal): {r.status_code} {r.text[:400]}")
        assert r.status_code in (403, 409, 400, 422), f"[C] self-approval should be refused: {r.text}"

        # ---- D: a qalead-level user does BOTH determination and QA on their
        # own case -- the actual segregation-of-duties check (not just a
        # role-level gate, since this user DOES hold qalead). ----
        r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_d}/claim", headers=HQ)
        print(f"[D] claim (by qalead acting as maker): {r.status_code}")
        r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_d}/determination", headers=HQ,
                        json={"determination": "CONFIRM",
                              "rationale": "journey-live determination for D, by the qalead user themselves"})
        print(f"[D] determination (by qalead): {r.status_code} {r.text[:300]}")
        r = client.post(f"{LIVE}/api/tefca/arc/reviews/{review_d}/qa", headers=HQ,
                        json={"qa_action": "APPROVE",
                              "qa_reason": "same qalead user approving their own determination"})
        print(f"[D] same-person SoD qa (expect 409 SoD refusal): {r.status_code} {r.text[:400]}")
        assert r.status_code == 409, f"[D] same-person SoD should be refused with 409: {r.text}"
        assert "segregation of duties" in r.text.lower(), \
            f"[D] refusal should name segregation of duties: {r.text}"
