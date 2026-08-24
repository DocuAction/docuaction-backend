"""Phase 5 — the ingestion framework, and the boundary it has to hold.

These tests exercise the engine end to end against the REAL RCE reader, field
map and schema fingerprint, using an in-memory repository. That combination is
deliberate: the parsing is genuine, so the framework is proved against a real
format rather than a convenient one, and no row is written to Area 1 to prove
it. The live 23,566 records are evidence, not a test fixture.
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from app.core.evidence_provenance import UNKNOWN_DATASET_VERSION, RetrievalMethod
from app.core.ingestion import (
    ACQUIRED,
    ACQUISITION_FAILED,
    NOTHING_TO_ACQUIRE,
    AcquisitionResult,
    CorrectionAuthority,
    DataQualityCategory,
    DataQualityFinding,
    DataQualityRule,
    IngestionEngine,
    IngestionState,
    RuleSet,
    Severity,
    SourceDescriptor,
    SourceRegistry,
)
from app.core.ingestion.memory import (
    InMemoryArtifactStore,
    InMemoryIngestionRepository,
    RecordingAuditSink,
)
from app.core.ingestion.quality import assert_not_a_disposition
from app.core.ingestion.security import (
    SecurityViolation,
    UrlPolicy,
    classify_failure,
    enforce_content_type,
    enforce_size,
    redact,
    safe_archive_member,
    validate_url,
)
from app.core.ingestion.states import (
    PPEF_STATE_MAP,
    TERMINAL_STATES,
    from_ppef_state,
    may_transition,
)
from app.tefca_registry.rce.field_map import (
    EXPECTED_SCHEMA_FINGERPRINT,
    FIELD_MAP_VERSION,
    RCE_FIELDS,
)
from app.tefca_registry.rce.ingestion_adapter import (
    RCE_DESCRIPTOR,
    RceDeliveryConnector,
    RceNormalizer,
    RceParser,
)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ── fixtures: a real RCE-shaped delivery ────────────────────────────────────


def _delivery(rows=2, *, header=None, delimiter="|") -> bytes:
    """A delivery with the real column set, so the real parser can read it."""
    columns = list(header or RCE_FIELDS)
    lines = [delimiter.join(columns)]
    for index in range(rows):
        lines.append(delimiter.join(f"v{index}_{n}" for n in range(len(columns))))
    return "\r\n".join(lines).encode("utf-8")


@pytest.fixture()
def engine_parts():
    repository = InMemoryIngestionRepository()
    store = InMemoryArtifactStore()
    audit = RecordingAuditSink()
    engine = IngestionEngine(repository=repository, artifact_store=store,
                             audit=audit)
    return engine, repository, store, audit


# ── connector contract ──────────────────────────────────────────────────────


class TestConnectorContract:

    def test_descriptor_identifies_program_and_source(self):
        descriptor = RCE_DESCRIPTOR
        assert descriptor.program == "TEFCA"
        assert descriptor.key() == "TEFCA:ONC_RCE_DIRECTORY"
        assert descriptor.connector_version
        assert descriptor.parser_version == FIELD_MAP_VERSION

    def test_acquisition_carries_the_metadata_the_contract_requires(self):
        raw = _delivery()
        result = run(RceDeliveryConnector(received_by="tester").acquire(
            raw=raw, filename="rce.csv"))
        assert result.status == ACQUIRED
        assert result.byte_size == len(raw)
        assert result.sha256 == hashlib.sha256(raw).hexdigest()
        assert result.content_type == "text/csv"
        assert result.artifact_filename == "rce.csv"
        assert result.acquired_at

    def test_absent_version_is_recorded_as_absent_not_invented(self):
        """The RCE publishes no dataset version. That must stay visible."""
        result = run(RceDeliveryConnector().acquire(raw=_delivery()))
        ref = result.version_ref()
        assert ref.dataset_version is None
        assert ref.effective_dataset_version == UNKNOWN_DATASET_VERSION
        assert ref.api_version is None

    def test_a_preserved_artefact_makes_the_observation_reproducible(self):
        result = run(RceDeliveryConnector().acquire(raw=_delivery()))
        assert result.version_ref().is_point_in_time is True

    def test_a_live_lookup_without_an_artefact_is_not_reproducible(self):
        """An API answer with no preserved bytes cannot be reproduced later."""
        descriptor = SourceDescriptor(
            program="TEFCA", source_name="NPPES", source_type="RECORD_LOOKUP",
            authority="https://npiregistry.cms.hhs.gov/api/",
            retrieval_method=RetrievalMethod.API, connector_version="1.0")
        result = AcquisitionResult(descriptor=descriptor, status=ACQUIRED,
                                   record_count=1, api_version="2.1")
        ref = result.version_ref()
        assert ref.is_point_in_time is False
        assert ref.effective_dataset_version == UNKNOWN_DATASET_VERSION, (
            "an API version must never be recorded as a dataset version")

    def test_acquired_with_nothing_obtained_is_refused(self):
        with pytest.raises(ValueError, match="NOTHING_TO_ACQUIRE"):
            AcquisitionResult(descriptor=RCE_DESCRIPTOR, status=ACQUIRED)

    def test_empty_upload_fails_and_is_not_retryable(self):
        result = run(RceDeliveryConnector().acquire(raw=b""))
        assert result.status == ACQUISITION_FAILED
        assert result.retryable is False


# ── hashing, versioning, identity ───────────────────────────────────────────


class TestArtifactIdentity:

    def test_hash_is_the_sha256_of_the_bytes(self):
        raw = _delivery(3)
        result = run(RceDeliveryConnector().acquire(raw=raw))
        assert result.sha256 == hashlib.sha256(raw).hexdigest()

    def test_identical_bytes_have_the_same_identity_under_different_names(self):
        raw = _delivery(3)
        first = run(RceDeliveryConnector().acquire(raw=raw, filename="a.csv"))
        second = run(RceDeliveryConnector().acquire(raw=raw, filename="b.csv"))
        assert first.identity() == second.identity()

    def test_changed_bytes_have_a_different_identity(self):
        first = run(RceDeliveryConnector().acquire(raw=_delivery(3)))
        second = run(RceDeliveryConnector().acquire(raw=_delivery(4)))
        assert first.identity() != second.identity()

    def test_a_versioned_source_without_a_file_is_identified_by_its_version(self):
        descriptor = SourceDescriptor(
            program="TEFCA", source_name="CMS_PPEF_ENROLLMENT",
            source_type="BULK_ARTEFACT", authority="https://data.cms.gov",
            retrieval_method=RetrievalMethod.DOWNLOAD, connector_version="1.0",
            publishes_version=True)
        result = AcquisitionResult(descriptor=descriptor, status=ACQUIRED,
                                   record_count=10, dataset_version="2026.07.17")
        assert result.identity() == "TEFCA:CMS_PPEF_ENROLLMENT@2026.07.17"

    def test_an_unidentifiable_acquisition_is_never_suppressed_as_duplicate(self):
        """No hash and no version means we cannot prove we already have it."""
        descriptor = SourceDescriptor(
            program="TEFCA", source_name="NPPES", source_type="RECORD_LOOKUP",
            authority="x", retrieval_method=RetrievalMethod.API,
            connector_version="1.0")
        first = AcquisitionResult(descriptor=descriptor, status=ACQUIRED,
                                  record_count=1)
        second = AcquisitionResult(descriptor=descriptor, status=ACQUIRED,
                                   record_count=1,
                                   acquired_at="2026-08-22T10:00:01+00:00")
        assert first.identity() != second.identity()


# ── the pipeline ────────────────────────────────────────────────────────────


class TestPipeline:

    def test_first_ingestion_runs_the_whole_pipeline(self, engine_parts):
        engine, repository, store, audit = engine_parts
        raw = _delivery(3)
        telemetry = run(engine.run(
            RceDeliveryConnector(), parser=RceParser(),
            normalizer=RceNormalizer(),
            request={"raw": raw, "filename": "rce.csv"}))
        assert telemetry.state is IngestionState.COMPLETED
        assert telemetry.records_received == 3
        assert telemetry.artifact_sha256 == hashlib.sha256(raw).hexdigest()
        assert hashlib.sha256(raw).hexdigest() in store.artifacts
        assert len(repository.runs) == 1
        assert "ingestion.started" in audit.names()
        assert "ingestion.finished" in audit.names()

    def test_records_are_parsed_and_counted(self, engine_parts):
        engine, repository, _store, _audit = engine_parts
        telemetry = run(engine.run(
            RceDeliveryConnector(), parser=RceParser(),
            normalizer=RceNormalizer(),
            request={"raw": _delivery(5), "filename": "rce.csv"}))
        assert telemetry.records_received == 5
        assert telemetry.records_accepted == 5
        assert telemetry.records_rejected == 0
        assert len(repository.records[list(repository.runs)[0]]) == 5

    def test_the_artefact_is_preserved_before_parsing(self, engine_parts):
        """Preservation must not depend on the parse succeeding."""
        engine, _repository, store, _audit = engine_parts
        raw = b"not,a,valid|delivery"        # one line, no rows: parses, no records
        run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                       request={"raw": raw, "filename": "broken.csv"}))
        assert hashlib.sha256(raw).hexdigest() in store.artifacts

    def test_a_parser_failure_preserves_the_artefact_and_fails_permanently(
            self, engine_parts):
        engine, repository, store, _audit = engine_parts

        class Exploding:
            version = "test"

            def parse(self, raw, *, filename=None):
                raise RuntimeError("parser blew up")

        raw = _delivery(2)
        telemetry = run(engine.run(RceDeliveryConnector(), parser=Exploding(),
                                   request={"raw": raw}))
        assert telemetry.state is IngestionState.PERMANENT_FAILURE
        assert telemetry.retryable is False
        assert hashlib.sha256(raw).hexdigest() in store.artifacts, (
            "the delivery must survive a parser that crashes")
        findings = repository.findings[list(repository.runs)[0]]
        assert any(f.category is DataQualityCategory.PARSER_FAILURE
                   for f in findings)

    def test_normalization_keeps_the_original_value(self):
        from app.core.ingestion.contracts import ParsedRecord
        record = ParsedRecord(line_number=2, raw_line="x",
                              fields={"state": " ca ", "org_name": " Acme "})
        values = {v.field_name: v for v in RceNormalizer().normalize(record)}
        assert values["state"].raw == " ca "
        assert values["state"].value == "CA"
        assert values["state"].rule == "trim+upper"
        assert values["state"].changed is True
        # An organisation name is trimmed but never case-folded: that would be
        # a substantive edit to a name, not a normalisation.
        assert values["org_name"].value == "Acme"
        assert values["org_name"].rule == "trim"

    def test_schema_drift_is_recorded_and_does_not_reject_the_delivery(
            self, engine_parts):
        engine, repository, _store, _audit = engine_parts
        shifted = list(RCE_FIELDS)
        shifted[0], shifted[1] = shifted[1], shifted[0]
        telemetry = run(engine.run(
            RceDeliveryConnector(), parser=RceParser(),
            request={"raw": _delivery(2, header=shifted)}))
        assert telemetry.schema_drift is True
        assert telemetry.state is IngestionState.COMPLETED_WITH_ISSUES
        assert telemetry.records_accepted == 2, (
            "a schema change is exactly what must not be silently discarded")

    def test_the_locked_fingerprint_matches_an_unchanged_header(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts
        telemetry = run(engine.run(
            RceDeliveryConnector(), parser=RceParser(),
            request={"raw": _delivery(2)}))
        assert telemetry.schema_drift is False
        assert EXPECTED_SCHEMA_FINGERPRINT


# ── idempotency ─────────────────────────────────────────────────────────────


class TestIdempotency:

    def test_the_same_artefact_twice_does_not_duplicate_evidence(self, engine_parts):
        engine, repository, _store, audit = engine_parts
        raw = _delivery(4)
        first = run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                               request={"raw": raw}))
        second = run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                                request={"raw": raw}))
        assert first.records_accepted == 4
        assert second.records_accepted == 0
        assert second.duplicates_suppressed == 1
        assert second.duplicate_of_run is not None
        assert len(repository.runs) == 1, "a duplicate must not open a second run"
        assert "ingestion.duplicate_suppressed" in audit.names(), (
            "a suppressed duplicate is still a job execution and must be audited")

    def test_a_new_version_is_ingested_without_touching_the_old_one(
            self, engine_parts):
        engine, repository, store, _audit = engine_parts
        first_raw = _delivery(3)
        second_raw = _delivery(4)
        run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                       request={"raw": first_raw}))
        run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                       request={"raw": second_raw}))
        assert len(repository.runs) == 2
        # both artefacts still preserved, neither overwritten
        assert hashlib.sha256(first_raw).hexdigest() in store.artifacts
        assert hashlib.sha256(second_raw).hexdigest() in store.artifacts
        assert store.artifacts[hashlib.sha256(first_raw).hexdigest()] == first_raw

    def test_preserving_the_same_artefact_twice_cannot_change_it(self):
        store = InMemoryArtifactStore()
        digest = hashlib.sha256(b"original").hexdigest()
        run(store.preserve(b"original", sha256=digest, filename="a"))
        run(store.preserve(b"tampered", sha256=digest, filename="a"))
        assert store.artifacts[digest] == b"original"


# ── retry and failure ───────────────────────────────────────────────────────


class TestRetryAndFailure:

    def test_a_retryable_acquisition_is_retried_then_succeeds(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts

        class Flaky:
            def __init__(self):
                self.calls = 0

            def describe(self):
                return RCE_DESCRIPTOR

            async def acquire(self, **request):
                self.calls += 1
                if self.calls < 3:
                    return AcquisitionResult(
                        descriptor=RCE_DESCRIPTOR, status=ACQUISITION_FAILED,
                        error="503 from upstream", retryable=True)
                return AcquisitionResult(descriptor=RCE_DESCRIPTOR,
                                         status=ACQUIRED, raw=_delivery(2))

        connector = Flaky()
        telemetry = run(engine.run(connector, parser=RceParser()))
        assert connector.calls == 3
        assert telemetry.state is IngestionState.COMPLETED
        assert telemetry.retry_count == 2

    def test_a_permanent_failure_is_not_retried(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts

        class Gone:
            def __init__(self):
                self.calls = 0

            def describe(self):
                return RCE_DESCRIPTOR

            async def acquire(self, **request):
                self.calls += 1
                return AcquisitionResult(
                    descriptor=RCE_DESCRIPTOR, status=ACQUISITION_FAILED,
                    error="404 not found", retryable=False)

        connector = Gone()
        telemetry = run(engine.run(connector, parser=RceParser()))
        assert connector.calls == 1, "a 404 will recur; retrying hides it"
        assert telemetry.state is IngestionState.PERMANENT_FAILURE

    def test_retries_stop_at_the_limit(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts

        class AlwaysFlaky:
            def __init__(self):
                self.calls = 0

            def describe(self):
                return RCE_DESCRIPTOR

            async def acquire(self, **request):
                self.calls += 1
                return AcquisitionResult(
                    descriptor=RCE_DESCRIPTOR, status=ACQUISITION_FAILED,
                    error="timeout", retryable=True)

        connector = AlwaysFlaky()
        telemetry = run(engine.run(connector, parser=RceParser()))
        assert connector.calls == engine.max_attempts
        assert telemetry.state is IngestionState.RETRYABLE_FAILURE

    def test_a_retry_after_a_successful_run_is_suppressed_not_duplicated(
            self, engine_parts):
        """Retries must be idempotent, not merely permitted."""
        engine, repository, _store, _audit = engine_parts
        raw = _delivery(3)
        run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                       request={"raw": raw}))
        again = run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                               request={"raw": raw}))
        assert again.duplicates_suppressed == 1
        assert sum(len(v) for v in repository.records.values()) == 3

    def test_nothing_to_acquire_is_a_clean_completion_not_an_error(
            self, engine_parts):
        engine, _repository, _store, _audit = engine_parts

        class Empty:
            def describe(self):
                return RCE_DESCRIPTOR

            async def acquire(self, **request):
                return AcquisitionResult(descriptor=RCE_DESCRIPTOR,
                                         status=NOTHING_TO_ACQUIRE)

        telemetry = run(engine.run(Empty(), parser=RceParser()))
        assert telemetry.state is IngestionState.COMPLETED
        assert telemetry.records_received == 0
        assert telemetry.error_reason is None

    def test_failure_classification_separates_transient_from_terminal(self):
        assert classify_failure(503)[0] is True
        assert classify_failure(429)[0] is True
        assert classify_failure(404)[0] is False
        assert classify_failure(403)[0] is False
        assert classify_failure(None, TimeoutError("slow"))[0] is True
        assert classify_failure(None, ValueError("bad"))[0] is False


# ── state vocabulary ────────────────────────────────────────────────────────


class TestStates:

    def test_every_existing_ppef_state_maps(self):
        """Two vocabularies that map are one; two that drift are a defect."""
        from app.Tefca.models import TEFCAPPEFIngestJob as job
        declared = {v for k, v in vars(job).items() if k.startswith("STATE_")}
        assert declared <= set(PPEF_STATE_MAP), (
            f"unmapped PPEF states: {sorted(declared - set(PPEF_STATE_MAP))}")
        for state in declared:
            assert isinstance(from_ppef_state(state), IngestionState)

    def test_an_unknown_ppef_state_is_not_guessed(self):
        with pytest.raises(ValueError, match="unmapped"):
            from_ppef_state("SOMETHING_NEW")

    def test_a_terminal_state_is_terminal(self):
        for state in TERMINAL_STATES:
            assert not may_transition(state, IngestionState.PARSING)

    def test_progress_only_moves_forward(self):
        assert may_transition(IngestionState.QUEUED, IngestionState.ACQUIRING)
        assert not may_transition(IngestionState.PARSING, IngestionState.ACQUIRING)
        assert may_transition(IngestionState.PARSING,
                              IngestionState.PERMANENT_FAILURE)


# ── data quality, and the line it must not cross ────────────────────────────


class TestDataQuality:

    def test_a_finding_may_not_state_a_disposition(self):
        with pytest.raises(ValueError, match="methodology term"):
            DataQualityFinding(
                rule_id="X.1", rule_version="1", severity=Severity.HIGH,
                category=DataQualityCategory.MISSING_REQUIRED_FIELD,
                description="entity is BUCKET_2 because the NPI is missing")

    def test_the_guard_names_the_offending_term(self):
        with pytest.raises(ValueError, match="VERIFIED"):
            assert_not_a_disposition("this entity is VERIFIED", where="test")

    def test_a_plain_data_statement_is_accepted(self):
        finding = DataQualityFinding(
            rule_id="X.2", rule_version="1", severity=Severity.HIGH,
            category=DataQualityCategory.MISSING_REQUIRED_FIELD,
            description="npi is empty on this record")
        assert finding.category is DataQualityCategory.MISSING_REQUIRED_FIELD

    def test_identity_findings_can_never_be_auto_corrected(self):
        with pytest.raises(ValueError, match="AUTO_SAFE"):
            DataQualityFinding(
                rule_id="X.3", rule_version="1", severity=Severity.HIGH,
                category=DataQualityCategory.MALFORMED_IDENTIFIER,
                description="npi is 9 digits",
                correction_authority=CorrectionAuthority.AUTO_SAFE)

    def test_a_rule_set_refuses_two_rules_with_one_id(self):
        rules = RuleSet("TEFCA", "1.0")
        rules.register(DataQualityRule(
            rule_id="R1", version="1", description="a",
            category=DataQualityCategory.INVALID_FORMAT))
        with pytest.raises(ValueError, match="already registered"):
            rules.register(DataQualityRule(
                rule_id="R1", version="2", description="b",
                category=DataQualityCategory.INVALID_FORMAT))

    def test_a_blocked_rule_is_reported_and_not_run(self):
        rules = RuleSet("TEFCA", "1.0")
        rules.register(DataQualityRule(
            rule_id="R-D1", version="1", description="needs the pecos decision",
            category=DataQualityCategory.CONFLICTING_SOURCE_VALUES,
            blocked_by="D1"))
        rules.register(DataQualityRule(
            rule_id="R-OK", version="1", description="runs",
            category=DataQualityCategory.INVALID_FORMAT))
        assert [r.rule_id for r in rules.runnable] == ["R-OK"]
        assert rules.blocked_report() == [
            {"rule_id": "R-D1", "blocked_by": "D1",
             "description": "needs the pecos decision"}]

    def test_blocked_rules_reach_the_telemetry(self, engine_parts):
        """'No findings' must never be mistaken for 'nothing to find'."""
        engine, _repository, _store, _audit = engine_parts
        rules = RuleSet("TEFCA", "1.0")
        rules.register(DataQualityRule(
            rule_id="R-D2", version="1", description="needs a confidence model",
            category=DataQualityCategory.CONFLICTING_SOURCE_VALUES,
            blocked_by="D2"))
        telemetry = run(engine.run(RceDeliveryConnector(), parser=RceParser(),
                                   rule_set=rules,
                                   request={"raw": _delivery(2)}))
        assert telemetry.blocked_rules == [
            {"rule_id": "R-D2", "blocked_by": "D2",
             "description": "needs a confidence model"}]


# ── security ────────────────────────────────────────────────────────────────


class TestSecurity:

    def test_a_loopback_host_is_refused(self):
        with pytest.raises(SecurityViolation, match="loopback"):
            validate_url("https://localhost/x", UrlPolicy())

    def test_the_cloud_metadata_address_is_refused(self):
        with pytest.raises(SecurityViolation, match="link-local"):
            validate_url("https://169.254.169.254/latest/meta-data/",
                         UrlPolicy())

    def test_a_private_address_is_refused(self):
        with pytest.raises(SecurityViolation, match="private"):
            validate_url("https://10.0.0.1/internal", UrlPolicy())

    def test_plain_http_is_refused(self):
        with pytest.raises(SecurityViolation, match="scheme"):
            validate_url("http://data.cms.gov/x", UrlPolicy())

    def test_a_host_outside_the_allow_list_is_refused(self):
        policy = UrlPolicy(allowed_hosts=frozenset({"data.cms.gov"}))
        with pytest.raises(SecurityViolation, match="allow-list"):
            validate_url("https://evil.example.com/x", policy)

    def test_an_oversized_payload_is_refused(self):
        with pytest.raises(SecurityViolation, match="over the"):
            enforce_size(2048, limit=1024)

    def test_content_type_is_compared_without_its_charset(self):
        assert enforce_content_type("text/csv; charset=utf-8", ["text/csv"]) == "text/csv"
        with pytest.raises(SecurityViolation, match="not one of"):
            enforce_content_type("application/zip", ["text/csv"])

    @pytest.mark.parametrize("name", [
        "../../etc/passwd", "/etc/passwd", "a/../../b", "C:\\windows\\system32",
    ])
    def test_archive_entries_cannot_escape_the_destination(self, name, tmp_path):
        with pytest.raises(SecurityViolation):
            safe_archive_member(name, destination=str(tmp_path))

    def test_a_benign_archive_entry_is_allowed(self, tmp_path):
        target = safe_archive_member("data/file.csv", destination=str(tmp_path))
        assert target.startswith(str(tmp_path))

    def test_credentials_are_redacted_before_anything_records_them(self):
        assert "secret123" not in redact(
            "https://api.sam.gov/x?api_key=secret123&q=1")
        assert "REDACTED" in redact("https://api.sam.gov/x?api_key=secret123")
        assert "REDACTED" in redact("Bearer abcdefghijklmnop")

    def test_a_security_refusal_is_never_retried(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts

        class Hostile:
            def __init__(self):
                self.calls = 0

            def describe(self):
                return RCE_DESCRIPTOR

            async def acquire(self, **request):
                self.calls += 1
                raise SecurityViolation("host resolves to 127.0.0.1 (loopback)")

        connector = Hostile()
        telemetry = run(engine.run(connector, parser=RceParser()))
        assert connector.calls == 1
        assert telemetry.state is IngestionState.PERMANENT_FAILURE
        assert "security control refused" in (telemetry.error_reason or "")

    def test_an_error_reason_is_redacted_in_telemetry(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts

        class Leaky:
            def describe(self):
                return RCE_DESCRIPTOR

            async def acquire(self, **request):
                return AcquisitionResult(
                    descriptor=RCE_DESCRIPTOR, status=ACQUISITION_FAILED,
                    error="failed calling https://api.sam.gov/x?api_key=hunter2xyz",
                    retryable=False)

        telemetry = run(engine.run(Leaky(), parser=RceParser()))
        assert "hunter2xyz" not in (telemetry.error_reason or "")


# ── telemetry ───────────────────────────────────────────────────────────────


class TestTelemetry:

    def test_an_operator_can_answer_what_that_run_did(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts
        telemetry = run(engine.run(
            RceDeliveryConnector(), parser=RceParser(),
            normalizer=RceNormalizer(),
            request={"raw": _delivery(6), "filename": "rce.csv"}))
        data = telemetry.as_dict()
        for key in ("program", "source_name", "state", "started_at",
                    "finished_at", "duration_ms", "source_version",
                    "artifact_sha256", "records_received", "records_accepted",
                    "records_rejected", "observations_created",
                    "duplicates_suppressed", "issues_created", "retry_count",
                    "error_reason", "correlation_id"):
            assert key in data, f"telemetry is missing {key}"
        assert data["duration_ms"] is not None

    def test_the_summary_line_carries_no_record_content(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts
        telemetry = run(engine.run(
            RceDeliveryConnector(), parser=RceParser(),
            request={"raw": _delivery(2)}))
        line = telemetry.summary_line()
        assert "v0_0" not in line and "v1_0" not in line

    def test_stage_durations_are_recorded(self, engine_parts):
        engine, _repository, _store, _audit = engine_parts
        telemetry = run(engine.run(
            RceDeliveryConnector(), parser=RceParser(),
            request={"raw": _delivery(2)}))
        assert "acquire" in telemetry.stage_durations_ms
        assert "parse" in telemetry.stage_durations_ms


# ── cross-program isolation ─────────────────────────────────────────────────


class TestCrossProgramIsolation:

    def test_two_programs_may_share_a_source_name_without_collision(self):
        registry = SourceRegistry()
        tefca = SourceDescriptor(
            program="TEFCA", source_name="NPPES", source_type="RECORD_LOOKUP",
            authority="a", retrieval_method=RetrievalMethod.API,
            connector_version="1")
        other = SourceDescriptor(
            program="ERP", source_name="NPPES", source_type="RECORD_LOOKUP",
            authority="b", retrieval_method=RetrievalMethod.API,
            connector_version="1")
        registry.register(tefca, lambda: object())
        registry.register(other, lambda: object())
        assert registry.describe("TEFCA", "NPPES").authority == "a"
        assert registry.describe("ERP", "NPPES").authority == "b"

    def test_a_program_cannot_resolve_another_programs_source(self):
        registry = SourceRegistry()
        registry.register(SourceDescriptor(
            program="ERP", source_name="SUPPLIERS", source_type="BULK_ARTEFACT",
            authority="x", retrieval_method=RetrievalMethod.DOWNLOAD,
            connector_version="1"), lambda: object())
        with pytest.raises(LookupError, match="no connector registered"):
            registry.get("TEFCA", "SUPPLIERS")

    def test_the_same_key_cannot_be_registered_twice(self):
        registry = SourceRegistry()
        descriptor = SourceDescriptor(
            program="TEFCA", source_name="X", source_type="BULK_ARTEFACT",
            authority="x", retrieval_method=RetrievalMethod.DOWNLOAD,
            connector_version="1")
        registry.register(descriptor, lambda: object())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(descriptor, lambda: object())


# ── TEFCA source declarations ───────────────────────────────────────────────


class TestTefcaSources:

    def test_all_five_external_sources_are_declared(self):
        from app.Tefca.ingestion_sources import TEFCA_SOURCES
        names = {d.source_name for d in TEFCA_SOURCES}
        assert names == {"NPPES", "CMS_PPEF_ENROLLMENT", "CMS_REVOCATION",
                         "OIG_LEIE", "SAM_GOV"}

    def test_every_declared_source_names_an_allow_listed_host(self):
        from app.Tefca.ingestion_sources import HOST_POLICIES, TEFCA_SOURCES
        for descriptor in TEFCA_SOURCES:
            policy = HOST_POLICIES[descriptor.source_name]
            assert policy.allowed_hosts, f"{descriptor.source_name} has no allow-list"
            assert policy.allow_private_addresses is False

    def test_versioned_and_unversioned_sources_are_told_apart(self):
        from app.Tefca.ingestion_sources import (
            CMS_PPEF_ENROLLMENT, NPPES, OIG_LEIE, SAM_GOV)
        assert CMS_PPEF_ENROLLMENT.publishes_version is True
        assert OIG_LEIE.publishes_version is True
        assert NPPES.publishes_version is False
        assert SAM_GOV.publishes_version is False

    def test_the_registry_reports_only_rce_as_runnable(self):
        from app.Tefca.ingestion_sources import register_all
        registry = SourceRegistry()
        register_all(registry)
        assert len(registry.for_program("TEFCA")) == 6
        rce = registry.get("TEFCA", "ONC_RCE_DIRECTORY")
        assert isinstance(rce, RceDeliveryConnector)

    def test_an_unwired_source_says_so_rather_than_returning_an_empty_success(self):
        """A zero that means 'not built' must not look like 'source has nothing'."""
        from app.Tefca.ingestion_sources import register_all
        registry = SourceRegistry()
        register_all(registry)
        connector = registry.get("TEFCA", "CMS_PPEF_ENROLLMENT")
        result = run(connector.acquire())
        assert result.status == ACQUISITION_FAILED
        assert result.retryable is False
        assert "Phase 6" in (result.error or "")


# ── Area 1 and the framework ────────────────────────────────────────────────


class TestArea1Boundary:

    def test_the_framework_grants_itself_no_update_or_delete(self):
        """Ingestion writes new evidence; it never edits delivered evidence."""
        import inspect

        from app.core.ingestion import contracts, engine as engine_module

        for module in (contracts, engine_module):
            source = inspect.getsource(module)
            for forbidden in ("UPDATE ", "DELETE FROM", "TRUNCATE"):
                assert forbidden not in source.upper(), (
                    f"{module.__name__} contains {forbidden!r}; the ingestion "
                    f"framework must not be able to mutate delivered evidence")

    def test_the_repository_port_offers_no_mutation_of_written_records(self):
        from app.core.ingestion.contracts import IngestionRepository
        methods = {m for m in dir(IngestionRepository) if not m.startswith("_")}
        for banned in ("update_record", "delete_record", "overwrite",
                       "replace_records"):
            assert banned not in methods
