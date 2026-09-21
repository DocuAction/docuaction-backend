"""Rule set 1.2.0 — the nine NPI codes, one primary finding per value.

    NPI-001  NPI_REQUIRED (profile requires) / NPI_NOT_SUPPLIED (optional)
    NPI-002  NPI_LENGTH_INVALID, MULTIPLE_NPI_IN_ONE_FIELD
    NPI-004  NPI_FORMAT_INVALID
    NPI-003  NPI_CHECKSUM_INVALID  (Luhn, 80840 prefix)
    NPI-005  NPI_NOT_FOUND                 } verification-time
    NPI-006  NPI_DEACTIVATED               } (metadata here, written by
    NPI-009  NPI_VERIFICATION_UNAVAILABLE  }  verification_findings)
    NPI-008  NPI_EXISTING_VALUE_CONFLICT     promotion-time (promotion.py)

Pure: no database.
"""

from __future__ import annotations

import pytest

from app.services.npi_validator import compute_check_digit, validate_npi
from app.tefca_registry.rce import quality_rules as qr
from app.tefca_registry.rce.field_map import RCE_FIELDS

NPI_RULES = tuple(r for r in qr.RULES if r.category == qr.CAT_NPI)


def ctx(**over) -> qr.RecordContext:
    values = {f: "" for f in RCE_FIELDS}
    values.update({"id": "9.99.777.1.1", "sequoiaorgtype": "Participant",
                   "name": "SYNTHETIC ORG", "NPI": "1982916078"})
    values.update(over)
    return qr.RecordContext(line_number=2, parse_status="ok", field_count=41,
                            values=values, dataset={})


def findings(rule_id: str, context) -> list:
    return qr.RULE_BY_ID[rule_id].evaluate(context)


def all_npi_findings(context) -> list:
    out = []
    for rule in NPI_RULES:
        out.extend(rule.evaluate(context))
    return out


# ── the fixture values are what the contract says they are ───────────────────

def test_the_contract_values_behave_as_stated():
    assert compute_check_digit("198291607") == "8"
    assert validate_npi("1982916078") == (True, "")
    assert validate_npi("1982916079")[0] is False


def test_rule_set_is_at_least_1_2_0():
    # 1.2.0 introduced the NPI codes below; 1.3.0 (September 2026) added rules
    # without changing any of them.
    assert tuple(int(x) for x in qr.RULE_SET_VERSION.split(".")) >= (1, 2, 0)


# ── QUALITY-time codes ───────────────────────────────────────────────────────

def test_nine_digit_value_is_a_length_finding_only():
    c = ctx(NPI="198291607")
    result = findings("NPI-002", c)
    assert [f.issue_type for f in result] == ["NPI_LENGTH_INVALID"]
    assert result[0].severity == qr.HIGH
    assert result[0].correction_authority == qr.HUMAN_REQUIRED
    assert result[0].original_value == "198291607"
    assert result[0].suggested_value is None
    assert findings("NPI-004", c) == []
    assert findings("NPI-003", c) == []


def test_eleven_digit_value_is_a_length_finding():
    c = ctx(NPI="19829160788")
    assert [f.issue_type for f in findings("NPI-002", c)] == ["NPI_LENGTH_INVALID"]
    assert findings("NPI-004", c) == [] and findings("NPI-003", c) == []


def test_lettered_ten_character_value_is_a_format_finding_only():
    c = ctx(NPI="19829160A8")
    assert findings("NPI-002", c) == []
    result = findings("NPI-004", c)
    assert [f.issue_type for f in result] == ["NPI_FORMAT_INVALID"]
    assert result[0].rule_id == "NPI-004"
    assert result[0].severity == qr.HIGH
    assert result[0].correction_authority == qr.HUMAN_REQUIRED
    assert findings("NPI-003", c) == [], "checksum is not evaluated on a non-numeric value"


def test_checksum_failure_is_high_and_human_required():
    c = ctx(NPI="1982916079")
    assert findings("NPI-002", c) == []
    assert findings("NPI-004", c) == []
    result = findings("NPI-003", c)
    assert [f.issue_type for f in result] == ["NPI_CHECKSUM_INVALID"]
    assert result[0].severity == qr.HIGH
    assert result[0].correction_authority == qr.HUMAN_REQUIRED
    assert result[0].original_value == "1982916079"
    assert result[0].suggested_value is None, "the correct digit is never written back"


def test_a_valid_npi_raises_nothing():
    assert all_npi_findings(ctx(NPI="1982916078")) == []


def test_comma_separated_cell_stays_on_npi_002():
    c = ctx(NPI="1982916078, 1234567893")
    result = findings("NPI-002", c)
    assert [f.issue_type for f in result] == ["MULTIPLE_NPI_IN_ONE_FIELD"]
    assert result[0].severity == qr.HIGH
    assert result[0].suggested_confidence == "LOW"
    assert findings("NPI-004", c) == [] and findings("NPI-003", c) == []


# ── presence: required by profile versus recorded fact ───────────────────────

def test_missing_npi_is_required_when_the_profile_requires_one():
    c = ctx(NPI="", sequoiaorgtype="Participant", hl7orgrole="provider")
    assert qr.npi_required(c) is True
    result = findings("NPI-001", c)
    assert [f.issue_type for f in result] == ["NPI_REQUIRED"]
    assert result[0].severity == qr.HIGH
    assert result[0].correction_authority == qr.HUMAN_REQUIRED


