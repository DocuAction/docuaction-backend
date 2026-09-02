"""
The QA gate — analyst and QA decisions as immutable events.

THE CONTRACT
────────────
    SYSTEM RECOMMENDATION        review_records.classification_* (never edited)
            |
    ANALYST DETERMINATION        event #1
            |
    QA REVIEW                    event #2, by a DIFFERENT person
            |
      +-----+------+---------------+
      |            |               |
   APPROVE      RETURN          ESCALATE
      |            |               |
  REPORTABLE   new analyst     program_manager issues a
               event #3        SUPERSEDING determination (#3)
               -> QA again     -> event #1 is preserved, pointed at

NOTHING IS OVERWRITTEN. A correction is a new event. A superseding determination
POINTS AT what it supersedes and the superseded event keeps its own actor,
timestamp and rationale. There is no `override` field and no MODIFY action,
because an overwritten decision cannot be audited — and "who decided what, when"
is the only question this module exists to answer.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
It assigns no work and routes nothing to a queue. Which tier receives a B3 is
Decision D3 and is unresolved; building queue behaviour on an unmade decision
would put entities in front of the wrong reviewers. QA is what happens AFTER a
determination exists, and that is independent of who was assigned it.

It also creates no events for the 43 existing determinations. They are system
recommendations no human has resolved, and back-dating a determination for them
would manufacture a decision that never happened.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg

logger = logging.getLogger(__name__)

E = reg.ReviewDecisionEvent

#: Minimum privilege level per act. Read from `app.core.security.ROLE_HIERARCHY`
#: at call time rather than duplicated, so the ladder has one definition.
ROLE_ANALYST = "reviewer"            # 4
ROLE_QA = "qalead"                   # 6
ROLE_SUPERSEDE = "program_manager"   # 7
ROLE_SOD_EXCEPTION = "admin"         # 8

#: Minimum rationale length. Mirrors the CHECK constraint so the API refuses
#: before the database has to.
MIN_RATIONALE = 10


class QaGateRefused(RuntimeError):
    """A QA or determination act was refused. Never a silent no-op."""


# ── reads ────────────────────────────────────────────────────────────────────

async def _events(db, review_id: str) -> List[E]:
    return list((await db.execute(
        select(E).where(E.review_id == review_id).order_by(E.sequence_number)
    )).scalars().all())


async def _next_sequence(db, review_id: str) -> int:
    current = (await db.execute(
        select(func.max(E.sequence_number)).where(E.review_id == review_id)
    )).scalar()
    return int(current or 0) + 1


def _latest_determination(events: List[E]) -> Optional[E]:
    """The determination in force: the newest one nothing supersedes."""
    superseded = {e.supersedes_decision_id for e in events if e.supersedes_decision_id}
    determinations = [e for e in events
                      if e.event_type in E.DETERMINATION_EVENTS and e.id not in superseded]
    return determinations[-1] if determinations else None


def _qa_after(events: List[E], determination: E) -> List[E]:
    return [e for e in events
            if e.event_type == E.QA_REVIEW
            and e.sequence_number > determination.sequence_number]


def is_reportable(events: List[E]) -> bool:
    """A determination is reportable ONLY on an APPROVE that still stands.

    A later RETURN or ESCALATE revokes it — the determination is back in play,
    and a report must not cite it as settled.
    """
    determination = _latest_determination(events)
    if determination is None:
        return False
    subsequent = _qa_after(events, determination)
    if not subsequent:
        return False
    return subsequent[-1].qa_action == E.QA_APPROVE


def effective_determination(events: List[E]) -> Optional[Dict[str, Any]]:
    determination = _latest_determination(events)
    if determination is None:
        return None
    return {
        "decision_event_id": str(determination.id),
        "event_type": determination.event_type,
        "determination": determination.determination,
        "determined_bucket": determination.determined_bucket,
        "actor_email": determination.actor_email,
        "actor_role": determination.actor_role,
        "occurred_at": determination.occurred_at,
        "sequence_number": determination.sequence_number,
    }


def history(events: List[E]) -> List[Dict[str, Any]]:
    """The full chain, superseded events INCLUDED and marked.

    Precedence is expressed, never concealed: a superseded decision is still
    part of the record of what happened.
    """
    superseded = {e.supersedes_decision_id for e in events if e.supersedes_decision_id}
    return [{
        "sequence_number": e.sequence_number,
        "event_type": e.event_type,
        "actor_email": e.actor_email,
        "actor_role": e.actor_role,
        "occurred_at": e.occurred_at,
        "determination": e.determination,
        "determined_bucket": e.determined_bucket,
        "qa_action": e.qa_action,
        "qa_reason": e.qa_reason,
        "rationale": e.rationale,
        "escalated_to_user_id": str(e.escalated_to_user_id) if e.escalated_to_user_id else None,
        "escalation_reason": e.escalation_reason,
        "supersedes_decision_id": str(e.supersedes_decision_id) if e.supersedes_decision_id else None,
        "supersession_reason": e.supersession_reason,
        "is_superseded": e.id in superseded,
        "sod_exception": bool(e.sod_exception_granted_by),
    } for e in events]


# ── writes ───────────────────────────────────────────────────────────────────

def _actor(user) -> tuple:
    actor_id, actor_email = reg_audit.actor_of(user)
    role = str(getattr(user, "role", "") or "")
    if actor_id is None:
        raise QaGateRefused(
            "the acting user has no id; a decision event must name its actor")
    return actor_id, (actor_email or "unknown"), role


def _require_rationale(text: Optional[str], field: str) -> str:
    value = (text or "").strip()
    if len(value) < MIN_RATIONALE:
        raise QaGateRefused(
            f"{field} is mandatory and must be at least {MIN_RATIONALE} "
            f"characters — a decision without a reason is not reviewable")
    return value


async def _review_or_refuse(db, review_id: str) -> reg.ReviewRecord:
    row = (await db.execute(
        select(reg.ReviewRecord).where(reg.ReviewRecord.review_id == review_id)
    )).scalars().first()
    if row is None:
        raise QaGateRefused(f"no review exists with id {review_id}")
    return row


async def record_analyst_determination(
    db, review_id: str, *, user, determination: str,
    determined_bucket: Optional[str] = None, rationale: str,
    ip_address: Optional[str] = None, correlation_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """Event #1 (or a fresh determination after a RETURN).

    DELIBERATELY DOES NOT CHECK OWNERSHIP ITSELF. This is a shared primitive:
    `priority_review.py` also calls it, and does its OWN `require_owner`
    check at ITS call site before doing so — the established convention in
    this codebase is that ownership is a CALL-SITE decision, not something
    this shared function imposes on every caller. Many synthetic-fixture
    test setups (and potentially other internal orchestration) legitimately
    call this directly to seed a determination without going through
    claim/release at all; forcing an ownership check in here broke every one
    of them when first tried (2026-09-02 DEV certification) for no gain,
    since the actual vulnerability is specific to the interactive HTTP route
    a real analyst uses. See `review_routes.record_determination` for where
    the check now lives, mirroring `priority_review.py`'s own pattern
    exactly.
    """
    await _review_or_refuse(db, review_id)
    actor_id, actor_email, actor_role = _actor(user)
    rationale = _require_rationale(rationale, "rationale")

    if determination not in ("CONFIRM", "RECLASSIFY"):
        raise QaGateRefused("determination must be CONFIRM or RECLASSIFY")
    if determination == "RECLASSIFY" and determined_bucket not in ("B1", "B2", "B3", "B4"):
        raise QaGateRefused("RECLASSIFY requires determined_bucket in B1..B4")

    events = await _events(db, review_id)
    if is_reportable(events):
        raise QaGateRefused(
            f"{review_id} already carries a standing QA approval. Issue a "
            f"superseding determination instead of a second analyst decision.")

    event = E(
        id=uuid.uuid4(), review_id=review_id,
        sequence_number=await _next_sequence(db, review_id),
        event_type=E.ANALYST_DETERMINATION,
        actor_user_id=actor_id, actor_email=actor_email, actor_role=actor_role,
        determination=determination,
        determined_bucket=determined_bucket if determination == "RECLASSIFY" else None,
        rationale=rationale, ip_address=ip_address, correlation_id=correlation_id,
    )
    db.add(event)
    await db.flush()

    reg_audit.record(db, "analyst_determination_recorded", None,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"review_id": review_id,
                               "decision_event_id": str(event.id),
                               "determination": determination,
                               "determined_bucket": event.determined_bucket})
    return {"decision_event_id": str(event.id),
            "sequence_number": event.sequence_number,
            "event_type": event.event_type}


async def submit_qa_review(
    db, review_id: str, *, user, qa_action: str, qa_reason: str,
    escalated_to_user_id: Optional[uuid.UUID] = None,
    escalation_reason: Optional[str] = None,
    sod_exception_granted_by: Optional[uuid.UUID] = None,
    sod_exception_reason: Optional[str] = None,
    ip_address: Optional[str] = None, correlation_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """Event #2. APPROVE, RETURN or ESCALATE — never an edit of the analyst's."""
    review = await _review_or_refuse(db, review_id)
    actor_id, actor_email, actor_role = _actor(user)
    qa_reason = _require_rationale(qa_reason, "qa_reason")

    if qa_action not in (E.QA_APPROVE, E.QA_RETURN, E.QA_ESCALATE):
        raise QaGateRefused("qa_action must be APPROVE, RETURN or ESCALATE")
    if qa_action == E.QA_ESCALATE:
        if escalated_to_user_id is None:
            raise QaGateRefused("ESCALATE requires escalated_to_user_id")
        escalation_reason = _require_rationale(escalation_reason, "escalation_reason")

    events = await _events(db, review_id)
    determination = _latest_determination(events)
    if determination is None:
        raise QaGateRefused(
            f"{review_id} has no analyst determination to review. A system "
            f"recommendation is not a determination.")
    if _qa_after(events, determination) and \
            _qa_after(events, determination)[-1].qa_action == E.QA_APPROVE:
        raise QaGateRefused(
            f"{review_id} already has a standing APPROVE for the current "
            f"determination")

    # SEGREGATION OF DUTIES — checked here so the caller gets a clear refusal,
    # and again by a database trigger so a future code path cannot bypass it.
    if determination.actor_user_id == actor_id:
        if not (sod_exception_granted_by and sod_exception_reason
                and sod_exception_granted_by != actor_id):
            raise QaGateRefused(
                f"segregation of duties: {actor_email} made the determination on "
                f"{review_id} and may not QA it. An exception requires an admin "
                f"grant from a different person, with a reason.")
        sod_exception_reason = _require_rationale(
            sod_exception_reason, "sod_exception_reason")

    event = E(
        id=uuid.uuid4(), review_id=review_id,
        sequence_number=await _next_sequence(db, review_id),
        event_type=E.QA_REVIEW,
        actor_user_id=actor_id, actor_email=actor_email, actor_role=actor_role,
        qa_action=qa_action, qa_reason=qa_reason,
        escalated_to_user_id=escalated_to_user_id,
        escalation_reason=escalation_reason,
        sod_exception_granted_by=sod_exception_granted_by,
        sod_exception_reason=sod_exception_reason,
        rationale=qa_reason, ip_address=ip_address, correlation_id=correlation_id,
    )
    db.add(event)
    await db.flush()

    # `reportable_at` is DERIVED state, written only on an approve that stands.
    review.reportable_at = datetime.utcnow() if qa_action == E.QA_APPROVE else None

    reg_audit.record(db, f"qa_{qa_action.lower()}", review.entity_id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"review_id": review_id,
                               "decision_event_id": str(event.id),
                               "qa_action": qa_action,
                               "reviewed_decision_event_id": str(determination.id),
                               "sod_exception": bool(sod_exception_granted_by)})
    return {"decision_event_id": str(event.id),
            "sequence_number": event.sequence_number,
            "qa_action": qa_action,
            "reportable": qa_action == E.QA_APPROVE}


