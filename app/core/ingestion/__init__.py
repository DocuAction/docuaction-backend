"""DocuAction ingestion framework — shared engine, program-isolated data.

    SOURCE → ACQUIRE → IDENTIFY VERSION → HASH → IMMUTABLE RAW → PARSE
           → NORMALIZE → DATA QUALITY → OBSERVATIONS → PROVENANCE → ISSUES

Everything above the issue handoff belongs to the program: program rules, human
review, QA and reportability are decided elsewhere, by people with the authority
to decide them.

This package owns NO TABLES. Under Option D
(docs/database_domain_architecture.md) the engine is shared and the data is
program-isolated, so persistence arrives as a port and each program writes into
schema it already owns. That is also why Phase 5 needed no migration.
"""
from app.core.ingestion.contracts import (  # noqa: F401
    ACQUIRED,
    ACQUISITION_FAILED,
    INGESTION_CONTRACT_VERSION,
    NOTHING_TO_ACQUIRE,
    AcquisitionResult,
    AuditSink,
    IngestionRepository,
    NormalizedValue,
    Normalizer,
    ParsedBatch,
    ParsedRecord,
    Parser,
    RawArtifactStore,
    SourceConnector,
    SourceDescriptor,
)
from app.core.ingestion.engine import IngestionEngine  # noqa: F401
from app.core.ingestion.quality import (  # noqa: F401
    DQ_TAXONOMY_VERSION,
    CorrectionAuthority,
    DataQualityCategory,
    DataQualityFinding,
    DataQualityRule,
    RuleSet,
    Severity,
)
from app.core.ingestion.registry import REGISTRY, SourceRegistry  # noqa: F401
from app.core.ingestion.security import (  # noqa: F401
    SecurityViolation,
    UrlPolicy,
    classify_failure,
    enforce_content_type,
    enforce_size,
    redact,
    safe_archive_member,
    validate_url,
)
from app.core.ingestion.states import (  # noqa: F401
    ACTIVE_STATES,
    SUCCESS_STATES,
    TERMINAL_STATES,
    IngestionState,
    from_ppef_state,
    may_transition,
)
from app.core.ingestion.telemetry import IngestionTelemetry  # noqa: F401

__all__ = [
    "IngestionEngine",
    "IngestionState",
    "IngestionTelemetry",
    "SourceRegistry",
    "REGISTRY",
]
