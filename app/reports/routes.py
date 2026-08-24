"""
Report API — generate, list, and download in HTML / PDF / CSV.

READ-ONLY BY CONSTRUCTION. None of these endpoints triggers a verification, a
source lookup, or a classification. `POST /generate` is a POST because it
CREATES a report artefact and its provenance record, not because it changes any
verification state.

RBAC follows the existing platform floors: viewing a report needs `viewer`,
generating one needs `contributor`. Reports carry entity names and review
outcomes, so nothing here is public.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["Reports"])


class GenerateReportRequest(BaseModel):
    report_type: str = Field(default="verification",
                             description="verification | verification_brief | executive")
    review_cycle_id: Optional[str] = Field(
        default=None, description="Scope to one review cycle. Omit for all records.")
    format: str = Field(default="html", description="html | pdf | csv")
    parameters: Dict[str, Any] = Field(default_factory=dict)


def _summary(result) -> Dict[str, Any]:
    snapshot = result["snapshot"]
    return {
        "report_id": result["report_id"],
        "report_type": result["report_type"],
        "stored_id": result["stored_id"],
        "snapshot": snapshot.to_dict(),
        "accessibility": result["accessibility"],
        "formats": {
            "html": f"/api/reports/{result['report_id']}/html",
            "pdf": f"/api/reports/{result['report_id']}/pdf",
            "csv": f"/api/reports/{result['report_id']}/csv",
        },
    }


@router.post("/generate", summary="Generate a report from frozen verification results")
async def generate(
    request: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("contributor")),
):
    from app.reports.generator import ReportGenerationError, generate_report

    try:
        result = await generate_report(
            db,
            report_type=request.report_type,
            review_cycle_id=request.review_cycle_id,
            generated_by=getattr(user, "email", None) or "SYSTEM",
            query_parameters=request.parameters,
        )
    except ReportGenerationError as exc:
        raise HTTPException(400, str(exc))

    if request.format == "csv":
        from app.reports.engine.csv_engine import to_bytes

        return Response(
            content=to_bytes(result["csv"]), media_type="text/csv",
            headers={"Content-Disposition":
                     f'attachment; filename="{result["report_id"]}.csv"'})
    if request.format == "pdf":
        return _pdf_response(result["html"], result["report_id"])
    if request.format == "html":
        return Response(content=result["html"], media_type="text/html")
    return _summary(result)


def _pdf_response(html: str, report_id: str) -> Response:
    """Render to PDF, or answer 503 with the reason.

    503 rather than 500: the engine's native libraries being absent is a
    service-configuration fact, not a bug in the request, and the message names
    exactly what is missing so an operator can act on it instead of filing a
    stack trace.
    """
    from app.reports.engine.pdf_engine import (
        PDFEngineUnavailable, pdf_available, render_pdf, unavailable_reason)

    if not pdf_available():
        raise HTTPException(503, f"PDF generation is unavailable: {unavailable_reason()}")
    try:
        pdf = render_pdf(html, title=report_id)
    except PDFEngineUnavailable as exc:
        raise HTTPException(503, str(exc))
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"'})


async def _stored(db, report_id: str):
    from app.tefca_registry import models as reg

    row = (await db.execute(
        select(reg.ReviewReport).where(reg.ReviewReport.report_id == report_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"No report exists with id {report_id}")
    return row


@router.get("", summary="List generated reports")
async def list_reports(
    limit: int = Query(50, ge=1, le=500),
    report_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry import models as reg

    stmt = select(reg.ReviewReport).order_by(reg.ReviewReport.generated_at.desc())
    if report_type:
        stmt = stmt.where(reg.ReviewReport.report_type == report_type)
    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    return {"items": [{
        "report_id": r.report_id,
        "report_type": r.report_type,
        "generated_at": r.generated_at,
        "snapshot": (r.report_data or {}).get("snapshot", {}),
    } for r in rows]}


@router.get("/{report_id}", summary="Report metadata and snapshot provenance")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    row = await _stored(db, report_id)
    data = row.report_data or {}
    return {
        "report_id": row.report_id,
        "report_type": row.report_type,
        "generated_at": row.generated_at,
        "snapshot": data.get("snapshot", {}),
        "dataset": data.get("dataset", {}),
    }


@router.get("/{report_id}/html", summary="Download a report as HTML")
async def get_report_html(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """The STORED HTML, byte for byte.

    Never re-rendered. A report is what the recipient received; regenerating it
    on read would quietly rewrite history the moment the underlying entities
    changed.
    """
    row = await _stored(db, report_id)
    if not row.report_html:
        raise HTTPException(404, f"Report {report_id} has no stored HTML.")
    return Response(content=row.report_html, media_type="text/html")


@router.get("/{report_id}/pdf", summary="Download a report as PDF")
async def get_report_pdf(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """PDF rendered from the STORED HTML — same document, different container."""
    row = await _stored(db, report_id)
    if not row.report_html:
        raise HTTPException(404, f"Report {report_id} has no stored HTML to render.")
    return _pdf_response(row.report_html, report_id)


@router.get("/{report_id}/csv", summary="Download a report's figures as CSV")
async def get_report_csv(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Regenerated from the STORED dataset, not from a fresh query.

    The numbers therefore match the stored report exactly, including when the
    live data has since moved on.
    """
    from app.reports.engine.csv_engine import report_to_csv, to_bytes

    row = await _stored(db, report_id)
    data = row.report_data or {}
    dataset = dict(data.get("dataset") or {})
    if not dataset:
        raise HTTPException(404, f"Report {report_id} has no stored dataset.")

    # Charts were excluded from the stored payload (they are presentation, not
    # data), so rebuild them from the stored numbers for the figure sections.
    from app.reports.charts import build_all_charts

    try:
        dataset["chart_list"] = build_all_charts(
            dataset.get("buckets") or {}, dataset.get("coverage") or {},
            dataset.get("dimensions") or {}, dataset.get("entity_status") or {},
            dataset.get("qhins") or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("csv export: charts not rebuilt for %s: %s", report_id, exc)
        dataset["chart_list"] = []

    snapshot = data.get("snapshot") or {}
    csv_text = report_to_csv(dataset, report_id,
                             snapshot.get("generation_timestamp", ""))
    return Response(
        content=to_bytes(csv_text), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.csv"'})


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
    user=Depends(require_role("viewer")),
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
    user=Depends(require_role("viewer")),
):
    """Every finalised version, oldest first. Nothing is ever replaced."""
    from app.reports.data.artifact_registry import artifact_versions

    versions = await artifact_versions(db, report_id, content_type=content_type)
    if not versions:
        raise HTTPException(404, f"No stored artifact for {report_id}")
    return {"report_id": report_id, "content_type": content_type,
            "versions": versions, "count": len(versions)}


@router.get("/artifacts/{report_id}/download",
            summary="Download a finalised artifact, integrity-verified")
async def artifact_download(
    report_id: str,
    content_type: str = Query("text/html"),
    version: Optional[int] = Query(None, description="Omit for the latest"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Fetch stored bytes and re-hash them before handing them over.

    A stored hash nobody recomputes is a claim. If the bytes no longer match
    what was registered this raises rather than serving them — a silently
    altered deliverable is worse than a failed download.
    """
    from app.reports.data.artifact_registry import retrieve_artifact

    try:
        got = await retrieve_artifact(db, report_id, content_type=content_type,
                                      version=version)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        logger.error("artifact integrity failure for %s: %s", report_id, exc)
        raise HTTPException(500, str(exc))

    artifact = got["artifact"]
    ext = {"text/html": "html", "application/pdf": "pdf",
           "text/csv": "csv"}.get(content_type, "bin")
    return Response(
        content=got["content"], media_type=content_type,
        headers={
            "Content-Disposition":
                f'attachment; filename="{report_id}-v{artifact["artifact_version"]}.{ext}"',
            "X-Artifact-SHA256": artifact["rendered_sha256"],
            "X-Artifact-Version": str(artifact["artifact_version"]),
            "X-Data-Classification": artifact["data_classification"],
        })
