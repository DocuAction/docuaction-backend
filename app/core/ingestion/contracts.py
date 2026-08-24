"""What a source must tell us, and the ports a program plugs into.

THIS MODULE OWNS NO TABLES
──────────────────────────
Under Option D (docs/database_domain_architecture.md) the engine is shared and
the data is program-isolated. So the framework defines ports and the program
supplies the storage: TEFCA persists into `rce_source_intakes`,
`rce_source_records`, `rce_issues` and `tefca_ppef_snapshots`, which it already
owns. Nothing here adds a table, which is also why Phase 5 needs no migration.

WHAT IS NOT FABRICATED
──────────────────────
Every optional field on `AcquisitionResult` is optional because some real source
does not publish it. NPPES has an API version and no dataset version; a CMS
quarterly extract has both; an operator-uploaded CSV has neither. The rule this
module enforces is that an absent value is recorded as absent — `None`, or the
`UNKNOWN_DATASET_VERSION` sentinel from the provenance model — never filled in
with something that looks like an answer. `SourceVersionRef.is_point_in_time`
then reports honestly whether the observation could be reproduced at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from app.core.evidence_provenance import (
    RetrievalMethod,
    SourceVersionRef,
    UNKNOWN_DATASET_VERSION,
    file_sha256,
)

#: Bumped when the shape of an AcquisitionResult changes. Recorded on every run
#: so an old run's fields can be read with the rules that were in force.
INGESTION_CONTRACT_VERSION = "1.0"


class AcquisitionStatus(str):
    """Deliberately a plain string type, not an Enum.

    The set is closed here, but a connector for a source nobody has written yet
    must be able to report something truthful without editing Core. The three
    values below cover every case seen so far.
    """


ACQUIRED = "ACQUIRED"
#: The source answered, and said it has nothing for this request. A fact about
#: the source, not an error, and not an absent result.
NOTHING_TO_ACQUIRE = "NOTHING_TO_ACQUIRE"
ACQUISITION_FAILED = "ACQUISITION_FAILED"

ACQUISITION_STATUSES = (ACQUIRED, NOTHING_TO_ACQUIRE, ACQUISITION_FAILED)


@dataclass(frozen=True)
class SourceDescriptor:
    """Who this source is, independent of any one acquisition.

    `program` is what keeps two programs' sources from colliding in the
    registry, and it is why a TEFCA connector cannot be resolved by an ERP
    ingestion run by accident.
    """

    program: str
    source_name: str
    #: BULK_ARTEFACT (a file), RECORD_LOOKUP (query one entity), STREAM.
    source_type: str
    #: Where the authoritative copy lives. A URL, or a description of the
    #: channel for sources that arrive by other means.
    authority: str
    retrieval_method: RetrievalMethod
    connector_version: str
    #: The parser this source's artefacts are expected to need. Recorded here so
    #: a stored observation can name the parser that produced it.
    parser_version: Optional[str] = None
    description: str = ""
    #: True when the source publishes its own version/edition label. False means
    #: reproducibility depends on preserving the artefact.
    publishes_version: bool = False

    def key(self) -> str:
        return f"{self.program}:{self.source_name}"


@dataclass
class AcquisitionResult:
    """One attempt to obtain something from one source.

    Carries the artefact when there is one. `raw` is bytes and is not persisted
    by this dataclass — `RawArtifactStore` does that, before parsing, so the
    evidence survives a parser that goes wrong.
    """

    descriptor: SourceDescriptor
    status: str
    acquired_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    raw: Optional[bytes] = None
    content_type: Optional[str] = None
    byte_size: Optional[int] = None
    sha256: Optional[str] = None
    #: Where the preserved artefact ended up. Set by the engine, not the
    #: connector — a connector that writes its own storage has no way to be
    #: consistent with the rest of the pipeline.
    storage_uri: Optional[str] = None
    artifact_filename: Optional[str] = None

    #: The source's own edition label, when it publishes one.
    dataset_version: Optional[str] = None
    #: The source's own as-of date, when it publishes one.
    source_as_of: Optional[str] = None
    dataset_identifier: Optional[str] = None
    #: The API's version. Never a stand-in for dataset_version — that
    #: substitution is the specific defect SourceVersionRef exists to prevent.
    api_version: Optional[str] = None

    http_status: Optional[int] = None
    http_last_modified: Optional[str] = None
    request_url: Optional[str] = None

    record_count: Optional[int] = None
    error: Optional[str] = None
    #: Set only when status is ACQUISITION_FAILED. True invites a retry.
    retryable: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in ACQUISITION_STATUSES:
            raise ValueError(
                f"unknown acquisition status {self.status!r}; expected one of "
                f"{ACQUISITION_STATUSES}")
        if self.raw is not None:
            if self.byte_size is None:
                self.byte_size = len(self.raw)
            if self.sha256 is None:
                self.sha256 = file_sha256(self.raw)
        if self.status == ACQUIRED and self.raw is None and self.record_count is None:
            raise ValueError(
                "ACQUIRED with neither an artefact nor a record count says "
                "nothing was actually obtained; use NOTHING_TO_ACQUIRE.")

    @property
    def succeeded(self) -> bool:
        return self.status in (ACQUIRED, NOTHING_TO_ACQUIRE)

    def version_ref(self) -> SourceVersionRef:
        """The provenance record for this acquisition.

        Absent version information stays absent. `is_point_in_time` then reports
        whether this observation could genuinely be reproduced, rather than
        implying it could because a field happened to be populated.
        """
        return SourceVersionRef(
            source=self.descriptor.source_name,
            retrieval_method=self.descriptor.retrieval_method,
            retrieved_at=self.acquired_at,
            dataset_version=self.dataset_version,
            source_as_of=self.source_as_of,
            source_file_hash=self.sha256,
            dataset_identifier=self.dataset_identifier,
            api_version=self.api_version,
            http_last_modified=self.http_last_modified,
            record_count=self.record_count,
            storage_uri=self.storage_uri,
        )

    def identity(self) -> str:
        """What makes this artefact the same artefact as a previous one.

        The file hash when there is a file — two byte-identical deliveries are
        the same delivery whatever they were called. Otherwise the source's own
        version, which is the only thing a versioned API offers. Failing both,
        the acquisition timestamp, which makes the run unique and therefore
        never suppressed as a duplicate: better to ingest twice than to discard
        something we cannot prove we already have.
        """
        if self.sha256:
            return f"sha256:{self.sha256}"
        if self.dataset_version and self.dataset_version != UNKNOWN_DATASET_VERSION:
            return f"{self.descriptor.key()}@{self.dataset_version}"
        return f"{self.descriptor.key()}@retrieved:{self.acquired_at}"


@dataclass
class ParsedRecord:
    """One record as the parser read it, before any normalisation."""

    line_number: int
    raw_line: str
    fields: Dict[str, Any]
    parse_status: str = "ok"
    parse_note: Optional[str] = None

    @property
    def record_sha256(self) -> str:
        return file_sha256(self.raw_line.encode("utf-8"))


@dataclass
class ParsedBatch:
    """What a parser produces from one artefact."""

    records: List[ParsedRecord] = field(default_factory=list)
    #: Fingerprint of the column set the artefact actually had. Drift against
    #: the expected fingerprint is recorded, never a reason to discard.
    schema_fingerprint: Optional[str] = None
    schema_fields: List[str] = field(default_factory=list)
    parser_version: str = "unknown"
    #: A parser that could not read the artefact at all says so here. The raw
    #: bytes are already preserved by the time this is read.
    fatal_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.fatal_error is None


@dataclass
class NormalizedValue:
    """A value after normalisation, with the original still attached.

    Normalisation must not erase what the source said. `raw` is what arrived,
    `value` is what the pipeline will use, and `rule` names the transformation
    so the two can be reconciled months later.
    """

    field_name: str
    raw: Optional[str]
    value: Optional[str]
    rule: Optional[str] = None
    parser_version: Optional[str] = None

    @property
    def changed(self) -> bool:
        return self.raw != self.value


# ── ports the program implements ────────────────────────────────────────────


@runtime_checkable
class SourceConnector(Protocol):
    """Obtains an artefact or a record set from one source."""

    def describe(self) -> SourceDescriptor: ...

    async def acquire(self, **request: Any) -> AcquisitionResult: ...


@runtime_checkable
class Parser(Protocol):
    """Turns raw bytes into records. Must not write anything."""

    version: str

    def parse(self, raw: bytes, *, filename: Optional[str] = None) -> ParsedBatch: ...


@runtime_checkable
class Normalizer(Protocol):
    """Turns a parsed record into normalised values, keeping the originals."""

    version: str

    def normalize(self, record: ParsedRecord) -> Sequence[NormalizedValue]: ...


@runtime_checkable
class RawArtifactStore(Protocol):
    """Preserves the bytes exactly as received, before anything parses them."""

    async def preserve(self, raw: bytes, *, sha256: str,
                       filename: str) -> str: ...

    async def exists(self, sha256: str) -> bool: ...


@runtime_checkable
class IngestionRepository(Protocol):
    """Where a program keeps its ingestion state. Core owns none of it."""

    async def find_by_identity(self, identity: str) -> Optional[str]:
        """Return an existing run id for this artefact identity, or None."""

    async def open_run(self, result: AcquisitionResult, identity: str) -> str:
        """Record the start of a run and return its id."""

    async def record_records(self, run_id: str, batch: ParsedBatch) -> int:
        """Persist the parsed records. Returns how many were written."""

    async def record_findings(self, run_id: str, findings: Sequence[Any]) -> int:
        """Append to the issue ledger. Returns how many were written."""

    async def close_run(self, run_id: str, state: str,
                        telemetry: Dict[str, Any]) -> None:
        """Record the outcome."""


@runtime_checkable
class AuditSink(Protocol):
    """Where ingestion events are audited. Optional; the engine works without."""

    async def emit(self, event: str, detail: Dict[str, Any]) -> None: ...
