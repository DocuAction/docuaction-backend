"""
Report snapshots — what makes a number answerable six months later.

THE QUESTION THIS EXISTS TO ANSWER
──────────────────────────────────
    "Why did DA-ARC-2026-001 show 47 B2 entities?"

Answering it needs more than the number. It needs the population the number was
computed over, the rule version that classified those entities, the evidence
generation the classification cited, the query logic that counted them, and the
exact parameters the report was run with. Every one of those can change
afterwards — CMS publishes a new quarter, ONC revises a rule, the service is
refactored — and none of the changes are wrong. What would be wrong is being
unable to say which versions were in force when the report was issued.

`data_payload_hash` is the integrity anchor: the SHA-256 of the canonicalised
dataset the report rendered from. Regenerating the report from the same snapshot
and getting the same hash proves the numbers were not quietly recomputed against
newer data.

SNAPSHOTS ARE APPEND-ONLY. A report is never regenerated in place. If the
underlying entities change next week, the report issued this week must still say
what the recipient received — the same contract `review_reports` already holds
for its archived HTML.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

REPORT_TYPES = ("verification", "data_quality", "executive", "intake")

#: DA-ARC-YYYY-NNN
REPORT_ID_PREFIX = "DA-ARC"


def _canonical(value: Any) -> Any:
    """Recursively canonicalise for hashing.

    Sorted keys, and every value coerced to a stable text form. Without this the
    hash would change when a dict happened to iterate differently or a Decimal
    arrived where a float did last time, and a "the data changed" signal that
    fires on nothing is worse than no signal.
    """
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return _canonical(value.to_dict())
    return str(value)


def data_payload_hash(dataset: Dict[str, Any]) -> str:
    """SHA-256 of the canonicalised dataset the report rendered from.

    Chart objects are excluded — they are a PRESENTATION of the numbers, and
    including them would make a change in a chart's caption look like a change
    in the data. The underlying values are all present in the other keys.
    """
    payload = {k: v for k, v in dataset.items()
               if k not in ("charts", "chart_list")}
    encoded = json.dumps(_canonical(payload), sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class ReportSnapshot:
    """Full provenance for one generated report."""

    report_id: str
    report_type: str
    generation_timestamp: str
    reporting_period_start: Optional[str] = None
    reporting_period_end: Optional[str] = None
    review_cycle_id: Optional[str] = None
    dataset_snapshot_version: Optional[str] = None
    rce_source_file_sha256: Optional[str] = None
    evidence_generation: Optional[str] = None
    b1_b4_rule_version: Optional[str] = None
    query_parameters: Dict[str, Any] = field(default_factory=dict)
    generated_by: str = "SYSTEM"
    template_version: Optional[str] = None
    report_data_service_version: Optional[str] = None
    data_payload_hash: Optional[str] = None
    pdf_engine: Dict[str, Any] = field(default_factory=dict)
    accessibility: Dict[str, Any] = field(default_factory=dict)
    #: DEVELOPMENT_TEST or GOVERNMENT. Carried on the snapshot itself so a
    #: stored report can never be re-read without its classification.
    data_classification: str = "DEVELOPMENT_TEST"
    #: The full Area-1 delivery record behind the population, not just its
    #: hash — filename, record count and schema fingerprint are what make the
    #: hash checkable by someone who was not here when it was generated.
    source_provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


async def next_report_id(db, report_type: str = "verification",
                         now: Optional[datetime] = None) -> str:
    """The next DA-ARC-YYYY-NNN, sequential within the calendar year.

    Derived from the count of reports already stored for the year. Two reports
    generated in the same instant could collide; that is acceptable for a
    human-facing label because `report_id` is not the primary key —
    `review_reports.report_id` is UNIQUE, so a genuine collision fails the insert
    loudly rather than silently overwriting an issued report.
    """
    from app.tefca_registry import models as reg

    stamp = now or datetime.now(timezone.utc)
    year = stamp.year
    try:
        existing = int((await db.execute(
            select(func.count()).select_from(reg.ReviewReport)
            .where(reg.ReviewReport.report_id.like(f"{REPORT_ID_PREFIX}-{year}-%"))
        )).scalar() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("report id sequence unavailable, starting at 1: %s", exc)
        existing = 0
    return f"{REPORT_ID_PREFIX}-{year}-{existing + 1:03d}"


async def latest_evidence_generation(db, review_cycle_id: Optional[str]) -> Optional[str]:
    """The newest evidence generation timestamp the report's numbers rest on."""
    from app.Tefca.models import TEFCADimensionEvidence

    try:
        stmt = select(func.max(TEFCADimensionEvidence.generation_timestamp))
        if review_cycle_id:
            stmt = stmt.where(
                TEFCADimensionEvidence.review_cycle_id == review_cycle_id)
        return (await db.execute(stmt)).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.info("evidence generation anchor unavailable: %s", exc)
        return None


