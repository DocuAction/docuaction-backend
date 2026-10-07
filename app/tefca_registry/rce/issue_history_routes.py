"""GET /api/tefca/rce/entities/by-oid/{oid}/issue-history

Cross-delivery issue history for one entity OID (minimum slice: NPI and
partOf/QHIN rules). READ ONLY: this module defines exactly one route, a GET, so
every other method answers 405 from the router itself and no write path exists
by absence.

  * role floor `viewer`; the feed scope (not the role) decides which deliveries
    are visible, and an empty scope shows nothing;
  * flag `ENABLE_ISSUE_HISTORY` (default off) -> 404, identical to an unknown OID;
  * the response is built from an allowlist per audience: a viewer gets structure
    (delivery, dates, states, reasons, rule id/version, severity, field NAME,
    finding type, decision, QA role and timestamp); reviewer and above also get
    submitted/prior values, rationale and actor identities;
  * every read writes ONE audit row: user, role, OID, number of visible
    deliveries. Never values.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_HIERARCHY, canonical_role, require_role, role_level

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/rce", tags=["TEFCA RCE Issue History"])

NOT_FOUND = "NOT_FOUND"


def _not_found() -> HTTPException:
    # One body for every cause: flag off, no allowed feed, hidden OID, unknown OID.
    return HTTPException(404, NOT_FOUND)


async def _audit_read(db, user, request, role: str, oid: str, visible: int) -> None:
    """One audit row per read. Never raises; never records a value."""
    try:
        from app.core.client_ip import get_client_ip
        from app.tefca_registry import audit as reg_audit
        from app.tefca_registry.rce.issue_history import AUDIT_ACTION

        actor_id, actor_email = reg_audit.actor_of(user)
        reg_audit.record(db, AUDIT_ACTION, actor_id=actor_id,
                         actor_email=actor_email, ip_address=get_client_ip(request),
                         metadata={"oid": oid, "role": role,
                                   "visible_deliveries": int(visible)})
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not audit issue history read: %s", type(exc).__name__)


@router.get("/entities/by-oid/{oid}/issue-history",
            summary="Cross-delivery issue history for one OID (NPI and partOf/QHIN "
                    "rules). Read-only; fail-closed feed scope.")
async def issue_history_route(
    oid: str,
    request: Request,
    limit: int = Query(12, ge=1, description="Deliveries returned, newest first "
                                             "window; hard cap 60."),
    before: Optional[str] = Query(None, description="Delivery (intake) id; return "
                                                    "deliveries before it."),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.core.config import settings
    from app.tefca_registry.rce import issue_history as svc

    if not bool(getattr(settings, "ENABLE_ISSUE_HISTORY", False)):
        raise _not_found()
    before_id = None
    if before is not None:
        try:
            before_id = uuid.UUID(before)
        except ValueError:
            raise HTTPException(422, "before must be a delivery id (UUID)")
    role = canonical_role(getattr(user, "role", None))
    reviewer = role_level(getattr(user, "role", None)) >= ROLE_HIERARCHY["reviewer"]
    try:
        result = await svc.get_issue_history(
            db, oid, reviewer_or_above=reviewer, settings=settings,
            limit=limit, before=before_id)
    except svc.HistoryNotFound as exc:
        await _audit_read(db, user, request, role, oid, exc.visible_deliveries)
        raise _not_found()
    await _audit_read(db, user, request, role, oid, len(result["deliveries"]))
    return result
