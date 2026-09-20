"""Report reconciliation and provenance: a delivery-scoped report must describe
exactly one population and must say which delivery it describes.

Added 2026-09-20 for QA-034/QA-035/QA-036. The downloaded CSV for a 50-record
delivery reported 108 received, 212 evaluated and 22,309 entity-status rows —
three different populations in one document, with a header that named no
delivery at all. Two rules close that:

1. `reconcile_dataset` — every section of a scoped report must be compatible
   with the governing population. A section that counts more entities than the
   scope received is a scope leak, and generation fails closed rather than
   issuing the document.
2. `delivery_provenance` — every delivery-scoped dataset carries the delivery
   job, intake, reconciliation snapshot, review cycle, source-file hash and
   build SHA, so the CSV/HTML header, the report card and the audit event all
   state the same facts.

Nothing here reads live data; both functions work from the dataset already
built, plus one intake lookup for the source-file provenance.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RECONCILIATION_CODE = "REPORT_SCOPE_UNRECONCILED"


def is_scoped(dataset: Dict[str, Any]) -> bool:
    """True when the dataset names a delivery or a review cycle — the reports
    for which a single population is a hard requirement. The documented
    registry-wide "all records" report (no delivery, no cycle) is not scoped."""
    return bool(dataset.get("delivery")) or bool(dataset.get("review_cycle_id"))


def _int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def reconcile_dataset(dataset: Dict[str, Any]) -> List[str]:
    """Problems that make a scoped report's sections incompatible. Empty = ok.

    Checks are deliberately inequalities, not equalities: an entity may be
    received and held (not evaluated), so evaluated <= received; a section may
    legitimately have fewer rows than the population, never more.
    """
    problems: List[str] = []
    scope = dataset.get("scope") or {}
    received = _int(scope.get("records_received"))
    evaluated = _int(scope.get("records_evaluated"))

    if received is not None and evaluated is not None and evaluated > received:
        problems.append(f"records_evaluated ({evaluated}) exceeds records_received ({received})")

    buckets = dataset.get("buckets") or {}
    bucket_total = _int(buckets.get("total"))
    if bucket_total is not None and evaluated is not None and bucket_total != evaluated:
        problems.append(f"B1-B4 total ({bucket_total}) != records_evaluated ({evaluated})")

    statuses = dataset.get("entity_status") or {}
    status_total = _int(statuses.get("total"))
    if status_total is not None and received is not None and status_total > received:
        problems.append(f"entity_status total ({status_total}) exceeds records_received ({received})")

    coverage = dataset.get("coverage") or {}
    for source in coverage.get("sources") or []:
        total = _int(source.get("total"))
        if total is not None and received is not None and total > received:
            problems.append(f"coverage[{source.get('source')}] total ({total}) exceeds "
                            f"records_received ({received})")

    qhins = dataset.get("qhins") or {}
    qhin_rows = sum(_int(q.get("total")) or 0 for q in (qhins.get("qhins") or []))
    if qhins.get("qhins") and evaluated is not None and qhin_rows > evaluated:
        problems.append(f"QHIN comparison rows ({qhin_rows}) exceed records_evaluated ({evaluated})")
    return problems


async def delivery_provenance(db, dataset: Dict[str, Any]) -> Dict[str, Any]:
    """The immutable provenance block for a delivery-scoped dataset.

    Every field is a fact the dataset (or the intake row it names) already
    carries; nothing is inferred. Missing values are None, never invented.
    """
    from app.core import request_context

    delivery = dict(dataset.get("delivery") or {})
    intake_block = dataset.get("intake") or {}
    job_id = delivery.get("job_id")
    intake_id = delivery.get("intake_id")
    filename = delivery.get("filename") or intake_block.get("filename")
    sha256 = delivery.get("sha256") or intake_block.get("sha256")
    delivery_label = delivery.get("delivery_label") or intake_block.get("delivery_label")

    if intake_id and not sha256:
        try:
            import uuid as _uuid

            from app.tefca_registry.rce import models as m

            intake = await db.get(m.RceSourceIntake, _uuid.UUID(str(intake_id)))
            if intake is not None:
                sha256 = intake.sha256
                filename = filename or intake.original_filename
                delivery_label = delivery_label or getattr(intake, "delivery_label", None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("report provenance: intake %s lookup failed: %s", intake_id, exc)

    return {
        "scope_type": "DELIVERY" if (job_id or intake_id) else "GLOBAL",
        "delivery_job_id": job_id,
        "intake_id": intake_id,
        "delivery_label": delivery_label,
        "reconciliation_snapshot_id": dataset.get("snapshot_id"),
        "review_cycle_id": dataset.get("review_cycle_id"),
        "source_filename": filename,
        "source_file_sha256": sha256,
        "build_sha": request_context.build_sha(),
    }


PROVENANCE_ROWS = (
    ("Scope", "scope_type"),
    ("Delivery job", "delivery_job_id"),
    ("Intake", "intake_id"),
    ("Delivery label", "delivery_label"),
    ("Reconciliation snapshot", "reconciliation_snapshot_id"),
    ("Review cycle", "review_cycle_id"),
    ("Source file", "source_filename"),
    ("Source file SHA-256", "source_file_sha256"),
    ("Build SHA", "build_sha"),
)


def provenance_lines(dataset: Dict[str, Any]) -> List[str]:
    """`# Label: value` lines for a CSV header, one per PROVENANCE_ROWS entry.
    A GLOBAL report states that explicitly instead of omitting the block."""
    prov = dataset.get("provenance") or {}
    if not prov:
        return ["# Scope: GLOBAL (every review cycle; no delivery named)"]
    out = []
    for label, key in PROVENANCE_ROWS:
        value = prov.get(key)
        out.append(f"# {label}: {value if value not in (None, '') else 'Not recorded'}")
    return out
