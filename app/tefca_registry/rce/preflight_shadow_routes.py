"""Routes for preflight, shadow reassessment and the analyst workspace.

Mounted under the same `/api/tefca/rce` prefix as the delivery surface. No
route here mutates Area 1, `rce_issues`, `tefca_dimension_evidence` or an
existing `review_records` row: preflight and shadow comparison write only
their own append-only tables; the one write into `review_records` is a NEW
successor row, and only in SHADOW_PUBLICATION_MODE=local_test.

FLOORS
    preflight run / read, workspace read      reviewer   (delivered values)
    shadow comparison build                    reviewer
    shadow comparison read / deltas            viewer     (buckets and rules, no delivered values)
    shadow ANALYST approval                    reviewer
    shadow INDEPENDENT_QA approval             qalead     (checked in-route; the
                                                          route floor is reviewer
                                                          so the refusal names the role)
    successor publication                      qalead
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, role_at_least
from app.tefca_registry.rce import analyst_workspace as ws
from app.tefca_registry.rce import preflight as pf
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import shadow_reassessment as shadow
from app.tefca_registry.rce.exception_ledger import as_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/rce", tags=["TEFCA RCE Preflight / Shadow / Workspace"])

EVIDENCE_ROLE = "reviewer"


def _uuid_or_422(value: str, name: str):
    out = as_uuid(value)
    if out is None:
        raise HTTPException(422, f"{name} must be a uuid")
    return out


def _actor(user) -> str:
    return getattr(user, "email", None) or str(getattr(user, "id", "")) or "unknown"


# ── preflight ────────────────────────────────────────────────────────────────

@router.post("/deliveries/{intake_id}/preflight", status_code=201,
             summary="Run preflight over a delivery (schema, identifiers, conditional "
                     "blanks, missing context) before final classification")
async def run_preflight_route(intake_id: str, db: AsyncSession = Depends(get_db),
                              user=Depends(require_role(EVIDENCE_ROLE))):
    iid = _uuid_or_422(intake_id, "intake_id")
    try:
        return await pf.run_preflight(db, iid, actor=_actor(user))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/deliveries/{intake_id}/preflight",
            summary="The latest preflight run for a delivery")
async def latest_preflight_route(intake_id: str, db: AsyncSession = Depends(get_db),
                                 user=Depends(require_role(EVIDENCE_ROLE))):
    iid = _uuid_or_422(intake_id, "intake_id")
    run = await pf.latest_run(db, iid)
    if run is None:
        raise HTTPException(404, "no preflight run exists for this delivery")
    return pf.run_dto(run)


@router.get("/preflight-runs/{run_id}/findings",
            summary="Preflight findings, four dimensions each, paginated")
async def preflight_findings_route(run_id: str, category: Optional[str] = None,
                                   disposition: Optional[str] = None,
                                   limit: int = Query(100, ge=1, le=1000),
                                   offset: int = Query(0, ge=0),
                                   db: AsyncSession = Depends(get_db),
                                   user=Depends(require_role(EVIDENCE_ROLE))):
    rid = _uuid_or_422(run_id, "run_id")
    if await db.get(pm.RcePreflightRun, rid) is None:
        raise HTTPException(404, "no such preflight run")
    try:
        return await pf.list_findings(db, rid, category=category, disposition=disposition,
                                      limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/preflight-runs/{run_id}/normalizations",
            summary="Derived normalizations recorded beside their originals")
async def preflight_normalizations_route(run_id: str,
                                         limit: int = Query(100, ge=1, le=1000),
                                         offset: int = Query(0, ge=0),
                                         db: AsyncSession = Depends(get_db),
                                         user=Depends(require_role(EVIDENCE_ROLE))):
    rid = _uuid_or_422(run_id, "run_id")
    if await db.get(pm.RcePreflightRun, rid) is None:
        raise HTTPException(404, "no such preflight run")
    return await pf.list_normalizations(db, rid, limit=limit, offset=offset)


# ── shadow reassessment ──────────────────────────────────────────────────────

class ShadowComparisonRequest(BaseModel):
    intake_id: str
    baseline_rule_version: Optional[int] = None
    candidate_rule_version: Optional[int] = None
    candidate_rules: Optional[List[Dict[str, Any]]] = None
    evaluation_date: Optional[date] = None


class ShadowApprovalRequest(BaseModel):
    approval_role: str = Field(..., description="ANALYST or INDEPENDENT_QA")
    package_hash: str = Field(..., min_length=64, max_length=64)
    rationale: str = Field(..., min_length=1)


@router.post("/shadow/comparisons", status_code=201,
             summary="Build a pinned shadow comparison (classifier only, over persisted evidence)")
async def build_shadow_comparison_route(req: ShadowComparisonRequest,
                                        db: AsyncSession = Depends(get_db),
                                        user=Depends(require_role(EVIDENCE_ROLE))):
    iid = _uuid_or_422(req.intake_id, "intake_id")
    try:
        return await shadow.build_comparison(
            db, iid, built_by=_actor(user),
            baseline_rule_version=req.baseline_rule_version,
            candidate_rule_version=req.candidate_rule_version,
            candidate_rules=req.candidate_rules, evaluation_date=req.evaluation_date)
    except shadow.ShadowRefused as exc:
        raise HTTPException(409, str(exc))


@router.get("/shadow/comparisons/{comparison_id}",
            summary="A shadow comparison: pins, hashes, summary, approvals, events")
async def get_shadow_comparison_route(comparison_id: str, db: AsyncSession = Depends(get_db),
                                      user=Depends(require_role("viewer"))):
    cid = _uuid_or_422(comparison_id, "comparison_id")
    row = await db.get(pm.RceShadowComparison, cid)
    if row is None:
        raise HTTPException(404, "no such shadow comparison")
    return await shadow.comparison_dto(db, row)


@router.get("/shadow/comparisons/{comparison_id}/deltas",
            summary="Per-entity NEW / REMOVED / CHANGED / UNCHANGED deltas with direction")
async def list_shadow_deltas_route(comparison_id: str, kind: Optional[str] = None,
                                   direction: Optional[str] = None,
                                   limit: int = Query(100, ge=1, le=1000),
                                   offset: int = Query(0, ge=0),
                                   db: AsyncSession = Depends(get_db),
                                   user=Depends(require_role("viewer"))):
    cid = _uuid_or_422(comparison_id, "comparison_id")
    try:
        return await shadow.list_deltas(db, cid, kind=kind, direction=direction,
                                        limit=limit, offset=offset)
    except shadow.ShadowRefused as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/shadow/comparisons/{comparison_id}/approvals", status_code=201,
             summary="Analyst or independent-QA approval, bound to the exact package hash")
async def approve_shadow_comparison_route(comparison_id: str, req: ShadowApprovalRequest,
                                          request: Request,
                                          db: AsyncSession = Depends(get_db),
                                          user=Depends(require_role(EVIDENCE_ROLE))):
    cid = _uuid_or_422(comparison_id, "comparison_id")
    if req.approval_role == pm.APPROVAL_INDEPENDENT_QA and not role_at_least(user, "qalead"):
        raise HTTPException(403, "Required: qalead for INDEPENDENT_QA approval, "
                                 f"Current: {getattr(user, 'role', None)}")
    try:
        return await shadow.record_approval(
            db, cid, approval_role=req.approval_role, user=user,
            package_hash=req.package_hash, rationale=req.rationale,
            ip_address=request.client.host if request.client else None)
    except shadow.ShadowRefused as exc:
        raise HTTPException(409, str(exc))


@router.post("/shadow/comparisons/{comparison_id}/publish", status_code=201,
             summary="Publish successor review records — local test mode only")
async def publish_shadow_successors_route(comparison_id: str, request: Request,
                                          db: AsyncSession = Depends(get_db),
                                          user=Depends(require_role("qalead"))):
    cid = _uuid_or_422(comparison_id, "comparison_id")
    try:
        return await shadow.publish_successors(
            db, cid, user=user, ip_address=request.client.host if request.client else None)
    except shadow.ShadowRefused as exc:
        raise HTTPException(409, str(exc))


# ── source policy registry (read-only) ───────────────────────────────────────

@router.get("/source-policies",
            summary="Versioned source/check policies: official (unapproved) and "
                    "proposed (inactive) views. Read-only.")
async def source_policies_route(user=Depends(require_role("viewer"))):
    """No delivered value and no entity data: policy metadata only. There is
    deliberately no write route -- approving a policy is a recorded human
    decision outside this code's authorization."""
    from app.Tefca import source_policy
    return source_policy.registry_dto()


