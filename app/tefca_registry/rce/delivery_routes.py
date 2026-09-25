"""Official ONC/RCE delivery registration — asynchronous — and the job-keyed
delivery detail API (remediation contract 2026-09-17, section 6).

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
`POST /api/tefca/rce/deliveries` still exists. It is what the existing tests
exercise and the path the delivered Government population was ingested
through. It is now DEPRECATED (program_manager floor, `Deprecation` /
`Sunset` headers, audited use) in favour of the official route here.

WHO MAY REGISTER A DELIVERY
───────────────────────────
`require_role("program_manager")`.

The first version set this to `manager` (level 3), reasoning from the
`analyst -> contributor` alias. Independent review found that the CONTRACT
Analyst is not that alias: `case_assignment.ROLE_ANALYST` is `reviewer`, level
4, "Task 3/4/5 front-line reviewers" — and 4 is above 3. A manager floor would
have let the actual analyst role establish what the official Government source
data IS, which collapses the separation this workflow is built on.

The hierarchy is linear, so the only floor that excludes reviewer (4),
senior_analyst (5) and qalead (6) — every review-side role — is
program_manager (7). §3 of the workflow names the page as for "authorized Data
Operations/Program personnel", so that floor is the stated intent, not an
over-restriction. If a dedicated Data Operations role is ever introduced it
belongs between 6 and 7 and this constant is the one place to change.

THE JOB-KEYED DETAIL API (2026-09-17)
─────────────────────────────────────
`GET /delivery-jobs/{id}/detail` answers "what happened to this delivery" in
one call, keyed by the JOB id the registration receipt returned. A caller who
only has an intake id is served too: the id is resolved job-id first, then
intake-id when exactly one job produced it (`resolved_from` says which), and
an intake with several jobs is a 409 naming the candidates rather than a guess.

FIELD-LEVEL AUTHORIZATION. The route floor is viewer, because status, counts,
timeline and build identity carry no Government data value. The blocks that DO
- records, exceptions, lineage, audit - are filled only for reviewer and above
(`security.role_at_least`); a viewer receives `null` for each and
`availability.<block> = "requires_role:reviewer"`. Nothing is masked or
partially rendered: a block is either the real thing or absent with a reason.

WHAT THIS MODULE IMPORTS FROM LANE P (and what it does when they are absent)
    delivery_jobs.status_for_job(db, job)      preferred status derivation;
                                               falls back to `_fallback_status`
                                               computed from the traceability
                                               tables with status_model
    curation.apply_disposition(...)            analyst disposition on an issue;
                                               falls back to transition_issue +
                                               recompute_hold_status (documented
                                               in the response as `fallback`)
    reconciliation.persist_snapshot(...)       re-snapshot after an identifier
                                               decision; skipped with a note
                                               when absent
Reads (latest snapshot, timeline, dispositions, identifier events) are done
here directly against the traceability tables and never depend on lane P.
"""

from __future__ import annotations

import hashlib

from sqlalchemy.exc import IntegrityError

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Request, Response, UploadFile)
from pydantic import BaseModel, Field
from sqlalchemy import Text, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import request_context
from app.core.database import get_db
from app.core.error_handler import create_error_response
from app.core.security import require_role, role_at_least
from app.tefca_registry.rce import status_model
from app.tefca_registry.rce import traceability_models as tm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/rce", tags=["TEFCA RCE Deliveries"])

#: The role floor for establishing official Government source data. See the
#: module docstring — it must sit ABOVE every review-side role.
DATA_OPERATIONS_ROLE = "program_manager"

#: Floor for evidence that carries delivered values (contract section 6).
EVIDENCE_ROLE = "reviewer"

#: Detail blocks that are null for a viewer.
REVIEWER_BLOCKS = ("records", "exceptions", "lineage", "audit")
#: Every block `availability` speaks about.
ALL_BLOCKS = ("records", "exceptions", "lineage", "audit", "verification", "reports")

AVAILABLE = "available"
NOT_YET = "not_yet"
NEVER_RAN = "never_ran"
NOT_CONFIGURED = "not_configured"
PROCESSING = "processing"
RECONSTRUCTED = "reconstructed"
UNAVAILABLE = "unavailable"
REQUIRES_REVIEWER = f"requires_role:{EVIDENCE_ROLE}"

#: What a Data Operations user should do after a failure, keyed by the stage
#: that failed. The words are the product's, fixed by the contract.
GUIDANCE_AREA1 = ("The file could not be parsed; the original bytes are preserved. "
                  "Correct the delivery file and register it as a NEW delivery.")
GUIDANCE_AFTER_AREA1 = ("Processing stopped after Area 1 was preserved. Review the "
                        "error, resolve the condition, and re-register the same file "
                        "as a new delivery; the failed job remains as history.")
GUIDANCE_UNKNOWN = ("Processing stopped before completion. Review the error reason, "
                    "resolve the condition, and register the delivery again as a NEW "
                    "delivery; this failed job remains as history.")
REMEDIATION_GUIDANCE = {
    "REGISTERED": GUIDANCE_AREA1, "RECEIPT_PRESERVED": GUIDANCE_AREA1,
    "SHA256": GUIDANCE_AREA1, "SCHEMA_VALIDATION": GUIDANCE_AREA1,
    "PARSING": GUIDANCE_AREA1, "ACCEPTED": GUIDANCE_AREA1,
    "QUALITY": GUIDANCE_AFTER_AREA1, "CURATION": GUIDANCE_AFTER_AREA1,
    "MATCHING": GUIDANCE_AFTER_AREA1, "PROMOTION": GUIDANCE_AFTER_AREA1,
    "RELATIONSHIPS": GUIDANCE_AFTER_AREA1, "VERIFICATION": GUIDANCE_AFTER_AREA1,
    "VERIFICATION_READINESS": GUIDANCE_AFTER_AREA1,
    "RECONCILIATION": GUIDANCE_AFTER_AREA1, "READY_FOR_REVIEW": GUIDANCE_AFTER_AREA1,
    "REPORT_GENERATION": GUIDANCE_AFTER_AREA1,
}

#: Cap on the audit union so one delivery of 23K lines cannot return 23K
#: disposition events in one page.
AUDIT_SOURCE_CAP = 2000


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


