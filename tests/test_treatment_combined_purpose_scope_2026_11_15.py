"""Treatment missing-NPI scope, combined-purpose variant (relayed 2026-11-15; IMPLEMENTED, INACTIVE).

Exact parsed T-TRTMNT token alone or combined with other purposes. Active; Participant or
Subparticipant; blank ORIGINAL submitted NPI; no initiating-node condition; a supplemental NPI
never fills the submitted blank. The sole-purpose behaviour is unchanged by default.
"""
from datetime import date

import pytest

from app.tefca_registry.rce.npi_applicability import (
    compare_treatment_scopes, treatment_combined_scope_missing_npi, treatment_onc_scope_missing_npi)
from app.tefca_registry.rce.quality_rules import BR_NPI_TRTMNT_EFFECTIVE

ASSESS = date(2026, 11, 8)


def comb(purposes, npi="", active="1", participation="Participant", **kw):
    return treatment_combined_scope_missing_npi(
        purposes, npi, active, participation, assessment_date=kw.pop("assessment_date", ASSESS),
        requirement_effective=BR_NPI_TRTMNT_EFFECTIVE)


def sole(purposes, npi="", active="1", participation="Participant"):
    return treatment_onc_scope_missing_npi(
        purposes, npi, active, participation, assessment_date=ASSESS,
        requirement_effective=BR_NPI_TRTMNT_EFFECTIVE)


@pytest.mark.parametrize("purposes", [
    "T-TRTMNT", "T-TRTMNT,T-PH-ECR", "T-PH-ECR,T-TRTMNT", "T-PH-ECR, T-IAS ,T-TRTMNT",
    "T-TRTMNT;T-PH", "T-TRTMNT|T-IAS"])
def test_the_exact_token_alone_or_combined_in_any_order_is_in_scope(purposes):
    sig = comb(purposes)
    assert sig and sig["status"] is True
    assert sig["reason_code"] == "TREATMENT_TOKEN_NPI_MISSING_COMBINED_SCOPE"
    assert sig["raw_npi"] == "" and "T-TRTMNT" in sig["purpose_tokens"]


@pytest.mark.parametrize("purposes", [
    "T-TREAT", "T-TREAT,T-PH", "T-TRTMNTX", "XT-TRTMNT", "t-trtmnt", "TRTMNT", "T-TRTMNT2,T-PH",
    "", "   ", None, "T-PH-ECR,T-IAS"])
def test_near_matches_t_treat_and_blank_purposes_are_out_of_scope(purposes):
    assert comb(purposes) is None


def test_inactive_and_unsupported_roles_are_out_of_scope():
    assert comb("T-TRTMNT,T-PH", active="0") is None and comb("T-TRTMNT", active="") is None
    for role in ("QHIN", "", "Participant Member", "Subparticipantx", None):
        assert comb("T-TRTMNT,T-PH", participation=role) is None, role
    for role in ("Participant", "Subparticipant", "Sub-Participant"):
        assert comb("T-TRTMNT,T-PH", participation=role) is not None, role


def test_a_supplied_submitted_npi_ends_the_condition_and_a_supplemental_one_never_fills_the_blank():
    assert comb("T-TRTMNT,T-PH", npi="1234567893") is None
    assert comb("T-TRTMNT,T-PH", npi="  ") is not None, "whitespace is not an NPI"
    # The function reads delivered values only: there is no parameter through which an NPPES/IQVIA/manual
    # NPI could be supplied, and the signal keeps the original blank verbatim.
    assert comb("T-TRTMNT,T-PH")["raw_npi"] == ""


def test_no_initiating_node_input_exists():
    import inspect
    for fn in (treatment_onc_scope_missing_npi, treatment_combined_scope_missing_npi):
        assert "org_node_type" not in inspect.signature(fn).parameters


def test_the_requirement_is_not_applied_before_its_effective_date():
    from datetime import timedelta
    before = BR_NPI_TRTMNT_EFFECTIVE - timedelta(days=1)
    assert comb("T-TRTMNT,T-PH", assessment_date=before) is None
    assert comb("T-TRTMNT,T-PH", assessment_date=BR_NPI_TRTMNT_EFFECTIVE) is not None


def test_sole_purpose_behaviour_is_unchanged_by_default():
    assert sole("T-TRTMNT") is not None and sole("T-TRTMNT")["reason_code"].endswith("ONC_SCOPE")
    assert sole("T-TRTMNT,T-PH-ECR") is None and sole("T-PH-ECR,T-TRTMNT") is None