def test_subparticipant_provider_also_requires_an_npi():
    c = ctx(NPI="", sequoiaorgtype="Subparticipant", hl7orgrole="provider")
    assert qr.npi_required(c) is True


def test_missing_npi_is_informational_when_not_required():
    c = ctx(NPI="")
    assert qr.npi_required(c) is False, "an empty hl7orgrole imposes no requirement"
    result = findings("NPI-001", c)
    assert [f.issue_type for f in result] == ["NPI_NOT_SUPPLIED"]
    assert result[0].severity == qr.INFO
    assert result[0].correction_authority == qr.NO_CORRECTION


@pytest.mark.parametrize("role", sorted(qr.NON_PROVIDER_HL7_ROLES))
def test_non_provider_roles_never_require_an_npi(role):
    c = ctx(NPI="", hl7orgrole=role)
    assert qr.npi_required(c) is False
    assert [f.issue_type for f in findings("NPI-001", c)] == ["NPI_NOT_SUPPLIED"]


def test_present_npi_raises_no_presence_finding():
    assert findings("NPI-001", ctx()) == []
    assert findings("NPI-001", ctx(hl7orgrole="provider")) == []


# ── one primary finding per value ────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("", "NPI_NOT_SUPPLIED"),
    ("198291607", "NPI_LENGTH_INVALID"),
    ("19829160788", "NPI_LENGTH_INVALID"),
    ("19829160A8", "NPI_FORMAT_INVALID"),
    ("1982916079", "NPI_CHECKSUM_INVALID"),
    ("1982916078, 1234567893", "MULTIPLE_NPI_IN_ONE_FIELD"),
])
def test_exactly_one_primary_finding_per_value(value, expected):
    result = all_npi_findings(ctx(NPI=value))
    assert [f.issue_type for f in result] == [expected]


def test_order_is_presence_length_format_checksum():
    """Each stage is guarded on the ones before it, so the engine — which runs
    the rules independently — cannot produce two primary findings."""
    length_bad = ctx(NPI="12345")
    assert findings("NPI-004", length_bad) == [] and findings("NPI-003", length_bad) == []
    format_bad = ctx(NPI="12345ABCDE")
    assert findings("NPI-003", format_bad) == []


# ── legacy vocabulary ────────────────────────────────────────────────────────

def test_legacy_issue_types_map_to_the_new_codes():
    assert qr.LEGACY_ISSUE_TYPES == {
        "NPI_MALFORMED": ["NPI_LENGTH_INVALID", "NPI_FORMAT_INVALID"],
        "NPI_CHECK_DIGIT_FAILED": ["NPI_CHECKSUM_INVALID"],
    }


def test_no_current_rule_emits_a_legacy_issue_type():
    samples = ["", "12345", "12345ABCDE", "1982916079", "1,2", "1982916078"]
    emitted = {f.issue_type for v in samples for f in all_npi_findings(ctx(NPI=v))}
    assert not (emitted & set(qr.LEGACY_ISSUE_TYPES)), emitted
    for new_types in qr.LEGACY_ISSUE_TYPES.values():
        for t in new_types:
            assert t in emitted, f"{t} is in the legacy map but no rule produces it"


# ── metadata rules for verification / promotion time ─────────────────────────

@pytest.mark.parametrize("rule_id, severity, stage", [
    ("NPI-005", qr.MEDIUM, "VERIFICATION"),
    ("NPI-006", qr.HIGH, "VERIFICATION"),
    ("NPI-008", qr.HIGH, "PROMOTION"),
    ("NPI-009", qr.INFO, "VERIFICATION"),
])
def test_later_stage_rules_are_known_but_not_executed(rule_id, severity, stage):
    rule = qr.RULE_BY_ID[rule_id]
    assert rule.stage == stage
    assert rule.severity() == severity
    assert rule not in qr.RULES, "the quality engine must not execute it"
    assert rule in qr.NON_QUALITY_RULES
    assert rule.evaluate(ctx()) == []


def test_quality_rules_are_stage_quality():
    assert all(r.stage == "QUALITY" for r in qr.RULES)


def test_non_quality_issue_types_name_their_rules():
    assert qr.NON_QUALITY_ISSUE_TYPES["NPI_NOT_FOUND"] == ("NPI-005", qr.MEDIUM, qr.HUMAN_REQUIRED)
    # QA_REQUIRED since 2026-09-18 (pre-merge review Decision 2): confirmed
    # deactivation is a named BLOCKING trigger and needs independent QA.
    # Exclusively post-promotion (verification_findings.py never writes it
    # pre-promotion), so this cannot affect any pre-promotion path.
    assert qr.NON_QUALITY_ISSUE_TYPES["NPI_DEACTIVATED"] == ("NPI-006", qr.HIGH, qr.QA_REQUIRED)
    assert qr.NON_QUALITY_ISSUE_TYPES["NPI_EXISTING_VALUE_CONFLICT"] == ("NPI-008", qr.HIGH, qr.HUMAN_REQUIRED)
    assert qr.NON_QUALITY_ISSUE_TYPES["IDENTIFIER_EXISTING_VALUE_CONFLICT"][0] == "NPI-008"
    assert qr.NON_QUALITY_ISSUE_TYPES["NPI_VERIFICATION_UNAVAILABLE"] == ("NPI-009", qr.INFO, qr.NO_CORRECTION)


def test_next_free_npi_id_accounts_for_the_metadata_rules():
    assert qr.next_available_rule_ids()["NPI"] == "NPI-010"