async def supersede_determination(
    db, review_id: str, *, user, supersedes_decision_id: uuid.UUID,
    determination: str, supersession_reason: str,
    determined_bucket: Optional[str] = None, rationale: Optional[str] = None,
    ip_address: Optional[str] = None, correlation_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """A NEW determination that supersedes an earlier one. Never an overwrite."""
    await _review_or_refuse(db, review_id)
    actor_id, actor_email, actor_role = _actor(user)
    supersession_reason = _require_rationale(supersession_reason, "supersession_reason")
    rationale = _require_rationale(rationale or supersession_reason, "rationale")

    if determination not in ("CONFIRM", "RECLASSIFY"):
        raise QaGateRefused("determination must be CONFIRM or RECLASSIFY")
    if determination == "RECLASSIFY" and determined_bucket not in ("B1", "B2", "B3", "B4"):
        raise QaGateRefused("RECLASSIFY requires determined_bucket in B1..B4")

    events = await _events(db, review_id)
    target = next((e for e in events if e.id == supersedes_decision_id), None)
    if target is None:
        raise QaGateRefused(
            f"decision event {supersedes_decision_id} does not belong to {review_id}")
    if target.event_type not in E.DETERMINATION_EVENTS:
        raise QaGateRefused("only a determination event can be superseded")
    if any(e.supersedes_decision_id == target.id for e in events):
        raise QaGateRefused(f"decision event {target.id} is already superseded")

    event = E(
        id=uuid.uuid4(), review_id=review_id,
        sequence_number=await _next_sequence(db, review_id),
        event_type=E.SUPERSEDING_DETERMINATION,
        actor_user_id=actor_id, actor_email=actor_email, actor_role=actor_role,
        determination=determination,
        determined_bucket=determined_bucket if determination == "RECLASSIFY" else None,
        rationale=rationale, supersedes_decision_id=target.id,
        supersession_reason=supersession_reason,
        ip_address=ip_address, correlation_id=correlation_id,
    )
    db.add(event)
    await db.flush()

    reg_audit.record(db, "determination_superseded", None,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"review_id": review_id,
                               "decision_event_id": str(event.id),
                               "supersedes": str(target.id),
                               "reason": supersession_reason})
    return {"decision_event_id": str(event.id),
            "sequence_number": event.sequence_number,
            "supersedes": str(target.id)}


async def qa_queue(db, limit: int = 100) -> List[Dict[str, Any]]:
    """Determinations awaiting QA: a determination exists, no QA follows it.

    This is NOT an analyst work queue. It assigns nothing and implies no tier —
    which tier receives which bucket is Decision D3 and is unresolved.
    """
    rows = (await db.execute(
        select(E.review_id).where(E.event_type.in_(E.DETERMINATION_EVENTS))
        .distinct().limit(limit * 4))).scalars().all()
    out: List[Dict[str, Any]] = []
    for review_id in rows:
        events = await _events(db, review_id)
        determination = _latest_determination(events)
        if determination is None or _qa_after(events, determination):
            continue
        out.append({
            "review_id": review_id,
            "awaiting_qa_since": determination.occurred_at,
            "analyst_email": determination.actor_email,
            "determination": determination.determination,
            "determined_bucket": determination.determined_bucket,
            "decision_event_id": str(determination.id),
        })
        if len(out) >= limit:
            break
    return out


async def review_state(db, review_id: str) -> Dict[str, Any]:
    events = await _events(db, review_id)
    return {
        "review_id": review_id,
        "effective_determination": effective_determination(events),
        "reportable": is_reportable(events),
        "event_count": len(events),
        "history": history(events),
    }