def _as_uuid(value) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _iso(value) -> Optional[str]:
    """ISO-8601 with an explicit offset. Naive datetimes in this codebase are
    UTC by construction (`datetime.utcnow()` writers, e.g. rce_issues.created_at)
    and are labelled so; a browser given an offset-less value parses it as
    local time, which showed a 4-hour skew on the exception ledger (QA-010)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _utc_naive(value) -> datetime:
    """A sortable key across tz-aware (traceability) and naive-UTC (legacy) rows.

    Accepts a datetime or an ISO-8601 string (the traceability `to_dict()`
    payloads carry strings); anything unparseable sorts first rather than
    failing the whole audit view.
    """
    if value is None:
        return datetime.min
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min
    if not isinstance(value, datetime):
        return datetime.min
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# ═══ registration ════════════════════════════════════════════════════════════

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
      5. Write the three instant stage events - REGISTERED, RECEIPT_PRESERVED,
         SHA256 - so the timeline starts at the route, not at the worker.
         Non-fatal: a delivery is not refused because its timeline could not be
         written; the gap is logged.

    Everything after that — parse, quality, curation, promotion, verification,
    reconciliation — belongs to the background worker. The browser is not asked
    to hold a connection open across it.

    THE RESPONSE IS A RECEIPT, NOT AN OUTCOME. It names the job to watch. The
    delivery has been ACCEPTED for processing; whether it processes cleanly is
    what the detail endpoint is for.
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

    await _record_registration_events(db, job, sha256=sha256, size=len(raw),
                                      filename=file.filename or "delivery",
                                      delivery_period=delivery_period,
                                      source=source,
                                      government_reference=government_reference,
                                      registered_by=getattr(user, "email", None))

    await _audit(db, "rce_official_delivery_registered", user, request, {
        "job_id": str(job.id), "sha256": sha256,
        "delivery_period": delivery_period, "source": source,
        "government_reference": government_reference,
        "original_filename": file.filename, "file_size_bytes": len(raw),
        "correlation_id": request_context.correlation_id(),
    })

    response.headers["Location"] = f"/api/tefca/rce/delivery-jobs/{job.id}"
    return {
        "accepted": True,
        "job": job.to_dict(),
        "watch": f"/api/tefca/rce/delivery-jobs/{job.id}",
        "detail": f"/api/tefca/rce/delivery-jobs/{job.id}/detail",
        "note": ("The delivery has been accepted and its original bytes "
                 "preserved. Processing runs in the background; poll the job "
                 "or the delivery detail for progress. This response is a "
                 "receipt, not an outcome."),
    }


async def _record_registration_events(db, job, *, sha256: str, size: int,
                                      filename: str, delivery_period, source,
                                      government_reference, registered_by) -> None:
    """REGISTERED, RECEIPT_PRESERVED, SHA256 - once per job, never fatal."""
    from app.tefca_registry.rce import stage_events

    try:
        existing = int((await db.execute(
            select(func.count()).select_from(tm.RceDeliveryStageEvent)
            .where(tm.RceDeliveryStageEvent.job_id == job.id))).scalar() or 0)
        if existing:
            # A second click or a poll that arrived before the first commit was
            # handed the SAME job; its timeline already starts.
            return
        with request_context.bind(job_id=job.id):
            await stage_events.record_instant(
                db, job.id, "REGISTERED", input_count=None,
                detail={"delivery_period": delivery_period, "source": source,
                        "government_reference": government_reference,
                        "registered_by": registered_by,
                        "original_filename": filename})
            await stage_events.record_instant(
                db, job.id, "RECEIPT_PRESERVED",
                detail={"file_size_bytes": size, "storage": "immutable_original"})
            await stage_events.record_instant(
                db, job.id, "SHA256", detail={"sha256": sha256})
    except Exception as exc:  # noqa: BLE001 - the receipt stands; the gap is logged
        logger.warning("registration stage events not written for job %s: %s",
                       job.id, type(exc).__name__, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass


# ═══ status derivation (lane P preferred, local fallback) ═══════════════════

async def latest_snapshot(db, job_id):
    """The highest-sequence reconciliation snapshot of a job, or None."""
    return (await db.execute(
        select(tm.RceReconciliationSnapshot)
        .where(tm.RceReconciliationSnapshot.job_id == job_id)
        .order_by(tm.RceReconciliationSnapshot.sequence.desc()).limit(1)
    )).scalar_one_or_none()


async def snapshot_history_count(db, job_id) -> int:
    return int((await db.execute(
        select(func.count()).select_from(tm.RceReconciliationSnapshot)
        .where(tm.RceReconciliationSnapshot.job_id == job_id))).scalar() or 0)


async def status_for_job(db, job) -> Dict[str, Any]:
    """`{processing_outcome, review_state, ...}` for one job.

    Prefers `delivery_jobs.status_for_job` (lane P) when it exists and returns
    the expected shape; otherwise derives the two axes here from the same
    persisted evidence with `status_model`. The result names which path ran.
    """
    try:
        from app.tefca_registry.rce import delivery_jobs as jobs
        derive = getattr(jobs, "status_for_job", None)
        if derive is not None:
            result = await derive(db, job)
            if isinstance(result, dict) and result.get("processing_outcome") \
                    and result.get("review_state"):
                result.setdefault("derived_by", "delivery_jobs.status_for_job")
                return result
    except Exception as exc:  # noqa: BLE001 - fall back, but say so
        logger.info("delivery_jobs.status_for_job unavailable (%s); using fallback",
                    type(exc).__name__, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
    return await _fallback_status(db, job)


async def _fallback_status(db, job) -> Dict[str, Any]:
    """Two-axis status from the traceability tables, via status_model only."""
    from app.tefca_registry.rce import dispositions, identifier_decisions, stage_events

    events = await stage_events.timeline(db, job.id)
    summary = stage_events.summarise(events)
    snapshot = await latest_snapshot(db, job.id)
    snapshot_dict = snapshot.to_dict() if snapshot is not None else None

    unresolved = conflicts = unexplained = 0
    review: Dict[str, int] = {}
    intake_id = job.source_intake_id
    if intake_id is not None:
        unresolved = await _unresolved_findings(db, intake_id)
        conflicts = await identifier_decisions.unresolved_for_intake(db, intake_id)
        if snapshot is not None:
            unexplained = await dispositions.records_without_disposition(db, intake_id)
        review = await _review_counts(db, intake_id)

    outcome = status_model.processing_outcome(
        job_state=job.state, job_stage=job.stage,
        failed_stage=summary["failed_stage"], error_reason=job.error_reason,
        snapshot=snapshot_dict,
        stages_completed=summary["completed_stages"] or None,
        unresolved_findings=unresolved, unresolved_conflicts=conflicts,
        unexplained_records=unexplained)
    review_state = status_model.review_state(
        outcome_code=outcome["code"],
        snapshot_passed=bool(snapshot_dict and snapshot_dict.get("passed")),
        **review)
    return {"processing_outcome": outcome, "review_state": review_state,
            "derived_by": "delivery_routes._fallback_status",
            "snapshot": snapshot_dict, "timeline_summary": summary}


async def _unresolved_findings(db, intake_id) -> int:
    """Open HIGH/CRITICAL findings of the current run."""
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.exception_ledger import OPEN_RESOLUTIONS
    from app.tefca_registry.rce.run_selection import current_issues_filter

    return int((await db.execute(
        select(func.count()).select_from(m.RceIssue).where(
            current_issues_filter(intake_id),
            m.RceIssue.severity.in_(("HIGH", "CRITICAL")),
            m.RceIssue.resolution.in_(OPEN_RESOLUTIONS)))).scalar() or 0)


async def _review_counts(db, intake_id) -> Dict[str, int]:
    """Where the humans are, from review_records tied to the delivery.

    A case is tied to the delivery either through the DQ bridge stamp
    (`verification_results.source_intake_id`) or through a promoted entity of
    the delivery (review-cycle cases). QA in progress is not derivable from the
    record alone and is reported as zero here; lane P's `status_for_job` reads
    the QA gate events for it.
    """
    # THIS delivery's cases only: the ones created against it (the DQ bridge
    # and the review cycle both stamp `source_intake_id`). Before 2026-09-20
    # a second disjunct also counted every case for any ENTITY the delivery
    # contains — cases from other deliveries, fixtures and the priority queue
    # — which is how a 50-record delivery reported 55 open items (QA-039,
    # QA-V03). The breakdown by queue source is what the overview labels.
    rows = (await db.execute(text("""
        SELECT
          coalesce(r.verification_results->>'queue_source', 'unknown') AS queue_source,
          count(*) FILTER (WHERE assigned_to_user_id IS NULL AND reviewer_resolution IS NULL
                             AND reportable_at IS NULL) AS open_items,
          count(*) FILTER (WHERE assigned_to_user_id IS NOT NULL AND reviewer_resolution IS NULL
                             AND reportable_at IS NULL) AS claimed_items,
          count(*) FILTER (WHERE reviewer_resolution IS NOT NULL AND reportable_at IS NULL)
                             AS qa_pending,
          count(*) FILTER (WHERE reportable_at IS NOT NULL) AS qa_approved
        FROM review_records r
        WHERE r.verification_results->>'source_intake_id' = :i
        GROUP BY 1"""),
        {"i": str(intake_id)})).all()
    out = {"open_work_items": 0, "claimed_work_items": 0, "qa_pending": 0,
           "qa_approved": 0, "open_breakdown": {}}
    for row in rows:
        out["open_work_items"] += int(row.open_items or 0)
        out["claimed_work_items"] += int(row.claimed_items or 0)
        out["qa_pending"] += int(row.qa_pending or 0)
        out["qa_approved"] += int(row.qa_approved or 0)
        if int(row.open_items or 0):
            out["open_breakdown"][row.queue_source] = int(row.open_items or 0)
    return out


# ═══ job list and single job ═════════════════════════════════════════════════

@router.get("/delivery-jobs", summary="Recent official delivery registrations")
async def list_delivery_jobs(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    state: Optional[str] = Query(None, description="QUEUED|RUNNING|SUCCEEDED|FAILED"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Each item carries the two-axis status, derived for the whole page in a
    bounded number of grouped queries (`delivery_jobs.status_for_jobs`), so
    the cost is a constant per page, never a multiple of `limit` and never a
    function of the table. `total` is the count of jobs matching `state`."""
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    rows = await jobs.list_jobs(db, limit=limit, state=state, offset=offset)
    count_stmt = select(func.count()).select_from(RceDeliveryJob)
    if state:
        count_stmt = count_stmt.where(RceDeliveryJob.state == state)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    derived: List[Optional[Dict[str, Any]]] = [None] * len(rows)
    if rows:
        try:
            derived = list(await jobs.status_for_jobs(db, rows))
        except Exception as exc:  # noqa: BLE001 - a status failure must not empty the list
            logger.info("status for the delivery job page unavailable: %s",
                        type(exc).__name__, exc_info=True)
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
    items = []
    for row, status in zip(rows, derived):
        item = row.to_dict()
        item["processing_outcome"] = status.get("processing_outcome") if status else None
        item["review_state"] = status.get("review_state") if status else None
        items.append(item)
    return {"items": items, "count": len(items), "total": total,
            "limit": limit, "offset": offset}