# ── controlled rechecks ──────────────────────────────────────────────────────
#
# Request: reviewer. Approve / run / resume: qalead, and never the requester.
# Every route refuses unless ENABLE_CONTROLLED_RECHECKS is on. No scheduler
# runs a recheck: a batch runs only when this route is called.

class RecheckRequest(BaseModel):
    trigger_kind: str
    source_id: str
    trigger_ref: str = Field(..., max_length=255)
    rationale: str
    max_entities: int = Field(default=2000, ge=1, le=2000)
    batch_size: int = Field(default=50, ge=1, le=200)


def _rechecks_enabled_or_409():
    from app.core.config import settings
    if not bool(getattr(settings, "ENABLE_CONTROLLED_RECHECKS", False)):
        raise HTTPException(409, "controlled rechecks are disabled "
                                 "(ENABLE_CONTROLLED_RECHECKS is off)")


@router.post("/deliveries/{intake_id}/rechecks", status_code=201,
             summary="Request a bounded recheck (PENDING_APPROVAL; looks nothing up)")
async def request_recheck_route(intake_id: str, req: RecheckRequest,
                                db: AsyncSession = Depends(get_db),
                                user=Depends(require_role(EVIDENCE_ROLE))):
    from app.tefca_registry.rce import rechecks
    _rechecks_enabled_or_409()
    iid = _uuid_or_422(intake_id, "intake_id")
    try:
        return await rechecks.request_recheck(
            db, iid, trigger_kind=req.trigger_kind, source_id=req.source_id,
            trigger_ref=req.trigger_ref, rationale=req.rationale, user=user,
            max_entities=req.max_entities, batch_size=req.batch_size)
    except rechecks.RecheckRefused as exc:
        raise HTTPException(409, str(exc))


