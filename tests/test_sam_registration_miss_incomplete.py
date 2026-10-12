"""WP4-SAM: a SAM registration-only miss must not reach the classifier as an exclusion clear. DB-free.

Stacked on #127 (which adds `normalized_values.screening_leg`). The correction is behind
`SAM_REGISTRATION_MISS_IS_UNAVAILABLE_FOR_EXCLUSION` (default OFF). It changes the classifier INPUT
(`sam_gov`: clear -> unavailable). Under the active v3 rule set (SEED_RULES_V3, mirrored in review_rules v3)
that does NOT change the bucket: RULE-001 has no positive SAM condition, so `unavailable` and `clear` both
satisfy it. Every test below that asserts a bucket pins that fact; making the bucket change would need
rule/applicability edits (decision C4) and is not attempted here.
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.Tefca.applicability import build_profile
from app.Tefca.evidence_assembly import _dimension_exclusion
from app.Tefca.mock_data import MOCK_ENTITY_INDEX
from app.tefca_registry.bucket_classifier import BucketClassifier, SEED_RULES_V3
from app.tefca_registry.rce.arc_pipeline import (
    dimensions_to_verification_results, is_registration_only_sam_miss, item_state)

V3 = BucketClassifier(rules=SEED_RULES_V3)
FLAG = "SAM_REGISTRATION_MISS_IS_UNAVAILABLE_FOR_EXCLUSION"


class _R:
    def __init__(self, data):
        self.success, self.data = True, data
        self.query_timestamp, self.api_version, self.error = "t", "v", None


def _classify(sam_result, *, key="sam_name", pecos="PASS", address=None):
    """Run SAM result -> D3 evidence -> translator -> v3 classifier. Returns (sam item, inputs, result)."""
    ent = MOCK_ENTITY_INDEX["rce-org-rp-006"]
    d3 = _dimension_exclusion(ent, build_profile(ent), {
        "leie_org": _R({"excluded": False, "exclusion_found": False}),
        key: _R(sam_result),
        "cms_revocation": _R({"matches": [], "result": "NONE"}),
    })
    sam = next(i for i in d3.items if i.source == "SAM_GOV")
    dims = [
        {"dimension": "IDENTITY", "disposition": "PASS", "applicability": "REQUIRED",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]},
        {"dimension": "MEDICARE_ENROLLMENT", "disposition": pecos, "applicability": "REQUIRED",
         "evidence": [{"source": "CMS_PPEF_ENROLLMENT", "disposition": pecos}]},
        d3.to_dict(),
    ]
    inp = dimensions_to_verification_results({"dimensions": dims, "data_quality_flags": []})
    return sam, inp, V3.classify(inp)


REG_MISS = {"found": False, "excluded": False}
V4_CLEAR = {"found": False, "excluded": False, "exclusion_screening": {"outcome": "NO_MATCH"}}


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, FLAG, True, raising=False)


def test_flag_defaults_off():
    """The DECLARED default (independent of any environment override) is OFF."""
    assert type(settings).model_fields[FLAG].default is False


def test_before_registration_miss_is_clear_and_b1_rule001_flag_off(monkeypatch):
    """BEFORE (main / flag OFF): the registration-only miss is read as an exclusion clear."""
    monkeypatch.setattr(settings, FLAG, False, raising=False)
    sam, inp, res = _classify(REG_MISS)
    assert sam.normalized_values["screening_leg"] == "registration_only"
    assert inp["sources"]["sam_gov"]["status"] == "clear"
    assert (res.bucket, res.rule_code) == ("B1", "RULE-001")
    assert res.evidence_summary["by_state"]["clear"] >= 2 and res.evidence_summary["sources_unavailable"] == 0


def test_after_registration_miss_is_unavailable_for_exclusion_flag_on(flag_on):
    """AFTER (flag ON): classifier sees `unavailable`, never `clear`; completeness counts it as unavailable.
    Bucket/rule are UNCHANGED (B1 via RULE-001): RULE-001 has no positive SAM condition."""
    sam, inp, res = _classify(REG_MISS)
    assert inp["sources"]["sam_gov"]["status"] == "unavailable"
    assert inp["sources"]["sam_gov"]["status"] not in ("clear", "verified")
    assert sam.disposition == "NOT_FOUND"          # disposition untouched
    assert (res.bucket, res.rule_code) == ("B1", "RULE-001")
    assert res.evidence_summary["sources_unavailable"] == 1
    assert res.evidence_summary["by_state"]["clear"] == 1   # only OIG LEIE remains a clear


def test_after_with_pecos_also_unavailable_is_rule002_b1_partial(flag_on):
    """When PECOS is unavailable too, RULE-002 (B1 partial, any_unavailable pecos/sam_gov) is the matched rule."""
    _, inp, res = _classify(REG_MISS, pecos="UNAVAILABLE")
    assert inp["sources"]["sam_gov"]["status"] == "unavailable"
    assert (res.bucket, res.rule_code) == ("B1", "RULE-002")


def test_genuine_exclusion_leg_clear_is_unaffected(flag_on):
    """A result carrying the v4 exclusion leg is an exclusion screen: still `clear`, still RULE-001."""
    sam, inp, res = _classify(V4_CLEAR)
    assert sam.normalized_values["screening_leg"] == "exclusion_and_registration"
    assert inp["sources"]["sam_gov"]["status"] == "clear"
    assert (res.bucket, res.rule_code) == ("B1", "RULE-001")


def test_exclusion_labelled_item_is_not_a_registration_only_miss(flag_on):
    """An item labelled `exclusion` (UEI path) is not a registration-only branch."""
    assert not is_registration_only_sam_miss(
        "EXCLUSION_REVOCATION",
        {"source": "SAM_GOV", "disposition": "NOT_FOUND", "normalized_values": {"screening_leg": "exclusion"}})


def test_potential_match_still_b4_with_flag_on(flag_on):
    """REVIEW (potential match) is not a miss: still `not_found` -> RULE-005 B4."""
    _, inp, res = _classify({"found": True, "excluded": True, "ambiguous": False, "exclusions": [{}]})
    assert inp["sources"]["sam_gov"]["status"] == "not_found"
    assert (res.bucket, res.rule_code) == ("B4", "RULE-005")


@pytest.mark.parametrize("dimension,item,expected", [
    ("EXCLUSION_REVOCATION", {"source": "SAM_GOV", "disposition": "NOT_FOUND",
                              "normalized_values": {"screening_leg": "registration_only"}}, "unavailable"),
    ("EXCLUSION_REVOCATION", {"source": "SAM_GOV", "disposition": "NOT_FOUND",
                              "normalized_values": {"screening_leg": "exclusion_and_registration"}}, "clear"),
    ("EXCLUSION_REVOCATION", {"source": "SAM_GOV", "disposition": "NOT_FOUND"}, "clear"),
    ("EXCLUSION_REVOCATION", {"source": "OIG_LEIE", "disposition": "NOT_FOUND",
                              "normalized_values": {"screening_leg": "registration_only"}}, "clear"),
    ("IDENTITY", {"source": "SAM_GOV", "disposition": "NOT_FOUND",
                  "normalized_values": {"screening_leg": "registration_only"}}, "not_found"),
])
def test_item_state_only_changes_the_registration_only_sam_exclusion_item(flag_on, dimension, item, expected):
    """Narrow scope of the change: only a registration-only SAM NOT_FOUND on the exclusion dimension moves."""
    assert item_state(dimension, item) == expected


def test_item_state_flag_off_is_identical_to_legacy(monkeypatch):
    monkeypatch.setattr(settings, FLAG, False, raising=False)
    item = {"source": "SAM_GOV", "disposition": "NOT_FOUND",
            "normalized_values": {"screening_leg": "registration_only"}}
    assert item_state("EXCLUSION_REVOCATION", item) == "clear"
