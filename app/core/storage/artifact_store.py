"""
Durable storage for finalised artifacts. CORE — no program knowledge.

WHAT THIS IS FOR
────────────────
A finalised report is a record of what somebody was told. Six months later the
question is not "what do the numbers say now" but "what did the document we
issued actually say". Answering that needs the bytes, unchanged, addressable,
and provably the ones that were sent.

Until now finalised reports lived only in a Postgres column. That is durable in
the ordinary sense — it is backed up with the database — but it gives no content
address, no immutability at the storage layer, and no retention metadata. A row
can be updated. Bytes in a content-addressed store cannot be updated without
becoming different bytes.

WHY THIS MODULE KNOWS NOTHING ABOUT TEFCA
─────────────────────────────────────────
Storing a finalised artifact is the same problem for every program: hash it,
write it once, never overwrite it, be able to hand it back. Report type, cycle,
evidence version, methodology version and determination references are *program*
facts, and they travel through `metadata` as an opaque mapping. The moment this
file knows what a "review cycle" is, the next program cannot use it.

The program-side registry that gives those fields names and columns lives in
`app/reports/data/artifact_registry.py`.

IMMUTABILITY, CONCRETELY
────────────────────────
`put()` refuses to replace the content of a key that already holds different
bytes. It does not raise on a *re-put of identical content* — regenerating the
same report from the same evidence is a normal, safe operation and should be
idempotent, not an error. Different content under the same logical key produces
a NEW VERSION, and the previous version stays retrievable. Nothing in this
module can delete or rewrite a finalised artifact; there is no method for it.

RETENTION IS DELIBERATELY NOT ENFORCED YET
──────────────────────────────────────────
The contractual retention period is open decision D8. A retention policy is
recorded on every artifact so an approved period can be applied later without
touching report semantics, and `worm_locked` is False everywhere. Locking
irreversible retention before the period is approved is the one mistake here
that cannot be undone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Keys become path segments. Anything outside this cannot be a key, which is
#: what keeps a caller-supplied identifier from escaping the store's root.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,190}$")


class ArtifactStoreError(RuntimeError):
    """Base for every failure this module raises."""


class ArtifactStoreUnconfigured(ArtifactStoreError):
    """A backend was selected that has not been given what it needs."""


class ArtifactNotFound(ArtifactStoreError):
    """No artifact exists at that locator."""


class ArtifactImmutable(ArtifactStoreError):
    """An attempt to change the bytes of a finalised artifact."""


class InvalidArtifactKey(ArtifactStoreError):
    """A key that cannot safely become a storage path."""


# ── retention ────────────────────────────────────────────────────────────────

#: The period is unset because it is a program decision, not a default.
RETENTION_PENDING = "PROGRAM_GUIDANCE_REQUESTED"


@dataclass(frozen=True)
class RetentionPolicy:
    """What is intended to happen to an artifact over time.

    `period_days` is None until the program says otherwise. A None period means
    "keep, pending guidance" — never "delete when convenient", and never a
    silently-chosen default. `worm_locked` stays False until an approved period
    exists, because a WORM lock is not reversible and D8 is not answered.
    """

    classification: str = RETENTION_PENDING
    period_days: Optional[int] = None
    worm_locked: bool = False
    basis: Optional[str] = None

    def with_approved_period(self, days: int, *, basis: str,
                             lock: bool = False) -> "RetentionPolicy":
        """The transition this design exists to make possible.

        Applying an approved period must not require changing how reports are
        produced or read — only this value. `lock` is opt-in and separate: a
        period can be recorded long before anyone is willing to make it
        irreversible.
        """
        if days <= 0:
            raise ValueError("retention period must be a positive number of days")
        if not basis:
            raise ValueError("an approved retention period needs a recorded basis")
        return RetentionPolicy(classification="APPROVED", period_days=days,
                               worm_locked=bool(lock), basis=basis)

    @property
    def is_pending(self) -> bool:
        return self.period_days is None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── the record a store hands back ────────────────────────────────────────────

@dataclass(frozen=True)
class StoredArtifact:
    """One immutable stored object."""

    artifact_id: str
    key: str
    version: int
    locator: str
    content_sha256: str
    size_bytes: int
    content_type: str
    stored_at: str
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: True when `put` found byte-identical content already stored under this
    #: key and returned it instead of writing again.
    deduplicated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["retention"] = self.retention.to_dict()
        return d


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not _SAFE_KEY.match(key):
        raise InvalidArtifactKey(
            f"{key!r} is not a usable artifact key. Keys are 1-191 characters of "
            f"letters, digits, dot, underscore or hyphen, starting alphanumeric.")
    return key


# ── the interface ────────────────────────────────────────────────────────────

class ReportArtifactStore(ABC):
    """Write-once artifact storage.

    There is no `delete` and no `overwrite`, by design. A backend that needs
    cleanup does it out of band, under whatever retention policy the program
    eventually approves — not through an application call path that a bug could
    reach.
    """

    #: Names the backend in provenance records and logs.
    backend: str = "abstract"

    @abstractmethod
    def put(self, key: str, content: bytes, *, content_type: str = "text/html",
            metadata: Optional[Dict[str, Any]] = None,
            retention: Optional[RetentionPolicy] = None) -> StoredArtifact:
        """Store bytes under `key`, returning the stored record.

        Idempotent for identical content. Different content under an existing
        key stores a new version; it never replaces the old one.
        """

    @abstractmethod
    def get(self, locator: str) -> bytes:
        """The exact bytes stored at `locator`."""

    @abstractmethod
    def head(self, locator: str) -> StoredArtifact:
        """The stored record at `locator`, without the bytes."""

    @abstractmethod
    def versions(self, key: str) -> list:
        """Every version stored under `key`, oldest first."""

    def verify(self, locator: str) -> bool:
        """Re-hash the stored bytes and compare to what was recorded.

        The check that makes the rest of it meaningful: a content hash nobody
        ever recomputes is a claim, not evidence.
        """
        record = self.head(locator)
        return content_sha256(self.get(locator)) == record.content_sha256


# ── local filesystem backend ─────────────────────────────────────────────────

class LocalFilesystemArtifactStore(ReportArtifactStore):
    """Development and single-host backend.

    Layout is `<root>/<key>/<version>/artifact.<ext>` with a sidecar
    `artifact.json` holding the record. Version directories are created with
    `os.mkdir`, which fails if the directory exists — so two concurrent writers
    cannot both believe they own the same version, and immutability does not
    depend on a check-then-write race.
    """

    backend = "local"

    _EXT = {"text/html": "html", "application/pdf": "pdf", "text/csv": "csv",
            "application/json": "json",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx"}

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _key_dir(self, key: str) -> str:
        return os.path.join(self.root, validate_key(key))

    def _record_path(self, version_dir: str) -> str:
        return os.path.join(version_dir, "artifact.json")

    def _read_record(self, version_dir: str) -> StoredArtifact:
        with open(self._record_path(version_dir), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["retention"] = RetentionPolicy(**raw.get("retention") or {})
        raw.pop("deduplicated", None)
        return StoredArtifact(**raw)

    def versions(self, key: str) -> list:
        key_dir = self._key_dir(key)
        if not os.path.isdir(key_dir):
            return []
        out = []
        for name in sorted(os.listdir(key_dir), key=lambda n: int(n) if n.isdigit() else 0):
            version_dir = os.path.join(key_dir, name)
            if name.isdigit() and os.path.exists(self._record_path(version_dir)):
                out.append(self._read_record(version_dir))
        return out

    def put(self, key: str, content: bytes, *, content_type: str = "text/html",
            metadata: Optional[Dict[str, Any]] = None,
            retention: Optional[RetentionPolicy] = None) -> StoredArtifact:
        validate_key(key)
        if not isinstance(content, (bytes, bytearray)):
            raise ArtifactStoreError("artifact content must be bytes")
        content = bytes(content)
        digest = content_sha256(content)

        existing = self.versions(key)
        for record in existing:
            if record.content_sha256 == digest:
                # Same bytes already stored. Regenerating an unchanged report is
                # normal; treating it as a collision would make the safe case
                # look like the dangerous one.
                return StoredArtifact(**{**record.to_dict(),
                                         "retention": record.retention,
                                         "deduplicated": True})

        key_dir = self._key_dir(key)
        os.makedirs(key_dir, exist_ok=True)
        version = len(existing) + 1
        while True:
            version_dir = os.path.join(key_dir, str(version))
            try:
                os.mkdir(version_dir)
                break
            except FileExistsError:
                # Another writer took this version. Take the next one rather
                # than overwrite theirs.
                version += 1

        ext = self._EXT.get(content_type, "bin")
        filename = f"artifact.{ext}"
        with open(os.path.join(version_dir, filename), "wb") as fh:
            fh.write(content)

        record = StoredArtifact(
            artifact_id=str(uuid.uuid4()), key=key, version=version,
            locator=f"local://{key}/{version}/{filename}",
            content_sha256=digest, size_bytes=len(content),
            content_type=content_type,
            stored_at=datetime.now(timezone.utc).isoformat(),
            retention=retention or RetentionPolicy(),
            metadata=dict(metadata or {}))
        with open(self._record_path(version_dir), "w", encoding="utf-8") as fh:
            json.dump(record.to_dict(), fh, indent=2, sort_keys=True, default=str)
        return record

    def _resolve(self, locator: str) -> str:
        if not locator.startswith("local://"):
            raise ArtifactNotFound(f"{locator!r} is not a local artifact locator")
        rel = locator[len("local://"):]
        parts = rel.split("/")
        if len(parts) != 3:
            raise ArtifactNotFound(f"{locator!r} is not a well-formed locator")
        validate_key(parts[0])
        if not parts[1].isdigit() or "/" in parts[2] or parts[2].startswith("."):
            raise ArtifactNotFound(f"{locator!r} is not a well-formed locator")
        path = os.path.abspath(os.path.join(self.root, *parts))
        # Belt and braces on top of key validation: the resolved path must still
        # be inside the root.
        if not path.startswith(self.root + os.sep):
            raise ArtifactNotFound(f"{locator!r} resolves outside the store")
        return path

    def get(self, locator: str) -> bytes:
        path = self._resolve(locator)
        if not os.path.exists(path):
            raise ArtifactNotFound(locator)
        with open(path, "rb") as fh:
            return fh.read()

    def head(self, locator: str) -> StoredArtifact:
        path = self._resolve(locator)
        version_dir = os.path.dirname(path)
        if not os.path.exists(self._record_path(version_dir)):
            raise ArtifactNotFound(locator)
        return self._read_record(version_dir)


# ── Azure Blob backend ───────────────────────────────────────────────────────

class AzureBlobArtifactStore(ReportArtifactStore):
    """Azure Blob backend.

    NOT EXERCISED. There is no storage account in `infra/` and
    `azure-storage-blob` is not in requirements.txt, so this has never run
    against Azure and is not claimed to work. It exists so the seam is real
    rather than hypothetical, and so the configuration contract is written down.

    Credentials are never taken as arguments and never read from source. The
    account comes from `REPORT_ARTIFACT_AZURE_ACCOUNT` and the container from
    `REPORT_ARTIFACT_AZURE_CONTAINER`; authentication is `DefaultAzureCredential`,
    which resolves managed identity in Azure. A connection string is deliberately
    not supported — it is the form of this configuration most likely to end up
    in a log or a commit.
    """

    backend = "azure_blob"

    def __init__(self, account: Optional[str] = None,
                 container: Optional[str] = None):
        self.account = account or os.getenv("REPORT_ARTIFACT_AZURE_ACCOUNT", "")
        self.container = container or os.getenv("REPORT_ARTIFACT_AZURE_CONTAINER", "")
        if not self.account or not self.container:
            raise ArtifactStoreUnconfigured(
                "Azure artifact storage needs REPORT_ARTIFACT_AZURE_ACCOUNT and "
                "REPORT_ARTIFACT_AZURE_CONTAINER. No storage account is currently "
                "provisioned in infra/, so this backend is not yet usable.")

    def _client(self):
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover - package not installed
            raise ArtifactStoreUnconfigured(
                "azure-storage-blob and azure-identity are not installed. They "
                "are deliberately absent from requirements.txt until a storage "
                "account exists to talk to.") from exc
        return BlobServiceClient(
            account_url=f"https://{self.account}.blob.core.windows.net",
            credential=DefaultAzureCredential()).get_container_client(self.container)

    def put(self, key: str, content: bytes, *, content_type: str = "text/html",
            metadata: Optional[Dict[str, Any]] = None,
            retention: Optional[RetentionPolicy] = None) -> StoredArtifact:
        raise ArtifactStoreUnconfigured(  # pragma: no cover
            "The Azure backend has never been exercised against a real account "
            "and must not be used until it has been. Provision storage, add the "
            "packages, then implement and test this method.")

    def get(self, locator: str) -> bytes:  # pragma: no cover
        raise ArtifactStoreUnconfigured("Azure backend not exercised.")

    def head(self, locator: str) -> StoredArtifact:  # pragma: no cover
        raise ArtifactStoreUnconfigured("Azure backend not exercised.")

    def versions(self, key: str) -> list:  # pragma: no cover
        raise ArtifactStoreUnconfigured("Azure backend not exercised.")


# ── selection ────────────────────────────────────────────────────────────────

DEFAULT_LOCAL_ROOT = os.path.join("uploads", "report_artifacts")

_store: Optional[ReportArtifactStore] = None


def build_artifact_store(backend: Optional[str] = None,
                         root: Optional[str] = None) -> ReportArtifactStore:
    """Construct a store from configuration.

    `REPORT_ARTIFACT_BACKEND` selects; local is the default because it is the
    one that works. Selecting azure without configuration raises rather than
    silently falling back to local — a report the operator believes is in Azure
    and is actually on a container's ephemeral disk is worse than an error.
    """
    backend = (backend or os.getenv("REPORT_ARTIFACT_BACKEND", "local")).strip().lower()
    if backend == "local":
        return LocalFilesystemArtifactStore(
            root or os.getenv("REPORT_ARTIFACT_ROOT", DEFAULT_LOCAL_ROOT))
    if backend in ("azure", "azure_blob"):
        return AzureBlobArtifactStore()
    raise ArtifactStoreUnconfigured(f"unknown artifact backend {backend!r}")


def get_artifact_store() -> ReportArtifactStore:
    """Process-wide store."""
    global _store
    if _store is None:
        _store = build_artifact_store()
    return _store


def reset_artifact_store() -> None:
    """Drop the cached store. For tests."""
    global _store
    _store = None
