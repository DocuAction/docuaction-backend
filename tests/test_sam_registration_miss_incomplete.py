"""WP4-SAM: a SAM registration-only miss never reaches the classifier as an exclusion clear. DB-free.

Stacked on #127 (which adds `normalized_values.screening_leg`). Two separate things:

(a) SOURCE TRUTH (this change, unconditional): a NOT_FOUND SAM item labelled `registration_only` is
    `unavailable` for the exclusion question, never `clear`.
(b) CLASSIFICATION POLICY (NOT changed; decision C4 pending): bucket rules, required-source applicability
    and `verified` semantics are exactly as before. B1 is STILL reachable with `sam_gov` unavailable:
    RULE-001 has no positive SAM condition, and RULE-002 is B1-partial when PECOS is also unavailable.
    The bucket assertions below pin that fact; they are not an endorsement of it.

`_legacy_state` below is the pre-change reading (main's `evidence_item_state`), kept as the contrast helper.
"""
from __future__ import annotations

import pytest

from app.Tefca.applicability import build_profile
from app.Tefca.evidence_assembly import _dimension_exclusion
from app.Tefca.mock_data import MOCK_ENTITY_INDEX
from app.tefca_registry.bucket_classifier import BucketClassifier, SEED_RULES_V3
from app.tefca_registry.rce.arc_pipeline import (
    dimensions_to_verification_results, evidence_item_state, is_registration_only_sam_miss, item_state)

V3 = BucketClassifier(rules=SEED_RULES_V3)


def _legacy_state(dimension, item):
    """OLD logic (main bc5f7d71): disposition only, label ignored."""
    return evidence_item_state(dimension, item.get("disposition"))


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


def test_before_legacy_reading_of_a_registration_miss_was_clear():
    """BEFORE (old logic, contrast only): the same item read as an exclusion clear."""
    sam, _, _ = _classify(REG_MISS)
    item = {"source": "SAM_GOV", "disposition": sam.disposition, "normalized_values": sam.normalized_values}
    assert sam.normalized_values["screening_leg"] == "registration_only"
    assert _legacy_state("EXCLUSION_REVOCATION", item) == "clear"
    assert item_state("EXCLUSION_REVOCATION", item) == "unavailable"


def test_after_registration_miss_is_unavailable_for_exclusion_but_b1_is_still_reachable():
    """AFTER: classifier sees `unavailable`, never `clear`; the summary counts it as unavailable.
    Bucket/rule are UNCHANGED (B1 via RULE-001): RULE-001 has no positive SAM condition (policy C4)."""
    sam, inp, res = _classify(REG_MISS)
    assert inp["sources"]["sam_gov"]["status"] == "unavailable"
    assert inp["sources"]["sam_gov"]["status"] not in ("clear", "verified")
    assert sam.disposition == "NOT_FOUND"          # disposition untouched
    assert (res.bucket, res.rule_code) == ("B1", "RULE-001")
    assert res.evidence_summary["sources_unavailable"] == 1
    assert res.evidence_summary["by_state"]["clear"] == 1   # only OIG LEIE remains a clear


def test_after_with_pecos_also_unavailable_is_rule002_b1_partial():
    """When PECOS is unavailable too, RULE-002 (B1 partial, any_unavailable pecos/sam_gov) is the matched rule."""
    _, inp, res = _classify(REG_MISS, pecos="UNAVAILABLE")
    assert inp["sources"]["sam_gov"]["status"] == "unavailable"
    assert (res.bucket, res.rule_code) == ("B1", "RULE-002")


def test_genuine_exclusion_leg_clear_is_unaffected():
    """A result carrying the v4 exclusion leg is an exclusion screen: still `clear`, still RULE-001."""
    sam, inp, res = _classify(V4_CLEAR)
    assert sam.normalized_values["screening_leg"] == "exclusion_and_registration"
    assert inp["sources"]["sam_gov"]["status"] == "clear"
    assert (res.bucket, res.rule_code) == ("B1", "RULE-001")


def test_exclusion_labelled_item_is_not_a_registration_only_miss():
    """An item labelled `exclusion` (UEI path) is not a registration-only branch."""
    assert not is_registration_only_sam_miss(
        "EXCLUSION_REVOCATION",
        {"source": "SAM_GOV", "disposition": "NOT_FOUND", "normalized_values": {"screening_leg": "exclusion"}})


def test_potential_match_still_b4_with():
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
def test_item_state_only_changes_the_registration_only_sam_exclusion_item(dimension, item, expected):
    """Narrow scope of the change: only a registration-only SAM NOT_FOUND on the exclusion dimension moves."""
    assert item_state(dimension, item) == expected


def test_manual_review_persisted_rows_use_the_same_translation():
    """review_service.apply_persisted_exclusion_evidence (manual path) is consistent with the bulk path."""
    import uuid
    from types import SimpleNamespace
    from app.tefca_registry import review_service as svc

    def row(leg):
        return SimpleNamespace(id=uuid.uuid4(), source="SAM_GOV", disposition="NOT_FOUND",
                               generation_timestamp="2026-10-02T12:00:00+00:00", review_id="REV-1",
                               rule_applied="D3", query_identifier="x",
                               normalized_values={"screening_leg": leg} if leg else {})
    for leg, expected in (("registration_only", svc.UNAVAILABLE), ("exclusion_and_registration", "clear"),
                          (None, "clear")):   # unlabelled legacy rows keep the legacy reading
        sources = {"sam_gov": {"status": svc.NOT_CHECKED}}
        svc.apply_persisted_exclusion_evidence(sources, {"SAM_GOV": row(leg)})
        assert sources["sam_gov"]["status"] == expected
