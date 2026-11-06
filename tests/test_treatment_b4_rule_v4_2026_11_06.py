"""SEED_RULES_V4 / RULE-006 + RULE-007: active + exact T-TRTMNT + NPI
requirement in force + missing SUBMITTED NPI -> B4 ONLY when the §7
initiating applicability is established by the delivered
organizationNodeType; unknown applicability -> B3 explicit review
(RULE-007), never B4 and never a clean-source B1. Proposed, never
auto-activated.

Four layers, separately:
  * the SIGNAL (pure): computed from submitted values only, raws preserved,
    T-TREAT and token variants never fire it, supplemental-evidence NPIs
    cannot suppress it (the helper never sees them, by design);
  * APPLICABILITY (pure, inside the signal): `status` True requires
    direction `initiating-node`; blank/passthrough/absent node type is
    `initiating_requirement = "unresolved"` with `applicability_review`;
  * the RULES (pure classifier): RULE-006/RULE-007 fire only under the v4
    rule set — the same input under v3 still classifies B1, proving
    merge/deploy activates nothing;
  * ACTIVATION (database): ensure_rules_v4 retires v3 idempotently, and the
    pipeline's automatic ensure chain cannot resurrect v3 afterwards.

All data synthetic.
"""
from __future__ import annotations

from datetime import date

import pytest

from rce_traceability_support import rolled_back_db  # noqa: F401

from app.tefca_registry.bucket_classifier import (
    BucketClassifier, SEED_RULES_V3, SEED_RULES_V4)
from app.tefca_registry.rce.npi_applicability import (
    active_treatment_missing_required_npi)
from app.tefca_registry.rce.quality_rules import BR_NPI_TRTMNT_EFFECTIVE

IN_FORCE = date(2026, 11, 6)        # >= BR_NPI_TRTMNT_EFFECTIVE (2026-10-25)
BEFORE = date(2026, 10, 1)


def _signal(purposes, npi="", active="1", when=IN_FORCE, node_type=None):
    """The RULE-006 signal for one synthetic record."""
    return active_treatment_missing_required_npi(
        purposes, npi, active,
        assessment_date=when, requirement_effective=BR_NPI_TRTMNT_EFFECTIVE,
        org_node_type=node_type)


# ── the signal ────────────────────────────────────────────────────────────────

def test_signal_b4_eligible_only_with_initiating_direction():
    sig = _signal("T-TRTMNT", node_type="initiating-node")
    assert sig is not None and sig["status"] is True
    assert sig["case"] == 1
    assert sig["initiating_requirement"] == "applies"
    assert sig["exchange_direction"] == "initiating"
    assert sig["applicability_review"] is False
    # original submitted values preserved verbatim
    assert sig["raw_purposes"] == "T-TRTMNT"
    assert sig["raw_npi"] == ""
    assert sig["raw_active"] == "1"
    assert sig["raw_org_node_type"] == "initiating-node"
    assert "supplemental" in sig["note"].lower()


def test_unknown_direction_is_review_never_confirmed():
    """A blank node type, a passthrough node, or no node type at all leaves
    the §7 applicability UNRESOLVED: status False (never B4-eligible) with an
    explicit applicability_review flag. Unknown is not confirmed."""
    for node_type in (None, "", "   ", "passthrough-node", "no-node", "OTHER"):
        sig = _signal("T-TRTMNT", node_type=node_type)
        assert sig is not None, node_type
        assert sig["status"] is False, node_type
        assert sig["initiating_requirement"] == "unresolved", node_type
        assert sig["applicability_review"] is True, node_type
        assert sig["raw_org_node_type"] == (node_type or "")
        # raws still preserved for the analyst
        assert sig["raw_purposes"] == "T-TRTMNT"


def test_signal_fires_on_any_combination_containing_trtmnt():
    for purposes in ("T-PH-ECR,T-TRTMNT", "T-TRTMNT,T-PYMNT",
                     "T-TREAT,T-TRTMNT", "T-PYMNT, T-TRTMNT ,T-PH-ECR"):
        sig = _signal(purposes, node_type="initiating-node")
        assert sig is not None and sig["status"] is True, purposes
        assert sig["raw_purposes"] == purposes  # untouched


def test_t_treat_and_token_variants_never_fire():
    """T-TREAT is case 3, its own question — never this rule's. Neither do
    substring/case variants of the exact token."""
    for purposes in ("T-TREAT", "T-TREAT,T-PYMNT", "t-trtmnt", "T-TRTMNTX",
                     "XT-TRTMNT", "TRTMNT", ""):
        assert _signal(purposes, node_type="initiating-node") is None, purposes


