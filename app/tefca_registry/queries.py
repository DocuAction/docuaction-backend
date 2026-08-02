"""
Read/query layer for the TEFCA registry API (Phase 2A).

Pure read functions over the ``tefca_reg_*`` tables. Each returns plain
dict/list structures (JSON-ready via FastAPI's encoder). No writes here.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry import models as reg

# Relationship types that define the QHIN → Participant → Sub hierarchy.
HIERARCHY_TYPES = ("belongs_to", "sub_participant_of")


# ── serializers ───────────────────────────────────────────────────────────────

def entity_summary(e: reg.TefcaRegEntity) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "display_name": e.display_name,
        "entity_level": e.entity_level,
        "entity_type": e.entity_type,
        "operational_status": e.operational_status,
        "verification_status": e.verification_status,
        "state": e.state,
        "is_active": e.is_active,
        "current_version": e.current_version,
        # Populated by _attach_identifiers for list/tree rows (None until attached).
        "npi": None,
        "tefcaid": None,
    }


async def _attach_identifiers(session: AsyncSession, summaries: list[dict]) -> list[dict]:
    """Batch-load each entity's primary active NPI + TEFCAID onto its summary dict.

    One query for the whole page of rows (no N+1). is_primary wins when an entity
    has more than one identifier of a type.
    """
    ids = [s["id"] for s in summaries if s.get("id")]
    if not ids:
        return summaries
    rows = (await session.execute(
        select(
            reg.TefcaEntityIdentifier.entity_id,
            reg.TefcaEntityIdentifier.identifier_type,
            reg.TefcaEntityIdentifier.identifier_value,
            reg.TefcaEntityIdentifier.is_primary,
        ).where(
            reg.TefcaEntityIdentifier.entity_id.in_(ids),
            reg.TefcaEntityIdentifier.identifier_type.in_(("npi", "tefcaid")),
            reg.TefcaEntityIdentifier.identifier_status == "active",
        ))).all()
    npi, tef = {}, {}
    for eid, itype, val, is_primary in rows:
        target = npi if itype == "npi" else tef
        if eid not in target or is_primary:
            target[eid] = val
    for s in summaries:
        s["npi"] = npi.get(s["id"])
        s["tefcaid"] = tef.get(s["id"])
    return summaries


def entity_full(e: reg.TefcaRegEntity) -> dict:
    d = entity_summary(e)
    d.update({
        "address": e.address, "city": e.city, "zip": e.zip, "county": e.county,
        "designation_date": e.designation_date, "onboarding_date": e.onboarding_date,
        "exchange_purposes": e.exchange_purposes,
        "fhir_resource": e.fhir_resource,
        "created_at": e.created_at, "updated_at": e.updated_at,
    })
    return d


def identifier_dict(i: reg.TefcaEntityIdentifier) -> dict:
    return {
        "id": i.id, "identifier_type": i.identifier_type,
        "identifier_value": i.identifier_value, "system_uri": i.system_uri,
        "is_primary": i.is_primary, "identifier_status": i.identifier_status,
        "effective_date": i.effective_date, "end_date": i.end_date,
    }


def finding_dict(f: reg.TefcaEntityFinding) -> dict:
    return {
        "id": f.id, "entity_id": f.entity_id, "finding_type": f.finding_type,
        "severity": f.severity, "title": f.title, "description": f.description,
        "status": f.status, "evidence": f.evidence,
        "verification_check_id": f.verification_check_id,
        "created_at": f.created_at,
    }


# ── entity list / detail ──────────────────────────────────────────────────────

async def list_entities(session: AsyncSession, *, entity_level=None, entity_type=None,
                        state=None, verification_status=None, operational_status=None,
                        is_active=None, q=None, limit=50, offset=0,
                        include_deleted=False) -> dict:
    # Soft-deleted rows are excluded by default. They must not appear in the
    # sample frame a weekly report draws from, or a report cites entities an
    # operator has already removed.
    conds = [] if include_deleted else [
        reg.TefcaRegEntity.is_deleted.is_(False)]
    if entity_level:
        conds.append(reg.TefcaRegEntity.entity_level == entity_level)
    if entity_type:
        conds.append(reg.TefcaRegEntity.entity_type == entity_type)
    if state:
        conds.append(reg.TefcaRegEntity.state == state)
    if verification_status:
        conds.append(reg.TefcaRegEntity.verification_status == verification_status)
    if operational_status:
        conds.append(reg.TefcaRegEntity.operational_status == operational_status)
    if is_active is not None:
        conds.append(reg.TefcaRegEntity.is_active == is_active)
    if q:
        conds.append(reg.TefcaRegEntity.name.ilike(f"%{q}%"))

    base = select(reg.TefcaRegEntity)
    if conds:
        base = base.where(*conds)
    total = await session.scalar(
        select(func.count()).select_from(base.subquery()))
    rows = (await session.execute(
        base.order_by(reg.TefcaRegEntity.entity_level, reg.TefcaRegEntity.name)
        .limit(limit).offset(offset))).scalars().all()
    items = await _attach_identifiers(session, [entity_summary(e) for e in rows])
    return {
        "items": items,
        "total": int(total or 0), "limit": limit, "offset": offset,
    }


async def _child_count(session, parent_id, *, active_only=True) -> int:
    conds = [reg.TefcaEntityRelationship.parent_entity_id == parent_id,
             reg.TefcaEntityRelationship.relationship_type.in_(HIERARCHY_TYPES)]
    if active_only:
        conds.append(reg.TefcaEntityRelationship.status == "active")
    return int(await session.scalar(
        select(func.count()).select_from(reg.TefcaEntityRelationship).where(*conds)) or 0)


async def get_entity_detail(session: AsyncSession, entity_id: uuid.UUID) -> Optional[dict]:
    e = await session.get(reg.TefcaRegEntity, entity_id)
    if not e:
        return None
    idents = (await session.execute(
        select(reg.TefcaEntityIdentifier)
        .where(reg.TefcaEntityIdentifier.entity_id == entity_id))).scalars().all()
    endpoints = (await session.execute(
        select(reg.TefcaEntityEndpoint)
        .where(reg.TefcaEntityEndpoint.entity_id == entity_id))).scalars().all()

    # parents (this entity is the child) and children (this entity is the parent)
    parent_rels = (await session.execute(
        select(reg.TefcaEntityRelationship, reg.TefcaRegEntity)
        .join(reg.TefcaRegEntity, reg.TefcaRegEntity.id == reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id == entity_id))).all()
    child_rels = (await session.execute(
        select(reg.TefcaEntityRelationship, reg.TefcaRegEntity)
        .join(reg.TefcaRegEntity, reg.TefcaRegEntity.id == reg.TefcaEntityRelationship.child_entity_id)
        .where(reg.TefcaEntityRelationship.parent_entity_id == entity_id))).all()

    latest_version = (await session.execute(
        select(reg.TefcaEntityVersion)
        .where(reg.TefcaEntityVersion.entity_id == entity_id)
        .order_by(reg.TefcaEntityVersion.version_number.desc()).limit(1))).scalar_one_or_none()
    findings = (await session.execute(
        select(reg.TefcaEntityFinding)
        .where(reg.TefcaEntityFinding.entity_id == entity_id)
        .order_by(reg.TefcaEntityFinding.created_at.desc()))).scalars().all()

    def rel_row(rel, other):
        return {
            "relationship_id": rel.id, "relationship_type": rel.relationship_type,
            "status": rel.status, "effective_date": rel.effective_date,
            "end_date": rel.end_date, "source": rel.source, "notes": rel.notes,
            "entity": entity_summary(other),
        }

    detail = entity_full(e)
    detail.update({
        "identifiers": [identifier_dict(i) for i in idents],
        "endpoints": [{
            "id": ep.id, "endpoint_type": ep.endpoint_type, "url": ep.url,
            "status": ep.status, "environment": ep.environment,
            "connection_type": ep.connection_type, "name": ep.name,
        } for ep in endpoints],
        "parents": [rel_row(r_, o) for r_, o in parent_rels],
        "children": [rel_row(r_, o) for r_, o in child_rels],
        "latest_version": (
            {"version_number": latest_version.version_number,
             "change_reason": latest_version.change_reason,
             "created_at": latest_version.created_at}
            if latest_version else None),
        "findings": [finding_dict(f) for f in findings],
        "findings_count": len(findings),
    })
    return detail


# ── qhins / participants ──────────────────────────────────────────────────────

async def list_qhins(session: AsyncSession) -> list[dict]:
    qhins = (await session.execute(
        select(reg.TefcaRegEntity)
        .where(reg.TefcaRegEntity.entity_level == "qhin")
        .order_by(reg.TefcaRegEntity.name))).scalars().all()
    out = []
    for q in qhins:
        d = entity_summary(q)
        d["participant_count"] = await _child_count(session, q.id)
        d["designation_date"] = q.designation_date
        out.append(d)
    return await _attach_identifiers(session, out)


async def list_participants(session: AsyncSession, *, qhin_id=None,
                            limit=200, offset=0) -> dict:
    if qhin_id:
        # participants that belong_to this QHIN (active)
        stmt = (select(reg.TefcaRegEntity)
                .join(reg.TefcaEntityRelationship,
                      reg.TefcaEntityRelationship.child_entity_id == reg.TefcaRegEntity.id)
                .where(reg.TefcaEntityRelationship.parent_entity_id == qhin_id,
                       reg.TefcaEntityRelationship.relationship_type == "belongs_to",
                       reg.TefcaEntityRelationship.status == "active",
                       reg.TefcaRegEntity.entity_level == "participant"))
    else:
        stmt = select(reg.TefcaRegEntity).where(
            reg.TefcaRegEntity.entity_level == "participant")
    rows = (await session.execute(
        stmt.order_by(reg.TefcaRegEntity.name).limit(limit).offset(offset))).scalars().all()
    out = []
    for p in rows:
        d = entity_summary(p)
        d["sub_participant_count"] = await _child_count(session, p.id)
        out.append(d)
    out = await _attach_identifiers(session, out)
    return {"items": out, "count": len(out)}


# ── hierarchy (lazy) ──────────────────────────────────────────────────────────

async def hierarchy_roots(session: AsyncSession) -> list[dict]:
    """Top level of the lazy tree: QHINs with their participant counts."""
    return await list_qhins(session)


async def get_children(session: AsyncSession, parent_id: uuid.UUID) -> list[dict]:
    """Direct active hierarchy children of an entity (one level — for lazy expand)."""
    rows = (await session.execute(
        select(reg.TefcaRegEntity, reg.TefcaEntityRelationship.relationship_type)
        .join(reg.TefcaEntityRelationship,
              reg.TefcaEntityRelationship.child_entity_id == reg.TefcaRegEntity.id)
        .where(reg.TefcaEntityRelationship.parent_entity_id == parent_id,
               reg.TefcaEntityRelationship.relationship_type.in_(HIERARCHY_TYPES),
               reg.TefcaEntityRelationship.status == "active")
        .order_by(reg.TefcaRegEntity.name))).all()
    out = []
    for child, rtype in rows:
        d = entity_summary(child)
        d["relationship_type"] = rtype
        d["child_count"] = await _child_count(session, child.id)
        out.append(d)
    return await _attach_identifiers(session, out)


async def get_subtree(session: AsyncSession, root_id: uuid.UUID, *, max_depth=3) -> Optional[dict]:
    root = await session.get(reg.TefcaRegEntity, root_id)
    if not root:
        return None

    async def build(node_id, depth):
        node = await session.get(reg.TefcaRegEntity, node_id)
        d = entity_summary(node)
        d["children"] = []
        if depth < max_depth:
            for child in await get_children(session, node_id):
                d["children"].append(await build(child["id"], depth + 1))
        else:
            d["has_more"] = await _child_count(session, node_id) > 0
        return d

    return await build(root_id, 0)


# ── search ────────────────────────────────────────────────────────────────────

async def search(session: AsyncSession, q: str, *, limit=25) -> dict:
    like = f"%{q}%"
    by_name = (await session.execute(
        select(reg.TefcaRegEntity).where(reg.TefcaRegEntity.name.ilike(like))
        .limit(limit))).scalars().all()
    # match by identifier value → entity
    ident_hits = (await session.execute(
        select(reg.TefcaRegEntity, reg.TefcaEntityIdentifier)
        .join(reg.TefcaEntityIdentifier,
              reg.TefcaEntityIdentifier.entity_id == reg.TefcaRegEntity.id)
        .where(reg.TefcaEntityIdentifier.identifier_value.ilike(like))
        .limit(limit))).all()
    seen = {}
    for e in by_name:
        seen[e.id] = {**entity_summary(e), "matched_on": "name"}
    for e, i in ident_hits:
        seen.setdefault(e.id, {**entity_summary(e),
                               "matched_on": f"identifier:{i.identifier_type}",
                               "matched_value": i.identifier_value})
    results = await _attach_identifiers(session, list(seen.values()))
    return {"query": q, "results": results, "count": len(results)}


# ── findings ──────────────────────────────────────────────────────────────────

async def list_findings(session: AsyncSession, *, finding_type=None, severity=None,
                        status=None, entity_id=None, limit=200, offset=0) -> dict:
    conds = []
    if finding_type:
        conds.append(reg.TefcaEntityFinding.finding_type == finding_type)
    if severity:
        conds.append(reg.TefcaEntityFinding.severity == severity)
    if status:
        conds.append(reg.TefcaEntityFinding.status == status)
    if entity_id:
        conds.append(reg.TefcaEntityFinding.entity_id == entity_id)
    base = select(reg.TefcaEntityFinding)
    if conds:
        base = base.where(*conds)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.execute(
        base.order_by(reg.TefcaEntityFinding.created_at.desc())
        .limit(limit).offset(offset))).scalars().all()
    return {"items": [finding_dict(f) for f in rows],
            "total": int(total or 0), "limit": limit, "offset": offset}


# ── stats ─────────────────────────────────────────────────────────────────────

async def stats(session: AsyncSession) -> dict:
    async def group_count(col):
        rows = (await session.execute(
            select(col, func.count()).group_by(col))).all()
        return {str(k): int(v) for k, v in rows}

    live = reg.TefcaRegEntity.is_deleted.is_(False)
    entities_total = int(await session.scalar(
        select(func.count()).select_from(reg.TefcaRegEntity).where(live)) or 0)
    deleted_total = int(await session.scalar(
        select(func.count()).select_from(reg.TefcaRegEntity)
        .where(reg.TefcaRegEntity.is_deleted.is_(True))) or 0)
    return {
        "entities_total": entities_total,
        # Surfaced rather than hidden: an operator comparing a row count against
        # an import batch needs to see where the difference went.
        "entities_deleted": deleted_total,
        "by_level": await group_count(reg.TefcaRegEntity.entity_level),
        "by_verification_status": await group_count(reg.TefcaRegEntity.verification_status),
        "by_operational_status": await group_count(reg.TefcaRegEntity.operational_status),
        "findings_total": int(await session.scalar(
            select(func.count()).select_from(reg.TefcaEntityFinding)) or 0),
        "findings_by_severity": await group_count(reg.TefcaEntityFinding.severity),
        "findings_by_type": await group_count(reg.TefcaEntityFinding.finding_type),
        "verification_jobs": int(await session.scalar(
            select(func.count()).select_from(reg.TefcaVerificationJob)) or 0),
    }
