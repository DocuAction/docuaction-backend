"""Route-level proof of the QA-approval fix (fix/qa-approval-non-b1-eligibility-
2026-10-03, 732b47b), through the REAL FastAPI app via httpx's ASGI
transport (in-process, no network, same pattern as
test_determination_ownership.py's own certification tests) -- not the
qa_gate function called directly.

Two real review records, same pipeline that produces every real one
(ingest -> quality -> curate -> promote -> verify_and_classify):
  - one classifies B2 with NO genuine post-promotion finding (eligible,
    the case the fix unblocks)
  - one has a REAL post_promotion_verification.record_finding call against
    it (a genuine blocking finding, the case that must still refuse)

Both go through claim -> POST .../determination -> POST .../qa for real,
with two different synthetic users (maker/checker), exactly the routes a
real analyst and a real independent QA reviewer would call.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

import test_sam_e2e_delivery_path as sam

pytestmark = pytest.mark.asyncio


async def _synthetic_user(db, label: str, role: str):
    from app.core.security import hash_password
    from app.models.database import User

    email = f"qaroute-{label}-{uuid.uuid4().hex[:8]}@synthetic-test.docuaction.invalid"
    password = f"CertTest!{uuid.uuid4().hex}"
    user = User(id=uuid.uuid4(), tenant_id="synthetic-cert", email=email,
               password_hash=hash_password(password), full_name=f"SYNTHETIC {label}",
               role=role, is_active=True, is_verified=True, status="active",
               allowed_modules=[])
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user, password


async def _login(client, email, password):
    last = None
    for attempt in range(4):
        last = await client.post("/api/auth/login", json={"email": email, "password": password})
        if last.status_code != 429:
            return last
        await asyncio.sleep(2 * (attempt + 1))
    pytest.skip(f"login still rate-limited after retries: {last.text[:150]!r}")


async def test_eligible_non_b1_completes_qa_via_real_routes_genuine_finding_still_denied():
    from app.core.database import async_session_maker
    from app.main import app
    from app.tefca_registry import case_assignment as ca
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import post_promotion_verification as ppv
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    # ---- Entity A: clean sources, no genuine finding -- the fix's target ----
    intake_a = await sam._seed_promoted_delivery(n=1)
    mp = pytest.MonkeyPatch()
    try:
        mp.setenv("ENTITY_RESOLVER_SOURCE", "db")  # explicit: the default resolver is a bundled mock dataset that cannot resolve freshly-seeded synthetic entities
        sam._clean_nppes_leie(mp)

        def clean_sam(*, uei, legal_name):
            from app.Tefca.connectors import SourceResult
            return SourceResult.ok("SAM_GOV", {
                "found": True, "matched_by": "uei", "excluded": False,
                "excluded_known": True, "identity_ambiguous": False,
                "registration_current": True,
            }, {"uei": uei})
        sam._patch_sam_verify(mp, clean_sam)

        async with async_session_maker() as db:
            refs_a = await sam._promoted_refs(db, intake_a, 1)
        async with async_session_maker() as db:
            result_a = await verify_and_classify(db, refs_a, intake_id=intake_a, actor="route-test")
    finally:
        mp.undo()
    review_id_a = result_a["outcomes"][0]["review_id"]
    entity_a_id = result_a["outcomes"][0]["entity_id"]
    print(f"[A] seeded, bucket={result_a['outcomes'][0]['bucket']}, review_id={review_id_a}")

    # ---- Entity B: classified exactly like A, THEN a REAL genuine blocking
    # finding is recorded against its actual entity_id (same path, same
    # helper, the only difference is the finding recorded afterward). ----
    intake_b = await sam._seed_promoted_delivery(n=1)
    mp_b = pytest.MonkeyPatch()
    try:
        mp_b.setenv("ENTITY_RESOLVER_SOURCE", "db")  # same reason as entity A -- mp.undo() above already reverted this
        sam._clean_nppes_leie(mp_b)
        sam._patch_sam_verify(mp_b, clean_sam)
        async with async_session_maker() as db:
            refs_b = await sam._promoted_refs(db, intake_b, 1)
        async with async_session_maker() as db:
            result_b = await verify_and_classify(db, refs_b, intake_id=intake_b, actor="route-test")
    finally:
        mp_b.undo()
    review_id_b = result_b["outcomes"][0]["review_id"]
    entity_b_id = result_b["outcomes"][0]["entity_id"]

    async with async_session_maker() as db:
        await ppv.record_finding(db, entity_id=entity_b_id, outcome=ppv.NPI_DEACTIVATED,
                                 npi="1234567893", actor="route-test")
        await db.commit()
    print(f"[B] seeded (bucket={result_b['outcomes'][0]['bucket']}), "
          f"THEN a genuine open finding recorded, review_id={review_id_b}")

    async with async_session_maker() as db:
        analyst, analyst_pw = await _synthetic_user(db, "analyst", "reviewer")
        qalead, qalead_pw = await _synthetic_user(db, "qalead", "qalead")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await _login(client, analyst.email, analyst_pw)
        if r.status_code != 200:
            pytest.skip(f"analyst login did not return 200 (known pytest-session "
                        f"environment quirk, documented in test_determination_"
                        f"ownership.py): {r.text[:200]!r}")
        client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

        # Claim + determine + (attempt) QA for BOTH entities as the analyst
        # and the independent QA lead, through the real routes.
        for label, review_id in (("A-eligible", review_id_a), ("B-genuine-finding", review_id_b)):
            async with async_session_maker() as db:
                class _FU:
                    def __init__(self, u):
                        self.id, self.email, self.role = u.id, u.email, u.role
                await ca.claim(db, review_id, user=_FU(analyst))
                await db.commit()

            det = await client.post(
                f"/api/tefca/arc/reviews/{review_id}/determination",
                json={"determination": "CONFIRM",
                      "rationale": f"SYNTHETIC route-level test, {label}"})
            print(f"[{label}] determination -> {det.status_code}")
            assert det.status_code == 200, f"[{label}] determination route failed: {det.text}"

        client.headers["Authorization"] = (
            f"Bearer {(await _login(client, qalead.email, qalead_pw)).json()['access_token']}")

        qa_a = await client.post(f"/api/tefca/arc/reviews/{review_id_a}/qa",
                                 json={"qa_action": "APPROVE",
                                       "qa_reason": "SYNTHETIC route-level: eligible non-B1"})
        print(f"[A-eligible] QA APPROVE -> {qa_a.status_code}: {qa_a.text[:200]}")
        assert qa_a.status_code == 200, (
            f"eligible non-B1 QA approval was refused through the real route: {qa_a.text}")

        qa_b = await client.post(f"/api/tefca/arc/reviews/{review_id_b}/qa",
                                 json={"qa_action": "APPROVE",
                                       "qa_reason": "SYNTHETIC route-level: genuine finding"})
        print(f"[B-genuine-finding] QA APPROVE -> {qa_b.status_code}: {qa_b.text[:200]}")
        assert qa_b.status_code == 409, (
            f"an entity with a genuine open blocking finding was NOT denied through "
            f"the real route (got {qa_b.status_code}, expected 409): {qa_b.text}")
        assert "unresolved post-promotion verification finding" in qa_b.text

    async with async_session_maker() as db:
        rr_a = (await db.execute(
            __import__("sqlalchemy").select(reg.ReviewRecord)
            .where(reg.ReviewRecord.review_id == review_id_a))).scalars().first()
        rr_b = (await db.execute(
            __import__("sqlalchemy").select(reg.ReviewRecord)
            .where(reg.ReviewRecord.review_id == review_id_b))).scalars().first()
    print(f"[A-eligible] reportable_at={rr_a.reportable_at}")
    print(f"[B-genuine-finding] reportable_at={rr_b.reportable_at}")
    assert rr_a.reportable_at is not None, "route-approved A did not become reportable"
    assert rr_b.reportable_at is None, "route-denied B must not be reportable"
