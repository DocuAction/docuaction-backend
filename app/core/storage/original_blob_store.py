"""Durable, immutable, streaming preservation of a large licensed original (the IQVIA upload).

WHY A SEPARATE MODULE. `ReportArtifactStore.put` takes `bytes`: it would load a 9.8 GB file into memory. This streams
from a file object in blocks, so memory is a few blocks regardless of file size.

CONTRACT
  * Existing Azure resources only: the report-artifact storage account and (by default) its container, under the key
    prefix `iqvia-original-<sha256>/`. Authentication is DefaultAzureCredential (the app's managed identity, which
    holds Storage Blob Data Contributor on the account); shared keys are disabled on the account and not supported.
  * Immutable by the service: every write uses `overwrite=False`. Re-preserving identical bytes is idempotent (the
    existing blob's size and recorded sha256 must match, or it is refused); a different file under the same key is
    refused, never replaced.
  * Fail-closed: when a durable backend is configured and the write or its read-back fails, `preserve_file` raises.
    When no backend is configured it returns an explicit "not preserved" record, never an implied success.
  * The whole-file SHA-256 is supplied by the caller (computed from the file the importer will read) and stored in
    the blob's metadata with the size. Azure per-block transport checksums (`validate_content`) protect the transfer;
    the end-to-end SHA-256 is verified against the blob by `verify_original` (a streamed read, used by restore).
  * Nothing here logs a path, a filename that came from a client, or file content.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BACKEND_ENV = "IQVIA_ORIGINAL_BACKEND"                # "azure_blob" to enable; anything else = not preserved
CONTAINER_ENV = "IQVIA_ORIGINAL_AZURE_CONTAINER"      # default: the report-artifact container
ACCOUNT_ENV = "REPORT_ARTIFACT_AZURE_ACCOUNT"
DEFAULT_CONTAINER_ENV = "REPORT_ARTIFACT_AZURE_CONTAINER"
KEY_PREFIX = "iqvia-original-"
BLOCK_BYTES = 4 * 1024 * 1024
MAX_CONCURRENCY = 2                                   # bounds memory to ~ BLOCK_BYTES * MAX_CONCURRENCY
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class OriginalStoreError(RuntimeError):
    pass


class OriginalNotPreserved(OriginalStoreError):
    """Raised when a durable backend is configured but the original could not be preserved and verified."""


def configured() -> bool:
    return os.getenv(BACKEND_ENV, "").strip().lower() in ("azure_blob", "azure")


def not_preserved_record(reason: str) -> Dict[str, Any]:
    return {"preserved": False, "reason": reason}


def blob_name(sha256: str) -> str:
    if not _SHA_RE.match(sha256 or ""):
        raise OriginalStoreError("sha256 must be 64 lowercase hex characters")
    return f"{KEY_PREFIX}{sha256}/original.csv"


def _container_client():
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:  # pragma: no cover
        raise OriginalStoreError("azure-storage-blob and azure-identity are required") from exc
    account = os.getenv(ACCOUNT_ENV, "")
    container = os.getenv(CONTAINER_ENV, "") or os.getenv(DEFAULT_CONTAINER_ENV, "")
    if not account or not container:
        raise OriginalStoreError(f"{ACCOUNT_ENV} and a container ({CONTAINER_ENV} or {DEFAULT_CONTAINER_ENV}) are required")
    return BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net",
                             credential=DefaultAzureCredential()).get_container_client(container), container


def preserve_file(path: Path, sha256: str, *, client_factory=None) -> Dict[str, Any]:
    """Stream `path` into the durable store and read the properties back. Returns the locator record that goes on
    `source_snapshot.metadata['durable_original']`. Blocking (synchronous SDK): call from a worker thread."""
    if not configured():
        return not_preserved_record("no durable original backend is configured")
    from azure.core.exceptions import ResourceExistsError

    path = Path(path)
    size = path.stat().st_size
    name = blob_name(sha256)
    try:
        container_client, container = (client_factory or _container_client)()
        blob = container_client.get_blob_client(name)
        meta = {"sha256": sha256, "size_bytes": str(size), "preserved_at": datetime.now(timezone.utc).isoformat()}
        try:
            with open(path, "rb") as fh:
                blob.upload_blob(fh, length=size, overwrite=False, metadata=meta, max_concurrency=MAX_CONCURRENCY,
                                 validate_content=True)
            outcome = "written"
        except ResourceExistsError:
            outcome = "already_present"
        props = blob.get_blob_properties()
        got_size = int(getattr(props, "size", -1))
        got_sha = ((getattr(props, "metadata", None) or {}).get("sha256") or "")
        if got_size != size:
            raise OriginalNotPreserved(f"stored size {got_size} != source size {size}")
        if got_sha != sha256:
            raise OriginalNotPreserved("the blob under this key records a different sha256; refusing to treat it as this file")
    except OriginalStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed, never leak the SDK message (it can carry URLs)
        logger.error("durable original NOT preserved: %s", type(exc).__name__)
        raise OriginalNotPreserved(f"{type(exc).__name__} while preserving the original") from exc
    return {"preserved": True, "outcome": outcome, "backend": "azure_blob", "container": container, "blob": name,
            "locator": f"azureblob://{container}/{name}", "sha256": sha256, "size_bytes": size,
            "stored_at": datetime.now(timezone.utc).isoformat(), "verified": "size+recorded_sha256",
            "end_to_end_sha256_verified": False}


def verify_original(sha256: str, *, client_factory=None) -> bool:
    """Streamed read-back: True only if the stored bytes hash to `sha256`. Memory bounded by one block."""
    container_client, _ = (client_factory or _container_client)()
    stream = container_client.get_blob_client(blob_name(sha256)).download_blob(max_concurrency=1)
    h = hashlib.sha256()
    for chunk in stream.chunks():
        h.update(chunk)
    return h.hexdigest() == sha256
