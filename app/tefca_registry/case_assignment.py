"""Who holds a review case — claim, release, assign, reassign.

WHY THIS IS NOT IN `qa_gate`
────────────────────────────
`qa_gate` says of itself: "It assigns no work and routes nothing to a queue. QA
is what happens AFTER a determination exists, and that is independent of who was
assigned it." That separation is deliberate and this module keeps it: ownership
decides WHO may act, the QA gate decides WHETHER an act stands.

OWNERSHIP IS ONE COLUMN, AND STATE IS DERIVED
─────────────────────────────────────────────
`review_records.assigned_to_user_id` is the whole of the stored ownership model.
There is no `case_status` column, on purpose.

Everything else a queue needs to know is already determined by
`review_decision_events` and read through `qa_gate`: whether a determination
stands, whether QA has answered it, and whether the answer was APPROVE, RETURN
or ESCALATE. `reportable_at` is the derived marker of a standing approval. A
status column would have to be kept in step with those events by convention, and
the day it drifted the two would disagree with no way to say which was right —
so `case_state()` computes it instead.

CLAIMING IS ONE STATEMENT
─────────────────────────
    UPDATE review_records
       SET assigned_to_user_id = :me, assigned_at = now()
     WHERE review_id = :id AND assigned_to_user_id IS NULL
    RETURNING review_id

PostgreSQL takes a row lock for the duration of the UPDATE, so of two concurrent
claimers exactly one matches `assigned_to_user_id IS NULL` and the other updates
zero rows and is told so. Read-then-write would let both read NULL and both
write; that is the race this avoids, and it is avoided by the database rather
than by a lock in one process, a retry, or a disabled button.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, func, select, text, update
from sqlalchemy import cast as sa_cast

from app.core.security import ROLE_HIERARCHY
from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg

#: The lowest role that may hold a case. Matches the analyst level the QA gate
#: already requires for a determination — a case may only be held by someone who
#: could act on it.
ROLE_ANALYST = "reviewer"          # 4

#: The lowest role that may assign work to someone else, or take it back.
#: `senior_analyst` is the first level above the analyst and is already the
#: calibration/escalation role in the staff guide. Nothing new is invented, and
#: `qalead`, `program_manager` and `admin` inherit it through the ladder.
ROLE_SUPERVISOR = "senior_analyst"  # 5

#: Derived case states. Ownership comes from the column; the rest from events.
AVAILABLE = "AVAILABLE"
CLAIMED = "CLAIMED"
SUBMITTED_FOR_QA = "SUBMITTED_FOR_QA"
RETURNED = "RETURNED"
ESCALATED = "ESCALATED"
APPROVED = "APPROVED"


class AssignmentRefused(RuntimeError):
    """An ownership act was refused. Never a silent no-op."""


def _level(role: Optional[str]) -> int:
    return ROLE_HIERARCHY.get(str(role or "").strip().lower(), 0)


def _actor(user):
    actor_id = getattr(user, "id", None)
    if actor_id is None:
        raise AssignmentRefused(
            "the acting user has no id; an ownership change must name its actor")
    return (actor_id, getattr(user, "email", None) or "unknown",
            str(getattr(user, "role", "") or ""))


def _require(user, minimum: str, what: str) -> None:
    if _level(getattr(user, "role", None)) < _level(minimum):
        raise AssignmentRefused(
            f"{what} requires at least the {minimum} role")


async def _case_or_refuse(db, review_id: str) -> reg.ReviewRecord:
    row = (await db.execute(
        select(reg.ReviewRecord).where(reg.ReviewRecord.review_id == review_id)
    )).scalars().first()
    if row is None:
        raise AssignmentRefused(f"no review exists with id {review_id}")
    return row


# ── derived state ────────────────────────────────────────────────────────────

async def case_state(db, review_id: str) -> str:
    """The case's workflow state, computed — never stored twice."""
    from app.tefca_registry.qa_gate import (_events, _latest_determination,
                                            _qa_after)

    record = await _case_or_refuse(db, review_id)
    if record.reportable_at is not None:
        return APPROVED

    events = await _events(db, review_id)
    determination = _latest_determination(events)
    if determination is not None:
        after = _qa_after(events, determination)
        if after:
            last = after[-1].qa_action
            if last == "RETURN":
                return RETURNED
            if last == "ESCALATE":
                return ESCALATED
            return APPROVED
        return SUBMITTED_FOR_QA
    return CLAIMED if record.assigned_to_user_id is not None else AVAILABLE


