"""A risk signal that DISAPPEARS is not a risk signal that was CLEARED.

Real pipeline (ingest -> quality -> curate -> promote -> verify_and_classify),
connector methods patched, no network, synthetic records only.

Cycle 1: SAM.gov returns a confirmed exclusion  -> not B1, entity in_review.
Cycle 2: the SAME entity, SAM.gov now returns a clean UEI match.

Before the 2026-10-04 guard, cycle 2 classified B1 and wrote
`verification_status = "verified"` unconditionally -- the exclusion signal
was cleared by nothing but its own absence, with no human decision and no
reinstatement evidence anywhere. Proven here:

  * an un-adjudicated prior exclusion signal keeps the entity `in_review`
    after a clean cycle, and the new review record says why;
  * the classifier's own bucket is NOT rewritten (the rules engine stays
    the sole classifier) -- the hold is recorded beside it;
  * a prior signal a human reclassified AND independent QA made reportable
    DOES let a later clean cycle verify (the guard is not a one-way trap);
  * an open BLOCKING ledger finding (deactivated NPI) has the same effect;
  * an entity with no prior signal is unaffected.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

import test_sam_e2e_delivery_path as sam

pytestmark = pytest.mark.asyncio


def _sam(excluded: bool):
    from app.Tefca.connectors import SourceResult

    def factory(*, uei, legal_name):
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": excluded,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True}, {"uei": uei})
    return factory


#: The synthetic fixture never reaches B1 under the live rules for reasons
#: unrelated to this guard (its name differs from the fake NPPES record and it
#: has no PECOS enrolment). The live rules are kept, and ONE what-if rule is
#: added AFTER the B4 disqualifier (priority 6 > RULE-005's 5): B1 when the
#: exclusion lists are clean. The guard, not the rule set, is under test.
WHAT_IF_B1 = {
    "rule_code": "RULE-902", "name": "what-if: B1 when exclusion lists are clean",
    "bucket": "B1", "priority": 6, "version": 0,
    "description": "synthetic rule used only by test_prior_risk_not_cleared",
    "conditions": {"all_of": [{"source": "nppes", "status": "verified"},
                              {"source": "oig_leie", "status": "clear"},
                              {"source": "sam_gov", "status": "clear"}]},
}


async def _cycle(monkeypatch, refs, intake_id, *, excluded: bool):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import arc_pipeline
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    real_rule_set = getattr(arc_pipeline._rule_set, "_real", arc_pipeline._rule_set)

    async def rules_plus_what_if(db):
        return (await real_rule_set(db)) + [dict(WHAT_IF_B1)]
    rules_plus_what_if._real = real_rule_set
    monkeypatch.setattr(arc_pipeline, "_rule_set", rules_plus_what_if)

    sam._patch_sam_verify(monkeypatch, _sam(excluded))
    async with async_session_maker() as db:
        return await verify_and_classify(db, refs, intake_id=intake_id, actor="pytest-prior-risk")


async def _seed_one(monkeypatch):
    from app.core.database import async_session_maker

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    sam._clean_nppes_leie(monkeypatch)
    intake_id = await sam._seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, 1)
    return intake_id, refs


async def _entity_status(entity_id):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    async with async_session_maker() as db:
        return (await db.get(reg.TefcaRegEntity, uuid.UUID(str(entity_id)))).verification_status


async def _record(review_id):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    async with async_session_maker() as db:
        return (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == review_id))).scalars().one()


async def test_a_disappeared_exclusion_signal_does_not_verify_the_entity(db_required, monkeypatch):
    intake_id, refs = await _seed_one(monkeypatch)

    first = (await _cycle(monkeypatch, refs, intake_id, excluded=True))["outcomes"][0]
    assert first["bucket"] != "B1"
    assert await _entity_status(first["entity_id"]) == "in_review"

    second = (await _cycle(monkeypatch, refs, intake_id, excluded=False))["outcomes"][0]
    # The rules engine is still the sole classifier: its answer is recorded as given.
    assert second["bucket"] == "B1"
    # ...but the entity is NOT verified, and the outcome says why.
    assert await _entity_status(second["entity_id"]) == "in_review"
    assert second["prior_risk_not_cleared"]["prior_review_id"] == first["review_id"]
    assert any(s.startswith("EXCLUSION:sam_gov") for s in second["prior_risk_not_cleared"]["signals"])

    rec = await _record(second["review_id"])
    held = rec.verification_results["prior_risk_not_cleared"]
    assert held["prior_review_id"] == first["review_id"]
    assert "PRIOR-RISK-NOT-CLEARED" in rec.classification_rationale
    # The cycle-1 record is untouched history.
    prior = await _record(first["review_id"])
    assert prior.classification_bucket == first["bucket"]
    assert prior.reviewer_resolution is None


async def test_a_human_cleared_and_qa_reportable_prior_signal_allows_verification(
        db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    intake_id, refs = await _seed_one(monkeypatch)
    first = (await _cycle(monkeypatch, refs, intake_id, excluded=True))["outcomes"][0]

    # Simulated adjudication with DISTINCT synthetic identities: an analyst
    # reclassifies after reviewing reinstatement evidence, independent QA
    # makes it reportable. No REAL approval is recorded anywhere (both
    # actors below are synthetic), but the decision-event rows a real
    # analyst+QA pair would leave ARE written -- this fixture is simulating
    # that a human act happened, so it must leave the same trail a real one
    # would, or it silently manufactures the exact "fabricated history" gap
    # tests/test_qa_gate.py::test_no_fabricated_history_for_existing_determinations
    # exists to catch.
    async with async_session_maker() as db:
        r = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == first["review_id"]))).scalars().one()
        r.reviewer_resolution = "reclassified"
        r.reclassified_to = "B1"
        r.resolution_rationale = "SYNTHETIC: reinstatement evidence reviewed (test fixture)"
        r.reportable_at = datetime.utcnow()
        db.add(reg.ReviewDecisionEvent(
            id=uuid.uuid4(), review_id=r.review_id, sequence_number=1,
            event_type="ANALYST_DETERMINATION", actor_user_id=uuid.uuid4(),
            actor_email="synthetic-analyst@test.docuaction.invalid",
            actor_role="reviewer", determination="RECLASSIFY",
            determined_bucket="B1",
            rationale="SYNTHETIC: reinstatement evidence reviewed (test fixture)"))
        db.add(reg.ReviewDecisionEvent(
            id=uuid.uuid4(), review_id=r.review_id, sequence_number=2,
            event_type="QA_REVIEW", actor_user_id=uuid.uuid4(),
            actor_email="synthetic-qalead@test.docuaction.invalid",
            actor_role="qalead", qa_action="APPROVE",
            qa_reason="SYNTHETIC: independent QA (test fixture)",
            rationale="SYNTHETIC: independent QA (test fixture)"))
        await db.commit()

    second = (await _cycle(monkeypatch, refs, intake_id, excluded=False))["outcomes"][0]
    assert second["bucket"] == "B1"
    assert second.get("prior_risk_not_cleared") is None
    assert await _entity_status(second["entity_id"]) == "verified"


async def test_reclassified_without_independent_qa_is_not_enough(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    intake_id, refs = await _seed_one(monkeypatch)
    first = (await _cycle(monkeypatch, refs, intake_id, excluded=True))["outcomes"][0]
    async with async_session_maker() as db:
        r = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == first["review_id"]))).scalars().one()
        r.reviewer_resolution = "reclassified"
        r.reclassified_to = "B1"          # maker only -- no QA, reportable_at stays NULL
        # The maker act itself is still a real decision and leaves a real
        # event -- only the QA event is deliberately absent here.
        db.add(reg.ReviewDecisionEvent(
            id=uuid.uuid4(), review_id=r.review_id, sequence_number=1,
            event_type="ANALYST_DETERMINATION", actor_user_id=uuid.uuid4(),
            actor_email="synthetic-analyst@test.docuaction.invalid",
            actor_role="reviewer", determination="RECLASSIFY",
            determined_bucket="B1",
            rationale="SYNTHETIC: maker-only reclassification, no QA (test fixture)"))
        await db.commit()

    second = (await _cycle(monkeypatch, refs, intake_id, excluded=False))["outcomes"][0]
    assert await _entity_status(second["entity_id"]) == "in_review"
    assert second["prior_risk_not_cleared"]["why_not_cleared"] == "no_independent_qa"


async def test_an_open_blocking_ledger_finding_keeps_the_entity_in_review(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import post_promotion_verification as ppv

    intake_id, refs = await _seed_one(monkeypatch)
    first = (await _cycle(monkeypatch, refs, intake_id, excluded=False))["outcomes"][0]
    assert first["bucket"] == "B1" and await _entity_status(first["entity_id"]) == "verified"

    async with async_session_maker() as db:
        await ppv.record_finding(db, entity_id=uuid.UUID(first["entity_id"]),
                                 outcome=ppv.NPI_DEACTIVATED, npi=None,
                                 detail="SYNTHETIC deactivation (test fixture)",
                                 actor="pytest-prior-risk")
        await db.commit()
    assert await _entity_status(first["entity_id"]) == "in_review"

    second = (await _cycle(monkeypatch, refs, intake_id, excluded=False))["outcomes"][0]
    assert second["bucket"] == "B1"
    assert await _entity_status(second["entity_id"]) == "in_review"
    assert second["prior_risk_not_cleared"]["open_blocking_finding"] is True


async def test_an_entity_with_no_prior_signal_verifies_normally(db_required, monkeypatch):
    intake_id, refs = await _seed_one(monkeypatch)
    first = (await _cycle(monkeypatch, refs, intake_id, excluded=False))["outcomes"][0]
    assert first["bucket"] == "B1" and first.get("prior_risk_not_cleared") is None
    assert await _entity_status(first["entity_id"]) == "verified"
    second = (await _cycle(monkeypatch, refs, intake_id, excluded=False))["outcomes"][0]
    assert await _entity_status(second["entity_id"]) == "verified"
