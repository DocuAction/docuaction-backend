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

from sqlalchemy import func, select, update

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


async def _snapshot_status_by_intake(db, intake_ids) -> Dict[str, Optional[str]]:
    """Chain-tip status per intake id (string keys); None when never registered."""
    from app.tefca_registry.rce import snapshot_effects as se

    out: Dict[str, Optional[str]] = {}
    for iid in {str(i) for i in intake_ids if i}:
        tip = await se.snapshot_tip(db, uuid.UUID(iid))
        out[iid] = tip.status if tip is not None else None
    return out


def _effective(status: Optional[str]) -> bool:
    return status in sm.SNAPSHOT_EFFECTIVE


async def history_for_entity(db, entity_id) -> Dict[str, Any]:
    """Current and historical edges of an entity (as child) with observations.

    CURRENT VIEW RULE (P1-1): an edge asserted by a snapshot that is not
    APPROVED is `staged`, not current; an edge ended by such a snapshot is
    still current (`pending_supersession_by` names the staged replacement).
    Edges with no observation at all predate the snapshot model and are
    current as they stand.
    """
    edges = (await db.execute(
        select(reg.TefcaEntityRelationship)
        .where(reg.TefcaEntityRelationship.child_entity_id == entity_id)
        .order_by(reg.TefcaEntityRelationship.effective_date.desc()))).scalars().all()
    obs = (await db.execute(
        select(sm.TefcaRelationshipObservation)
        .where(sm.TefcaRelationshipObservation.child_entity_id == entity_id)
        .order_by(sm.TefcaRelationshipObservation.observed_at.desc()))).scalars().all()
    status_by_intake = await _snapshot_status_by_intake(db, [o.intake_id for o in obs])

    asserted_by: Dict[str, Any] = {}     # rel id -> ASSERTED observation (latest)
    superseded_by: Dict[str, Any] = {}   # rel id -> SUPERSEDED observation (latest)
    rolled_back: set = set()
    restored: set = set()
    for o in obs:                        # newest first, so first wins
        rid = str(o.relationship_id) if o.relationship_id else None
        if rid is None:
            continue
        if o.observation == sm.OBS_ASSERTED:
            asserted_by.setdefault(rid, o)
        elif o.observation == sm.OBS_SUPERSEDED:
            superseded_by.setdefault(rid, o)
        elif o.observation == sm.OBS_ROLLED_BACK:
            rolled_back.add(rid)
        elif o.observation == sm.OBS_RESTORED:
            restored.add(rid)

    current: List[Dict[str, Any]] = []
    staged: List[Dict[str, Any]] = []
    historical: List[Dict[str, Any]] = []
    for e in edges:
        rid = str(e.id)
        d = _edge(e)
        a = asserted_by.get(rid)
        a_status = status_by_intake.get(str(a.intake_id)) if a else None
        if e.end_date is None:
            if a is not None and not _effective(a_status) and rid not in restored:
                d["snapshot_status"] = a_status
                d["asserting_intake_id"] = str(a.intake_id)
                staged.append(d)
            else:
                d["snapshot_status"] = a_status if a is not None else "legacy"
                current.append(d)
        else:
            s_obs = superseded_by.get(rid)
            s_status = status_by_intake.get(str(s_obs.intake_id)) if s_obs else None
            if (s_obs is not None and not _effective(s_status) and rid not in rolled_back
                    and e.status == STATUS_HISTORICAL):
                # ended by a snapshot that is not approved: still the current
                # relationship until that snapshot is approved.
                replacement = next((r for r, o in asserted_by.items()
                                    if o.supersedes_relationship_id == e.id), None)
                d["pending_supersession_by"] = replacement
                d["snapshot_status"] = s_status
                current.append(d)
            else:
                d["ended_by_snapshot_status"] = s_status
                historical.append(d)

    return {
        "entity_id": str(entity_id),
        "current": current,
        "staged": staged,
        "historical": historical,
        "observations": [{
            "id": str(o.id), "observation": o.observation, "relationship_type": o.relationship_type,
            "relationship_id": str(o.relationship_id) if o.relationship_id else None,
            "parent_entity_id": str(o.parent_entity_id) if o.parent_entity_id else None,
            "supersedes_relationship_id": (str(o.supersedes_relationship_id)
                                           if o.supersedes_relationship_id else None),
            "intake_id": str(o.intake_id), "effective_boundary": o.effective_boundary.isoformat(),
            "snapshot_status": status_by_intake.get(str(o.intake_id)),
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


# ── P1-2: compensating rollback of one snapshot's relationship changes ───────
#
# Three different things, never confused:
#   * Alembic schema downgrade   drops the 1.3.0 evidence tables (and REFUSES
#                                when they hold rows) — it does not touch
#                                tefca_entity_relationships.
#   * Application/image rollback redeploys the previous build — the data an
#                                already-applied promotion wrote stays as is.
#   * Data compensation (THIS)   reverses the relationship changes ONE snapshot
#                                applied, using the snapshot's own observation
#                                evidence, append-only and idempotent.

STATUS_ROLLED_BACK = "rolled_back"


class RollbackRefused(RuntimeError):
    pass


async def plan_snapshot_rollback(db, intake_id) -> Dict[str, Any]:
    """Read-only plan: which replacement edges to retire and which prior edges
    to restore for ONE intake, with every refusal reason. Raises
    RollbackRefused on any fail-closed condition."""
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import snapshot_effects as se

    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise RollbackRefused(f"scope: no delivery {intake_id}")
    tip = await se.snapshot_tip(db, intake_id)
    if tip is not None and tip.intake_id != intake.id:
        raise RollbackRefused("scope: snapshot chain does not belong to the requested intake")

    # A later APPROVED snapshot may depend on the edges this one created.
    later_approved = (await db.execute(
        select(sm.SourceSnapshot.intake_id, m.RceSourceIntake.received_at)
        .join(m.RceSourceIntake, m.RceSourceIntake.id == sm.SourceSnapshot.intake_id)
        .where(sm.SourceSnapshot.source_system == sm.SOURCE_ONC_RCE,
               sm.SourceSnapshot.status == sm.SNAPSHOT_APPROVED,
               m.RceSourceIntake.received_at > intake.received_at,
               sm.SourceSnapshot.id.notin_(
                   select(sm.SourceSnapshot.supersedes_snapshot_id)
                   .where(sm.SourceSnapshot.supersedes_snapshot_id.isnot(None)))))).all()
    if later_approved:
        raise RollbackRefused(
            f"a later APPROVED snapshot exists (intake {later_approved[0][0]}); "
            f"roll that one back first or leave this one in place")

    obs = (await db.execute(
        select(sm.TefcaRelationshipObservation)
        .where(sm.TefcaRelationshipObservation.intake_id == intake_id)
        .order_by(sm.TefcaRelationshipObservation.observed_at.asc()))).scalars().all()
    asserted = [o for o in obs if o.observation == sm.OBS_ASSERTED]
    superseded = {str(o.relationship_id): o for o in obs
                  if o.observation == sm.OBS_SUPERSEDED and o.relationship_id}
    already_rolled = {str(o.relationship_id) for o in obs
                      if o.observation == sm.OBS_ROLLED_BACK and o.relationship_id}
    already_restored = {str(o.relationship_id) for o in obs
                        if o.observation == sm.OBS_RESTORED and o.relationship_id}

    for o in asserted:
        if o.relationship_id is None:
            raise RollbackRefused(f"evidence incomplete: ASSERTED observation {o.id} names no edge")
    for rid, o in superseded.items():
        if not any(str(a.supersedes_relationship_id) == rid for a in asserted):
            raise RollbackRefused(f"evidence incomplete: SUPERSEDED edge {rid} has no ASSERTED replacement")

    rel_ids = [o.relationship_id for o in asserted] + [o.relationship_id for o in superseded.values()]
    edges = {str(e.id): e for e in (await db.execute(
        select(reg.TefcaEntityRelationship)
        .where(reg.TefcaEntityRelationship.id.in_(rel_ids)))).scalars().all()} if rel_ids else {}

    steps: List[Dict[str, Any]] = []
    for a in asserted:
        rid = str(a.relationship_id)
        new_edge = edges.get(rid)
        if new_edge is None:
            raise RollbackRefused(f"evidence incomplete: asserted edge {rid} no longer exists")
        if str(new_edge.child_entity_id) != str(a.child_entity_id) or new_edge.relationship_type != a.relationship_type:
            raise RollbackRefused(f"scope: edge {rid} does not match its observation (child/type)")
        prior = None
        if a.supersedes_relationship_id is not None:
            pid = str(a.supersedes_relationship_id)
            prior = edges.get(pid)
            if prior is None:
                raise RollbackRefused(f"evidence incomplete: superseded edge {pid} no longer exists")
            if pid not in superseded:
                raise RollbackRefused(f"evidence incomplete: no SUPERSEDED observation for {pid}")
            if str(prior.child_entity_id) != str(new_edge.child_entity_id) or prior.relationship_type != new_edge.relationship_type:
                raise RollbackRefused(f"ambiguous: superseded edge {pid} is not the same child/type as {rid}")
            # a prior edge later re-superseded by a different snapshot is ambiguous
            others = (await db.execute(
                select(func.count()).select_from(sm.TefcaRelationshipObservation)
                .where(sm.TefcaRelationshipObservation.relationship_id == prior.id,
                       sm.TefcaRelationshipObservation.observation == sm.OBS_SUPERSEDED,
                       sm.TefcaRelationshipObservation.intake_id != intake_id))).scalar()
            if int(others or 0) > 0:
                raise RollbackRefused(f"ambiguous: prior edge {pid} was also superseded by another snapshot")
        done = rid in already_rolled and (prior is None or str(prior.id) in already_restored)
        steps.append({"retire": rid, "restore": str(prior.id) if prior is not None else None,
                      "child_entity_id": str(new_edge.child_entity_id),
                      "relationship_type": new_edge.relationship_type, "already_done": done,
                      "retire_active": new_edge.end_date is None,
                      "restore_active": (prior.end_date is None) if prior is not None else None})

    # would any restore leave TWO active parents for the same (child, type)?
    for st in steps:
        if st["already_done"] or st["restore"] is None:
            continue
        active_others = (await db.execute(
            select(func.count()).select_from(reg.TefcaEntityRelationship)
            .where(reg.TefcaEntityRelationship.child_entity_id == uuid.UUID(st["child_entity_id"]),
                   reg.TefcaEntityRelationship.relationship_type == st["relationship_type"],
                   reg.TefcaEntityRelationship.end_date.is_(None),
                   reg.TefcaEntityRelationship.id.notin_(
                       [uuid.UUID(st["retire"]), uuid.UUID(st["restore"])])))).scalar()
        if int(active_others or 0) > 0:
            raise RollbackRefused(
                f"two active parents: child {st['child_entity_id']} already has another active "
                f"{st['relationship_type']} edge besides {st['retire']}")

    return {"intake_id": str(intake_id), "tip_status": tip.status if tip else None,
            "steps": steps, "to_retire": sum(1 for s in steps if not s["already_done"]),
            "to_restore": sum(1 for s in steps if not s["already_done"] and s["restore"]),
            "already_done": sum(1 for s in steps if s["already_done"])}


async def compensate_snapshot(db, intake_id, *, actor: str, role: str, reason: str,
                              apply: bool = False, commit: bool = True) -> Dict[str, Any]:
    """Reverse the relationship changes ONE snapshot applied. `apply=False`
    only plans. Append-only evidence: ROLLED_BACK / RESTORED observations,
    a ROLLED_BACK snapshot row, and a registry audit row — each with actor,
    role, reason, intake, build SHA and correlation id. Idempotent: steps
    already evidenced as done are skipped; a repeat changes nothing."""
    from datetime import date as _date
    from app.core.security import ROLE_HIERARCHY
    from app.tefca_registry import audit as reg_audit
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import snapshot_effects as se

    if ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY.get("program_manager", 7):
        raise RollbackRefused("rollback requires the Data Operations (program_manager) role or above")
    if not (reason or "").strip():
        raise RollbackRefused("a reason is required")
    plan = await plan_snapshot_rollback(db, intake_id)
    if not apply:
        return {"applied": False, **plan}

    intake = await db.get(m.RceSourceIntake, intake_id)
    boundary = _date.today()
    cid, sha = request_context.correlation_id()[:64], request_context.build_sha()
    retired, restored_n = 0, 0
    for st in plan["steps"]:
        if st["already_done"]:
            continue
        child = uuid.UUID(st["child_entity_id"])
        retire_id = uuid.UUID(st["retire"])
        if st["retire_active"]:
            await db.execute(update(reg.TefcaEntityRelationship)
                             .where(reg.TefcaEntityRelationship.id == retire_id)
                             .values(end_date=boundary, status=STATUS_ROLLED_BACK))
        db.add(_observation(
            intake_id=intake_id, child_id=child, parent_id=None, rel_type=st["relationship_type"],
            observation=sm.OBS_ROLLED_BACK, boundary=boundary, relationship_id=retire_id,
            actor=actor, reason=f"compensation by {actor} ({role}): {reason}"))
        retired += 1
        if st["restore"]:
            restore_id = uuid.UUID(st["restore"])
            await db.execute(update(reg.TefcaEntityRelationship)
                             .where(reg.TefcaEntityRelationship.id == restore_id)
                             .values(end_date=None, status="active"))
            db.add(_observation(
                intake_id=intake_id, child_id=child, parent_id=None, rel_type=st["relationship_type"],
                observation=sm.OBS_RESTORED, boundary=boundary, relationship_id=restore_id,
                supersedes=retire_id, actor=actor,
                reason=f"restored as current by compensation ({role}): {reason}"))
            restored_n += 1

    # the snapshot chain records the rollback (append-only), unless already
    tip = await se.snapshot_tip(db, intake_id)
    snapshot_row = None
    if tip is not None and tip.status != sm.SNAPSHOT_ROLLED_BACK:
        snapshot_row = se._snapshot_row(
            intake, status=sm.SNAPSHOT_ROLLED_BACK, supersedes=tip.id, actor=actor,
            metadata={"rollback": {"reason": reason[:500], "role": role, "retired": retired,
                                   "restored": restored_n}})
        db.add(snapshot_row)
    if retired or restored_n or snapshot_row is not None:
        reg_audit.record(db, "relationship_snapshot_rolled_back", None, actor_email=actor,
                         metadata={"source_intake_id": str(intake_id), "actor_role": role,
                                   "reason": reason[:500], "retired": retired,
                                   "restored": restored_n, "build_sha": sha,
                                   "correlation_id": cid})
    if commit:
        await db.commit()
    return {"applied": True, "retired": retired, "restored": restored_n,
            "skipped_already_done": plan["already_done"],
            "snapshot_row_id": str(snapshot_row.id) if snapshot_row is not None else None,
            "actor": actor, "role": role, "build_sha": sha, "correlation_id": cid,
            "intake_id": str(intake_id)}
