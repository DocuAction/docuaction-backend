"""HIPAA 164.312(b) audit controls for case-management PHI access.

WHY A ROUTER-LEVEL DEPENDENCY
    The alternative was adding an audit call to each of the 22 handlers. That
    approach fails the moment someone adds route 23 and forgets - which is exactly
    how audit gaps appear in the first place. Attaching this to the router means a
    new endpoint is audited because it exists, not because its author remembered.

WHY IT NEVER RAISES
    An audit backend that is down must not take clinical documentation down with
    it. A failure here is logged at ERROR and swallowed. That is a deliberate
    availability-over-completeness choice: the alternative is refusing to generate
    a discharge summary because a log row would not write. The ERROR line is the
    signal that the trail has a hole in it.

WHAT IS RECORDED
    Who (user id), what (method + path), when (created_at), from where (client IP,
    honouring X-Forwarded-For since App Service terminates TLS upstream), and the
    route template rather than the concrete URL. Query strings and request bodies
    are deliberately NOT recorded: they carry the PHI this log exists to protect,
    and an audit trail that itself leaks PHI is a breach, not a control.
"""

from __future__ import annotations

import logging

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import get_client_ip
from app.core.security import get_current_user
from app.core.database import get_db
from app.services.audit import log_tefca_event

logger = logging.getLogger("docuaction.case_management.audit")


def _client_ip(request: Request) -> str | None:
    """Real client IP, as observed by App Service.

    Delegates to the canonical helper, which reads the RIGHTMOST X-Forwarded-For
    entry. The previous implementation here took the leftmost entry, which the
    caller controls — a forged header would have written an attacker-chosen
    address into the PHI audit trail.
    """
    return get_client_ip(request)


async def audit_phi_access(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Record one PHI-surface access. Attached to the router, not to handlers."""
    try:
        route = request.scope.get("route")
        # The route template ("/patients/{patient_id}"), never the concrete path -
        # a patient id in an audit row is PHI in the audit log.
        resource = getattr(route, "path", None) or request.url.path
        await log_tefca_event(
            db,
            user=user,
            action=f"phi_access:{request.method.lower()}",
            resource_type="case_management",
            resource_id=resource,
            result="success",
            ip_address=_client_ip(request),
            details={
                "method": request.method,
                "route": resource,
                "module": "case_management",
                "control": "HIPAA 164.312(b)",
            },
        )
        await db.commit()
    except Exception as exc:
        # Never block clinical work on an audit failure - but say so loudly.
        logger.error(
            "PHI ACCESS AUDIT FAILED - trail has a gap for %s %s: %s: %s",
            request.method, request.url.path, type(exc).__name__, exc,
        )