# ── ownership acts ───────────────────────────────────────────────────────────

async def claim(db, review_id: str, *, user,
                ip_address: Optional[str] = None) -> Dict[str, Any]:
    """Take an unowned case. Atomic: at most one claimer can win."""
    _require(user, ROLE_ANALYST, "claiming a case")
    actor_id, actor_email, actor_role = _actor(user)
    record = await _case_or_refuse(db, review_id)

    state = await case_state(db, review_id)
    if state in (APPROVED,):
        raise AssignmentRefused(
            f"{review_id} is {state} and is not available to work")

    now = datetime.utcnow()
    # The guard is IN the UPDATE. A prior SELECT would be advisory only.
    won = (await db.execute(
        update(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id,
               reg.ReviewRecord.assigned_to_user_id.is_(None))
        .values(assigned_to_user_id=actor_id, assigned_at=now)
        .returning(reg.ReviewRecord.review_id))).scalars().first()

    if won is None:
        current = await _case_or_refuse(db, review_id)
        holder = current.assigned_to_user_id
        raise AssignmentRefused(
            f"{review_id} is already held by another reviewer"
            if holder != actor_id else
            f"{review_id} is already held by you")

    reg_audit.record(db, "review_case_claimed", record.entity_id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"review_id": review_id,
                               "actor_role": actor_role,
                               "claimed_at": now.isoformat()})
    return {"review_id": review_id, "assigned_to_user_id": str(actor_id),
            "assigned_at": now, "state": await case_state(db, review_id)}


async def release(db, review_id: str, *, user, reason: Optional[str] = None,
                  ip_address: Optional[str] = None) -> Dict[str, Any]:
    """Give a case back. The holder may; a supervisor may on their behalf."""
    actor_id, actor_email, actor_role = _actor(user)
    record = await _case_or_refuse(db, review_id)

    if record.assigned_to_user_id is None:
        raise AssignmentRefused(f"{review_id} is not currently held")

    is_holder = record.assigned_to_user_id == actor_id
    if not is_holder and _level(actor_role) < _level(ROLE_SUPERVISOR):
        raise AssignmentRefused(
            f"{review_id} is held by another reviewer; releasing someone "
            f"else's case requires at least the {ROLE_SUPERVISOR} role")

    previous = record.assigned_to_user_id
    # Same conditional shape: release only what is still held by that person.
    freed = (await db.execute(
        update(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id,
               reg.ReviewRecord.assigned_to_user_id == previous)
        .values(assigned_to_user_id=None, assigned_at=None)
        .returning(reg.ReviewRecord.review_id))).scalars().first()
    if freed is None:
        raise AssignmentRefused(
            f"{review_id} changed hands while being released; nothing was done")

    reg_audit.record(db, "review_case_released", record.entity_id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"review_id": review_id,
                               "previous_owner": str(previous),
                               "released_by_holder": is_holder,
                               "actor_role": actor_role,
                               "reason": reason})
    return {"review_id": review_id, "assigned_to_user_id": None,
            "previous_owner": str(previous),
            "state": await case_state(db, review_id)}


#: Minimum length of the reason required to take a case off a live holder.
#: Mirrors `qa_gate.MIN_RATIONALE`: a handover with no stated reason is not
#: reviewable afterwards, and "reassigned" alone tells nobody why.
MIN_OVERRIDE_REASON = 10