def test_impact_comparison_counts_only_what_the_combined_scope_adds():
    rows = [dict(purposes="T-TRTMNT", npi="", active="1", participation="Participant"),
            dict(purposes="T-TRTMNT,T-PH", npi="", active="1", participation="Subparticipant"),
            dict(purposes="T-PH,T-TRTMNT", npi="", active="1", participation="Participant"),
            dict(purposes="T-TRTMNT,T-PH", npi="1234567893", active="1", participation="Participant"),
            dict(purposes="T-TRTMNT,T-PH", npi="", active="0", participation="Participant"),
            dict(purposes="T-TREAT", npi="", active="1", participation="Participant")]
    out = compare_treatment_scopes(rows, assessment_date=ASSESS, requirement_effective=BR_NPI_TRTMNT_EFFECTIVE)
    assert out == {"records": 6, "sole_purpose_scope": 1, "combined_purpose_scope": 3,
                   "added_by_combined": 2, "sole_only": 0}


def test_the_combined_signal_describes_itself_as_combined_not_sole_purpose():
    sig = comb("T-PH-ECR,T-TRTMNT")
    assert "combined-purpose" in sig["scope_basis"] and "sole purpose" not in sig["note"]
    assert "alone or combined" in sig["note"]
    old = sole("T-TRTMNT")
    assert "sole purpose" in old["note"] and old["scope_basis"].startswith("ONC updated")


# -- 2026-11-24: submitted methodology v1.4 RULE-006 as a selectable, NOT ACTIVATED rule version. --
# The combined-scope signal is emitted by the ONE shared producer (bulk and single-record paths) and consumed only
# by the component `treatment_b4_combined_scope`. Sole-purpose behaviour under the ONC-scope component is unchanged.

def _signals(purposes, npi="", active="1", seq="Participant", node=""):
    from app.tefca_registry.rce.npi_applicability import treatment_signals_from_parsed
    return treatment_signals_from_parsed(
        {"purposesofuse": purposes, "NPI": npi, "active": active, "sequoiaorgtype": seq,
         "organizationNodeType": node},
        assessment_date=ASSESS, requirement_effective=BR_NPI_TRTMNT_EFFECTIVE)


def _bucket(selection, fields):
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from app.tefca_registry.rule_configuration import compose
    rules = [{**r, "version": 99} for r in compose(selection)]
    out = BucketClassifier(rules=rules).classify(
        {"sources": {"nppes": "verified", "oig_leie": "clear", "pecos": "verified"}, "fields": dict(fields)})
    return out.bucket, out.rule_code


COMBINED = {"treatment_b4_combined_scope"}
SOLE = {"treatment_b4_onc_scope"}


@pytest.mark.parametrize("purposes", ["T-TRTMNT", "T-PH-ECR,T-TRTMNT", "T-TRTMNT,T-PH-ECR",
                                      "T-GOVDTRM-ACP,T-PH-ECR,T-TRTMNT", "T-TREAT,T-TRTMNT"])
def test_v14_rule_006_fires_for_the_token_alone_or_combined_in_any_order(purposes):
    assert _bucket(COMBINED, _signals(purposes)) == ("B4", "RULE-006")


def test_sole_purpose_rule_is_unchanged_a_combined_record_is_b4_only_under_the_v14_component():
    sole, combined = _signals("T-TRTMNT"), _signals("T-PH-ECR,T-TRTMNT")
    assert _bucket(SOLE, sole) == ("B4", "RULE-006")
    assert _bucket(SOLE, combined) == ("B1", "RULE-001")           # sole-purpose scope: left to the other rules
    assert _bucket(COMBINED, sole) == ("B4", "RULE-006")
    assert _bucket(COMBINED, combined) == ("B4", "RULE-006")
    assert _bucket(set(), combined) == ("B1", "RULE-001")          # nothing selected: the signal is inert
    assert _bucket(set(), sole) == ("B1", "RULE-001")


@pytest.mark.parametrize("purposes", ["T-TREAT", "T-TREAT,T-PH-ECR", "", "T-TRTMNTX", "t-trtmnt", "T-PH-ECR"])
def test_t_treat_alone_near_matches_and_blank_purposes_never_trigger_it(purposes):
    fields = _signals(purposes)
    assert "treatment_combined_scope_missing_npi" not in fields
    assert _bucket(COMBINED, fields) == ("B1", "RULE-001")


