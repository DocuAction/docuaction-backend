"""CodeRabbit Major on #225: the bulk path screened an NPI-less delivered record by the registry ENTITY's name
and wrote the result under the record without the W4 identity check the single-record path applies.
Owner decision W4 (2026-11-14): NFC + casefold + collapsed whitespace; punctuation, order and suffixes
preserved; no fuzzy matching; an unestablished identity never passes."""
import copy
import inspect

import pytest

from app.tefca_registry.bucket_classifier import NOT_CHECKED, BucketClassifier
from app.tefca_registry.rce import arc_pipeline as ap

pytestmark = pytest.mark.regression


def _evidence():
    return {"dimensions": [
        {"dimension": "EXCLUSION_REVOCATION", "disposition": "REVIEW", "applicability": "APPLICABLE", "evidence": [
            {"source": "OIG_LEIE", "disposition": "REVIEW", "query_identifier": "org=Other Entity Name", "rule_applied": "x"},
            {"source": "SAM_GOV", "disposition": "REVIEW", "query_identifier": "legal_name=Other Entity Name", "rule_applied": "x"},
            {"source": "CMS_REVOCATION", "disposition": "PASS", "query_identifier": "npi=1234567893", "rule_applied": "x"}]},
        {"dimension": "IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE", "evidence": [
            {"source": "NPPES", "disposition": "PASS", "query_identifier": "npi=1234567893", "rule_applied": "x"}]}]}


def test_an_unestablished_name_withholds_the_name_screens_and_nothing_else():
    original = _evidence()
    snapshot = copy.deepcopy(original)
    out, withheld = ap.withhold_unestablished_name_screens(
        original, delivered_name="Delivered Clinic", entity_name="Other Entity Name")
    assert original == snapshot, "the input is never mutated"
    assert sorted(w["source"] for w in withheld) == ["OIG_LEIE", "SAM_GOV"]
    left = [i["source"] for d in out["dimensions"] for i in d["evidence"]]
    assert sorted(left) == ["CMS_REVOCATION", "NPPES"], "NPI-keyed evidence is untouched"
    dims = {d["dimension"]: d["disposition"] for d in out["dimensions"]}
    assert dims == {"EXCLUSION_REVOCATION": "UNAVAILABLE", "IDENTITY": "PASS"}
    # The classifier then reads those sources as not checked (and the mandatory-source policy fails closed).
    results = ap.dimensions_to_verification_results(out)
    for src in ("oig_leie", "sam_gov"):
        assert BucketClassifier._source_state(results, src) == NOT_CHECKED, src


@pytest.mark.parametrize("delivered,entity", [
    ("Other Entity Name", "OTHER ENTITY NAME"), ("  other   entity name ", "Other Entity Name")])
def test_an_established_name_keeps_every_screen(delivered, entity):
    ev = _evidence()
    out, withheld = ap.withhold_unestablished_name_screens(ev, delivered_name=delivered, entity_name=entity)
    assert withheld == [] and out == ev


@pytest.mark.parametrize("delivered,entity", [
    ("Other Entity Name LLC", "Other Entity Name"), ("Other Entity Name.", "Other Entity Name"),
    ("Entity Name Other", "Other Entity Name"), ("Other Entity", "Other Entity Name"), ("", "Other Entity Name"),
    (None, None)])
def test_suffix_punctuation_order_prefix_and_blank_never_establish_identity(delivered, entity):
    _out, withheld = ap.withhold_unestablished_name_screens(_evidence(), delivered_name=delivered, entity_name=entity)
    assert len(withheld) == 2


def test_the_bulk_loop_applies_the_guard_to_npi_less_selected_records_before_persisting():
    src = inspect.getsource(ap.verify_and_classify)
    guard = src.index("withhold_unestablished_name_screens(")
    assert guard < src.index("evidence_rows_for_persistence(") < src.index("dimensions_to_verification_results(evidence)")
    assert "selected is not None and not submitted_npi" in src[guard - 200:guard]
