"""FMT-007 — organisation phone completeness, the counterpart of FMT-005.

WHY THIS RULE EXISTS
────────────────────
`contact_phone` has had a digit-count observation since rule set 1.0.0
(FMT-005). The organisation's own `phone` had none, so an identical fragment
was recorded in one field and silently ignored in the other. The delivery
populates `phone` on 84 of 23,566 records, which is why the asymmetry never
surfaced.

WHAT IT DELIBERATELY DOES NOT DO
────────────────────────────────
It OBSERVES; it never repairs and never accuses. A partial number is preserved
exactly as delivered, the finding is INFORMATIONAL, and it carries
NO_CORRECTION — reconstructing a phone number from a fragment would invent data,
and a short number says nothing about the organisation. This mirrors FMT-005
exactly rather than inventing a second convention for the same question.

It is also the ONLY deterministic rule added in this pass. The contact-address
block remains deliberately uncovered: whether the ARC evaluates it at all is an
open ONC methodology question, and 6,978 records would be affected.
"""

from __future__ import annotations

import pytest

from app.tefca_registry.rce.quality_rules import (
    AUTO_SAFE_RULES,
    RULE_BY_ID,
    RULES,
    RULE_SET_VERSION,
    RecordContext,
    rule_config_hash,
)


def _ctx(**values) -> RecordContext:
    """A synthetic record. No Government value ever appears here."""
    return RecordContext(line_number=2, parse_status="ok", field_count=41,
                         values=values, dataset={})


def _run(**values):
    return RULE_BY_ID["FMT-007"].evaluate(_ctx(**values))


# ── registration ─────────────────────────────────────────────────────────────

def test_the_rule_is_registered_exactly_once():
    ids = [r.rule_id for r in RULES]
    assert ids.count("FMT-007") == 1
    rule = RULE_BY_ID["FMT-007"]
    assert rule.category == "FMT"
    assert rule.description == "Organisation phone complete"


def test_adding_the_rule_versioned_the_rule_set():
    """A run records the version and config hash it ran under.

    The delivered population was assessed under 1.0.0 and stays explicable at
    1.0.0; this is a new version, not a rewrite of the old one.
    """
    assert RULE_SET_VERSION == "1.1.0"
    assert len(rule_config_hash()) == 64


def test_the_rule_can_never_correct_anything():
    """INVARIANT: it is an observation, so it is not in the AUTO_SAFE list."""
    assert "FMT-007" not in AUTO_SAFE_RULES


# ── behaviour: valid / invalid / blank / boundary ────────────────────────────

def test_a_complete_number_raises_nothing():
    assert _run(phone="555-0100999") == []
    assert _run(phone="(555) 010-0999") == []
    assert _run(phone="+1 555 010 0999 ext 22") == []


def test_a_blank_or_absent_phone_raises_nothing():
    """Absence is not a finding. The field is populated on 0.36% of records."""
    assert _run(phone="") == []
    assert _run(phone="   ") == []
    assert _run() == []


def test_a_fragment_is_observed():
    findings = _run(phone="555-0100")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "FMT-007"
    assert finding.issue_type == "ORG_PHONE_FRAGMENT"
    assert finding.severity == "INFORMATIONAL"
    assert finding.correction_authority == "NO_CORRECTION"
    assert finding.field_name == "phone"
    assert finding.original_value == "555-0100"
    assert finding.suggested_value is None, "a fragment must never be rebuilt"


@pytest.mark.parametrize("digits,expected", [
    ("123456789", 1),      # 9 digits — one short
    ("1234567890", 0),     # exactly 10 — the boundary, complete
    ("12345678901", 0),    # 11 — complete
])
def test_the_boundary_is_ten_digits(digits, expected):
    assert len(_run(phone=digits)) == expected


def test_non_digits_are_ignored_when_counting():
    """Formatting is not the question; how many digits were delivered is."""
    assert _run(phone="(555) 010-0999") == []
    assert len(_run(phone="(555) 010-")) == 1


# ── it matches its counterpart exactly ───────────────────────────────────────

def test_it_behaves_identically_to_its_contact_phone_counterpart():
    """The two fields must not answer the same question differently."""
    org = RULE_BY_ID["FMT-007"].evaluate(_ctx(phone="555-0100"))
    contact = RULE_BY_ID["FMT-005"].evaluate(_ctx(contact_phone="555-0100"))
    assert len(org) == len(contact) == 1
    assert org[0].severity == contact[0].severity
    assert org[0].correction_authority == contact[0].correction_authority
    assert org[0].suggested_value == contact[0].suggested_value is None
    # Each speaks only about its own field.
    assert org[0].field_name == "phone"
    assert contact[0].field_name == "contact_phone"


def test_it_reads_only_its_own_field():
    """A contact fragment must not raise an organisation finding, or vice versa."""
    assert _run(contact_phone="555") == []
    assert RULE_BY_ID["FMT-005"].evaluate(_ctx(phone="555")) == []


# ── it is routed like every other DQ finding ─────────────────────────────────

def test_the_review_bridge_classifies_it():
    """An unclassified rule is refused by the bridge, so it must be listed."""
    from app.tefca_registry.rce import dq_review_bridge as bridge

    assert bridge.classification_for("FMT-007") == "DQ"
    for rule in RULES:
        assert rule.rule_id in bridge.RULE_CLASSIFICATION


def test_every_rule_still_has_a_unique_id():
    from app.tefca_registry.rce.quality_rules import _assert_rule_ids_unique

    _assert_rule_ids_unique()      # raises on a duplicate
    assert len({r.rule_id for r in RULES}) == len(RULES) == 32
