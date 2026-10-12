"""Exact path by which a SAM result reaches B1/B4, pinned (corrects an earlier loose statement).

CORRECTION. "A name-only no-hit carries B4 weight" is FALSE on the persisted-evidence paths. The seed rule
RULE-005 (SEED_RULES_V3) lists `sam_gov == not_found` as a B4 disqualifier, but the translator
`arc_pipeline.evidence_item_state` turns a NOT_FOUND item on the EXCLUSION_REVOCATION dimension into the
classifier state `clear`, so a clean name screen reaches RULE-001 (B1), not RULE-005. What reaches B4 is a REVIEW
disposition (potential match, identifier match, ambiguous) because REVIEW -> `not_found`.

Code path: evidence_assembly._sam_disposition -> EvidenceItem.disposition -> arc_pipeline.evidence_item_state
(NOT_FOUND -> "clear" on D3, REVIEW -> "not_found") -> dimensions_to_verification_results -> BucketClassifier(SEED_RULES_V3)
RULE-005 (priority 5, any_of sam_gov == not_found) / RULE-001 (none_of sam_gov == not_found).

What the tests below also pin, as DEFECTS rather than approved behaviour:
  * a registration-only miss (v3 entity search; the NPI-less `sam_name` branch) becomes NOT_FOUND -> clear, i.e. B1-eligible,
    although no exclusion screen ran;
  * a name-only POTENTIAL_MATCH becomes REVIEW -> B4 (RULE-005) as a system recommendation.
No rule, mapping or disposition is changed by this file.
"""
from __future__ import annotations

from app.Tefca.applicability import build_profile
from app.Tefca.evidence_assembly import _dimension_exclusion
from app.Tefca.evidence_dimensions import Disposition
from app.tefca_registry.bucket_classifier import BucketClassifier, SEED_RULES_V3
from app.tefca_registry.rce.arc_pipeline import dimensions_to_verification_results
from app.Tefca.mock_data import MOCK_ENTITY_INDEX


def entity(entity_id):
    return MOCK_ENTITY_INDEX[entity_id]

V3 = BucketClassifier(rules=SEED_RULES_V3)


class _R:
    def __init__(self, data):
        self.success, self.data = True, data
        self.query_timestamp, self.api_version, self.error = "t", "v", None


def _bucket(sam_result, *, key="sam_name"):
    ent = entity("rce-org-rp-006")
    d3 = _dimension_exclusion(ent, build_profile(ent), {
        "leie_org": _R({"excluded": False, "exclusion_found": False}),
        key: sam_result,
        "cms_revocation": _R({"matches": [], "result": "NONE"}),
    })
    sam = next(i for i in d3.items if i.source == "SAM_GOV")
    evidence = {"dimensions": [
        {"dimension": "IDENTITY", "disposition": "PASS", "applicability": "REQUIRED",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]},
        d3.to_dict(),
    ], "data_quality_flags": []}
    inp = dimensions_to_verification_results(evidence)
    return sam, inp["sources"]["sam_gov"]["status"], V3.classify(inp)


def test_clean_name_screen_does_not_reach_b4():
    sam, state, result = _bucket(_R({"found": False, "excluded": False, "ambiguous": False}))
    assert sam.disposition == Disposition.NOT_FOUND.value
    # WP4-SAM: this NPI-less name screen is registration-only, so the translator no longer calls it `clear`
    # (it was NOT_FOUND on D3 -> clear). Still NOT `not_found`: it does not reach B4. B1 is still reachable (policy C4).
    assert state == "unavailable"
    assert result.rule_code != "RULE-005" and result.bucket != "B4"
    assert result.bucket == "B1"


def test_a_registration_only_miss_is_no_longer_read_as_a_clean_exclusion_screen_but_b1_is_still_reachable():
    sam, state, result = _bucket(_R({"found": False, "excluded": False}))
    leg = (sam.normalized_values or {}).get("screening_leg")
    assert leg == "registration_only"             # the new diagnostic field labels it...
    assert state == "unavailable"                 # ...and (WP4-SAM) the classifier no longer reads it as an exclusion clear
    assert result.bucket == "B1"                  # bucket rules unchanged (decision C4 pending): B1 still reachable


def test_a_name_only_potential_match_reaches_b4_by_the_review_mapping():
    sam, state, result = _bucket(_R({"found": True, "excluded": True, "ambiguous": False, "exclusions": [{}]}))
    assert sam.disposition == Disposition.REVIEW.value
    assert state == "not_found"                   # REVIEW -> not_found
    assert result.bucket == "B4" and result.rule_code == "RULE-005"


def test_ambiguity_is_honoured_only_under_the_key_verify_sets_DEFECT_on_the_npi_less_path():
    # `verify()` sets `identity_ambiguous`; `lookup_by_name` (the NPI-less `sam_name` branch) returns `ambiguous`.
    # `_sam_disposition` reads only `identity_ambiguous`, so a multi-entity name match on the NPI-less path is
    # reported NOT_FOUND ("No match found") and then classified B1-eligible.
    sam, state, result = _bucket(_R({"found": True, "matched_by": "name", "ambiguous": True, "excluded": False}))
    # WP4-SAM: labelled registration_only -> `unavailable` for exclusion (was `clear`); bucket rules unchanged.
    assert sam.disposition == Disposition.NOT_FOUND.value and state == "unavailable" and result.bucket == "B1"
    # Same facts under the key the other path uses: REVIEW -> B4.
    sam, state, result = _bucket(_R({"found": True, "identity_ambiguous": True, "excluded": False}), key="sam_exclusion")
    assert sam.disposition == Disposition.REVIEW.value and result.bucket == "B4"


def test_unavailable_sam_is_not_b4_and_still_b1_by_policy():
    class _U:
        success = False
        error = "SAM_GOV unavailable: HTTP 404 with an empty body"
        data = None
        query_timestamp = api_version = None
    sam, state, result = _bucket(_U())
    assert sam.disposition == Disposition.UNAVAILABLE.value and state == "unavailable"
    assert result.bucket == "B1"                  # RULE-001 has no positive SAM requirement (open policy P1)


def test_registration_miss_is_labelled_not_an_exclusion_answer_while_disposition_and_bucket_are_unchanged():
    """#127 labels the leg; WP4-SAM (#147) corrects the classifier signal. Same NPI-less registration miss: the evidence
    says registration_only and carries no exclusion outcome; disposition stays NOT_FOUND; the classifier state is
    now `unavailable` (was `clear`); bucket B1 is unchanged (rules untouched, decision C4 pending)."""
    sam, state, result = _bucket(_R({"found": False, "excluded": False}))
    nv = sam.normalized_values or {}
    assert nv.get("screening_leg") == "registration_only"
    assert "outcome" not in nv                              # no exclusion answer is claimed
    assert (sam.disposition, state, result.bucket) == (Disposition.NOT_FOUND.value, "unavailable", "B1")
    # contrast: a result carrying the v4 exclusion leg is labelled as an exclusion screen (same disposition class)
    both, _, _ = _bucket(_R({"found": False, "excluded": False, "exclusion_screening": {"outcome": "NO_MATCH"}}))
    assert both.normalized_values["screening_leg"] == "exclusion_and_registration"
