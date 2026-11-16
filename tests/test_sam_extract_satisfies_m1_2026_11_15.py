"""M-1 (approved 2026-11-07, OCTOBER_DECISIONS_APPROVED_2026-11-07.md): attributable SAM screening is required for B1
and "the authorized official extract method is accepted, subject to source edition, scope and date limitations.
Candidates remain separate from confirmed exclusions." The extract is its OWN source state and satisfies the
(INACTIVE until Gate C) mandatory policy when the live API did not answer; it is never rewritten into an API success
and a candidate is never a pass."""
import pytest

from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import source_policy as sp

pytestmark = pytest.mark.regression
POLICY = {"id": "11111111-1111-1111-1111-111111111111", "version": 1, "source": "sam_gov", "role": "mandatory",
          "applicability": {}, "policy_reference": "M-1 owner decision (test)", "approval_reference": "22222222-2222-2222-2222-222222222222"}
FACTS = {"has_submitted_npi": True}


def _go(sources, bucket="B1"):
    return sp.apply_source_policy(bucket, "RULE-001", sources, FACTS, [POLICY])


@pytest.mark.parametrize("live", ["unavailable", "not_checked", "failed", None])
def test_a_clean_extract_screen_satisfies_the_requirement_when_the_live_api_did_not_answer(live):
    sources = {"sam_extract": {"status": "clear"}}
    if live:
        sources["sam_gov"] = {"status": live}
    bucket, rule, reason, matched = _go(sources)
    assert (bucket, reason, matched) == ("B1", None, None)


def test_without_an_extract_result_the_requirement_is_still_not_completed():
    for sources in ({"sam_gov": {"status": "unavailable"}}, {"sam_gov": {"status": "unavailable"}, "sam_extract": {"status": "unavailable"}},
                    {"sam_extract": {"status": "not_checked"}}):
        bucket, _r, reason, matched = _go(sources)
        assert (bucket, reason) == ("B3", "SOURCE_NOT_COMPLETED_SAM_GOV") and matched is POLICY, sources


@pytest.mark.parametrize("live", ["clear", "unavailable", None])
def test_an_extract_candidate_is_a_review_reason_never_a_pass(live):
    sources = {"sam_extract": {"status": "possible_match"}}
    if live:
        sources["sam_gov"] = {"status": live}
    bucket, _r, reason, matched = _go(sources)
    assert (bucket, reason) == ("B3", "EXCLUSION_POSSIBLE_MATCH_PENDING") and matched is POLICY


def test_an_answered_adverse_live_result_is_still_owned_by_the_rule_conditions():
    bucket, _r, reason, matched = _go({"sam_gov": {"status": "excluded"}, "sam_extract": {"status": "clear"}})
    assert (bucket, reason, matched) == ("B1", None, None), "a finding is left to the rules, as before"


def test_the_extract_never_rewrites_the_live_state_and_no_policy_is_a_noop():
    sources = {"sam_gov": {"status": "unavailable"}, "sam_extract": {"status": "clear"}}
    before = {k: dict(v) for k, v in sources.items()}
    _go(sources)
    assert sources == before
    assert sp.apply_source_policy("B1", "RULE-001", sources, FACTS, []) == ("B1", "RULE-001", None, None)


def _evidence(*items):
    return {"dimensions": [{"dimension": "EXCLUSION_REVOCATION", "disposition": "PASS", "applicability": "APPLICABLE",
                            "evidence": [dict(source=s, disposition=d, query_identifier="npi=1234567893",
                                              rule_applied="x") for s, d in items]}]}


@pytest.mark.parametrize("disposition,state", [("PASS", "clear"), ("REVIEW", "possible_match"),
                                               ("UNAVAILABLE", "unavailable"), ("ERROR", "unavailable")])
def test_the_bulk_path_exposes_the_extract_as_its_own_source_state(disposition, state):
    out = ap.dimensions_to_verification_results(_evidence(("SAM_GOV", "UNAVAILABLE"), (ap.EXTRACT_EVIDENCE_SOURCE, disposition)))
    assert out["sources"]["sam_extract"]["status"] == state
    assert out["sources"]["sam_gov"]["status"] == "unavailable", "the live API result is reported as it is"


def test_an_extract_only_record_has_no_sam_gov_state():
    out = ap.dimensions_to_verification_results(_evidence((ap.EXTRACT_EVIDENCE_SOURCE, "PASS")))
    assert "sam_gov" not in out["sources"] and out["sources"]["sam_extract"]["status"] == "clear"