async def assign(db, review_id: str, *, user, to_user_id: uuid.UUID,
                 reason: Optional[str] = None,
                 override_reason: Optional[str] = None,
                 ip_address: Optional[str] = None) -> Dict[str, Any]:
    """Supervisor assignment, and reassignment. Both are one act.

    Reassignment is not a separate operation: it is an assignment where a
    previous holder existed, and the audit row records both sides so the
    handover is reconstructable.

    TAKING WORK OFF A LIVE HOLDER NEEDS A STATED REASON
    ──────────────────────────────────────────────────
    Assigning an unheld case is routine and needs nothing. Moving a case that
    someone already holds is a different act — the previous holder may be part
    way through it — so it requires `override_reason`. The point is not to make
    reassignment hard; it is to make it VISIBLE, because a silent handover
    looks identical in the audit trail to a case that was never claimed.

    COMPARE-AND-SET, LIKE CLAIM AND RELEASE
    ───────────────────────────────────────
    The write is conditional on the owner this call actually observed. Two
    supervisors assigning the same case at the same moment previously both
    succeeded through a read-modify-write, so one assignment was silently lost
    AND both audit rows claimed the case had come from nobody. Now the second
    one is refused and says why.
    """
    _require(user, ROLE_SUPERVISOR, "assigning a case to someone else")
    actor_id, actor_email, actor_role = _actor(user)
    record = await _case_or_refuse(db, review_id)

    state = await case_state(db, review_id)
    if state == APPROVED:
        raise AssignmentRefused(
            f"{review_id} is APPROVED; reassigning it would imply the settled "
            f"determination is back in play. Supersede it instead.")

    previous = record.assigned_to_user_id
    if previous == to_user_id:
        raise AssignmentRefused(f"{review_id} is already assigned to that user")

    if previous is not None:
        stated = (override_reason or "").strip()
        if len(stated) < MIN_OVERRIDE_REASON:
            raise AssignmentRefused(
                f"{review_id} is held by another reviewer and is {state}. "
                f"Taking it off them requires override_reason of at least "
                f"{MIN_OVERRIDE_REASON} characters saying why.")
        override_reason = stated

    now = datetime.utcnow()
    # Conditional on the owner observed above. `IS NOT DISTINCT FROM` so the
    # unheld case (NULL) compares correctly rather than never matching.
    won = (await db.execute(
        update(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id,
               reg.ReviewRecord.assigned_to_user_id.is_not_distinct_from(previous))
        .values(assigned_to_user_id=to_user_id, assigned_at=now)
        .returning(reg.ReviewRecord.review_id))).scalars().first()
    if won is None:
        raise AssignmentRefused(
            f"{review_id} changed hands while being assigned; nothing was done. "
            f"Re-read the case and decide again.")
    await db.refresh(record)

    reg_audit.record(db, "review_case_reassigned" if previous else
                     "review_case_assigned", record.entity_id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"review_id": review_id,
                               "previous_owner": str(previous) if previous else None,
                               "new_owner": str(to_user_id),
                               "actor_role": actor_role,
                               "previous_state": state,
                               "reason": reason,
                               "override_reason": override_reason})
    return {"review_id": review_id, "assigned_to_user_id": str(to_user_id),
            "previous_owner": str(previous) if previous else None,
            "assigned_at": now, "state": await case_state(db, review_id)}


def require_owner(record: reg.ReviewRecord, user) -> None:
    """Refuse an act on a case the caller does not hold.

    Ownership, not role, is what protects a case in progress: two analysts both
    hold the reviewer role, and only one of them holds the case.
    """
    actor_id = getattr(user, "id", None)
    if record.assigned_to_user_id is None:
        raise AssignmentRefused(
            f"{record.review_id} is not held by anyone; claim it first")
    if record.assigned_to_user_id != actor_id:
        raise AssignmentRefused(
            f"{record.review_id} is held by another reviewer")


# ── queues ───────────────────────────────────────────────────────────────────

async def _queue(db, *, queue_source: Optional[str] = None,
                 intake_id=None, limit: int = 100):
    priority = sa_cast(
        reg.ReviewRecord.verification_results["priority"].astext, Integer)
    stmt = select(reg.ReviewRecord)
    if queue_source:
        stmt = stmt.where(
            reg.ReviewRecord.verification_results["queue_source"].astext
            == queue_source)
    if intake_id is not None:
        stmt = stmt.where(
            reg.ReviewRecord.verification_results["source_intake_id"].astext
            == str(intake_id))
    return stmt, priority, limit


