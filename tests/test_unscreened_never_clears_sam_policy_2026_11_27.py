"""Unscreened is never clear: the SAM exclusion requirement under the ACTIVE DEV configuration (owner, 2026-11-27).

The owner asked for the outcome of a record whose SAM extract screen is missing, using the real evidence format, the
active mandatory-source policy and the submitted methodology; and to distinguish NOT APPLICABLE, UNAVAILABLE, NOT
SCREENED and CLEAR. Every step below is the production function, in the writer's order
(`arc_pipeline.verify_and_classify`): the connector's SourceResult -> `sam_extract_evidence_items` (EvidenceItem)
-> `withhold_unestablished_name_screens` (identity safeguard) -> `dimensions_to_verification_results` (classifier
input) -> `source_policy.enforce_on_classification` with the M-1 policy row as on DEV. The DEV database read of
2026-11-26 recorded one active row, "sam_gov mandatory v1 (M-1)" (qa-evidence/2026-11-24-release-review/PROGRESS.md:207,
277); its applicability is NOT in that record. It was inserted at Gate C on 2026-11-10 by the activation script from
`PROPOSED_POLICY_M1` (applicability {}), and the later activation script's G5 refuses unless the row is mandatory with
applicability {}. Not assumed at run time: `require_m1_for_pinned_run` re-reads the active row on every pinned call
and refuses if it is not mandatory with applicability {}. The live SAM API does not answer on DEV (HTTP
404 since 2026-11-12, PROGRESS.md:222,279), so `sam_gov` is UNAVAILABLE in every case: only a completed extract screen
with no candidate satisfies M-1 (source_policy.py:170-184; methodology v1.4 §55, IN-04 §358, M-1 decision).

Pure functions only (no database).
"""
from __future__ import annotations

import pytest

from app.Tefca.connectors import SourceResult
from app.Tefca.evidence_assembly import sam_extract_evidence_items
from app.Tefca.evidence_dimensions import Dimension, EvidenceItem
from app.tefca_registry.bucket_classifier import ClassificationResult
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import source_policy as sp

DIM = Dimension.D3_EXCLUSION_REVOCATION.value
SHA = "e" * 64
#: The DEV row, as loaded by `source_policy.load_active_policies` (ids are placeholders).
DEV_M1 = [{"id": "dev-m1", "version": 1, "source": "sam_gov", "role": "mandatory", "applicability": {},
           "policy_reference": sp.PROPOSED_POLICY_M1[0]["policy_reference"], "approval_reference": "dev"}]
EDITION = {"source_dataset": "SAM_Exclusions_Public_Extract_V2", "publication_date": "2026-11-26",
           "extract_filename": "SAM_Exclusions_Public_Extract_V2_26330.ZIP", "extract_sha256": SHA}


def _extract_answer(candidate=False, npi="1234567893", org=None):
    data = {"candidate_match": candidate, "candidate_count": int(candidate), "candidates": [],
            "match_methods": ["npi"] if npi else ["normalized_business_name"],
            "match_methods_matched": (["npi"] if npi else ["normalized_business_name"]) if candidate else [],
            **EDITION}
    return SourceResult.ok("SAM_EXTRACT", data, {"npi": npi, "org": org}, "GSA-Public-Extract-V2",
                           raw_for_hash={"matches": int(candidate)})


def _sam_api_unavailable():
    return EvidenceItem(dimension=DIM, source="SAM_GOV", disposition="UNAVAILABLE",
                        query_identifier="npi=1234567893", rule_applied="SAM_EXCLUSION_SCREEN",
                        note="SAM_GOV unavailable: NOT_ROUTING (HTTP 404, empty body)").to_dict()


def _evidence(extract_result):
    items, _hit = sam_extract_evidence_items(DIM, extract_result)
    return {"dimensions": [{"dimension": DIM, "disposition": "UNAVAILABLE", "applicability": "APPLICABLE",
                            "evidence": [_sam_api_unavailable(), *[i.to_dict() for i in items]]}]}


def _b1():
    return ClassificationResult(bucket="B1", rule_code="RULE-001", rule_version=4, rule_name="clean",
                                rationale="all rule conditions met", matched_conditions=[], evidence_summary={},
                                evaluated_rules=["RULE-001"])


def _outcome(evidence, *, has_npi=True, policies=DEV_M1):
    sources = ap.dimensions_to_verification_results(evidence)["sources"]
    result, matched = sp.enforce_on_classification(_b1(), sources, {"has_submitted_npi": has_npi}, policies)
    return sources.get("sam_extract", {}).get("status"), result.bucket, result.matched_conditions, matched


