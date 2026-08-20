"""
Local PPEF evidence store — reading ingested snapshots back out.

The store always answers from ONE snapshot per component: the most recent
completed ingest. Mixing quarters inside a single determination would produce
evidence that cannot be reproduced, because no single CMS publication ever
contained that combination of rows.

Every read returns the snapshot provenance alongside the records, so the
evidence layer can state which extract answered — file name, CMS title,
version, checksum, retrieval time — rather than just "PECOS said so".
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _snapshot_provenance(snap) -> Dict[str, Any]:
    """Provenance block shaped like the CMS API one, so EvidenceItem needs no
    special case for download-sourced evidence."""
    return {
        "source": "CMS_PPEF",
        "source_dataset": snap.parent_dataset_id,
        "ppef_component": snap.component,
        "cms_resource_title": snap.cms_title,
        "file_name": snap.file_name,
        "resource_id": snap.resource_id,
        "resource_version": snap.resource_version,
        "as_of_label": snap.as_of_label,
        "sha256": snap.sha256,
        # The SNAPSHOT's total, not the number of rows this query matched. Named
        # distinctly because conflating the two produced an evidence item that
        # read "CORROBORATED, records=0" — a self-contradiction in an audit
        # trail. `row_count` (set per query below) is the matched count.
        "snapshot_record_count": snap.record_count,
        "rows_truncated": bool(snap.rows_truncated),
        "transport": snap.transport,
        "query_identifier": f"snapshot:{snap.id}",
        "query_timestamp": snap.ingested_at.isoformat() if snap.ingested_at else None,
        "retrieved_at": snap.retrieved_at.isoformat() if snap.retrieved_at else None,
        "dataset_version_anchor": f"{snap.file_name}@{snap.sha256[:16]}" if snap.sha256 else None,
        "update_cadence": "quarterly",
        "realtime": False,
        "http_last_modified": snap.http_last_modified,
    }


async def latest_snapshot(db, component: str):
    """Most recent COMPLETED snapshot for a component, or None.

    A failed or in-flight ingest is invisible here on purpose: half a file is
    not a smaller file, it is a different and misleading one.
    """
    from app.Tefca.models import TEFCAPPEFSnapshot

    result = await db.execute(
        select(TEFCAPPEFSnapshot)
        .where(TEFCAPPEFSnapshot.component == component)
        .where(TEFCAPPEFSnapshot.ingest_status == "complete")
        .order_by(TEFCAPPEFSnapshot.ingested_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def snapshot_status(db) -> Dict[str, Dict[str, Any]]:
    """Per-component snapshot summary for health reporting."""
    from app.Tefca.ppef_resources import EXPECTED_FIELDS

    out: Dict[str, Dict[str, Any]] = {}
    for component in EXPECTED_FIELDS:
        try:
            snap = await latest_snapshot(db, component)
        except Exception as exc:
            logger.warning("snapshot status lookup failed for %s: %s", component, exc)
            snap = None
        if snap is None:
            continue
        out[component] = {
            "snapshot_id": str(snap.id),
            "cms_title": snap.cms_title,
            "file_name": snap.file_name,
            "resource_version": snap.resource_version,
            "as_of_label": snap.as_of_label,
            "sha256": snap.sha256,
            "record_count": snap.record_count,
            "rows_truncated": bool(snap.rows_truncated),
            "ingested_at": snap.ingested_at.isoformat() if snap.ingested_at else None,
        }
    return out


def make_local_store(db):
    """Build the callable PPEFRelationalConnector expects.

    Signature: (component, key_field, enrollment_ids) -> (records, provenance) | None
    Returning None means "no snapshot" — distinct from returning ([], provenance),
    which means "the snapshot was searched and this enrolment genuinely has no
    rows". CMS documents that some individual enrolments legitimately have no
    practice-location row, so those two states must never collapse.
    """
    from app.Tefca.models import TEFCAPPEFRecord

    async def store(component: str, key_field: str,
                    enrollment_ids: List[str]) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
        snap = await latest_snapshot(db, component)
        if snap is None:
            return None

        column = TEFCAPPEFRecord.enrollment_id
        # REASSIGNMENT is queried from either end: by the practitioner enrolment
        # (REASGN_BNFT_ENRLMT_ID) or by the receiving entity (RCV_BNFT_ENRLMT_ID).
        if key_field == "RCV_BNFT_ENRLMT_ID":
            column = TEFCAPPEFRecord.related_enrollment_id

        result = await db.execute(
            select(TEFCAPPEFRecord)
            .where(TEFCAPPEFRecord.snapshot_id == snap.id)
            .where(TEFCAPPEFRecord.component == component)
            .where(column.in_(list(enrollment_ids)))
        )
        rows = result.scalars().all()
        # The payload is returned as CMS published it — original field names and
        # values — so downstream evidence quotes the source rather than a
        # paraphrase of it.
        records = [dict(r.payload or {}) for r in rows]
        provenance = _snapshot_provenance(snap)
        # Rows matched for THIS enrolment set — what the evidence item reports.
        provenance["row_count"] = len(records)
        provenance["query_filters"] = {key_field: list(enrollment_ids)}
        return records, provenance

    return store


async def copy_records(db, snapshot_id, component: str, rows: List[Dict[str, Any]]) -> int:
    """Bulk-insert PPEF rows with Postgres COPY.

    WHY NOT ORM INSERTS
    Full quarterly loads are ~5.5M rows across the four sub-files (Reassignment
    alone is ~3.7M). Row-by-row ORM inserts over a network to Azure Postgres
    make that a multi-hour job; COPY makes it minutes. The evidence semantics
    are identical — this is purely how the bytes get into the table.

    Falls back to ORM inserts when the driver is not asyncpg (e.g. a test or a
    different backend), so correctness never depends on the fast path being
    available.

    `payload` is JSONB, and asyncpg's COPY expects it pre-encoded as text.
    `id` is generated here because COPY bypasses the Python-side column default.
    """
    if not rows:
        return 0

    from app.Tefca.models import TEFCAPPEFRecord

    records = [
        (
            uuid.uuid4(),
            snapshot_id,
            component,
            r.get("enrollment_id"),
            r.get("related_enrollment_id"),
            r.get("npi"),
            json.dumps(r.get("payload") or {}),
        )
        for r in rows
    ]

    try:
        connection = await db.connection()
        raw = await connection.get_raw_connection()
        driver = getattr(raw, "driver_connection", None)
        if driver is None or not hasattr(driver, "copy_records_to_table"):
            raise AttributeError("driver does not support COPY")
        await driver.copy_records_to_table(
            "tefca_ppef_records",
            records=records,
            columns=["id", "snapshot_id", "component", "enrollment_id",
                     "related_enrollment_id", "npi", "payload"],
        )
        return len(records)
    except Exception as exc:
        # A COPY failure must not silently drop rows: fall back to the slow path
        # rather than returning a count for data that never landed.
        logger.info("COPY unavailable (%s); falling back to ORM inserts", exc)
        for r in rows:
            db.add(TEFCAPPEFRecord(
                snapshot_id=snapshot_id,
                component=component,
                enrollment_id=r.get("enrollment_id"),
                related_enrollment_id=r.get("related_enrollment_id"),
                npi=r.get("npi"),
                payload=r.get("payload") or {},
            ))
        await db.flush()
        return len(rows)
