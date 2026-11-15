"""CodeRabbit Major on #225: the classifier compared source states exactly, while the source policy normalises
(strip + lower-case) before treating an answer as adverse. ' Excluded ' therefore missed the exclusion rule
and the record could classify B1."""
import pytest

from app.tefca_registry.bucket_classifier import SEED_RULES_V3, BucketClassifier

pytestmark = pytest.mark.regression


def _classify(sam_state):
    results = {"sources": {"nppes": "verified", "oig_leie": "clear", "pecos": "verified", "sam_gov": sam_state},
               "fields": {}}
    return BucketClassifier(rules=[{**r, "version": 99} for r in SEED_RULES_V3]).classify(results)


@pytest.mark.parametrize("state", ["excluded", " Excluded ", "EXCLUDED", "\texcluded\n"])
def test_an_excluded_answer_is_adverse_however_it_is_written(state):
    assert _classify(state).bucket == "B4", state


def test_a_clean_answer_still_classifies_as_before_and_mapping_form_is_normalised_too():
    assert _classify("clear").bucket == "B1"
    results = {"sources": {"nppes": "verified", "oig_leie": "clear", "pecos": "verified",
                           "sam_gov": {"status": " Excluded "}}, "fields": {}}
    got = BucketClassifier(rules=[{**r, "version": 99} for r in SEED_RULES_V3]).classify(results)
    assert got.bucket == "B4"