def test_clear_a_completed_screen_with_no_candidate_satisfies_m1_and_b1_stands():
    state, bucket, reasons, matched = _outcome(_evidence(_extract_answer()))
    assert (state, bucket, matched) == ("clear", "B1", None)


def test_candidate_is_a_review_reason_never_clear():
    state, bucket, reasons, _ = _outcome(_evidence(_extract_answer(candidate=True)))
    assert (state, bucket, reasons) == ("possible_match", "B3", ["EXCLUSION_POSSIBLE_MATCH_PENDING"])


def test_not_screened_no_usable_identifier_is_incomplete_never_clear():
    """The entity had neither a usable NPI nor a name: the screen did not happen. Recorded as attempted-not-screened,
    classified B3 SOURCE_NOT_COMPLETED_SAM_GOV."""
    not_screened = SourceResult.unavailable(
        "SAM_EXTRACT", "no_usable_identifier: neither a valid NPI nor an organisation name is available", {})
    state, bucket, reasons, matched = _outcome(_evidence(not_screened), has_npi=False)
    assert (state, bucket, reasons) == ("unavailable", "B3", ["SOURCE_NOT_COMPLETED_SAM_GOV"])
    assert matched["id"] == "dev-m1"


@pytest.mark.parametrize("reason", ["stale_source: extract edition 2026-11-26 is 2.1 day(s) old",
                                    "extract unavailable or refused", "ENABLE_SAM_EXTRACT_SCREENING not enabled"])
def test_unavailable_source_is_incomplete_never_clear(reason):
    state, bucket, reasons, _ = _outcome(_evidence(SourceResult.unavailable("SAM_EXTRACT", reason, {})))
    assert (state, bucket, reasons) == ("unavailable", "B3", ["SOURCE_NOT_COMPLETED_SAM_GOV"])


def test_withheld_name_screen_missing_evidence_is_incomplete_never_clear():
    """THE case from the review: an NPI-less record whose delivered name is not established to be the entity's. The
    name-keyed extract screen is withheld by the identity safeguard (kept, not weakened); the record then has NO extract
    state at all - and the policy treats missing exactly like unavailable: B3, never B1."""
    evidence = _evidence(_extract_answer(npi=None, org="REGISTRY ENTITY NAME"))
    kept, withheld = ap.withhold_unestablished_name_screens(
        evidence, delivered_name="A DIFFERENT DELIVERED NAME", entity_name="REGISTRY ENTITY NAME")
    assert [w["source"] for w in withheld] == ["SAM_EXTRACT_SUPPLEMENTARY"]
    state, bucket, reasons, _ = _outcome(kept, has_npi=False)
    assert (state, bucket, reasons) == (None, "B3", ["SOURCE_NOT_COMPLETED_SAM_GOV"])
    # The same record whose name IS established keeps its screen and is clear.
    same, none_withheld = ap.withhold_unestablished_name_screens(
        evidence, delivered_name="Registry  Entity Name", entity_name="REGISTRY ENTITY NAME")
    assert none_withheld == [] and _outcome(same, has_npi=False)[:2] == ("clear", "B1")


def test_not_applicable_is_a_policy_scope_fact_and_never_arises_for_sam_under_m1():
    """NOT APPLICABLE is decided only by a policy's stated applicability (`source_policy._applies`): an advisory row,
    a `requires_npi` row on a record with no NPI, or a conditional row without stated facts. M-1 on DEV is mandatory
    with applicability {} - it applies to EVERY record, so a missing SAM screen is never 'not applicable'."""
    assert all(sp._applies(p, {"has_submitted_npi": f}) for p in DEV_M1 for f in (True, False))
    conditional = [{**DEV_M1[0], "applicability": {"requires_npi": True}}]
    assert not sp._applies(conditional[0], {"has_submitted_npi": False})


def test_without_an_active_policy_missing_evidence_would_reach_b1_so_pinned_runs_require_m1():
    """Why the pinned classification refuses without M-1 (arc_pipeline.require_m1_for_pinned_run): with no active
    row the hook is a no-op and an unscreened record keeps B1."""
    state, bucket, _, _ = _outcome(_evidence(SourceResult.unavailable("SAM_EXTRACT", "extract unavailable", {})),
                                   policies=[])
    assert (state, bucket) == ("unavailable", "B1")
    with pytest.raises(ap.SamExtractEditionMismatch, match="M-1"):
        ap.require_m1_for_pinned_run([])
    with pytest.raises(ap.SamExtractEditionMismatch, match="M-1"):
        ap.require_m1_for_pinned_run([{**DEV_M1[0], "applicability": {"requires_npi": True}}])
    ap.require_m1_for_pinned_run(DEV_M1)
