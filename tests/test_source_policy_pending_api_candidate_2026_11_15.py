"""CodeRabbit on #233 (reproduced): the dated-extract fallback must replace only an API state that did NOT answer.
A live SAM candidate (`possible_match`) is a review reason; a clear extract must never clear it. Synthetic only."""
import pytest

from app.tefca_registry.rce import source_policy as sp

pytestmark = pytest.mark.regression
POLICY = {"id": "11111111-1111-1111-1111-111111111111", "version": 1, "source": "sam_gov", "role": "mandatory",
          "applicability": {}, "policy_reference": "M-1 owner decision (test)", "approval_reference": "22222222-2222-2222-2222-222222222222"}
FACTS = {"has_submitted_npi": True}


@pytest.mark.parametrize("bucket", ["B1", "B2"])
@pytest.mark.parametrize("extract", ["clear", "verified"])
def test_a_live_api_candidate_is_b3_pending_even_when_the_extract_is_clear(bucket, extract):
    sources = {"sam_gov": {"status": "possible_match"}, "sam_extract": {"status": extract}}
    out = sp.apply_source_policy(bucket, "RULE-001", sources, FACTS, [POLICY])
    assert (out[0], out[2]) == ("B3", "EXCLUSION_POSSIBLE_MATCH_PENDING") and out[3] is POLICY


@pytest.mark.parametrize("live", ["not_checked", "unavailable", "failed", None])
def test_the_extract_fallback_still_applies_to_an_api_that_did_not_answer(live):
    sources = {"sam_extract": {"status": "clear"}}
    if live:
        sources["sam_gov"] = {"status": live}
    assert sp.apply_source_policy("B1", "RULE-001", sources, FACTS, [POLICY])[:3] == ("B1", "RULE-001", None)


def test_an_answered_adverse_api_state_is_still_left_to_the_rules():
    for st in ("excluded", "debarred", "not_found", "deactivated"):
        sources = {"sam_gov": {"status": st}, "sam_extract": {"status": "clear"}}
        assert sp.apply_source_policy("B1", "RULE-001", sources, FACTS, [POLICY])[2] is None
