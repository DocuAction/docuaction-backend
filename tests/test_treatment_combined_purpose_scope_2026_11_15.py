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
