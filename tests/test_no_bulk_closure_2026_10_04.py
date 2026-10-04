"""No bulk closure of exclusion / identity-conflict findings -- Part B.

THE CHANNEL THIS CLOSES
Every close/disposition ROUTE in the application is single-item
(`/reviews/{id}/resolve`, `/issues/{id}/dispositions`, `/identifier-decisions`),
and the one multi-id body (`workflow_routes` bulk ASSIGNMENT) distributes
work, it does not decide it. The single place one action could change the
standing of MANY findings is `shadow_reassessment.publish_successors`
(local-test mode only): it writes a successor review record for every delta
of a comparison on one pair of approvals. Before this guard it withheld only
EIN/FEIN-flagged and non-reproducible deltas, so a more-permissive candidate
rule set would have superseded an OIG-excluded or identity-conflict record
together with every purely technical one.

Proven here, on synthetic records only:
  * `risk_signals` names exclusion and identity-conflict signals, and does
    NOT flag a clean exclusion screen or a purely technical variance.
  * A comparison-wide publication supersedes the technical record and
    WITHHOLDS the exclusion and identity-conflict records, each with the
    signal that withheld it; their predecessors are byte-identical after.
  * The publication step re-derives the guard itself, so a delta row whose
    stored flag is False (a comparison built before the guard) is still
    withheld.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, update

import test_shadow_reassessment as base

CLEAN = {"nppes": {"status": "verified"}, "oig_leie": {"status": "clear"},
         "pecos": {"status": "not_checked"}}
#: A purely technical/administrative variance: B2 under v2 (RULE-003). PECOS
#: is `not_found` so RULE-002's partial pass does not claim it first.
TECHNICAL_B2 = {"sources": {**CLEAN, "pecos": {"status": "not_found"}},
                "confidence_score": None,
                "fields": {"address_mismatch": {"severity": "minor"}}}
OIG_EXCLUDED = {"sources": {**CLEAN, "oig_leie": {"status": "excluded"}},
                "fields": {}, "confidence_score": None}
IDENTITY_CONFLICT = {"sources": {**CLEAN, "nppes": {"status": "not_found"}},
                     "fields": {}, "confidence_score": None}

#: A deliberately over-permissive what-if candidate: everything becomes B1.
EVERYTHING_B1 = [{
    "rule_code": "RULE-901", "name": "what-if: everything is B1", "bucket": "B1",
    "priority": 1, "version": 0,
    "description": "synthetic over-permissive candidate used only by this test",
    "conditions": {"none_of": [{"field": "signal_that_never_exists", "status": True}]},
}]


# ── pure: what counts as a risk signal ───────────────────────────────────────

def test_risk_signals_names_exclusions_and_identity_conflicts():
    from app.tefca_registry.rce.shadow_reassessment import risk_signals

    assert risk_signals(OIG_EXCLUDED) == ["EXCLUSION:oig_leie:excluded"]
    assert risk_signals(IDENTITY_CONFLICT) == ["IDENTITY:nppes:not_found"]
    pending_sam = {"sources": {"sam_gov": {"status": "not_found", "disposition": "REVIEW"}}}
    assert risk_signals(pending_sam) == ["EXCLUSION:sam_gov:potential_hit_or_unknown_origin"]
    # The manual path persists no disposition: unknown origin is NOT assumed clean.
    unknown = {"sources": {"sam_gov": "not_found"}}
    assert risk_signals(unknown) == ["EXCLUSION:sam_gov:potential_hit_or_unknown_origin"]
    deactivated = {"sources": {"nppes": {"status": "verified", "npi_status": "DEACTIVATED"}}}
    assert risk_signals(deactivated) == ["IDENTITY:nppes:npi_deactivated"]
    flagged = {"sources": {}, "fields": {"npi_validation": {"status": "flagged"}}}
    assert risk_signals(flagged) == ["IDENTITY:npi_validation:flagged"]


def test_risk_signals_does_not_flag_clean_screens_or_technical_variance():
    from app.tefca_registry.rce.shadow_reassessment import risk_signals

    assert risk_signals(TECHNICAL_B2) == []
    clean_name_screen = {"sources": {"sam_gov": {"status": "not_found",
                                                 "disposition": "NOT_FOUND"},
                                     "oig_leie": {"status": "clear"}}}
    assert risk_signals(clean_name_screen) == []
    outage = {"sources": {"sam_gov": {"status": "unavailable"}}}
    assert risk_signals(outage) == []     # an outage is a limitation, not a finding
    assert risk_signals(None) == []


# ── database: publication withholds them, individually ───────────────────────

async def _planted_comparison(monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from app.tefca_registry.rce import shadow_reassessment as shadow

    intake_id, entity_ids, _ = await base._seed(monkeypatch, n=3)
    async with async_session_maker() as db:
        v2 = await shadow.rules_for_version(db, 2)
    planted = {}
    for eid, inp in zip(entity_ids, (OIG_EXCLUDED, IDENTITY_CONFLICT, TECHNICAL_B2)):
        got = BucketClassifier().classify(inp, rules=v2)
        assert got.bucket != "B1", (inp, got.bucket)   # each is a finding under v2
        planted[eid] = await base._plant_latest_record(
            eid, rule_version=2, classifier_input=inp, bucket=got.bucket,
            rule=got.rule_code or "DEFAULT-UNMATCHED")
    tag = uuid.uuid4().hex[:6]
    analyst, qa = base._User("reviewer", tag), base._User("qalead", tag)
    async with async_session_maker() as db:
        dto = await shadow.build_comparison(db, intake_id, built_by=analyst.email,
                                            baseline_rule_version=2,
                                            candidate_rules=[dict(r) for r in EVERYTHING_B1])
    return entity_ids, planted, analyst, qa, dto


async def _approve_both(cid, package_hash, analyst, qa):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    async with async_session_maker() as db:
        await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_ANALYST, user=analyst,
                                     package_hash=package_hash, rationale="synthetic")
    async with async_session_maker() as db:
        await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_INDEPENDENT_QA, user=qa,
                                     package_hash=package_hash, rationale="synthetic")


async def _successor_count(entity_id) -> int:
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    async with async_session_maker() as db:
        return int((await db.execute(select(func.count()).select_from(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == entity_id,
            reg.ReviewRecord.classification_rationale.like("[SHADOW-SUCCESSOR%")))).scalar() or 0)


@pytest.mark.asyncio
async def test_comparison_wide_publication_withholds_exclusion_and_identity_records(
        db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    entity_ids, planted, analyst, qa, dto = await _planted_comparison(monkeypatch)
    excluded_e, identity_e, technical_e = entity_ids
    cid = uuid.UUID(dto["comparison_id"])

    async with async_session_maker() as db:
        deltas = {d["entity_id"]: d for d in (await shadow.list_deltas(db, cid))["items"]}
    # The over-permissive candidate would clear all three: REMOVED, MORE_PERMISSIVE.
    for e in entity_ids:
        assert deltas[str(e)]["delta_kind"] == pm.DELTA_REMOVED
        assert deltas[str(e)]["direction"] == pm.DIR_MORE_PERMISSIVE
    assert deltas[str(excluded_e)]["manual_review_required"] is True
    assert deltas[str(identity_e)]["manual_review_required"] is True
    assert deltas[str(technical_e)]["manual_review_required"] is False
    assert deltas[str(excluded_e)]["detail"]["risk_signals"] == ["EXCLUSION:oig_leie:excluded"]
    assert "individually adjudicated" in deltas[str(excluded_e)]["reason"]
    assert dto["summary"]["manual_review_required"] == 2

    before, _ = await base._official_fingerprint([excluded_e, identity_e])
    await _approve_both(cid, dto["package_hash"], analyst, qa)
    monkeypatch.setenv(shadow.PUBLICATION_MODE_ENV, shadow.LOCAL_TEST_MODE)
    async with async_session_maker() as db:
        pub = await shadow.publish_successors(db, cid, user=qa)

    # Exactly ONE successor: the technical record. Nothing else was superseded.
    assert pub["predecessor_review_ids"] == [planted[technical_e]]
    assert len(pub["successor_review_ids"]) == 1
    assert {w["entity_id"] for w in pub["withheld"]} == {str(excluded_e), str(identity_e)}
    assert await _successor_count(technical_e) == 1
    assert await _successor_count(excluded_e) == 0
    assert await _successor_count(identity_e) == 0
    # The exclusion and identity-conflict records are byte-identical afterwards.
    after, _ = await base._official_fingerprint([excluded_e, identity_e])
    assert after == before


@pytest.mark.asyncio
async def test_publication_rederives_the_guard_when_a_stored_flag_says_otherwise(
        db_required, monkeypatch):
    """A comparison built before the build-time guard existed carries
    manual_review_required=False on its delta rows. Simulated by clearing
    the stored flag; the publication step must still withhold."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    entity_ids, planted, analyst, qa, dto = await _planted_comparison(monkeypatch)
    excluded_e, identity_e, technical_e = entity_ids
    cid = uuid.UUID(dto["comparison_id"])
    async with async_session_maker() as db:
        await db.execute(update(pm.RceShadowFindingDelta)
                         .where(pm.RceShadowFindingDelta.comparison_id == cid)
                         .values(manual_review_required=False))
        await db.commit()

    await _approve_both(cid, dto["package_hash"], analyst, qa)
    monkeypatch.setenv(shadow.PUBLICATION_MODE_ENV, shadow.LOCAL_TEST_MODE)
    async with async_session_maker() as db:
        pub = await shadow.publish_successors(db, cid, user=qa)

    assert pub["predecessor_review_ids"] == [planted[technical_e]]
    withheld = {w["entity_id"]: w for w in pub["withheld"]}
    assert set(withheld) == {str(excluded_e), str(identity_e)}
    assert withheld[str(excluded_e)]["risk_signals"] == ["EXCLUSION:oig_leie:excluded"]
    assert withheld[str(identity_e)]["risk_signals"] == ["IDENTITY:nppes:not_found"]
    assert await _successor_count(excluded_e) == 0
    assert await _successor_count(identity_e) == 0
