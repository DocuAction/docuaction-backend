"""
Versioned ingestion of CMS PPEF sub-files.

WHY INGESTION RATHER THAN PER-ENTITY API CALLS
──────────────────────────────────────────────
Four of the five PPEF components have no data-api endpoint at all, so for those
this is not a preference — it is the only transport CMS offers. But even where
an API exists, ingestion is the right production path for ARC:

  * CMS states PPEF carries CURRENT enrolment information, not historical. The
    quarter behind a determination disappears from the source when the next one
    publishes. A preserved snapshot with a checksum is what keeps the
    determination explicable afterwards.
  * A review cycle evaluates hundreds of entities. Against one snapshot that is
    a set of local joins; against the API it is thousands of requests whose
    answers may drift mid-cycle, so two entities in the same cycle could be
    judged against different data.
  * The relationships are joins (ENRLMT_ID in both directions for
    REASSIGNMENT), and joins want the whole table, not row-at-a-time lookups.

WHAT IS PRESERVED, AND WHY EACH PIECE
─────────────────────────────────────
  source URL + resource id   what was fetched
  CMS title + file name      what CMS called it (title and file name differ)
  resource version           which quarter
  sha256                     that these are the exact bytes, byte-for-byte
  schema fields              which columns existed then
  record count               how much was loaded
  retrieved / ingested at    when
  rows_truncated             whether a cap was applied — never silently

FAIL LOUD ON SCHEMA DRIFT. If CMS changes a column, ingestion aborts rather
than loading nulls into an evidence store. Evidence that is quietly wrong is
worse than evidence that is missing, because only one of the two announces
itself.

Nothing here labels this data real-time. It is quarterly, and the snapshot
records say so.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

import httpx

from app.Tefca.ppef_resources import (
    EXPECTED_FIELDS,
    PPEFResource,
    PPEFResourceCatalog,
    Transport,
)

logger = logging.getLogger(__name__)

#: Streaming chunk size for the download.
CHUNK_BYTES = 1 << 20  # 1 MiB

#: Rows per database batch. Large enough to be efficient, small enough that a
#: failure does not roll back an hour of work.
BATCH_ROWS = 5_000

DOWNLOAD_TIMEOUT_SECONDS = 900.0


class SchemaDriftError(RuntimeError):
    """CMS changed the columns. Refuse to ingest rather than guess."""


class IngestError(RuntimeError):
    pass


@dataclass
class SnapshotMeta:
    """Provenance for one ingested file."""

    component: str
    cms_title: str
    file_name: Optional[str]
    resource_id: Optional[str]
    parent_dataset_id: Optional[str]
    download_url: Optional[str]
    api_endpoint: Optional[str]
    transport: str
    resource_version: Optional[str]
    as_of_label: Optional[str]
    file_size: Optional[int]
    sha256: str
    schema_fields: List[str]
    record_count: int
    rows_truncated: bool
    http_last_modified: Optional[str]
    retrieved_at: str
    ingested_at: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _as_of_label(cms_title: str) -> Optional[str]:
    """Pull "Q3 2026" out of a CMS resource title, if it is there."""
    import re

    m = re.search(r"(Q[1-4]\s+\d{4})", cms_title or "")
    return m.group(1) if m else None


def normalize_row(component: str, row: Dict[str, str]) -> Dict[str, Any]:
    """One CSV row → the columns the record table keys on, plus the payload.

    REASSIGNMENT is the case that matters: CMS documents
    REASGN_BNFT_ENRLMT_ID as the practitioner enrolment and RCV_BNFT_ENRLMT_ID
    as the entity receiving the reassigned benefits, and BOTH join back to
    ENROLLMENT.ENRLMT_ID. They are stored in named columns so the traversal in
    both directions is an indexed lookup.
    """
    clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
    if component == "REASSIGNMENT":
        return {
            "enrollment_id": clean.get("REASGN_BNFT_ENRLMT_ID") or None,
            "related_enrollment_id": clean.get("RCV_BNFT_ENRLMT_ID") or None,
            "npi": None,
            "payload": clean,
        }
    return {
        "enrollment_id": clean.get("ENRLMT_ID") or None,
        "related_enrollment_id": None,
        "npi": clean.get("NPI") or None,
        "payload": clean,
    }


def validate_schema(component: str, header: Iterable[str]) -> List[str]:
    """Confirm CMS still publishes the columns this component is parsed against.

    Extra columns are tolerated — CMS adding a field does not invalidate the
    fields already relied on. Missing columns are fatal.
    """
    fields = [h.strip().lstrip("﻿") for h in header if h is not None]
    expected = EXPECTED_FIELDS.get(component)
    if not expected:
        raise IngestError(f"No expected schema registered for component {component}")
    missing = [f for f in expected if f not in fields]
    if missing:
        raise SchemaDriftError(
            f"{component}: CMS file is missing expected column(s) {missing}. "
            f"Published columns: {fields}. Refusing to ingest — a determination "
            f"made from a partially-parsed file would be silently wrong."
        )
    return fields


async def download_component(
    resource: PPEFResource,
    sink: Callable[[bytes], None],
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Stream a component file, hashing as it goes.

    The hash is computed over the bytes as received, so it attests to what was
    actually ingested rather than to what a later re-download might return.
    """
    if not resource.download_url:
        raise IngestError(f"{resource.component}: no download URL discovered")

    digest = hashlib.sha256()
    total = 0
    last_modified = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
        async with client.stream("GET", resource.download_url, headers={"User-Agent": "DocuAction-TEFCA/1.0"}) as resp:
            if resp.status_code != 200:
                raise IngestError(f"{resource.component}: HTTP {resp.status_code} from CMS")
            last_modified = resp.headers.get("Last-Modified")
            async for chunk in resp.aiter_bytes(CHUNK_BYTES):
                digest.update(chunk)
                total += len(chunk)
                sink(chunk)
    return {
        "sha256": digest.hexdigest(),
        "bytes": total,
        "http_last_modified": last_modified,
        "retrieved_at": datetime.utcnow().isoformat(),
    }