@router.get("/deliveries/{intake_id}/verification-completeness",
            summary="Entity status for a delivery with `verified` split by whether "
                    "every applicable check answered. Read-only.")
async def delivery_completeness_route(intake_id: str, db: AsyncSession = Depends(get_db),
                                      user=Depends(require_role("viewer"))):
    from app.tefca_registry.rce import verification_completeness as vcomp
    return await vcomp.delivery_completeness(db, _uuid_or_422(intake_id, "intake_id"))


@router.get("/deliveries/{intake_id}/rechecks",
            summary="Recheck jobs for a delivery, and whether rechecks are enabled")
async def list_rechecks_route(intake_id: str, db: AsyncSession = Depends(get_db),
                              user=Depends(require_role("viewer"))):
    from app.tefca_registry.rce import rechecks
    return await rechecks.list_jobs(db, _uuid_or_422(intake_id, "intake_id"))


@router.post("/rechecks/{job_id}/approve",
             summary="Independent approval of a recheck (never the requester)")
async def approve_recheck_route(job_id: str, db: AsyncSession = Depends(get_db),
                                user=Depends(require_role("qalead"))):
    from app.tefca_registry.rce import rechecks
    _rechecks_enabled_or_409()
    try:
        return await rechecks.approve_recheck(db, _uuid_or_422(job_id, "job_id"), user=user)
    except rechecks.RecheckRefused as exc:
        raise HTTPException(409, str(exc))


