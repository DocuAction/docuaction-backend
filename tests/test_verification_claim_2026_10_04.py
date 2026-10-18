"""`verified` while an exclusion screen never completed -- OFFICIAL vs PROPOSED.

THE FINDING (reproduced below against the ACTIVE rule set, v3)
An entity is classified B1, and so marked `verified`, when SAM.gov or the
CMS revocation extract was unavailable, never checked or insufficient. Only
OIG LEIE is required for B1. The rules are approved text and are NOT changed.

WHAT IS IMPLEMENTED
  * the gap is RECORDED on every such review record (`verification_claim`),
    in both views against the same persisted input;
  * OFFICIAL behaviour is unchanged by default: the entity is still marked
    verified (`ENFORCE_COMPLETE_EXCLUSION_SCREENING` off);
  * the PROPOSED, inactive policy withholds `verified`; turning the flag on
    is the decision still to be made -- it is not made here.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.tefca_registry.rce import prior_risk as pr


def _inp(**sources):
    return {"sources": {k: (v if isinstance(v, dict) else {"status": v})
                        for k, v in sources.items()}, "fields": {}}


# ── pure ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,sources,expected_rule", [
    ("sam_outage", dict(nppes="verified", oig_leie="clear", pecos="verified",
                        sam_gov="unavailable", cms_revocation="verified"), "RULE-001"),
    ("sam_and_pecos_outage", dict(nppes="verified", oig_leie="clear", pecos="unavailable",
                                  sam_gov="unavailable", cms_revocation="verified"), "RULE-002"),
    ("sam_never_checked", dict(nppes="verified", oig_leie="clear", pecos="verified",
                               sam_gov="not_checked", cms_revocation="verified"), "RULE-001"),
    ("revocation_outage", dict(nppes="verified", oig_leie="clear", pecos="verified",
                               sam_gov="clear", cms_revocation="unavailable"), "RULE-001"),
])
def test_the_active_rules_classify_b1_while_an_exclusion_screen_is_incomplete(
        label, sources, expected_rule):
    """Pins the OFFICIAL behaviour so it cannot change unnoticed either way."""
    from app.tefca_registry.bucket_classifier import BucketClassifier, _v3_rules

    inp = _inp(**sources)
    got = BucketClassifier().classify(inp, rules=_v3_rules())
    assert (got.bucket, got.rule_code) == ("B1", expected_rule), label
    assert pr.exclusion_screening_gaps(inp), label          # ...and the gap is detected


def test_gaps_name_each_incomplete_control_and_ignore_not_applicable():
    inp = _inp(oig_leie="clear",
               sam_gov={"status": "unavailable", "disposition": "UNAVAILABLE"},
               cms_revocation={"status": "not_checked", "disposition": "NOT_APPLICABLE"})
    assert pr.exclusion_screening_gaps(inp) == [{"control": "sam_gov", "gap": "UNAVAILABLE"}]

    insufficient = _inp(oig_leie={"status": "not_checked", "disposition": "INSUFFICIENT_EVIDENCE"},
                        sam_gov="clear", cms_revocation="verified")
    assert pr.exclusion_screening_gaps(insufficient) == [
        {"control": "oig_leie", "gap": "INSUFFICIENT_EVIDENCE"}]

    # The manual path stubs SAM as never evaluated; a missing control is a gap too.
    manual = _inp(oig_leie="clear", sam_gov="not_checked")
    assert pr.exclusion_screening_gaps(manual) == [
        {"control": "sam_gov", "gap": "NOT_EVALUATED"},
        {"control": "cms_revocation", "gap": "NOT_EVALUATED"}]

    complete = _inp(oig_leie="clear", sam_gov="clear", cms_revocation="verified")
    assert pr.exclusion_screening_gaps(complete) == []


def test_the_two_views_differ_only_when_a_gap_exists(monkeypatch):
    gaps = [{"control": "sam_gov", "gap": "UNAVAILABLE"}]
    assert settings.ENFORCE_COMPLETE_EXCLUSION_SCREENING is False
    claim = pr.verification_claim("B1", gaps, None)
    assert claim["official"]["entity_marked_verified"] is True           # unchanged today
    assert claim["proposed_inactive"]["entity_would_be_marked_verified"] is False
    assert claim["proposed_inactive"]["status"] == "INACTIVE_UNAPPROVED"

    monkeypatch.setattr(settings, "ENFORCE_COMPLETE_EXCLUSION_SCREENING", True)
    enforced = pr.verification_claim("B1", gaps, None)
    assert enforced["official"]["entity_marked_verified"] is False
    assert enforced["proposed_inactive"]["status"] == "ENFORCED"

    no_gap = pr.verification_claim("B1", [], None)
    assert no_gap["official"]["entity_marked_verified"] is True
    assert no_gap["proposed_inactive"]["entity_would_be_marked_verified"] is True


# ── real pipeline, SAM.gov down ──────────────────────────────────────────────

#: Mirrors RULE-001's exclusion requirement exactly (OIG only), without the
#: PECOS requirement this synthetic fixture cannot satisfy offline. Added
#: after the B4 disqualifier, like the live B1 rules.
WHAT_IF_B1_OIG_ONLY = {
    "rule_code": "RULE-903", "name": "what-if: B1 on NPPES + OIG (as RULE-001, no PECOS)",
    "bucket": "B1", "priority": 6, "version": 0,
    "description": "synthetic rule used only by test_verification_claim",
    "conditions": {"all_of": [{"source": "nppes", "status": "verified"},
                              {"source": "oig_leie", "status": "clear"}]},
}


async def _classified_during_a_sam_outage(monkeypatch):
    import test_sam_e2e_delivery_path as sam
    from app.Tefca.connectors import SourceResult
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import arc_pipeline

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    sam._clean_nppes_leie(monkeypatch)
    sam._patch_sam_verify(monkeypatch, lambda uei, legal_name: SourceResult.unavailable(
        "SAM_GOV", "synthetic outage (HTTP 503)", {"legal_name": legal_name}, "v3+v4"))
    real = arc_pipeline._rule_set

    async def rules(db):
        return (await real(db)) + [dict(WHAT_IF_B1_OIG_ONLY)]
    monkeypatch.setattr(arc_pipeline, "_rule_set", rules)

    intake_id = await sam._seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, 1)
    async with async_session_maker() as db:
        outcome = (await arc_pipeline.verify_and_classify(
            db, refs, intake_id=intake_id, actor="pytest-claim"))["outcomes"][0]
    async with async_session_maker() as db:
        record = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == outcome["review_id"]))).scalars().one()
        status = (await db.get(reg.TefcaRegEntity,
                               uuid.UUID(outcome["entity_id"]))).verification_status
    return outcome, record, status


@pytest.mark.asyncio
async def test_official_view_still_verifies_and_the_gap_is_recorded(db_required, monkeypatch):
    assert settings.ENFORCE_COMPLETE_EXCLUSION_SCREENING is False
    outcome, record, status = await _classified_during_a_sam_outage(monkeypatch)

    assert outcome["bucket"] == "B1"
    assert status == "verified"                        # OFFICIAL behaviour, unchanged
    claim = record.verification_results["verification_claim"]
    assert {"control": "sam_gov", "gap": "UNAVAILABLE"} in claim["exclusion_screening_incomplete"]
    assert claim["official"]["entity_marked_verified"] is True
    assert claim["proposed_inactive"]["entity_would_be_marked_verified"] is False
    assert claim["enforced"] is False
    assert outcome["tier"] == 1                        # routing unchanged too


@pytest.mark.asyncio
async def test_proposed_view_enforced_withholds_verified_without_changing_the_bucket(
        db_required, monkeypatch):
    monkeypatch.setattr(settings, "ENFORCE_COMPLETE_EXCLUSION_SCREENING", True)
    outcome, record, status = await _classified_during_a_sam_outage(monkeypatch)

    assert outcome["bucket"] == "B1" == record.classification_bucket   # classifier untouched
    assert status == "in_review"                                       # no false pass
    assert outcome["tier"] == 2                                        # a person must look
    assert record.verification_results["verification_claim"]["enforced"] is True
