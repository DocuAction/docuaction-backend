"""USPS observability endpoints.

Mounted at ``/api/v1/usps/*``. Admin-only: the metrics expose upstream error
rates and circuit state, which is operational detail rather than review data.

Deliberately read-only. There is no endpoint here to reset the circuit breaker or
zero the counters — a breaker that an operator can force closed is a breaker that
gets forced closed during the incident it exists to contain. It reopens on its own
cooldown or on a successful probe.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import require_role
from app.tefca_registry.usps_client import get_usps_client

router = APIRouter(prefix="/api/v1/usps", tags=["usps"])


@router.get("/metrics", dependencies=[Depends(require_role("admin"))])
async def usps_metrics():
    """The seven counters, plus enough context to read them.

    Zeroes across the board mean one of two very different things — USPS is not
    configured, or it is configured and nothing has called it yet — so the
    configured flag and circuit state travel with the numbers rather than
    leaving the reader to guess.
    """
    client = get_usps_client()
    return {
        **client.metrics_snapshot(),
        "configured": client.configured,
        "environment": client.environment,
        "status": client.health()["status"],
    }