def test_inactive_or_unsupported_active_never_fires():
    assert _signal("T-TRTMNT", active="0", node_type="initiating-node") is None
    assert _signal("T-TRTMNT", active="", node_type="initiating-node") is None
    assert _signal("T-TRTMNT", active="yes-ish",
                   node_type="initiating-node") is None  # UNSUPPORTED:<value>


def test_supplied_npi_never_fires():
    assert _signal("T-TRTMNT", npi="1234567893",
                   node_type="initiating-node") is None
    # even a malformed supplied value is case 2 (validity is NPI-002/003/004's
    # question, not this rule's)
    assert _signal("T-TRTMNT", npi="12345", node_type="initiating-node") is None


def test_requirement_not_yet_in_force_never_fires():
    assert _signal("T-TRTMNT", when=BEFORE, node_type="initiating-node") is None


# ── the rules, against the two rule sets ──────────────────────────────────────

_CLEAN_SOURCES = {
    "sources": {
        "nppes": {"status": "verified"},
        "oig_leie": {"status": "clear"},
        "pecos": {"status": "verified"},
    },
}


def _results(with_signal: bool, node_type="initiating-node") -> dict:
    results = {k: ({sk: dict(sv) for sk, sv in v.items()}
                   if isinstance(v, dict) else v)
               for k, v in _CLEAN_SOURCES.items()}
    if with_signal:
        sig = _signal("T-TRTMNT", node_type=node_type)
        fields = {"active_treatment_missing_required_npi": sig}
        if sig.get("applicability_review"):
            # mirror of arc_pipeline's companion-signal wiring
            fields["treatment_npi_applicability_unresolved"] = {
                "status": True,
                "initiating_requirement": sig["initiating_requirement"],
                "exchange_direction": sig["exchange_direction"],
                "raw_org_node_type": sig["raw_org_node_type"],
            }
        results["fields"] = fields
    return results


def test_v4_classifies_b4_via_rule_006_when_initiating():
    out = BucketClassifier().classify(_results(True), rules=SEED_RULES_V4)
    assert out.bucket == "B4"
    assert out.rule_code == "RULE-006"
    assert "active_treatment_missing_required_npi" in " ".join(out.matched_conditions)


def test_v4_unknown_direction_is_explicit_b3_review_not_b4_not_b1():
    """THE applicability guard: direction-unknown case-1 record with clean
    sources classifies B3 via RULE-007 — explicitly reviewed, never B4
    (unknown is not confirmed) and never RULE-001's B1 (clean sources do not
    resolve an undecided §7 question)."""
    for node_type in (None, "", "passthrough-node"):
        out = BucketClassifier().classify(_results(True, node_type=node_type),
                                          rules=SEED_RULES_V4)
        assert out.bucket == "B3", node_type
        assert out.rule_code == "RULE-007", node_type


def test_same_input_under_v3_still_classifies_b1():
    """Merging/deploying the code is NOT activation: until ensure_rules_v4
    runs, the active rule set has no RULE-006/007 and nothing changes —
    for the initiating shape and the unknown-direction shape alike."""
    for node_type in ("initiating-node", None):
        out = BucketClassifier().classify(_results(True, node_type=node_type),
                                          rules=SEED_RULES_V3)
        assert out.bucket == "B1", node_type
        assert out.rule_code not in ("RULE-006", "RULE-007"), node_type


def test_v4_without_the_signal_matches_v3_behavior():
    v4 = BucketClassifier().classify(_results(False), rules=SEED_RULES_V4)
    v3 = BucketClassifier().classify(_results(False), rules=SEED_RULES_V3)
    assert (v4.bucket, v4.rule_code) == (v3.bucket, v3.rule_code)


def test_exclusion_disqualifier_still_leads():
    """RULE-005 (priority 5) stays ahead of RULE-006 (priority 6) and
    RULE-007 (priority 7): a debarment is the leading B4 reason."""
    for node_type in ("initiating-node", None):
        results = _results(True, node_type=node_type)
        results["sources"]["oig_leie"] = {"status": "excluded"}
        out = BucketClassifier().classify(results, rules=SEED_RULES_V4)
        assert out.bucket == "B4" and out.rule_code == "RULE-005", node_type