@router.get("/delivery-jobs/{job_id}", summary="One registration, and its progress")
async def get_delivery_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Poll this. Reads only — polling must never start or restart work."""
    from app.tefca_registry.rce import delivery_jobs as jobs

    if _as_uuid(job_id) is None:
        raise HTTPException(404, f"No delivery job {job_id}")
    job = await jobs.get_job(db, _as_uuid(job_id))
    if job is None:
        raise HTTPException(404, f"No delivery job {job_id}")
    return job.to_dict()


# ═══ resolution: job id first, intake id second ══════════════════════════════

async def resolve_job(db, ident: str):
    """(job, resolved_from) or an HTTP response.

    job id -> "job_id"; intake id with exactly one job -> "intake_id"; an
    intake with several jobs -> 409 with the candidates; nothing -> 404.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    ident_uuid = _as_uuid(ident)
    if ident_uuid is None:
        raise HTTPException(404, f"No delivery job or delivery {ident}")
    job = await db.get(RceDeliveryJob, ident_uuid)
    if job is not None:
        return job, "job_id"
    candidates = (await db.execute(
        select(RceDeliveryJob).where(RceDeliveryJob.source_intake_id == ident_uuid)
        .order_by(RceDeliveryJob.created_at.desc()))).scalars().all()
    if len(candidates) == 1:
        return candidates[0], "intake_id"
    if not candidates:
        raise HTTPException(404, f"No delivery job or delivery {ident}")
    return create_error_response(
        409, f"Delivery {ident} was processed by {len(candidates)} jobs; name one.",
        "CONFLICT", extra={"candidates": [
            {"job_id": str(c.id), "state": c.state, "stage": c.stage,
             "created_at": _iso(c.created_at), "attempt_count": c.attempt_count}
            for c in candidates]}), None


# ═══ the detail ══════════════════════════════════════════════════════════════

@router.get("/delivery-jobs/{job_id}/detail",
            summary="Everything about one delivery, keyed by job (or intake) id")
async def delivery_job_detail(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import dispositions, identifier_decisions, stage_events
    from app.tefca_registry.rce import models as m

    resolved = await resolve_job(db, job_id)
    if resolved[1] is None:
        return resolved[0]  # the 409 response
    job, resolved_from = resolved
    reviewer_ok = role_at_least(user, EVIDENCE_ROLE)

    intake = None
    if job.source_intake_id is not None:
        intake = await db.get(m.RceSourceIntake, job.source_intake_id)

    # Captured now, not re-read from `job`/`intake` later: _verification_block
    # below rolls back the session on a caught exception, and
    # AsyncSession.rollback() expires every attribute of every tracked
    # instance regardless of expire_on_commit. A later bare (non-awaited)
    # attribute access on an expired instance asks the ORM to reload it,
    # which needs a greenlet bridge that only exists inside an active
    # `await`, and raises MissingGreenlet instead (run 35169275292: 503 on
    # every delivery, reproduced live and root-caused via container logs).
    # Every block called after _verification_block must use these captured
    # values instead of reading `job`/`intake` attributes directly.
    job_id = job.id
    job_state = job.state
    intake_id = intake.id if intake is not None else None

    events = await stage_events.timeline(db, job.id)
    summary = stage_events.summarise(events)
    failed_stage = summary["failed_stage"] or (job.stage if job.state == "FAILED" else None)

    status = await status_for_job(db, job)
    snapshot = await latest_snapshot(db, job.id)
    snapshot_dict = snapshot.to_dict() if snapshot is not None else None
    history_count = await snapshot_history_count(db, job.id)

    job_dict = job.to_dict()
    job_dict["failed_stage"] = failed_stage
    job_dict["remediation_guidance"] = (
        _guidance(failed_stage) if job.state == "FAILED" else None)

    received = job.records_received
    if received is None and intake is not None:
        received = intake.record_count

    disposition_block = None
    if intake is not None:
        counts = await dispositions.counts_for_intake(db, intake.id)
        disposition_block = {
            **{k.lower(): v for k, v in counts.items()},
            "equation": dispositions.equation(counts, int(received or 0)),
            "unexplained": await dispositions.records_without_disposition(db, intake.id),
        }

    accounted = None
    if snapshot_dict:
        accounted = snapshot_dict["equation"]["accounted"]
    elif disposition_block:
        accounted = disposition_block.get("total")

    counts_block = {
        "records_received": received,
        "records_accounted": accounted,
        "records_processed": job.records_processed,
        "records_declared": intake.record_count if intake is not None else None,
    }
    if disposition_block:
        for key in ("created", "updated", "matched_unchanged", "held", "rejected",
                    "missing_key", "excluded"):
            counts_block[key] = disposition_block.get(key)

    reports = await _reports(db, job.id)
    verification = await _verification_block(db, intake, job)

    blocks: Dict[str, Any] = {"records": None, "exceptions": None, "lineage": None,
                              "audit": None, "verification": verification,
                              "reports": reports}
    if reviewer_ok and intake is not None:
        blocks["records"] = await _records_block(db, intake_id)
        blocks["exceptions"] = await _exceptions_block(db, intake_id)
        blocks["lineage"] = await _lineage_block(db, intake_id, identifier_decisions)
        blocks["audit"] = await _audit_block(db, job_id, intake_id)

    availability = {
        block: _availability(block, job_state=job_state, intake_id=intake_id,
                             reviewer_ok=reviewer_ok, value=blocks[block],
                             snapshot=snapshot_dict)
        for block in ALL_BLOCKS}

    return {
        "resolved_from": resolved_from,
        "job": job_dict,
        "status": {"processing_outcome": status.get("processing_outcome"),
                   "review_state": status.get("review_state"),
                   "derived_by": status.get("derived_by")},
        "counts": counts_block,
        "timeline": events,
        "reconciliation": {"snapshot": snapshot_dict, "history_count": history_count},
        "dispositions": disposition_block,
        "exceptions": blocks["exceptions"],
        "records": blocks["records"],
        "lineage": blocks["lineage"],
        "audit": blocks["audit"],
        "verification": blocks["verification"],
        "reports": blocks["reports"],
        "build": await _build_block(db),
        "correlation": {
            "request_id": request_context.get("request_id"),
            "job_correlation_id": events[0]["correlation_id"] if events else None,
            "job_id": str(job_id),
            "intake_id": str(intake_id) if intake_id is not None else None,
        },
        "availability": availability,
    }


def _guidance(failed_stage: Optional[str]) -> str:
    return REMEDIATION_GUIDANCE.get((failed_stage or "").upper(), GUIDANCE_UNKNOWN)


def _availability(block: str, *, job_state: str, intake_id, reviewer_ok: bool, value,
                  snapshot: Optional[Dict[str, Any]]) -> str:
    if block in REVIEWER_BLOCKS and not reviewer_ok:
        return REQUIRES_REVIEWER
    if intake_id is None:
        if job_state == "QUEUED":
            return NOT_YET
        if job_state == "RUNNING":
            return PROCESSING
        return NEVER_RAN  # failed before Area 1 existed
    if block == "verification":
        if not value:
            return UNAVAILABLE
        state = value.get("state")
        if state == status_model.COVERAGE_NOT_CONFIGURED:
            return NOT_CONFIGURED
        if state == status_model.COVERAGE_IN_PROGRESS:
            return PROCESSING
        if state == status_model.COVERAGE_NOT_RUN:
            return NEVER_RAN
        return AVAILABLE
    if block == "reports":
        return AVAILABLE if value else NOT_YET
    if job_state == "RUNNING" and block in ("exceptions", "lineage"):
        return PROCESSING
    if snapshot and snapshot.get("reconstructed") and block in ("exceptions", "lineage"):
        return RECONSTRUCTED
    return AVAILABLE if value is not None else UNAVAILABLE


async def _build_block(db) -> Dict[str, Any]:
    from app.api.admin_health import migration_revision

    identity = request_context.build_identity()
    return {"git_sha": identity["git_sha"], "build_time": identity["build_time"],
            "version": identity["version"], "environment": identity["environment"],
            "migration_revision": await migration_revision(db)}


async def _reports(db, job_id) -> List[Dict[str, Any]]:
    """Reports for the job, one entry per report id, each VERIFIED against the
    stored report it names (`links_for_job` drops any link whose stored report
    describes another delivery — QA-034) and carrying every registered
    rendering with format, filename, size and checksum (QA-033)."""
    from app.reports.data.delivery_report_links import links_for_job

    links = await links_for_job(db, job_id)
    grouped: Dict[str, Dict[str, Any]] = {}
    for link in links:
        entry = grouped.setdefault(link["report_id"], {
            "report_id": link["report_id"], "report_type": link.get("report_type"),
            "snapshot_id": link.get("snapshot_id"), "generated_at": link.get("generated_at"),
            "generated_by": link.get("generated_by"),
            "template_version": link.get("template_version"),
            "build_sha": link.get("build_sha"), "correlation_id": link.get("correlation_id"),
            "delivery_verified": True, "artifacts": []})
        if link.get("artifact_id"):
            entry["artifacts"].append({
                "id": str(link["artifact_id"]),
                "format": _format_word(link.get("content_type")),
                "content_type": link.get("content_type"),
                "filename": f"delivery-report-{link['report_id']}."
                            f"{_format_word(link.get('content_type'))}",
                "size_bytes": link.get("size_bytes"),
                "sha256": link.get("file_sha256"),
                "artifact_version": link.get("artifact_version"),
                "storage_backend": link.get("storage_backend"),
                "durable": link.get("durable"),
                "status": "registered" if link.get("file_sha256") else "unregistered",
            })
    return list(grouped.values())


def _format_word(content_type: Optional[str]) -> str:
    return {"text/html": "html", "text/csv": "csv", "application/pdf": "pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            }.get(content_type or "", "file")


async def _verification_block(db, intake, job) -> Dict[str, Any]:
    from app.tefca_registry.rce import verification_coverage as vc

    try:
        if intake is None:
            return vc.empty_coverage(
                reason="No Area 1 intake exists for this job, so no entity was eligible.")
        return await vc.coverage_for_intake(db, intake.id, job=job)
    except Exception as exc:  # noqa: BLE001 - one panel, not the page
        logger.info("verification coverage unavailable: %s", type(exc).__name__,
                    exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}


async def _records_block(db, intake_id) -> Dict[str, Any]:
    from app.tefca_registry.rce import models as m

    by_parse = (await db.execute(
        select(m.RceSourceRecord.parse_status, func.count())
        .where(m.RceSourceRecord.source_intake_id == intake_id)
        .group_by(m.RceSourceRecord.parse_status))).all()
    by_promotion = (await db.execute(
        select(m.RceSourceRecord.promotion_status, func.count())
        .where(m.RceSourceRecord.source_intake_id == intake_id)
        .group_by(m.RceSourceRecord.promotion_status))).all()
    by_curated = (await db.execute(
        select(m.RceCuratedRecord.record_status, func.count())
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .group_by(m.RceCuratedRecord.record_status))).all()
    return {
        "source_records": sum(int(n) for _, n in by_parse),
        "by_parse_status": {str(k): int(n) for k, n in by_parse},
        "by_promotion_status": {str(k): int(n) for k, n in by_promotion},
        "curated_by_status": {str(k): int(n) for k, n in by_curated},
        "endpoints": {
            "records": f"/api/tefca/rce/deliveries/{intake_id}/records",
            "curated": f"/api/tefca/rce/deliveries/{intake_id}/curated",
            "dispositions": f"/api/tefca/rce/deliveries/{intake_id}/dispositions",
        },
    }


async def _exceptions_block(db, intake_id) -> Dict[str, Any]:
    from app.tefca_registry.rce.exception_ledger import totals_for_intake

    totals = await totals_for_intake(db, intake_id)
    totals["endpoint"] = f"/api/tefca/rce/deliveries/{intake_id}/exceptions"
    return totals


async def _lineage_block(db, intake_id, identifier_decisions) -> Dict[str, Any]:
    unresolved = await identifier_decisions.unresolved_for_intake(db, intake_id)
    recent = (await db.execute(
        select(tm.TefcaIdentifierDecisionEvent)
        .where(tm.TefcaIdentifierDecisionEvent.intake_id == intake_id)
        .order_by(tm.TefcaIdentifierDecisionEvent.decided_at.desc()).limit(20)
    )).scalars().all()
    versions = int((await db.execute(text("""
        SELECT count(*) FROM tefca_entity_versions v
        WHERE v.entity_id IN (SELECT canonical_entity_id FROM rce_curated_records
                              WHERE source_intake_id = CAST(:i AS uuid)
                                AND canonical_entity_id IS NOT NULL)"""),
        {"i": str(intake_id)})).scalar() or 0)
    return {"unresolved_identifier_conflicts": unresolved,
            "recent_identifier_decisions": [r.to_dict() for r in recent],
            "entity_versions": versions}


async def _audit_block(db, job_id, intake_id) -> Dict[str, Any]:
    from app.models.database import AuditLog
    from app.tefca_registry import models as reg

    ids = [str(job_id), str(intake_id)]
    registry = int((await db.execute(
        select(func.count()).select_from(reg.TefcaRegAuditLog).where(or_(
            cast(reg.TefcaRegAuditLog.metadata_, Text).ilike(f"%{ids[0]}%"),
            cast(reg.TefcaRegAuditLog.metadata_, Text).ilike(f"%{ids[1]}%"))))).scalar() or 0)
    platform = int((await db.execute(
        select(func.count()).select_from(AuditLog).where(or_(
            AuditLog.resource_id.in_(ids), AuditLog.correlation_id == ids[0])))).scalar() or 0)
    disposition_events = int((await db.execute(
        select(func.count()).select_from(tm.RceDispositionEvent)
        .where(tm.RceDispositionEvent.intake_id == intake_id))).scalar() or 0)
    return {"registry_audit_rows": registry, "platform_audit_rows": platform,
            "disposition_events": disposition_events,
            "endpoint": f"/api/tefca/rce/deliveries/{intake_id}/audit"}


# ═══ timeline ════════════════════════════════════════════════════════════════

@router.get("/delivery-jobs/{job_id}/timeline", summary="Every stage attempt of one job")
async def delivery_job_timeline(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import stage_events

    resolved = await resolve_job(db, job_id)
    if resolved[1] is None:
        return resolved[0]
    job, resolved_from = resolved
    events = await stage_events.timeline(db, job.id)
    summary = stage_events.summarise(events)
    return {"job_id": str(job.id), "resolved_from": resolved_from,
            "intake_id": str(job.source_intake_id) if job.source_intake_id else None,
            "state": job.state, "stage": job.stage, "events": events,
            "summary": {"failed_stage": summary["failed_stage"],
                        "completed_stages": summary["completed_stages"],
                        "attempts": summary["attempts"]},
            "correlation": {"request_id": request_context.get("request_id")}}


# ═══ dashboard (kept) ════════════════════════════════════════════════════════

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

    if _as_uuid(intake_id) is None:
        raise HTTPException(404, f"No delivery {intake_id}")
    result = await delivery_dashboard(db, _as_uuid(intake_id))
    if not result:
        raise HTTPException(404, f"No delivery {intake_id}")
    return result


# ═══ dispositions ════════════════════════════════════════════════════════════

async def _intake_or_404(db, intake_id: str):
    from app.tefca_registry.rce import models as m

    intake_uuid = _as_uuid(intake_id)
    intake = await db.get(m.RceSourceIntake, intake_uuid) if intake_uuid else None
    if intake is None:
        raise HTTPException(404, f"No delivery {intake_id}")
    return intake


@router.get("/deliveries/{intake_id}/dispositions",
            summary="Current disposition of every delivered line")
async def list_dispositions_route(
    intake_id: str,
    disposition: Optional[str] = Query(None, description="|".join(tm.DISPOSITIONS)),
    source_row: Optional[int] = Query(None, ge=1),
    entity_name: Optional[str] = Query(None, max_length=200),
    npi: Optional[str] = Query(None, max_length=40),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(EVIDENCE_ROLE)),
):
    from app.tefca_registry.rce.exception_ledger import list_dispositions

    intake = await _intake_or_404(db, intake_id)
    try:
        rows, total = await list_dispositions(
            db, intake.id, disposition=disposition, source_row=source_row,
            entity_name=entity_name, npi=npi, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"intake_id": str(intake.id), "items": rows, "count": len(rows),
            "total": total, "offset": offset, "limit": limit}


@router.get("/deliveries/{intake_id}/dispositions.csv",
            summary="Record-level dispositions as CSV")
async def dispositions_csv_route(
    intake_id: str,
    disposition: Optional[str] = Query(None),
    source_row: Optional[int] = Query(None, ge=1),
    entity_name: Optional[str] = Query(None, max_length=200),
    npi: Optional[str] = Query(None, max_length=40),
    limit: int = Query(50000, ge=1, le=200000),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(EVIDENCE_ROLE)),
):
    from app.tefca_registry.rce.exception_ledger import (dispositions_csv,
                                                          list_dispositions)

    intake = await _intake_or_404(db, intake_id)
    try:
        rows, total = await list_dispositions(
            db, intake.id, disposition=disposition, source_row=source_row,
            entity_name=entity_name, npi=npi, limit=limit, offset=0)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    body = dispositions_csv(rows)
    from app.reports.routes import download_headers
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers=download_headers(f"dispositions-{intake.id}.csv",
                                 extra={"X-Total-Rows": str(total),
                                        "X-Returned-Rows": str(len(rows))}))


