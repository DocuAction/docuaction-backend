"""What a NEW delivery does to the registry's history — recorded, never
rewritten (September 2026 snapshot, docs/monthly_delivery_model.md).

Three effects, each append-only, each idempotent per (previous, current) pair:

1. `persist_delta`        — the per-`id` classification between the previous
                             accepted delivery and this one, written to
                             `rce_delivery_delta` so "what changed between July
                             and September" is one query, not a recomputation.
2. `record_presence`      — one `rce_entity_presence` row per entity per intake:
                             present (with the delivered `active` value) or
                             ABSENT (present in the previous delivery, missing
                             now). Absence is an observation about a FILE; the
                             entity is never deactivated or deleted by it.
3. `mark_stale`           — an `arc_stale_marks` row per ARC result (review
                             record) whose entity carried a MATERIAL change:
                             partOf / QHIN / active / purposesofuse / NPI. The
                             historical report that carried the result is not
                             touched; the mark says "re-evaluation required"
                             on every current view.

Non-material changes (contact phone, postal code, office hours, coordinates)
mark nothing: an approved rule that depends on them would add its own reason.

`assert_ids_unique` is the parse-time 1:1 guard the model document asked for.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from sqlalchemy import func, select

from app.core import request_context
from app.tefca_registry import models as reg
from app.tefca_registry.rce import delivery_delta as dd
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import snapshot_models as sm

logger = logging.getLogger(__name__)

#: Delivered field -> stale reason. ONLY material changes are here.
MATERIAL_FIELD_REASONS: Dict[str, str] = {
    "partOf": sm.STALE_PART_OF_CHANGED,
    "orgManagingOrg": sm.STALE_QHIN_CHANGED,
    "active": sm.STALE_ACTIVE_CHANGED,
    "purposesofuse": sm.STALE_PURPOSES_CHANGED,
    "NPI": sm.STALE_NPI_CHANGED,
    "CCN": sm.STALE_CCN_EVIDENCE_CHANGED,
}


class SnapshotRefused(RuntimeError):
    pass


def _cid() -> str:
    return request_context.correlation_id()[:64]


def _sha() -> str:
    return request_context.build_sha()


# ── 0. parse-time guard ──────────────────────────────────────────────────────

async def assert_ids_unique(db, intake_id) -> Dict[str, Any]:
    """`count(*) == count(distinct id)` on the intake. Raises SnapshotRefused
    when the primary matching key is not 1:1 — the delta must not be computed
    on a guess about which row is the entity."""
    total, distinct = (await db.execute(
        select(func.count(), func.count(func.distinct(m.RceSourceRecord.source_rce_id)))
        .where(m.RceSourceRecord.source_intake_id == intake_id,
               m.RceSourceRecord.source_rce_id.isnot(None)))).one()
    out = {"records_with_id": int(total or 0), "distinct_ids": int(distinct or 0),
           "unique": int(total or 0) == int(distinct or 0)}
    if not out["unique"]:
        dupes = (await db.execute(
            select(m.RceSourceRecord.source_rce_id, func.count())
            .where(m.RceSourceRecord.source_intake_id == intake_id)
            .group_by(m.RceSourceRecord.source_rce_id)
            .having(func.count() > 1).limit(20))).all()
        out["examples"] = [{"id": i, "rows": int(n)} for i, n in dupes]
        raise SnapshotRefused(
            f"source `id` is not 1:1 on intake {intake_id}: {out['records_with_id']} rows, "
            f"{out['distinct_ids']} distinct. The delta is not computed on a guess; resolve "
            f"the identity ambiguity first. Examples: {out['examples'][:5]}")
    return out


# ── 1. delta ─────────────────────────────────────────────────────────────────

def _is_material(changed_fields: Iterable[str]) -> bool:
    return any(f in MATERIAL_FIELD_REASONS for f in changed_fields)


async def persist_delta(db, current_intake_id, *, previous_intake_id=None,
                        commit: bool = True) -> Dict[str, Any]:
    """Compute (via `delivery_delta.compare_delivery`) and persist. Idempotent:
    a pair that already has rows is left alone and reported as `already`."""
    result = await dd.compare_delivery(db, current_intake_id,
                                       previous_intake_id=previous_intake_id,
                                       include_records=True)
    if not result.get("comparable"):
        return {"persisted": 0, "state": result.get("state"), "reason": result.get("reason"),
                "previous_intake_id": result.get("previous_intake_id")}
    prev_id = result["previous_intake_id"]
    existing = int((await db.execute(
        select(func.count()).select_from(sm.RceDeliveryDelta)
        .where(sm.RceDeliveryDelta.previous_intake_id == prev_id,
               sm.RceDeliveryDelta.current_intake_id == current_intake_id))).scalar() or 0)
    if existing:
        return {"persisted": 0, "already": existing, "state": "COMPARED",
                "previous_intake_id": prev_id, "counts": result["counts"]}
    rows = []
    cid, sha = _cid(), _sha()
    for r in result["records"]:
        rows.append({
            "previous_intake_id": prev_id, "current_intake_id": current_intake_id,
            "rce_org_oid": r["source_rce_id"], "classification": r["classification"],
            "changed_fields": r["changed_fields"], "field_changes": r["field_changes"],
            "material": _is_material(r["changed_fields"]),
            "previous_sha256": r["previous_sha256"], "current_sha256": r["current_sha256"],
            "current_source_record_id": r["source_record_id"],
            "delta_version": dd.DELTA_VERSION, "correlation_id": cid, "build_sha": sha,
        })
    import uuid as _uuid
    for chunk_start in range(0, len(rows), 2000):
        chunk = rows[chunk_start:chunk_start + 2000]
        for row in chunk:
            row["id"] = _uuid.uuid4()
        await db.execute(sm.RceDeliveryDelta.__table__.insert(), chunk)
    if commit:
        await db.commit()
    return {"persisted": len(rows), "state": "COMPARED", "previous_intake_id": prev_id,
            "counts": result["counts"],
            "material_changes": sum(1 for r in rows if r["material"])}


# ── 2. presence ──────────────────────────────────────────────────────────────

async def record_presence(db, current_intake_id, *, commit: bool = True) -> Dict[str, Any]:
    """Presence rows for this intake: every promoted/matched entity as PRESENT
    (with the delivered `active` value), and every entity that the previous
    delivery carried but this one does not as ABSENT. Idempotent per intake."""
    from app.tefca_registry.rce.field_map import normalize_active

    intake = await db.get(m.RceSourceIntake, current_intake_id)
    if intake is None:
        raise SnapshotRefused(f"No delivery {current_intake_id}")
    received = intake.received_at
    if received.tzinfo is None:
        from datetime import timezone
        received = received.replace(tzinfo=timezone.utc)

    existing = {str(e) for (e,) in (await db.execute(
        select(sm.RceEntityPresence.entity_id)
        .where(sm.RceEntityPresence.intake_id == current_intake_id))).all()}

    cid = _cid()
    present_rows: List[Dict[str, Any]] = []
    rows = (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id, m.RceCuratedRecord.rce_org_oid,
               m.RceCuratedRecord.source_record_id, m.RceSourceRecord.parsed)
        .join(m.RceSourceRecord, m.RceSourceRecord.id == m.RceCuratedRecord.source_record_id)
        .where(m.RceCuratedRecord.source_intake_id == current_intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None)))).all()
    import uuid as _uuid
    seen = set()
    for entity_id, oid, src_id, parsed in rows:
        if str(entity_id) in existing or str(entity_id) in seen:
            continue
        seen.add(str(entity_id))
        raw = ((parsed or {}).get("active") or "").strip()
        norm = normalize_active(raw)
        present_rows.append({
            "id": _uuid.uuid4(), "entity_id": entity_id, "intake_id": current_intake_id,
            "rce_org_oid": oid, "present": True, "active_raw": raw[:16] or None,
            "active_normalized": norm if norm in ("0", "1") else None,
            "source_record_id": src_id, "snapshot_received_at": received,
            "correlation_id": cid,
        })

    absent_rows: List[Dict[str, Any]] = []
    previous = await dd.previous_delivery(db, intake)
    if previous is not None:
        prev_entities = (await db.execute(
            select(m.RceCuratedRecord.canonical_entity_id, m.RceCuratedRecord.rce_org_oid)
            .where(m.RceCuratedRecord.source_intake_id == previous.id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None)))).all()
        present_ids = seen | existing
        for entity_id, oid in prev_entities:
            if str(entity_id) in present_ids:
                continue
            present_ids.add(str(entity_id))
            absent_rows.append({
                "id": _uuid.uuid4(), "entity_id": entity_id, "intake_id": current_intake_id,
                "rce_org_oid": oid, "present": False, "active_raw": None,
                "active_normalized": None, "source_record_id": None,
                "snapshot_received_at": received, "correlation_id": cid,
            })
    for chunk in (present_rows, absent_rows):
        for i in range(0, len(chunk), 2000):
            await db.execute(sm.RceEntityPresence.__table__.insert(), chunk[i:i + 2000])
    if commit:
        await db.commit()
    return {"present": len(present_rows), "absent": len(absent_rows),
            "already": len(existing), "previous_intake_id": str(previous.id) if previous else None}


# ── 3. staleness ─────────────────────────────────────────────────────────────

async def _open_marks(db, entity_ids: List[Any]) -> set:
    """(entity_id, review_id) pairs that already carry an unresolved STALE mark."""
    if not entity_ids:
        return set()
    resolved = select(sm.ArcStaleMark.resolves_mark_id).where(
        sm.ArcStaleMark.kind == "RESOLVED", sm.ArcStaleMark.resolves_mark_id.isnot(None))
    rows = (await db.execute(
        select(sm.ArcStaleMark.entity_id, sm.ArcStaleMark.review_id)
        .where(sm.ArcStaleMark.kind == "STALE",
               sm.ArcStaleMark.entity_id.in_(entity_ids),
               sm.ArcStaleMark.id.notin_(resolved)))).all()
    return {(str(e), r) for e, r in rows}


async def mark_stale(db, current_intake_id, *, actor: str = "SYSTEM",
                     commit: bool = True) -> Dict[str, Any]:
    """One STALE mark per (entity, ARC result) for every MATERIAL change the
    persisted delta recorded, plus ABSENT_FROM_DELIVERY for entities the
    previous delivery carried and this one does not. Never touches
    review_records, review_reports or report artifacts."""
    deltas = (await db.execute(
        select(sm.RceDeliveryDelta)
        .where(sm.RceDeliveryDelta.current_intake_id == current_intake_id,
               (sm.RceDeliveryDelta.material.is_(True))
               | (sm.RceDeliveryDelta.classification == sm.DELTA_NOT_PRESENT)))).scalars().all()
    if not deltas:
        return {"marked": 0, "entities": 0, "reason": "no material change persisted"}

    # entity per oid: current delivery's curated rows, else registry identifier.
    oids = [d.rce_org_oid for d in deltas]
    entity_by_oid: Dict[str, Any] = {}
    for i in range(0, len(oids), 5000):
        chunk = oids[i:i + 5000]
        for oid, eid in (await db.execute(
                select(reg.TefcaEntityIdentifier.identifier_value,
                       reg.TefcaEntityIdentifier.entity_id)
                .where(reg.TefcaEntityIdentifier.identifier_type == "rce_org_oid",
                       reg.TefcaEntityIdentifier.identifier_value.in_(chunk)))).all():
            entity_by_oid.setdefault(oid, eid)

    entity_ids = list({entity_by_oid[o] for o in oids if o in entity_by_oid})
    # ARC results per entity: review records with a determination or a report.
    results_by_entity: Dict[str, List[str]] = {}
    for i in range(0, len(entity_ids), 5000):
        chunk = entity_ids[i:i + 5000]
        for eid, rid in (await db.execute(
                select(reg.ReviewRecord.entity_id, reg.ReviewRecord.review_id)
                .where(reg.ReviewRecord.entity_id.in_(chunk)))).all():
            results_by_entity.setdefault(str(eid), []).append(rid)

    already = await _open_marks(db, entity_ids)
    cid, sha = _cid(), _sha()
    import uuid as _uuid
    marks: List[Dict[str, Any]] = []
    for d in deltas:
        eid = entity_by_oid.get(d.rce_org_oid)
        if eid is None:
            continue
        if d.classification == sm.DELTA_NOT_PRESENT:
            reasons = [sm.STALE_ABSENT_FROM_DELIVERY]
        else:
            reasons = sorted({MATERIAL_FIELD_REASONS[f] for f in (d.changed_fields or [])
                              if f in MATERIAL_FIELD_REASONS})
        targets = results_by_entity.get(str(eid)) or [None]
        for review_id in targets:
            if (str(eid), review_id) in already:
                continue
            already.add((str(eid), review_id))
            marks.append({
                "id": _uuid.uuid4(), "entity_id": eid, "review_id": review_id,
                "intake_id": current_intake_id, "kind": "STALE",
                "reason": reasons[0] if reasons else sm.STALE_MATCH_CHANGED,
                "changed_fields": list(d.changed_fields or []),
                "detail": {"reasons": reasons, "delta_id": str(d.id),
                           "classification": d.classification},
                "resolves_mark_id": None, "actor": actor, "correlation_id": cid,
                "build_sha": sha,
            })
    for i in range(0, len(marks), 2000):
        await db.execute(sm.ArcStaleMark.__table__.insert(), marks[i:i + 2000])
    if commit:
        await db.commit()
    return {"marked": len(marks), "entities": len(entity_ids),
            "with_results": sum(1 for e in entity_ids if str(e) in results_by_entity)}


async def resolve_stale(db, mark_id, *, actor: str, reason: str,
                        commit: bool = True) -> Dict[str, Any]:
    """Append a RESOLVED row naming the mark. The STALE row stays."""
    mark = await db.get(sm.ArcStaleMark, mark_id)
    if mark is None or mark.kind != "STALE":
        raise SnapshotRefused(f"no STALE mark {mark_id}")
    import uuid as _uuid
    row = sm.ArcStaleMark(
        id=_uuid.uuid4(), entity_id=mark.entity_id, review_id=mark.review_id,
        intake_id=mark.intake_id, kind="RESOLVED", reason=mark.reason,
        changed_fields=mark.changed_fields, detail={"resolution": reason},
        resolves_mark_id=mark.id, actor=actor, correlation_id=_cid(), build_sha=_sha())
    db.add(row)
    if commit:
        await db.commit()
    return {"resolved_mark_id": str(mark.id), "resolution_id": str(row.id)}


async def stale_for_entities(db, entity_ids: List[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Unresolved marks per entity id (string keys) — for current views."""
    if not entity_ids:
        return {}
    resolved = select(sm.ArcStaleMark.resolves_mark_id).where(
        sm.ArcStaleMark.kind == "RESOLVED", sm.ArcStaleMark.resolves_mark_id.isnot(None))
    rows = (await db.execute(
        select(sm.ArcStaleMark)
        .where(sm.ArcStaleMark.kind == "STALE", sm.ArcStaleMark.entity_id.in_(entity_ids),
               sm.ArcStaleMark.id.notin_(resolved))
        .order_by(sm.ArcStaleMark.marked_at.desc()))).scalars().all()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r.entity_id), []).append({
            "mark_id": str(r.id), "review_id": r.review_id, "reason": r.reason,
            "changed_fields": r.changed_fields, "intake_id": str(r.intake_id),
            "marked_at": r.marked_at.isoformat() if r.marked_at else None})
    return out


