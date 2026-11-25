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
