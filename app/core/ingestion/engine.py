"""The pipeline, once, for every program.

    ACQUIRE → HASH → IDENTIFY VERSION → IDEMPOTENCY → PRESERVE RAW
           → PARSE → NORMALIZE → DATA QUALITY → OBSERVATION HANDOFF

ORDER IS THE DESIGN
───────────────────
The raw bytes are preserved BEFORE anything parses them. A parser that crashes
on a malformed delivery must not also lose the delivery: the artefact is the
evidence, and it has to exist even when the run does not finish. This is the
same ordering `rce/intake.py` already uses, lifted so a second program does not
have to rediscover why.

The identity check happens AFTER acquisition and BEFORE any write. That is what
makes a re-run cheap and safe: the second run recognises the artefact, records
that it ran, and stops — rather than writing a second copy of the same evidence
under a new id.

WHERE THIS STOPS
Phase 5 owns the pipeline through the observation and issue handoff. It does not
classify, does not decide a disposition, and does not resolve a methodology
question. A rule that needs a COR decision is registered as blocked and reported
as blocked; the engine never substitutes a default.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from app.core.evidence_provenance import new_correlation_id
from app.core.ingestion.contracts import (
    ACQUIRED,
    ACQUISITION_FAILED,
    NOTHING_TO_ACQUIRE,
    AcquisitionResult,
    IngestionRepository,
    Normalizer,
    Parser,
    ParsedBatch,
    RawArtifactStore,
    SourceConnector,
)
from app.core.ingestion.quality import (
    DataQualityCategory,
    DataQualityFinding,
    RuleSet,
    Severity,
)
from app.core.ingestion.security import DEFAULT_MAX_ATTEMPTS, SecurityViolation, redact
from app.core.ingestion.states import IngestionState
from app.core.ingestion.telemetry import IngestionTelemetry

logger = logging.getLogger(__name__)

#: Rule id used when the engine itself records that a parser failed. Namespaced
#: so it cannot collide with a program's own rule ids.
CORE_PARSER_FAILURE_RULE = "CORE.PARSE.001"
CORE_SCHEMA_DRIFT_RULE = "CORE.SCHEMA.001"
CORE_RULE_VERSION = "1.0"


class _Stage:
    """Times a stage without letting the timing hide an exception."""

    def __init__(self, telemetry: IngestionTelemetry, name: str) -> None:
        self.telemetry = telemetry
        self.name = name

    def __enter__(self) -> "_Stage":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        elapsed = int((time.perf_counter() - self._start) * 1000)
        self.telemetry.stage_durations_ms[self.name] = elapsed
        return None


class IngestionEngine:
    """Runs one source through the pipeline.

    The engine holds no program semantics. Everything program-specific arrives
    as a port: the connector knows the source, the parser knows the format, the
    normalizer knows the field map, the rule set knows the checks, and the
    repository knows where the program keeps its data.
    """

    def __init__(
        self,
        *,
        repository: IngestionRepository,
        artifact_store: Optional[RawArtifactStore] = None,
        audit: Optional[Any] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.audit = audit
        self.max_attempts = max_attempts

    # ── acquisition, with retry that is safe to repeat ──────────────────────

    async def _acquire(self, connector: SourceConnector,
                       telemetry: IngestionTelemetry,
                       request: Dict[str, Any]) -> AcquisitionResult:
        """Attempt acquisition, retrying only what is worth retrying.

        Retries are safe because nothing has been written yet — acquisition is
        the one stage with no side effect on our side.
        """
        last: Optional[AcquisitionResult] = None
        for attempt in range(1, self.max_attempts + 1):
            telemetry.attempt = attempt
            try:
                last = await connector.acquire(**request)
            except SecurityViolation as exc:
                # A control refused. Never retried: the answer will not change,
                # and repeating a refused request is itself a signal worth not
                # sending.
                raise
            except Exception as exc:  # noqa: BLE001 — classified, not swallowed
                last = AcquisitionResult(
                    descriptor=connector.describe(),
                    status=ACQUISITION_FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    retryable=True,
                )
            if last.status != ACQUISITION_FAILED or not last.retryable:
                return last
            telemetry.retry_count = attempt
            if attempt < self.max_attempts:
                logger.info("ingestion retry %s/%s for %s: %s", attempt,
                            self.max_attempts, connector.describe().key(),
                            redact(last.error or ""))
        return last  # type: ignore[return-value]

    # ── the run ─────────────────────────────────────────────────────────────

    async def run(
        self,
        connector: SourceConnector,
        *,
        parser: Optional[Parser] = None,
        normalizer: Optional[Normalizer] = None,
        rule_set: Optional[RuleSet] = None,
        request: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> IngestionTelemetry:
        descriptor = connector.describe()
        telemetry = IngestionTelemetry(
            program=descriptor.program,
            source_name=descriptor.source_name,
            correlation_id=correlation_id or new_correlation_id(),
        )
        if rule_set is not None:
            telemetry.blocked_rules = rule_set.blocked_report()

        await self._emit("ingestion.started", telemetry, {})

        # ── ACQUIRE ─────────────────────────────────────────────────────────
        telemetry.mark(IngestionState.ACQUIRING)
        try:
            with _Stage(telemetry, "acquire"):
                result = await self._acquire(connector, telemetry, request or {})
        except SecurityViolation as exc:
            telemetry.fail(f"security control refused: {exc}", retryable=False)
            await self._close(telemetry, None)
            return telemetry

        if result.status == ACQUISITION_FAILED:
            telemetry.fail(result.error or "acquisition failed",
                           retryable=result.retryable)
            await self._close(telemetry, None)
            return telemetry

        telemetry.artifact_sha256 = result.sha256
        telemetry.source_version = result.version_ref().effective_dataset_version
        telemetry.artifact_identity = result.identity()
        telemetry.mark(IngestionState.ACQUIRED)

        if result.status == NOTHING_TO_ACQUIRE:
            # The source answered and has nothing. A fact, recorded as a clean
            # completion with zero records — not an error, and not silence.
            telemetry.mark(IngestionState.COMPLETED)
            await self._close(telemetry, None)
            return telemetry

        # ── IDEMPOTENCY ─────────────────────────────────────────────────────
        with _Stage(telemetry, "identity"):
            existing = await self.repository.find_by_identity(
                telemetry.artifact_identity)
        if existing:
            telemetry.duplicate_of_run = existing
            telemetry.duplicates_suppressed = 1
            telemetry.mark(IngestionState.COMPLETED)
            await self._emit("ingestion.duplicate_suppressed", telemetry,
                             {"existing_run": existing})
            await self._close(telemetry, None)
            return telemetry

        run_id = await self.repository.open_run(result, telemetry.artifact_identity)

        # ── PRESERVE RAW, before any parser sees the bytes ──────────────────
        if result.raw is not None and self.artifact_store is not None:
            with _Stage(telemetry, "preserve"):
                result.storage_uri = await self.artifact_store.preserve(
                    result.raw,
                    sha256=result.sha256 or "",
                    filename=result.artifact_filename or f"{descriptor.source_name}.bin",
                )

        # ── PARSE ───────────────────────────────────────────────────────────
        findings: List[DataQualityFinding] = []
        batch = ParsedBatch()
        if parser is not None and result.raw is not None:
            telemetry.mark(IngestionState.PARSING)
            with _Stage(telemetry, "parse"):
                try:
                    batch = parser.parse(
                        result.raw, filename=result.artifact_filename)
                except Exception as exc:  # noqa: BLE001
                    batch = ParsedBatch(parser_version=getattr(parser, "version",
                                                               "unknown"),
                                        fatal_error=f"{type(exc).__name__}: {exc}")
            if not batch.ok:
                # The artefact is already preserved, so the delivery is not lost.
                # This is a permanent failure: the same bytes will fail the same
                # way until a parser or the source changes.
                findings.append(DataQualityFinding(
                    rule_id=CORE_PARSER_FAILURE_RULE,
                    rule_version=CORE_RULE_VERSION,
                    category=DataQualityCategory.PARSER_FAILURE,
                    severity=Severity.CRITICAL,
                    description=(f"parser {batch.parser_version} could not read "
                                 f"the artefact: {redact(batch.fatal_error or '')}"),
                ))
                telemetry.issues_created = await self.repository.record_findings(
                    run_id, findings)
                telemetry.fail(f"parse failed: {redact(batch.fatal_error or '')}",
                               retryable=False)
                await self._close(telemetry, run_id)
                return telemetry

        telemetry.records_received = len(batch.records)

        # ── NORMALIZE ───────────────────────────────────────────────────────
        normalized: Dict[int, List[Any]] = {}
        if normalizer is not None and batch.records:
            with _Stage(telemetry, "normalize"):
                for record in batch.records:
                    normalized[record.line_number] = list(
                        normalizer.normalize(record))

        # ── DATA QUALITY ────────────────────────────────────────────────────
        telemetry.mark(IngestionState.VALIDATING)
        with _Stage(telemetry, "persist_records"):
            written = await self.repository.record_records(run_id, batch)
        telemetry.records_accepted = written
        telemetry.records_rejected = max(0, telemetry.records_received - written)

        if batch.schema_fingerprint and result.metadata.get("expected_fingerprint"):
            if batch.schema_fingerprint != result.metadata["expected_fingerprint"]:
                telemetry.schema_drift = True
                findings.append(DataQualityFinding(
                    rule_id=CORE_SCHEMA_DRIFT_RULE,
                    rule_version=CORE_RULE_VERSION,
                    category=DataQualityCategory.SCHEMA_DRIFT,
                    severity=Severity.HIGH,
                    description=(
                        f"delivery schema fingerprint {batch.schema_fingerprint} "
                        f"differs from the locked map "
                        f"{result.metadata['expected_fingerprint']}. Recorded, "
                        f"not rejected."),
                ))

        if findings:
            with _Stage(telemetry, "findings"):
                telemetry.issues_created += await self.repository.record_findings(
                    run_id, findings)
            for finding in findings:
                key = finding.severity.value
                telemetry.issues_by_severity[key] = (
                    telemetry.issues_by_severity.get(key, 0) + 1)

        telemetry.mark(IngestionState.COMPLETED_WITH_ISSUES if telemetry.issues_created
                       else IngestionState.COMPLETED)
        await self._close(telemetry, run_id)
        return telemetry

    # ── helpers ─────────────────────────────────────────────────────────────

    async def _close(self, telemetry: IngestionTelemetry,
                     run_id: Optional[str]) -> None:
        if run_id is not None:
            await self.repository.close_run(run_id, telemetry.state.value,
                                            telemetry.as_dict())
        await self._emit("ingestion.finished", telemetry, {})
        logger.info(telemetry.summary_line())

    async def _emit(self, event: str, telemetry: IngestionTelemetry,
                    detail: Dict[str, Any]) -> None:
        if self.audit is None:
            return
        payload = {
            "program": telemetry.program,
            "source": telemetry.source_name,
            "correlation_id": telemetry.correlation_id,
            "state": telemetry.state.value,
            "artifact_identity": telemetry.artifact_identity,
            **detail,
        }
        try:
            await self.audit.emit(event, payload)
        except Exception:  # noqa: BLE001 — auditing must not fail an ingestion
            logger.warning("audit sink refused %s", event, exc_info=True)
