"""Reviewed (analyst + QA) disposition for the record-level report.

The record loader reports the PERSISTED AUTOMATED classification. Analysts and QA
leads act through `review_decision_events` (append-only). This module reads those
events and states, per review, what a client report may say about them - without
touching the automated bucket, its rationale, a HELD status or any hold.

RULES (owner instruction 2026-11-15; the two policy defaults are marked)
  * The automated bucket and rationale stay as persisted. The reviewed disposition
    is reported in SEPARATE columns.
  * Only events at or before the report cutoff are visible.
  * The governing determination is the latest determination event that no visible
    later event supersedes.
  * A disposition is reported as APPROVED only when the latest visible QA event
    AFTER the governing determination is an APPROVE (policy default - DECISIONS-
    NEEDED: which QA statuses count as approved in the client report).
  * Pending, returned, escalated and superseded decisions are never shown as
    approved; the status says what they are.
  * Reporting never changes a status. A HELD record keeps its HELD status and its
    blocking reason; the reviewed column says so.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

NOT_REVIEWED = "No analyst determination recorded"
AWAITING_QA = "Analyst determination recorded; awaiting QA"
QA_RETURNED = "Returned by QA; not approved"
QA_ESCALATED = "Escalated by QA; not approved"
APPROVED = "Approved by QA"

#: Statuses that count as "approved" in the client report. Policy default, not an
#: owner decision.
APPROVING_QA_ACTIONS = frozenset({"APPROVE"})


def _ts(value: Any) -> str:
    return value.isoformat(timespec="seconds") if value is not None else ""


def summarize(events: Iterable[Any], *, automated_bucket: Optional[str]) -> Dict[str, str]:
    """One review's visible events -> the four report values.

    `events` carry: id, sequence_number, event_type, determination,
    determined_bucket, qa_action, supersedes_decision_id, occurred_at.
    """
    ordered = sorted(events, key=lambda e: (e.sequence_number, _ts(e.occurred_at)))
    superseded = {e.supersedes_decision_id for e in ordered if e.supersedes_decision_id}
    determinations = [e for e in ordered
                      if e.event_type in ("ANALYST_DETERMINATION", "SUPERSEDING_DETERMINATION")
                      and e.id not in superseded]
    if not determinations:
        return {"disposition": "", "date": "", "qa_status": NOT_REVIEWED, "basis": ""}
    gov = determinations[-1]
    if gov.determination == "RECLASSIFY":
        proposed = f"RECLASSIFY to {gov.determined_bucket or '(none recorded)'}"
        shown = f"Reclassified {automated_bucket or 'unclassified'} -> {gov.determined_bucket}"
    else:
        proposed = f"CONFIRM {automated_bucket or 'unclassified'}"
        shown = f"Confirmed {automated_bucket or 'unclassified'}"
    qa = [e for e in ordered if e.event_type == "QA_REVIEW" and e.sequence_number > gov.sequence_number]
    last_qa = qa[-1] if qa else None
    basis = (f"analyst determination #{gov.sequence_number} ({proposed}) at {_ts(gov.occurred_at)}"
             + (f"; QA {last_qa.qa_action} at {_ts(last_qa.occurred_at)}" if last_qa else ""))
    if last_qa is None:
        return {"disposition": "", "date": "", "qa_status": AWAITING_QA, "basis": basis}
    if last_qa.qa_action in APPROVING_QA_ACTIONS:
        return {"disposition": shown, "date": _ts(last_qa.occurred_at), "qa_status": APPROVED, "basis": basis}
    status = QA_RETURNED if last_qa.qa_action == "RETURN" else QA_ESCALATED
    return {"disposition": "", "date": "", "qa_status": status, "basis": basis}


BUCKETS = ("B1", "B2", "B3", "B4")


def final_bucket_decision(events: Iterable[Any], *, automated_bucket: Optional[str]) -> Dict[str, str]:
    """The reclassification the report may apply to a record's REPORTING bucket.

    Owner authorization 2026-11-16 (synthetic testing only): an eligible,
    independently QA-approved RECLASSIFY becomes the final reporting bucket.
    Same visibility rules as `summarize` (the caller passes only events at or
    before the cutoff; a superseded determination never governs). Returns
    {"bucket": target or "", "note": why it is / is not applied}. Nothing is
    written; the automated bucket and the event history stay as stored.

    Fail-closed: the approval must come from a recorded actor different from the
    analyst's recorded actor. HELD and unclassified records are decided by the
    dataset builder, not here. A B1 target additionally needs `b1_evidence_verdict`.
    """
    ordered = sorted(events, key=lambda e: (e.sequence_number, _ts(e.occurred_at)))
    superseded = {e.supersedes_decision_id for e in ordered if e.supersedes_decision_id}
    determinations = [e for e in ordered
                      if e.event_type in ("ANALYST_DETERMINATION", "SUPERSEDING_DETERMINATION")
                      and e.id not in superseded]
    if not determinations:
        return {"bucket": "", "note": ""}
    gov = determinations[-1]
    qa = [e for e in ordered if e.event_type == "QA_REVIEW" and e.sequence_number > gov.sequence_number]
    last_qa = qa[-1] if qa else None
    if gov.determination != "RECLASSIFY" or last_qa is None or last_qa.qa_action not in APPROVING_QA_ACTIONS:
        return {"bucket": "", "note": ""}
    target = gov.determined_bucket
    if target not in BUCKETS:
        return {"bucket": "", "note": "QA-approved reclassification NOT applied: no valid target bucket recorded"}
    analyst, reviewer = getattr(gov, "actor_user_id", None), getattr(last_qa, "actor_user_id", None)
    if not analyst or not reviewer or str(analyst) == str(reviewer):
        return {"bucket": "", "note": (f"QA-approved reclassification to {target} NOT applied: the QA reviewer is "
                                       "not recorded as a different person from the analyst")}
    if target == automated_bucket:
        return {"bucket": "", "note": ""}
    return {"bucket": target, "note": (
        f"QA-approved reclassification {automated_bucket or 'unclassified'} -> {target} "
        f"(analyst determination #{gov.sequence_number} at {_ts(gov.occurred_at)}; in-app QA APPROVE "
        f"#{last_qa.sequence_number} by a different recorded user at {_ts(last_qa.occurred_at)})")}


MANUAL_CHECK_DATE_NOT_RECORDED = "not recorded"


def _identifier(event: Any) -> str:
    """The actor exactly as the decision event stored it; nothing is looked up or reformatted."""
    return (f"{getattr(event, 'actor_email', '') or '(no email stored)'} "
            f"(user id {getattr(event, 'actor_user_id', '') or 'not stored'}; "
            f"role {getattr(event, 'actor_role', '') or 'not stored'})")


def decision_details(events: Iterable[Any]) -> Dict[str, str]:
    """Who decided and what they wrote, for the record-level master (owner authorization 2026-11-17, part (a)).

    Same visibility rule as `summarize`: the governing (non-superseded) determination among the events the caller
    passes (already bounded by the cutoff) and the latest QA event after it. The two texts are returned VERBATIM
    from the stored rows and are never logged. No manual check date is stored for a determination, so the value
    is the literal "not recorded" - the time the decision was saved is NOT a check date and is never substituted.
    """
    ordered = sorted(events, key=lambda e: (e.sequence_number, _ts(e.occurred_at)))
    superseded = {e.supersedes_decision_id for e in ordered if e.supersedes_decision_id}
    determinations = [e for e in ordered
                      if e.event_type in ("ANALYST_DETERMINATION", "SUPERSEDING_DETERMINATION")
                      and e.id not in superseded]
    if not determinations:
        return {"analyst": "", "qa_reviewer": "", "analyst_rationale": "", "qa_reason": "", "manual_check_date": ""}
    gov = determinations[-1]
    qa = [e for e in ordered if e.event_type == "QA_REVIEW" and e.sequence_number > gov.sequence_number]
    last_qa = qa[-1] if qa else None
    return {"analyst": _identifier(gov),
            "qa_reviewer": _identifier(last_qa) if last_qa is not None else "",
            "analyst_rationale": getattr(gov, "rationale", None) or "",
            "qa_reason": (getattr(last_qa, "qa_reason", None) or "") if last_qa is not None else "",
            "manual_check_date": MANUAL_CHECK_DATE_NOT_RECORDED}


#: Exclusion sources whose STORED pending candidate keeps an approved B4 -> B2 at B4 (SAM and LEIE only).
B2_CANDIDATE_SOURCES = ("sam_gov", "sam_extract", "oig_leie")


def stored_pending_candidates(review: Any) -> Dict[str, Any]:
    """Pending SAM / LEIE candidates in the review's STORED snapshot:
    {"evaluated": bool, "pending": [names], "undetermined": [names]}.

    Owner authorization 2026-11-17, part (a): an approved B4 -> B2 on a record whose governing review stored a
    pending SAM or LEIE candidate stays B4. Reuses `shadow_reassessment.classifier_input` / `source_states`. A
    candidate is a stored status `possible_match` or a stored disposition REVIEW. BOTH stored shapes are read:
    the delivery path keeps the disposition beside the status; the single-record path
    (`review_service.apply_persisted_exclusion_evidence`) keeps it under `persisted_evidence` (or
    `persisted_evidence_seen` when the live answer was kept). With the possible-match state flag off a pending hit
    is stored as `not_found`, so the disposition is what tells a pending hit from a clean screen.

    `undetermined` names a source stored as `not_found` with NO disposition in any of those places: the snapshot
    cannot say whether that was a clean screen or a pending hit (the repo's own `risk_signals` calls this
    "potential_hit_or_unknown_origin"). It is reported as not determinable - never as "none".

    NOTHING saved after the review is read (no evidence rows, no live call), so a later candidate is invisible
    here - stated, not approximated. `evaluated` is False when the review stores no classifier input.
    """
    from app.tefca_registry.rce import shadow_reassessment as sh

    inp = sh.classifier_input(getattr(review, "verification_results", None) or {})
    if inp is None:
        return {"evaluated": False, "pending": [], "undetermined": []}
    raw_sources = inp.get("sources") or {}
    pending, undetermined = [], []
    for name, state in sh.source_states(inp).items():
        if name not in B2_CANDIDATE_SOURCES:
            continue
        raw = raw_sources.get(name) if isinstance(raw_sources.get(name), dict) else {}
        dispositions = {str(state.get("disposition") or "").upper()}
        for key in ("persisted_evidence", "persisted_evidence_seen"):
            if isinstance(raw.get(key), dict):
                dispositions.add(str(raw[key].get("disposition") or "").upper())
        dispositions.discard("")
        status = str(state.get("status") or "").lower()
        if status == "possible_match" or "REVIEW" in dispositions:
            pending.append(name)
        elif status == "not_found" and not dispositions:
            undetermined.append(name)
    return {"evaluated": True, "pending": sorted(pending), "undetermined": sorted(undetermined)}


#: Exclusion sources whose pending candidate (disposition REVIEW / state possible_match) blocks B1.
B1_CANDIDATE_SOURCES = ("sam_gov", "oig_leie", "cms_revocation", "sam_extract")
B1_NO_LATER_EVIDENCE = ("evidence saved after that review is NOT re-evaluated by this report "
                        "(no approved as-of evaluator exists)")


def b1_evidence_verdict(review: Any, *, rules: Optional[List[dict]], policy_active: bool,
                        cutoff_rows: Optional[Dict[str, Any]] = None,
                        not_attributed: Optional[List[str]] = None) -> Dict[str, Any]:
    """May an approved B4 -> B1 move be REPORTED as B1? {"established": bool, "reason": str}.

    Owner authorization 2026-11-17: B1 only when saved evidence at the cutoff
    establishes it under the recorded rule version and the source-applicability
    policy, with no unresolved B4 condition; otherwise B4 is kept and the reason
    is stated. No rule is restated here and nothing live is called.

    REUSED, NOT REBUILT
      * `shadow_reassessment.classifier_input` - the classifier input the
        governing review STORED when it was written (the loader only selects a
        review created at or before the cutoff);
      * `BucketClassifier.classify` over the rule rows stored at the review's own
        rule version (`shadow_reassessment.rules_for_version`, loaded by the
        caller);
      * `shadow_reassessment.risk_signals` for open exclusion / identity signals.

    LIMITATION, STATED RATHER THAN APPROXIMATED
      That input is the REVIEW-TIME snapshot. No existing function rebuilds a
      classifier input from evidence saved later, as of a cutoff, with the
      pipeline's record-identity and worst-state-wins rules. So this verdict can
      explain why B1 is not established, and it NEVER returns established=True:
      a record the stored evidence classified B4 reproduces B4. Establishing B1
      from later evidence needs that evaluator, under its own authorization.
    """
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from app.tefca_registry.rce import shadow_reassessment as sh

    why: List[str] = []
    rid = getattr(review, "review_id", "?")
    vr = getattr(review, "verification_results", None) or {}
    inp = sh.classifier_input(vr)
    version = getattr(review, "classification_rule_version", None)
    if inp is None:
        why.append(f"review {rid} stores no classifier input, so its saved evidence cannot be evaluated")
    elif version is None:
        why.append(f"review {rid} records no rule version")
    elif not rules:
        why.append(f"no rules are stored at rule version {version}")
    else:
        result = BucketClassifier().classify(inp, rules=rules)
        recorded = (getattr(review, "classification_bucket", None), getattr(review, "classification_rule", None))
        if (result.bucket, result.rule_code) != recorded:
            why.append(f"the stored input of review {rid} does not reproduce its recorded classification under "
                       f"rule version {version} (re-evaluated {result.bucket} by {result.rule_code or 'no rule'}); "
                       "left for a person")
        else:
            matched = "; ".join(result.matched_conditions) or "conditions met"
            why.append(f"the saved evidence of review {rid} classifies {result.bucket} under {result.rule_code} "
                       f"v{version} ({matched}) and that condition is not resolved in the saved evidence")
        signals = sh.risk_signals(inp)
        if signals:
            why.append("open risk signal(s): " + ", ".join(signals))
    pending = []
    for name, state in sh.source_states(inp or {}).items():
        if name in B1_CANDIDATE_SOURCES and (state.get("status") == "possible_match"
                                             or str(state.get("disposition") or "").upper() == "REVIEW"):
            pending.append(f"{name} (stored input)")
    for label, row in (cutoff_rows or {}).items():
        if row is not None and str(getattr(row, "disposition", "") or "").upper() == "REVIEW":
            pending.append(f"{label} (evidence row at the cutoff)")
    if pending:
        why.append("pending exclusion candidate with no recorded determination: " + ", ".join(sorted(set(pending)))
                   + "; a clear SAM extract never clears a live SAM candidate")
    if not_attributed:
        # The loader withheld these sources' rows at the cutoff as another identifier's result. They were not
        # evaluated for this record, which is stated - an unevaluated source is never "no pending candidate".
        why.append("evidence at the cutoff not attributed to this record, so not evaluated: "
                   + ", ".join(sorted(set(not_attributed))))
    for key, text in (("prior_risk_not_cleared", "an earlier risk signal on the entity has not been cleared"),
                      ("analyst_review_dimensions", "the review routed evidence dimension(s) to analyst review")):
        if vr.get(key):
            why.append(text)
    if not policy_active:
        why.append("no source-applicability policy row is active, so the mandatory-source policy was not evaluated")
    why.append(B1_NO_LATER_EVIDENCE)
    return {"established": False, "reason": "; ".join(why)}


async def load_for_reviews(db, review_ids: List[str], cutoff) -> Dict[str, List[Any]]:
    """Visible decision events per review_id, bounded by the report cutoff."""
    from sqlalchemy import select

    from app.tefca_registry import models as reg

    if not review_ids:
        return {}
    E = reg.ReviewDecisionEvent
    out: Dict[str, List[Any]] = {}
    for e in (await db.execute(
            select(E).where(E.review_id.in_(review_ids), E.occurred_at <= cutoff)
            .order_by(E.review_id, E.sequence_number))).scalars().all():
        out.setdefault(e.review_id, []).append(e)
    return out