def test_every_other_condition_still_applies_and_there_is_no_node_gate():
    assert "treatment_combined_scope_missing_npi" not in _signals("T-PH-ECR,T-TRTMNT", active="0")      # inactive
    assert "treatment_combined_scope_missing_npi" not in _signals("T-PH-ECR,T-TRTMNT", seq="QHIN")      # level
    assert "treatment_combined_scope_missing_npi" not in _signals("T-PH-ECR,T-TRTMNT", seq="")          # blank level
    assert "treatment_combined_scope_missing_npi" not in _signals("T-PH-ECR,T-TRTMNT", npi="1234567893")  # NPI supplied
    assert "treatment_combined_scope_missing_npi" not in _signals("T-PH-ECR,T-TRTMNT", npi="1234567890")  # supplied, invalid
    for node in ("", "initiating", "passthrough", "no node"):
        assert _bucket(COMBINED, _signals("T-PH-ECR,T-TRTMNT", seq="Sub-Participant", node=node)) == ("B4", "RULE-006")


def test_a_supplemental_npi_is_not_an_input_so_it_can_never_fill_the_blank():
    import inspect

    from app.tefca_registry.rce import npi_applicability as npa
    params = set(inspect.signature(npa.treatment_combined_scope_missing_npi).parameters)
    assert params == {"purposes_raw", "npi_raw", "active_raw", "participation_raw", "assessment_date",
                      "requirement_effective"}
    signal = _signals("T-PH-ECR,T-TRTMNT")["treatment_combined_scope_missing_npi"]
    assert signal["raw_npi"] == "" and "supplemental" in signal["note"].lower()
    assert signal["reason_code"] == "TREATMENT_TOKEN_NPI_MISSING_COMBINED_SCOPE"


def test_the_requirement_date_is_assessed_separately_and_applies_to_both_scopes():
    from datetime import date as _d

    from app.tefca_registry.rce.npi_applicability import treatment_signals_from_parsed
    before = treatment_signals_from_parsed(
        {"purposesofuse": "T-PH-ECR,T-TRTMNT", "NPI": "", "active": "1", "sequoiaorgtype": "Participant"},
        assessment_date=_d(2026, 10, 24), requirement_effective=BR_NPI_TRTMNT_EFFECTIVE)
    assert "treatment_combined_scope_missing_npi" not in before and "treatment_onc_scope_missing_npi" not in before
    assert BR_NPI_TRTMNT_EFFECTIVE == _d(2026, 10, 25)


def test_bulk_and_single_record_entry_points_emit_the_same_combined_signal():
    from app.tefca_registry.rce.npi_applicability import treatment_field_signals
    bulk = treatment_field_signals("T-PH-ECR,T-TRTMNT", "", "1", "Sub-Participant", "",
                                   assessment_date=ASSESS, requirement_effective=BR_NPI_TRTMNT_EFFECTIVE)
    single = _signals("T-PH-ECR,T-TRTMNT", seq="Sub-Participant")
    assert bulk == single and "treatment_combined_scope_missing_npi" in bulk
    assert "treatment_onc_scope_missing_npi" not in bulk            # the sole-purpose signal stays sole-purpose


def test_the_v14_component_is_one_regime_at_a_time_and_higher_priority_rules_still_win():
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from app.tefca_registry.rule_configuration import ActivationRefused, COMPONENTS, compose
    for other in ("treatment_b4_onc_scope", "treatment_b4_owner_adopted", "treatment_review_b3"):
        with pytest.raises(ActivationRefused, match="incompatible"):
            compose({"treatment_b4_combined_scope", other})
    assert COMPONENTS["treatment_b4_combined_scope"].regime == "treatment"
    rules = [{**r, "version": 99} for r in compose(COMBINED)]
    r6 = [r for r in rules if r["rule_code"] == "RULE-006"]
    assert len(r6) == 1 and (r6[0]["bucket"], r6[0]["priority"]) == ("B4", 6)
    out = BucketClassifier(rules=rules).classify(
        {"sources": {"nppes": "verified", "oig_leie": "excluded", "pecos": "verified"},
         "fields": _signals("T-PH-ECR,T-TRTMNT")})
    assert (out.bucket, out.rule_code) == ("B4", "RULE-005")       # a confirmed exclusion outranks the scope rule
