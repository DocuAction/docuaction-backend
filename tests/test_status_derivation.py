"""status_model — every processing outcome and review state, from plain dicts."""

from __future__ import annotations

import pytest

from app.tefca_registry.rce import status_model as sm


def snapshot(passed=True, **counts):
    eq = {"received": 10, "created": 10, "updated": 0, "matched_unchanged": 0,
          "held": 0, "rejected": 0, "missing_key": 0, "excluded": 0}
    eq.update(counts)
    eq["accounted"] = sum(eq[k] for k in ("created", "updated", "matched_unchanged",
                                          "held", "rejected", "missing_key", "excluded"))
    return {"id": "snap-1", "passed": passed, "equation": eq,
            "failure_reason": None if passed else "populations do not close"}


ALL_STAGES = list(sm.REQUIRED_STAGES) + ["VERIFICATION_READINESS"]


def succeeded(**kw):
    args = dict(job_state="SUCCEEDED", job_stage="READY_FOR_REVIEW",
                snapshot=snapshot(), stages_completed=ALL_STAGES)
    args.update(kw)
    return sm.processing_outcome(**args)


# ── processing outcome ───────────────────────────────────────────────────────

@pytest.mark.parametrize("state", [None, "QUEUED"])
def test_queued(state):
    out = sm.processing_outcome(job_state=state, job_stage="ACCEPTED", snapshot=None)
    assert out["value"] == sm.OUTCOME_QUEUED and out["code"] == "QUEUED"


def test_processing_names_the_stage():
    out = sm.processing_outcome(job_state="RUNNING", job_stage="CURATION", snapshot=None)
    assert out["code"] == "PROCESSING" and "CURATION" in out["detail"]


def test_failed_names_the_failed_stage_and_reason():
    out = sm.processing_outcome(job_state="FAILED", job_stage="PROMOTION",
                                failed_stage="PROMOTION", error_reason="boom",
                                snapshot=None)
    assert out["code"] == "FAILED"
    assert out["failed_stage"] == "PROMOTION" and "boom" in out["detail"]


def test_partial_when_a_required_stage_did_not_complete():
    out = succeeded(stages_completed=["PARSING", "QUALITY", "CURATION"])
    assert out["code"] == "PARTIALLY_PROCESSED"
    assert "PROMOTION" in out["detail"] and "RECONCILIATION" in out["detail"]


def test_partial_when_no_snapshot_exists():
    out = succeeded(snapshot=None)
    assert out["code"] == "PARTIALLY_PROCESSED"
    assert out["basis"][0] == {"criterion": "reconciliation_passed", "held": False,
                               "detail": "no reconciliation snapshot persisted"}


def test_partial_when_the_snapshot_did_not_pass():
    out = succeeded(snapshot=snapshot(passed=False))
    assert out["code"] == "PARTIALLY_PROCESSED"
    assert "populations do not close" in out["detail"]


def test_clean_requires_every_criterion_and_lists_them():
    out = succeeded()
    assert out["code"] == "COMPLETED_CLEAN"
    criteria = [b["criterion"] for b in out["basis"]]
    assert criteria == list(sm.CLEAN_CRITERIA)
    assert all(b["held"] for b in out["basis"])


@pytest.mark.parametrize("kw, criterion", [
    ({"unresolved_findings": 1}, "no_unresolved_findings"),
    ({"snapshot": snapshot(created=9, held=1)}, "no_held_records"),
    ({"snapshot": snapshot(created=9, rejected=1)}, "no_rejected_records"),
    ({"snapshot": snapshot(created=9, missing_key=1)}, "no_missing_key_records"),
    ({"failed_required_verification": 1}, "no_failed_required_verification"),
    ({"invalid_identifiers_promoted": 1}, "no_invalid_identifier_promoted"),
    ({"unresolved_conflicts": 1}, "no_unresolved_conflicts"),
    ({"unexplained_records": 1}, "no_unexplained_records"),
])
def test_each_failing_criterion_makes_it_with_exceptions(kw, criterion):
    out = succeeded(**kw)
    assert out["code"] == "COMPLETED_WITH_EXCEPTIONS"
    failed = [b["criterion"] for b in out["basis"] if not b["held"]]
    assert failed == [criterion]
    assert "exceptions remain" in out["detail"]


