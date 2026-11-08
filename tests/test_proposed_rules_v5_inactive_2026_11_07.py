"""The 2026-11-07/08 PROPOSED configuration is implemented, previewable — and
INACTIVE, as named composable components.

Step 2C superseded the monolithic v5 draft: proposals are now COMPONENTS in
`app.tefca_registry.rule_configuration`, each tied to its own decision
(D-01 split into review-B3 vs B4, D-02, D-03/M-4, D-04, D-05, EX-3/D-16),
composed over the deployed v3 baseline and activatable ONLY through one
governed mechanism that validates recorded governance approvals. Producer
behaviors stay behind default-off env flags covered by the same components.
The M-1..M-4 enforcement hook is inactive BY DATA (no active policy row).

Every test protects one of two claims:
  1. DEFAULT-OFF: with no component activated, no flag set and no policy row,
     behavior is byte-identical to today.
  2. PROPOSED: each component does exactly what its decision row says.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, timedelta

import pytest

from app.tefca_registry.bucket_classifier import SEED_RULES_V3, BucketClassifier
from app.tefca_registry.rule_configuration import (
    APPROVAL_EVENT_TYPE,
    COMPONENTS,
    ActivationRefused,
    activate_rule_configuration,
    compose,
)
from app.tefca_registry.rce.arc_pipeline import (
    EXCLUSION_DIMENSION,
    EXCLUSION_POSSIBLE_MATCH_STATE,
    PROPOSED_EXCLUSION_STATES_ENV,
    PROPOSED_NAME_MAJOR_BELOW_ENV,
    PROPOSED_NPI_INVALID_SPLIT_ENV,
    dimensions_to_verification_results,
    evidence_item_state,
    proposed_structural_npi_signal,
)
from app.tefca_registry.rce.quality_engine import (
    PROPOSED_ASSESSMENT_AS_OF_ENV,
    _proposed_assessment_as_of,
)
from app.tefca_registry.rce import source_policy as sp
from app.tefca_registry.review_cycle import (
    PROPOSED_VERIFY_SKIP_HELD_ENV,
    _proposed_verify_skip_held,
)
from rce_traceability_support import rolled_back_db  # noqa: F401 — DB fixture

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

GUARD_COMPONENTS = {"structural_npi_handling", "mandatory_source_guards",
                    "material_conflict_guards"}


def _by_code(rules):
    return {r["rule_code"]: r for r in rules}


def _classify(rules, results):
    return BucketClassifier(
        rules=[{**r, "version": 99} for r in rules]).classify(results)


def _base_clean():
    return {"sources": {"nppes": "verified", "oig_leie": "clear",
                        "pecos": "verified"}, "fields": {}}


# ── 1. composition is exactly the documented deltas ──────────────────────────

def test_compose_empty_selection_is_the_deployed_baseline():
    assert compose(set()) == SEED_RULES_V3


def test_guard_components_only_append_none_of_guards():
    v3 = _by_code(SEED_RULES_V3)
    composed = _by_code(compose(GUARD_COMPONENTS))
    assert set(composed) == set(v3)
    for code in v3:
        # Positive clauses untouched: guards can only EXCLUDE.
        for clause in ("all_of", "any_of", "any_unavailable"):
            assert (v3[code]["conditions"].get(clause)
                    == composed[code]["conditions"].get(clause)), (code, clause)
        old = v3[code]["conditions"].get("none_of", [])
        new = composed[code]["conditions"].get("none_of", [])
        assert new[: len(old)] == old, f"{code}: guards must only APPEND"


def test_treatment_review_b3_maps_rule006_to_b3_with_candidate_name():
    composed = _by_code(compose({"treatment_review_b3"}))
    assert composed["RULE-006"]["bucket"] == "B3"
    assert "B4 candidate" in composed["RULE-006"]["name"]
    assert composed["RULE-007"]["bucket"] == "B3"


def test_treatment_b4_requires_its_dependency_and_upgrades_the_bucket():
    with pytest.raises(ActivationRefused):
        compose({"treatment_b4"})
    composed = _by_code(compose({"treatment_review_b3", "treatment_b4"}))
    assert composed["RULE-006"]["bucket"] == "B4"


def test_exclusion_candidate_component_rewires_pending_to_b3():
    composed = _by_code(compose({"exclusion_candidate_b3"}))
    # The v3 PENDING not_found disqualifiers are gone from RULE-005...
    pending = [c for c in composed["RULE-005"]["conditions"]["any_of"]
               if c.get("status") == "not_found"]
    assert pending == []
    # ...confirmed-exclusion literals remain...
    assert {("oig_leie", "excluded"), ("sam_gov", "debarred"),
            ("sam_gov", "excluded")} <= {
        (c.get("source"), c.get("status"))
        for c in composed["RULE-005"]["conditions"]["any_of"]}
    # ...and RULE-008 consumes the proposed candidate state.
    assert composed["RULE-008"]["bucket"] == "B3"
    assert composed["RULE-008"]["priority"] == 8


def test_unknown_component_is_refused():
    with pytest.raises(ActivationRefused):
        compose({"no_such_component"})


# ── 2. nothing activates anything ────────────────────────────────────────────

def test_nothing_in_app_calls_the_activation_mechanism():
    """`activate_rule_configuration` must be invoked by NO application code
    path — running it is the governed Gate-C step. (v4's audited admin route
    remains the reviewed treatment-pair special case.)"""
    offenders = []
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if ("activate_rule_configuration(" in text
                and py.name != "rule_configuration.py"):
            offenders.append(str(py))
    assert not offenders, f"must stay uncalled: {offenders}"


@pytest.mark.asyncio
async def test_activation_refuses_arbitrary_strings_and_unrecorded_refs(
        rolled_back_db):
    db = rolled_back_db
    with pytest.raises(ActivationRefused, match="not a governance-record id"):
        await activate_rule_configuration(
            db, {"mandatory_source_guards": "APPROVED-TRUST-ME"})
    with pytest.raises(ActivationRefused, match="no methodology_approval"):
        await activate_rule_configuration(
            db, {"mandatory_source_guards": str(uuid.uuid4())})


@pytest.mark.asyncio
async def test_activation_validates_the_governance_record_names_the_component(
        rolled_back_db):
    from app.tefca_registry import models as reg
    db = rolled_back_db
    row = reg.TefcaRegAuditLog(
        id=uuid.uuid4(), action=APPROVAL_EVENT_TYPE,
        actor_email="owner@example.test",
        metadata_={"components": ["material_conflict_guards"],
                   "decision": "D-04", "synthetic_test_record": True})
    db.add(row)
    await db.flush()
    # The record names D-04's component, not D-03's — refused.
    with pytest.raises(ActivationRefused, match="does not name this component"):
        await activate_rule_configuration(
            db, {"mandatory_source_guards": str(row.id)})


@pytest.mark.asyncio
async def test_activation_with_validated_approvals_activates_one_lineage(
        rolled_back_db):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    db = rolled_back_db
    row = reg.TefcaRegAuditLog(
        id=uuid.uuid4(), action=APPROVAL_EVENT_TYPE,
        actor_email="owner@example.test",
        metadata_={"components": ["mandatory_source_guards",
                                  "material_conflict_guards"],
                   "synthetic_test_record": True})
    db.add(row)
    await db.flush()
    out = await activate_rule_configuration(
        db, {"mandatory_source_guards": str(row.id),
             "material_conflict_guards": str(row.id)},
        actor="test")
    active = (await db.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.is_active.is_(True),
                                     reg.ReviewRule.version == out["version"])
    )).scalars().all()
    assert len(active) == out["rules"] == len(SEED_RULES_V3)
    assert all("components:" in (r.description or "") for r in active)


# ── 3. proposed rule behavior, measured on the composed sets ─────────────────

def test_d03_failed_mandatory_source_blocks_partial_pass():
    results = {"sources": {"nppes": "verified", "oig_leie": "clear",
                           "pecos": "failed"}, "fields": {}}
    assert _classify(SEED_RULES_V3, results).bucket == "B1"
    out = _classify(compose({"mandatory_source_guards"}), results)
    assert out.bucket == "B3"


def test_d04_material_conflicts_demote_b1_and_b2():
    guards = compose({"material_conflict_guards"})
    results = _base_clean()
    results["fields"]["name_mismatch"] = {"severity": "major"}
    assert _classify(SEED_RULES_V3, results).bucket == "B1"
    assert _classify(guards, results).bucket == "B3"

    results = _base_clean()
    results["fields"]["name_mismatch"] = {"severity": "minor"}
    results["fields"]["address_mismatch"] = {"severity": "major"}
    assert _classify(SEED_RULES_V3, results).bucket == "B1"  # first-match defect
    assert _classify(guards, results).bucket == "B3"


def test_d02_invalid_npi_is_b4_and_guarded():
    results = _base_clean()
    results["fields"]["npi_validation"] = {"status": "invalid"}
    assert _classify(SEED_RULES_V3, results).bucket == "B4"
    assert _classify(compose({"structural_npi_handling"}), results).bucket == "B4"


def test_ex3_candidate_is_b3_pending_and_confirmed_stays_b4():
    composed = compose({"exclusion_candidate_b3"})
    candidate = {"sources": {"nppes": "verified", "oig_leie": "clear",
                             "pecos": "verified",
                             "sam_gov": EXCLUSION_POSSIBLE_MATCH_STATE},
                 "fields": {}}
    out = _classify(composed, candidate)
    assert (out.bucket, out.rule_code) == ("B3", "RULE-008")

    confirmed = {"sources": {"nppes": "verified", "oig_leie": "excluded"},
                 "fields": {}}
    assert _classify(composed, confirmed).bucket == "B4"
    # The deployed baseline still disqualifies a pending not_found — the
    # conflict this component exists to resolve:
    pending_v3 = {"sources": {"nppes": "verified", "oig_leie": "clear",
                              "sam_gov": "not_found"}, "fields": {}}
    assert _classify(SEED_RULES_V3, pending_v3).bucket == "B4"
    # Under the component, a stray exclusion not_found is review, not B4 and
    # not a pass:
    assert _classify(composed, pending_v3).bucket == "B3"


EQUIVALENCE_BATTERY = [
    ("all clean", _base_clean()),
    ("partial pass: pecos not_checked",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "not_checked"}, "fields": {}}),
    ("partial pass: pecos unavailable",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "unavailable"}, "fields": {}}),
    ("minor name variance",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "verified"},
      "fields": {"name_mismatch": {"severity": "minor"}}}),
    ("LEIE excluded",
     {"sources": {"nppes": "verified", "oig_leie": "excluded",
                  "pecos": "verified"}, "fields": {}}),
    ("pending exclusion review (not_found)",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "sam_gov": "not_found"}, "fields": {}}),
    ("NPPES not found",
     {"sources": {"nppes": "not_found", "oig_leie": "clear"}, "fields": {}}),
    ("flagged NPI",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "verified"},
      "fields": {"npi_validation": {"status": "flagged"}}}),
    ("nothing checked", {"sources": {}, "fields": {}}),
]


@pytest.mark.parametrize("desc,results", EQUIVALENCE_BATTERY,
                         ids=[d for d, _ in EQUIVALENCE_BATTERY])
def test_guard_components_equal_v3_when_no_proposed_signal_is_present(
        desc, results):
    """Flags-off equivalence for the guard components: on every input carrying
    no proposed signal (no FAILED mandatory source, no major conflict, no
    invalid npi, no possible_match), the composed guards reproduce the
    deployed baseline byte-for-byte."""
    v3 = _classify(SEED_RULES_V3, results)
    composed = _classify(compose(GUARD_COMPONENTS), results)
    assert (v3.bucket, v3.rule_code) == (composed.bucket, composed.rule_code), desc


def test_name_major_guard_is_disclosed_not_hidden():
    from app.core.vocabulary_contract import READY, condition_readiness
    rows = [r for r in condition_readiness(compose({"material_conflict_guards"}))
            if r.signal == "name_mismatch" and r.expected_value == "major"]
    assert rows and all(r.status != READY for r in rows)


# ── 4. proposed producers, flag-gated, default OFF ───────────────────────────

def test_exclusion_state_translator_is_flag_gated(monkeypatch):
    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    assert evidence_item_state(EXCLUSION_DIMENSION, "REVIEW") == "not_found"
    assert evidence_item_state(EXCLUSION_DIMENSION, "NOT_FOUND") == "clear"
    monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")
    assert (evidence_item_state(EXCLUSION_DIMENSION, "REVIEW")
            == EXCLUSION_POSSIBLE_MATCH_STATE)
    assert evidence_item_state(EXCLUSION_DIMENSION, "NOT_FOUND") == "clear"
    # Non-exclusion dimensions are untouched by the flag.
    assert evidence_item_state("IDENTITY", "REVIEW") == "not_found"


def _identity_evidence(similarity):
    return {"dimensions": [{
        "dimension": "IDENTITY", "disposition": "REVIEW",
        "applicability": "APPLICABLE",
        "evidence": [{
            "source": "NPPES", "disposition": "PASS",
            "field_conflicts": [{"field": "legal_name", "submitted": "A",
                                 "nppes": "B", "result": "DIFFERS",
                                 "similarity": similarity}],
        }],
    }]}


def test_d05_default_severity_stays_hardcoded_minor(monkeypatch):
    monkeypatch.delenv(PROPOSED_NAME_MAJOR_BELOW_ENV, raising=False)
    out = dimensions_to_verification_results(_identity_evidence(0.12))
    assert out["fields"]["name_mismatch"] == {"severity": "minor"}


def test_d05_band_from_env_grades_major_below(monkeypatch):
    monkeypatch.setenv(PROPOSED_NAME_MAJOR_BELOW_ENV, "0.5")
    assert dimensions_to_verification_results(
        _identity_evidence(0.32))["fields"]["name_mismatch"] == {"severity": "major"}
    assert dimensions_to_verification_results(
        _identity_evidence(0.5))["fields"]["name_mismatch"] == {"severity": "minor"}
    assert dimensions_to_verification_results(
        _identity_evidence(None))["fields"]["name_mismatch"] == {"severity": "minor"}


@pytest.mark.parametrize("bad", ["abc", "-0.1", "1.5", ""])
def test_d05_invalid_band_means_grading_stays_off(monkeypatch, bad):
    monkeypatch.setenv(PROPOSED_NAME_MAJOR_BELOW_ENV, bad)
    out = dimensions_to_verification_results(_identity_evidence(0.01))
    assert out["fields"]["name_mismatch"] == {"severity": "minor"}


def test_d02_default_structural_npi_stays_flagged(monkeypatch):
    monkeypatch.delenv(PROPOSED_NPI_INVALID_SPLIT_ENV, raising=False)
    out = dimensions_to_verification_results(
        {"dimensions": [], "data_quality_flags": ["NPI_CHECK_DIGIT_FAILED"]})
    assert out["fields"]["npi_validation"] == {"status": "flagged"}


def test_d02_split_emits_invalid_for_structurally_impossible(monkeypatch):
    monkeypatch.setenv(PROPOSED_NPI_INVALID_SPLIT_ENV, "1")
    out = dimensions_to_verification_results(
        {"dimensions": [], "data_quality_flags": ["NPI_CHECK_DIGIT_FAILED"]})
    assert out["fields"]["npi_validation"] == {"status": "invalid"}


def test_structural_npi_signal_separates_np1_from_np3():
    f = proposed_structural_npi_signal
    assert f(None) is None and f("") is None and f("   ") is None
    multi = f("1780787176, 1770559767")
    assert multi == {"status": "flagged", "basis": "MULTIPLE_NPI_IN_ONE_FIELD",
                     "raw_npi": "1780787176, 1770559767"}
    assert f("123")["status"] == "invalid"
    assert f("12345678ab")["status"] == "invalid"
    assert f("1234567890") == {"status": "invalid",
                               "basis": "NPI_CHECKSUM_INVALID",
                               "raw_npi": "1234567890"}
    assert f("1234567893") is None


def test_structural_npi_signal_agrees_with_the_quality_engine_validator():
    from app.services.npi_validator import validate_npi
    for value in ("1234567893", "1234567890", "1780787176"):
        ok, _ = validate_npi(value)
        assert (proposed_structural_npi_signal(value) is None) == ok, value


def test_treatment_reason_codes_are_mutually_exclusive_and_producer_emitted():
    from app.tefca_registry.rce.npi_applicability import (
        active_treatment_missing_required_npi as sig)
    from app.tefca_registry.rce.quality_rules import BR_NPI_TRTMNT_EFFECTIVE

    def run(node_type):
        return sig("T-TRTMNT", None, "1",
                   assessment_date=date(2026, 11, 7),
                   requirement_effective=BR_NPI_TRTMNT_EFFECTIVE,
                   org_node_type=node_type)

    assert run("initiating-node")["reason_code"] == "TREATMENT_NPI_MISSING_B4_CANDIDATE"
    assert run(None)["reason_code"] == "TREATMENT_APPLICABILITY_PRESUMED_NPI_MISSING"
    assert run("")["reason_code"] == "TREATMENT_APPLICABILITY_PRESUMED_NPI_MISSING"
    assert run("no-node")["reason_code"] == "TREATMENT_NODE_TYPE_REVIEW"
    assert run("passthrough-node")["reason_code"] == "TREATMENT_NODE_TYPE_REVIEW"
    assert sig("T-TREAT", None, "1", assessment_date=date(2026, 11, 7),
               requirement_effective=BR_NPI_TRTMNT_EFFECTIVE,
               org_node_type="initiating-node") is None


# ── 5. D-12 pinned assessment date — reader semantics ────────────────────────

def test_d12_env_reader_accepts_only_a_past_or_today_iso_date(monkeypatch):
    monkeypatch.delenv(PROPOSED_ASSESSMENT_AS_OF_ENV, raising=False)
    assert _proposed_assessment_as_of() is None
    monkeypatch.setenv(PROPOSED_ASSESSMENT_AS_OF_ENV, "2026-10-14")
    assert _proposed_assessment_as_of() == date(2026, 10, 14)
    monkeypatch.setenv(PROPOSED_ASSESSMENT_AS_OF_ENV, "not-a-date")
    assert _proposed_assessment_as_of() is None
    monkeypatch.setenv(PROPOSED_ASSESSMENT_AS_OF_ENV,
                       (date.today() + timedelta(days=1)).isoformat())
    assert _proposed_assessment_as_of() is None


# ── 6. D-15 verify-skip-HELD ─────────────────────────────────────────────────

def test_d15_flag_default_off(monkeypatch):
    monkeypatch.delenv(PROPOSED_VERIFY_SKIP_HELD_ENV, raising=False)
    assert _proposed_verify_skip_held() is False
    monkeypatch.setenv(PROPOSED_VERIFY_SKIP_HELD_ENV, "1")
    assert _proposed_verify_skip_held() is True


@pytest.mark.asyncio
async def test_d15_held_entity_ids_reads_live_record_status(rolled_back_db):
    from test_post_promotion_verification import _promoted
    from app.tefca_registry.review_cycle import _held_entity_ids

    db = rolled_back_db
    rows, intake_id, job, curated, promo = await _promoted(db, "D15")
    entity_id = curated.canonical_entity_id
    assert curated.record_status in ("CLEAN", "CORRECTED")
    assert await _held_entity_ids(db, intake_id, [entity_id]) == set()
    curated.record_status = "HELD"
    await db.flush()
    assert await _held_entity_ids(db, intake_id, [entity_id]) == {entity_id}
    assert await _held_entity_ids(db, intake_id, []) == set()
    assert await _held_entity_ids(db, uuid.uuid4(), [entity_id]) == set()


# ── 7. M-1..M-4 policy hook — inactive by data ───────────────────────────────

def test_policy_hook_is_a_noop_without_policies():
    assert sp.apply_source_policy("B1", "RULE-001", {}, {}, []) == (
        "B1", "RULE-001", None, None)
    assert sp.apply_source_policy(
        "B4", "RULE-005", {"sam_gov": "not_checked"},
        {}, [{"source": "sam_gov", "role": "mandatory", "applicability": {}}]
    )[0] == "B4"  # only B1/B2 are ever reconsidered


def test_policy_mandatory_incomplete_blocks_b1_with_named_reason():
    pol = [{"id": "p-1", "version": 7, "source": "oig_leie",
            "role": "mandatory", "applicability": {}}]
    for state in ("not_checked", "unavailable", "failed", None):
        sources = {"oig_leie": state} if state else {}
        b, r, reason, matched = sp.apply_source_policy(
            "B1", "RULE-001", sources, {}, pol)
        assert (b, reason) == ("B3", "SOURCE_NOT_COMPLETED_OIG_LEIE"), state
        assert matched["version"] == 7, "the ACTUAL policy row is cited"
    b, r, reason, matched = sp.apply_source_policy(
        "B1", "RULE-001", {"oig_leie": "clear"}, {}, pol)
    assert (b, reason, matched) == ("B1", None, None)


def test_policy_answered_adverse_is_never_relabelled_incomplete():
    pol = [{"source": "pecos", "role": "mandatory", "applicability": {}}]
    b, r, reason, matched = sp.apply_source_policy(
        "B2", "RULE-003", {"pecos": "not_found"}, {}, pol)
    assert reason is None and b == "B2"  # the FINDING is the rules' business


def test_policy_pending_candidate_gets_the_specific_review_reason():
    pol = [{"source": "sam_gov", "role": "mandatory", "applicability": {}}]
    b, r, reason, matched = sp.apply_source_policy(
        "B1", "RULE-001", {"sam_gov": "possible_match"}, {}, pol)
    assert (b, reason) == ("B3", "EXCLUSION_POSSIBLE_MATCH_PENDING")
    assert matched is not None


def test_policy_conditional_and_advisory_roles():
    cond = [{"source": "nppes", "role": "conditional",
             "applicability": {"requires_npi": True}}]
    b, _, reason, _m = sp.apply_source_policy(
        "B1", "RULE-001", {}, {"has_submitted_npi": False}, cond)
    assert reason is None, "conditional outside applicability never blocks"
    b, _, reason, _m = sp.apply_source_policy(
        "B1", "RULE-001", {}, {"has_submitted_npi": True}, cond)
    assert reason == "SOURCE_NOT_COMPLETED_NPPES"
    # A conditional policy with NO stated facts applies nowhere (no default).
    b, _, reason, _m = sp.apply_source_policy(
        "B1", "RULE-001", {}, {"has_submitted_npi": True},
        [{"source": "nppes", "role": "conditional", "applicability": {}}])
    assert reason is None
    # Advisory never blocks.
    b, _, reason, _m = sp.apply_source_policy(
        "B1", "RULE-001", {}, {},
        [{"source": "iqvia", "role": "advisory", "applicability": {}}])
    assert reason is None


@pytest.mark.asyncio
async def test_policy_storage_ships_empty_and_inactive(rolled_back_db):
    from app.tefca_registry.rce import models as m
    db = rolled_back_db
    assert await sp.load_active_policies(db) == []
    db.add(m.RceSourcePolicy(
        id=uuid.uuid4(), version=1, source="oig_leie", role="mandatory",
        applicability={}, policy_reference="D-11 Part 2 (test row)",
        approval_reference=uuid.uuid4(), is_active=False))
    await db.flush()
    assert await sp.load_active_policies(db) == [], \
        "an inactive row must never be enforced"


# ── 8. D-01 SUPERSEDING (2026-11-08): owner-adopted Treatment policy ─────────

def test_owner_adopted_component_is_a_new_version_of_rule006_itself():
    """The superseding rule keeps rule_code RULE-006 (versioned-rules design:
    the historical initiating-gated definition survives as the prior version)
    and RULE-007 is left unselected — no independent coverage remains for its
    unresolved-applicability condition under the owner rule."""
    composed = compose({"treatment_b4_owner_adopted"})
    codes = [r["rule_code"] for r in composed]
    assert codes.count("RULE-006") == 1
    assert "RULE-007" not in codes and "RULE-009" not in codes


def test_owner_adopted_component_classifies_b4_without_node_type_gate():
    composed = compose({"treatment_b4_owner_adopted"})
    results = _base_clean()
    results["fields"]["treatment_owner_adopted_missing_npi"] = {"status": True}
    out = _classify(composed, results)
    assert (out.bucket, out.rule_code) == ("B4", "RULE-006")
    assert "owner-adopted" in out.rule_name
    # Higher-priority approved conditions still win (RULE-005 at priority 5).
    results["sources"]["oig_leie"] = "excluded"
    out = _classify(composed, results)
    assert (out.bucket, out.rule_code) == ("B4", "RULE-005")
    # Without the owner condition, nothing new fires.
    clean = _base_clean()
    assert _classify(composed, clean).bucket == "B1"


def test_owner_adopted_conflicts_with_config_a_components():
    with pytest.raises(ActivationRefused, match="incompatible"):
        compose({"treatment_b4_owner_adopted", "treatment_review_b3"})
    with pytest.raises(ActivationRefused, match="incompatible"):
        compose({"treatment_b4_owner_adopted", "treatment_review_b3",
                 "treatment_b4"})


def test_owner_adopted_signal_condition_is_submitted_values_only():
    """The producer condition is exactly the gated signal's existence: active
    + exact T-TRTMNT token (any combination) + blank SUBMITTED NPI + the
    requirement in force — node type irrelevant, T-TREAT never matches,
    inactive records never match."""
    from app.tefca_registry.rce.npi_applicability import (
        active_treatment_missing_required_npi as sig)
    from app.tefca_registry.rce.quality_rules import BR_NPI_TRTMNT_EFFECTIVE

    def exists(purposes, npi, active, node):
        return sig(purposes, npi, active, assessment_date=date(2026, 11, 8),
                   requirement_effective=BR_NPI_TRTMNT_EFFECTIVE,
                   org_node_type=node) is not None

    assert exists("T-TRTMNT", None, "1", None)
    assert exists("T-TRTMNT,T-HCO-OPS", "", "1", "passthrough-node")
    assert exists("T-TRTMNT", None, "1", "initiating-node")
    assert not exists("T-TREAT", None, "1", "initiating-node")
    assert not exists("T-TRTMNT", "1234567893", "1", None)   # NPI submitted
    assert not exists("T-TRTMNT", None, "0", None)           # inactive


# ── 9. exact-token membership across combinations; inactive observation ──────

def test_exact_token_membership_across_combinations_never_whole_cell():
    """The parser splits the multi-value field and tests exact membership —
    never whole-cell equality, never a hardcoded combination list. T-TREAT
    alone is distinct; T-TREAT combined with T-TRTMNT does not exempt."""
    from app.tefca_registry.rce.npi_applicability import assign_case

    for purposes in ("T-TRTMNT",
                     "T-TRTMNT,T-HCO-OPS",
                     "T-HCO-OPS; T-TRTMNT | T-PH",
                     "T-TREAT, T-TRTMNT",
                     "T-TRTMNT T-TREAT"):
        a = assign_case(purposes, None)
        assert a.case == 1 and a.has_trtmnt, purposes
    for purposes in ("T-TREAT", "T-TRTMNTX", "XT-TRTMNT", "t-trtmnt", "TRTMNT"):
        assert not assign_case(purposes, None).has_trtmnt, purposes


def test_inactive_treatment_observation_is_emitted_separately():
    """Inactive + exact token + blank NPI -> INACTIVE_TREATMENT_NPI_MISSING
    observation, consumed by no rule; active records get the owner signal
    instead; neither leaks into the other population."""
    import asyncio as _a  # noqa: F401 (parity with file style)
    from app.tefca_registry.rce.field_map import normalize_active
    from app.tefca_registry.rce.npi_applicability import assign_case

    # The producer condition, mirrored: case 1 + not active.
    assert assign_case("T-TRTMNT,T-HCO-OPS", None).case == 1
    assert normalize_active("0") != "1"
    # Rule-level: the observation signal triggers nothing in any composition.
    composed = compose({"treatment_b4_owner_adopted"})
    results = _base_clean()
    results["fields"]["inactive_treatment_npi_missing"] = {"status": True}
    out = _classify(composed, results)
    assert out.bucket == "B1", "the observation must never gate or grant a bucket"


# ── 10. 2026-11-08 review findings — regression pins ─────────────────────────

@pytest.mark.asyncio
async def test_activation_refuses_empty_selection_and_future_effective(
        rolled_back_db):
    """Both refusals must fire BEFORE any rule or audit write: the active rule
    rows and the audit trail are byte-identical after a refused call."""
    from datetime import datetime as _dt
    from sqlalchemy import func as safunc, select
    from app.tefca_registry import models as reg
    db = rolled_back_db

    async def _state():
        rules = (await db.execute(
            select(reg.ReviewRule.id, reg.ReviewRule.is_active,
                   reg.ReviewRule.retired_date)
            .order_by(reg.ReviewRule.id))).all()
        audits = int((await db.execute(
            select(safunc.count()).select_from(reg.TefcaRegAuditLog))).scalar() or 0)
        return rules, audits

    before = await _state()
    with pytest.raises(ActivationRefused, match="empty selection"):
        await activate_rule_configuration(db, {})
    assert await _state() == before, "a refusal must change nothing"

    row = reg.TefcaRegAuditLog(
        id=uuid.uuid4(), action=APPROVAL_EVENT_TYPE,
        actor_email="owner@example.test",
        metadata_={"components": ["mandatory_source_guards"],
                   "synthetic_test_record": True})
    db.add(row)
    await db.flush()
    before = await _state()
    with pytest.raises(ActivationRefused, match="future"):
        await activate_rule_configuration(
            db, {"mandatory_source_guards": str(row.id)},
            effective=_dt.utcnow().date() + timedelta(days=1))
    assert await _state() == before,         "a future-date refusal must retire nothing and audit nothing"


def test_policy_determination_cites_its_own_code_never_default_unmatched():
    assert sp.SOURCE_POLICY_RULE_CODE == "SOURCE-POLICY-M4"
    # The reserved code must never collide with a review_rules code.
    from app.tefca_registry.rule_configuration import COMPONENTS, compose
    all_codes = {r["rule_code"] for r in compose(
        set(COMPONENTS) - {"treatment_review_b3", "treatment_b4"})}
    assert sp.SOURCE_POLICY_RULE_CODE not in all_codes


@pytest.mark.asyncio
async def test_d15_skipped_members_leave_the_selectable_set(rolled_back_db):
    """Review Major (2026-11-08): a skipped-held member must stop being
    selectable (else `remaining` never reaches 0 and a held-first batch
    stalls), while staying UNLINKED (no review case invented for it)."""
    from app.tefca_registry import models as reg
    from app.tefca_registry.review_cycle import (SKIPPED_HELD_STATUS,
                                                 _unlinked_members)
    db = rolled_back_db
    sample_id = uuid.uuid4()
    db.add(reg.ReviewSample(
        id=sample_id, review_type="QHIN", population_size=2, sample_size=2,
        confidence_level=0.95, margin_of_error=0.05, proportion=0.5,
        strata_config={"synthetic_test": True}))
    def _entity(eid):
        return reg.TefcaRegEntity(
            id=eid, name=f"SYNTHETIC-D15-{eid}", display_name="SYNTHETIC-D15",
            entity_level="participant", entity_type="healthcare_provider",
            operational_status="active", verification_status="not_verified",
            current_version=1, is_active=True)

    a, b = uuid.uuid4(), uuid.uuid4()
    for eid in (a, b):
        db.add(_entity(eid))
    await db.flush()
    for eid in (a, b):
        db.add(reg.SampleEntity(id=uuid.uuid4(), sample_id=sample_id,
                                entity_id=eid, stratum="S1"))
    await db.flush()

    members = await _unlinked_members(db, sample_id)
    assert {m.entity_id for m in members} == {a, b}

    held = next(m for m in members if m.entity_id == a)
    held.review_status = SKIPPED_HELD_STATUS
    await db.flush()

    members = await _unlinked_members(db, sample_id)
    assert {m.entity_id for m in members} == {b}, \
        "a skipped_held member must not be re-selected"
    assert held.review_id is None, "skipped means UNLINKED, never a fake case"

    # HELD > batch: add three more skipped_held members - whatever the batch
    # size, the selectable set is exactly the one live member, so a
    # held-majority sample can never stall the cycle.
    extra = [uuid.uuid4() for _ in range(3)]
    for eid in extra:
        db.add(_entity(eid))
    await db.flush()
    for eid in extra:
        db.add(reg.SampleEntity(id=uuid.uuid4(), sample_id=sample_id,
                                entity_id=eid, stratum="S0",
                                review_status=SKIPPED_HELD_STATUS))
    await db.flush()
    members = await _unlinked_members(db, sample_id)
    assert {m.entity_id for m in members} == {b}
    assert len(members[:2]) == 1  # any batch slice sees only live members


def test_loader_and_activation_share_the_utc_date_convention():
    """Review finding 2026-11-08 (round 3): activate_rule_configuration stamps
    effective/retired dates with the UTC date, so BucketClassifier.load_rules
    must FILTER with the UTC date too. With a local-date filter, a worker whose
    local date trails UTC would watch a governed activation retire the old rows
    (is_active=False, immediate) while the new rows' UTC effective date is
    still "tomorrow" locally -- no applicable rules until local midnight.
    Source-shape pin (the behavior needs a movable clock to prove end-to-end;
    deployed containers run in UTC, where the two calls are identical).
    """
    import inspect

    from app.tefca_registry import bucket_classifier
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from app.tefca_registry.rule_configuration import activate_rule_configuration

    loader_src = inspect.getsource(BucketClassifier.load_rules)
    assert ".now(_tz.utc).date()" in loader_src
    assert ".now(_tz.utc).date()" in inspect.getsource(activate_rule_configuration)
    # Round 4 widened the pin to the SEED producers (a UTC-ahead host's
    # local-date default would stamp tomorrow's UTC date, hiding the fresh
    # seed from the loader until UTC midnight): no local-date call may
    # remain anywhere in the module.
    assert "date.today()" not in inspect.getsource(bucket_classifier), (
        "bucket_classifier must use one date convention — UTC — in the "
        "loader filter AND every seed default, matching activation's stamps")
