"""DEF-004 governed original-artifact restoration — the HTTP surface for the
DEV-only restore (`admin_restore_original.py` holds the actual business
logic; this module is FastAPI plumbing only: route registration, auth,
error mapping, sanitized response shape).

WHY THIS ROUTER MAY HAVE ZERO ROUTES ON IT
────────────────────────────────────────────
`router` is a plain, always-importable `APIRouter` so `main.py`'s
`safe_load()` can include it unconditionally, exactly like every other RCE
router. The ACTUAL endpoint is only decorated onto it inside
`if settings.is_development and settings.ENABLE_DEV_RESTORE_ORIGINAL:` at
IMPORT TIME. With either condition false, `router` carries no routes at all
— the endpoint does not exist in the OpenAPI schema, is not listed by any
route inspector, and returns nothing "existing but forbidden" to probe.
Turning it on/off is exactly the same on/off/restart pattern already used
for `REPORT_ARTIFACT_BACKEND`.

RESPONSE CONTRACT
──────────────────
No-store, correlation id, and a body carrying ONLY: restored/already_restored
booleans, the delivery job and intake ids, the content SHA-256 (a hash is
not sensitive), size, storage backend/locator, and the reconciliation
control's own PASS/FAIL shape. Never a stack trace (the global sanitized
handler already guarantees that for anything uncaught); never a database
detail; never a name, address or NPI (none of that is anywhere in scope —
this endpoint moves ZERO PII, only the bytes of a CSV whose contents are
never parsed).
"""

from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import request_context
from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_role

logger = logging.getLogger(__name__)

#: Always importable, always includable — see module docstring for why it
#: may end up with zero routes attached.
router = APIRouter(prefix="/api/tefca/rce/admin", tags=["TEFCA RCE Admin Restore (DEV only)"])


if settings.is_development and settings.ENABLE_DEV_RESTORE_ORIGINAL:

    class RestoreOriginalRequest(BaseModel):
        delivery_job_id: str = Field(
            ..., min_length=32, max_length=36,
            description="The delivery job whose preserved original is being restored")
        staged_blob_name: str = Field(
            ..., min_length=1, max_length=256,
            description="Relative name of the ALREADY-STAGED file under the approved "
                        "'operator-restore-staging/' prefix — never a URL, path, "
                        "account, container, key or token")
        expected_size_bytes: int = Field(..., gt=0)
        expected_sha256: str = Field(..., min_length=64, max_length=64)

    @router.post(
        "/restore-original",
        summary="[DEV only, feature-flagged] Restore a delivery's preserved original "
                "from a pre-staged private blob",
    )
    async def restore_original_route(
        body: RestoreOriginalRequest,
        request: Request,
        response: Response,
        db: AsyncSession = Depends(get_db),
        user=Depends(require_role("admin")),
    ):
        from app.tefca_registry.rce.admin_restore_original import \
            admin_restore_original_from_staged_blob
        from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
        from app.tefca_registry.rce.preserve_original_cmd import RestoreRefused

        correlation_id = str(_uuid.uuid4())
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Correlation-Id"] = correlation_id

        # Malformed identifier: sanitized 404, the established convention for
        # every other delivery-job lookup in this codebase (see
        # `pecos_retry_plan`). Validated BEFORE touching the database so the
        # only exceptions able to escape the lookup below are real
        # database/infrastructure failures, which must reach the global
        # sanitized handler — never be mis-reported as "no such job".
        try:
            job_uuid = _uuid.UUID(body.delivery_job_id)
        except ValueError:
            raise HTTPException(404, f"No delivery job {body.delivery_job_id}")

        job = await db.get(RceDeliveryJob, job_uuid)  # uncaught on purpose
        if job is None:
            raise HTTPException(404, f"No delivery job {body.delivery_job_id}")

        actor = getattr(user, "email", None) or "SYSTEM"

        try:
            result = await admin_restore_original_from_staged_blob(
                db, delivery_job=job, staged_blob_name=body.staged_blob_name,
                expected_size_bytes=body.expected_size_bytes,
                expected_sha256=body.expected_sha256, actor=actor)
        except RestoreRefused as exc:
            await db.rollback()
            logger.warning(
                "admin restore-original refused correlation_id=%s job=%s reason=%s",
                correlation_id, body.delivery_job_id, type(exc).__name__)
            raise HTTPException(409, str(exc))

        logger.info(
            "admin restore-original succeeded correlation_id=%s job=%s "
            "already_restored=%s actor=%s",
            correlation_id, body.delivery_job_id,
            result.get("already_restored"), actor)

        return {
            "correlation_id": correlation_id,
            "delivery_job_id": result["delivery_job_id"],
            "intake_id": result["intake_id"],
            "restored": result["restored"],
            "already_restored": result["already_restored"],
            "sha256": result["sha256"],
            "size_bytes": result["size_bytes"],
            "storage_backend": result["storage_backend"],
            "storage_locator": result["storage_locator"],
            "reconciliation_control": {
                "checked": result["verification"].get("checked"),
                "intact": result["verification"].get("intact"),
                "verified_by": result["verification"].get("verified_by"),
            },
            "request": {"request_id": request_context.get("request_id")},
        }
