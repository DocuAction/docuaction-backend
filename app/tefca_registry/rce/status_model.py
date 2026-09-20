"""
Two-axis delivery status, derived from persisted evidence.

PROCESSING OUTCOME says what the machine did.  REVIEW STATE says where the
humans are.  They are independent: a job that SUCCEEDED can have exceptions,
and a delivery that is Ready for Analyst Review is not thereby clean.

    Processing outcome           Review state
    ------------------           ------------
    Queued                       Not Ready
    Processing                   Ready for Analyst Review
    Completed - Clean            Under Review
    Completed - With Exceptions  Ready for QA
    Partially Processed          QA Review
    Failed                       QA Approved
                                 Closed

`Completed - Clean` is a claim with a proof obligation.  It is granted only
when every one of the CLEAN_CRITERIA holds against persisted rows; the basis
list in the result names each criterion and whether it held, so the screen
can show WHY a delivery is or is not clean rather than just the word.

Pure functions over plain dicts, so they are testable without a database.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── vocabularies ─────────────────────────────────────────────────────────────

OUTCOME_QUEUED = "Queued"
OUTCOME_PROCESSING = "Processing"
OUTCOME_CLEAN = "Completed — Clean"
OUTCOME_EXCEPTIONS = "Completed — With Exceptions"
OUTCOME_PARTIAL = "Partially Processed"
OUTCOME_FAILED = "Failed"
OUTCOMES = (OUTCOME_QUEUED, OUTCOME_PROCESSING, OUTCOME_CLEAN,
            OUTCOME_EXCEPTIONS, OUTCOME_PARTIAL, OUTCOME_FAILED)

#: Machine codes for the outcomes (stable for tests, filters and reports).
OUTCOME_CODES = {
    OUTCOME_QUEUED: "QUEUED", OUTCOME_PROCESSING: "PROCESSING",
    OUTCOME_CLEAN: "COMPLETED_CLEAN", OUTCOME_EXCEPTIONS: "COMPLETED_WITH_EXCEPTIONS",
    OUTCOME_PARTIAL: "PARTIALLY_PROCESSED", OUTCOME_FAILED: "FAILED",
}

REVIEW_NOT_READY = "Not Ready"
REVIEW_READY_ANALYST = "Ready for Analyst Review"
REVIEW_UNDER_REVIEW = "Under Review"
REVIEW_READY_QA = "Ready for QA"
REVIEW_QA_REVIEW = "QA Review"
REVIEW_QA_APPROVED = "QA Approved"
REVIEW_CLOSED = "Closed"
REVIEW_STATES = (REVIEW_NOT_READY, REVIEW_READY_ANALYST, REVIEW_UNDER_REVIEW,
                 REVIEW_READY_QA, REVIEW_QA_REVIEW, REVIEW_QA_APPROVED,
                 REVIEW_CLOSED)
REVIEW_CODES = {
    REVIEW_NOT_READY: "NOT_READY", REVIEW_READY_ANALYST: "READY_FOR_ANALYST_REVIEW",
    REVIEW_UNDER_REVIEW: "UNDER_REVIEW", REVIEW_READY_QA: "READY_FOR_QA",
    REVIEW_QA_REVIEW: "QA_REVIEW", REVIEW_QA_APPROVED: "QA_APPROVED",
    REVIEW_CLOSED: "CLOSED",
}

#: Stages whose absence from a SUCCEEDED job means "partially processed".
REQUIRED_STAGES = ("PARSING", "QUALITY", "CURATION", "PROMOTION", "RECONCILIATION")

#: Every criterion that must hold for Completed - Clean.
CLEAN_CRITERIA = (
    "reconciliation_passed",
    "no_unresolved_findings",
    "no_held_records",
    "no_rejected_records",
    "no_missing_key_records",
    "no_failed_required_verification",
    "no_invalid_identifier_promoted",
    "no_unresolved_conflicts",
    "no_unexplained_records",
)


def _n(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ── processing outcome ───────────────────────────────────────────────────────

def processing_outcome(*, job_state: Optional[str], job_stage: Optional[str],
                       failed_stage: Optional[str] = None,
                       error_reason: Optional[str] = None,
                       snapshot: Optional[Dict[str, Any]],
                       stages_completed: Optional[List[str]] = None,
                       unresolved_findings: int = 0,
                       unresolved_conflicts: int = 0,
                       invalid_identifiers_promoted: int = 0,
                       failed_required_verification: int = 0,
                       unexplained_records: int = 0) -> Dict[str, Any]:
    """Derive the processing outcome and the evidence basis for it.

    `snapshot` is the latest persisted reconciliation snapshot as a dict with
    keys `passed` and `equation` (see RceReconciliationSnapshot.to_dict), or
    None when no snapshot exists.
    """
    basis: List[Dict[str, Any]] = []

    def note(criterion: str, held: bool, detail: str) -> None:
        basis.append({"criterion": criterion, "held": bool(held), "detail": detail})

    if job_state in (None, "QUEUED"):
        return _result(OUTCOME_QUEUED, basis, "The delivery is registered and waiting for a worker.")
    if job_state == "RUNNING":
        return _result(OUTCOME_PROCESSING, basis,
                       f"Processing is in progress at stage {job_stage or 'unknown'}.")
    if job_state == "FAILED":
        where = failed_stage or job_stage or "unknown"
        return _result(OUTCOME_FAILED, basis,
                       f"Processing stopped at {where}: {error_reason or 'no reason recorded'}.",
                       failed_stage=where)

    # SUCCEEDED from here on.
    completed = set(stages_completed or [])
    missing = [s for s in REQUIRED_STAGES if completed and s not in completed]
    if missing:
        note("required_stages_completed", False, f"stages not completed: {missing}")
        return _result(OUTCOME_PARTIAL, basis,
                       f"The job finished but required stage(s) did not complete: {missing}.")
    if snapshot is None:
        note("reconciliation_passed", False, "no reconciliation snapshot persisted")
        return _result(OUTCOME_PARTIAL, basis,
                       "The job finished but no reconciliation snapshot exists.")

    eq = snapshot.get("equation") or {}
    passed = bool(snapshot.get("passed"))
    note("reconciliation_passed", passed,
         f"snapshot {snapshot.get('id')} passed={passed}; received {eq.get('received')} "
         f"accounted {eq.get('accounted')}")
    if not passed:
        return _result(OUTCOME_PARTIAL, basis,
                       "Reconciliation did not pass: " + (snapshot.get("failure_reason")
                                                          or "the populations do not close."))

    checks = (
        ("no_unresolved_findings", _n(unresolved_findings) == 0,
         f"{_n(unresolved_findings)} undecided holding-severity (HIGH/CRITICAL) finding(s)"),
        ("no_held_records", _n(eq.get("held")) == 0, f"{_n(eq.get('held'))} held"),
        ("no_rejected_records", _n(eq.get("rejected")) == 0, f"{_n(eq.get('rejected'))} rejected"),
        ("no_missing_key_records", _n(eq.get("missing_key")) == 0,
         f"{_n(eq.get('missing_key'))} missing key"),
        ("no_failed_required_verification", _n(failed_required_verification) == 0,
         f"{_n(failed_required_verification)} failed required verification(s)"),
        ("no_invalid_identifier_promoted", _n(invalid_identifiers_promoted) == 0,
         f"{_n(invalid_identifiers_promoted)} invalid identifier(s) promoted"),
        ("no_unresolved_conflicts", _n(unresolved_conflicts) == 0,
         f"{_n(unresolved_conflicts)} unresolved identifier conflict(s)"),
        ("no_unexplained_records", _n(unexplained_records) == 0,
         f"{_n(unexplained_records)} record(s) without a disposition reason"),
    )
    clean = True
    for criterion, held, detail in checks:
        note(criterion, held, detail)
        clean = clean and held
    if clean:
        return _result(OUTCOME_CLEAN, basis,
                       "Every received record reconciled; no undecided holding-severity "
                       "findings, holds, rejections, missing keys, conflicts or failed "
                       "verifications remain. Lower-severity findings may remain and are "
                       "listed in the exception ledger.")
    failed = [b["detail"] for b in basis if not b["held"]]
    return _result(OUTCOME_EXCEPTIONS, basis,
                   "Processing completed and reconciled; exceptions remain: "
                   + "; ".join(failed) + ".")


def _result(value: str, basis, detail: str, **extra) -> Dict[str, Any]:
    out = {"value": value, "code": OUTCOME_CODES[value], "detail": detail,
           "basis": basis}
    out.update(extra)
    return out


# ── review state ─────────────────────────────────────────────────────────────

QUEUE_SOURCE_LABELS = {
    "data_quality": "exception work item(s) from delivery findings",
    "dq_bridge": "exception work item(s) from delivery findings",
    "post_promotion_verification": "post-promotion verification work item(s)",
    "review_cycle": "sampled review case(s)",
    "priority": "priority review case(s)",
    "unknown": "item(s) of unstated origin",
}


def _breakdown_sentence(open_breakdown) -> str:
    if not open_breakdown:
        return ""
    parts = [f"{int(n)} {QUEUE_SOURCE_LABELS.get(str(k), str(k) + ' item(s)')}"
             for k, n in sorted(open_breakdown.items(), key=lambda kv: str(kv[0]))]
    return " (" + "; ".join(parts) + ")"


def review_state(*, outcome_code: str, snapshot_passed: bool,
                 open_work_items: int = 0, claimed_work_items: int = 0,
                 determined_items: int = 0, qa_pending: int = 0,
                 qa_in_progress: int = 0, qa_approved: int = 0,
                 closed: bool = False, open_breakdown=None) -> Dict[str, Any]:
    """Where the humans are. Never a statement about data quality.

    Counts come from review records tied to the delivery (DQ bridge cases and
    review-cycle cases) and from the QA gate events.
    """
    if closed:
        return _review(REVIEW_CLOSED, "The delivery's review has been closed.")
    if outcome_code in ("QUEUED", "PROCESSING", "FAILED", "PARTIALLY_PROCESSED") \
            or not snapshot_passed:
        return _review(REVIEW_NOT_READY,
                       "Review cannot start until processing has completed and "
                       "reconciliation has passed.")
    total = _n(open_work_items) + _n(claimed_work_items) + _n(determined_items) \
        + _n(qa_pending) + _n(qa_in_progress) + _n(qa_approved)
    if _n(qa_in_progress) > 0:
        return _review(REVIEW_QA_REVIEW, f"{_n(qa_in_progress)} item(s) under QA review.")
    if _n(qa_pending) > 0 and _n(open_work_items) == 0 and _n(claimed_work_items) == 0:
        return _review(REVIEW_READY_QA, f"{_n(qa_pending)} determination(s) await QA.")
    if _n(claimed_work_items) > 0:
        return _review(REVIEW_UNDER_REVIEW,
                       f"{_n(claimed_work_items)} item(s) claimed by analysts; "
                       f"{_n(open_work_items)} still open.")
    if total > 0 and _n(open_work_items) == 0 and _n(qa_pending) == 0 \
            and _n(qa_in_progress) == 0 and _n(qa_approved) == total:
        return _review(REVIEW_QA_APPROVED, "Every item has been QA approved.")
    return _review(REVIEW_READY_ANALYST,
                   f"{_n(open_work_items)} open item(s){_breakdown_sentence(open_breakdown)}; "
                   "a review cycle may be created. "
                   "Readiness for review is a workflow state, not a statement that "
                   "the data is clean.",
                   open_breakdown=dict(open_breakdown or {}))


def _review(value: str, detail: str, **extra: Any) -> Dict[str, Any]:
    return {"value": value, "code": REVIEW_CODES[value], "detail": detail, **extra}


# ── verification coverage ────────────────────────────────────────────────────

COVERAGE_NOT_RUN = "Not Run"
COVERAGE_IN_PROGRESS = "In Progress"
COVERAGE_COMPLETE = "Complete"
COVERAGE_PARTIAL = "Partial"
COVERAGE_UNAVAILABLE = "Unavailable"
COVERAGE_NOT_CONFIGURED = "Not Configured"
COVERAGE_STATES = (COVERAGE_NOT_RUN, COVERAGE_IN_PROGRESS, COVERAGE_COMPLETE,
                   COVERAGE_PARTIAL, COVERAGE_UNAVAILABLE, COVERAGE_NOT_CONFIGURED)


def coverage_state(*, configured: bool, eligible: int, attempted: int,
                   verified: int = 0, not_found: int = 0, deactivated: int = 0,
                   failed: int = 0, unavailable: int = 0,
                   in_progress: bool = False) -> Dict[str, Any]:
    """One source's coverage of one delivery, from counted evidence rows."""
    eligible, attempted = _n(eligible), _n(attempted)
    if not configured:
        state = COVERAGE_NOT_CONFIGURED
    elif in_progress:
        state = COVERAGE_IN_PROGRESS
    elif attempted == 0:
        state = COVERAGE_NOT_RUN
    elif _n(unavailable) == attempted:
        state = COVERAGE_UNAVAILABLE
    elif attempted >= eligible and _n(unavailable) == 0:
        state = COVERAGE_COMPLETE
    else:
        state = COVERAGE_PARTIAL
    pct = round(100.0 * attempted / eligible, 1) if eligible else None
    return {
        "state": state, "configured": bool(configured), "eligible": eligible,
        "attempted": attempted, "verified": _n(verified), "not_found": _n(not_found),
        "deactivated": _n(deactivated), "failed": _n(failed),
        "unavailable": _n(unavailable), "coverage_pct": pct,
        "note": ("Connector readiness is not coverage: a source counts as attempted "
                 "only when an evidence row exists for an entity of this delivery."),
    }
