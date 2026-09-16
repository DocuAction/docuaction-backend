"""
Durable copies of every rendering of a generated report.

WHAT "DURABLE" MEANS HERE
─────────────────────────
`review_reports` holds the report's dataset and HTML in a mutable Postgres row.
That is backed up with the database, but a row can be updated, carries no
content address and has no retention record. The artifact registry
(`artifact_registry.py`) plus the configured store (`app.core.storage`) is the
application's approved durable mechanism: content-hashed, write-once, versioned,
with retention metadata, and served back only through a path that re-hashes.

Until this module, only the HTML of a report went through it. The CSV was
regenerated from the stored dataset on every download and the PDF was rendered
from the stored HTML on every download — so neither had a hash of record, and
neither could be shown, later, to be the file a reviewer was handed. Every
format the report is issued in is registered now, for the delivery-scoped report
types (`delivery_processing`, `data_quality`, `intake`) and the HTML for every
other type as before.

WHICH STORE
───────────
Whatever `REPORT_ARTIFACT_BACKEND` selects. The local filesystem backend is for
tests and single-host development ONLY: on App Service it writes to the
container's ephemeral layer and is not a durable copy. That fact is not hidden —
`storage_backend` and `durable` are reported on every link and every listing so
an operator reading DEV output sees `local / durable: false` rather than a
comforting word.

PDF
───
The PDF engine's native libraries are absent on some hosts. When
`pdf_available()` is False the PDF is NOT registered; `pdf_unavailable_reason`
is recorded on the generation record and `/pdf` keeps rendering on demand. A
report is never failed for want of a PDF.

FAILURE POLICY
──────────────
Each format is finalised and COMMITTED on its own, so a failure registering the
PDF cannot roll back the HTML that was already registered. A failure is logged
loudly, recorded in `errors` and returned to the caller; it never fails the
generation — the analyst already has the document.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HTML = "text/html"
CSV = "text/csv"
PDF = "application/pdf"


def source_delivery_sha256(dataset: Dict[str, Any], snapshot) -> Optional[str]:
    """The SHA-256 of the delivered file THIS report describes.

    For a delivery-scoped report that is the job's / intake's recorded hash, in
    the dataset. The snapshot's `rce_source_file_sha256` is the authoritative
    CURRENT delivery, which is not necessarily the one a regeneration names, so
    the dataset wins and the snapshot is the fallback.
    """
    delivery = dataset.get("delivery") or {}
    intake = dataset.get("intake") or {}
    return (delivery.get("sha256") or intake.get("sha256")
            or getattr(snapshot, "rce_source_file_sha256", None))


async def finalize_report_renderings(
    db, *, report_id: str, report_type: str, html: str, csv_text: Optional[str],
    snapshot, dataset: Dict[str, Any], generated_by: str,
    include_csv: bool = True, include_pdf: bool = True, store=None,
    html_artifact: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register HTML (+ CSV, + PDF when the engine is available) as finalised
    artifacts. Never raises.

    `html_artifact` is the registry row when the caller has ALREADY finalised
    the HTML (the generator does, so the existing call site and its guards stay
    exactly where they are); it is folded into the result and the HTML is not
    registered twice. A dict with `registered: False` means the caller tried and
    failed; the HTML is then attempted again here.

    Returns::

        {"artifacts": [registry rows], "html": row | None, "csv": row | None,
         "pdf": row | None, "pdf_unavailable_reason": str | None,
         "storage_backend": str, "durable": bool, "storage_note": str,
         "errors": [str]}
    """
    from app.core.storage.artifact_store import get_artifact_store
    from app.reports.data.artifact_registry import (finalize_artifact,
                                                    storage_durability)

    store = store or get_artifact_store()
    out: Dict[str, Any] = {"artifacts": [], "html": None, "csv": None, "pdf": None,
                           "pdf_unavailable_reason": None, "errors": [],
                           **storage_durability(store.backend)}
    common = dict(
        report_id=report_id, report_type=report_type,
        review_cycle_id=snapshot.review_cycle_id, generated_by=generated_by,
        template_version=snapshot.template_version,
        evidence_rule_version=snapshot.b1_b4_rule_version,
        report_data_hash=snapshot.data_payload_hash,
        source_artifact_sha256=source_delivery_sha256(dataset, snapshot),
        data_classification=snapshot.data_classification,
        store=store)

    async def _one(kind: str, content_type: str, content: bytes) -> None:
        try:
            row = await finalize_artifact(db, content=content,
                                          content_type=content_type, **common)
            # finalize_artifact flushes; the write that owns the row owns its
            # durability, and a later format failing must not undo this one.
            await db.commit()
            out[kind] = row
            out["artifacts"].append(row)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "report %s: durable %s artifact registration FAILED (%s: %s). The "
                "report was generated and stored, but no immutable content-"
                "addressed %s record exists for it.",
                report_id, kind.upper(), type(exc).__name__, exc, kind.upper())
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
            out["errors"].append(f"{kind}: {type(exc).__name__}: {exc}")

    if html_artifact and html_artifact.get("registered") is not False:
        out["html"] = html_artifact
        out["artifacts"].append(html_artifact)
    else:
        await _one("html", HTML, html.encode("utf-8"))

    if include_csv and csv_text is not None:
        from app.reports.engine.csv_engine import to_bytes

        await _one("csv", CSV, to_bytes(csv_text))

    if include_pdf:
        from app.reports.engine.pdf_engine import (PDFEngineUnavailable,
                                                   pdf_available, render_pdf,
                                                   unavailable_reason)

        if not pdf_available():
            out["pdf_unavailable_reason"] = unavailable_reason()
        else:
            try:
                pdf_bytes = render_pdf(html, title=report_id)
            except PDFEngineUnavailable as exc:
                out["pdf_unavailable_reason"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                logger.error("report %s: PDF render for the durable copy FAILED: %s",
                             report_id, exc)
                out["pdf_unavailable_reason"] = f"render failed: {type(exc).__name__}"
            else:
                await _one("pdf", PDF, pdf_bytes)
    return out
