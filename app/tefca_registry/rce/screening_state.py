"""Four screening outcomes that must never be conflated, plus documented unavailable-source handling (Track A2).

    INCOMPLETE_SCREENING      a required source was unavailable / failed / not checked / not usable for an
                              exclusion question. NOT a clearance, NOT a finding.
    NO_HIT                    every required source ANSWERED and listed nothing. Scope-limited, not a blanket clearance.
    POTENTIAL_MATCH           a possible hit that a person has not resolved (name-only, ambiguous, identifier-matched
                              but unadjudicated). Awaiting an analyst.
    ADJUDICATED_CONFIRMATION  a human-confirmed determination: analyst CONFIRM plus a DIFFERENT person's QA approval.
                              No automated path produces this.

PURE AND ADDITIVE. This module reads the classifier input already snapshotted on a review record. It changes no
bucket, no rule, no disposition and no entity status. The only behaviour change anywhere in this track is behind
default-OFF flags (see docs/A2_SCREENING_STATES_AND_UNAVAILABLE_SOURCES.md) and is a PROPOSAL.

REGISTRATION IS NOT EXCLUSION. A SAM registration lookup ("is this entity registered?") cannot answer "is this party
excluded?". Under the proposal flag a registration-only SAM result is INCOMPLETE_SCREENING for the exclusion question.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

INCOMPLETE_SCREENING = "INCOMPLETE_SCREENING"
NO_HIT = "NO_HIT"
POTENTIAL_MATCH = "POTENTIAL_MATCH"
ADJUDICATED_CONFIRMATION = "ADJUDICATED_CONFIRMATION"
SCREENING_STATES = (INCOMPLETE_SCREENING, NO_HIT, POTENTIAL_MATCH, ADJUDICATED_CONFIRMATION)

#: Overall precedence, most conservative first. NO_HIT only when nothing else applies.
_PRECEDENCE = (ADJUDICATED_CONFIRMATION, POTENTIAL_MATCH, INCOMPLETE_SCREENING, NO_HIT)

SCHEMA = "screening-state/1"

#: Exclusion controls (same list as prior_risk.EXCLUSION_CONTROLS; restated to keep this module import-free).
EXCLUSION_CONTROLS = ("oig_leie", "sam_gov", "cms_revocation")

REASON_UNAVAILABLE = "SOURCE_UNAVAILABLE"
REASON_FAILED = "SOURCE_FAILED"
REASON_INSUFFICIENT = "NO_USABLE_IDENTIFIER"
REASON_NOT_EVALUATED = "NOT_EVALUATED"
REASON_REGISTRATION_ONLY = "REGISTRATION_LOOKUP_IS_NOT_EXCLUSION_SCREEN"

#: Documented handling of an unavailable source. Source: Task 2 (10_07_2026) "Indeterminate" row and the
#: "Important" paragraph; matrix row Indeterminate / conflict 1; decision C4 (which sources are REQUIRED) is OPEN.
#: Every timing below is DOCUMENTED INTENT; no timer, queue or notification implements it.
UNAVAILABLE_SOURCE_HANDLING: Dict[str, Any] = {
    "classification_state": "INDETERMINATE (held)",
    "implemented_on_rce_path": False,
    "rule": "An unavailable required source is never a pass, never a finding, never 'not found'.",
    "re_review_within_business_days": 1,
    "escalate_to_cor_after_business_days": 3,
    "alternative_methods": {
        "nppes": "NPPES bulk-file lookup instead of API query",
        "oig_leie": "direct OIG LEIE file download instead of web query",
        "sam_gov": "GSA daily public exclusions extract (prototype on PR #127)",
    },
    "required_sources_decision": "OPEN (COR decision C4): Task 2 says 'all required sources'; the set is not yet fixed",
    "timers_implemented": False,
}


def _entry(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {"status": raw}


def source_state(raw: Any, *, registration_only_is_incomplete: bool = False) -> Optional[Dict[str, str]]:
    """Screening state for ONE exclusion source entry of the classifier input, or None when the control does not
    apply (NOT_APPLICABLE is not a gap and not a clearance)."""
    if raw is None:
        return {"state": INCOMPLETE_SCREENING, "reason": REASON_NOT_EVALUATED}
    e = _entry(raw)
    status = e.get("status")
    disposition = (e.get("disposition") or "").upper()
    if disposition == "NOT_APPLICABLE":
        return None
    if registration_only_is_incomplete and e.get("screening_leg") == "registration_only":
        return {"state": INCOMPLETE_SCREENING, "reason": REASON_REGISTRATION_ONLY}
    if disposition in ("REVIEW", "CONFLICT", "FAIL"):
        return {"state": POTENTIAL_MATCH, "reason": "AWAITING_ANALYST"}
    if status == "unavailable":
        return {"state": INCOMPLETE_SCREENING, "reason": REASON_UNAVAILABLE}
    if status == "failed":
        return {"state": INCOMPLETE_SCREENING, "reason": REASON_FAILED}
    if disposition == "INSUFFICIENT_EVIDENCE":
        return {"state": INCOMPLETE_SCREENING, "reason": REASON_INSUFFICIENT}
    if status == "not_checked":
        return {"state": INCOMPLETE_SCREENING, "reason": REASON_NOT_EVALUATED}
    if status == "not_found":  # classifier literal for an unresolved exclusion hit on this path
        return {"state": POTENTIAL_MATCH, "reason": "AWAITING_ANALYST"}
    if status in ("clear", "verified"):
        return {"state": NO_HIT, "reason": "SOURCE_ANSWERED_NOTHING_LISTED"}
    return {"state": INCOMPLETE_SCREENING, "reason": REASON_NOT_EVALUATED}


def _adjudicated(adjudication: Optional[Dict[str, Any]]) -> bool:
    """Human-confirmed means: analyst CONFIRM and QA APPROVE by two DIFFERENT people. Anything less is not it."""
    if not adjudication:
        return False
    analyst, qa = adjudication.get("analyst_id"), adjudication.get("qa_id")
    return bool(adjudication.get("analyst_confirmed") and adjudication.get("qa_approved")
                and analyst and qa and analyst != qa)


def derive_screening_state(classifier_input: Optional[Dict[str, Any]], *,
                           adjudication: Optional[Dict[str, Any]] = None,
                           registration_only_is_incomplete: bool = False) -> Dict[str, Any]:
    """Overall + per-control screening state. Precedence: ADJUDICATED > POTENTIAL > INCOMPLETE > NO_HIT."""
    sources = (classifier_input or {}).get("sources") or {}
    controls: Dict[str, Dict[str, str]] = {}
    for control in EXCLUSION_CONTROLS:
        st = source_state(sources.get(control), registration_only_is_incomplete=registration_only_is_incomplete)
        if st is not None:
            controls[control] = st
    states = {c["state"] for c in controls.values()}
    adjudicated = _adjudicated(adjudication)
    if adjudicated:
        states.add(ADJUDICATED_CONFIRMATION)
    overall = next((s for s in _PRECEDENCE if s in states), INCOMPLETE_SCREENING)
    incomplete = [c for c, v in controls.items() if v["state"] == INCOMPLETE_SCREENING]
    return {
        "schema": SCHEMA,
        "overall": overall,
        "controls": controls,
        "incomplete_controls": incomplete,
        "no_hit_is_clearance": False,   # NO_HIT is scoped to the sources and date searched, never a blanket clearance
        "unavailable_source_handling": UNAVAILABLE_SOURCE_HANDLING if incomplete else None,
    }


def registration_only_gaps(classifier_input: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Gap rows (prior_risk.exclusion_screening_gaps shape) for exclusion results that came from a REGISTRATION
    lookup only. Used by the pipeline ONLY under the proposal flag."""
    sources = (classifier_input or {}).get("sources") or {}
    out = []
    for control in EXCLUSION_CONTROLS:
        e = sources.get(control)
        if isinstance(e, dict) and e.get("screening_leg") == "registration_only":
            out.append({"control": control, "gap": REASON_REGISTRATION_ONLY})
    return out
