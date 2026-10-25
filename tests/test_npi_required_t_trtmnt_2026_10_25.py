"""BR-NPI-TRTMNT-001 — "Under purposeofuse, T-TRTMNT requires an NPI" (rule set 1.5.0, NPI-001 1.3.0).

Business clarification from Meley relayed 2026-10-25. These tests pin exactly what was clarified and
nothing more: exact-token membership on the delivered purpose list, independence from hl7orgrole /
sequoiaorgtype / active, effective-date gating, one primary NPI finding per value, and that a missing
required NPI is a HIGH review finding — never a bucket, an exclusion or a rewrite of history. Pure.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.tefca_registry.bucket_classifier import SEED_RULES, SEED_RULES_V2, SEED_RULES_V3
from app.tefca_registry.rce import quality_rules as qr
from app.tefca_registry.rce.field_map import RCE_FIELDS

VALID = "1982916078"
BAD_CHECKSUM = "1982916079"


def ctx(dataset=None, **over) -> qr.RecordContext:
    values = {f: "" for f in RCE_FIELDS}
    values.update({"id": "9.99.777.1.1", "sequoiaorgtype": "Participant", "name": "SYNTHETIC ORG",
                   "NPI": "", "hl7orgrole": "", "purposesofuse": "T-TRTMNT", "active": "1"})
    values.update(over)
    return qr.RecordContext(line_number=2, parse_status="ok", field_count=41, values=values,
                            dataset=dict(dataset or {}))


def npi_001(c):
    return qr.RULE_BY_ID["NPI-001"].evaluate(c)


def all_npi(c):
    return [f for r in qr.RULES if r.category == qr.CAT_NPI for f in r.evaluate(c)]


# ── versions ─────────────────────────────────────────────────────────────────

def test_rule_set_and_rule_versions_were_bumped_not_edited_in_place():
    assert tuple(int(x) for x in qr.RULE_SET_VERSION.split(".")) >= (1, 5, 0)
    assert qr.RULE_BY_ID["NPI-001"].version == "1.3.0"
    assert qr.BR_NPI_TRTMNT_EFFECTIVE == date(2026, 10, 25)
    assert qr.BR_NPI_TRTMNT_TOKEN == "T-TRTMNT"


# ── the token, parsed from the delivered list ────────────────────────────────

@pytest.mark.parametrize("value", [
    "T-TRTMNT", " T-TRTMNT ", "T-TRTMNT,T-PYMNT", "T-PYMNT,T-TRTMNT", "T-PH-ECR, T-TRTMNT ,T-PYMNT",
    "T-PYMNT;T-TRTMNT", "T-PYMNT|T-TRTMNT", "T-TRTMNT,T-TRTMNT",
])
def test_t_trtmnt_alone_or_among_multiple_purposes_requires_an_npi(value):
    c = ctx(purposesofuse=value)
    assert qr.purpose_requires_npi(c) is True
    assert qr.npi_requirement_basis(c) == qr.BASIS_PURPOSE_T_TRTMNT
    result = npi_001(c)
    assert [f.issue_type for f in result] == ["NPI_REQUIRED"]
    assert result[0].severity == qr.HIGH
    assert result[0].correction_authority == qr.HUMAN_REQUIRED
    assert "BR-NPI-TRTMNT-001" in result[0].description


@pytest.mark.parametrize("value", [
    "", "T-PYMNT", "T-PH-ECR,T-PYMNT", "T-TREAT", "T-TREAT,T-PYMNT",       # other purposes, incl. the variant
    "T-TRTMNTX", "XT-TRTMNT", "T-TRTMNT-2", "TRTMNT", "T_TRTMNT", "t-trtmnt",  # similar-looking, not the token
])
def test_absent_or_nonmatching_purpose_imposes_nothing_on_this_basis(value):
    c = ctx(purposesofuse=value)
    assert qr.purpose_requires_npi(c) is False
    assert qr.npi_requirement_basis(c) is None
    result = npi_001(c)
    assert [f.issue_type for f in result] == ["NPI_NOT_SUPPLIED"]
    assert result[0].severity == qr.INFO


def test_t_treat_is_not_extended_without_policy():
    """The RCE never stated T-TREAT means T-TRTMNT; the clarification covers T-TRTMNT only."""
    assert qr.purpose_requires_npi(ctx(purposesofuse="T-TREAT")) is False
    assert "T-TREAT" in qr.SUSPECTED_PURPOSE_VARIANTS, "still reported as a suspected variant, not merged"


# ── nothing suppresses the requirement ───────────────────────────────────────

def test_blank_provider_role_does_not_suppress_the_requirement():
    c = ctx(hl7orgrole="")
    assert qr.npi_required(c) is True
    assert [f.issue_type for f in npi_001(c)] == ["NPI_REQUIRED"]


@pytest.mark.parametrize("role", ["provider", "payer", "agency", "HIE/HIO", "diagnostics"])
def test_any_role_value_leaves_the_purpose_requirement_in_force(role):
    assert qr.npi_required(ctx(hl7orgrole=role)) is True


@pytest.mark.parametrize("org_type", ["Participant", "Subparticipant", "", "Node", "QHIN"])
def test_participant_and_subparticipant_labels_do_not_suppress_it(org_type):
    c = ctx(sequoiaorgtype=org_type)
    assert qr.npi_requirement_basis(c) == qr.BASIS_PURPOSE_T_TRTMNT


@pytest.mark.parametrize("active", ["1", "0", "", "UNSUPPORTED:x"])
def test_source_active_status_is_recorded_but_not_consulted(active):
    result = npi_001(ctx(active=active))
    assert [f.issue_type for f in result] == ["NPI_REQUIRED"]
    assert f"active={active!r}" in result[0].description


def test_profile_role_basis_still_wins_the_label_when_both_apply():
    c = ctx(sequoiaorgtype="Participant", hl7orgrole="provider", purposesofuse="T-TRTMNT")
    assert qr.npi_requirement_basis(c) == qr.BASIS_PROFILE_ROLE
    assert [f.issue_type for f in npi_001(c)] == ["NPI_REQUIRED"]


def test_profile_role_basis_is_unchanged_from_1_2_0():
    assert qr.npi_requirement_basis(ctx(hl7orgrole="provider", purposesofuse="T-PYMNT")) == qr.BASIS_PROFILE_ROLE
    assert qr.npi_requirement_basis(ctx(hl7orgrole="payer", purposesofuse="T-PYMNT")) is None
    assert qr.npi_requirement_basis(ctx(hl7orgrole="", purposesofuse="T-PYMNT")) is None


# ── requiredness is separate from identifier validity ────────────────────────

def test_valid_npi_raises_no_presence_finding_and_is_not_evidence_of_verification():
    c = ctx(NPI=VALID)
    assert npi_001(c) == []
    assert all_npi(c) == [], "validity ends the quality stage; NPPES verification is a later stage"


@pytest.mark.parametrize("value, expected", [
    ("198291607", "NPI_LENGTH_INVALID"),
    ("19829160A8", "NPI_FORMAT_INVALID"),
    (BAD_CHECKSUM, "NPI_CHECKSUM_INVALID"),
    (f"{VALID}, 1234567893", "MULTIPLE_NPI_IN_ONE_FIELD"),
])
def test_malformed_or_checksum_invalid_npi_is_a_separate_finding_not_a_missing_one(value, expected):
    result = all_npi(ctx(NPI=value))
    assert [f.issue_type for f in result] == [expected]
    assert npi_001(ctx(NPI=value)) == []


def test_exactly_one_primary_npi_finding_per_value_still_holds():
    assert [f.issue_type for f in all_npi(ctx(NPI=""))] == ["NPI_REQUIRED"]
    assert [f.issue_type for f in all_npi(ctx(NPI="", purposesofuse="T-PYMNT"))] == ["NPI_NOT_SUPPLIED"]


# ── version / effective-date behaviour ───────────────────────────────────────

@pytest.mark.parametrize("as_of", [date(2026, 10, 24), "2026-10-24", datetime(2026, 10, 24, 23, 59),
                                   date(2026, 9, 24), "2026-09-24T19:56:29"])
def test_assessments_dated_before_the_effective_date_keep_the_1_2_0_predicate(as_of):
    c = ctx(dataset={"assessment_date": as_of})
    assert qr.assessment_date(c) < qr.BR_NPI_TRTMNT_EFFECTIVE
    assert qr.purpose_requires_npi(c) is False
    assert [f.issue_type for f in npi_001(c)] == ["NPI_NOT_SUPPLIED"]


@pytest.mark.parametrize("as_of", [date(2026, 10, 25), "2026-10-25", datetime(2026, 10, 25, 0, 0), date(2027, 1, 1)])
def test_assessments_on_or_after_the_effective_date_apply_the_rule(as_of):
    c = ctx(dataset={"assessment_date": as_of})
    assert qr.purpose_requires_npi(c) is True
    assert [f.issue_type for f in npi_001(c)] == ["NPI_REQUIRED"]


def test_a_context_without_an_assessment_date_is_a_current_assessment():
    assert qr.assessment_date(ctx()) == date.today()
    assert qr.assessment_date(ctx(dataset={"assessment_date": ""})) == date.today()


# ── a review finding, never a bucket or an overwrite ─────────────────────────

def test_missing_required_npi_is_a_review_finding_not_a_bucket_or_exclusion():
    result = npi_001(ctx())
    f = result[0]
    assert (f.severity, f.correction_authority) == (qr.HIGH, qr.HUMAN_REQUIRED)
    assert not hasattr(f, "bucket")
    assert "exclu" not in f.description.lower() and "B4" not in f.description
    # No B1-B4 rule, in any seed version, keys on the presence finding.
    for spec in SEED_RULES + SEED_RULES_V2 + SEED_RULES_V3:
        for clause in spec["conditions"].values():
            if isinstance(clause, list):
                for cond in clause:
                    if isinstance(cond, dict):
                        assert cond.get("field") != "npi_required"
                        assert cond.get("status") != "NPI_REQUIRED"


def test_historical_issue_vocabulary_is_untouched():
    """Rows written under 1.2.0 keep their issue types; the new predicate reuses NPI_REQUIRED, so no
    legacy mapping is needed and nothing is rewritten."""
    assert qr.LEGACY_ISSUE_TYPES == {
        "NPI_MALFORMED": ["NPI_LENGTH_INVALID", "NPI_FORMAT_INVALID"],
        "NPI_CHECK_DIGIT_FAILED": ["NPI_CHECKSUM_INVALID"],
    }
    assert "NPI_REQUIRED" not in qr.LEGACY_ISSUE_TYPES
