"""
TEFCA registry API (Phase 2A) — read/query + verification.

Mounted at ``/api/tefca/registry/*``, separate from the legacy ``/api/tefca/*``
and ``/api/v1/tefca/*`` routers. Router-gated with ``require_role("reviewer")``.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.tefca_registry import models as reg
from app.tefca_registry import queries as q
from app.tefca_registry import verification as v
from app.tefca_registry.schemas import BulkVerifyRequest, VerifyOptions

router = APIRouter(
    prefix="/api/tefca/registry",
    tags=["TEFCA Registry"],
    dependencies=[Depends(require_role("reviewer"))],
)


# ── reads ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    return await q.stats(db)


@router.get("/entities")
async def list_entities(
    entity_level: Optional[str] = None,
    entity_type: Optional[str] = None,
    state: Optional[str] = None,
    verification_status: Optional[str] = None,
    operational_status: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = Query(None, alias="q"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await q.list_entities(
        db, entity_level=entity_level, entity_type=entity_type, state=state,
        verification_status=verification_status, operational_status=operational_status,
        is_active=is_active, q=search, limit=limit, offset=offset)


@router.get("/qhins")
async def list_qhins(db: AsyncSession = Depends(get_db)):
    return {"items": await q.list_qhins(db)}


@router.get("/participants")
async def list_participants(
    qhin_id: Optional[uuid.UUID] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await q.list_participants(db, qhin_id=qhin_id, limit=limit, offset=offset)


@router.get("/hierarchy")
async def hierarchy_roots(db: AsyncSession = Depends(get_db)):
    """Lazy tree root: QHINs with participant counts."""
    return {"roots": await q.hierarchy_roots(db)}


@router.get("/search")
async def search(q_: str = Query(..., alias="q", min_length=1),
                 limit: int = Query(25, ge=1, le=100),
                 db: AsyncSession = Depends(get_db)):
    return await q.search(db, q_, limit=limit)


@router.get("/findings")
async def list_findings(
    finding_type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await q.list_findings(
        db, finding_type=finding_type, severity=severity, status=status,
        entity_id=entity_id, limit=limit, offset=offset)


@router.get("/verification-jobs")
async def list_jobs(limit: int = Query(100, ge=1, le=500),
                    db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    rows = (await db.execute(
        select(reg.TefcaVerificationJob)
        .order_by(reg.TefcaVerificationJob.completed_at.desc().nullslast())
        .limit(limit))).scalars().all()
    return {"items": [{
        "id": j.id, "entity_id": j.entity_id, "status": j.status,
        "trigger_type": j.trigger_type, "started_at": j.started_at,
        "completed_at": j.completed_at, "duration_ms": j.duration_ms,
        "summary": j.summary,
    } for j in rows]}


@router.get("/verification-jobs/{job_id}")
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    job = await db.get(reg.TefcaVerificationJob, job_id)
    if not job:
        raise HTTPException(404, "Verification job not found")
    checks = (await db.execute(
        select(reg.TefcaVerificationCheck)
        .where(reg.TefcaVerificationCheck.job_id == job_id))).scalars().all()
    return {
        "id": job.id, "entity_id": job.entity_id, "status": job.status,
        "trigger_type": job.trigger_type, "started_at": job.started_at,
        "completed_at": job.completed_at, "duration_ms": job.duration_ms,
        "summary": job.summary,
        "checks": [{
            "id": c.id, "source": c.source, "result": c.result,
            "identifier_type": c.identifier_type, "discrepancies": c.discrepancies,
            "checked_at": c.checked_at,
        } for c in checks],
    }


# NOTE: keep the parameterized /entities/{id} routes AFTER the static ones above
# so /entities/... literals aren't shadowed.

@router.get("/entities/{entity_id}")
async def get_entity(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    detail = await q.get_entity_detail(db, entity_id)
    if not detail:
        raise HTTPException(404, "Entity not found")
    return detail


@router.get("/entities/{entity_id}/children")
async def get_children(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return {"children": await q.get_children(db, entity_id)}


@router.get("/entities/{entity_id}/hierarchy")
async def get_subtree(entity_id: uuid.UUID,
                      max_depth: int = Query(3, ge=1, le=6),
                      db: AsyncSession = Depends(get_db)):
    tree = await q.get_subtree(db, entity_id, max_depth=max_depth)
    if not tree:
        raise HTTPException(404, "Entity not found")
    return tree


@router.get("/entities/{entity_id}/findings")
async def entity_findings(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await q.list_findings(db, entity_id=entity_id)


# ── verification (writes) ─────────────────────────────────────────────────────

@router.post("/entities/{entity_id}/verify")
async def verify_entity(entity_id: uuid.UUID,
                        opts: VerifyOptions = VerifyOptions(),
                        db: AsyncSession = Depends(get_db),
                        user=Depends(require_role("reviewer"))):
    if not await db.get(reg.TefcaRegEntity, entity_id):
        raise HTTPException(404, "Entity not found")
    return await v.verify_one(
        db, entity_id, include_external=opts.include_external,
        trigger_type=opts.trigger_type,
        actor_id=getattr(user, "id", None), actor_email=getattr(user, "email", None))


@router.post("/verify")
async def verify_bulk(req: BulkVerifyRequest = BulkVerifyRequest(),
                      db: AsyncSession = Depends(get_db),
                      user=Depends(require_role("senior_analyst"))):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    stmt = select(reg.TefcaRegEntity.id)
    if req.entity_level:
        stmt = stmt.where(reg.TefcaRegEntity.entity_level == req.entity_level)
    ids = (await db.execute(stmt.limit(req.limit))).scalars().all()
    return await v.verify_entities(
        db, ids, include_external=req.include_external, trigger_type=req.trigger_type,
        actor_id=getattr(user, "id", None), actor_email=getattr(user, "email", None))
