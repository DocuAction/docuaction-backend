"""An extract candidate on a record the policy never looks at must still be visible on the review (2026-11-26).

Owner: "Ensure a no-NPI record's extract candidate remains visible as a pending exclusion concern in its
rationale/work item - not merely buried in evidence. Preserve the distinction between a candidate and a confirmed
exclusion."

WHY IT WAS BURIED: the mandatory-source policy acts only on a B1/B2 outcome (`source_policy.apply_source_policy`:
"if not policies or bucket not in (\"B1\", \"B2\"): return"). A record with no NPI cannot be asked of NPPES or
CMS, so no B1/B2 rule matches and it is B3 by DEFAULT-UNMATCHED before the policy is consulted; RULE-008 reads
`sam_gov` and `oig_leie`, not `sam_extract`. The candidate therefore lived only on the evidence row.

WHAT CHANGED: the review's rationale gains one sentence and its results one key, drawn from the extract evidence.
Bucket, rule code, rule version and policy are exactly what they were. Synthetic data; real writer.
"""
from __future__ import annotations

import inspect

import pytest

from app.reports.data import october_review_loader as ld
from app.tefca_registry import supervisor_ops, workspace
from app.tefca_registry.rce import arc_pipeline as ap
from rce_traceability_support import rolled_back_db  # noqa: F401 - fixture
from test_mandatory_sam_policy_cases_2026_11_26 import _policy_text, configuration  # noqa: F401 - fixture
from test_sam_extract_bulk_run_trace_2026_11_26 import (  # noqa: F401 - `batch` is a fixture
    B1, EXTRACT_FILE, POLICY_B3, UNMATCHED_B3, _run, _today, batch,
)

pytestmark = pytest.mark.asyncio

KEY = ap.PENDING_EXCLUSION_CANDIDATE_KEY
UNMATCHED_TEXT = ("[DEFAULT-UNMATCHED] No active rule matched these results, so the review defaults to B3 for "
                  "manual examination rather than being passed silently. Rule set version in force: 7 active "
                  "rule(s), evaluated in priority order (RULE-005v4, RULE-006v4, RULE-008v4, RULE-001v4, "
                  "RULE-002v4, RULE-003v4, RULE-004v4).")


def _never_calls_it_an_exclusion(text):
    lowered = text.lower()
    for claim in ("is excluded", "was excluded", "excluded entity", "is debarred", "exclusion confirmed",
                  "confirmed applicable exclusion"):
        assert claim not in lowered, (claim, text)
    assert lowered.count("confirmed exclusion") == lowered.count("not a confirmed exclusion")


async def test_a_no_npi_record_with_an_extract_candidate_says_so_on_the_review(batch):
    out = await _run(batch)
    f = out["name_only_extract_candidate_by_name"]
    # THE SAME bucket, rule and rule version as before.
    assert (f.bucket, f.rule, f.rule_version) == UNMATCHED_B3
    sentence = (f" Pending exclusion concern: the dated SAM public exclusions extract (edition {_today()}, "
                f"{EXTRACT_FILE}) returned 1 candidate match(es) for this record (matched by "
                f"normalized_business_name). A candidate is not a confirmed exclusion; an analyst must confirm or "
                f"dismiss it. The bucket and rule above are unchanged.")
    assert f.rationale == UNMATCHED_TEXT + sentence          # the old text, unchanged, then the one new sentence
    concern = f.results[KEY]
    assert concern == {
        "source": "sam_extract", "status": "CANDIDATE_REQUIRES_CONFIRMATION", "confirmed_exclusion": False,
        "candidate_count": 1, "matched_by": ["normalized_business_name"], "extract_filename": EXTRACT_FILE,
        "publication_date": _today(), "extract_sha256": "0" * 64, "note": concern["note"]}
    _never_calls_it_an_exclusion(f.rationale)
    _never_calls_it_an_exclusion(concern["note"])
    # the record report: its SAM extract cell says the same thing, and its reasons column is this rationale
    assert ld._sam_extract_cell(f.extract_row).startswith(
        "CANDIDATE MATCH (1) - NOT a confirmed exclusion; analyst determination required")
    assert "reasons = _s(rev.classification_rationale)" in inspect.getsource(ld)


async def test_every_other_record_is_exactly_as_it_was(batch):
    out = await _run(batch)
    clean = out["name_only_clean"]
    assert (clean.bucket, clean.rule, clean.rule_version) == UNMATCHED_B3
    assert clean.rationale == UNMATCHED_TEXT and KEY not in clean.results
    for label in ("npi_clean_1", "npi_clean_2", "npi_clean_3"):
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == B1
        assert KEY not in out[label].results and "Pending exclusion concern" not in out[label].rationale
    # A record WITH an NPI and a candidate is already named by the policy: nothing is added to it.
    for label in ("npi_extract_candidate_by_npi", "npi_extract_candidate_by_name"):
        f = out[label]
        assert (f.bucket, f.rule, f.rule_version) == POLICY_B3
        assert f.rationale == _policy_text("EXCLUSION_POSSIBLE_MATCH_PENDING", batch.configuration)
        assert KEY not in f.results


def test_the_concern_is_decided_from_the_extract_state_alone():
    evidence = {"dimensions": [{"dimension": "EXCLUSION_REVOCATION", "evidence": [
        {"source": ap.EXTRACT_EVIDENCE_SOURCE, "disposition": "REVIEW",
         "original_values": {"candidate_count": 2, "match_methods_matched": ["npi"],
                             "extract_filename": "F.ZIP", "publication_date": "2026-11-25",
                             "extract_sha256": "ab"}}]}]}
    candidate = {"sam_extract": {"status": "possible_match"}}
    concern = ap.pending_extract_candidate(evidence, candidate, "[DEFAULT-UNMATCHED] ...")
    assert (concern["candidate_count"], concern["matched_by"], concern["confirmed_exclusion"]) == (2, ["npi"], False)
    _never_calls_it_an_exclusion(ap.pending_extract_candidate_sentence(concern))
    # not for a clear, unavailable or absent extract; not when the determination already names the candidate
    for state in ("clear", "unavailable", None):
        sources = {"sam_extract": {"status": state}} if state else {}
        assert ap.pending_extract_candidate(evidence, sources, "x") is None
    assert ap.pending_extract_candidate(
        evidence, candidate, "Mandatory-source policy: EXCLUSION_POSSIBLE_MATCH_PENDING. ...") is None
    # a candidate whose evidence item is missing is still raised, with the facts it has
    bare = ap.pending_extract_candidate({}, candidate, "x")
    assert bare["status"] == "CANDIDATE_REQUIRES_CONFIRMATION" and bare["candidate_count"] is None
    assert "returned a candidate match(es)" in ap.pending_extract_candidate_sentence(bare)


def test_the_queue_and_the_case_detail_carry_the_concern():
    """Read-only pass-through beside `prior_risk_not_cleared`: the operations queue item and the case detail."""
    assert '"pending_exclusion_candidate": payload.get("pending_exclusion_candidate")' in inspect.getsource(
        supervisor_ops)
    assert '"pending_exclusion_candidate": vr.get("pending_exclusion_candidate")' in inspect.getsource(workspace)
