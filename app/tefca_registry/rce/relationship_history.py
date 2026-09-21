"""Relationship SUPERSESSION for monthly deliveries (September 2026).

Promotion pass 2 used to be add-only: a Subparticipant whose `partOf` moved
(228 records, KONZA `.300` → `.700`) gained a second active parent edge and
kept the first. This module makes pass 2 a reconciliation of the CHILD's
current edges against the delivered parent, with history kept in
`tefca_entity_relationships` (end_date/status) and provenance in
`tefca_relationship_observations`.

Rules (fail closed — a refused move changes nothing and writes why):
  UNRESOLVED_PARENT   delivered partOf resolves to no registry entity.
  CROSS_QHIN_REFUSED  the new parent's managing QHIN is not the child's.
                      A Subparticipant is never moved across QHINs by a file.
  SNAPSHOT_MISMATCH   the delivery boundary is not after the current edge's
                      effective_date (an older file cannot end a newer edge),
                      or the (parent, child, type, boundary) key already exists.
  SUPERSEDED          the old edge gets end_date = boundary, status =
                      'historical'; nothing else on it changes.
  ASSERTED            the new edge is inserted effective_date = boundary,
                      supersedes_relationship_id = the old one.

The boundary is the delivery's `received_at.date()` — the date the RCE's
file said so — never "today" and never a date the pipeline chose.

Only `end_date` and `status` are ever updated on an existing edge (the
migration grants the app role exactly those two columns).
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update

from app.core import request_context
from app.tefca_registry import models as reg
from app.tefca_registry.rce import snapshot_models as sm

logger = logging.getLogger(__name__)

STATUS_HISTORICAL = "historical"


class EdgeState:
    """Everything pass 2 needs about the CURRENT edges, loaded once."""

    def __init__(self) -> None:
        #: (parent, child, type) -> list of (rel_id, effective_date, end_date)
        self.by_key: Dict[Tuple[str, str, str], List[Tuple[Any, date, Optional[date]]]] = {}
        #: (child, type) -> (rel_id, parent, effective_date) for the ACTIVE edge
        self.active: Dict[Tuple[str, str], Tuple[Any, str, date]] = {}
        self.qhin_of_entity: Dict[str, Optional[str]] = {}
        self.counters = {"asserted": 0, "superseded": 0, "unresolved_parent": 0,
                         "cross_qhin_refused": 0, "snapshot_mismatch": 0,
                         "observations": 0, "unchanged": 0}

    @classmethod
    async def load(cls, db) -> "EdgeState":
        st = cls()
        rows = (await db.execute(
            select(reg.TefcaEntityRelationship.id,
                   reg.TefcaEntityRelationship.parent_entity_id,
                   reg.TefcaEntityRelationship.child_entity_id,
                   reg.TefcaEntityRelationship.relationship_type,
                   reg.TefcaEntityRelationship.effective_date,
                   reg.TefcaEntityRelationship.end_date,
                   reg.TefcaEntityRelationship.status))).all()
        for rid, p, c, t, eff, end, status in rows:
            key = (str(p), str(c), t)
            st.by_key.setdefault(key, []).append((rid, eff, end))
            if end is None and (status or "active") == "active":
                st.active[(str(c), t)] = (rid, str(p), eff)
        return st

    async def qhin_of(self, db, entity_id) -> Optional[str]:
        k = str(entity_id)
        if k not in self.qhin_of_entity:
            self.qhin_of_entity[k] = (await db.execute(
                select(reg.TefcaRegEntity.org_managing_org)
                .where(reg.TefcaRegEntity.id == entity_id))).scalar_one_or_none()
        return self.qhin_of_entity[k]


def _observation(*, intake_id, child_id, parent_id, rel_type, observation, boundary,
                 relationship_id=None, supersedes=None, delivered_parent_oid=None,
                 reason: str, actor: str) -> sm.TefcaRelationshipObservation:
    return sm.TefcaRelationshipObservation(
        id=uuid.uuid4(), relationship_id=relationship_id, child_entity_id=child_id,
        parent_entity_id=parent_id, relationship_type=rel_type, observation=observation,
        intake_id=intake_id, effective_boundary=boundary,
        supersedes_relationship_id=supersedes, delivered_parent_oid=delivered_parent_oid,
        reason=reason[:2000], actor=actor[:320],
        correlation_id=request_context.correlation_id()[:64],
        build_sha=request_context.build_sha())


async def sync_edge(db, state: EdgeState, *, intake_id, boundary: date, child_id,
                    parent_id, rel_type: str, delivered_parent_oid: Optional[str],
                    child_qhin_oid: Optional[str], actor: str, notes: str,
                    enforce_same_qhin: bool) -> str:
    """Make `parent_id` the child's current `rel_type` parent, or refuse.
    Returns the observation written (or 'unchanged')."""
    c = state.counters
    ckey = (str(child_id), rel_type)

    if parent_id is None:
        c["unresolved_parent"] += 1
        db.add(_observation(
            intake_id=intake_id, child_id=child_id, parent_id=None, rel_type=rel_type,
            observation=sm.OBS_UNRESOLVED_PARENT, boundary=boundary,
            delivered_parent_oid=delivered_parent_oid, actor=actor,
            reason=f"partOf {delivered_parent_oid!r} resolves to no registry entity; "
                   f"the child's current edge (if any) is kept."))
        c["observations"] += 1
        return sm.OBS_UNRESOLVED_PARENT

    current = state.active.get(ckey)
    if current is not None and current[1] == str(parent_id):
        c["unchanged"] += 1
        return "unchanged"

    if enforce_same_qhin and current is not None:
        parent_qhin = await state.qhin_of(db, parent_id)
        if child_qhin_oid and parent_qhin and parent_qhin != child_qhin_oid:
            c["cross_qhin_refused"] += 1
            db.add(_observation(
                intake_id=intake_id, child_id=child_id, parent_id=parent_id,
                rel_type=rel_type, observation=sm.OBS_CROSS_QHIN_REFUSED, boundary=boundary,
                relationship_id=current[0], delivered_parent_oid=delivered_parent_oid,
                actor=actor,
                reason=f"delivered parent is managed by QHIN {parent_qhin!r}; the child is "
                       f"managed by {child_qhin_oid!r}. A file does not move a "
                       f"Subparticipant across QHINs; current edge kept."))
            c["observations"] += 1
            return sm.OBS_CROSS_QHIN_REFUSED

    new_key = (str(parent_id), str(child_id), rel_type)
    if current is not None and boundary <= current[2]:
        c["snapshot_mismatch"] += 1
        db.add(_observation(
            intake_id=intake_id, child_id=child_id, parent_id=parent_id, rel_type=rel_type,
            observation=sm.OBS_SNAPSHOT_MISMATCH, boundary=boundary,
            relationship_id=current[0], delivered_parent_oid=delivered_parent_oid,
            actor=actor,
            reason=f"delivery boundary {boundary} is not after the current edge's "
                   f"effective_date {current[2]}; an older snapshot cannot end a "
                   f"newer edge. Current edge kept."))
        c["observations"] += 1
        return sm.OBS_SNAPSHOT_MISMATCH
    if any(eff == boundary for _, eff, _ in state.by_key.get(new_key, [])):
        c["snapshot_mismatch"] += 1
        db.add(_observation(
            intake_id=intake_id, child_id=child_id, parent_id=parent_id, rel_type=rel_type,
            observation=sm.OBS_SNAPSHOT_MISMATCH, boundary=boundary,
            relationship_id=current[0] if current else None,
            delivered_parent_oid=delivered_parent_oid, actor=actor,
            reason=f"an edge (parent, child, {rel_type}) effective {boundary} already "
                   f"exists; not re-asserted."))
        c["observations"] += 1
        return sm.OBS_SNAPSHOT_MISMATCH

    old_id = None
    if current is not None:
        old_id = current[0]
        await db.execute(
            update(reg.TefcaEntityRelationship)
            .where(reg.TefcaEntityRelationship.id == old_id)
            .values(end_date=boundary, status=STATUS_HISTORICAL))
        for i, (rid, eff, end) in enumerate(state.by_key.get(
                (current[1], str(child_id), rel_type), [])):
            if rid == old_id:
                state.by_key[(current[1], str(child_id), rel_type)][i] = (rid, eff, boundary)
        db.add(_observation(
            intake_id=intake_id, child_id=child_id, parent_id=uuid.UUID(current[1]),
            rel_type=rel_type, observation=sm.OBS_SUPERSEDED, boundary=boundary,
            relationship_id=old_id, delivered_parent_oid=delivered_parent_oid, actor=actor,
            reason=f"delivery asserts a different parent; edge ended at {boundary} "
                   f"(status {STATUS_HISTORICAL}). The row is otherwise untouched."))
        c["superseded"] += 1
        c["observations"] += 1

    new_id = uuid.uuid4()
    db.add(reg.TefcaEntityRelationship(
        id=new_id, parent_entity_id=parent_id, child_entity_id=child_id,
        relationship_type=rel_type, effective_date=boundary, status="active",
        source="import", notes=notes))
    state.by_key.setdefault(new_key, []).append((new_id, boundary, None))
    state.active[ckey] = (new_id, str(parent_id), boundary)
    db.add(_observation(
        intake_id=intake_id, child_id=child_id, parent_id=parent_id, rel_type=rel_type,
        observation=sm.OBS_ASSERTED, boundary=boundary, relationship_id=new_id,
        supersedes=old_id, delivered_parent_oid=delivered_parent_oid, actor=actor,
        reason=("asserted by delivery" if old_id is None
                else f"asserted by delivery, superseding {old_id}")))
    c["asserted"] += 1
    c["observations"] += 1
    return sm.OBS_ASSERTED


async def history_for_entity(db, entity_id) -> Dict[str, Any]:
    """Current and historical edges of an entity (as child) with observations."""
    edges = (await db.execute(
        select(reg.TefcaEntityRelationship)
        .where(reg.TefcaEntityRelationship.child_entity_id == entity_id)
        .order_by(reg.TefcaEntityRelationship.effective_date.desc()))).scalars().all()
    obs = (await db.execute(
        select(sm.TefcaRelationshipObservation)
        .where(sm.TefcaRelationshipObservation.child_entity_id == entity_id)
        .order_by(sm.TefcaRelationshipObservation.observed_at.desc()))).scalars().all()
    return {
        "entity_id": str(entity_id),
        "current": [_edge(e) for e in edges if e.end_date is None],
        "historical": [_edge(e) for e in edges if e.end_date is not None],
        "observations": [{
            "id": str(o.id), "observation": o.observation, "relationship_type": o.relationship_type,
            "relationship_id": str(o.relationship_id) if o.relationship_id else None,
            "parent_entity_id": str(o.parent_entity_id) if o.parent_entity_id else None,
            "supersedes_relationship_id": (str(o.supersedes_relationship_id)
                                           if o.supersedes_relationship_id else None),
            "intake_id": str(o.intake_id), "effective_boundary": o.effective_boundary.isoformat(),
            "delivered_parent_oid": o.delivered_parent_oid, "reason": o.reason,
            "actor": o.actor, "observed_at": o.observed_at.isoformat() if o.observed_at else None,
        } for o in obs],
    }


def _edge(e) -> Dict[str, Any]:
    return {"id": str(e.id), "parent_entity_id": str(e.parent_entity_id),
            "relationship_type": e.relationship_type,
            "effective_date": e.effective_date.isoformat() if e.effective_date else None,
            "end_date": e.end_date.isoformat() if e.end_date else None,
            "status": e.status, "source": e.source}
