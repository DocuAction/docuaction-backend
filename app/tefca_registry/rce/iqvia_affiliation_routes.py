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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_role
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

    @router.get("/coverage", summary="Truthful statement of whether IQVIA data was used")
    async def coverage(response: Response, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
        _gate(user)
        response.headers["Cache-Control"] = "no-store"
        rows = (await db.execute(select(sm.SourceSnapshot.id, sm.SourceSnapshot.source_system,
                                        sm.SourceSnapshot.status, sm.SourceSnapshot.record_count)
                                 .where(sm.SourceSnapshot.source_system.like("IQVIA%")))).all()
        snaps = [{"id": r[0], "source_system": r[1], "status": r[2], "record_count": r[3]} for r in rows]
        return ac.coverage_statement(flag_enabled=True, snapshots=snaps, consumed=False)