# ═══ exceptions ══════════════════════════════════════════════════════════════

@router.get("/deliveries/{intake_id}/exceptions",
            summary="The exception ledger for one delivery")
async def list_exceptions_route(
    intake_id: str,
    source_row: Optional[int] = Query(None, ge=1),
    entity_name: Optional[str] = Query(None, max_length=200),
    npi: Optional[str] = Query(None, max_length=40),
    rule_code: Optional[str] = Query(None, max_length=32),
    issue_type: Optional[str] = Query(None, max_length=64),
    severity: Optional[str] = Query(None, max_length=20),
    stage: Optional[str] = Query(None, description="QUALITY|PROMOTION|VERIFICATION"),
    status: Optional[str] = Query(None, description="issue resolution, e.g. OPEN"),
    from_: Optional[str] = Query(None, alias="from", description="created_at >= ISO"),
    to: Optional[str] = Query(None, description="created_at <= ISO"),
    assigned_to: Optional[str] = Query(None, description="holder user id"),
    disposition: Optional[str] = Query(None, description="current disposition"),
    all_runs: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(EVIDENCE_ROLE)),
):
    from app.tefca_registry.rce.exception_ledger import list_exceptions

    intake = await _intake_or_404(db, intake_id)
    try:
        result = await list_exceptions(
            db, intake.id, source_row=source_row, entity_name=entity_name, npi=npi,
            rule_code=rule_code, issue_type=issue_type, severity=severity, stage=stage,
            status=status, from_=from_, to=to, assigned_to=assigned_to,
            disposition=disposition, all_runs=all_runs, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    result["correlation"] = {"request_id": request_context.get("request_id")}
    return result


# ═══ verification coverage ═══════════════════════════════════════════════════

@router.get("/deliveries/{intake_id}/verification-coverage",
            summary="Per-source verification coverage of one delivery")
async def verification_coverage_route(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Viewer: counts of entities per source and outcome, never a value.

    `automated_coverage` is the whole-population figure (every eligible entity,
    every source) — the ONLY thing `sources.*` below already counted, restated
    as one eligible/covered/remaining triple so a screen does not have to sum
    four cards to answer "is this delivery done". `analyst_sample` is the
    SEPARATE, methodologically-approved statistical sample (see
    `qhin_sampling.CochranSampler`); it is deliberately not merged into
    `automated_coverage` — conflating "the whole population has been looked
    up" with "the audit sample is complete" is exactly the ambiguity this
    response exists to remove.
    """
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce import verification_coverage as vc
    from app.tefca_registry.rce import automated_verification as av

    intake = await _intake_or_404(db, intake_id)
    job = await jobs.job_for_intake(db, intake.id)
    result = await vc.coverage_for_intake(db, intake.id, job=job)
    result["automated_coverage"] = await av.coverage_progress(db, intake.id)
    result["automated_coverage"]["automatic_scheduling_enabled"] = av.automated_coverage_enabled()
    result["analyst_sample"] = await _analyst_sample_summary(db, intake.id)
    return result


async def _analyst_sample_summary(db, intake_id) -> Dict[str, Any]:
    """Calculated vs. actual analyst-review sample size for one delivery,
    read-only (never draws or persists a plan). Distinct from
    `automated_coverage` above: this is Cochran's formula over the delivery's
    eligible population, and the ACTUAL figure is only nonzero once a Program
    Manager has actually created a review cycle for this delivery."""
    from app.tefca_registry.rce.reconciliation import reconcile_delivery
    from app.tefca_registry.rce.verification_coverage import _eligible_count
    from app.tefca_registry.sampling_engine import CochranSampler
    from app.tefca_registry import review_cycle as rc

    eligible = await _eligible_count(db, intake_id)
    calculated = CochranSampler().calculate_sample_size(eligible) if eligible else 0

    cycles = await rc.read_review_cycle(db, intake_id)
    plans = cycles.get("plans") or []
    latest = plans[0] if plans else None
    actual_sample_size = latest.get("sample_size") if latest else 0
    linked = (latest.get("membership_count", 0) - latest.get("unlinked_members", 0)) if latest else 0

    recon = None
    try:
        recon = await reconcile_delivery(db, intake_id)
    except Exception:  # noqa: BLE001 - this summary must not fail the coverage read
        recon = None

    return {
        "eligible_population": eligible,
        "calculated_sample_size": calculated,
        "review_cycle_exists": latest is not None,
        # The id a delivery-scoped report request must name in
        # parameters.review_cycle_id (see generator.py's job_id/intake_id
        # cross-check). None until a Program Manager creates the cycle, and
        # None (not a fabricated id) if a legacy sample predates this fix and
        # was never linked to a ReviewCycle row - see read_review_cycle.
        "review_cycle_id": latest.get("review_cycle_id") if latest else None,
        "actual_sample_size": actual_sample_size,
        "actual_sample_verified": linked,
        "reconciliation_passed": bool(recon.get("passed")) if recon else None,
        "note": ("Calculated from Cochran's formula (95% confidence, 5% margin, "
                 "finite-population correction) over this delivery's eligible "
                 "population. The sample is smaller than the population by "
                 "design when the approved methodology says so; that is not a "
                 "processing gap."),
    }


@router.post("/deliveries/{intake_id}/verification-coverage/run",
             summary="Run one batch of automated verification coverage over eligible entities")
async def run_verification_coverage(
    intake_id: str,
    batch_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("program_manager")),
):
    """PROGRAM MANAGER — an explicit, human-authorized batch, regardless of
    whether unattended scheduling (`ENABLE_AUTOMATED_VERIFICATION_COVERAGE`)
    is on. Never creates a ReviewRecord or a review cycle; see
    `automated_verification.py` for why that separation is deliberate. Call
    again (or let the scheduler, if enabled) until the response's `remaining`
    is 0."""
    from app.tefca_registry.rce import automated_verification as av

    intake = await _intake_or_404(db, intake_id)
    return await av.run_coverage_batch(
        db, intake.id, batch_size=batch_size,
        actor=getattr(user, "email", None) or "SYSTEM")


# ═══ audit ═══════════════════════════════════════════════════════════════════

@router.get("/deliveries/{intake_id}/audit",
            summary="Every audited act about one delivery, newest first")
async def delivery_audit_route(
    intake_id: str,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    job_id: Optional[str] = Query(None, description=(
        "The delivery job the client is showing. Echoed as requested_job_id "
        "beside the intake's latest job so the header never silently swaps jobs.")),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(EVIDENCE_ROLE)),
):
    """A union of six evidence sources, each bounded, sorted by time.

    Registry audit rows whose metadata names the intake or the job; platform
    audit rows keyed to either id (or correlated to the job); stage events;
    disposition events; identifier decision events; report links.
    """
    from app.models.database import AuditLog
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce import stage_events

    from app.tefca_registry.identity import actor_facts, resolve_principals

    intake = await _intake_or_404(db, intake_id)
    job = await jobs.job_for_intake(db, intake.id)
    ids = [str(intake.id)] + ([str(job.id)] if job else [])
    if job_id and job_id not in ids:
        ids.append(job_id)
    entries: List[Dict[str, Any]] = []
    truncated: Dict[str, bool] = {}

    # Every entry carries the governed facts as FIRST-CLASS fields (QA-023..
    # QA-028): a UTC timestamp with offset, event type, resource type/id, the
    # correlation id, the actor CLASS (human / service / system) beside the
    # actor, and a display label. `detail` keeps the raw payload.
    def add(source, at, action, actor, ref, detail, *, event_type=None,
            resource_type=None, resource_id=None, correlation_id=None,
            actor_class=None, executing_service=None, human_initiator=None):
        facts = (actor_facts(service=executing_service, actor_email=human_initiator)
                 if executing_service else actor_facts(actor_email=actor))
        if actor_class:
            facts["actor_class"] = actor_class
        entries.append({"at": _iso(at), "_key": _utc_naive(at), "source": source,
                        "action": action, "label": _audit_label(action),
                        "event_type": event_type or _event_type_of(source, action),
                        "resource_type": resource_type or _resource_type_of(source),
                        "resource_id": resource_id or ref,
                        "correlation_id": correlation_id,
                        "actor": actor, "ref": ref, "detail": detail, **facts})

    registry_rows = (await db.execute(
        select(reg.TefcaRegAuditLog).where(or_(*[
            cast(reg.TefcaRegAuditLog.metadata_, Text).ilike(f"%{i}%") for i in ids]))
        .order_by(reg.TefcaRegAuditLog.created_at.desc()).limit(AUDIT_SOURCE_CAP)
    )).scalars().all()
    truncated["tefca_reg_audit_log"] = len(registry_rows) >= AUDIT_SOURCE_CAP
    for row in registry_rows:
        meta = row.metadata_ or {}
        add("tefca_reg_audit_log", row.created_at, row.action, row.actor_email,
            str(row.id), {"entity_id": str(row.entity_id) if row.entity_id else None,
                          "metadata": meta},
            resource_type="entity" if row.entity_id else "registry",
            resource_id=str(row.entity_id) if row.entity_id else str(row.id),
            correlation_id=meta.get("correlation_id") or meta.get("request_id"),
            actor_class="human" if row.actor_email and "@" in str(row.actor_email) else "system")

    platform_rows = (await db.execute(
        select(AuditLog).where(or_(AuditLog.resource_id.in_(ids),
                                   AuditLog.correlation_id.in_(ids)))
        .order_by(AuditLog.created_at.desc()).limit(AUDIT_SOURCE_CAP))).scalars().all()
    truncated["audit_logs"] = len(platform_rows) >= AUDIT_SOURCE_CAP
    principals = await resolve_principals(db, [r.user_id for r in platform_rows if r.user_id])
    for row in platform_rows:
        who = principals.get(str(row.user_id)) if row.user_id else None
        add("audit_logs", row.created_at, row.action,
            (who or {}).get("label") or (str(row.user_id) if row.user_id else None), str(row.id),
            {"event_type": row.event_type, "outcome": row.outcome,
             "resource_type": row.resource_type, "resource_id": row.resource_id,
             "correlation_id": row.correlation_id, "details": row.details,
             "actor_user_id": str(row.user_id) if row.user_id else None},
            event_type=row.event_type, resource_type=row.resource_type,
            resource_id=row.resource_id, correlation_id=row.correlation_id,
            actor_class="human" if row.user_id else "system")

    if job is not None:
        for ev in await stage_events.timeline(db, job.id):
            add("rce_delivery_stage_events", ev["completed_at"] or ev["started_at"],
                f"{ev['stage']}:{ev['status']}", ev["worker_id"], ev["id"], ev,
                event_type="delivery_stage", resource_type="delivery_job",
                resource_id=str(job.id),
                correlation_id=ev.get("correlation_id") or str(job.id),
                executing_service=ev["worker_id"],
                human_initiator=(ev.get("detail") or {}).get("actor") if isinstance(ev.get("detail"), dict) else None)

    disp_rows = (await db.execute(
        select(tm.RceDispositionEvent).where(tm.RceDispositionEvent.intake_id == intake.id)
        .order_by(tm.RceDispositionEvent.decided_at.desc()).limit(AUDIT_SOURCE_CAP)
    )).scalars().all()
    truncated["rce_disposition_events"] = len(disp_rows) >= AUDIT_SOURCE_CAP
    for row in disp_rows:
        payload = row.to_dict()
        add("rce_disposition_events", row.decided_at,
            f"disposition:{row.disposition}", row.actor, str(row.id), payload,
            event_type="disposition", resource_type="curated_record",
            resource_id=payload.get("curated_record_id") or payload.get("source_record_id"),
            correlation_id=payload.get("correlation_id") or payload.get("request_id"),
            actor_class="human" if row.actor and "@" in str(row.actor) else "service")

    ident_rows = (await db.execute(
        select(tm.TefcaIdentifierDecisionEvent)
        .where(tm.TefcaIdentifierDecisionEvent.intake_id == intake.id)
        .order_by(tm.TefcaIdentifierDecisionEvent.decided_at.desc()).limit(AUDIT_SOURCE_CAP)
    )).scalars().all()
    truncated["tefca_identifier_decision_events"] = len(ident_rows) >= AUDIT_SOURCE_CAP
    for row in ident_rows:
        payload = row.to_dict()
        add("tefca_identifier_decision_events", row.decided_at,
            f"identifier:{row.decision}", row.actor, str(row.id), payload,
            event_type="identifier_decision", resource_type="entity",
            resource_id=payload.get("entity_id"),
            correlation_id=payload.get("correlation_id") or payload.get("request_id"),
            actor_class="human" if row.actor and "@" in str(row.actor) else "service")

    for row in (await db.execute(
            select(tm.RceDeliveryReportLink)
            .where(tm.RceDeliveryReportLink.intake_id == intake.id))).scalars().all():
        add("rce_delivery_report_links", row.generated_at,
            f"report:{row.report_type}", row.generated_by, str(row.id), row.to_dict(),
            event_type="reporting", resource_type="report", resource_id=row.report_id,
            correlation_id=row.correlation_id,
            actor_class="human" if row.generated_by and "@" in str(row.generated_by) else "system")

    entries.sort(key=lambda e: e["_key"], reverse=True)
    total = len(entries)
    page = entries[offset:offset + limit]
    for e in page:
        e.pop("_key", None)
    return {"intake_id": str(intake.id), "job_id": str(job.id) if job else None,
            # Labelled identifiers (QA-029): the job the client asked about and
            # the intake's latest job are two facts, not one.
            "identifiers": {"intake_id": str(intake.id),
                            "latest_job_id": str(job.id) if job else None,
                            "requested_job_id": job_id,
                            "request_id": request_context.get("request_id")},
            "items": page, "count": len(page), "total": total, "offset": offset,
            "limit": limit, "source_caps": {"per_source": AUDIT_SOURCE_CAP,
                                            "truncated": truncated},
            "correlation": {"request_id": request_context.get("request_id")}}


_EVENT_TYPE_BY_SOURCE = {
    "tefca_reg_audit_log": "registry", "audit_logs": "platform",
    "rce_delivery_stage_events": "delivery_stage", "rce_disposition_events": "disposition",
    "tefca_identifier_decision_events": "identifier_decision",
    "rce_delivery_report_links": "reporting",
}
_RESOURCE_TYPE_BY_SOURCE = {
    "tefca_reg_audit_log": "registry", "audit_logs": "platform",
    "rce_delivery_stage_events": "delivery_job", "rce_disposition_events": "curated_record",
    "tefca_identifier_decision_events": "entity", "rce_delivery_report_links": "report",
}


def _event_type_of(source: str, action: str) -> str:
    return _EVENT_TYPE_BY_SOURCE.get(source, source)


def _resource_type_of(source: str) -> str:
    return _RESOURCE_TYPE_BY_SOURCE.get(source, source)


def _audit_label(action: Optional[str]) -> str:
    """One display label for the two naming styles (QA-028): `STAGE:STATUS`
    from the delivery pipeline and `snake_case` from the registry."""
    if not action:
        return "Event"
    text_ = str(action)
    if ":" in text_:
        left, right = text_.split(":", 1)
        return f"{left.replace('_', ' ').title()} {right.replace('_', ' ').lower()}"
    return text_.replace("_", " ").capitalize()


# ═══ analyst dispositions on issues ══════════════════════════════════════════

#: API vocabulary for analyst dispositions (UPPER_SNAKE, as the frontend sends
#: it). Each maps onto lane P's curation.DISPOSITION_DECISIONS by lower-casing.
ISSUE_DECISIONS = ("ACCEPT", "REJECT", "CORRECT", "CONFIRM_EXISTING", "CONFIRM_SUBMITTED",
                   "REQUEST_EVIDENCE", "DEFER", "ESCALATE")
#: Fallback mapping onto the Issue Ledger state machine when lane P's
#: apply_disposition is absent.
_FALLBACK_TRANSITION = {
    "ACCEPT": "APPROVED", "REJECT": "REJECTED", "CORRECT": "APPROVED",
    "CONFIRM_EXISTING": "REJECTED", "CONFIRM_SUBMITTED": "APPROVED",
    "REQUEST_EVIDENCE": "UNDER_REVIEW", "DEFER": "UNDER_REVIEW", "ESCALATE": "UNDER_REVIEW",
}


class IssueDispositionBody(BaseModel):
    decision: str = Field(..., min_length=1, max_length=32,
                          description="|".join(ISSUE_DECISIONS))
    reason: str = Field(..., min_length=1, max_length=4000,
                        description="Why. Required; an unexplained decision is refused.")
    corrected_value: Optional[str] = Field(None, max_length=2000)


@router.post("/issues/{issue_id}/dispositions",
             summary="Record an analyst disposition on a finding")
async def post_issue_disposition(
    issue_id: str,
    body: IssueDispositionBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(EVIDENCE_ROLE)),
):
    from app.tefca_registry.rce import curation
    from app.tefca_registry.rce import models as m

    if not body.reason.strip():
        raise HTTPException(422, "A reason is required for every disposition.")
    decision = body.decision.strip().upper()
    if decision not in ISSUE_DECISIONS:
        raise HTTPException(422, f"decision must be one of {list(ISSUE_DECISIONS)}")
    issue_uuid = _as_uuid(issue_id)
    issue = await db.get(m.RceIssue, issue_uuid) if issue_uuid else None
    if issue is None:
        raise HTTPException(404, f"No issue {issue_id}")

    actor = getattr(user, "email", None) or "SYSTEM"
    actor_id = getattr(user, "id", None)
    apply = getattr(curation, "apply_disposition", None)
    try:
        if apply is not None:
            # curation.DISPOSITION_DECISIONS are the same words in lower case
            # (accept, reject, correct, confirm_existing, ...).
            result = await apply(db, issue.id, decision=decision.lower(),
                                 reason=body.reason.strip(), actor=actor,
                                 corrected_value=body.corrected_value, actor_id=actor_id)
            path = "curation.apply_disposition"
        else:
            result = await _fallback_apply_disposition(
                db, issue, decision=decision, reason=body.reason.strip(),
                actor=actor, corrected_value=body.corrected_value)
            path = "delivery_routes._fallback_apply_disposition"
    except curation.CorrectionRefused as exc:
        await db.rollback()
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        # Includes identifier_decisions.IdentifierAlreadyRegistered (409) and
        # the value/conflict refusals (422) from the decision gate.
        await db.rollback()
        from app.tefca_registry.rce import identifier_decisions as _idd
        if isinstance(exc, _idd.IdentifierAlreadyRegistered):
            raise HTTPException(409, str(exc))
        raise HTTPException(422, str(exc))
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A concurrent change was recorded for this record; "
                                 "refresh and review the current state.")

    await _audit(db, "analyst_disposition", user, request, {
        "issue_id": str(issue.id), "issue_code": issue.issue_code,
        "intake_id": str(issue.source_intake_id),
        "source_record_id": (str(issue.source_record_id)
                             if issue.source_record_id else None),
        "decision": decision, "reason": body.reason.strip(),
        "corrected_value_supplied": body.corrected_value is not None,
        "applied_by": path, "correlation_id": request_context.correlation_id(),
    })
    return {"issue_id": str(issue.id), "decision": decision,
            "applied_by": path, "result": result,
            "correlation": {"request_id": request_context.get("request_id")}}


async def _fallback_apply_disposition(db, issue, *, decision: str, reason: str,
                                      actor: str, corrected_value: Optional[str]
                                      ) -> Dict[str, Any]:
    """Used only while `curation.apply_disposition` (lane P) is absent.

    Maps the decision onto the Issue Ledger's existing resolution transition,
    applies an APPROVED correction when a corrected value is supplied, and
    re-derives the record's HELD state. It does NOT append an
    rce_disposition_events row or re-promote: those belong to lane P's
    implementation and the response says so (`fallback: true`).
    """
    from app.tefca_registry.rce import curation
    from app.tefca_registry.rce import models as m

    to_status = _FALLBACK_TRANSITION.get(decision.upper())
    if to_status is None:
        raise ValueError(f"decision must be one of {list(ISSUE_DECISIONS)}")
    if decision.upper() == "CORRECT" and corrected_value is None:
        raise ValueError("CORRECT requires corrected_value")
    if corrected_value is not None and to_status == "APPROVED":
        issue.suggested_value = corrected_value
        await db.flush()
    transition = await curation.transition_issue(
        db, issue.id, to_status=to_status, actor=actor, notes=reason)
    applied = None
    if corrected_value is not None and to_status == "APPROVED":
        applied = await curation.apply_correction(db, issue.id, actor=actor)
    hold = await curation.recompute_hold_status(db, issue.source_intake_id)
    return {"fallback": True, "transition": transition, "applied": applied,
            "hold_status": hold,
            "note": ("curation.apply_disposition is not available in this build; "
                     "the Issue Ledger transition was applied and the record's "
                     "hold state re-derived. No disposition event was appended.")}


# ═══ identifier decisions ════════════════════════════════════════════════════

class IdentifierDecisionBody(BaseModel):
    entity_id: str = Field(..., min_length=32, max_length=36)
    identifier_type: str = Field(..., min_length=1, max_length=32)
    decision: str = Field(..., min_length=1, max_length=32,
                          description="|".join(d for d in tm.IDENTIFIER_DECISIONS
                                               if d != "CONFLICT_RAISED"))
    reason: str = Field(..., min_length=1, max_length=4000)
    selected_value: Optional[str] = Field(None, max_length=500)
    issue_id: Optional[str] = Field(None, max_length=36,
                                    description="The rce_issues row this decides, if known.")
    source_record_id: Optional[str] = Field(None, max_length=36,
                                            description="The delivered line, if known.")


@router.post("/identifier-decisions",
             summary="Decide an identifier conflict raised by promotion")
async def post_identifier_decision(
    body: IdentifierDecisionBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(EVIDENCE_ROLE)),
):
    """Append the decision; then release the hold, re-promote and re-snapshot.

    Only CONFIRM_SUBMITTED changes the registry, and it does so inside
    `identifier_decisions.decide` with a version row and an audit row. The
    follow-up steps are reported individually: a re-promotion that could not
    run is a fact the analyst needs, not a reason to lose the decision.
    """
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import curation, identifier_decisions

    if not body.reason.strip():
        raise HTTPException(422, "A reason is required for every decision.")
    entity_uuid = _as_uuid(body.entity_id)
    if entity_uuid is None:
        raise HTTPException(422, "entity_id must be a uuid")
    # Validate the whole body (422) before looking anything up (404).
    decision = body.decision.strip().upper()
    if decision not in tm.IDENTIFIER_DECISIONS or decision == "CONFLICT_RAISED":
        raise HTTPException(422, "decision must be one of " + str(
            [d for d in tm.IDENTIFIER_DECISIONS if d != "CONFLICT_RAISED"]))
    for name in ("issue_id", "source_record_id"):
        if getattr(body, name) is not None and _as_uuid(getattr(body, name)) is None:
            raise HTTPException(422, f"{name} must be a uuid")
    entity = await db.get(reg.TefcaRegEntity, entity_uuid)
    if entity is None:
        raise HTTPException(404, f"No entity {body.entity_id}")

    actor = getattr(user, "email", None) or "SYSTEM"
    actor_id = getattr(user, "id", None)
    from sqlalchemy.exc import IntegrityError
    try:
        decided = await identifier_decisions.decide(
            db, entity_id=entity_uuid, identifier_type=body.identifier_type.strip().lower(),
            decision=decision, reason=body.reason.strip(),
            actor=actor, actor_id=actor_id, selected_value=body.selected_value,
            issue_id=_as_uuid(body.issue_id), source_record_id=_as_uuid(body.source_record_id))
    except identifier_decisions.IdentifierAlreadyRegistered as exc:
        await db.rollback()
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc))
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A concurrent decision was recorded for this "
                                 "identifier; refresh and review the current state.")

    event = decided["event"]
    intake_id = _as_uuid(event.get("intake_id"))
    follow_up: Dict[str, Any] = {"hold_recomputed": None, "repromotion": None,
                                 "snapshot": None}
    if decided.get("releases_hold") and intake_id is not None:
        try:
            follow_up["hold_recomputed"] = await curation.recompute_hold_status(db, intake_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hold recompute failed after identifier decision: %s",
                           type(exc).__name__, exc_info=True)
            await db.rollback()
            follow_up["hold_recomputed"] = {"error_class": type(exc).__name__}
        follow_up["repromotion"] = await _repromote(db, intake_id, actor, actor_id)
        follow_up["snapshot"] = await _try_persist_snapshot(db, intake_id, actor)
    else:
        await db.commit()

    await _audit(db, "identifier_decision", user, request, {
        "entity_id": str(entity_uuid), "identifier_type": body.identifier_type,
        "decision": decision, "reason": body.reason.strip(),
        "issue_id": body.issue_id, "source_record_id": body.source_record_id,
        "intake_id": str(intake_id) if intake_id else None,
        "registry_changed": decided.get("registry_changed"),
        "correlation_id": request_context.correlation_id(),
    })
    return {"decision": decided, "follow_up": follow_up,
            "correlation": {"request_id": request_context.get("request_id")}}


async def _repromote(db, intake_id, actor, actor_id) -> Dict[str, Any]:
    try:
        from app.tefca_registry.rce.promotion import promote_delivery
        result = await promote_delivery(db, intake_id, actor=actor, actor_id=actor_id)
        return {"ran": True, "result": result}
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        logger.warning("re-promotion after identifier decision failed: %s",
                       type(exc).__name__, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"ran": False, "error_class": type(exc).__name__}


async def _try_persist_snapshot(db, intake_id, actor) -> Dict[str, Any]:
    """Re-run reconciliation and persist a DISPOSITION-triggered snapshot.

    `reconciliation.persist_snapshot` needs the fresh `reconcile_delivery`
    result (the snapshot IS that result, hashed), so both are run here. A
    failure is reported, never hidden, and never loses the decision.
    """
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce import reconciliation

    job = await jobs.job_for_intake(db, intake_id)
    if job is None:
        return {"ran": False, "note": "no delivery job for this intake"}
    try:
        result = await reconciliation.reconcile_delivery(db, intake_id)
        snapshot = await reconciliation.persist_snapshot(
            db, intake_id, result, job_id=job.id, actor=actor, trigger="DISPOSITION")
        return {"ran": True, "passed": bool(result.get("passed")),
                "snapshot": snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot}
    except Exception as exc:  # noqa: BLE001
        logger.warning("snapshot after identifier decision failed: %s",
                       type(exc).__name__, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"ran": False, "error_class": type(exc).__name__}


# ── audit ────────────────────────────────────────────────────────────────────

async def _audit(db, action: str, user, request, metadata: Dict[str, Any]
                 ) -> None:
    """Record an act. Never the delivered data, only the act."""
    try:
        from app.tefca_registry import audit as reg_audit

        actor_id, actor_email = reg_audit.actor_of(user)
        reg_audit.record(db, action, actor_id=actor_id,
                         actor_email=actor_email,
                         ip_address=_client_ip(request), metadata=metadata)
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — an audit write must not fail the act
        logger.warning("could not audit %s: %s", action, type(exc).__name__)


# ═══ September 2026 — delta, presence, staleness, relationship history ═══════

@router.get("/deliveries/{intake_id}/delta",
            summary="Persisted per-id delta against the previous delivery")
async def delivery_delta_route(
    intake_id: str,
    classification: Optional[str] = Query(None, description="ADDED|MODIFIED|UNCHANGED|NOT_PRESENT"),
    material_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Counts at viewer floor; records (which carry delivered values in
    `field_changes`) only at the evidence floor."""
    from sqlalchemy import func, select
    from app.tefca_registry.rce import snapshot_models as sm

    intake = await _intake_or_404(db, intake_id)
    base = select(sm.RceDeliveryDelta).where(sm.RceDeliveryDelta.current_intake_id == intake.id)
    counts = dict((c, int(n)) for c, n in (await db.execute(
        select(sm.RceDeliveryDelta.classification, func.count())
        .where(sm.RceDeliveryDelta.current_intake_id == intake.id)
        .group_by(sm.RceDeliveryDelta.classification))).all())
    material = int((await db.execute(
        select(func.count()).select_from(sm.RceDeliveryDelta)
        .where(sm.RceDeliveryDelta.current_intake_id == intake.id,
               sm.RceDeliveryDelta.material.is_(True)))).scalar() or 0)
    previous_id = (await db.execute(
        select(sm.RceDeliveryDelta.previous_intake_id)
        .where(sm.RceDeliveryDelta.current_intake_id == intake.id).limit(1))).scalar_one_or_none()
    from app.tefca_registry.rce import snapshot_effects as se
    snap = await se.snapshot_state(db, intake.id)
    out: Dict[str, Any] = {
        "intake_id": str(intake.id),
        "previous_intake_id": str(previous_id) if previous_id else None,
        "state": "COMPARED" if previous_id else "BASELINE_OR_NOT_COMPUTED",
        # PENDING / FAILED / ROLLED_BACK deltas are evidence about the delivery,
        # not the current state; the status says which.
        "snapshot_status": snap.get("status"),
        "snapshot_effective": bool(snap.get("effective")),
        "counts": counts, "material_changes": material,
        "records": None,
        "availability": {"records": AVAILABLE if role_at_least(user, EVIDENCE_ROLE)
                         else REQUIRES_REVIEWER},
    }
    if not role_at_least(user, EVIDENCE_ROLE):
        return out
    if classification:
        base = base.where(sm.RceDeliveryDelta.classification == classification.upper())
    if material_only:
        base = base.where(sm.RceDeliveryDelta.material.is_(True))
    rows = (await db.execute(base.order_by(sm.RceDeliveryDelta.rce_org_oid)
                             .limit(limit).offset(offset))).scalars().all()
    out["records"] = [{
        "rce_org_oid": r.rce_org_oid, "classification": r.classification,
        "material": bool(r.material), "changed_fields": r.changed_fields,
        "field_changes": r.field_changes, "previous_sha256": r.previous_sha256,
        "current_sha256": r.current_sha256,
        "current_source_record_id": str(r.current_source_record_id) if r.current_source_record_id else None,
    } for r in rows]
    out["limit"], out["offset"] = limit, offset
    return out


@router.get("/deliveries/{intake_id}/stale-marks",
            summary="ARC results marked stale by this delivery (unresolved first)")
async def delivery_stale_marks_route(
    intake_id: str,
    include_resolved: bool = Query(False),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from sqlalchemy import func, select
    from app.tefca_registry.rce import snapshot_models as sm

    from app.tefca_registry.rce import snapshot_effects as se

    intake = await _intake_or_404(db, intake_id)
    snap = await se.snapshot_state(db, intake.id)
    resolved_ids = select(sm.ArcStaleMark.resolves_mark_id).where(
        sm.ArcStaleMark.kind == "RESOLVED", sm.ArcStaleMark.resolves_mark_id.isnot(None))
    q = select(sm.ArcStaleMark).where(sm.ArcStaleMark.intake_id == intake.id,
                                      sm.ArcStaleMark.kind == "STALE")
    if not snap.get("effective"):
        # Not a current view: a pending/failed/rolled-back snapshot's marks are
        # listed as evidence only, flagged, and never counted as current.
        rows = (await db.execute(q.order_by(sm.ArcStaleMark.marked_at.desc())
                                 .limit(limit).offset(offset))).scalars().all()
        return {"intake_id": str(intake.id), "snapshot_status": snap.get("status"),
                "snapshot_effective": False, "total": 0, "unresolved_by_reason": {},
                "evidence_only": [{"mark_id": str(r.id), "entity_id": str(r.entity_id),
                                   "review_id": r.review_id, "reason": r.reason}
                                  for r in rows],
                "limit": limit, "offset": offset}
    if not include_resolved:
        q = q.where(sm.ArcStaleMark.id.notin_(resolved_ids))
    total = int((await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0)
    by_reason = dict((r, int(n)) for r, n in (await db.execute(
        select(sm.ArcStaleMark.reason, func.count())
        .where(sm.ArcStaleMark.intake_id == intake.id, sm.ArcStaleMark.kind == "STALE",
               sm.ArcStaleMark.id.notin_(resolved_ids))
        .group_by(sm.ArcStaleMark.reason))).all())
    rows = (await db.execute(q.order_by(sm.ArcStaleMark.marked_at.desc())
                             .limit(limit).offset(offset))).scalars().all()
    return {
        "intake_id": str(intake.id), "snapshot_status": snap.get("status"),
        "snapshot_effective": True, "total": total, "unresolved_by_reason": by_reason,
        "marks": [{
            "mark_id": str(r.id), "entity_id": str(r.entity_id), "review_id": r.review_id,
            "reason": r.reason, "changed_fields": r.changed_fields,
            "marked_at": r.marked_at.isoformat() if r.marked_at else None, "actor": r.actor,
        } for r in rows],
        "limit": limit, "offset": offset,
    }


@router.get("/entities/{entity_id}/relationship-history",
            summary="Current and historical parent edges of one entity, with observations")
async def entity_relationship_history_route(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import relationship_history as rh
    from app.tefca_registry.rce import snapshot_effects

    eid = _as_uuid(entity_id)
    if eid is None:
        raise HTTPException(404, f"No entity {entity_id}")
    out = await rh.history_for_entity(db, eid)
    out["stale"] = (await snapshot_effects.stale_for_entities(db, [eid])).get(str(eid), [])
    return out


# ═══ snapshot governance (P1-1): state, approval, retry ══════════════════════

@router.get("/deliveries/{intake_id}/snapshot",
            summary="The delivery's snapshot chain state and approval gates")
async def delivery_snapshot_state_route(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import snapshot_effects as se

    intake = await _intake_or_404(db, intake_id)
    state = await se.snapshot_state(db, intake.id)
    gates = await se.approval_gates(db, intake.id)
    chain = await se.snapshot_chain(db, intake.id)
    current = await se.current_snapshot(db)
    return {
        "intake_id": str(intake.id), **state,
        "gates": gates["gates"], "approvable": bool(state.get("approvable") and gates["all"]),
        "reconciliation_snapshot_id": gates.get("reconciliation_snapshot_id"),
        "reconciliation_hash": gates.get("reconciliation_hash"),
        "is_current": bool(current.get("current")
                           and current["current"]["intake_id"] == str(intake.id)),
        "chain": [{"snapshot_id": str(r.id), "status": r.status,
                   "created_by": r.created_by, "created_at": r.created_at.isoformat(),
                   "approved_by": r.approved_by, "approved_role": r.approved_role,
                   "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                   "approval_ref": r.approval_ref,
                   "reconciliation_snapshot_id": (str(r.reconciliation_snapshot_id)
                                                  if r.reconciliation_snapshot_id else None),
                   "reconciliation_hash": r.reconciliation_hash, "build_sha": r.build_sha,
                   "request_id": r.request_id, "correlation_id": r.correlation_id,
                   "supersedes_snapshot_id": (str(r.supersedes_snapshot_id)
                                              if r.supersedes_snapshot_id else None)}
                  for r in chain],
        "approval_role": se.SNAPSHOT_APPROVAL_ROLE,
    }


@router.post("/deliveries/{intake_id}/snapshot/approve",
             summary="Approve the delivery's snapshot (QA lead or above; after reconciliation)")
async def delivery_snapshot_approve_route(
    intake_id: str,
    approval_ref: str = Form(..., min_length=3, max_length=120),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("qalead")),
):
    """Append-only: writes the APPROVED successor row, never edits. Refused
    (409) unless every gate holds: effects completed (PENDING tip), the
    persisted reconciliation snapshot PASSED with a hash after the effects,
    the live reconciliation passes now, and the approver is neither the
    system nor the delivery's registrant."""
    from app.tefca_registry import audit as reg_audit
    from app.tefca_registry.rce import snapshot_effects as se

    intake = await _intake_or_404(db, intake_id)
    try:
        result = await se.approve_delivery_snapshot(db, intake.id, user=user,
                                                    approval_ref=approval_ref, commit=False)
    except se.ApprovalRefused as exc:
        raise HTTPException(409, str(exc))
    actor_id, actor_email = reg_audit.actor_of(user)
    reg_audit.record(db, "source_snapshot_approved", None, actor_id=actor_id,
                     actor_email=actor_email,
                     metadata={"source_intake_id": str(intake.id),
                               "actor_role": getattr(user, "role", None),
                               "snapshot_id": result["snapshot_id"],
                               "reconciliation_snapshot_id": result["reconciliation_snapshot_id"],
                               "reconciliation_hash": result["reconciliation_hash"],
                               "approval_ref": approval_ref,
                               "build_sha": request_context.build_sha(),
                               "request_id": request_context.get("request_id")})
    await db.commit()
    return result


@router.post("/deliveries/{intake_id}/snapshot-effects/retry",
             summary="Idempotently re-apply the snapshot effects after a failure")
async def delivery_snapshot_effects_retry_route(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(DATA_OPERATIONS_ROLE)),
):
    """Re-runs delta / presence / stale marks / PENDING registration. Every
    step is idempotent, so a partial earlier run is completed, not doubled.
    A FAILED tip is superseded by PENDING on success; on failure another
    FAILED row is NOT appended (idempotent) and 409 is returned."""
    from app.tefca_registry.rce import snapshot_effects as se
    from app.tefca_registry.rce.stage_events import safe_failure_text

    intake = await _intake_or_404(db, intake_id)
    actor = getattr(user, "email", None) or "SYSTEM"
    try:
        result = await se.apply_snapshot_effects(db, intake.id, actor=actor)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        failure = await se.record_effects_failure(
            db, intake.id, actor=actor, error=safe_failure_text(exc, 1000))
        raise HTTPException(409, {"error": "snapshot_effects_failed",
                                  "detail": safe_failure_text(exc, 1000),
                                  "failure_record": failure})
    return result
