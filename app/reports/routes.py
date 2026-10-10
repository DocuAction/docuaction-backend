"""
Report API — generate, list, and download in HTML / PDF / CSV.

READ-ONLY BY CONSTRUCTION. None of these endpoints triggers a verification, a
source lookup, or a classification. `POST /generate` is a POST because it
CREATES a report artefact and its provenance record, not because it changes any
verification state.

RBAC (revised 2026-09-16, Decision 1 of the pre-merge review): a report's
CONTENT - generating one, and every download form of one (html/pdf/docx/csv/
package/artifact-history/artifact-download, and the SOW deliverable data) -
needs `reviewer`. `viewer` reaches only metadata and availability: the listing
(`GET ""`), one report's metadata (`GET /{report_id}`, with content fields
null and an `availability` reason below reviewer), release/workflow status,
the SOW family list (names and descriptions, no data), and engine health.
Reports carry entity names and review outcomes, so nothing that returns
delivered values is reachable below `reviewer`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role, require_role_audited

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["Reports"])


# ── one place that builds a download response ────────────────────────────────
#
# There were four, and they disagreed. The CSV route set a filename from a
# report id with no sanitising, the HTML route set no disposition at all, and
# only the artifact route said anything about caching. A header that matters for
# safety cannot be re-decided per route.

#: Characters allowed in a download filename. A report identifier reaches the
#: filename, and a header value carrying a quote, a newline or a semicolon is
#: header injection — the browser would read whatever followed as another
#: directive. Everything outside this set becomes an underscore.
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]")

#: Longer than any identifier this system issues, short enough that no
#: filesystem or header refuses it.
MAX_FILENAME = 120


#: AP-001: `GET /{report_id}` is a metadata/provenance endpoint (<2s gate,
#: workbook case AP-001 — 0.96s/0.88s normally, 3.95s on the 24,589-row job).
#: `report_data["dataset"]` carries whatever record-level arrays the generator
#: built to render annexes (e.g. `dataset["progress"]["annex_rows"]`) — full
#: rows are already reachable through the dedicated annex/CSV download route,
#: so this endpoint has no reason to also serialise and transfer them. Any
#: list this long gets summarised instead of transferred.
_DATASET_LIST_PREVIEW_LIMIT = 25


def _lightweight_dataset(value: Any) -> Any:
    """`value` with any list longer than `_DATASET_LIST_PREVIEW_LIMIT` replaced
    by a count summary. Recurses into dicts and lists; scalars pass through.
    """
    if isinstance(value, list):
        if len(value) > _DATASET_LIST_PREVIEW_LIMIT:
            return {"_truncated": True, "count": len(value),
                    "note": "Full rows are available from this report's annex/CSV download, "
                            "not this metadata endpoint."}
        return [_lightweight_dataset(item) for item in value]
    if isinstance(value, dict):
        return {key: _lightweight_dataset(item) for key, item in value.items()}
    return value


def safe_filename(stem: str, extension: str) -> str:
    """A filename that cannot escape the header it sits in.

    Not decoration: `stem` comes from a stored report identifier, and the
    disposition header is parsed by the browser. A leading dot is also stripped
    so a download cannot arrive as a hidden file.
    """
    cleaned = _FILENAME_SAFE.sub("_", stem or "report").lstrip(".")[:MAX_FILENAME]
    return f"{cleaned or 'report'}.{extension}"


def download_headers(filename: str, *, inline: bool = False,
                     sensitive: bool = True, extra=None) -> dict:
    """The headers every artifact download carries, and why.

    `Content-Disposition: attachment` — the browser saves the file instead of
    rendering it. For stored HTML that is the difference between a download and
    executing a report's markup on this origin.

    `X-Content-Type-Options: nosniff` — the browser must believe the declared
    type. Without it a file whose bytes look like HTML can be sniffed and
    rendered whatever the Content-Type says, which is the same problem again by
    a different route.

    `Cache-Control: no-store` for anything carrying Government content. A
    controlled export is served over an authenticated request; a copy left in a
    shared or proxy cache outlives the authorisation that produced it. This is
    per-response and deliberately does NOT touch the application's global cache
    policy. `sensitive=False` exists for genuinely public payloads; nothing in
    this router uses it today.
    """
    headers = {
        "Content-Disposition":
            f'{"inline" if inline else "attachment"}; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }
    if sensitive:
        headers["Cache-Control"] = "no-store, private"
        headers["Pragma"] = "no-cache"
    headers.update(extra or {})
    return headers


class GenerateReportRequest(BaseModel):
    report_type: str = Field(
        default="verification",
        description=("verification | verification_brief | executive | data_quality | "
                     "intake | delivery_processing | "
                     "retrospective_weekly (D3.1) | retrospective_final (D3.2) | "
                     "ongoing_biweekly (D4.1) | ongoing_quarterly (D4.2) | "
                     "priority_status (D5.1) | priority_quarterly (D5.2)"))
    review_cycle_id: Optional[str] = Field(
        default=None, description="Scope to one review cycle. Omit for all records.")
    format: str = Field(default="html", description="html | pdf | csv")
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description=("RCE types (delivery_processing, data_quality, intake) REQUIRE "
                     "job_id or intake_id here; snapshot_id pins a regeneration to one "
                     "persisted reconciliation snapshot. There is no newest-delivery "
                     "default."))
    #: SOW families: the reporting period the stratified list covers, and the
    #: human-authored change sections the contract asks for.
    period_start: Optional[str] = Field(default=None, description="ISO date")
    period_end: Optional[str] = Field(default=None, description="ISO date")
    suggested_changes: Optional[str] = Field(
        default=None, description="One suggested methodology/control change per line")
    implemented_changes: Optional[str] = Field(
        default=None, description="One implemented methodology/control change per line")
    #: Client-chosen token for ONE logical generation (QA-031). A repeat of the
    #: same token by the same principal returns the report the first call
    #: produced (`replayed: true`) instead of generating a second document —
    #: so a client that timed out can retry safely. Optional; absent means
    #: every call is a distinct generation, as before.
    idempotency_key: Optional[str] = Field(default=None, max_length=64, min_length=8,
                                           pattern=r"^[A-Za-z0-9._:-]+$")


#: MQA-2026-103 / RX-006 — the product rule for generating a draft deliverable.
#:
#: A report draft must name what it describes. Generating with NO scope used to
#: fall through to the deliberate "every ReviewRecord in the system" path, so a
#: single click under the DEVELOPMENT / TEST banner minted a system-wide draft
#: with nothing chosen. The route now refuses that shape with a machine code;
#: `generate_report()` itself is unchanged, so the system-wide figure remains
#: available to a caller that asks for it explicitly with a period.
#:
#: Accepted scopes: a review cycle; a delivery (`job_id` / `intake_id`); or,
#: for the SOW and global technical families, an explicit reporting period
#: (`period_start` AND `period_end`). The role floor is unchanged (reviewer,
#: Decision 1, 2026-09-16).
REPORT_SCOPE_REQUIRED = "REPORT_SCOPE_REQUIRED"


def require_explicit_scope(report_type: str, parameters: Dict[str, Any]) -> None:
    """Raise 422 REPORT_SCOPE_REQUIRED when a generate request names no scope."""
    p = parameters or {}
    if p.get("review_cycle_id") or p.get("job_id") or p.get("intake_id"):
        return
    if p.get("period_start") and p.get("period_end"):
        return
    raise HTTPException(422, detail={
        "error": (f"A {report_type} draft must name its scope: a review_cycle_id, a "
                  f"delivery (parameters.job_id or parameters.intake_id), or an "
                  f"explicit reporting period (period_start and period_end). "
                  f"Nothing was generated."),
        "code": REPORT_SCOPE_REQUIRED})


def parameter_error_http(exc) -> HTTPException:
    """A ReportParameterError as the HTTP answer: its own status (422 by
    default) and a body naming the machine code beside the message, so a
    client can act on `code` without parsing prose."""
    return HTTPException(
        getattr(exc, "status", 422),
        detail={"error": str(exc),
                "code": getattr(exc, "code", "DELIVERY_IDENTIFIER_REQUIRED")})


def _summary(result) -> Dict[str, Any]:
    snapshot = result["snapshot"]
    return {
        "report_id": result["report_id"],
        "report_type": result["report_type"],
        "stored_id": result["stored_id"],
        # RCE types: which persisted reconciliation snapshot the report rests
        # on, and the delivery linkage/audit written for it.
        "snapshot_id": result.get("snapshot_id"),
        "delivery_link": result.get("delivery_link"),
        # The durable renderings registered for a delivery-scoped report: one
        # entry per format with its hash, size, backend NAME and verified
        # download path. `durable` is false on the local (test/dev) backend.
        "artifacts": _artifacts_summary(result.get("artifacts")),
        "snapshot": snapshot.to_dict(),
        "accessibility": result["accessibility"],
        "formats": {
            "html": f"/api/reports/{result['report_id']}/html",
            "pdf": f"/api/reports/{result['report_id']}/pdf",
            "csv": f"/api/reports/{result['report_id']}/csv",
        },
    }


def _artifacts_summary(finalised: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The generator's finalisation record, shaped for a client: per-format
    summaries through `link_artifact_summary` (no locator), the backend name
    and durability, the PDF reason when no PDF was registered, and any
    registration errors — a silently missing durable copy is the failure this
    exists to surface."""
    if not finalised:
        return None
    from app.reports.data.artifact_registry import link_artifact_summary

    return {
        "items": [link_artifact_summary(a) for a in finalised.get("artifacts") or []],
        "storage_backend": finalised.get("storage_backend"),
        "durable": finalised.get("durable"),
        "storage_note": finalised.get("storage_note"),
        "pdf_unavailable_reason": finalised.get("pdf_unavailable_reason"),
        "errors": list(finalised.get("errors") or []),
    }


#: How long a retry waits for a still-running generation with the same key before answering 409. Kept under the
#: browser's 30 s request budget so the caller gets a stated reason instead of another timeout.
GENERATION_LOCK_WAIT_SECONDS = 20.0
GENERATION_LOCK_POLL_SECONDS = 0.5


