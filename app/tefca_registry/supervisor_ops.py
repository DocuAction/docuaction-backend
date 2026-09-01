"""The supervisor control plane: one operational view over every ARC engine.

WHAT THIS IS, AND WHAT IT REFUSES TO BE
───────────────────────────────────────
    DQ exceptions ┐
    sampling      ├─> review_records ──> case_assignment ──> qa_gate ──> report
    priority      ┘         │                   │              │
                            └───────────────────┴──────────────┘
                                        │
                              THIS MODULE READS IT

    Every number here is DERIVED from the tables that already own the answer.
    There is no supervisor table, no cached count, no second status machine and
    no migration. A control plane that persists its own copy of the workload
    becomes a second source of truth, and the first time the two disagree
    nobody can say which one is right.

WHAT A SUPERVISOR ACTUALLY NEEDS, AND WHAT THEY MUST NOT GET
────────────────────────────────────────────────────────────
    Management authority is not review authority. Nothing in this module can
    record a determination, approve a QA review, set `reportable_at`, or touch
    Government source or evidence. Assignment — the one write a supervisor
    legitimately owns — stays in `case_assignment`, where the segregation rules
    and the audit trail already live.

THREE THINGS THIS DELIBERATELY DOES NOT INVENT
──────────────────────────────────────────────
    1. A DEADLINE. Only a COR-supplied Task 5 deadline is a deadline. Every
       other case reports NO_DEADLINE, and a dashboard must never colour a case
       red because a timestamp is old.
    2. A CONTRACTUAL CONCLUSION. `PAST_DUE` is arithmetic on two timestamps.
       Whether a missed deadline is a contract failure depends on what was
       agreed and communicated, which a timestamp does not know.
    3. AN EMPLOYEE SCORE. Workload counts are counts. They are not throughput,
       not quality, not a ranking, and the payload says so — a per-analyst
       number that acquires a league table stops being workload management.

TWO CLOCKS, NAMED
─────────────────
    "Age" is ambiguous across a workflow, so this module never reports one
    number. `age_days` is measured from case creation, `held_days` from the
    current assignment, and `idle_days` from the last thing that actually
    happened. A queue of unassigned work is old in the first sense; a case
    stuck with an analyst is old in the second; a case nobody has touched is
    old in the third. Collapsing them would hide exactly the case that needs
    attention.

PERFORMANCE
───────────
    `case_assignment.case_state` costs one query per case, which is right for
    one case and wrong for a page of them. Every list here resolves state,
    provenance, deadlines and source limitations in a FIXED number of queries
    for the whole page, using the same pure functions from `qa_gate` so the
    derived state cannot drift from the canonical one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import Integer, String, func, or_, select
from sqlalchemy import cast as sa_cast

from app.tefca_registry import models as reg

SERVICE_VERSION = "1.0.0"

# ── work provenance ──────────────────────────────────────────────────────────
#: WHY a case exists. A case may carry several of these at once and they are
#: never collapsed into one: "exception" would erase the difference between a
#: Government request and a formatting finding.
HUMAN_REQUIRED = "HUMAN_REQUIRED"
STATISTICAL_SAMPLE = "STATISTICAL_SAMPLE"
PRIORITY_REQUEST = "PRIORITY_REQUEST"
QA_RETURN = "QA_RETURN"
QA_ESCALATION = "QA_ESCALATION"

#: `verification_results.queue_source` values the ARC engines stamp.
QUEUE_DQ = "RCE_DQ_HUMAN_REQUIRED"
QUEUE_PRIORITY = "TEFCA_ARC_PRIORITY"
QUEUE_SAMPLE = "TEFCA_ARC_PER_QHIN"

# ── deadline vocabulary ──────────────────────────────────────────────────────
#: Identical to `priority_review`, on purpose: two modules disagreeing about
#: what PAST_DUE means would be worse than either being wrong alone.
NO_DEADLINE = "NO_DEADLINE"
ON_TRACK = "ON_TRACK"
DUE_SOON = "DUE_SOON"
PAST_DUE = "PAST_DUE"

# ── operational attention — INTERNAL, never contractual ──────────────────────
NORMAL = "NORMAL"
ATTENTION = "ATTENTION"
BLOCKED = "BLOCKED"

#: Sampling empty state. NOT "0% complete": a plan that was never drawn is not
#: a plan running late, and a progress bar at zero says the opposite.
NOT_YET_CREATED = "NOT_YET_CREATED"

SORTS = ("age", "held", "idle", "deadline", "created", "review_id")


class SupervisorRefused(RuntimeError):
    """A supervisor read was refused, and the reason is stated."""


# ── batched derivation ───────────────────────────────────────────────────────

async def _events_for(db, review_ids: Sequence[str]
                      ) -> Dict[str, List[reg.ReviewDecisionEvent]]:
    """Every decision event for a whole page, in ONE query.

    `qa_gate._events` is per review and correct for one case. A supervisor
    queue of 50 would issue 50 queries for state alone, then 50 more for
    reportability. Same rows, one round trip, and the grouping is handed to the
    SAME pure functions `qa_gate` uses so the answer cannot diverge.
    """
    if not review_ids:
        return {}
    rows = (await db.execute(
        select(reg.ReviewDecisionEvent)
        .where(reg.ReviewDecisionEvent.review_id.in_(list(review_ids)))
        .order_by(reg.ReviewDecisionEvent.review_id,
                  reg.ReviewDecisionEvent.sequence_number))).scalars().all()
    grouped: Dict[str, List[reg.ReviewDecisionEvent]] = {r: [] for r in review_ids}
    for row in rows:
        grouped.setdefault(row.review_id, []).append(row)
    return grouped


def _state_of(record: reg.ReviewRecord, events: List[reg.ReviewDecisionEvent]) -> str:
    """`case_assignment.case_state`, computed from events already in hand.

    The ladder is deliberately identical, and a test asserts the two agree on
    every state — a supervisor list that disagreed with the case itself would
    be worse than no list.
    """
    from app.tefca_registry.case_assignment import (APPROVED, AVAILABLE,
                                                    CLAIMED, ESCALATED,
                                                    RETURNED, SUBMITTED_FOR_QA)
    from app.tefca_registry.qa_gate import _latest_determination, _qa_after

    if record.reportable_at is not None:
        return APPROVED
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


async def _priority_context(db, case_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Deadline and COR reference for a page of priority cases, in one query."""
    if not case_ids:
        return {}
    from app.Tefca.models import TEFCAPriorityCase

    ids = []
    for value in case_ids:
        try:
            ids.append(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            continue
    if not ids:
        return {}
    rows = (await db.execute(
        select(TEFCAPriorityCase)
        .where(TEFCAPriorityCase.case_id.in_(ids)))).scalars().all()
    return {str(r.case_id): {"cor_reference": r.cor_reference,
                             "requested_by": r.assigned_by,
                             "received_at": r.assigned_date,
                             "deadline": r.deadline_date}
            for r in rows}


async def _sampled_entities(db, entity_ids: Sequence[Any]) -> Dict[str, List[str]]:
    """Which of these entities are frozen into an official sample, in one query."""
    ids = [e for e in entity_ids if e is not None]
    if not ids:
        return {}
    rows = (await db.execute(
        select(reg.SampleEntity.entity_id, reg.SampleEntity.sample_id)
        .where(reg.SampleEntity.entity_id.in_(ids)))).all()
    out: Dict[str, List[str]] = {}
    for entity_id, sample_id in rows:
        out.setdefault(str(entity_id), []).append(str(sample_id))
    return out


async def _unavailable_sources(db, entity_ids: Sequence[Any]) -> Dict[str, List[str]]:
    """Sources that could not answer, per entity, in one query.

    `unavailable` is carried through unchanged. It is not a pass, not a clear
    and not a no-match, and a supervisor screen is precisely where that
    distinction gets quietly lost.
    """
    ids = [e for e in entity_ids if e is not None]
    if not ids:
        return {}
    rows = (await db.execute(
        select(reg.TefcaVerification.entity_id, reg.TefcaVerification.source)
        .where(reg.TefcaVerification.entity_id.in_(ids),
               reg.TefcaVerification.verification_status == "unavailable"))).all()
    out: Dict[str, List[str]] = {}
    for entity_id, source in rows:
        bucket = out.setdefault(str(entity_id), [])
        if source not in bucket:
            bucket.append(source)
    return out


async def _entity_names(db, entity_ids: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    ids = [e for e in entity_ids if e is not None]
    if not ids:
        return {}
    rows = (await db.execute(
        select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.name,
               reg.TefcaRegEntity.entity_level)
        .where(reg.TefcaRegEntity.id.in_(ids)))).all()
    return {str(i): {"name": n, "entity_level": level} for i, n, level in rows}


async def _qhins_for(db, entity_ids: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    """The canonical managing QHIN per entity, in one query. Ambiguity reported."""
    ids = [e for e in entity_ids if e is not None]
    if not ids:
        return {}
    rows = (await db.execute(
        select(reg.TefcaEntityRelationship.child_entity_id,
               reg.TefcaEntityRelationship.parent_entity_id,
               reg.TefcaRegEntity.name)
        .join(reg.TefcaRegEntity,
              reg.TefcaRegEntity.id == reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id.in_(ids),
               reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
               reg.TefcaEntityRelationship.status == "active"))).all()
    grouped: Dict[str, List[Tuple[Any, str]]] = {}
    for child, parent, name in rows:
        grouped.setdefault(str(child), []).append((parent, name))
    return {child: ({"qhin_entity_id": str(edges[0][0]), "qhin_name": edges[0][1]}
                    if len(edges) == 1 else
                    {"qhin_entity_id": None, "qhin_name": None,
                     "qhin_ambiguous": True})
            for child, edges in grouped.items()}


# ── deadline and age ─────────────────────────────────────────────────────────

def deadline_status(deadline: Optional[datetime], *, now: datetime,
                    due_soon_within_hours: Optional[float] = None) -> Dict[str, Any]:
    """Where a case stands against a deadline the GOVERNMENT set.

    Delegates to `priority_review` rather than restating the rule, so the
    supervisor screen and the Task 5 report can never disagree about what
    PAST_DUE means or about the fact that it concludes nothing.
    """
    from app.tefca_registry.priority_review import deadline_status as _status

    block = _status(deadline, now=now, due_soon_within_hours=due_soon_within_hours)
    return {"deadline": block["deadline"], "deadline_status": block["status"],
            "hours_remaining": block["hours_remaining"],
            "compliance_conclusion": block["compliance_conclusion"]}


def _days(since: Optional[datetime], now: datetime) -> Optional[float]:
    if since is None:
        return None
    return round((now - since).total_seconds() / 86400.0, 3)


def _last_activity(record: reg.ReviewRecord,
                   events: List[reg.ReviewDecisionEvent]) -> Optional[datetime]:
    stamps = [record.created_at, record.assigned_at, record.reportable_at]
    stamps += [e.occurred_at for e in events]
    real = [s for s in stamps if s is not None]
    return max(real) if real else None


# ── the work item ────────────────────────────────────────────────────────────

def _provenance(payload: Dict[str, Any], state: str,
                sample_ids: List[str]) -> List[str]:
    """Every reason this case exists. Order is stable; nothing is dropped."""
    from app.tefca_registry.case_assignment import ESCALATED, RETURNED

    reasons: List[str] = []
    queue_source = payload.get("queue_source")
    selection = payload.get("selection_reason")

    if queue_source == QUEUE_DQ:
        reasons.append(HUMAN_REQUIRED)
    if queue_source == QUEUE_PRIORITY or selection == PRIORITY_REQUEST:
        reasons.append(PRIORITY_REQUEST)
    if selection == STATISTICAL_SAMPLE or sample_ids:
        reasons.append(STATISTICAL_SAMPLE)
    # A returned or escalated case is still the case it was; the QA act is an
    # ADDITIONAL reason it is on someone's desk, never a replacement for the
    # reason it was opened.
    if state == RETURNED:
        reasons.append(QA_RETURN)
    if state == ESCALATED:
        reasons.append(QA_ESCALATION)
    return reasons


def _limitations(payload: Dict[str, Any], unavailable: List[str]) -> List[Dict[str, str]]:
    """Why a case may be unable to progress. Facts, never diagnoses."""
    out: List[Dict[str, str]] = []
    resolution = payload.get("target_resolution")
    if resolution in ("AMBIGUOUS", "NOT_FOUND", "INSUFFICIENT_INFORMATION"):
        out.append({"kind": "ENTITY_RESOLUTION", "detail": resolution,
                    "meaning": ("The organisation named cannot be tied to one "
                                "canonical entity. A human must resolve it.")})
    for source in unavailable:
        out.append({"kind": "SOURCE_UNAVAILABLE", "detail": source,
                    "meaning": ("The source could not answer. This is not "
                                "evidence for or against the entity.")})
    if payload.get("government_verification_pending"):
        out.append({"kind": "GOVERNMENT_VERIFICATION_PENDING",
                    "detail": str(payload.get("government_verification_pending")),
                    "meaning": ("AGT has no authorized mechanism to verify this "
                                "identifier.")})
    return out


def _attention(limitations: List[Dict[str, str]], deadline_state: str,
               idle_days: Optional[float],
               stale_after_days: Optional[float]) -> str:
    """An INTERNAL operational indicator. Never a compliance statement.

    `stale_after_days` has NO default. No approved threshold exists for how
    long a review case may sit, so a hard-coded one would be an AGT service
    level invented on a dashboard and then reported against. A caller that
    wants a staleness band says how wide it is.
    """
    if limitations:
        return BLOCKED
    if deadline_state == PAST_DUE:
        return ATTENTION
    if stale_after_days is not None and idle_days is not None \
            and idle_days >= stale_after_days:
        return ATTENTION
    return NORMAL


async def _work_items(db, records: List[reg.ReviewRecord], *, now: datetime,
                      due_soon_within_hours: Optional[float],
                      stale_after_days: Optional[float]) -> List[Dict[str, Any]]:
    """One page of records, fully derived, in a fixed number of queries."""
    review_ids = [r.review_id for r in records]
    entity_ids = [r.entity_id for r in records if r.entity_id is not None]
    payloads = {r.review_id: (r.verification_results or {}) for r in records}
    case_ids = [p.get("priority_case_id") for p in payloads.values()
                if p.get("priority_case_id")]

    events = await _events_for(db, review_ids)
    priority = await _priority_context(db, case_ids)
    sampled = await _sampled_entities(db, entity_ids)
    unavailable = await _unavailable_sources(db, entity_ids)
    names = await _entity_names(db, entity_ids)
    qhins = await _qhins_for(db, entity_ids)

    items = []
    for record in records:
        payload = payloads[record.review_id]
        case_events = events.get(record.review_id, [])
        state = _state_of(record, case_events)
        entity_key = str(record.entity_id) if record.entity_id else None
        sample_ids = sampled.get(entity_key, []) if entity_key else []
        limits = _limitations(payload, unavailable.get(entity_key, [])
                              if entity_key else [])

        case = priority.get(str(payload.get("priority_case_id")), {})
        block = deadline_status(case.get("deadline"), now=now,
                                due_soon_within_hours=due_soon_within_hours)
        activity = _last_activity(record, case_events)
        idle_days = _days(activity, now)

        entity = names.get(entity_key, {}) if entity_key else {}
        qhin = qhins.get(entity_key, {}) if entity_key else {}

        items.append({
            "review_id": record.review_id,
            "entity_id": entity_key,
            "entity_name": entity.get("name"),
            "entity_level": entity.get("entity_level"),
            "qhin_entity_id": qhin.get("qhin_entity_id"),
            "qhin_name": qhin.get("qhin_name"),
            "qhin_ambiguous": qhin.get("qhin_ambiguous", False),
            "source_record_id": (str(record.source_record_id)
                                 if record.source_record_id else None),
            "queue_source": payload.get("queue_source"),
            "work_reasons": _provenance(payload, state, sample_ids),
            "sample_ids": sample_ids,
            "priority_case_id": payload.get("priority_case_id"),
            "cor_reference": case.get("cor_reference"),
            "state": state,
            "assigned_to_user_id": (str(record.assigned_to_user_id)
                                    if record.assigned_to_user_id else None),
            "assigned_at": record.assigned_at,
            "created_at": record.created_at,
            "last_activity_at": activity,
            # Three named clocks. See the module docstring: one number would
            # hide whichever case is actually stuck.
            "age_days": _days(record.created_at, now),
            "held_days": _days(record.assigned_at, now),
            "idle_days": idle_days,
            "decision_events": len(case_events),
            "reportable": record.reportable_at is not None,
            "reportable_at": record.reportable_at,
            "limitations": limits,
            "attention": _attention(limits, block["deadline_status"], idle_days,
                                    stale_after_days),
            **block,
        })
    return items


# ── the queue ────────────────────────────────────────────────────────────────

_SORT_COLUMNS = {
    "age": reg.ReviewRecord.created_at.asc(),
    "created": reg.ReviewRecord.created_at.desc(),
    "held": reg.ReviewRecord.assigned_at.asc().nullslast(),
    "review_id": reg.ReviewRecord.review_id.asc(),
}


def ordered(stmt, sort: str):
    """Apply a sort, ALWAYS tie-broken by the one unique column.

    Public so a test can inspect it. Without the tie-break, two cases created
    in the same millisecond have no defined order between them: Postgres may
    return them either way round on either page, so a supervisor paging through
    a queue sees one case twice and another not at all — and nothing tells them
    it happened. The tie-break is what makes the ordering total.
    """
    column = _SORT_COLUMNS.get(sort)
    if column is None:
        return stmt.order_by(reg.ReviewRecord.review_id.asc())
    return stmt.order_by(column, reg.ReviewRecord.review_id.asc())


async def work_queue(db, *, queue_source: Optional[str] = None,
                     work_reason: Optional[str] = None,
                     state: Optional[str] = None,
                     assignee: Optional[uuid.UUID] = None,
                     unassigned_only: bool = False,
                     qhin_entity_id: Optional[uuid.UUID] = None,
                     limited_only: bool = False,
                     reportable: Optional[bool] = None,
                     deadline_state: Optional[str] = None,
                     search: Optional[str] = None,
                     sort: str = "age", offset: int = 0, limit: int = 50,
                     now: Optional[datetime] = None,
                     due_soon_within_hours: Optional[float] = None,
                     stale_after_days: Optional[float] = None) -> Dict[str, Any]:
    """One paginated operational queue over every ARC work source.

    Filters that the DATABASE can answer are applied in SQL; the ones that are
    derived (state, provenance, limitation, deadline band) are applied after
    derivation, because they are not columns and inventing columns for them is
    exactly the second source of truth this module exists to avoid.

    ORDERING IS ALWAYS TIE-BROKEN BY `review_id`. Without it, two cases created
    in the same millisecond can swap places between page 1 and page 2, so a
    supervisor paging through a queue would see one case twice and another not
    at all — and would never know.
    """
    if sort not in SORTS:
        raise SupervisorRefused(f"sort must be one of {SORTS}")
    if limit < 1 or limit > 200:
        raise SupervisorRefused("limit must be between 1 and 200")
    now = now or datetime.utcnow()

    stmt = select(reg.ReviewRecord)
    if queue_source:
        stmt = stmt.where(
            reg.ReviewRecord.verification_results["queue_source"].astext
            == queue_source)
    if unassigned_only:
        stmt = stmt.where(reg.ReviewRecord.assigned_to_user_id.is_(None))
    if assignee is not None:
        stmt = stmt.where(reg.ReviewRecord.assigned_to_user_id == assignee)
    if reportable is True:
        stmt = stmt.where(reg.ReviewRecord.reportable_at.isnot(None))
    if reportable is False:
        stmt = stmt.where(reg.ReviewRecord.reportable_at.is_(None))
    if search:
        # Case reference or entity name. Deliberately anchored rather than
        # `%term%` on both sides: a leading wildcard cannot use an index, and a
        # supervisor search box is not a reason to table-scan 23,566 rows.
        term = search.strip()
        stmt = stmt.where(or_(
            reg.ReviewRecord.review_id.ilike(f"{term}%"),
            reg.ReviewRecord.verification_results["cor_reference"].astext.ilike(f"{term}%"),
            reg.ReviewRecord.entity_id.in_(
                select(reg.TefcaRegEntity.id)
                .where(reg.TefcaRegEntity.name.ilike(f"{term}%")))))
    if qhin_entity_id is not None:
        stmt = stmt.where(reg.ReviewRecord.entity_id.in_(
            select(reg.TefcaEntityRelationship.child_entity_id)
            .where(reg.TefcaEntityRelationship.parent_entity_id == qhin_entity_id,
                   reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
                   reg.TefcaEntityRelationship.status == "active")))

    derived_filter = any(x is not None for x in (state, work_reason, deadline_state)) \
        or limited_only

    stmt = ordered(stmt, sort)

    if not derived_filter:
        total = int((await db.execute(
            select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
        records = (await db.execute(
            stmt.offset(offset).limit(limit))).scalars().all()
        items = await _work_items(db, list(records), now=now,
                                  due_soon_within_hours=due_soon_within_hours,
                                  stale_after_days=stale_after_days)
        if sort in ("idle", "deadline"):
            items = _sort_derived(items, sort)
        return _page(items, total, offset, limit, sort, now,
                     due_soon_within_hours, stale_after_days)

    # A derived filter cannot be pushed into SQL, so the candidate set is
    # bounded first and the count reported is the count AFTER filtering. The
    # bound is stated in the payload rather than hidden: a truncated queue that
    # looks complete is worse than one that says it was truncated.
    ceiling = 2000
    records = (await db.execute(stmt.limit(ceiling))).scalars().all()
    items = await _work_items(db, list(records), now=now,
                              due_soon_within_hours=due_soon_within_hours,
                              stale_after_days=stale_after_days)
    if state:
        items = [i for i in items if i["state"] == state]
    if work_reason:
        items = [i for i in items if work_reason in i["work_reasons"]]
    if deadline_state:
        items = [i for i in items if i["deadline_status"] == deadline_state]
    if limited_only:
        items = [i for i in items if i["limitations"]]
    if sort in ("idle", "deadline"):
        items = _sort_derived(items, sort)
    total = len(items)
    page = items[offset:offset + limit]
    result = _page(page, total, offset, limit, sort, now,
                   due_soon_within_hours, stale_after_days)
    result["candidate_ceiling"] = ceiling
    result["truncated"] = len(records) >= ceiling
    return result


def _sort_derived(items: List[Dict[str, Any]], sort: str) -> List[Dict[str, Any]]:
    """Sort on a derived value, always tie-broken by review_id.

    `None` sorts LAST in both cases and does so explicitly: a case with no
    deadline is not the most urgent case, and a Python comparison against None
    would raise rather than decide.
    """
    if sort == "idle":
        return sorted(items, key=lambda i: (i["idle_days"] is None,
                                            -(i["idle_days"] or 0),
                                            i["review_id"]))
    return sorted(items, key=lambda i: (i["deadline"] is None,
                                        i["deadline"] or "",
                                        i["review_id"]))


def _page(items, total, offset, limit, sort, now, due_soon, stale) -> Dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(items),
        "has_more": offset + len(items) < total,
        "sort": sort,
        "generated_at": now,
        "due_soon_within_hours": due_soon,
        "stale_after_days": stale,
        "note": ("Counts are derived from review_records, review_decision_events, "
                 "sample_entities and the priority requests. Nothing here is "
                 "stored, cached or scored."),
    }


# ── dashboard ────────────────────────────────────────────────────────────────

def _scoped(stmt, queue_source: Optional[str]):
    """Narrow a case query to one work source, or leave it estate-wide.

    The same scope the queue takes, for the same reason: a supervisor who runs
    one part of the estate needs the summary to agree with the list they are
    looking at, and a summary that silently counted everything would not.
    """
    if queue_source is None:
        return stmt
    return stmt.where(
        reg.ReviewRecord.verification_results["queue_source"].astext
        == queue_source)


async def dashboard(db, *, queue_source: Optional[str] = None,
                    now: Optional[datetime] = None,
                    due_soon_within_hours: Optional[float] = None,
                    stale_after_days: Optional[float] = None) -> Dict[str, Any]:
    """The supervisor summary. Every card is a count of something real."""
    from app.tefca_registry.case_assignment import (APPROVED, AVAILABLE,
                                                    CLAIMED, ESCALATED,
                                                    RETURNED, SUBMITTED_FOR_QA)

    now = now or datetime.utcnow()
    records = (await db.execute(
        _scoped(select(reg.ReviewRecord), queue_source))).scalars().all()
    items = await _work_items(db, list(records), now=now,
                              due_soon_within_hours=due_soon_within_hours,
                              stale_after_days=stale_after_days)

    by_state = {s: 0 for s in (AVAILABLE, CLAIMED, SUBMITTED_FOR_QA, RETURNED,
                               ESCALATED, APPROVED)}
    by_reason: Dict[str, int] = {}
    by_queue: Dict[str, int] = {}
    by_deadline: Dict[str, int] = {}
    by_attention: Dict[str, int] = {}
    limitations: Dict[str, int] = {}
    for item in items:
        by_state[item["state"]] = by_state.get(item["state"], 0) + 1
        by_queue[item["queue_source"] or "(none)"] = \
            by_queue.get(item["queue_source"] or "(none)", 0) + 1
        by_deadline[item["deadline_status"]] = \
            by_deadline.get(item["deadline_status"], 0) + 1
        by_attention[item["attention"]] = by_attention.get(item["attention"], 0) + 1
        for reason in item["work_reasons"]:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        for limit in item["limitations"]:
            limitations[limit["kind"]] = limitations.get(limit["kind"], 0) + 1

    return {
        "generated_at": now,
        "service_version": SERVICE_VERSION,
        "queue_source": queue_source,
        "total_cases": len(items),
        "unassigned": by_state[AVAILABLE],
        "in_progress": by_state[CLAIMED],
        "awaiting_qa": by_state[SUBMITTED_FOR_QA],
        "returned": by_state[RETURNED],
        "escalated": by_state[ESCALATED],
        "reportable": sum(1 for i in items if i["reportable"]),
        "by_state": by_state,
        "by_work_reason": by_reason,
        "by_queue_source": by_queue,
        "by_deadline_status": by_deadline,
        "by_attention": by_attention,
        "source_limitations": limitations,
        "sampling": await sampling_overview(db),
        "priority": await priority_overview(db, now=now,
                                            due_soon_within_hours=due_soon_within_hours),
        "notes": [
            "Counts are operational workload, not performance measures.",
            "A deadline exists only where the COR supplied one. PAST_DUE is "
            "arithmetic and asserts no contractual conclusion.",
        ],
    }


# ── workload by person ───────────────────────────────────────────────────────

async def analyst_workload(db, *, queue_source: Optional[str] = None,
                           now: Optional[datetime] = None,
                           stale_after_days: Optional[float] = None
                           ) -> Dict[str, Any]:
    """Workload per holder. NOT a performance measure, and it says so.

    Counts of open work, by state. There is deliberately no throughput figure,
    no average handling time and no ranking: a per-person number on a
    management screen acquires a league table the moment one exists, and
    nothing here can distinguish a slow case from a hard one.
    """
    from app.tefca_registry.case_assignment import APPROVED

    now = now or datetime.utcnow()
    records = (await db.execute(_scoped(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.assigned_to_user_id.isnot(None)),
        queue_source))).scalars().all()
    items = await _work_items(db, list(records), now=now,
                              due_soon_within_hours=None,
                              stale_after_days=stale_after_days)

    per: Dict[str, Dict[str, Any]] = {}
    for item in items:
        holder = item["assigned_to_user_id"]
        bucket = per.setdefault(holder, {"assigned_to_user_id": holder,
                                         "open_cases": 0, "by_state": {},
                                         "oldest_held_days": None,
                                         "attention_cases": 0})
        bucket["open_cases"] += 1
        bucket["by_state"][item["state"]] = bucket["by_state"].get(item["state"], 0) + 1
        if item["held_days"] is not None:
            current = bucket["oldest_held_days"]
            bucket["oldest_held_days"] = max(current or 0, item["held_days"])
        if item["attention"] != NORMAL:
            bucket["attention_cases"] += 1

    unassigned = int((await db.execute(_scoped(
        select(func.count()).select_from(reg.ReviewRecord)
        .where(reg.ReviewRecord.assigned_to_user_id.is_(None),
               reg.ReviewRecord.reportable_at.is_(None)),
        queue_source))).scalar() or 0)

    return {
        "generated_at": now,
        "analysts": sorted(per.values(), key=lambda b: -b["open_cases"]),
        "unassigned_cases": unassigned,
        "note": ("Open workload only. These are counts of work in hand, not a "
                 "performance measure, a productivity score or a ranking."),
    }


async def qa_workload(db, *, queue_source: Optional[str] = None,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """The independent QA view. Kept separate from analyst workload on purpose.

    QA is not the analyst's queue with a different filter. Merging them would
    let a screen suggest that whoever holds a case can also clear it, which is
    the opposite of what the segregation rule exists to prevent — so this
    reports the ANALYST whose determination is waiting, precisely so a QA lead
    can see who they may not be.
    """
    from app.tefca_registry.case_assignment import (ESCALATED, RETURNED,
                                                    SUBMITTED_FOR_QA)
    from app.tefca_registry.qa_gate import _latest_determination

    now = now or datetime.utcnow()
    records = (await db.execute(
        _scoped(select(reg.ReviewRecord), queue_source))).scalars().all()
    events = await _events_for(db, [r.review_id for r in records])

    awaiting, returned, escalated, approved = [], [], [], []
    for record in records:
        case_events = events.get(record.review_id, [])
        state = _state_of(record, case_events)
        determination = _latest_determination(case_events)
        row = {
            "review_id": record.review_id,
            "state": state,
            "determined_by": determination.actor_email if determination else None,
            "determined_by_user_id": (str(determination.actor_user_id)
                                      if determination else None),
            "determined_at": determination.occurred_at if determination else None,
            "waiting_days": _days(determination.occurred_at, now) if determination else None,
            "reportable": record.reportable_at is not None,
        }
        if state == SUBMITTED_FOR_QA:
            awaiting.append(row)
        elif state == RETURNED:
            returned.append(row)
        elif state == ESCALATED:
            escalated.append(row)
        elif record.reportable_at is not None:
            approved.append(row)

    awaiting.sort(key=lambda r: (r["determined_at"] is None, r["determined_at"],
                                 r["review_id"]))
    return {
        "generated_at": now,
        "awaiting_qa": awaiting,
        "returned": returned,
        "escalated": escalated,
        "approved": approved,
        "counts": {"awaiting_qa": len(awaiting), "returned": len(returned),
                   "escalated": len(escalated), "approved": len(approved)},
        "segregation_note": ("`determined_by` is shown so a QA reviewer can see "
                             "whose determination this is. A reviewer may never "
                             "QA their own, and the gate refuses it "
                             "independently of this screen."),
    }


# ── sampling and priority overviews ──────────────────────────────────────────

async def sampling_overview(db) -> Dict[str, Any]:
    """Sampling progress — or an honest statement that no plan exists.

    A plan that was never drawn reports NOT_YET_CREATED. It must never render
    as "0% complete": a progress bar at zero says the work is behind, and no
    work can be behind before anyone has decided it should start.
    """
    from app.tefca_registry import qhin_sampling as qs

    samples = (await db.execute(
        select(reg.ReviewSample).order_by(reg.ReviewSample.drawn_at.desc())
    )).scalars().all()
    if not samples:
        return {
            "status": NOT_YET_CREATED,
            "official_plans": 0,
            "note": ("No official sampling plan has been created. This is a "
                     "zero state, not incomplete work, and no percentage is "
                     "reported for it."),
        }

    plans = []
    for sample in samples:
        completion = await qs.plan_completion(db, sample.id)
        config = sample.strata_config or {}
        plans.append({
            "sample_id": str(sample.id),
            "sample_name": sample.sample_name,
            "review_type": sample.review_type,
            "plan_source": config.get("plan_source"),
            "stratify_by": config.get("stratify_by"),
            "population_size": sample.population_size,
            "sample_size": sample.sample_size,
            "drawn_at": sample.drawn_at,
            "counts": completion["counts"],
            "complete": completion["complete"],
        })
    return {"status": "PLANS_EXIST", "official_plans": len(plans), "plans": plans}


async def priority_overview(db, *, now: Optional[datetime] = None,
                            due_soon_within_hours: Optional[float] = None
                            ) -> Dict[str, Any]:
    """Task 5 workload, measured against the deadlines the COR actually set."""
    from app.tefca_registry import priority_review as pr

    now = now or datetime.utcnow()
    requests = await pr.open_requests(db, limit=500, include_withdrawn=True)
    by_state: Dict[str, int] = {}
    by_deadline: Dict[str, int] = {}
    for item in requests:
        by_state[item["state"]] = by_state.get(item["state"], 0) + 1
        deadline = (datetime.fromisoformat(item["deadline"])
                    if item["deadline"] else None)
        status = deadline_status(deadline, now=now,
                                 due_soon_within_hours=due_soon_within_hours)
        by_deadline[status["deadline_status"]] = \
            by_deadline.get(status["deadline_status"], 0) + 1

    return {
        "active_requests": len(requests),
        "by_state": by_state,
        "by_deadline_status": by_deadline,
        "reportable": sum(1 for r in requests if r["reportable"]),
        "note": ("Deadlines are the COR's own, per request. No standing "
                 "turnaround is applied and no compliance conclusion is drawn."),
    }


# ── one case ─────────────────────────────────────────────────────────────────

async def case_detail(db, review_id: str, *, now: Optional[datetime] = None,
                      due_soon_within_hours: Optional[float] = None,
                      stale_after_days: Optional[float] = None) -> Dict[str, Any]:
    """Everything a supervisor needs to MANAGE one case — and nothing more.

    No evidence values, no delivered Government field content, no connector
    internals. Managing work needs to know that evidence exists and whether a
    source could answer; reading the evidence is the analyst's job and happens
    on the analyst surface, under the analyst's own authority.
    """
    now = now or datetime.utcnow()
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    if record is None:
        raise SupervisorRefused(f"no review exists with id {review_id}")

    items = await _work_items(db, [record], now=now,
                              due_soon_within_hours=due_soon_within_hours,
                              stale_after_days=stale_after_days)
    return {**items[0], "timeline": await audit_timeline(db, review_id)}


async def audit_timeline(db, review_id: str) -> List[Dict[str, Any]]:
    """What actually happened to this case, oldest first.

    Assembled from the decision events and the audit rows that already exist.
    Nothing is inferred and nothing is filled in: a case with no assignment
    audit row simply has no assignment entry, which is the truth about it.
    """
    from app.tefca_registry.qa_gate import _events, history

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    if record is None:
        raise SupervisorRefused(f"no review exists with id {review_id}")

    entries: List[Dict[str, Any]] = []
    if record.created_at:
        payload = record.verification_results or {}
        entries.append({"at": record.created_at, "event": "case_created",
                        "actor": None,
                        "detail": {"queue_source": payload.get("queue_source"),
                                   "selection_reason": payload.get("selection_reason"),
                                   "cor_reference": payload.get("cor_reference")}})

    audits = (await db.execute(select(reg.TefcaRegAuditLog))).scalars().all()
    for row in audits:
        meta = row.metadata_ or {}
        if meta.get("review_id") != review_id:
            continue
        entries.append({"at": row.created_at, "event": row.action,
                        "actor": row.actor_email,
                        "detail": {k: v for k, v in meta.items()
                                   if k not in ("review_id",)}})

    for event in history(await _events(db, review_id)):
        entries.append({
            "at": event["occurred_at"],
            "event": (f"qa_{event['qa_action'].lower()}" if event["qa_action"]
                      else event["event_type"].lower()),
            "actor": event["actor_email"],
            "detail": {"determination": event["determination"],
                       "determined_bucket": event["determined_bucket"],
                       "rationale": event["rationale"],
                       "is_superseded": event["is_superseded"]}})

    if record.reportable_at:
        entries.append({"at": record.reportable_at, "event": "became_reportable",
                        "actor": None, "detail": {}})

    entries.sort(key=lambda e: (e["at"] is None, e["at"] or datetime.min))
    return entries


# ── read-only Government forecast ────────────────────────────────────────────

async def government_readiness(db) -> Dict[str, Any]:
    """Aggregate operational readiness. READ ONLY; creates nothing.

    The DQ ledger and the operational queue are reported SEPARATELY and on
    purpose. 138 HUMAN_REQUIRED findings are not 138 analyst cases: a case
    exists only where the bridge has made one, and quietly presenting the
    finding count as a workload would imply work that nobody has been asked to
    do.
    """
    from app.tefca_registry.rce import models as m

    async def count(stmt) -> int:
        return int((await db.execute(stmt)).scalar() or 0)

    human_required = await count(
        select(func.count()).select_from(m.RceIssue)
        .where(m.RceIssue.correction_authority == "HUMAN_REQUIRED"))
    operational = await count(
        select(func.count()).select_from(reg.ReviewRecord)
        .where(reg.ReviewRecord.verification_results["queue_source"].astext
               == QUEUE_DQ))

    return {
        "dq_human_required_findings": human_required,
        "operational_dq_review_cases": operational,
        "unoperationalized_findings": human_required - operational,
        "review_records_total": await count(
            select(func.count()).select_from(reg.ReviewRecord)),
        "assigned": await count(
            select(func.count()).select_from(reg.ReviewRecord)
            .where(reg.ReviewRecord.assigned_to_user_id.isnot(None))),
        "reportable": await count(
            select(func.count()).select_from(reg.ReviewRecord)
            .where(reg.ReviewRecord.reportable_at.isnot(None))),
        "decision_events": await count(
            select(func.count()).select_from(reg.ReviewDecisionEvent)),
        "official_samples": await count(
            select(func.count()).select_from(reg.ReviewSample)),
        "sample_membership": await count(
            select(func.count()).select_from(reg.SampleEntity)),
        "priority_review_cases": await count(
            select(func.count()).select_from(reg.ReviewRecord)
            .where(reg.ReviewRecord.verification_results["queue_source"].astext
                   == QUEUE_PRIORITY)),
        "qhin_attributed_entities": await count(
            select(func.count(func.distinct(
                reg.TefcaEntityRelationship.child_entity_id)))
            .where(reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
                   reg.TefcaEntityRelationship.status == "active")),
        "note": ("A HUMAN_REQUIRED finding is not an analyst case. Cases exist "
                 "only where the DQ bridge created them; creating the "
                 "difference is a separate authorized act, not a reconciliation "
                 "this view may perform."),
    }