async def active_rule_version(db) -> Optional[str]:
    """The B1-B4 rule-set version in force when the report was generated."""
    from app.tefca_registry import models as reg

    try:
        version = (await db.execute(
            select(func.max(reg.ReviewRule.version))
            .where(reg.ReviewRule.is_active.is_(True))
        )).scalar()
        return str(version) if version is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.info("rule version unavailable: %s", exc)
        return None


async def latest_rce_source_sha256(db) -> Optional[str]:
    """SHA-256 of the delivery behind the population, or None.

    Reads Area 1, which is the only authoritative delivery record.

    This previously read `tefca_import_batches` ordered by `created_at desc`.
    The newest row in that table is a unit-test fixture whose checksum is the
    string "cafe", so every report ever generated stamped "cafe" as its source
    hash while the real digest sat unread in Area 1. Never returns a value that
    is not a real SHA-256; see `source_provenance.authoritative_source_provenance`
    for the reason when it returns None.
    """
    from app.reports.data.source_provenance import authoritative_source_provenance

    return (await authoritative_source_provenance(db)).sha256


async def build_snapshot(
    db,
    *,
    report_id: str,
    report_type: str,
    dataset: Dict[str, Any],
    query_parameters: Optional[Dict[str, Any]] = None,
    generated_by: str = "SYSTEM",
    template_version: Optional[str] = None,
    accessibility: Optional[Dict[str, Any]] = None,
) -> ReportSnapshot:
    """Assemble the provenance record for one generated report."""
    from app.reports.engine.pdf_engine import engine_info

    from app.reports.data.source_provenance import (
        authoritative_source_provenance, resolve_cycle_id)

    scope = dataset.get("scope") or {}
    source = await authoritative_source_provenance(db)
    evidence_generation = await latest_evidence_generation(
        db, dataset.get("review_cycle_id"))
    # Never null. A stored report with no cycle cannot be scoped afterwards,
    # which is what every pre-existing report in review_reports suffers from.
    # Anchored on the canonical evidence RULE version, not the generation
    # timestamp: the rule version is what determines which observations a report
    # may read, it is stable across re-runs, and it is the thing a reader can
    # look up. A timestamp would make every regeneration a different "cycle".
    from app.Tefca.evidence_version import current_rule_version

    review_cycle_id = resolve_cycle_id(
        dataset.get("review_cycle_id"),
        evidence_version=(dataset.get("evidence_rule_version")
                          or current_rule_version()),
        source_sha256=source.sha256)

    def _iso(value: Any) -> Optional[str]:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    return ReportSnapshot(
        report_id=report_id,
        report_type=report_type,
        generation_timestamp=datetime.now(timezone.utc).isoformat(),
        reporting_period_start=_iso(scope.get("reporting_period_start")),
        reporting_period_end=_iso(scope.get("reporting_period_end")),
        review_cycle_id=str(review_cycle_id),
        dataset_snapshot_version=evidence_generation,
        rce_source_file_sha256=source.sha256,
        evidence_generation=evidence_generation,
        b1_b4_rule_version=await active_rule_version(db),
        query_parameters=dict(query_parameters or {}),
        generated_by=generated_by,
        template_version=template_version,
        report_data_service_version=dataset.get("service_version"),
        data_payload_hash=data_payload_hash(dataset),
        pdf_engine=engine_info(),
        accessibility=dict(accessibility or {}),
        data_classification=source.data_classification,
        source_provenance=source.to_dict(),
    )


async def store_report(db, snapshot: ReportSnapshot, dataset: Dict[str, Any],
                       html: str) -> Optional[str]:
    """Persist the report and its provenance. Returns the row id, or None.

    A storage failure does NOT fail generation. The analyst waiting on the
    document still gets it, and the caller is told persistence did not complete —
    the same trade `import_bridge` makes, and for the same reason: losing work
    that already succeeded because a secondary write failed is the worse outcome.
    """
    from app.tefca_registry import models as reg

    try:
        row_id = uuid.uuid4()
        payload = {k: v for k, v in dataset.items()
                   if k not in ("charts", "chart_list")}
        db.add(reg.ReviewReport(
            id=row_id,
            report_id=snapshot.report_id,
            report_type=snapshot.report_type,
            period_start=None,
            period_end=None,
            rule_set_version=(int(snapshot.b1_b4_rule_version)
                              if (snapshot.b1_b4_rule_version or "").isdigit()
                              else None),
            report_data={
                "snapshot": snapshot.to_dict(),
                "dataset": _canonical(payload),
            },
            report_html=html,
        ))
        await db.commit()
        return str(row_id)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning("report %s generated but not persisted: %s: %s",
                       snapshot.report_id, type(exc).__name__, exc)
        return None


def verify_reproducible(snapshot: ReportSnapshot, dataset: Dict[str, Any]) -> bool:
    """True when `dataset` still hashes to what the snapshot recorded.

    The executable form of the reproducibility promise: regenerating from the
    same frozen inputs must produce the same numbers. A False here means the
    underlying data moved, which is exactly the condition a reviewer needs
    flagged rather than absorbed.
    """
    return data_payload_hash(dataset) == snapshot.data_payload_hash