def test_excluded_and_updated_and_unchanged_do_not_break_clean():
    out = succeeded(snapshot=snapshot(created=5, updated=2, matched_unchanged=2,
                                      excluded=1))
    assert out["code"] == "COMPLETED_CLEAN"


def test_outcome_codes_cover_every_outcome():
    assert set(sm.OUTCOME_CODES) == set(sm.OUTCOMES)
    assert set(sm.REVIEW_CODES) == set(sm.REVIEW_STATES)


# ── review state ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", ["QUEUED", "PROCESSING", "FAILED", "PARTIALLY_PROCESSED"])
def test_not_ready_until_processing_completed(code):
    out = sm.review_state(outcome_code=code, snapshot_passed=True, open_work_items=3)
    assert out["code"] == "NOT_READY"


def test_not_ready_when_snapshot_did_not_pass():
    out = sm.review_state(outcome_code="COMPLETED_WITH_EXCEPTIONS", snapshot_passed=False)
    assert out["code"] == "NOT_READY"


def test_ready_for_analyst_review_is_not_a_cleanliness_claim():
    out = sm.review_state(outcome_code="COMPLETED_WITH_EXCEPTIONS", snapshot_passed=True,
                          open_work_items=4)
    assert out["code"] == "READY_FOR_ANALYST_REVIEW"
    assert "not a statement that the data is clean" in out["detail"]


def test_clean_with_no_items_is_still_ready_for_analyst_review():
    out = sm.review_state(outcome_code="COMPLETED_CLEAN", snapshot_passed=True)
    assert out["code"] == "READY_FOR_ANALYST_REVIEW"


def test_under_review_when_items_are_claimed():
    out = sm.review_state(outcome_code="COMPLETED_WITH_EXCEPTIONS", snapshot_passed=True,
                          open_work_items=2, claimed_work_items=1)
    assert out["code"] == "UNDER_REVIEW"


def test_ready_for_qa_when_only_determinations_remain():
    out = sm.review_state(outcome_code="COMPLETED_WITH_EXCEPTIONS", snapshot_passed=True,
                          qa_pending=3)
    assert out["code"] == "READY_FOR_QA"


def test_qa_review_when_qa_is_in_progress():
    out = sm.review_state(outcome_code="COMPLETED_WITH_EXCEPTIONS", snapshot_passed=True,
                          qa_pending=1, qa_in_progress=1)
    assert out["code"] == "QA_REVIEW"


def test_qa_approved_when_every_item_is_approved():
    out = sm.review_state(outcome_code="COMPLETED_WITH_EXCEPTIONS", snapshot_passed=True,
                          qa_approved=5)
    assert out["code"] == "QA_APPROVED"


def test_closed_wins_over_everything():
    out = sm.review_state(outcome_code="FAILED", snapshot_passed=False, closed=True)
    assert out["code"] == "CLOSED"


# ── coverage ─────────────────────────────────────────────────────────────────

def test_coverage_states():
    assert sm.coverage_state(configured=False, eligible=5, attempted=0)["state"] == sm.COVERAGE_NOT_CONFIGURED
    assert sm.coverage_state(configured=True, eligible=5, attempted=0)["state"] == sm.COVERAGE_NOT_RUN
    assert sm.coverage_state(configured=True, eligible=5, attempted=5, verified=5)["state"] == sm.COVERAGE_COMPLETE
    assert sm.coverage_state(configured=True, eligible=5, attempted=3)["state"] == sm.COVERAGE_PARTIAL
    assert sm.coverage_state(configured=True, eligible=5, attempted=5, unavailable=5)["state"] == sm.COVERAGE_UNAVAILABLE
    assert sm.coverage_state(configured=True, eligible=5, attempted=1, in_progress=True)["state"] == sm.COVERAGE_IN_PROGRESS