@asynccontextmanager
async def _generation_key_lock(key, user_id):
    """Serialise generation per (principal, idempotency key) with a Postgres advisory lock on a DEDICATED
    connection (a session-level lock must outlive the request session's commits and pool checkouts)."""
    if not key:
        yield
        return
    from sqlalchemy import text

    from app.core.database import engine

    token = f"report-generate:{user_id}:{key}"
    async with engine.connect() as conn:
        deadline = time.monotonic() + GENERATION_LOCK_WAIT_SECONDS
        while True:
            got = (await conn.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"),
                                      {"k": token})).scalar()
            if got:
                break
            if time.monotonic() >= deadline:
                raise HTTPException(409, detail={
                    "code": "REPORT_GENERATION_IN_PROGRESS",
                    "message": ("A report for this same action is still being generated. Nothing new was started. "
                                "Check the Report Register in a minute, or press Generate draft again to pick it up."),
                })
            await asyncio.sleep(GENERATION_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": token})
            except Exception:  # noqa: BLE001 - the lock dies with the connection anyway
                pass


@router.post("/generate", summary="Generate a report from frozen verification results")
async def generate(
    request: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): the response can carry the report's
    # full dataset or rendered bytes, the same protected content the download
    # routes carry. Generating a report must not be a lower-privileged way to
    # read one.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    from app.reports.generator import (ReportGenerationError, ReportParameterError,
                                       generate_report)

    from app.core import request_context

    parameters = dict(request.parameters or {})
    for key in ("period_start", "period_end", "suggested_changes", "implemented_changes"):
        value = getattr(request, key)
        if value:
            parameters[key] = value
    if request.review_cycle_id and "review_cycle_id" not in parameters:
        parameters["review_cycle_id"] = request.review_cycle_id

    require_explicit_scope(request.report_type, parameters)

    # One generation per (principal, key) AT A TIME. The replay lookup below reads the audit row written when a
    # generation FINISHES, so a retry that arrives while the first is still running (the browser gave up at 30 s;
    # the server did not) used to find nothing and generate a second report. The lock is held on its own
    # connection for the whole request: the retry waits for the first to finish, then replays it; if the first is
    # still running after the wait it gets 409 REPORT_GENERATION_IN_PROGRESS, never a duplicate.
    async with _generation_key_lock(request.idempotency_key, getattr(user, "id", None)):
        if request.idempotency_key:
            replay = await _replay_for_key(db, request.idempotency_key, getattr(user, "id", None))
            if replay is not None:
                return replay
        return await _generate_locked(request, parameters, db, user)


async def _generate_locked(request, parameters, db, user):
    from app.reports.generator import (ReportGenerationError, ReportParameterError,
                                       generate_report)

    from app.core import request_context

    try:
        with request_context.bind(idempotency_key=request.idempotency_key):
            result = await generate_report(
                db,
                report_type=request.report_type,
                review_cycle_id=request.review_cycle_id,
                generated_by=getattr(user, "email", None) or "SYSTEM",
                generated_by_id=getattr(user, "id", None),
                query_parameters=parameters,
            )
    except ReportParameterError as exc:
        # The request named no delivery, a delivery that does not exist, or a
        # snapshot that is not the delivery's. 422/404/409 with a machine code.
        raise parameter_error_http(exc)
    except ReportGenerationError as exc:
        raise HTTPException(400, str(exc))

    if request.format == "csv":
        from app.reports.engine.csv_engine import to_bytes

        return Response(
            content=to_bytes(result["csv"]), media_type="text/csv",
            headers=download_headers(safe_filename(result["report_id"], "csv")))
    if request.format == "pdf":
        return await _pdf_response(result["html"], result["report_id"])
    if request.format == "html":
        return Response(content=result["html"], media_type="text/html",
                        headers=download_headers(
                            safe_filename(result["report_id"], "html")))
    return _summary(result)


@router.post("/generate/jobs", status_code=202,
             summary="Queue report generation as a durable background job")
