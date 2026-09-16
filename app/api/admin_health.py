"""Operational health for administrators: `GET /api/admin/health`.

WHY THIS IS A SEPARATE, ROLE-GATED ENDPOINT
───────────────────────────────────────────
The public `/health` is polled unauthenticated by load balancers and the deploy
gate. Until the 2026-09-17 remediation it also published the live connector
probe, the bulletin scheduler state and the USPS client state. None of that is
a liveness fact, and all of it describes the deployment's operational topology
to anyone who can reach the host. Contract section 8 moves it here, behind
`require_role("admin")`, and adds what an operator diagnosing a deployment
actually needs: the Alembic revision the database is at and whether the
database answers, with latency.

WHAT IS STILL NEVER RETURNED
────────────────────────────
No secret, no connection string, no hostname beyond what `/api/config` already
shows to the caller. The database block says `reachable` and `latency_ms`; it
does not say where the database is. Connector notes are the connectors' own
labelling strings (which source, which agency, whether a key is configured),
never a key value.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import request_context
from app.core.database import get_db
from app.core.security import require_role
from app.core.telemetry import telemetry_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin Health"])


async def migration_revision(db) -> str:
    """`alembic_version.version_num`, or `unknown` when it cannot be read.

    Shared with the delivery detail `build` block. "unknown" is reported, never
    guessed, for the same reason `git_sha` is: a value that cannot be read is
    not a value that can be attributed.
    """
    try:
        value = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        return str(value) if value else "unknown"
    except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
        logger.info("migration revision unavailable: %s", type(exc).__name__)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return "unknown"


async def _database(db) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        return {"reachable": True,
                "latency_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin health: database probe failed: %s", type(exc).__name__)
        return {"reachable": False, "latency_ms": None,
                "error_class": type(exc).__name__}


def _scheduler() -> Dict[str, Any]:
    try:
        from app.bulletin_intelligence.scheduler import scheduler_status
        scheduler = scheduler_status()
        if isinstance(scheduler, dict):
            # The operator alert address is an operational contact, not a
            # health fact; kept out of every health surface.
            scheduler = {k: v for k, v in scheduler.items() if k != "alert_email"}
        return scheduler
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "error_class": type(exc).__name__}


def _usps() -> Dict[str, Any]:
    # Reported from client state only, never probed: an external API being slow
    # must not make this instance look unhealthy.
    try:
        from app.tefca_registry.usps_client import get_usps_client
        return get_usps_client().health()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error_class": type(exc).__name__}


async def _connectors() -> Dict[str, Any]:
    """The cached live probe that used to sit on /health, unchanged in substance."""
    try:
        from app.main import _probe_tefca  # lazy: app.main imports this module
        probe = await _probe_tefca()
        return {"status": probe.get("status"),
                "sources": probe.get("connectors") or {}}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "sources": {},
                "error_class": type(exc).__name__}


def _program_profile() -> Dict[str, Any]:
    from app.core.modules import profile_summary
    return profile_summary()


@router.get("/health", summary="Operational health (admin)")
async def admin_health(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin")),
):
    from app.main import health as public_health  # the public fields, verbatim

    body: Dict[str, Any] = dict(await public_health())
    body.update({
        "migration_revision": await migration_revision(db),
        "database": await _database(db),
        "scheduler": _scheduler(),
        "connectors": await _connectors(),
        "usps": _usps(),
        "program_profile": _program_profile(),
        "log_format": os.environ.get("DOCUACTION_LOG_FORMAT", "json"),
        # {enabled, reason, sampler, exporter}: whether traces are exported and
        # why not; never the connection string (presence is implied by `enabled`).
        "telemetry": telemetry_status(),
        "request_id": request_context.get("request_id"),
    })
    return body
