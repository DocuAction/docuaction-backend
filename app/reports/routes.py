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
