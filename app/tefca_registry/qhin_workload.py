"""Organise a delivery's review population the way the work is actually done.

THE PROBLEM THIS SOLVES
───────────────────────
A Program Manager cannot manage 25,000 unrelated rows. The work has a natural
shape and it is already in the data:

    QHIN → Participant → Subparticipant

ONC describes Participants as organisations that connect to a QHIN, and
Subparticipants as organisations that connect through a connected organisation.
This module reports the relationship AS DELIVERED and never invents a missing
one — a record whose QHIN cannot be resolved is returned as UNRESOLVED with the
reason, not placed somewhere plausible.

WHERE THE RELATIONSHIP COMES FROM
─────────────────────────────────
The canonical edges written at promotion: `managed_by_qhin` for the QHIN, and
`sub_participant_of` for the parent. Never a column that happens to look right,
never the entity's name, never its OID prefix. That is the same rule
`qhin_sampling.resolve_qhin_strata` follows, and this module calls that function
rather than restating it — one resolver, one answer.

WHY THE CASE STATES ARE COMPUTED IN ONE PASS
────────────────────────────────────────────
`case_assignment.case_state` answers for ONE case and issues its own query for
that case's events. Correct, and completely unusable here: a rollup over a 25K
delivery would issue 25K queries. So this loads every review record and every
decision event for the population in TWO queries and applies the SAME rules
`case_state` applies — `_latest_determination` and `_qa_after` are imported from
`qa_gate` rather than reimplemented, so the two can never drift apart on the
question of what supersedes what.

NOTHING HERE MAKES A DECISION
─────────────────────────────
Counting, grouping and workload distribution only. Which analyst holds a case is
an operational matter; what the case CONCLUDES is a D1-D9 determination and a QA
approval, and neither is touched.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m

logger = logging.getLogger(__name__)

UNRESOLVED_QHIN = "UNRESOLVED"

#: The case states, in the order a Program Manager reads them. Same vocabulary
#: as `case_assignment`; imported rather than redefined.
from app.tefca_registry.case_assignment import (  # noqa: E402
    APPROVED, AVAILABLE, CLAIMED, ESCALATED, RETURNED, SUBMITTED_FOR_QA)

STATE_ORDER = (AVAILABLE, CLAIMED, SUBMITTED_FOR_QA, RETURNED, ESCALATED,
               APPROVED)


class WorkloadRefused(RuntimeError):
    """The rollup could not be produced, and the reason is stated."""


# ── set-based case state ─────────────────────────────────────────────────────

async def case_states(db, review_ids: List[str]) -> Dict[str, str]:
    """The workflow state of many cases, in two queries rather than 2N.

    Applies exactly the rules `case_assignment.case_state` applies:

      * `reportable_at` set  → APPROVED (the QA gate has been passed)
      * a determination in force, with QA after it → that QA action
      * a determination in force, with no QA after it → SUBMITTED_FOR_QA
      * no determination → CLAIMED if held, else AVAILABLE

    `_latest_determination` and `_qa_after` come from `qa_gate`, so "which
    determination is in force" is decided in one place for the whole system.
    """
    from app.tefca_registry.qa_gate import _latest_determination, _qa_after

    if not review_ids:
        return {}

    records = (await db.execute(
        select(reg.ReviewRecord.review_id, reg.ReviewRecord.reportable_at,
               reg.ReviewRecord.assigned_to_user_id)
        .where(reg.ReviewRecord.review_id.in_(review_ids)))).all()

    E = reg.ReviewDecisionEvent
    events = (await db.execute(
        select(E).where(E.review_id.in_(review_ids))
        .order_by(E.review_id, E.sequence_number))).scalars().all()
    by_review: Dict[str, List[Any]] = {}
    for event in events:
        by_review.setdefault(event.review_id, []).append(event)

    out: Dict[str, str] = {}
    for review_id, reportable_at, assignee in records:
        if reportable_at is not None:
            out[review_id] = APPROVED
            continue
        own = by_review.get(review_id, [])
        determination = _latest_determination(own)
        if determination is not None:
            after = _qa_after(own, determination)
            if after:
                last = after[-1].qa_action
                out[review_id] = (RETURNED if last == "RETURN"
                                  else ESCALATED if last == "ESCALATE"
                                  else APPROVED)
            else:
                out[review_id] = SUBMITTED_FOR_QA
        else:
            out[review_id] = CLAIMED if assignee is not None else AVAILABLE
    return out


# ── the rollup ───────────────────────────────────────────────────────────────

async def qhin_rollup(db, intake_id, *, sample_id=None,
                      include_held: bool = False) -> Dict[str, Any]:
    """Population and review progress for one delivery, grouped by QHIN.

    `sample_id` narrows the REVIEW columns to one drawn sample — the review
    population — while the POPULATION column stays the whole delivery. Those are
    different numbers and a screen that showed only one of them would be
    answering a different question from the one being asked.
    """
    from app.tefca_registry.qhin_sampling import resolve_qhin_strata

    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise WorkloadRefused(f"No delivery {intake_id}")

    eligible, unresolved = await resolve_qhin_strata(
        db, intake_id, include_held=include_held)

    # entity -> QHIN, from the canonical edge only.
    qhin_of: Dict[Any, str] = {}
    population: Dict[str, int] = {}
    for unit in eligible:
        qhin_of[unit["entity_id"]] = unit["qhin"]
        population[unit["qhin"]] = population.get(unit["qhin"], 0) + 1

    names = await qhin_names(db, list(population.keys()))
    levels = await _entity_levels(db, list(qhin_of.keys()))

    # ── the review population ────────────────────────────────────────────────
    stmt = select(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id.in_(list(qhin_of.keys()))
        if qhin_of else False)
    if sample_id is not None:
        stmt = stmt.where(reg.ReviewRecord.sample_id == sample_id)
    reviews = (await db.execute(stmt)).scalars().all() if qhin_of else []

    states = await case_states(db, [r.review_id for r in reviews])

    per_qhin: Dict[str, Dict[str, Any]] = {}
    for key, count in population.items():
        per_qhin[key] = {
            "qhin_entity_id": key,
            "qhin_name": names.get(key, "(unnamed QHIN)"),
            "population": count,
            "in_review": 0,
            "assigned": 0,
            "completed": 0,
            "qa": 0,
            "by_state": {state: 0 for state in STATE_ORDER},
            "participants": 0,
            "subparticipants": 0,
            "analysts": {},
        }

    for entity_id, level in levels.items():
        key = qhin_of.get(entity_id)
        if key is None or key not in per_qhin:
            continue
        if level == "participant":
            per_qhin[key]["participants"] += 1
        elif level == "sub_participant":
            per_qhin[key]["subparticipants"] += 1

    for record in reviews:
        key = qhin_of.get(record.entity_id)
        if key is None or key not in per_qhin:
            continue
        bucket = per_qhin[key]
        state = states.get(record.review_id, AVAILABLE)
        bucket["in_review"] += 1
        bucket["by_state"][state] = bucket["by_state"].get(state, 0) + 1
        if record.assigned_to_user_id is not None:
            bucket["assigned"] += 1
            holder = str(record.assigned_to_user_id)
            bucket["analysts"][holder] = bucket["analysts"].get(holder, 0) + 1
        # COMPLETED means the analyst is done with it — it has left their hands.
        # It is deliberately NOT "approved": a case sitting in QA is complete
        # from the analyst's side and pending from QA's, and one number cannot
        # be both.
        if state in (SUBMITTED_FOR_QA, APPROVED, ESCALATED):
            bucket["completed"] += 1
        if state in (SUBMITTED_FOR_QA, ESCALATED):
            bucket["qa"] += 1

    for bucket in per_qhin.values():
        bucket["percent_complete"] = (
            round(100.0 * bucket["completed"] / bucket["in_review"], 1)
            if bucket["in_review"] else None)

    totals = _totals(per_qhin)
    return {
        "intake_id": str(intake_id),
        "delivery_label": intake.delivery_label,
        "sample_id": str(sample_id) if sample_id else None,
        "include_held": include_held,
        "qhin_count": len(per_qhin),
        "qhins": [per_qhin[k] for k in sorted(
            per_qhin, key=lambda k: (per_qhin[k]["qhin_name"] or "").lower())],
        "totals": totals,
        "unresolved": {
            "count": len(unresolved),
            "reasons": _reason_counts(unresolved),
            "note": ("These records carry no single canonical managed_by_qhin "
                     "edge, or were never promoted. They are reported here "
                     "rather than assigned to a QHIN — an invented "
                     "relationship is worse than a visible gap."),
        },
        "relationship_basis": (
            "QHIN is the canonical managed_by_qhin edge written at promotion. "
            "Participant and Subparticipant are the delivered entity_level. No "
            "relationship is inferred from name, OID or address."),
    }


def _totals(per_qhin: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    keys = ("population", "in_review", "assigned", "completed", "qa",
            "participants", "subparticipants")
    out = {key: sum(b[key] for b in per_qhin.values()) for key in keys}
    by_state: Dict[str, int] = {state: 0 for state in STATE_ORDER}
    for bucket in per_qhin.values():
        for state, count in bucket["by_state"].items():
            by_state[state] = by_state.get(state, 0) + count
    out["by_state"] = by_state
    out["percent_complete"] = (
        round(100.0 * out["completed"] / out["in_review"], 1)
        if out["in_review"] else None)
    return out


def _reason_counts(unresolved: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for unit in unresolved:
        reason = unit.get("reason") or "unstated"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


# ── one QHIN, in detail ──────────────────────────────────────────────────────

async def qhin_detail(db, intake_id, qhin_entity_id, *, sample_id=None,
                      include_held: bool = False, limit: int = 500,
                      offset: int = 0) -> Dict[str, Any]:
    """The review population of ONE QHIN, entity by entity.

    This is the screen a PM assigns from, so it carries what an assignment
    decision needs — the entity, its level, its parent, who holds the case and
    what state it is in — and nothing else. Evidence and determinations live in
    the analyst's workspace.
    """
    from app.tefca_registry.qhin_sampling import resolve_qhin_strata

    eligible, _ = await resolve_qhin_strata(db, intake_id,
                                            include_held=include_held)
    target = str(qhin_entity_id)
    entity_ids = [u["entity_id"] for u in eligible if u["qhin"] == target]
    if not entity_ids:
        return {"intake_id": str(intake_id), "qhin_entity_id": target,
                "population": 0, "items": [], "count": 0, "offset": offset}

    page = entity_ids[offset:offset + limit]
    entities = {e.id: e for e in (await db.execute(
        select(reg.TefcaRegEntity)
        .where(reg.TefcaRegEntity.id.in_(page)))).scalars().all()}
    parents = await _parent_names(db, page)

    stmt = select(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id.in_(page))
    if sample_id is not None:
        stmt = stmt.where(reg.ReviewRecord.sample_id == sample_id)
    reviews = (await db.execute(stmt)).scalars().all()
    by_entity: Dict[Any, Any] = {r.entity_id: r for r in reviews}
    states = await case_states(db, [r.review_id for r in reviews])

    names = await qhin_names(db, [target])
    items = []
    for entity_id in page:
        entity = entities.get(entity_id)
        record = by_entity.get(entity_id)
        items.append({
            "entity_id": str(entity_id),
            "name": getattr(entity, "name", None),
            "entity_level": getattr(entity, "entity_level", None),
            "entity_type": getattr(entity, "entity_type", None),
            "operational_status": getattr(entity, "operational_status", None),
            "parent": parents.get(entity_id),
            "review_id": getattr(record, "review_id", None),
            "in_review": record is not None,
            "state": (states.get(record.review_id) if record else None),
            "assigned_to_user_id": (str(record.assigned_to_user_id)
                                    if record and record.assigned_to_user_id
                                    else None),
            "classification_bucket": getattr(record, "classification_bucket",
                                             None),
        })

    return {
        "intake_id": str(intake_id),
        "qhin_entity_id": target,
        "qhin_name": names.get(target, "(unnamed QHIN)"),
        "population": len(entity_ids),
        "items": items,
        "count": len(items),
        "offset": offset,
    }


# ── auto-distribution ────────────────────────────────────────────────────────

async def plan_distribution(db, review_ids: List[str],
                            analyst_ids: List[uuid.UUID]
                            ) -> List[Tuple[str, uuid.UUID]]:
    """Spread unassigned cases evenly across analysts. WORKLOAD ONLY.

    THIS MAKES NO COMPLIANCE DECISION AND MUST NEVER BE MADE TO.
    It decides who LOOKS at a case, never what the case concludes. There is
    deliberately no scoring, no priority weighting by bucket, and no routing by
    finding — any of those would be the system forming a view about an entity
    before an analyst has, and that view would then be the one the analyst is
    arguing against.

    Cases already held by someone are skipped rather than reassigned: taking a
    case out of an analyst's hands mid-review is a supervisor's act, and it has
    its own audited route (`case_assignment.assign`).

    Deterministic — cases in the order given, analysts round-robin — so the same
    plan is produced twice and a PM can preview it before applying.
    """
    if not analyst_ids:
        raise WorkloadRefused("No analysts were given to distribute across.")
    if not review_ids:
        return []

    records = (await db.execute(
        select(reg.ReviewRecord.review_id, reg.ReviewRecord.assigned_to_user_id)
        .where(reg.ReviewRecord.review_id.in_(review_ids)))).all()
    unassigned = [rid for rid, holder in records if holder is None]
    unassigned.sort()

    plan: List[Tuple[str, uuid.UUID]] = []
    for index, review_id in enumerate(unassigned):
        plan.append((review_id, analyst_ids[index % len(analyst_ids)]))
    return plan


async def apply_distribution(db, plan: List[Tuple[str, uuid.UUID]], *, user,
                             ip_address: Optional[str] = None
                             ) -> Dict[str, Any]:
    """Apply a distribution plan through the EXISTING assignment route.

    Every assignment goes through `case_assignment.assign`, so each one gets the
    same authorisation check, the same refusal rules and the same audit row a
    single manual assignment gets. Bulk is a convenience over the controlled
    act, never a way around it.

    A refused assignment does not abort the batch — one case that cannot be
    assigned should not cost the other two hundred — but every refusal is
    returned with its reason.
    """
    from app.tefca_registry.case_assignment import AssignmentRefused, assign

    assigned, refused = [], []
    for review_id, to_user_id in plan:
        try:
            await assign(db, review_id, user=user, to_user_id=to_user_id,
                         ip_address=ip_address)
            assigned.append({"review_id": review_id,
                             "assigned_to": str(to_user_id)})
        except AssignmentRefused as exc:
            refused.append({"review_id": review_id, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.error("distribution failed for %s: %s", review_id, exc)
            refused.append({"review_id": review_id,
                            "reason": f"{type(exc).__name__}"})
    return {
        "requested": len(plan),
        "assigned": len(assigned),
        "refused": len(refused),
        "assignments": assigned,
        "refusals": refused,
        "note": ("Each assignment was made through the standard assignment "
                 "route and is individually audited."),
    }


# ── entity lookups ───────────────────────────────────────────────────────────

async def qhin_names(db, qhin_ids: List[str]) -> Dict[str, str]:
    """Display names for QHIN entity ids. Never invents one."""
    ids = [q for q in qhin_ids if q and q != UNRESOLVED_QHIN]
    if not ids:
        return {}
    try:
        rows = (await db.execute(
            select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.name,
                   reg.TefcaRegEntity.display_name)
            .where(reg.TefcaRegEntity.id.in_(ids)))).all()
    except Exception as exc:  # noqa: BLE001 — a malformed id must not 500 a rollup
        logger.info("QHIN name lookup failed: %s", exc)
        return {}
    return {str(i): (display or name) for i, name, display in rows}


async def _entity_levels(db, entity_ids: List[Any]) -> Dict[Any, str]:
    if not entity_ids:
        return {}
    rows = (await db.execute(
        select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.entity_level)
        .where(reg.TefcaRegEntity.id.in_(entity_ids)))).all()
    return {i: level for i, level in rows}


async def _parent_names(db, entity_ids: List[Any]) -> Dict[Any, Optional[Dict]]:
    """The `sub_participant_of` parent of each entity, where there is one.

    A Participant connects to its QHIN and has no sub_participant_of edge; that
    is a correct absence and is returned as None rather than as the QHIN, which
    would quietly turn every Participant into a Subparticipant.
    """
    if not entity_ids:
        return {}
    edges = (await db.execute(
        select(reg.TefcaEntityRelationship.child_entity_id,
               reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id.in_(entity_ids),
               reg.TefcaEntityRelationship.relationship_type
               == "sub_participant_of",
               reg.TefcaEntityRelationship.status == "active"))).all()
    if not edges:
        return {}
    parent_ids = list({p for _, p in edges})
    names = {i: (display or name) for i, name, display in (await db.execute(
        select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.name,
               reg.TefcaRegEntity.display_name)
        .where(reg.TefcaRegEntity.id.in_(parent_ids)))).all()}
    return {child: {"entity_id": str(parent), "name": names.get(parent)}
            for child, parent in edges}