async def register_source_snapshot(db, intake_id, *, actor: str = "SYSTEM",
                                   commit: bool = True) -> Dict[str, Any]:
    """One `source_snapshot` row (ONC_RCE) per intake — the generic source
    ledger IQVIA snapshots will share. An ONC delivery is approved by the act
    of registering it (Data Operations role), so it lands APPROVED with the
    intake as its approval reference. Idempotent per intake."""
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise SnapshotRefused(f"No delivery {intake_id}")
    existing = (await db.execute(
        select(sm.SourceSnapshot).where(sm.SourceSnapshot.intake_id == intake_id)
        .limit(1))).scalars().first()
    if existing is not None:
        return {"snapshot_id": str(existing.id), "already": True, "status": existing.status}
    received = intake.received_at
    if received.tzinfo is None:
        from datetime import timezone
        received = received.replace(tzinfo=timezone.utc)
    previous = await dd.previous_delivery(db, intake)
    prev_snapshot = None
    if previous is not None:
        prev_snapshot = (await db.execute(
            select(sm.SourceSnapshot.id).where(sm.SourceSnapshot.intake_id == previous.id)
            .limit(1))).scalar_one_or_none()
    import uuid as _uuid
    row = sm.SourceSnapshot(
        id=_uuid.uuid4(), source_system=sm.SOURCE_ONC_RCE,
        snapshot_label=(intake.delivery_label or intake.original_filename or "")[:200],
        sha256=intake.sha256, record_count=int(intake.record_count or 0),
        received_at=received, intake_id=intake_id, status=sm.SNAPSHOT_APPROVED,
        approved_by=actor[:320], approved_at=received,
        approval_ref=f"rce_source_intake:{intake_id}",
        supersedes_snapshot_id=prev_snapshot,
        metadata_={"schema_fingerprint": intake.schema_fingerprint,
                   "delimiter": intake.delimiter, "encoding": intake.encoding},
        created_by=actor[:320], correlation_id=_cid())
    db.add(row)
    if commit:
        await db.commit()
    return {"snapshot_id": str(row.id), "already": False, "status": row.status,
            "supersedes_snapshot_id": str(prev_snapshot) if prev_snapshot else None}


async def apply_snapshot_effects(db, current_intake_id, *, actor: str = "SYSTEM") -> Dict[str, Any]:
    """Runner entry point after PROMOTION: guard, snapshot row, delta,
    presence, staleness. Every step is idempotent per intake."""
    guard = await assert_ids_unique(db, current_intake_id)
    snapshot = await register_source_snapshot(db, current_intake_id, actor=actor)
    delta = await persist_delta(db, current_intake_id)
    presence = await record_presence(db, current_intake_id)
    stale = await mark_stale(db, current_intake_id, actor=actor)
    return {"completed": True, "id_guard": guard, "source_snapshot": snapshot,
            "delta": delta, "presence": presence, "stale": stale}
