"""GET /api/tefca/rce/records/search
GET /api/tefca/rce/records/by-hcid/{hcid}/history
GET /api/tefca/rce/records/compare

Month-to-month record investigation surface: search by NPI/HCID/TEFCAID/
organisation name/QHIN, delivery history by HCID, and field-by-field
comparison between two selected deliveries. Extends the existing TEFCA
Entity Detail / review-case / issue-history surface. READ ONLY: this
module defines only GETs, so every other method 405s from the router
itself and no write path exists by absence -- the same pattern
`issue_history_routes.py` already uses.

Role floor `viewer`, same as every other TEFCA read in this codebase.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_HIERARCHY, require_role, role_level

router = APIRouter(prefix="/api/tefca/rce", tags=["TEFCA Record History"])

NOT_FOUND = "NOT_FOUND"


def _reviewer_or_above(user) -> bool:
    return role_level(getattr(user, "role", None)) >= ROLE_HIERARCHY["reviewer"]


@router.get("/records/search",
            summary="Read-only search by NPI, HCID, TEFCAID, organisation name "
                    "or QHIN. Multiple results when an identifier maps to "
                    "multiple directory entries.")
async def search_records_route(
    npi: Optional[str] = Query(None), hcid: Optional[str] = Query(None),
    tefcaid: Optional[str] = Query(None), name: Optional[str] = Query(None),
    qhin_entity_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.core.config import settings
    from app.tefca_registry.rce import record_history_comparison as svc

    if not any([npi, hcid, tefcaid, name]):
        raise HTTPException(422, "at least one of npi, hcid, tefcaid, name is required")
    try:
        return await svc.search_records(
            db, npi=npi, hcid=hcid, tefcaid=tefcaid, name=name,
            qhin_entity_id=qhin_entity_id, limit=limit,
            reviewer_or_above=_reviewer_or_above(user), settings=settings,
            allowed_modules=getattr(user, "allowed_modules", None))
    except svc.RecordHistoryNotFound:
        return {"results": [], "count": 0}


@router.get("/records/by-hcid/{hcid}/history",
            summary="Delivery history for one HCID across every allowed "
                    "delivery. A month with no delivery for this HCID is "
                    "simply absent -- never interpolated.")
async def hcid_history_route(
    hcid: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.core.config import settings
    from app.tefca_registry.rce import record_history_comparison as svc

    try:
        return await svc.hcid_delivery_history(
            db, hcid=hcid, reviewer_or_above=_reviewer_or_above(user),
            settings=settings, allowed_modules=getattr(user, "allowed_modules", None))
    except svc.RecordHistoryNotFound:
        raise HTTPException(404, NOT_FOUND)


@router.get("/records/compare",
            summary="Field-by-field comparison between two specific delivered "
                    "records (e.g. one from an earlier delivery, one from a "
                    "later one), selected from a prior /by-hcid/history call.")
async def compare_records_route(
    earlier_record_id: uuid.UUID = Query(...),
    later_record_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import record_history_comparison as svc

    try:
        return await svc.compare_two_records(
            db, earlier_record_id=earlier_record_id, later_record_id=later_record_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