async def available_cases(db, *, queue_source=None, intake_id=None,
                          limit: int = 100) -> List[Dict[str, Any]]:
    """Unowned, undecided work. Highest priority first, then oldest."""
    stmt, priority, limit = await _queue(db, queue_source=queue_source,
                                         intake_id=intake_id, limit=limit)
    rows = (await db.execute(
        stmt.where(reg.ReviewRecord.assigned_to_user_id.is_(None),
                   reg.ReviewRecord.reportable_at.is_(None))
        .order_by(priority.desc().nullslast(),
                  reg.ReviewRecord.created_at.asc())
        .limit(limit))).scalars().all()
    return [await _dto(db, r) for r in rows]


async def my_work(db, *, user, queue_source=None, intake_id=None,
                  limit: int = 100) -> List[Dict[str, Any]]:
    """Cases this person holds."""
    actor_id, _, _ = _actor(user)
    stmt, priority, limit = await _queue(db, queue_source=queue_source,
                                         intake_id=intake_id, limit=limit)
    rows = (await db.execute(
        stmt.where(reg.ReviewRecord.assigned_to_user_id == actor_id)
        .order_by(priority.desc().nullslast(),
                  reg.ReviewRecord.created_at.asc())
        .limit(limit))).scalars().all()
    return [await _dto(db, r) for r in rows]


async def cases_in_state(db, state: str, *, queue_source=None, intake_id=None,
                         limit: int = 500) -> List[Dict[str, Any]]:
    """Every case whose DERIVED state matches — SUBMITTED_FOR_QA, RETURNED, …"""
    stmt, priority, limit = await _queue(db, queue_source=queue_source,
                                         intake_id=intake_id, limit=limit)
    rows = (await db.execute(
        stmt.order_by(priority.desc().nullslast(),
                      reg.ReviewRecord.created_at.asc())
        .limit(limit))).scalars().all()
    out = []
    for record in rows:
        if await case_state(db, record.review_id) == state:
            out.append(await _dto(db, record))
    return out


async def _dto(db, record) -> Dict[str, Any]:
    payload = record.verification_results or {}
    return {
        "review_id": record.review_id,
        # NULL for a pre-promotion case. Never str(None).
        "entity_id": str(record.entity_id) if record.entity_id else None,
        "source_record_id": (str(record.source_record_id)
                             if record.source_record_id else None),
        "assigned_to_user_id": (str(record.assigned_to_user_id)
                                if record.assigned_to_user_id else None),
        "assigned_at": record.assigned_at,
        "state": await case_state(db, record.review_id),
        "case_classification": payload.get("case_classification"),
        "severity": payload.get("severity"),
        "priority": payload.get("priority", 50),
        "issue_codes": payload.get("issue_codes", []),
        "created_at": record.created_at,
        "reportable": record.reportable_at is not None,
    }


async def workload_by_analyst(db, *, queue_source=None,
                              intake_id=None) -> Dict[str, Any]:
    """Counts per holder, for a supervisor.

    `operational_age_days` is an INTERNAL OPERATIONAL measure. Nothing in the
    contract or COR direction sets a due date for a review case, so it must
    never be presented as an SLA or a deadline.
    """
    stmt, _, _ = await _queue(db, queue_source=queue_source,
                              intake_id=intake_id, limit=None)
    records = (await db.execute(stmt)).scalars().all()

    now = datetime.utcnow()
    by_analyst: Dict[str, int] = {}
    by_state: Dict[str, int] = {}
    ages: List[float] = []
    for record in records:
        holder = (str(record.assigned_to_user_id)
                  if record.assigned_to_user_id else "(unassigned)")
        by_analyst[holder] = by_analyst.get(holder, 0) + 1
        state = await case_state(db, record.review_id)
        by_state[state] = by_state.get(state, 0) + 1
        if record.created_at:
            ages.append((now - record.created_at).total_seconds() / 86400.0)

    return {
        "total_cases": len(records),
        "by_analyst": dict(sorted(by_analyst.items(), key=lambda kv: -kv[1])),
        "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1])),
        "operational_age_days": {
            "oldest": round(max(ages), 2) if ages else None,
            "median": round(sorted(ages)[len(ages) // 2], 2) if ages else None,
        },
        "note": ("operational_age_days is an internal operational measure, not "
                 "a contractual SLA or deadline."),
    }