@router.post("/rechecks/{job_id}/run-batch",
             summary="Run ONE bounded batch of an approved recheck")
async def run_recheck_batch_route(job_id: str, db: AsyncSession = Depends(get_db),
                                  user=Depends(require_role("qalead"))):
    from app.tefca_registry.rce import recheck_models as rm
    from app.tefca_registry.rce import rechecks
    _rechecks_enabled_or_409()
    jid = _uuid_or_422(job_id, "job_id")
    try:
        job = await rechecks._job_or_refuse(db, jid)
        if job.state == rm.STATE_QUEUED and await rechecks.claim(db, jid) is None:
            raise rechecks.RecheckRefused("the job could not be claimed (another worker "
                                          "holds it, or its attempts are exhausted)")
        return await rechecks.run_batch(db, jid)
    except rechecks.RecheckRefused as exc:
        raise HTTPException(409, str(exc))


@router.post("/rechecks/{job_id}/resume",
             summary="Requeue a recheck the circuit breaker stopped (bounded attempts)")
async def resume_recheck_route(job_id: str, db: AsyncSession = Depends(get_db),
                               user=Depends(require_role("qalead"))):
    from app.tefca_registry.rce import rechecks
    _rechecks_enabled_or_409()
    try:
        return await rechecks.resume_stopped(db, _uuid_or_422(job_id, "job_id"), user=user)
    except rechecks.RecheckRefused as exc:
        raise HTTPException(409, str(exc))


@router.get("/rechecks/{job_id}", summary="Recheck job status and pinned versions")
async def get_recheck_route(job_id: str, db: AsyncSession = Depends(get_db),
                            user=Depends(require_role("viewer"))):
    from app.tefca_registry.rce import rechecks
    try:
        return rechecks.job_dto(await rechecks._job_or_refuse(db, _uuid_or_422(job_id, "job_id")))
    except rechecks.RecheckRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/rechecks/{job_id}/items", summary="Recheck drill-down, one row per entity")
async def recheck_items_route(job_id: str, limit: int = Query(100, ge=1, le=500),
                              offset: int = Query(0, ge=0),
                              db: AsyncSession = Depends(get_db),
                              user=Depends(require_role(EVIDENCE_ROLE))):
    from app.tefca_registry.rce import rechecks
    try:
        return await rechecks.list_items(db, _uuid_or_422(job_id, "job_id"),
                                         limit=limit, offset=offset)
    except rechecks.RecheckRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/rechecks/{job_id}/items.csv", summary="Recheck drill-down as CSV")
async def recheck_items_csv_route(job_id: str, db: AsyncSession = Depends(get_db),
                                  user=Depends(require_role(EVIDENCE_ROLE))):
    from fastapi.responses import Response
    from app.tefca_registry.rce import rechecks
    try:
        body = await rechecks.items_csv(db, _uuid_or_422(job_id, "job_id"))
    except rechecks.RecheckRefused as exc:
        raise HTTPException(404, str(exc))
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="recheck-{job_id}.csv"'})


# ── analyst workspace ────────────────────────────────────────────────────────

@router.get("/deliveries/{intake_id}/workspace",
            summary="Consolidated analyst workspace: findings, evidence freshness, "
                    "governing requirements, unanswered questions, context")
async def delivery_workspace_route(intake_id: str, entity_id: Optional[str] = None,
                                   limit: int = Query(50, ge=1, le=500),
                                   offset: int = Query(0, ge=0),
                                   db: AsyncSession = Depends(get_db),
                                   user=Depends(require_role(EVIDENCE_ROLE))):
    iid = _uuid_or_422(intake_id, "intake_id")
    eid = _uuid_or_422(entity_id, "entity_id") if entity_id else None
    try:
        return await ws.delivery_workspace(db, iid, entity_id=eid, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