def test_both_v4_signals_are_registered_classifier_signals():
    from app.core.evidence_vocabulary import CLASSIFIER_SIGNAL_REGISTRY
    assert "active_treatment_missing_required_npi" in CLASSIFIER_SIGNAL_REGISTRY
    assert "treatment_npi_applicability_unresolved" in CLASSIFIER_SIGNAL_REGISTRY


# ── activation (database): explicit, idempotent, never resurrects v3 ─────────

@pytest.mark.asyncio
async def test_ensure_rules_v4_retires_v3_idempotently(rolled_back_db):
    """Activation is isolated by an outer transaction, including helper commits."""
    from sqlalchemy import delete, select

    from app.tefca_registry import models as reg
    from app.tefca_registry.bucket_classifier import (
        ensure_rules_v3, ensure_rules_v4, ensure_rules_v2, ensure_seed_rules)

    db = rolled_back_db
    # Start from a deterministic rule set INSIDE the fixture's outer transaction.
    # All original rows/fields (including v2 and pre-existing v4) are restored
    # by its rollback even if activation or a database assertion fails.
    await db.execute(delete(reg.ReviewRule))
    await ensure_seed_rules(db)
    await ensure_rules_v2(db)
    await ensure_rules_v3(db)

    added = await ensure_rules_v4(db)
    assert added == len(SEED_RULES_V4)
    # idempotent
    assert await ensure_rules_v4(db) == 0
    # the automatic chain cannot resurrect v3: existence, not activity
    assert await ensure_rules_v3(db) == 0
    v3_rows = (await db.execute(select(reg.ReviewRule).where(
        reg.ReviewRule.version == 3))).scalars().all()
    assert v3_rows and all(not r.is_active for r in v3_rows)
    v4_active = (await db.execute(select(reg.ReviewRule).where(
        reg.ReviewRule.version == 4,
        reg.ReviewRule.is_active.is_(True)))).scalars().all()
    assert {r.rule_code for r in v4_active} == {
        s["rule_code"] for s in SEED_RULES_V4}
    assert {"RULE-006", "RULE-007"} <= {r.rule_code for r in v4_active}
    # a fresh DB-backed load serves the activated set, both rules in
    loaded = await BucketClassifier().load_rules(db)
    assert {"RULE-006", "RULE-007"} <= {r["rule_code"] for r in loaded}



# ── full-population plan (database): enumeration, not a relabelled sample ────

@pytest.mark.asyncio
async def test_full_population_plan_enumerates_every_eligible_unit(db_required, monkeypatch):
    import test_sam_e2e_delivery_path as sam

    from app.core.database import async_session_maker
    from app.tefca_registry.qhin_sampling import finalize_plan

    import uuid as _uuid

    from sqlalchemy import delete

    from app.tefca_registry import models as reg

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    intake_id = await sam._seed_promoted_delivery(n=4)
    created: list = []
    async with async_session_maker() as db:
        try:
            plan = await finalize_plan(db, intake_id, full_population=True,
                                       actor="pytest-full-population")
            await db.commit()
            created.append(_uuid.UUID(plan["sample_id"]))
            assert plan["already_finalized"] is False
            assert plan["sample_size"] == plan["population_size"]
            assert plan["membership_count"] == plan["population_size"]
            assert plan["population_size"] >= 4
            assert plan["selection_algorithm"] == "FULL_POPULATION_ENUMERATION"
            assert plan["plan_key"].endswith("|full_population")
            # frozen and idempotent, like any official plan
            again = await finalize_plan(db, intake_id, full_population=True,
                                        actor="pytest-full-population")
            assert again["already_finalized"] is True
            assert again["sample_id"] == plan["sample_id"]
            # ...and it never collides with a STATISTICAL plan drawn with the
            # same parameters: that one is its own, different sample.
            statistical = await finalize_plan(db, intake_id,
                                              actor="pytest-full-population")
            await db.commit()
            created.append(_uuid.UUID(statistical["sample_id"]))
            assert statistical["sample_id"] != plan["sample_id"]
        finally:
            # The shared test database serves suites that assert EMPTY
            # ReviewSample/SampleEntity baselines; committed plans must go.
            await db.execute(delete(reg.SampleEntity)
                             .where(reg.SampleEntity.sample_id.in_(created)))
            await db.execute(delete(reg.ReviewSample)
                             .where(reg.ReviewSample.id.in_(created)))
            await db.commit()