async def generate_async(
    request: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
    # Same floor as the synchronous route (Decision 1): the completed job
    # answers with the report's full content, so queuing one needs the same
    # role as reading one.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """Queue a report generation instead of rendering it inline.

    `POST /generate` still exists unchanged for every existing caller. This is
    the same request body, answered in under two seconds with a receipt
    instead of the document: `POST /reports/{id}/html` etc. still serve the
    finished report once `GET /generate/jobs/{job_id}` reports SUCCEEDED, the
    same download routes the synchronous path already uses.

    Reuses `report_export_jobs` (added for the ONC review workbook export,
    Step #17) rather than a second job table or queue: the durable state,
    heartbeat, reaper and partial-unique-index concurrency guard it already
    has are exactly what a queued report generation needs too.
    """
    from app.reports.data.delivery_processing_data import resolve_delivery
    from app.reports.data.export_jobs import (ExportJobConflict, active_job,
                                              REPORT_GENERATION_EXPORT_TYPE_PREFIX,
                                              report_generation_identity, request_job)
    from app.reports.data.source_provenance import resolve_classification
    from app.reports.engine.template_engine import TEMPLATE_VERSION
    from app.reports.generator import ReportParameterError

    parameters = dict(request.parameters or {})
    for key in ("period_start", "period_end", "suggested_changes", "implemented_changes"):
        value = getattr(request, key)
        if value:
            parameters[key] = value
    if request.review_cycle_id and "review_cycle_id" not in parameters:
        parameters["review_cycle_id"] = request.review_cycle_id

    require_explicit_scope(request.report_type, parameters)

    # Async generation is delivery-scoped ONLY (migration review,
    # qa-evidence/2026-10-01-reporting-architecture/MIGRATION-REVIEW-20261001-report-generation-jobs.md):
    # `report_export_jobs.source_intake_id` stays NOT NULL on purpose, so a
    # review-cycle-only or period-only request -- valid for the synchronous
    # route, but naming no one delivery -- is refused here rather than
    # queued with no intake. A caller that needs one of those report types
    # still has the unchanged synchronous `POST /generate`.
    if not (parameters.get("job_id") or parameters.get("intake_id")):
        raise HTTPException(422, detail={
            "error": ("Asynchronous generation requires a delivery: "
                      "parameters.job_id or parameters.intake_id. A "
                      "review-cycle-only or period-only report is not yet "
                      "supported on this path -- use POST /generate."),
            "code": "ASYNC_GENERATION_REQUIRES_DELIVERY"})
    try:
        resolved = await resolve_delivery(
            db, job_id=parameters.get("job_id"), intake_id=parameters.get("intake_id"))
    except ReportParameterError as exc:
        raise parameter_error_http(exc)
    intake = resolved.get("intake")
    if intake is None:
        raise HTTPException(404, detail={
            "error": "The named delivery has no intake to scope this report to.",
            "code": "DELIVERY_NOT_FOUND"})
    intake_id = intake.id

    classification = await resolve_classification(db)
    requested_by = getattr(user, "email", None) or "SYSTEM"

    # Carries everything `generate_report()` needs to replay this exact
    # request from the worker (`run_report_generation_job`): the normal
    # `parameters` dict, plus `format` and `review_cycle_id`, which the
    # synchronous route keeps as separate call arguments rather than folding
    # into `parameters`.
    stored_parameters = {**parameters, "format": request.format,
                         "review_cycle_id": request.review_cycle_id}

    identity = report_generation_identity(
        report_type=request.report_type, format=request.format,
        parameters=parameters, review_cycle_id=request.review_cycle_id,
        template_version=TEMPLATE_VERSION, principal=requested_by,
        idempotency_key=request.idempotency_key)

    before = await active_job(db, identity)
    try:
        job = await request_job(
            db, identity=identity,
            export_type=f"{REPORT_GENERATION_EXPORT_TYPE_PREFIX}{request.report_type}",
            intake_id=intake_id, classification=classification,
            generator_version=TEMPLATE_VERSION, requested_by=requested_by,
            report_type=request.report_type, request_parameters=stored_parameters)
    except ExportJobConflict as exc:
        raise HTTPException(409, str(exc))

    reused = before is not None and str(before.id) == str(job.id)
    return {
        **job.to_dict(),
        "reused_existing_job": reused,
        "status_url": f"/api/reports/generate/jobs/{job.id}",
        # Short and fixed: the job table has no progress percentage to base a
        # longer estimate on, and a client should poll promptly after a 202.
        "retry_after": 2,
    }


@router.get("/generate/jobs/{job_id}",
            summary="Status of one queued report generation")
async def generate_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Where one queued generation got to. READS ONLY -- polling must never
    start work (same rule the ONC export's status route follows).

    Readable by whoever requested it, and by a program manager or
    administrator who supervise the queue. Everyone else gets 404, not 403,
    for the same enumeration-oracle reason the ONC export status route gives.
    """
    from app.core.security import ROLE_HIERARCHY
    from app.reports.data.export_jobs import get_job

    job = await get_job(db, job_id)
    if job is None:
        raise HTTPException(404, "No such report generation job.")

    email = (getattr(user, "email", None) or "").lower()
    role = (getattr(user, "role", "") or "").lower()
    supervises = ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY["program_manager"]
    if not supervises and (job.requested_by or "").lower() != email:
        raise HTTPException(404, "No such report generation job.")

    payload = job.to_dict()
    if job.state == job.STATE_SUCCEEDED and job.report_id:
        report_format = (job.request_parameters or {}).get("format", "html")
        payload["report"] = {
            "report_id": job.report_id,
            "formats": {
                "html": f"/api/reports/{job.report_id}/html",
                "pdf": f"/api/reports/{job.report_id}/pdf",
                "csv": f"/api/reports/{job.report_id}/csv",
            },
            "requested_format": report_format,
        }
    return payload


async def _replay_for_key(db, key: str, user_id) -> Optional[Dict[str, Any]]:
    """The report a previous call with this idempotency key produced, or None.

    Looked up on the `report_generated` audit row (the event of record, which
    carries the key in its details) for the SAME principal; a key is never
    honoured across users. The answer is the stored report's metadata and
    links — never a regeneration."""
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import ACTION_GENERATED, links_for_report
    from app.tefca_registry import models as reg

    stmt = (select(AuditLog)
            .where(AuditLog.action == ACTION_GENERATED,
                   AuditLog.details["idempotency_key"].as_string() == key)
            .order_by(AuditLog.created_at.desc()).limit(1))
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    audit = (await db.execute(stmt)).scalar_one_or_none()
    if audit is None:
        return None
    report_id = audit.resource_id
    row = (await db.execute(
        select(reg.ReviewReport).where(reg.ReviewReport.report_id == report_id)
    )).scalar_one_or_none()
    if row is None:
        return None
    data = row.report_data or {}
    links = await links_for_report(db, report_id)
    return {
        "report_id": report_id,
        "report_type": row.report_type,
        "stored_id": str(row.id),
        "replayed": True,
        "idempotency_key": key,
        "snapshot_id": (audit.details or {}).get("snapshot_id"),
        "delivery_link": {"written": True, "audit_id": str(audit.id),
                          "link_ids": [link["id"] for link in links],
                          "job_id": (audit.details or {}).get("job_id"),
                          "intake_id": (audit.details or {}).get("intake_id")},
        "artifacts": {"items": [link["artifact"] for link in links if link.get("artifact")]},
        "snapshot": data.get("snapshot") or {},
        "accessibility": (data.get("snapshot") or {}).get("accessibility"),
        "formats": {
            "html": f"/api/reports/{report_id}/html",
            "pdf": f"/api/reports/{report_id}/pdf",
            "csv": f"/api/reports/{report_id}/csv",
        },
    }


#: Hard wall-clock budget for one on-request PDF render. Generous for every
#: report CI renders in ~1 minute total, far short of the platform's ~230 s
#: connection drop that used to be the only "answer" a slow render gave.
PDF_RENDER_BUDGET_SECONDS = 120.0


async def _pdf_response(html: str, report_id: str) -> Response:
    """Render to PDF, or answer 503 with the reason.

    503 rather than 500: the engine's native libraries being absent is a
    service-configuration fact, not a bug in the request, and the message names
    exactly what is missing so an operator can act on it instead of filing a
    stack trace.

    The render runs in a worker thread (`asyncio.to_thread`), never on the
    event loop. WeasyPrint is CPU-bound and synchronous; called inline it
    stalls every other coroutine for the whole render. On DEV (2026-09-24,
    DA-ARC-2026-028: a 29 MB delivery_processing document) that stall held
    `/health` unanswered for about eight minutes and the container was
    recycled mid-render. The worker thread keeps the loop answering; it does
    not make the render faster, and a very large document can still exceed the
    platform's request timeout.
    """
    from app.reports.engine.pdf_engine import (
        PDFEngineUnavailable, pdf_available, render_pdf, unavailable_reason)

    # DEV-ONLY OPT-IN, 2026-10-02: this module's own docstring is "ONE ENGINE,
    # DELIBERATELY" — WeasyPrint only, no silent fallback, because a different
    # engine produces a structurally different, differently-accessible
    # document. That policy is preserved: WeasyPrint stays the unconditional
    # default below. The ONLY way to reach the Chromium/Playwright renderer is
    # an operator or test explicitly setting `PDF_ENGINE_DEV_OVERRIDE` to the
    # exact, loudly-named value below — never implied by WeasyPrint being
    # unavailable, never a silent substitution. Its own response header marks
    # it as untagged/non-production so a caller can never mistake it for the
    # real engine's output.
    import os

    from app.reports.engine.pdf_engine import (
        PDF_ENGINE_DEV_OVERRIDE_ENV, PDF_ENGINE_DEV_OVERRIDE_VALUE,
        PlaywrightEngineUnavailable, render_pdf_dev_chromium_UNTAGGED,
        render_pdf_dev_chromium_tagged)

    #: A second, separate opt-in value, same ENV var. Produces a single-
    #: document, tagged render (real /StructTreeRoot, confirmed non-trivial —
    #: see pdf_engine.inspect_pdf_tagging) instead of the split+merge
    #: UNTAGGED path above, which was confirmed to drop its structure tree
    #: entirely on merge. Still explicitly opt-in, still not the production
    #: default, still NOT claimed Section 508/PDF-UA conformant — a
    #: structure tree is a precondition, not a certificate.
    PDF_ENGINE_DEV_OVERRIDE_TAGGED_VALUE = "playwright_chromium_tagged_dev_only"

    if os.environ.get(PDF_ENGINE_DEV_OVERRIDE_ENV) == PDF_ENGINE_DEV_OVERRIDE_TAGGED_VALUE:
        try:
            pdf = await asyncio.wait_for(
                asyncio.to_thread(render_pdf_dev_chromium_tagged, html, title=report_id),
                timeout=PDF_RENDER_BUDGET_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(503, (
                f"PDF rendering exceeded the {PDF_RENDER_BUDGET_SECONDS:.0f}s "
                "budget (dev Chromium tagged engine)."))
        except PlaywrightEngineUnavailable as exc:
            raise HTTPException(503, str(exc))
        return Response(
            content=pdf, media_type="application/pdf",
            headers=download_headers(
                safe_filename(report_id, "pdf"),
                extra={"X-PDF-Engine": "playwright-chromium-dev-only-TAGGED",
                      "X-PDF-Accessibility": "STRUCTURE-TREE-PRESENT-NOT-508-EVALUATED"}))

    if os.environ.get(PDF_ENGINE_DEV_OVERRIDE_ENV) == PDF_ENGINE_DEV_OVERRIDE_VALUE:
        try:
            pdf = await asyncio.wait_for(
                asyncio.to_thread(render_pdf_dev_chromium_UNTAGGED, html, title=report_id),
                timeout=PDF_RENDER_BUDGET_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(503, (
                f"PDF rendering exceeded the {PDF_RENDER_BUDGET_SECONDS:.0f}s "
                "budget (dev Chromium engine)."))
        except PlaywrightEngineUnavailable as exc:
            raise HTTPException(503, str(exc))
        return Response(
            content=pdf, media_type="application/pdf",
            headers=download_headers(
                safe_filename(report_id, "pdf"),
                extra={"X-PDF-Engine": "playwright-chromium-dev-only-UNTAGGED",
                      "X-PDF-Accessibility": "NOT-EVALUATED-NOT-ACCESSIBLE"}))

    if not pdf_available():
        raise HTTPException(503, f"PDF generation is unavailable: {unavailable_reason()}")
    try:
        # HARD render budget (QA108-20260927-004): on DEV a per-request render
        # was observed answering NOTHING for 200+ seconds until the platform
        # dropped the connection - a hung download with no explanation. The
        # budget converts that into an honest 503 the caller can act on.
        # (asyncio.wait_for cannot stop the worker thread itself; the response
        # stops waiting, which is the part the caller experiences.)
        pdf = await asyncio.wait_for(
            asyncio.to_thread(render_pdf, html, title=report_id),
            timeout=PDF_RENDER_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(503, (
            f"PDF rendering exceeded the {PDF_RENDER_BUDGET_SECONDS:.0f}s budget "
            "for this document. The stored HTML and CSV downloads carry the same "
            "content; retry the PDF when the service is less loaded."))
    except PDFEngineUnavailable as exc:
        raise HTTPException(503, str(exc))
    return Response(
        content=pdf, media_type="application/pdf",
        headers=download_headers(safe_filename(report_id, "pdf")))


async def _stored(db, report_id: str, job_id: Optional[str] = None, *,
                  defer_html: bool = False):
    """The stored report, and — when the caller acts in a delivery context —
    proof that it is THAT delivery's report.

    `job_id` is the delivery the client is showing. A report whose frozen
    dataset describes a different delivery (or no delivery: a GLOBAL report) is
    refused with 409 `REPORT_DELIVERY_MISMATCH` and the refusal is audited.
    Authorising by report id alone is how one delivery's Reports tab served
    another delivery's CSV (QA-034).

    `defer_html=True` (QA108-20260927-005) skips loading `report_html` — 29 MB
    for the 24,589-record delivery report — for callers that only read the
    frozen dataset (CSV, DOCX). The deferred column must then never be touched.
    """
    from app.reports.data.delivery_report_links import (DELIVERY_MISMATCH,
                                                        stored_delivery)
    from app.tefca_registry import models as reg

    stmt = select(reg.ReviewReport).where(reg.ReviewReport.report_id == report_id)
    if defer_html:
        from sqlalchemy.orm import defer

        stmt = stmt.options(defer(reg.ReviewReport.report_html))
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"No report exists with id {report_id}")
    if job_id:
        described = stored_delivery(row)
        if described["job_id"] != str(job_id):
            from app.reports.data.delivery_report_links import record_report_download_failure

            await record_report_download_failure(
                db, report_id=report_id, report_type=row.report_type, fmt="any",
                actor="unknown", code=DELIVERY_MISMATCH,
                reason="stored report describes a different delivery than the one named",
                extra={"requested_job_id": str(job_id),
                       "stored_job_id": described["job_id"]})
            raise HTTPException(409, {
                "error": (f"Report {report_id} does not belong to delivery job "
                          f"{job_id}; it describes "
                          f"{described['job_id'] or 'no delivery (global scope)'}. "
                          f"Nothing was served."),
                "code": DELIVERY_MISMATCH})
    return row


async def _stored_html_light(db, report_id: str, job_id: Optional[str] = None,
                             *, defer_html: bool = False):
    """(row, file_stem) for the HTML/PDF download routes, with `report_data`
    NEVER loaded (QA108-20260927-005).

    The full `_stored()` loads the whole entity: for the 24,589-record
    delivery report that is a 29 MB `report_html` PLUS a multi-megabyte
    frozen dataset the download route never reads — measured >44 s to first
    byte on DEV. The scope check and file stem need exactly three small
    values from `report_data`, so they come from a JSONB path projection and
    the entity is loaded with the column deferred. Same 404/409 semantics,
    same audit on refusal, same bytes served."""
    from sqlalchemy.orm import defer

    from app.reports.data.delivery_report_links import DELIVERY_MISMATCH
    from app.tefca_registry import models as reg

    R = reg.ReviewReport
    meta = (await db.execute(select(
        R.report_data["dataset"]["delivery"]["job_id"].label("described_job"),
        R.report_data["dataset"]["branding"]["contract_number"].label("contract_branded"),
        R.report_data["dataset"]["contract_number"].label("contract_plain"),
    ).where(R.report_id == report_id))).first()
    opts = [defer(R.report_data)]
    if defer_html:
        # The caller will serve REGISTERED artifact bytes (QA108-20260927-013's
        # canonical surface), so the stored column must not be dragged along.
        opts.append(defer(R.report_html))
    row = (await db.execute(
        select(R).options(*opts).where(R.report_id == report_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"No report exists with id {report_id}")
    if job_id:
        described_job = str(meta.described_job) if meta and meta.described_job else None
        if described_job != str(job_id):
            from app.reports.data.delivery_report_links import record_report_download_failure

            await record_report_download_failure(
                db, report_id=report_id, report_type=row.report_type, fmt="any",
                actor="unknown", code=DELIVERY_MISMATCH,
                reason="stored report describes a different delivery than the one named",
                extra={"requested_job_id": str(job_id),
                       "stored_job_id": described_job})
            raise HTTPException(409, {
                "error": (f"Report {report_id} does not belong to delivery job "
                          f"{job_id}; it describes "
                          f"{described_job or 'no delivery (global scope)'}. "
                          f"Nothing was served."),
                "code": DELIVERY_MISMATCH})
    contract = (meta.contract_branded or meta.contract_plain) if meta else None
    return row, _stem_for(row, contract=contract)


JOB_SCOPE_QUERY = Query(
    None, description=("The delivery job the client is acting for. When given, the "
                       "report must describe exactly that delivery or the request "
                       "is refused (409 REPORT_DELIVERY_MISMATCH)."))


@router.get("", summary="List generated reports")
async def list_reports(
    limit: int = Query(50, ge=1, le=500),
    report_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """QA108-20260927: this listing used to SELECT full ReviewReport entities,
    which drags every row's `report_data` (the frozen dataset — megabytes for
    a 24,589-record delivery report) and `report_html` out of Postgres to
    serve a page of metadata; 50 rows took 8–13 s on DEV. The query now
    projects exactly the columns the listing returns plus the three SMALL
    JSONB subtrees it reads (`snapshot`, `release`, the contract number) —
    the response shape is unchanged, byte for byte."""
    from app.reports.data.release import current_release
    from app.tefca_registry import models as reg

    R = reg.ReviewReport
    stmt = select(
        R.report_id, R.report_type, R.generated_at, R.generated_by,
        R.period_start, R.period_end,
        R.report_data["snapshot"].label("snapshot"),
        R.report_data["release"].label("release"),
        R.report_data["dataset"]["branding"]["contract_number"].label("contract_branded"),
        R.report_data["dataset"]["contract_number"].label("contract_plain"),
        # The named population (delivery / review cycle) of a scoped report —
        # small identifier subtrees, so the register can say what a report
        # describes without loading its dataset.
        R.report_data["dataset"]["delivery"].label("delivery"),
        R.report_data["dataset"]["scope"].label("scope"),
    ).order_by(R.generated_at.desc())
    if report_type:
        stmt = stmt.where(R.report_type == report_type)
    rows = (await db.execute(stmt.limit(limit))).all()

    from app.reports.branding import deliverable_filename_stem
    from app.reports.generator import document_marking_for

    items = []
    for r in rows:
        snapshot = r.snapshot or {}
        meta = _deliverable_meta(r.report_type)
        contract = r.contract_branded or r.contract_plain
        if meta.get("deliverable") and contract:
            file_stem = deliverable_filename_stem(
                contract_number=contract, task=meta.get("task"),
                deliverable=meta.get("deliverable"), kind=meta.get("kind"),
                period_start=str(r.period_start) if r.period_start else None,
                period_end=str(r.period_end) if r.period_end else None,
                report_id=r.report_id)
        else:
            file_stem = r.report_id
        items.append({
            "report_id": r.report_id,
            "report_type": r.report_type,
            "generated_at": r.generated_at,
            # The STORED principal, not the copy inside the snapshot. An auditor
            # asking the API who generated a report must get the column the
            # application wrote, or a populated row reads back as anonymous.
            "generated_by": str(r.generated_by) if r.generated_by else None,
            "snapshot": snapshot,
            "generated_by_email": snapshot.get("generated_by"),
            "period_start": r.period_start,
            "period_end": r.period_end,
            # current_release reads only the `release` key — hand it exactly
            # that, wrapped the way it expects.
            "release": current_release({"release": r.release} if r.release else {}),
            "file_stem": file_stem,
            "formats": supported_formats(r.report_type),
            "source": _source_summary(r.delivery, r.scope),
            "document_marking": document_marking_for(snapshot.get("data_classification")),
            **meta,
        })
    return {"items": items}


def _source_summary(delivery, scope) -> Optional[Dict[str, Any]]:
    """Which delivery / review cycle a stored report describes (identifiers
    and the delivery label only), or None for a global report."""
    delivery = delivery or {}
    scope = scope or {}
    job_id = delivery.get("job_id") or scope.get("job_id")
    intake_id = delivery.get("intake_id") or scope.get("intake_id")
    cycle = scope.get("review_cycle_id")
    if not (job_id or intake_id or cycle):
        return None
    return {"job_id": job_id, "intake_id": intake_id,
            "delivery_label": delivery.get("delivery_label"),
            "review_cycle_id": cycle, "sample_id": scope.get("sample_id")}


def _deliverable_meta(report_type: str) -> Dict[str, Any]:
    """Which contract deliverable a report type produces, if any."""
    from app.reports.data.sow_report_data import SOW_REPORT_TYPES

    meta = SOW_REPORT_TYPES.get(report_type)
    if not meta:
        return {"deliverable": None, "task": None, "title": None}
    return {"deliverable": meta["deliverable"], "task": meta["task"],
            "title": meta["title"], "cadence": meta["cadence"],
            # One-word family label for file names ("Weekly"); the cadence
            # sentence is for people, not paths.
            "kind": meta.get("kind")}


_UNSET = object()


def _stem_for(row, contract=_UNSET) -> str:
    """Traceable file stem for one stored report (contract, task, deliverable,
    cadence, period, report id). Falls back to the bare report id for report
    families that are not contract deliverables.

    `contract` may be supplied by a caller that loaded the report row with
    `report_data` DEFERRED (QA108-20260927-005): touching the attribute on such
    a row would ask the ORM for a lazy async load it cannot perform. The
    override carries the same value, extracted by a cheap JSONB path query."""
    from app.reports.branding import deliverable_filename_stem

    if contract is _UNSET:
        data = row.report_data or {}
        dataset = data.get("dataset") or {}
        branding = dataset.get("branding") or {}
        contract = branding.get("contract_number") or dataset.get("contract_number")
    meta = _deliverable_meta(row.report_type)
    if not (meta.get("deliverable") and contract):
        return row.report_id
    return deliverable_filename_stem(
        contract_number=contract, task=meta.get("task"),
        deliverable=meta.get("deliverable"), kind=meta.get("kind"),
        period_start=str(row.period_start) if row.period_start else None,
        period_end=str(row.period_end) if row.period_end else None,
        report_id=row.report_id)


def supported_formats(report_type: str) -> Dict[str, Any]:
    """The download formats a report type supports, stated as data so no client has to guess.

    html, csv, pdf and the package (ZIP) exist for every report type. DOCX exists ONLY for the contract
    deliverables (`SOW_REPORT_TYPES`); a delivery evidence report (`delivery_processing`) has no editable Word
    form, and `GET /{report_id}/docx` answers 404 for it. A client must not offer DOCX where `available` is false.
    """
    from app.reports.data.sow_report_data import SOW_REPORT_TYPES

    contract = report_type in SOW_REPORT_TYPES
    return {
        "html": {"available": True},
        "csv": {"available": True},
        "pdf": {"available": True,
                "note": "Rendered by the report engine; refused with a stated reason only where its native "
                        "libraries are missing."},
        "package": {"available": True, "note": "ZIP of the available formats with a README and SHA-256 manifest."},
        "docx": {"available": contract,
                 "reason": None if contract else (
                     f"'{report_type}' is a delivery evidence report, not a contract deliverable; "
                     f"it has no editable Word form.")},
    }


def _listing_extras(r) -> Dict[str, Any]:
    """Deliverable, period and PM release state for one stored report."""
    from app.reports.data.release import current_release

    from app.reports.generator import document_marking_for

    data = r.report_data or {}
    snapshot = data.get("snapshot", {})
    dataset = data.get("dataset") or {}
    return {
        "generated_by_email": snapshot.get("generated_by"),
        "period_start": r.period_start,
        "period_end": r.period_end,
        "release": current_release(data),
        "file_stem": _stem_for(r),
        "formats": supported_formats(r.report_type),
        "source": _source_summary(dataset.get("delivery"), dataset.get("scope")),
        "document_marking": document_marking_for(snapshot.get("data_classification")),
        **_deliverable_meta(r.report_type),
    }


@router.get("/by-delivery/{job_id}",
            summary="Reports generated for one delivery job")
async def reports_by_delivery(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """Every rce_delivery_report_links row for the job, newest first, each
    joined to the artifact it names (hash, type, size, backend name, verified
    download path), plus the same rows folded to one entry per report.

    The link is the deterministic answer to "which report was produced from
    which snapshot of this delivery"; nothing is inferred from timestamps.

    AUTHORISATION. `reviewer` is the floor: a listing of hashes and download
    paths for Government-derived documents is the doorway to the files, and it
    sits with the role that fetches them. The platform's roles are GLOBAL —
    `app.core.tenant` scopes only the enterprise document tables, and no RCE or
    report table carries a tenant or delivery ownership column — so a reviewer
    who may read one delivery's reports may read every delivery's. There is no
    per-delivery scoping to enforce here and none is invented; the role floor
    plus the audit row on every download is the control.
    """
    import uuid as _uuid

    from app.reports.data.artifact_registry import storage_durability
    from app.reports.data.delivery_report_links import (group_links_by_report,
                                                        links_for_job,
                                                        quarantined_links_for_job)

    try:
        key = _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(422, {"error": f"{job_id!r} is not a valid job id",
                                  "code": "DELIVERY_IDENTIFIER_INVALID"})
    links = await links_for_job(db, key)
    quarantined = await quarantined_links_for_job(db, key)
    artifacts = [link["artifact"] for link in links if link.get("artifact")]
    backends = sorted({a["storage_backend"] for a in artifacts})
    storage = (storage_durability(backends[0]) if len(backends) == 1
               else {"storage_backend": backends or None,
                     "durable": bool(artifacts) and all(a["durable"] for a in artifacts),
                     "storage_note": ("No artifacts registered for this job." if not artifacts
                                      else "Artifacts span more than one backend.")})
    return {"job_id": str(key), "count": len(links), "items": links,
            "reports": group_links_by_report(links),
            "artifacts": artifacts, "storage": storage,
            # Links whose stored report describes another delivery are listed
            # here by id and reason only — never as downloadable items.
            "quarantined": quarantined,
            "scope": {"model": "global_roles", "minimum_role": "reviewer",
                      "per_delivery_scoping": True,
                      "note": ("Every item is verified against the stored report's "
                               "own delivery; downloads with ?job_id= are refused "
                               "on mismatch (409 REPORT_DELIVERY_MISMATCH).")}}


@router.get("/{report_id}", summary="Report metadata and snapshot provenance")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Metadata and availability to everyone at `viewer`; the report's
    content (dataset, delivery links, artifact listing) only from `reviewer`.

    AUTHORISATION (Decision 1, pre-merge directive, 2026-09-16). A viewer may
    see that a report exists, its type, when it was generated and by whom, its
    provenance/classification, and its release status — none of that is
    delivery evidence. The `dataset` block is the report's actual content
    (record-level values); `delivery_link(s)` and `artifacts` name and can
    reach the stored bytes. Those three are null with an explicit
    `availability` reason below `reviewer`, exactly as the delivery-detail
    endpoint already does for the same floor (`delivery_routes.py`).
    """
    from app.core.security import role_at_least

    row = await _stored(db, report_id)
    data = row.report_data or {}
    base = {
        "report_id": row.report_id,
        "report_type": row.report_type,
        "generated_at": row.generated_at,
        "generated_by": str(row.generated_by) if row.generated_by else None,
        "snapshot": data.get("snapshot", {}),
        **_listing_extras(row),
    }
    if not role_at_least(user, "reviewer"):
        return {
            **base,
            "dataset": None,
            "delivery_link": None,
            "delivery_links": [],
            "artifacts": [],
            "availability": {
                "dataset": "requires_role:reviewer",
                "delivery_links": "requires_role:reviewer",
                "artifacts": "requires_role:reviewer",
            },
        }

    from app.reports.data.artifact_registry import (artifacts_for_report,
                                                    link_artifact_summary)
    from app.reports.data.delivery_report_links import links_for_report

    links = await links_for_report(db, report_id)
    artifacts = [link_artifact_summary(a) for a in await artifacts_for_report(db, report_id)]
    return {
        **base,
        "dataset": _lightweight_dataset(data.get("dataset", {})),
        # Present only when the report was generated for a named delivery and
        # a reconciliation snapshot existed to link it to. One link per stored
        # rendering; `delivery_link` is the first (the HTML) for callers that
        # read a single object, `delivery_links` is all of them.
        "delivery_link": links[0] if links else None,
        "delivery_links": links,
        # Every registered rendering of this report (all formats, all
        # versions) with hash, size, backend name and verified download path.
        "artifacts": artifacts,
        "availability": {"dataset": "available", "delivery_links": "available",
                         "artifacts": "available"},
    }


async def _audit_download(db, row, fmt: str, user, **extra) -> None:
    """One report_downloaded audit row per served download. Never raises."""
    from app.reports.data.delivery_report_links import record_report_download

    await record_report_download(
        db, report_id=row.report_id, report_type=row.report_type, fmt=fmt,
        actor=getattr(user, "email", None) or "SYSTEM",
        actor_id=getattr(user, "id", None), extra=extra or None)


# ── PM release control ───────────────────────────────────────────────────────
#
# QA-approved data -> report draft -> PM review -> ready for delivery ->
# download / email-ready package. The programme manager keeps external release:
# nothing here transmits a deliverable, and no status says "sent".


class ReleaseRequest(BaseModel):
    action: str = Field(description="PM_REVIEWED | READY_FOR_DELIVERY | RETURNED_TO_DRAFT")
    note: str = Field(default="", max_length=2000)
    #: PROPOSAL (O-01). Only consulted when ENABLE_RELEASE_READ_ACK is on.
    acknowledge_read: bool = False


@router.get("/{report_id}/release", summary="Release status and decision history")
async def get_release(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.reports.data.release import current_release

    row = await _stored(db, report_id)
    return {"report_id": report_id, "release": current_release(row.report_data)}


@router.post("/{report_id}/release", summary="Record a PM release decision")
async def post_release(
    report_id: str,
    request: ReleaseRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("program_manager")),
):
    """Append one release decision. `program_manager` and above only.

    The snapshot, dataset and stored HTML are untouched; only the `release`
    block is written. Every decision is also written to the platform audit
    trail so "who released this report" is a query, not a search.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.database import AuditLog
    from app.reports.data.release import (ReleaseTransitionError,
                                          apply_transition, current_release)

    row = await _stored(db, report_id)
    actor = getattr(user, "email", None) or "SYSTEM"

    # A3 PROPOSAL (O-01), both controls default OFF: generator != releaser and
    # an explicit read acknowledgement. Refusals carry a machine code.
    from app.tefca_registry import qa_controls
    separation = qa_controls.flag("ENABLE_RELEASE_GENERATOR_SEPARATION")
    read_ack = qa_controls.flag("ENABLE_RELEASE_READ_ACK")
    if separation or read_ack:
        snapshot = ((row.report_data or {}).get("snapshot") or {})
        denial = qa_controls.release_denial(
            action=request.action, generator_id=getattr(row, "generated_by", None),
            generator_email=snapshot.get("generated_by"),
            actor_id=getattr(user, "id", None), actor_email=actor,
            acknowledge_read=request.acknowledge_read,
            separation_enabled=separation, read_ack_enabled=read_ack)
        if denial:
            await qa_controls.audit_denial(
                db, action="report_release_denied", code=denial, user=user,
                metadata={"report_id": report_id, "release_action": request.action})
            raise HTTPException(**qa_controls.denial_http(
                f"report release refused ({denial}); see "
                f"docs/A3-qa-independence-and-deadline-controls.md", denial))
    try:
        new_data, entry = apply_transition(
            row.report_data, action=request.action, actor=actor, note=request.note)
    except ReleaseTransitionError as exc:
        raise HTTPException(409, str(exc), headers={
            "X-Denial-Code": qa_controls.RELEASE_TRANSITION_REFUSED})
    if read_ack and entry["status"] == "READY_FOR_DELIVERY":
        entry["read_acknowledged"] = True

    row.report_data = new_data
    flag_modified(row, "report_data")
    db.add(AuditLog(
        # The authenticated principal, so the Audit Trail's User column names
        # the programme manager who released the report rather than "System".
        user_id=getattr(user, "id", None),
        action=f"REPORT_RELEASE_{entry['status']}",
        event_type="reporting",
        outcome="success",
        resource_type="report",
        resource_id=report_id,
        details={"actor": actor, "note": entry["note"], "report_type": row.report_type},
    ))
    await db.commit()
    return {"report_id": report_id, "release": current_release(new_data)}


@router.get("/{report_id}/package", summary="Download the email-ready deliverable package")
async def get_package(
    report_id: str,
    job_id: Optional[str] = JOB_SCOPE_QUERY,
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): the package is the report's raw
    # evidence in every format at once.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """ZIP of the HTML, the CSV, the PDF where available, a README and a manifest with SHA-256 of every
    member. Each member is the REGISTERED artifact's verified bytes when one exists (so it equals the file
    downloaded separately); only a report from before artifact registration is assembled from the stored column.
    Nothing is transmitted."""
    from app.reports.data.release import build_package, current_release
    from app.reports.engine.pdf_engine import pdf_available, render_pdf, unavailable_reason

    row = await _stored(db, report_id, job_id)
    data = row.report_data or {}
    snapshot = data.get("snapshot") or {}
    dataset = dict(data.get("dataset") or {})

    # CANONICAL SURFACE RULE (QA108-20260927-013): when a registered artifact exists, its verified bytes ARE the
    # deliverable. The package used to re-render the PDF from the stored HTML on every request, so the PDF it shipped
    # was a different file from the registered one (same content, different bytes) and its manifest hash could never
    # equal the hash of the PDF a recipient downloaded separately. Registered bytes first; re-render only when none exist.
    reg_html = await _registered_bytes(db, report_id, "text/html")
    reg_csv = await _registered_bytes(db, report_id, "text/csv")
    reg_pdf = await _registered_bytes(db, report_id, "application/pdf")
    package_html = reg_html["content"].decode("utf-8") if reg_html else row.report_html
    if not package_html:
        raise HTTPException(404, f"Report {report_id} has no stored HTML.")

    csv_text = (reg_csv["content"].decode("utf-8-sig") if reg_csv else csv_for_stored_report(row))
    stem = _stem_for(row)
    docx_bytes = None
    try:
        docx_bytes = await run_in_threadpool(docx_for_stored_report, row)
    except Exception as exc:  # noqa: BLE001  — the package still ships without it
        logger.warning("DOCX omitted from package %s: %s", report_id, exc)

    pdf_bytes = None
    pdf_reason = None
    if reg_pdf is not None:
        pdf_bytes = reg_pdf["content"]
    elif pdf_available():
        try:
            pdf_bytes = await run_in_threadpool(render_pdf, package_html, title=stem)
        except Exception as exc:  # noqa: BLE001
            pdf_reason = str(exc)
    else:
        pdf_reason = unavailable_reason()

    package = build_package(
        report_id=report_id, html=package_html, csv_text=csv_text,
        pdf_bytes=pdf_bytes, snapshot=snapshot, release=current_release(data),
        deliverable=_deliverable_meta(row.report_type),
        pdf_unavailable_reason=pdf_reason, docx_bytes=docx_bytes, stem=stem)
    await _audit_download(db, row, "package", user,
                          pdf_included=pdf_bytes is not None,
                          docx_included=docx_bytes is not None)
    return Response(
        content=package["bytes"], media_type="application/zip",
        headers=download_headers(
            safe_filename(stem, "zip"),
            extra={"X-Data-Classification": snapshot.get("data_classification") or "DEVELOPMENT_TEST",
                   "X-Release-Status": package["manifest"]["release"].get("status", "DRAFT")}))


async def _registered_bytes(db, report_id: str, content_type: str):
    """The verified REGISTERED artifact for (report, content_type), or None.

    CANONICAL SURFACE RULE (QA108-20260927-013): for a report with a registered
    artifact, the REGISTERED bytes are the deliverable — they were hashed at
    finalisation and are re-hashed here. The stored column is the source the
    artifact was made from, and later governed writes (e.g. a PM release stamp
    on `report_data`) mean the two can legitimately drift; the registered file
    is what the recipient received.

    Returns the verified dict, or None when this report has no registered
    artifact of that type (legacy reports — the caller serves the stored
    column) or the registered bytes are GONE from the store (the caller falls
    back to the stored column and the fallback is audited: availability, with
    the divergence on the record instead of silent). An INTEGRITY failure —
    bytes present but hashing differently — refuses, exactly like the artifact
    download route: a silently altered deliverable is worse than no download.
    """
    from app.core.storage.artifact_store import ArtifactNotFound
    from app.reports.data.artifact_registry import retrieve_artifact
    from app.reports.data.delivery_report_links import record_report_download_failure

    try:
        return await retrieve_artifact(db, report_id, content_type=content_type)
    except LookupError:
        return None  # never registered — the stored column is all there is
    except ArtifactNotFound as exc:
        await record_report_download_failure(
            db, report_id=report_id, report_type=None,
            fmt=f"artifact-fallback:{content_type}", actor="SYSTEM",
            code="ARTIFACT_MISSING_FALLBACK",
            reason=("registered bytes are gone from the store; serving the "
                    "stored column instead"),
            extra={"content_type": content_type, "store_error": str(exc)[:200]})
        return None
    except RuntimeError as exc:
        # Integrity failure: registered bytes no longer hash to the record.
        await record_report_download_failure(
            db, report_id=report_id, report_type=None,
            fmt=f"artifact:{content_type}", actor="SYSTEM",
            code="ARTIFACT_INTEGRITY", reason=str(exc)[:300],
            extra={"content_type": content_type})
        raise HTTPException(500, "Artifact integrity check failed; nothing was served.")


@router.get("/{report_id}/html", summary="Download a report as HTML")
async def get_report_html(
    report_id: str,
    job_id: Optional[str] = JOB_SCOPE_QUERY,
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): identical bytes to the artifact
    # download route, which is already reviewer-gated; a legacy alias must not
    # reopen the same door at a lower floor.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """The report's HTML, byte for byte — REGISTERED artifact first.

    Never re-rendered. A report is what the recipient received; regenerating it
    on read would quietly rewrite history the moment the underlying entities
    changed. When a registered HTML artifact exists its verified bytes are
    served (the canonical deliverable, and no multi-megabyte column read —
    QA108-20260927-005/013); a report from before artifact registration falls
    back to the stored column, exactly as before.
    """
    registered = await _registered_bytes(db, report_id, "text/html")
    row, stem = await _stored_html_light(db, report_id, job_id,
                                         defer_html=registered is not None)
    if registered is not None:
        await _audit_download(db, row, "html", user, job_id=job_id,
                              source="artifact-registry",
                              artifact_version=(registered.get("artifact") or {}).get("artifact_version"),
                              sha256=(registered.get("artifact") or {}).get("rendered_sha256"))
        headers = download_headers(safe_filename(stem, "html"))
        headers["X-Report-Source"] = "artifact-registry"
        return Response(content=registered["content"], media_type="text/html",
                        headers=headers)
    if not row.report_html:
        raise HTTPException(404, f"Report {report_id} has no stored HTML.")
    await _audit_download(db, row, "html", user, job_id=job_id,
                          source="stored-column")
    # Served as an attachment, not rendered. A stored report is a document the
    # recipient received; rendering it on this origin would execute whatever
    # markup it contains with the application's own privileges.
    headers = download_headers(safe_filename(stem, "html"))
    headers["X-Report-Source"] = "stored-column"
    return Response(content=row.report_html, media_type="text/html",
                    headers=headers)


@router.get("/{report_id}/pdf", summary="Download a report as PDF")
async def get_report_pdf(
    report_id: str,
    job_id: Optional[str] = JOB_SCOPE_QUERY,
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): same document as /html, rendered.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """The report's PDF — REGISTERED artifact first, render as fallback.

    Generation registers the PDF rendering at finalisation whenever the engine
    is available, so the normal download is a verified byte serve, not a
    20-second render on the request path (QA108-20260927-004). A report whose
    PDF was never registered (legacy, or generated while the engine was
    absent) still renders from the stored HTML inside the existing budget.
    """
    registered = await _registered_bytes(db, report_id, "application/pdf")
    row, stem = await _stored_html_light(db, report_id, job_id,
                                         defer_html=registered is not None)
    if registered is not None:
        await _audit_download(db, row, "pdf", user, job_id=job_id,
                              source="artifact-registry",
                              artifact_version=(registered.get("artifact") or {}).get("artifact_version"),
                              sha256=(registered.get("artifact") or {}).get("rendered_sha256"))
        headers = download_headers(safe_filename(stem, "pdf"))
        headers["X-Report-Source"] = "artifact-registry"
        return Response(content=registered["content"], media_type="application/pdf",
                        headers=headers)
    if not row.report_html:
        raise HTTPException(404, f"Report {report_id} has no stored HTML to render.")
    response = await _pdf_response(row.report_html, stem)
    await _audit_download(db, row, "pdf", user, job_id=job_id,
                          source="rendered-on-request")
    return response


@router.post("/{report_id}/artifacts/backfill",
             summary="Register the PDF rendering of an already-stored report, once")
async def backfill_pdf_artifact(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    # `qalead`: this is an operator action that spends a full render, not a
    # read; reviewers keep downloading exactly as before.
    user=Depends(require_role_audited("qalead", resource_type="report")),
):
    """Give a report generated BEFORE PDF registration its durable PDF
    (QA108-20260927-004).

    Idempotent: a report that already has a registered PDF is answered from
    the registry without rendering (`backfilled: False`). Otherwise the stored
    HTML — the same bytes /html serves — is rendered ONCE, off the event loop
    and inside the same budget the on-request path uses, then registered with
    the provenance the stored snapshot already carries. Nothing is
    regenerated, no dataset is re-queried, no report row is modified; the only
    write is the new artifact registration, and it is audited.
    """
    from dataclasses import fields as dc_fields

    from app.reports.data.artifact_registry import public_artifact
    from app.reports.data.delivery_report_artifacts import (
        PDF, finalize_report_renderings)
    from app.reports.data.delivery_report_links import (
        record_report_download, record_report_download_failure)
    from app.reports.data.report_snapshot import ReportSnapshot
    from app.reports.engine.pdf_engine import pdf_available, unavailable_reason
    from app.tefca_registry import models as reg

    # The id names a storage key (artifact_key -> filesystem path on the local
    # backend), so the URL value is never used past this point: it must match
    # the report-id shape, and every later call uses the STORED row's own id.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,40}", report_id or ""):
        raise HTTPException(404, "No report exists with that id")
    row = (await db.execute(
        select(reg.ReviewReport).where(reg.ReviewReport.report_id == report_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"No report exists with id {report_id}")
    report_id = str(row.report_id)

    existing = await _registered_bytes(db, report_id, PDF)
    if existing is not None:
        return {"report_id": report_id, "backfilled": False,
                "artifact": public_artifact(existing.get("artifact") or {}),
                "note": "A registered PDF already exists; nothing was rendered."}

    if not pdf_available():
        raise HTTPException(503, f"PDF generation is unavailable: {unavailable_reason()}")
    if not row.report_html:
        raise HTTPException(404, f"Report {report_id} has no stored HTML to render.")

    data = row.report_data or {}
    snap_dict = dict(data.get("snapshot") or {})
    allowed = {f.name for f in dc_fields(ReportSnapshot)}
    snapshot = ReportSnapshot(**{k: v for k, v in snap_dict.items() if k in allowed},
                              **({"report_id": report_id} if "report_id" not in snap_dict else {}),
                              **({"report_type": row.report_type} if "report_type" not in snap_dict else {}),
                              **({"generation_timestamp": ""} if "generation_timestamp" not in snap_dict else {}))
    html_artifact = await _registered_bytes(db, report_id, "text/html")

    actor = getattr(user, "email", None) or "SYSTEM"
    try:
        out = await asyncio.wait_for(
            finalize_report_renderings(
                db, report_id=report_id, report_type=row.report_type,
                html=row.report_html, csv_text=None, snapshot=snapshot,
                dataset=data.get("dataset") or {}, generated_by=actor,
                include_csv=False, include_pdf=True,
                html_artifact=(html_artifact or {}).get("artifact")),
            timeout=PDF_RENDER_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        await record_report_download_failure(
            db, report_id=report_id, report_type=row.report_type,
            fmt="artifact-backfill:pdf", actor=actor, actor_id=getattr(user, "id", None),
            code="PDF_RENDER_BUDGET_EXCEEDED",
            reason=f"backfill render exceeded the {PDF_RENDER_BUDGET_SECONDS:.0f}s budget")
        raise HTTPException(503, (
            f"PDF rendering exceeded the {PDF_RENDER_BUDGET_SECONDS:.0f}s budget "
            "for this document; nothing was registered."))

    pdf_row = out.get("pdf")
    if pdf_row is None:
        reason = out.get("pdf_unavailable_reason") or "; ".join(out.get("errors") or []) or "unknown"
        await record_report_download_failure(
            db, report_id=report_id, report_type=row.report_type,
            fmt="artifact-backfill:pdf", actor=actor, actor_id=getattr(user, "id", None),
            code="PDF_BACKFILL_FAILED", reason=str(reason)[:300])
        raise HTTPException(503, f"The PDF could not be registered: {str(reason)[:300]}")

    await record_report_download(
        db, report_id=report_id, report_type=row.report_type,
        fmt="artifact-backfill:pdf", actor=actor, actor_id=getattr(user, "id", None),
        extra={"artifact_version": pdf_row.get("artifact_version"),
               "sha256": pdf_row.get("rendered_sha256"),
               "storage_backend": out.get("storage_backend")})
    return {"report_id": report_id, "backfilled": True,
            "artifact": public_artifact(pdf_row),
            "storage_backend": out.get("storage_backend"), "durable": out.get("durable")}


def docx_for_stored_report(row) -> Optional[bytes]:
    """The editable (DOCX) copy of a stored contract deliverable, built from
    the STORED dataset — never from a fresh query — so it matches the HTML,
    PDF and CSV of the same report id. None for report families that have no
    DOCX form."""
    from app.reports.data.release import current_release
    from app.reports.data.sow_report_data import SOW_REPORT_TYPES
    from app.reports.engine.docx_engine import docx_available, render_sow_docx

    if row.report_type not in SOW_REPORT_TYPES or not docx_available():
        return None
    data = row.report_data or {}
    dataset = dict(data.get("dataset") or {})
    if not dataset:
        return None
    snapshot = data.get("snapshot") or {}
    branding = dataset.get("branding") or {}
    return render_sow_docx(dataset, snapshot, current_release(data), branding)


@router.get("/{report_id}/docx", summary="Download the editable (DOCX) deliverable")
async def get_report_docx(
    report_id: str,
    job_id: Optional[str] = JOB_SCOPE_QUERY,
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): built from the same stored dataset.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """Word document with real styles (Title, Heading 1–3), accessible tables
    with repeating header rows, a TOC field, header/footer with contract and
    page numbers, and document properties. Built from the stored dataset."""
    from app.reports.engine.docx_engine import DOCX_CONTENT_TYPE, docx_available

    row = await _stored(db, report_id, job_id, defer_html=True)
    if not docx_available():
        raise HTTPException(503, "DOCX generation is unavailable: python-docx is not installed.")
    if not (row.report_data or {}).get("dataset"):
        raise HTTPException(404, f"Report {report_id} has no stored dataset.")
    docx_bytes = await run_in_threadpool(docx_for_stored_report, row)
    if docx_bytes is None:
        supported = ", ".join(k for k, v in supported_formats(row.report_type).items() if v["available"])
        raise HTTPException(
            404, f"Report type '{row.report_type}' has no DOCX form (FORMAT_NOT_AVAILABLE). "
                 f"Supported formats for this report: {supported}.")
    await _audit_download(db, row, "docx", user, job_id=job_id)
    return Response(
        content=docx_bytes, media_type=DOCX_CONTENT_TYPE,
        headers=download_headers(
            safe_filename(_stem_for(row), "docx"),
            extra={"X-Data-Classification":
                   ((row.report_data or {}).get("snapshot") or {}).get("data_classification")
                   or "DEVELOPMENT_TEST"}))


def csv_for_stored_report(row) -> str:
    """The CSV of one STORED report, regenerated from its stored dataset.

    SOW deliverables get the stratified Participant/Subparticipant list; the
    technical reports get their figure data. One helper, used by the standalone
    CSV download and by the package, so the two can never disagree about what
    the CSV of a report is.
    """
    from app.reports.engine.csv_engine import (delivery_processing_to_csv,
                                               report_to_csv, sow_report_to_csv)
    from app.reports.generator import SOW_TYPES

    data = row.report_data or {}
    dataset = dict(data.get("dataset") or {})
    snapshot = data.get("snapshot") or {}
    generated_at = snapshot.get("generation_timestamp", "")
    if row.report_type in SOW_TYPES and dataset.get("progress"):
        # The controlled annex the progress deliverables were issued with.
        from app.reports.data.sow_progress_data import annex_csv, annex_provenance
        from app.reports.generator import document_marking_for, synthetic_note_for

        return annex_csv(dataset["progress"], report_id=row.report_id,
                         marking=document_marking_for(snapshot.get("data_classification")),
                         note=synthetic_note_for(snapshot.get("data_classification")),
                         provenance=annex_provenance(dataset, snapshot))
    if row.report_type in SOW_TYPES:
        return sow_report_to_csv(dataset, row.report_id, generated_at)
    if row.report_type == "delivery_processing":
        # Dispositions plus the labelled annexes (findings, coverage, review records), from the STORED dataset. The
        # rule-set version comes from the stored snapshot so these bytes equal the registered CSV artifact.
        return delivery_processing_to_csv(dataset, row.report_id, generated_at,
                                          rule_set_version=snapshot.get("b1_b4_rule_version"))

    # Charts were excluded from the stored payload (they are presentation, not
    # data), so rebuild them from the stored numbers for the figure sections.
    from app.reports.charts import build_all_charts

    try:
        dataset["chart_list"] = build_all_charts(
            dataset.get("buckets") or {}, dataset.get("coverage") or {},
            dataset.get("dimensions") or {}, dataset.get("entity_status") or {},
            dataset.get("qhins") or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("csv export: charts not rebuilt for %s: %s", row.report_id, exc)
        dataset["chart_list"] = []
    return report_to_csv(dataset, row.report_id, generated_at)


@router.get("/{report_id}/csv", summary="Download a report's data as CSV")
async def get_report_csv(
    report_id: str,
    job_id: Optional[str] = JOB_SCOPE_QUERY,
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): regenerated from the same stored
    # dataset as HTML/PDF/DOCX.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """Regenerated from the STORED dataset, not from a fresh query.

    The numbers therefore match the stored report exactly, including when the
    live data has since moved on. For a SOW deliverable this is the stratified
    entity list; for a technical report it is the figure data.
    """
    from app.reports.engine.csv_engine import to_bytes

    row = await _stored(db, report_id, job_id, defer_html=True)
    if not (row.report_data or {}).get("dataset"):
        raise HTTPException(404, f"Report {report_id} has no stored dataset.")
    await _audit_download(db, row, "csv", user, job_id=job_id)
    return Response(
        content=to_bytes(csv_for_stored_report(row)), media_type="text/csv",
        headers=download_headers(safe_filename(_stem_for(row), "csv")))


@router.get("/health/engine", summary="Report engine health (PDF availability)")
async def engine_health(user=Depends(require_role("viewer"))):
    from app.reports.engine.pdf_engine import engine_info

    return {"pdf": engine_info()}


# ═══ SOW deliverable families ════════════════════════════════════════════════
#
# The contract's report families, served from the canonical path. Until Phase
# 7.5 these existed only under /api/tefca/reports/*, which reads `tefca_reviews`
# with one-off SQL, takes `review.status` as the discrepancy category, and
# consults neither the canonical evidence selector nor the reportability gate.
#
# These endpoints return DATA, not a rendered document. The contract's families
# are stratified lists and aggregates; a caller that wants a rendered artifact
# generates one and finalises it through the artifact store. Keeping the data
# model separate from the template and the renderer is what let the equivalence
# comparison run at all.

SOW_DELIVERABLES = {
    "D3.1": ("retrospective_weekly", "Task 3 weekly progress report"),
    "D3.1M": ("retro_monthly", "Task 3 monthly progress report"),
    "D3.2": ("retrospective_final", "Task 3 final retrospective report"),
    "D4.1": ("ongoing_biweekly", "Task 4 bi-weekly progress report"),
    "D4.2": ("ongoing_quarterly", "Task 4 quarterly report"),
    "D5.1": ("priority_status", "Task 5 priority review status report"),
    "D5.2": ("priority_quarterly", "Task 5 quarterly report"),
    "D6.1": ("closeout_framework", "Task 6 contract closeout report framework"),
    "D6.2": ("closeout_presentation", "Task 6 closeout educational presentation"),
}


@router.get("/sow", summary="The contract's report families and what serves them")
async def list_sow_families(user=Depends(require_role("viewer"))):
    """What each deliverable is, and where it comes from."""
    return {
        "deliverables": [
            {"deliverable": key, "method": method, "description": description,
             "endpoint": f"/api/reports/sow/{key}"}
            for key, (method, description) in sorted(SOW_DELIVERABLES.items())
        ],
        "data_source": "canonical Report Data Service",
        "note": ("Categories follow the Government's terminology from the "
                 "solicitation. B1-B4 is AGT internal shorthand and is not a "
                 "TEFCA, ONC, ASTP, RCE or Sequoia classification."),
    }


@router.get("/sow/{deliverable}", summary="Data for one SOW deliverable")
async def sow_deliverable(
    deliverable: str,
    review_cycle_id: Optional[str] = Query(None),
    case_id: Optional[str] = Query(None, description="D5.1 only"),
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): this IS the stratified evidence,
    # unlike `/sow` (the family list, names and descriptions only, no data).
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    from app.reports.data.sow_report_data import SowReportDataService

    key = deliverable.strip().upper()
    if key not in SOW_DELIVERABLES:
        raise HTTPException(
            404, f"{deliverable!r} is not a contract deliverable. "
                 f"Known: {', '.join(sorted(SOW_DELIVERABLES))}")

    method_name, _ = SOW_DELIVERABLES[key]
    service = SowReportDataService(db)
    method = getattr(service, method_name)
    data = (await method(case_id=case_id, review_cycle_id=review_cycle_id)
            if key == "D5.1" else await method(review_cycle_id=review_cycle_id))

    from app.reports.data.source_provenance import authoritative_source_provenance

    provenance = await authoritative_source_provenance(db)
    data["source_provenance"] = provenance.to_dict()
    data["data_classification"] = provenance.data_classification
    # Every development-facing payload says so. The banner is on rendered
    # documents; this is the same statement for a caller reading JSON.
    if provenance.data_classification != "GOVERNMENT":
        data["development_notice"] = (
            "DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY — "
            "NOT ONC FINDINGS. The Government source file has not been imported.")
    return data


@router.get("/artifacts/{report_id}", summary="Stored artifact versions for a report")
async def artifact_history(
    report_id: str,
    content_type: str = Query("text/html"),
    db: AsyncSession = Depends(get_db),
    # `reviewer` (Decision 1, 2026-09-16): matches `/artifacts/{id}/download`,
    # which is already reviewer-gated; the version listing names hashes and
    # sizes of Government-derived documents and is the doorway to them.
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """Every finalised version, oldest first. Nothing is ever replaced."""
    from app.reports.data.artifact_registry import (artifact_versions,
                                                     public_artifact)

    versions = await artifact_versions(db, report_id, content_type=content_type)
    if not versions:
        raise HTTPException(404, f"No stored artifact for {report_id}")
    return {"report_id": report_id, "content_type": content_type,
            "versions": [public_artifact(v) for v in versions],
            "count": len(versions)}


@router.get("/artifacts/{report_id}/download",
            summary="Download a finalised artifact, integrity-verified")
async def artifact_download(
    report_id: str,
    content_type: str = Query("text/html"),
    version: Optional[int] = Query(None, ge=1, le=2_147_483_647,
                                   description="Omit for the latest"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role_audited("reviewer", resource_type="report")),
):
    """Fetch stored bytes and re-hash them before handing them over.

    A stored hash nobody recomputes is a claim. If the bytes no longer match
    what was registered this raises rather than serving them — a silently
    altered deliverable is worse than a failed download.

    `report_id` is normally the report identifier (DA-ARC-YYYY-NNN) selected
    with `content_type` and `version`; it may also be the REGISTRY ROW ID a
    delivery link carries (a UUID — no report id is one), in which case the
    query parameters are ignored and that exact row is served.

    OUTCOMES, ALL AUDITED. Served → `report_downloaded`. Registered but the
    bytes are gone from the store (deleted blob or file) → 410 with
    `code: ARTIFACT_MISSING`, never a 500: the registry row is the record that
    the artefact existed, and "gone" is a fact about the store, not a fault in
    the request. Never registered → 404 `ARTIFACT_NOT_REGISTERED`. Bytes
    present but hash mismatch → 500, refused (the platform's handler scrubs
    5xx bodies; the code lives in the audit row). Every non-served outcome
    writes `report_download_failed`.

    The 4xx bodies are built with the platform's standard error shape
    (`error`, `code`, `request_id`) directly, because the global HTTPException
    handler assigns `code` from the status alone and would flatten a machine
    code carried in `detail` into prose.

    AUTHORISATION. `reviewer` floor. Roles are global (no per-delivery
    scoping exists on the platform); see `reports_by_delivery`.
    """
    import uuid as _uuid

    from app.core.error_handler import create_error_response
    from app.core.storage.artifact_store import ArtifactNotFound
    from app.reports.data.artifact_registry import (ARTIFACT_SUFFIXES,
                                                     ReportArtifact,
                                                     retrieve_artifact,
                                                     retrieve_artifact_by_id)
    from app.reports.data.delivery_report_links import (
        record_report_download, record_report_download_failure)

    actor = getattr(user, "email", None) or "SYSTEM"
    actor_id = getattr(user, "id", None)
    by_row_id = None
    try:
        by_row_id = _uuid.UUID(report_id)
    except ValueError:
        pass

    async def _failed(code: str, reason: str, registered=None) -> Dict[str, Any]:
        """Write the `report_download_failed` audit row; return the body."""
        known = registered or {}
        await record_report_download_failure(
            db, report_id=known.get("report_id") or report_id,
            report_type=known.get("report_type"),
            fmt=f"artifact:{ARTIFACT_SUFFIXES.get(known.get('content_type') or content_type, 'bin')}",
            actor=actor, actor_id=actor_id, code=code, reason=reason,
            extra={"artifact_row_id": str(by_row_id) if by_row_id else None,
                   "content_type": content_type, "version": version,
                   "artifact_version": known.get("artifact_version"),
                   "rendered_sha256": known.get("rendered_sha256")})
        return {"code": code, "error": reason,
                "report_id": known.get("report_id") or report_id}

    registered = None
    try:
        if by_row_id is not None:
            row = await db.get(ReportArtifact, by_row_id)
            registered = row.to_dict() if row is not None else None
            got = await retrieve_artifact_by_id(db, by_row_id)
        else:
            got = await retrieve_artifact(db, report_id, content_type=content_type,
                                          version=version)
    except LookupError as exc:
        body = await _failed("ARTIFACT_NOT_REGISTERED", str(exc))
        return create_error_response(404, error=body["error"], code=body["code"],
                                     extra={"report_id": body["report_id"]})
    except ArtifactNotFound:
        # Registered, hashed, and no longer in the store. The row stands as the
        # record that it existed; the bytes do not. 410 Gone, not 500.
        body = await _failed(
            "ARTIFACT_MISSING",
            "The artifact is registered but its stored bytes are missing from the "
            "artifact store. The registry row is retained as the record of issue.",
            registered)
        return create_error_response(410, error=body["error"], code=body["code"],
                                     extra={"report_id": body["report_id"]})
    except RuntimeError as exc:
        logger.error("artifact integrity failure for %s: %s", report_id, exc)
        raise HTTPException(500, await _failed(
            "ARTIFACT_INTEGRITY_FAILURE",
            "The stored bytes do not hash to the registered SHA-256; the artifact "
            "is refused rather than served.", registered))

    artifact = got["artifact"]
    served_type = artifact.get("content_type") or content_type
    ext = ARTIFACT_SUFFIXES.get(served_type, "bin")

    await record_report_download(
        db, report_id=artifact.get("report_id") or report_id,
        report_type=artifact.get("report_type"),
        fmt=f"artifact:{ext}", actor=actor, actor_id=actor_id,
        extra={"artifact_row_id": artifact.get("id"),
               "artifact_version": artifact.get("artifact_version"),
               "rendered_sha256": artifact.get("rendered_sha256"),
               "verified": bool(got.get("verified"))})
    # The stored content type, not one the caller asked for. `content_type` is a
    # query parameter and selects WHICH artifact to fetch; echoing it back as the
    # response type would let a caller name the type their browser sees.
    return Response(
        content=got["content"], media_type=served_type,
        headers=download_headers(
            safe_filename(f"{artifact.get('report_id') or report_id}"
                          f"-v{artifact['artifact_version']}", ext),
            extra={
                "X-Artifact-SHA256": artifact["rendered_sha256"],
                "X-Artifact-Version": str(artifact["artifact_version"]),
                "X-Artifact-Verified": "true" if got.get("verified") else "false",
                "X-Data-Classification": artifact["data_classification"],
            }))


# ── the controlled Excel export ──────────────────────────────────────────────
#
# DocuAction is the system of record. The workbook is an EXPORT: a snapshot,
# taken under a classification the caller does not choose, registered with its
# own hash, and downloaded through the same integrity-verified path as every
# other artefact. Nothing that happens in the spreadsheet comes back.


class WorkbookExportRequest(BaseModel):
    intake_id: Optional[str] = Field(
        default=None,
        description="Which delivery to export. Omit for the current one.")
    preview: bool = Field(
        default=False,
        description="Ten rows per sheet, for checking shape. Not an artefact.")

    # NOTE: there is deliberately no `classification` field. Classification is
    # a property of the DATA, read from the data-state model, and a caller who
    # could set it could label a Government export as development test — or the
    # reverse, which is worse.


PREVIEW_ROWS = 10

#: Repeated here rather than imported so the status route stays a pure
#: read; a test asserts it equals the engine's own constant.
XLSX_CONTENT_TYPE_LITERAL = ("application/vnd.openxmlformats-officedocument"
                             ".spreadsheetml.sheet")

#: One export product today. Named so the job identity and the audit trail
#: agree on what was asked for.
EXPORT_TYPE = "onc_review_workbook"


async def _export_classification(db) -> str:
    """What this data IS, resolved against the intake — never from a caller.

    Step #17 gave this route its own resolver because `source_provenance` was
    classifying from the session-free fallback. Step #17C corrected that at the
    source, so there is one resolver again and the export uses it: a workbook
    and a report of the same population must not be able to disagree about what
    the population is.
    """
    from app.reports.data.source_provenance import resolve_classification

    return await resolve_classification(db)


@router.post("/exports/onc-review-workbook",
             summary="Generate the controlled ONC data review workbook")
async def export_onc_review_workbook(
    request: WorkbookExportRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("qalead")),
):
    """Build, register and hash one review workbook.

    `qalead`, not `viewer` or `contributor`. Reading a report inside DocuAction
    keeps the platform's controls around it; a workbook is a file that leaves,
    and once it has left, RBAC, the audit trail and the immutable source are all
    behind it. The floor is therefore the role that already carries independent
    responsibility for what may be relied upon.

    The bytes are NOT returned here. They are stored, registered and fetched
    from `/artifacts/{report_id}/download`, which re-hashes before serving — so
    there is one download path and it is the verified one.
    """
    from app.reports.data.export_audit import (ACTION_REQUESTED, ACTION_REUSED,
                                               record_export_event)
    from app.reports.data.export_jobs import (ExportJobConflict, active_job,
                                              job_identity, request_job)
    from app.reports.data.onc_review_workbook import (WORKBOOK_VERSION,
                                                      WorkbookRefused,
                                                      build_workbook_dataset)
    from app.reports.engine.xlsx_engine import (XLSX_CONTENT_TYPE,
                                                XLSX_ENGINE_VERSION,
                                                render_workbook)

    generated_by = getattr(user, "email", None) or "SYSTEM"
    classification = await _export_classification(db)

    try:
        dataset = await build_workbook_dataset(
            db, intake_id=request.intake_id,
            classification=classification,
            generated_by=generated_by)
    except WorkbookRefused as exc:
        # The export refused itself — the delivered schema did not match the
        # contract, or there is no delivery to export. That is a 409, not a
        # 500: nothing failed, the export declined to assert something untrue.
        raise HTTPException(409, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    if request.preview:
        return _workbook_preview(dataset, render_workbook)

    # The full export is QUEUED, not produced here. Step #17 measured the
    # delivered population at roughly seven and a half minutes; a request that
    # waited for it would be killed by a gateway long before it finished, and a
    # user who refreshed would have no way to find out whether the first attempt
    # was still running. What comes back is a receipt.
    identity = job_identity(
        intake_id=dataset["intake_id"], workbook_version=WORKBOOK_VERSION,
        engine_version=XLSX_ENGINE_VERSION,
        classification=dataset["classification"],
        export_type=EXPORT_TYPE)

    before = await active_job(db, identity)
    try:
        job = await request_job(
            db, identity=identity, export_type=EXPORT_TYPE,
            intake_id=dataset["intake_id"],
            classification=dataset["classification"],
            generator_version=(f"workbook {WORKBOOK_VERSION} / "
                               f"engine {XLSX_ENGINE_VERSION}"),
            requested_by=generated_by)
    except ExportJobConflict as exc:
        raise HTTPException(409, str(exc))

    reused = before is not None and str(before.id) == str(job.id)
    await record_export_event(
        db, action=ACTION_REUSED if reused else ACTION_REQUESTED,
        actor=generated_by, job_id=str(job.id),
        detail=("An export for this delivery was already in flight; the "
                "existing job was returned." if reused else
                "A controlled export was requested."),
        extra={"delivery": dataset["delivery_label"],
               "intake_id": dataset["intake_id"],
               "classification": dataset["classification"],
               "generator_version": job.generator_version})

    return {
        **job.to_dict(),
        "reused_existing_job": reused,
        "delivery": dataset["delivery_label"],
        "workbook_version": WORKBOOK_VERSION,
        "engine_version": XLSX_ENGINE_VERSION,
        "file_type": "Excel workbook (.xlsx)",
        "rows_per_sheet": {name: len(sheet["rows"])
                           for name, sheet in dataset["sheets"].items()},
        "reconciliation": dataset["reconciliation"],
        "status_url": f"/api/reports/exports/jobs/{job.id}",
    }


def _workbook_preview(dataset, render) -> Response:
    """Ten rows a sheet, returned directly and registered nowhere.

    A preview exists to answer "is this the right shape" before someone waits
    for the whole delivery. It is therefore NOT an artefact: it has no registry
    row, no version and no hash of record, and it says so on its own face — the
    identifier ends in -PREVIEW and every sheet carries a note. A truncated file
    that could be mistaken for the export would be worse than no preview.
    """
    from app.reports.engine.xlsx_engine import XLSX_CONTENT_TYPE

    banner = (f"PREVIEW — the first {PREVIEW_ROWS} rows of each sheet. "
              f"This file is not a registered artefact and is not the export.")
    trimmed = dict(dataset)
    trimmed["report_id"] = f"{dataset['report_id']}-PREVIEW"
    trimmed["sheets"] = {
        name: {**sheet,
               "rows": sheet["rows"][:PREVIEW_ROWS],
               "note": f"{banner} {sheet.get('note') or ''}".strip()}
        for name, sheet in dataset["sheets"].items()}

    return Response(
        content=render(trimmed), media_type=XLSX_CONTENT_TYPE,
        headers=download_headers(
            safe_filename(trimmed["report_id"], "xlsx"),
            extra={"X-Artifact-Preview": "true",
                   "X-Data-Classification": dataset["classification"]}))


@router.get("/exports/jobs/{job_id}",
            summary="Status of one controlled export job")
async def export_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("qalead")),
):
    """Where one export got to. READS ONLY.

    Polling must never start work. A status endpoint that claimed, retried or
    re-queued would mean a browser left open on this page generated exports all
    afternoon, and a user who refreshed twice got two.

    A job is readable by the person who requested it, and by a program manager
    or administrator — who supervise the queue and have to be able to see a
    failure that is not their own. Everyone else gets 404 rather than 403: an
    identifier that answers "not yours" still confirms the job exists, which is
    how a job id becomes an enumeration oracle.
    """
    from app.core.security import ROLE_HIERARCHY
    from app.reports.data.export_jobs import get_job

    job = await get_job(db, job_id)
    if job is None:
        raise HTTPException(404, "No such export job.")

    email = (getattr(user, "email", None) or "").lower()
    role = (getattr(user, "role", "") or "").lower()
    supervises = ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY["program_manager"]
    if not supervises and (job.requested_by or "").lower() != email:
        raise HTTPException(404, "No such export job.")

    payload = job.to_dict()
    if job.state == job.STATE_SUCCEEDED and job.report_id:
        payload["download"] = (
            f"/api/reports/artifacts/{job.report_id}/download"
            f"?content_type={XLSX_CONTENT_TYPE_LITERAL}")
    return payload
