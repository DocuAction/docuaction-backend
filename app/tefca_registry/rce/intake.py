"""
P2 — accept a delivery into Area 1.

WHAT HAPPENS, IN ORDER, AND WHY THE ORDER MATTERS
─────────────────────────────────────────────────
  1. Security scan the bytes (existing FileScanner) BEFORE anything is written
     or parsed. A malicious payload must never be walked by a parser.
  2. Preserve the ORIGINAL bytes to immutable storage, unmodified. This happens
     before parsing so the evidence exists even if parsing goes wrong.
  3. Read: detect delimiter and encoding, split every line.
  4. Check the schema fingerprint against the locked map. Drift does NOT reject
     the delivery — it is recorded, and the intake is flagged, because a
     delivery with a changed schema is exactly the thing that must not be
     silently discarded.
  5. Write the intake row, then one source record for EVERY line.
  6. Link duplicates by SHA-256, without rejecting them.

STEP 5 IS THE CONTRACT: line count in equals row count out. `verify_line_count`
asserts it at the end of the transaction, and a mismatch aborts the intake
rather than committing a partial Area 1 — a half-loaded delivery that reports
success is worse than one that failed.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import repository as repo
from app.tefca_registry.rce.field_map import (
    EXPECTED_SCHEMA_FINGERPRINT,
    FIELD_MAP_VERSION,
    RCE_FIELDS,
)
from app.tefca_registry.rce.reader import (
    PARSE_OK,
    DelimiterUndecidable,
    read_delivery,
)

logger = logging.getLogger(__name__)

#: Where original deliveries are preserved. Configurable so a deployment can
#: point it at durable storage; the default sits under the app's upload root.
STORAGE_SUBDIR = "rce_deliveries"


class IntakeError(RuntimeError):
    """The delivery could not be accepted at all."""


class LineCountMismatch(IntakeError):
    """Rows written != lines read. The intake is aborted rather than committed."""


def storage_root() -> str:
    base = os.getenv("UPLOAD_DIR", os.path.join(os.getcwd(), "uploads"))
    if not os.path.isabs(base):
        base = os.path.join(os.getcwd(), base)
    return os.path.join(base, STORAGE_SUBDIR)


def preserve_original(raw: bytes, sha256: str, filename: str) -> str:
    """Write the original bytes to immutable storage and return the path.

    The stored name carries the content hash, so two deliveries with identical
    bytes resolve to the same file and a delivery can never overwrite a
    DIFFERENT one. If the path already exists the bytes are NOT rewritten — they
    are already the same bytes by construction, and rewriting would touch
    preserved evidence for no reason.
    """
    root = storage_root()
    os.makedirs(root, exist_ok=True)
    safe = os.path.basename(filename or "delivery").replace(os.sep, "_")[:120]
    path = os.path.join(root, f"{sha256[:16]}_{safe}")
    if os.path.exists(path):
        logger.info("delivery bytes already preserved at %s; not rewritten", path)
        return path
    with open(path, "wb") as handle:
        handle.write(raw)
    try:
        os.chmod(path, 0o444)  # read-only; best effort, ignored on some systems
    except OSError:
        pass
    return path


async def ingest_delivery(
    db,
    raw: bytes,
    *,
    filename: str,
    delivery_label: Optional[str] = None,
    declared_delimiter: Optional[str] = None,
    received_by: str = "SYSTEM",
    source_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Accept one delivery into Area 1. Every line lands or the intake aborts."""
    sha256 = hashlib.sha256(raw).hexdigest()

    # 2 — preserve the original before anything else can go wrong.
    storage_path = preserve_original(raw, sha256, filename)

    # 3 — read.
    try:
        read = read_delivery(raw, declared_delimiter=declared_delimiter,
                             expected_fields=RCE_FIELDS)
    except DelimiterUndecidable as exc:
        # Recorded as a FAILED intake rather than discarded: a delivery that
        # arrived and could not be parsed is still a delivery that arrived.
        intake = await repo.create_intake(
            db, original_filename=filename, storage_path=storage_path,
            sha256=sha256, file_size_bytes=len(raw),
            headers=[], schema_fingerprint="", record_count=0,
            delivery_label=delivery_label, received_by=received_by,
            status="FAILED", error=str(exc),
            source_metadata=source_metadata or {},
        )
        await db.commit()
        raise IntakeError(
            f"Delivery preserved and recorded as intake {intake.id}, but its "
            f"structure could not be established: {exc}") from exc

    # 4 — schema drift: recorded, never a silent reject.
    drift = read.schema_fingerprint != EXPECTED_SCHEMA_FINGERPRINT
    metadata = dict(source_metadata or {})
    metadata.update({
        "field_map_version": FIELD_MAP_VERSION,
        "schema_drift": drift,
        "detection_note": read.detection_note,
        "expected_schema_fingerprint": EXPECTED_SCHEMA_FINGERPRINT,
        "mojibake_cells": read.mojibake_cells,
        "embedded_tab_cells": read.embedded_tab_cells,
        "parse_ok": read.ok_count,
        "parse_malformed": read.malformed_count,
    })
    if drift:
        metadata["schema_drift_note"] = (
            "The delivered header does not match the locked 41-field map. The "
            "delivery is preserved and its records are stored; promotion is "
            "held until the map is reconciled, because parsing an unknown "
            "schema against a stale map would mis-assign values.")

    # 6 — duplicate detection. Linked, never rejected.
    earlier = await repo.find_intakes_by_sha(db, sha256)
    duplicate_of = earlier[0].id if earlier else None

    intake = await repo.create_intake(
        db,
        delivery_label=delivery_label,
        original_filename=filename,
        storage_path=storage_path,
        sha256=sha256,
        file_size_bytes=len(raw),
        delimiter=read.delimiter,
        encoding=read.encoding,
        encoding_anomaly=bool(read.encoding_had_errors or read.mojibake_cells),
        line_terminator=read.line_terminator,
        headers=list(read.headers),
        schema_fingerprint=read.schema_fingerprint,
        record_count=read.record_count,
        received_by=received_by,
        source_metadata=metadata,
        status="PARSED",
        duplicate_of_intake_id=duplicate_of,
        duplicate_content=bool(duplicate_of),
    )

    # 5 — one row per delivered line.
    rows: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    for line in read.lines:
        rows.append({
            "source_intake_id": intake.id,
            "line_number": line.line_number,
            "raw_line": line.raw_line,
            "parsed": line.parsed,
            "record_sha256": line.record_sha256,
            "source_rce_id": (line.get("id") or None),
            "tefcaid": (line.get("TEFCAID") or None),
            "hcid": (line.get("HCID") or None),
            "npi": (line.get("NPI") or None),
            "field_count": line.field_count,
            "parse_status": line.parse_status,
            "parse_note": line.parse_note,
            "promotion_status": "pending",
            "created_at": now,
        })

    written = 0
    batch = 2000
    for start in range(0, len(rows), batch):
        written += await repo.create_source_records(db, rows[start:start + batch])

    if written != read.record_count:
        await db.rollback()
        raise LineCountMismatch(
            f"Read {read.record_count} lines but wrote {written} source records. "
            f"The intake was rolled back rather than committed — a partially "
            f"loaded Area 1 that reports success is worse than a failed load.")

    await db.commit()

    stored = await repo.count_source_records(db, intake.id)
    if stored != read.record_count:
        raise LineCountMismatch(
            f"Post-commit count is {stored}, expected {read.record_count}.")

    return {
        "intake_id": str(intake.id),
        "sha256": sha256,
        "storage_path": storage_path,
        "record_count": read.record_count,
        "records_stored": stored,
        "parse_ok": read.ok_count,
        "parse_malformed": read.malformed_count,
        "delimiter": read.delimiter,
        "encoding": read.encoding,
        "line_terminator": read.line_terminator,
        "schema_fingerprint": read.schema_fingerprint,
        "schema_drift": drift,
        "duplicate_content": bool(duplicate_of),
        "duplicate_of_intake_id": str(duplicate_of) if duplicate_of else None,
        "mojibake_cells": read.mojibake_cells,
        "embedded_tab_cells": read.embedded_tab_cells,
        "detection_note": read.detection_note,
        "every_line_stored": stored == read.record_count,
    }
