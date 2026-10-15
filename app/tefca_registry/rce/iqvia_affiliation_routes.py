"""Read-only, advisory HTTP surface for `iqvia_affiliation_consumer`.

The router is always importable but carries NO routes unless
`ENABLE_IQVIA_AFFILIATION_CONSUMPTION` is true at import time (the pattern
`admin_restore_routes` uses), so by default the endpoints do not exist in the
OpenAPI schema. Identifiers travel in POST bodies, never in URLs or query
strings, so licensed values do not reach access logs. Every route also needs
`ENABLE_IQVIA_SOURCES` and the reviewer role floor (`licensed_access_allowed`).
Nothing here writes to the database.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_role
from app.tefca_registry.rce import iqvia_advisory_assessment as aa
from app.tefca_registry.rce import iqvia_affiliation_consumer as ac
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import source_matching as sx

router = APIRouter(prefix="/api/tefca/rce/iqvia/affiliation", tags=["IQVIA Release 1 (advisory)"])


def _gate(user) -> None:
    decision = sx.licensed_access_allowed(user)
    if not decision["allowed"]:
        raise HTTPException(403, detail={"error": decision["reason"], "availability": decision["availability"]})


if getattr(settings, ac.FLAG, False):

    class ResolveOrgRequest(BaseModel):
        org_npi: Optional[str] = Field(None, max_length=32)
        org_ccn: Optional[str] = Field(None, max_length=32)

    class RelationshipsRequest(BaseModel):
        snapshot_id: uuid.UUID
        hcp_record_key: str = Field(..., min_length=1, max_length=64)
        limit: int = Field(200, ge=1, le=500)

    @router.post("/resolve-organisation", summary="Advisory: registry entity CANDIDATES for an organisation identifier pair")
    async def resolve_organisation(body: ResolveOrgRequest, response: Response,
                                   db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
        _gate(user)
        response.headers["Cache-Control"] = "no-store"
        return (await ac.resolve_organisation(db, org_npi=body.org_npi, org_ccn=body.org_ccn)).as_dict()

    @router.post("/relationships", summary="Advisory: affiliation relationships for a staged HCP key (approved snapshot only)")
    async def relationships(body: RelationshipsRequest, response: Response,
                            db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
        _gate(user)
        response.headers["Cache-Control"] = "no-store"
        try:
            return await ac.relationships_for_hcp(db, snapshot_id=body.snapshot_id,
                                                  hcp_record_key=body.hcp_record_key, limit=body.limit)
        except ac.SnapshotNotEligible as exc:
            raise HTTPException(409, detail={"code": "SNAPSHOT_NOT_ELIGIBLE", "error": str(exc)}) from exc

    class OrganisationRequest(BaseModel):
        snapshot_id: uuid.UUID
        org_npi: Optional[str] = Field(None, max_length=32)
        org_ccn: Optional[str] = Field(None, max_length=32)
        hco_record_key: Optional[str] = Field(None, max_length=64)
        limit: int = Field(100, ge=1, le=10_000)
        cursor: Optional[dict] = None

    @router.post("/organisation-relationships",
                 summary="Advisory: organisation-first lookup with conflicts and explicit pagination (approved snapshot only)")
    async def organisation_relationships(body: OrganisationRequest, response: Response,
                                         db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
        _gate(user)
        response.headers["Cache-Control"] = "no-store"
        if body.cursor is not None and not {"hco", "hcp", "type"} <= set(body.cursor):
            raise HTTPException(422, detail={"code": "BAD_CURSOR", "error": "cursor must carry hco, hcp and type"})
        try:
            return await ac.organisation_relationships(
                db, snapshot_id=body.snapshot_id, org_npi=body.org_npi, org_ccn=body.org_ccn,
                hco_record_key=body.hco_record_key, limit=body.limit, cursor=body.cursor)
        except ac.SnapshotNotEligible as exc:
            raise HTTPException(409, detail={"code": "SNAPSHOT_NOT_ELIGIBLE", "error": str(exc)}) from exc

    @router.get("/coverage", summary="Truthful statement of whether IQVIA data was used")
    async def coverage(response: Response, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
        _gate(user)
        response.headers["Cache-Control"] = "no-store"
        rows = (await db.execute(select(sm.SourceSnapshot.id, sm.SourceSnapshot.source_system,
                                        sm.SourceSnapshot.status, sm.SourceSnapshot.record_count)
                                 .where(sm.SourceSnapshot.source_system.like("IQVIA%")))).all()
        snaps = [{"id": r[0], "source_system": r[1], "status": r[2], "record_count": r[3]} for r in rows]
        # "consumed" is a stored fact (an assessment row exists), not a flag reading.
        # The table exists only after the draft migration is applied; an absent or unreadable store is reported
        # as such, never counted (that would be a 500) and never read as "no use".
        store = await aa._store_state(db)
        used = store == "READABLE" and (await db.execute(
            select(func.count()).select_from(aa.IqviaAdvisoryAssessment))).scalar_one() > 0
        out = ac.coverage_statement(flag_enabled=True, snapshots=snaps, consumed=used)
        out["assessment_store"] = store
        if store != "READABLE":
            out["statement"] += (f" The assessment store is {store.replace('_', ' ').lower()}, so recorded use "
                                 "cannot be confirmed from here.")
        return out

    class AssessmentRequest(BaseModel):
        snapshot_id: uuid.UUID
        org_npi: Optional[str] = Field(None, max_length=32)
        org_ccn: Optional[str] = Field(None, max_length=32)
        hco_record_key: Optional[str] = Field(None, max_length=64)
        decision: str = Field(..., max_length=40)
        rationale: str = Field(..., min_length=10, max_length=2000)
        entity_id: Optional[uuid.UUID] = None
        delivery_intake_id: uuid.UUID  # required; validated server-side against rce_source_intakes
        review_id: Optional[str] = Field(None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")  # optional review link

    @router.post("/assessments", status_code=201,
                 summary="Advisory: record an analyst assessment of one organisation lookup (append-only evidence)")
    async def create_assessment(body: AssessmentRequest, response: Response,
                                db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
        """The lookup is re-run server-side; a decision may not claim more than it supports. Never changes a
        bucket, a report row or a verification outcome."""
        from app.core import request_context
        from app.tefca_registry.rce.iqvia_routes import _audit
        _gate(user)
        response.headers["Cache-Control"] = "no-store"
        try:
            row = await aa.record_assessment(
                db, user=user, secret=settings.SECRET_KEY, correlation_id=request_context.correlation_id(),
                build_sha=request_context.build_sha(), snapshot_id=body.snapshot_id, org_npi=body.org_npi,
                org_ccn=body.org_ccn, hco_record_key=body.hco_record_key, decision=body.decision,
                rationale=body.rationale, entity_id=body.entity_id, delivery_intake_id=body.delivery_intake_id,
                review_id=body.review_id)
        except ac.SnapshotNotEligible as exc:
            raise HTTPException(409, detail={"code": "SNAPSHOT_NOT_ELIGIBLE", "error": str(exc)}) from exc
        except aa.AssessmentRefused as exc:
            raise HTTPException(422, detail={"code": exc.code, "error": str(exc)}) from exc
        await db.flush()
        await _audit(db, user, action="IQVIA_ADVISORY_ASSESSMENT", outcome="success",
                     resource_type="iqvia_advisory_assessment", resource_id=row.id,
                     details={"snapshot_id": str(row.snapshot_id), "decision": row.decision}, commit=False)
        await db.commit()
        return aa.redact_for_role(aa._row_dict(row), user)

    @router.get("/assessments", summary="Advisory: list recorded assessments (role-redacted)")
    async def list_assessments(response: Response, snapshot_id: Optional[uuid.UUID] = None,
                               delivery_intake_id: Optional[uuid.UUID] = None, review_id: Optional[str] = None,
                               limit: int = 50, offset: int = 0,
                               db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
        _gate(user)
        response.headers["Cache-Control"] = "no-store"
        rows = await aa.list_assessments(db, snapshot_id=snapshot_id, delivery_intake_id=delivery_intake_id,
                                         review_id=review_id,
                                         limit=limit, offset=offset)
        return {"limit": max(1, min(limit, 200)), "offset": max(0, offset), "returned": len(rows),
                "assessments": [aa.redact_for_role(r, user) for r in rows]}
