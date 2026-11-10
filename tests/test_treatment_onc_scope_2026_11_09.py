"""ONC updated Treatment scope (relayed 2026-11-09) — implemented, INACTIVE.

The ONC-communicated correction to the Treatment missing-NPI rule: all of
  - Purpose_of_Use contains the exact token T-TRTMNT as its SOLE purpose
    (combined-purpose values such as "T-PH-ECR,T-TRTMNT" are out of scope;
    T-TREAT is never a substitute);
  - participation level (sequoiaorgtype) is Participant or Sub-Participant;
  - Activity Status is Active;
  - the ORIGINAL SUBMITTED NPI is blank.
No initiating-node requirement. A supplemental NPI (manual QA, NPPES, IQVIA)
never fills the submitted blank and never suppresses the signal. Records
outside the scope are evaluated against the remaining applicable rules —
never auto-routed to B3. Implemented as the component `treatment_b4_onc_scope`
(a NEW VERSION of RULE-006; the owner-adopted definition is preserved as the
prior version), INACTIVE until activated under its recorded approval.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.tefca_registry.bucket_classifier import SEED_RULES_V3, BucketClassifier
from app.tefca_registry.rule_configuration import (
    COMPONENTS,
    ActivationRefused,
    compose,
)
from app.tefca_registry.rce.npi_applicability import (
    normalize_participation_level,
    treatment_onc_scope_missing_npi,
)
from app.tefca_registry.rce.quality_rules import BR_NPI_TRTMNT_EFFECTIVE

ASSESS = date(2026, 11, 8)   # D-12 assessment date used by the analysis runs
SELECTION = {"treatment_b4_onc_scope", "structural_npi_handling",
             "mandatory_source_guards", "exclusion_candidate_b3"}


def _sig(purposes, npi, active, participation):
    return treatment_onc_scope_missing_npi(
        purposes, npi, active, participation,
        assessment_date=ASSESS, requirement_effective=BR_NPI_TRTMNT_EFFECTIVE)


def _classify(rules, results):
    return BucketClassifier(
        rules=[{**r, "version": 99} for r in rules]).classify(results)


def _base_clean():
    return {"sources": {"nppes": "verified", "oig_leie": "clear",
                        "pecos": "verified"}, "fields": {}}


# ── 1. producer: the four ONC conditions, each tested both ways ──────────────

def test_sole_purpose_in_scope_combined_out_of_scope():
    """Exact T-TRTMNT as the sole purpose matches; any combined-purpose value
    does not — including the literal example from the ONC scope."""
    assert _sig("T-TRTMNT", None, "1", "Participant") is not None
    assert _sig(" T-TRTMNT ", "", "1", "Subparticipant") is not None
    for combined in ("T-PH-ECR,T-TRTMNT", "T-TRTMNT,T-PH-ECR",
                     "T-GOVDTRM-ACP,T-TRTMNT", "T-IAS,T-TRTMNT",
                     "T-GOVDTRM-ACP,T-PH-ECR,T-TRTMNT", "T-TREAT,T-TRTMNT"):
        assert _sig(combined, None, "1", "Participant") is None, combined


def test_t_treat_is_never_a_substitute():
    """T-TREAT alone, or any casing/substring variant of the token, never
    matches."""
    for purposes in ("T-TREAT", "t-trtmnt", "T-TRTMNTX", "XT-TRTMNT", "TRTMNT",
                     "", None):
        assert _sig(purposes, None, "1", "Participant") is None, purposes


def test_participation_levels_participant_and_sub_participant_only():
    """Both delivered levels match (tolerating form variants); blank or
    out-of-vocabulary levels are out of scope, never presumed."""
    for level in ("Participant", "participant", "PARTICIPANT",
                  "Subparticipant", "Sub-Participant", "sub_participant",
                  "SUB PARTICIPANT"):
        assert _sig("T-TRTMNT", None, "1", level) is not None, level
    for level in (None, "", "QHIN", "Principal Node", "Sub", "Participants"):
        assert _sig("T-TRTMNT", None, "1", level) is None, level


def test_normalize_participation_level_vocabulary():
    assert normalize_participation_level("Participant") == "participant"
    assert normalize_participation_level("Sub-Participant") == "sub_participant"
    assert normalize_participation_level("Subparticipant") == "sub_participant"
    assert normalize_participation_level("sub_participant") == "sub_participant"
    assert normalize_participation_level("QHIN") is None
    assert normalize_participation_level("") is None
    assert normalize_participation_level(None) is None


def test_normalize_participation_never_maps_garbled_values():
    """Whole-string membership only (review finding 2026-11-10): a
    strip-anywhere normalizer would launder arbitrary garbled values into the
    vocabulary. These must all stay out of scope."""
    for garbled in ("p-a-r-t_i c i p a n t", "sub--participant", "-participant",
                    "participant_", "subparticipantx", "sub participantt",
                    "su b participant"):
        assert normalize_participation_level(garbled) is None, garbled


def test_single_record_path_produces_the_same_signal_as_the_bulk_path():
    """Path parity (review finding 2026-11-10): review_service must produce
    the ONC-scope signal from the entity's preserved raw attributes exactly
    as arc_pipeline produces it from delivered values."""
    from types import SimpleNamespace
    from app.tefca_registry.review_service import _treatment_onc_scope_signal

    ent = SimpleNamespace(
        rce_attributes={"purposesofuse_raw": "T-TRTMNT", "active_raw": "1"},
        entity_level="sub_participant")
    sig = _treatment_onc_scope_signal(ent, None)
    assert sig is not None and sig["status"] is True
    assert sig["reason_code"] == "TREATMENT_SOLE_PURPOSE_NPI_MISSING_ONC_SCOPE"
    # Registered submitted NPI present -> out of scope.
    assert _treatment_onc_scope_signal(ent, "1234567893") is None
    # Combined purposes -> out of scope.
    ent2 = SimpleNamespace(
        rce_attributes={"purposesofuse_raw": "T-PH-ECR,T-TRTMNT", "active_raw": "1"},
        entity_level="participant")
    assert _treatment_onc_scope_signal(ent2, None) is None
    # Inactive / missing raw attributes -> out of scope, never an error.
    ent3 = SimpleNamespace(rce_attributes=None, entity_level="participant")
    assert _treatment_onc_scope_signal(ent3, None) is None


def test_regime_group_refuses_two_versions_even_without_pairwise_conflicts():
    """Structural exclusion (review finding 2026-11-10): a hypothetical new
    treatment regime that FORGETS its pairwise conflicts_with is still
    refused by the regime group, while CONFIG A's declared dependency stack
    (treatment_review_b3 + treatment_b4) stays composable."""
    import dataclasses
    from app.tefca_registry import rule_configuration as rc

    forgot = dataclasses.replace(
        rc.COMPONENTS["treatment_b4_onc_scope"], conflicts_with=())
    original = rc.COMPONENTS["treatment_b4_onc_scope"]
    rc.COMPONENTS["treatment_b4_onc_scope"] = forgot
    try:
        with pytest.raises(ActivationRefused, match="incompatible"):
            rc.compose({"treatment_b4_onc_scope", "treatment_b4_owner_adopted"})
    finally:
        rc.COMPONENTS["treatment_b4_onc_scope"] = original
    # The declared CONFIG-A stack is one version, not two.
    rules = compose({"treatment_review_b3", "treatment_b4"})
    assert any(r["rule_code"] == "RULE-006" and r["bucket"] == "B4" for r in rules)


def test_inactive_records_are_out_of_scope():
    for active in ("0", "", None, "false"):
        assert _sig("T-TRTMNT", None, active, "Participant") is None, active


def test_blank_vs_populated_submitted_npi():
    """Only a blank ORIGINAL SUBMITTED NPI is in scope; any submitted value —
    even an invalid one — takes the record out of THIS rule's scope (its
    validity is a different check)."""
    assert _sig("T-TRTMNT", None, "1", "Participant") is not None
    assert _sig("T-TRTMNT", "   ", "1", "Participant") is not None
    for npi in ("1234567893", "123", "ABC"):
        assert _sig("T-TRTMNT", npi, "1", "Participant") is None, npi


def test_supplemental_npi_is_separate_evidence_and_never_fills_the_blank():
    """The producer reads the SUBMITTED value only — there is no parameter
    through which a manual-QA/NPPES/IQVIA supplemental NPI could enter — and
    the payload states that boundary."""
    import inspect
    params = inspect.signature(treatment_onc_scope_missing_npi).parameters
    assert set(params) == {"purposes_raw", "npi_raw", "active_raw",
                           "participation_raw", "assessment_date",
                           "requirement_effective"}
    sig = _sig("T-TRTMNT", None, "1", "Participant")
    assert "supplemental" in sig["note"].lower()
    assert sig["reason_code"] == "TREATMENT_SOLE_PURPOSE_NPI_MISSING_ONC_SCOPE"
    assert sig["raw_npi"] == "" and sig["purpose_tokens"] == ["T-TRTMNT"]


def test_requirement_not_in_force_is_out_of_scope():
    """An assessment date before BR_NPI_TRTMNT_EFFECTIVE (2026-10-25) emits
    nothing — the D-12 date-gating behavior is unchanged by the scope."""
    assert treatment_onc_scope_missing_npi(
        "T-TRTMNT", None, "1", "Participant",
        assessment_date=date(2026, 10, 14),
        requirement_effective=BR_NPI_TRTMNT_EFFECTIVE) is None


def test_no_node_type_input_exists():
    """There is deliberately NO initiating-node requirement: the function has
    no node-type parameter at all (asserted via the signature above) and the
    payload carries no node-type key."""
    sig = _sig("T-TRTMNT", None, "1", "Sub-Participant")
    assert not any("node" in k for k in sig)


# ── 2. composition: the rule, its precedence, and regime exclusivity ─────────

def test_component_composes_a_new_rule_006_version_at_priority_6():
    rules = compose(SELECTION)
    r6 = [r for r in rules if r["rule_code"] == "RULE-006"]
    assert len(r6) == 1
    assert (r6[0]["bucket"], r6[0]["priority"]) == ("B4", 6)
    assert "ONC updated scope" in r6[0]["name"]
    assert r6[0]["conditions"] == {"all_of": [
        {"field": "treatment_onc_scope_missing_npi", "status": True}]}
    # Historical definitions untouched: baseline rules keep their positive
    # clauses, and no RULE-007 is selected by this component.
    assert not any(r["rule_code"] == "RULE-007" for r in rules)


def test_one_treatment_regime_at_a_time():
    for other in ("treatment_b4_owner_adopted", "treatment_review_b3"):
        with pytest.raises(ActivationRefused, match="incompatible"):
            compose({"treatment_b4_onc_scope", other})
    with pytest.raises(ActivationRefused, match="incompatible"):
        compose({"treatment_b4_onc_scope", "treatment_review_b3",
                 "treatment_b4"})


def test_signal_routes_b4_and_higher_priority_rules_still_win():
    composed = compose(SELECTION)
    results = _base_clean()
    results["fields"]["treatment_onc_scope_missing_npi"] = {"status": True}
    out = _classify(composed, results)
    assert (out.bucket, out.rule_code) == ("B4", "RULE-006")
    assert "ONC updated scope" in out.rule_name
    # A confirmed exclusion (RULE-005, priority 5) wins over the scope rule.
    results["sources"]["oig_leie"] = "excluded"
    out = _classify(composed, results)
    assert (out.bucket, out.rule_code) == ("B4", "RULE-005")


def test_out_of_scope_records_fall_to_the_remaining_rules_never_auto_b3():
    """A record outside the ONC Treatment scope (no signal emitted) is
    evaluated by the remaining applicable rules: clean evidence still reaches
    B1; adverse evidence routes by ITS OWN rule, not by any treatment rule."""
    composed = compose(SELECTION)
    # Combined-purpose record with clean sources: B1, not auto-B3.
    assert _classify(composed, _base_clean()).bucket == "B1"
    # Combined-purpose record with an NPPES not_found: RULE-004 B3 (its own
    # evidence route, not a treatment route).
    adverse = _base_clean()
    adverse["sources"]["nppes"] = "not_found"
    out = _classify(composed, adverse)
    assert (out.bucket, out.rule_code) == ("B3", "RULE-004")
    # SAM candidate: RULE-008 B3 pending adjudication (EX-3), never B4.
    cand = _base_clean()
    cand["sources"]["sam_gov"] = "possible_match"
    out = _classify(composed, cand)
    assert (out.bucket, out.rule_code) == ("B3", "RULE-008")


def test_owner_adopted_signal_alone_triggers_nothing_under_onc_scope():
    """Under the ONC-scope regime the superseded owner-adopted signal is
    inert: only the ONC-scope signal is consumed."""
    composed = compose(SELECTION)
    results = _base_clean()
    results["fields"]["treatment_owner_adopted_missing_npi"] = {"status": True}
    assert _classify(composed, results).bucket == "B1"


def test_default_off_baseline_unchanged():
    """With no component selected, composition is byte-identical to the
    deployed baseline: merging this code changes no classification."""
    assert compose(set()) == SEED_RULES_V3
    assert "treatment_b4_onc_scope" in COMPONENTS
