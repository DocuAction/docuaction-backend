"""The RCE delivery, expressed as an implementation of the Core framework.

WHY AN ADAPTER RATHER THAN A REWRITE
────────────────────────────────────
`reader.py`, `field_map.py` and `quality_rules.py` already read, normalise and
check an RCE delivery, and they do it against 23,566 records of live Area 1
evidence with a passing test suite behind them. Rewriting that onto a new
framework would put working, audited code at risk to prove an abstraction.

So the framework is proved the other way round: these adapters present the
existing components through the Core ports. The delivery is read by the same
reader, normalised by the same field map, and checked by the same rules — and
the engine drives them. If the adapter is right, the framework is real; if the
framework needed the pipeline rewritten to fit, the framework would be wrong.

WHAT IS TEFCA HERE AND WOULD NOT BE IN ANOTHER PROGRAM
The delimiter and encoding conventions, the 27-column field map, the schema
fingerprint, what counts as an identifier, and every rule id. None of that is in
Core, and none of it needs to be.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from app.core.ingestion.contracts import (
    ACQUIRED,
    ACQUISITION_FAILED,
    AcquisitionResult,
    NormalizedValue,
    ParsedBatch,
    ParsedRecord,
    SourceDescriptor,
)
from app.core.evidence_provenance import RetrievalMethod
from app.core.ingestion.security import (
    SecurityViolation,
    enforce_size,
)
from app.tefca_registry.rce import intake as rce_intake
from app.tefca_registry.rce.field_map import (
    EXPECTED_SCHEMA_FINGERPRINT,
    FIELD_MAP_VERSION,
    RCE_FIELDS,
)
from app.tefca_registry.rce.reader import (
    DelimiterUndecidable,
    PARSE_OK,
    read_delivery,
)

PROGRAM = "TEFCA"
RCE_SOURCE_NAME = "ONC_RCE_DIRECTORY"
CONNECTOR_VERSION = "1.0"

#: An RCE delivery arrives by operator upload, not by fetch. Bounded anyway:
#: the size limit is a property of what we are willing to hold in memory while
#: hashing, not of who sent it.
MAX_DELIVERY_BYTES = 256 * 1024 * 1024


RCE_DESCRIPTOR = SourceDescriptor(
    program=PROGRAM,
    source_name=RCE_SOURCE_NAME,
    source_type="BULK_ARTEFACT",
    authority="ONC Recognized Coordinating Entity — quarterly directory delivery",
    #: LOCAL_FILE, not DOWNLOAD: nothing is fetched. The delivery arrives and is
    #: preserved, and every observation from it is read back from those bytes —
    #: which is exactly what LOCAL_FILE means in the approved provenance model.
    #: A new enum member for "uploaded" would describe how it reached us rather
    #: than how it is read, and the provenance model records the latter.
    retrieval_method=RetrievalMethod.LOCAL_FILE,
    connector_version=CONNECTOR_VERSION,
    parser_version=FIELD_MAP_VERSION,
    description=("The RCE participant/subparticipant directory. Delivered as a "
                 "delimited file; the delimiter is detected, not assumed."),
    #: The RCE publishes no version label on the file itself. Reproducibility
    #: therefore rests entirely on preserving the artefact and its SHA-256,
    #: which is what `is_point_in_time` will report.
    publishes_version=False,
)


class RceDeliveryConnector:
    """Accepts bytes that already arrived. Makes no network call.

    An upload is still an acquisition: it has a time, a size, a hash and an
    origin, and recording those is what lets somebody later ask what exactly was
    received. The connector shape is the same as a fetching source's so the
    engine does not need to know the difference.
    """

    def __init__(self, *, received_by: Optional[str] = None) -> None:
        self.received_by = received_by

    def describe(self) -> SourceDescriptor:
        return RCE_DESCRIPTOR

    async def acquire(self, *, raw: bytes = b"", filename: str = "delivery.csv",
                      **_ignored: Any) -> AcquisitionResult:
        if not raw:
            return AcquisitionResult(
                descriptor=RCE_DESCRIPTOR,
                status=ACQUISITION_FAILED,
                error="no bytes supplied",
                #: An empty upload will be empty however many times it is
                #: retried. Retrying would only delay telling the operator.
                retryable=False,
            )
        try:
            enforce_size(len(raw), limit=MAX_DELIVERY_BYTES)
        except SecurityViolation:
            raise
        return AcquisitionResult(
            descriptor=RCE_DESCRIPTOR,
            status=ACQUIRED,
            raw=raw,
            artifact_filename=os.path.basename(filename or "delivery.csv"),
            content_type="text/csv",
            metadata={
                "expected_fingerprint": EXPECTED_SCHEMA_FINGERPRINT,
                "received_by": self.received_by,
            },
        )


class RceParser:
    """The existing reader, behind the Core parser port."""

    version = FIELD_MAP_VERSION

    def parse(self, raw: bytes, *, filename: Optional[str] = None) -> ParsedBatch:
        try:
            delivery = read_delivery(raw, expected_fields=RCE_FIELDS)
        except DelimiterUndecidable as exc:
            # The file's shape could not be established, so no line can be
            # trusted. Fatal for parsing; the artefact is already preserved.
            return ParsedBatch(parser_version=self.version,
                               fatal_error=f"delimiter undecidable: {exc}")
        records = [
            ParsedRecord(
                line_number=line.line_number,
                raw_line=line.raw_line,
                fields=dict(line.parsed),
                parse_status=line.parse_status,
                parse_note=line.parse_note,
            )
            for line in delivery.lines
        ]
        return ParsedBatch(
            records=records,
            schema_fingerprint=delivery.schema_fingerprint,
            schema_fields=list(delivery.headers),
            parser_version=self.version,
        )


class RceNormalizer:
    """Normalises without erasing what arrived.

    Every value keeps its original alongside the normalised form and the name of
    the transformation, so the two can be reconciled later. The transformations
    are deliberately the non-substantive ones — trimming and case folding of
    codes. Anything that would change an identity is a finding for a human, not
    a normalisation.
    """

    version = FIELD_MAP_VERSION

    #: Fields whose canonical form is upper case. Codes, not names: upper-casing
    #: an organisation name would be a substantive edit.
    _UPPER = frozenset({"state", "state_cd", "country", "status"})

    def normalize(self, record: ParsedRecord) -> Sequence[NormalizedValue]:
        values: List[NormalizedValue] = []
        for name, raw in record.fields.items():
            if raw is None:
                values.append(NormalizedValue(field_name=name, raw=None,
                                              value=None, rule=None,
                                              parser_version=self.version))
                continue
            trimmed = raw.strip()
            rule = "trim" if trimmed != raw else None
            value = trimmed
            if name.lower() in self._UPPER and trimmed:
                folded = trimmed.upper()
                if folded != value:
                    value = folded
                    rule = f"{rule}+upper" if rule else "upper"
            values.append(NormalizedValue(
                field_name=name, raw=raw, value=value, rule=rule,
                parser_version=self.version))
        return values


class FilesystemArtifactStore:
    """Preserves the original bytes, unmodified, before anything parses them.

    Delegates to `intake.preserve_original`, which is what already writes Area 1
    deliveries — so a framework-driven ingestion and the existing route put the
    artefact in the same place, under the same naming, with the same guarantees.
    """

    async def preserve(self, raw: bytes, *, sha256: str, filename: str) -> str:
        return rce_intake.preserve_original(raw, sha256, filename)

    async def exists(self, sha256: str) -> bool:
        root = rce_intake.storage_root()
        if not os.path.isdir(root):
            return False
        return any(name.startswith(sha256[:16]) for name in os.listdir(root))