def iter_rows(component: str, text: str, max_rows: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    """Parse CSV text into normalised records, validating the header first."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise IngestError(f"{component}: file is empty")
    fields = validate_schema(component, header)
    for i, raw in enumerate(reader):
        if max_rows is not None and i >= max_rows:
            return
        if not any(raw):
            continue
        yield normalize_row(component, dict(zip(fields, raw)))


class PPEFIngestor:
    """Downloads a component, validates it, and hands rows to a writer.

    The writer is injected so the same ingestion logic is used by the API
    endpoint, by a scheduled job, and by tests — with no database required in
    the last case.
    """

    def __init__(self, catalog: Optional[PPEFResourceCatalog] = None):
        self.catalog = catalog or PPEFResourceCatalog()

    async def ingest(
        self,
        component: str,
        write_batch: Callable[[List[Dict[str, Any]]], Any],
        max_rows: Optional[int] = None,
        resource: Optional[PPEFResource] = None,
    ) -> SnapshotMeta:
        """Ingest one component. Returns the snapshot provenance.

        `max_rows` caps the load for constrained environments. When it bites,
        `rows_truncated` is True on the snapshot — a partial ingest is never
        presented as a complete one, because evidence assembled from a silently
        truncated table would look identical to evidence assembled from a
        complete one.
        """
        if resource is None:
            discovered = await self.catalog.discover()
            resource = discovered.get(component)
        if resource is None:
            raise IngestError(f"{component}: not discovered in the CMS resource list")

        buffer = bytearray()
        meta = await download_component(resource, buffer.extend)

        text = bytes(buffer).decode("utf-8-sig", errors="replace")
        del buffer

        count = 0
        truncated = False
        batch: List[Dict[str, Any]] = []
        fields: List[str] = []

        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if header is None:
            raise IngestError(f"{component}: file is empty")
        fields = validate_schema(component, header)

        for raw in reader:
            if max_rows is not None and count >= max_rows:
                truncated = True
                break
            if not any(raw):
                continue
            batch.append(normalize_row(component, dict(zip(fields, raw))))
            count += 1
            if len(batch) >= BATCH_ROWS:
                maybe = write_batch(batch)
                if hasattr(maybe, "__await__"):
                    await maybe
                batch = []
        if batch:
            maybe = write_batch(batch)
            if hasattr(maybe, "__await__"):
                await maybe

        return SnapshotMeta(
            component=component,
            cms_title=resource.cms_title,
            file_name=resource.file_name,
            resource_id=resource.resource_id,
            parent_dataset_id=resource.parent_dataset_id,
            download_url=resource.download_url,
            api_endpoint=resource.api_endpoint,
            transport=resource.transport or Transport.DOWNLOAD.value,
            resource_version=resource.resource_version,
            as_of_label=_as_of_label(resource.cms_title),
            file_size=meta["bytes"],
            sha256=meta["sha256"],
            schema_fields=fields,
            record_count=count,
            rows_truncated=truncated,
            http_last_modified=meta.get("http_last_modified"),
            retrieved_at=meta["retrieved_at"],
            ingested_at=datetime.utcnow().isoformat(),
        )
