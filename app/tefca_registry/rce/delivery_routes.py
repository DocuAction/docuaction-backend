"""Official ONC/RCE delivery registration — asynchronous.

WHY THIS IS A SEPARATE ROUTER FROM `rce/routes.py`
──────────────────────────────────────────────────
That module carries a load-bearing statement in its docstring: AREA 1 HAS NO
MUTATING ROUTE. Its immutability guarantee is enforced "by absence rather than
by a guard clause", and the value of that claim depends on the module staying
small enough to verify by reading it. Adding a registration flow, a job poller
surface and a dashboard to it would bury that.

So the pipeline routes stay where they are and this holds the operational
surface Data Operations uses. Neither module gains a route that mutates Area 1;
this one does not touch Area 1 at all, it registers work and reports on it.

WHY THE SYNCHRONOUS UPLOAD ROUTE IS NOT REMOVED
───────────────────────────────────────────────
`POST /api/tefca/rce/deliveries` still exists and still behaves exactly as it
did. It is what the existing tests exercise, it is the path the delivered
Government population was ingested through, and removing it would invalidate a
proven route to gain nothing. It is now documented as the direct/small-file
path; this is the operational one.

WHO MAY REGISTER A DELIVERY
───────────────────────────
`require_role("manager")`, which is ABOVE contributor. That is deliberate and it
is a change from the synchronous route's `contributor` floor.

`analyst` is an alias for `contributor` in `core/security.ROLE_ALIASES`, so a
contributor-gated registration route is one an analyst can call. An analyst
establishing what the official Government source data IS would collapse the
separation this workflow is built on — Data Operations registers the delivery,
the analyst reviews what it produced. The floor is set where it excludes the
analyst role and nothing higher, because Data Operations is not a reviewer, a QA
lead or a program manager.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Request, Response, UploadFile)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/rce", tags=["TEFCA RCE Deliveries"])

#: The role floor for establishing official Government source data. See the
#: module docstring — this is above `contributor` on purpose.
DATA_OPERATIONS_ROLE = "manager"


def _client_ip(request: Request):
    from app.core.client_ip import get_client_ip
    return get_client_ip(request)


def _parse_received(received_date: Optional[str]) -> Optional[datetime]:
    """Strict ISO parsing. A malformed date is refused, never defaulted to now.

    The same rule the synchronous route applies, for the same reason: silently
    recording today as the receipt date records a date that is simply wrong, and
    the receipt date is a fact about the Government's transmission.
    """
    if not received_date:
        return None
    try:
        return datetime.fromisoformat(received_date)
    except ValueError:
        raise HTTPException(
            422, f"received_date {received_date!r} is not an ISO date "
                 f"(expected e.g. 2026-09-01).")


@router.post("/official-deliveries", status_code=202,
             summary="Register an official ONC/RCE delivery (asynchronous)")
async def register_official_delivery(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="The official delivery file."),
    delivery_period: Optional[str] = Form(
        None, description="The period this delivery covers, e.g. 'September 2026'."),
    source: Optional[str] = Form(
        None, description="Who it came from, e.g. 'ONC/RCE'."),
    received_date: Optional[str] = Form(
        None, description="ISO date the delivery was RECEIVED."),
    government_reference: Optional[str] = Form(
        None, description="Optional Government reference or transmittal id."),
    notes: Optional[str] = Form(None),
    delimiter: Optional[str] = Form(
        None, description="Declare the delimiter explicitly: pipe, comma or "
                          "tab. Omit to detect."),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(DATA_OPERATIONS_ROLE)),
):
    """Accept a delivery for processing and return a receipt. HTTP 202.

    WHAT HAPPENS SYNCHRONOUSLY, AND WHY EACH PART MUST
      1. Security scan the bytes. A malicious payload must never be written or
         parsed, and a scan that happened later would happen after the file was
         already on disk.
      2. Refuse a binary container. Area 1 is append-only, so a mis-parsed .xlsx
         could never be removed once its rows landed. Refused here it leaves no
         trace at all, which is correct — it was never a delivery.
      3. Preserve the original bytes, unmodified, to immutable storage. This is
         a file write; it is fast even for a 100K-row delivery, and doing it now
         means the evidence exists before anything else can go wrong.
      4. Create the job row and return.

    Everything after that — parse, quality, curation, promotion, verification,
    reconciliation — belongs to the background worker. The browser is not asked
    to hold a connection open across it.

    THE RESPONSE IS A RECEIPT, NOT AN OUTCOME. It names the job to watch. The
    delivery has been ACCEPTED for processing; whether it processes cleanly is
    what the dashboard is for.
    """
    from app.api.routes import _scan_upload_or_reject
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce.intake import (NotADelimitedFile,
                                               preserve_original,
                                               reject_if_binary)

    raw = await file.read()
    if not raw:
        raise HTTPException(422, "The uploaded file is empty.")

    extension = ((file.filename or "").rsplit(".", 1)[-1].lower()
                 if "." in (file.filename or "") else "csv")
    await _scan_upload_or_reject(db, user, request, raw, file.filename,
                                 extension, "rce_delivery")

    try:
        reject_if_binary(raw, file.filename or "delivery")
    except NotADelimitedFile as exc:
        raise HTTPException(422, str(exc))

    received_at = _parse_received(received_date)
    sha256 = hashlib.sha256(raw).hexdigest()
    storage_path = preserve_original(raw, sha256, file.filename or "delivery")

    declared = {"pipe": "|", "comma": ",", "tab": "\t"}.get(
        (delimiter or "").lower(), delimiter)

    identity = jobs.job_identity(sha256=sha256, delivery_label=delivery_period,
                                 received_date=received_at)
    try:
        job = await jobs.request_job(
            db, identity=identity,
            original_filename=file.filename or "delivery",
            storage_path=storage_path, sha256=sha256,
            file_size_bytes=len(raw),
            registered_by=getattr(user, "email", None) or "SYSTEM",
            delivery_label=delivery_period,
            declared_delimiter=(declared or None),
            received_date=received_at,
            government_reference=government_reference,
            notes=notes, source_name=source)
    except jobs.DeliveryJobConflict as exc:
        raise HTTPException(409, str(exc))

    await _audit(db, "rce_official_delivery_registered", user, request, {
        "job_id": str(job.id), "sha256": sha256,
        "delivery_period": delivery_period, "source": source,
        "government_reference": government_reference,
        "original_filename": file.filename, "file_size_bytes": len(raw),
    })

    response.headers["Location"] = f"/api/tefca/rce/delivery-jobs/{job.id}"
    return {
        "accepted": True,
        "job": job.to_dict(),
        "watch": f"/api/tefca/rce/delivery-jobs/{job.id}",
        "note": ("The delivery has been accepted and its original bytes "
                 "preserved. Processing runs in the background; poll the job "
                 "or the delivery dashboard for progress. This response is a "
                 "receipt, not an outcome."),
    }


@router.get("/delivery-jobs", summary="Recent official delivery registrations")
async def list_delivery_jobs(
    limit: int = Query(50, ge=1, le=200),
    state: Optional[str] = Query(None, description="QUEUED|RUNNING|SUCCEEDED|FAILED"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import delivery_jobs as jobs

    rows = await jobs.list_jobs(db, limit=limit, state=state)
    return {"items": [row.to_dict() for row in rows], "count": len(rows)}


@router.get("/delivery-jobs/{job_id}", summary="One registration, and its progress")
async def get_delivery_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Poll this. Reads only — polling must never start or restart work."""
    from app.tefca_registry.rce import delivery_jobs as jobs

    job = await jobs.get_job(db, job_id)
    if job is None:
        raise HTTPException(404, f"No delivery job {job_id}")
    return job.to_dict()


@router.get("/deliveries/{intake_id}/dashboard",
            summary="The operational view of one delivery")
async def delivery_dashboard_route(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Status, counts, per-stage state and what may be done next — in one call.

    Every number is measured: the counts come from reconciliation recomputing
    the populations from the rows, and the statuses are the delivery's own
    CLEAN / CORRECTED / HELD / REJECTED vocabulary.
    """
    from app.tefca_registry.rce.delivery_dashboard import delivery_dashboard

    result = await delivery_dashboard(db, intake_id)
    if not result:
        raise HTTPException(404, f"No delivery {intake_id}")
    return result


# ── audit ────────────────────────────────────────────────────────────────────

async def _audit(db, action: str, user, request, metadata: Dict[str, Any]
                 ) -> None:
    """Record a registration act. Never the delivered data, only the act."""
    try:
        from app.tefca_registry import audit as reg_audit

        actor_id, actor_email = reg_audit.actor_of(user)
        reg_audit.record(db, action, actor_id=actor_id,
                         actor_email=actor_email,
                         ip_address=_client_ip(request), metadata=metadata)
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — an audit write must not fail the act
        logger.warning("could not audit %s: %s", action, exc)
