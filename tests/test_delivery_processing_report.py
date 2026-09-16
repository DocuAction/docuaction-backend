"""Delivery Processing Report - built from persisted evidence, pinned to one
delivery and one reconciliation snapshot, identical across HTML / CSV / dataset.

WHAT IS PINNED
──────────────
  * every RCE report type REFUSES to run without parameters.job_id or
    parameters.intake_id (no newest-delivery default), and the refusal is a
    ReportParameterError the API maps to 422 with a machine code;
  * a job id resolves to its intake through rce_delivery_jobs.source_intake_id;
  * the dataset carries the pinned snapshot's id, hash and creation time and
    the HTML prints them on page one with the build SHA, migration revision and
    template version;
  * the record-level totals in the CSV, the HTML and the dataset agree;
  * regeneration from an earlier snapshot states which snapshot it used and
    never silently adopts newer live data;
  * a snapshot that belongs to another job is refused.

HOW THE DATABASE TESTS AVOID TOUCHING GOVERNMENT DATA
─────────────────────────────────────────────────────
Same pattern as test_curated_text_columns.py: every commit lands in a
savepoint inside an outer transaction that is rolled back. All fixture data is
synthetic (OIDs under an unassigned 9.99.999 arc, placeholder names, the
published NPI check-digit example values).
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

SYN = "SYNTHETIC-DPR-TEST"
VALID_NPI = "1234567893"      # the published check-digit example; not a real provider
INVALID_NPI = "1234567890"    # fails the Luhn check


class FakeResult:
    def scalars(self): return self
    def all(self): return []
    def scalar(self): return None
    def scalar_one_or_none(self): return None
    def first(self): return None


class FakeDB:
    async def execute(self, *a, **k): return FakeResult()
    async def get(self, *a, **k): return None
    def add(self, *a, **k): pass
    async def commit(self): pass
    async def rollback(self): pass
    async def flush(self): pass


# ── no database: the identifier is mandatory ─────────────────────────────────

class TestIdentifierIsRequired:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("report_type", ["delivery_processing", "data_quality", "intake"])
    async def test_every_rce_type_refuses_without_a_delivery(self, report_type):
        from app.reports import generator

        with pytest.raises(generator.ReportParameterError) as excinfo:
            await generator.generate_report(FakeDB(), report_type=report_type, persist=False)
        assert excinfo.value.code == "DELIVERY_IDENTIFIER_REQUIRED"
        assert excinfo.value.status == 422
        assert "never defaults to the newest delivery" in str(excinfo.value)

    def test_parameter_error_is_a_generation_error_the_api_maps_to_422(self):
        from app.reports.generator import ReportGenerationError, ReportParameterError
        from app.reports.routes import parameter_error_http

        exc = ReportParameterError("no delivery named")
        assert isinstance(exc, ReportGenerationError)
        http = parameter_error_http(exc)
        assert http.status_code == 422
        assert http.detail == {"error": "no delivery named",
                               "code": "DELIVERY_IDENTIFIER_REQUIRED"}
        not_found = parameter_error_http(
            ReportParameterError("gone", code="DELIVERY_NOT_FOUND", status=404))
        assert not_found.status_code == 404 and not_found.detail["code"] == "DELIVERY_NOT_FOUND"

    def test_delivery_processing_is_a_registered_rce_type(self):
        from app.reports import generator
        from app.reports.data.report_snapshot import REPORT_TYPES

        assert "delivery_processing" in generator.RCE_TYPES
        assert generator.TEMPLATES["delivery_processing"] == "delivery_processing.html"
        assert "delivery_processing" in REPORT_TYPES

    def test_the_newest_intake_default_is_gone(self):
        import inspect

        from app.reports.data import rce_report_data

        source = inspect.getsource(rce_report_data.RceReportDataService._intake)
        assert "received_at.desc()" not in source
        assert "ReportParameterError" in source


# ── synthetic delivery ───────────────────────────────────────────────────────

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def seed_delivery(db, *, with_snapshot: bool = True, label: str = SYN) -> dict:
    """One fully processed synthetic delivery with every evidence table populated.

    Five delivered lines: CREATED, UPDATED, HELD (then analyst -> EXCLUDED),
    REJECTED, MISSING_KEY. Two snapshots: sequence 1 (PIPELINE, before the
    analyst decision) and sequence 2 (DISPOSITION, after it). Returns ids.
    """
    from app.core import request_context
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import dispositions, identifier_decisions, stage_events
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import traceability_models as tm
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    now = datetime.utcnow().replace(microsecond=0)
    t0 = now - timedelta(minutes=10)
    headers = ["id", "name", "NPI", "address_city"]
    blob = "|".join(headers) + "\r\n"
    intake_id = uuid.uuid4()
    lines = [
        ("9.99.999.1.1", f"{label} ORG ONE", VALID_NPI, "Testville", "ok"),
        ("9.99.999.1.2", f"{label} ORG TWO", "", "Newtown", "ok"),
        ("9.99.999.1.3", f"{label} ORG THREE", INVALID_NPI, "Testville", "ok"),
        ("9.99.999.1.4", f"{label} ORG FOUR|broken", "", "", "field_count_mismatch"),
        ("", "", "", "Testville", "ok"),
    ]
    for l in lines:
        blob += "|".join(l[:4]) + "\r\n"
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=f"{label}-INTAKE", original_filename="synthetic_dpr.psv",
        storage_path="(synthetic)", sha256=_sha(blob), file_size_bytes=len(blob),
        delimiter="|", encoding="utf-8", line_terminator="CRLF", headers=headers,
        schema_fingerprint=_sha("|".join(headers)), record_count=len(lines),
        received_at=t0, received_by=label, status="PROCESSED",
        source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()

    records = []
    for i, (oid, name, npi, city, status) in enumerate(lines, start=2):
        raw = "|".join((oid, name, npi, city))
        rec = m.RceSourceRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, line_number=i, raw_line=raw,
            parsed={"id": oid, "name": name, "NPI": npi, "address_city": city},
            record_sha256=_sha(raw), source_rce_id=oid or None, npi=npi or None,
            field_count=4, parse_status=status,
            promotion_status="promoted" if i in (2, 3) else ("held" if i == 4 else "excluded"))
        db.add(rec)
        records.append(rec)
    await db.flush()

    job = RceDeliveryJob(
        id=uuid.uuid4(), identity=_sha(label + "job"), delivery_label=f"{label}-INTAKE",
        original_filename="synthetic_dpr.psv", storage_path="(synthetic)", sha256=_sha(blob),
        file_size_bytes=len(blob), declared_delimiter="|", received_date=t0,
        government_reference="SYN-REF-001", source_name="ONC/RCE (synthetic)",
        state="SUCCEEDED", stage="READY_FOR_REVIEW", active_marker=None,
        created_at=t0, started_at=t0 + timedelta(seconds=5),
        completed_at=now - timedelta(minutes=1), attempt_count=1, registered_by=label,
        source_intake_id=intake_id, records_received=len(lines), records_processed=len(lines),
        reconciliation_passed=True, stage_detail={})
    db.add(job)
    await db.flush()

    # existing registry entity (matched by line 3) created BEFORE the job window
    existing = reg.TefcaRegEntity(
        id=uuid.uuid4(), name=f"{label} ORG TWO", entity_level="participant",
        entity_type="provider", created_at=t0 - timedelta(days=30))
    created = reg.TefcaRegEntity(
        id=uuid.uuid4(), name=f"{label} ORG ONE", entity_level="participant",
        entity_type="provider", created_at=t0 + timedelta(minutes=3))
    db.add_all([existing, created])
    await db.flush()
    db.add(reg.TefcaEntityIdentifier(entity_id=created.id, identifier_type="npi",
                                     identifier_value=VALID_NPI, identifier_status="active"))
    db.add(reg.TefcaEntityVersion(entity_id=existing.id, version_number=2,
                                  snapshot_data={"address_city": "Newtown"},
                                  change_reason="rce_delivery_update",
                                  created_at=t0 + timedelta(minutes=4)))

    run = m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="1.2.0",
        rule_config_hash=_sha("rules"), started_at=t0 + timedelta(minutes=1),
        completed_at=t0 + timedelta(minutes=2), records_evaluated=len(lines),
        issues_generated=2, run_status="COMPLETE", executed_by=label)
    db.add(run)
    await db.flush()
    stem = uuid.uuid4().hex[:8].upper()
    held_issue = m.RceIssue(
        id=uuid.uuid4(), issue_code=f"DQ-{stem}-0001", source_intake_id=intake_id,
        source_record_id=records[2].id, run_id=run.id, rule_id="NPI-003",
        rule_version="1.2.0", issue_type="NPI_CHECKSUM_INVALID", severity="HIGH",
        field_name="NPI", original_value=INVALID_NPI, correction_authority="HUMAN_REQUIRED",
        description="NPI fails the Luhn check digit.", resolution="OPEN")
    fixed_issue = m.RceIssue(
        id=uuid.uuid4(), issue_code=f"DQ-{stem}-0002", source_intake_id=intake_id,
        source_record_id=records[0].id, run_id=run.id, rule_id="FMT-001",
        rule_version="1.2.0", issue_type="POSTAL_CODE_FORMAT", severity="LOW",
        field_name="address_postalCode", original_value="1234", suggested_value="01234",
        correction_authority="AUTO_SAFE", description="Postal code zero-padded.",
        resolution="RESOLVED", resolved_by="SYSTEM", resolved_at=t0 + timedelta(minutes=2))
    db.add_all([held_issue, fixed_issue])
    await db.flush()

    curated = {}
    for idx, status, entity in ((0, "CORRECTED", created), (1, "CLEAN", existing),
                                (2, "HELD", None), (3, "REJECTED", None), (4, "REJECTED", None)):
        row = m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=records[idx].id,
            record_status=status, issue_count=1 if idx in (0, 2) else 0,
            correction_count=1 if idx == 0 else 0,
            rce_org_oid=records[idx].source_rce_id, npi=records[idx].npi,
            name=records[idx].parsed["name"] or None, entity_level="participant",
            address_city=records[idx].parsed["address_city"] or None,
            transformation_version="1.0.0",
            canonical_entity_id=entity.id if entity else None,
            promoted_at=(t0 + timedelta(minutes=3)) if entity else None)
        db.add(row)
        curated[idx] = row
    await db.flush()
    db.add(m.RceCorrectionDetail(
        curated_record_id=curated[0].id, source_record_id=records[0].id, issue_id=fixed_issue.id,
        column_name="address_postalCode", original_value="1234", original_value_hash=_sha("1234"),
        corrected_value="01234", correction_reason="FMT-001 zero-pad", correction_rule_id="FMT-001",
        correction_authority="AUTO_SAFE", corrected_by="SYSTEM", confidence="HIGH"))
    db.add(reg.ReviewRecord(review_id=f"REV-{stem[:6]}-000001", source_record_id=records[2].id,
                            classification_bucket="B3", classification_rule="R-NPI"))
    await db.flush()

    # stage timeline
    with request_context.bind(job_id=str(job.id), intake_id=str(intake_id)):
        await stage_events.record_instant(db, job.id, "REGISTERED", intake_id=intake_id, commit=False)
        for stage in ("PARSING", "QUALITY", "CURATION", "PROMOTION", "RECONCILIATION"):
            ev = await stage_events.open_stage(db, job.id, stage, intake_id=intake_id,
                                               input_count=len(lines), commit=False)
            await stage_events.close_stage(db, ev, "COMPLETED", output_count=len(lines),
                                           held_count=1 if stage == "PROMOTION" else None,
                                           commit=False)
        await stage_events.record_instant(db, job.id, "READY_FOR_REVIEW", intake_id=intake_id,
                                          commit=False)

        # sequence-1 SYSTEM dispositions
        plan = [
            (0, dispositions.CREATED, dispositions.REASON_CREATED, created.id, []),
            (1, dispositions.UPDATED, dispositions.REASON_UPDATED, existing.id, ["address_city"]),
            (2, dispositions.HELD, dispositions.REASON_HELD_QUALITY, None, []),
            (3, dispositions.REJECTED, dispositions.REASON_REJECTED_PARSE, None, []),
            (4, dispositions.MISSING_KEY, dispositions.REASON_MISSING_KEY, None, []),
        ]
        for idx, disp, code, entity_id, changed in plan:
            await dispositions.record(
                db, intake_id=intake_id, source_record_id=records[idx].id, disposition=disp,
                reason_code=code, curated_record_id=curated[idx].id, job_id=job.id,
                entity_id=entity_id, changed_fields=changed)
        # identifier conflict on the matched entity, then a human decision
        await identifier_decisions.raise_conflict(
            db, entity_id=existing.id, identifier_type="npi", submitted_value=VALID_NPI,
            existing_value="1497758544", source_record_id=records[1].id, intake_id=intake_id)
        await identifier_decisions.decide(
            db, entity_id=existing.id, identifier_type="npi", decision="CONFIRM_EXISTING",
            reason="Registered value confirmed against NPPES (synthetic).",
            actor="analyst@synthetic.invalid", intake_id=intake_id)

        snapshots = []
        if with_snapshot:
            snapshots.append(_snapshot(tm, job, intake_id, 1, "PIPELINE",
                                       held=1, excluded=0, at=now - timedelta(minutes=2)))
            db.add(snapshots[-1])
            await db.flush()
            # analyst releases the held record as a test record
            await dispositions.record(
                db, intake_id=intake_id, source_record_id=records[2].id,
                disposition=dispositions.EXCLUDED, reason_code=dispositions.REASON_ANALYST,
                reason="Synthetic test record confirmed by analyst.",
                curated_record_id=curated[2].id, job_id=job.id,
                actor="analyst@synthetic.invalid", actor_type="HUMAN")
            snapshots.append(_snapshot(tm, job, intake_id, 2, "DISPOSITION",
                                       held=0, excluded=1, at=now - timedelta(minutes=1)))
            db.add(snapshots[-1])
            await db.flush()
    await db.commit()
    return {
        "job_id": job.id, "intake_id": intake_id, "sha256": _sha(blob),
        "record_ids": [r.id for r in records], "snapshot_ids": [s.id for s in snapshots],
        "created_entity_id": created.id, "existing_entity_id": existing.id,
        "received": len(lines),
    }


def _snapshot(tm, job, intake_id, sequence, trigger, *, held, excluded, at):
    eq = {"received": 5, "created": 1, "updated": 1, "matched_unchanged": 0,
          "held": held, "rejected": 1, "missing_key": 1, "excluded": excluded}
    return tm.RceReconciliationSnapshot(
        id=uuid.uuid4(), job_id=job.id, intake_id=intake_id, sequence=sequence, passed=True,
        **eq, dimensions={"A_received": 5, "D_curated": 5, "E_promoted": 2},
        checks=[{"name": "A == D", "passed": True, "detail": "5 == 5"}],
        source_evidence={"tables": ["rce_source_records", "rce_curated_records"]},
        actor="SYSTEM", trigger=trigger, created_at=at.replace(tzinfo=timezone.utc),
        hash=_sha(f"{job.id}:{sequence}:{trigger}"), build_sha="synthetic0",
        migration_revision="20260917_delivery_traceability", correlation_id=str(job.id))


@pytest.fixture
async def rolled_back_db(db_required):
    """Every commit lands in a savepoint that is thrown away at the end."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url

    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    connection = await engine.connect()
    outer = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint",
                           expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
def artifact_root(tmp_path, monkeypatch):
    """Keep the artifact store's bytes out of the repository during tests."""
    from app.core.storage import artifact_store

    monkeypatch.setenv("REPORT_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    artifact_store.reset_artifact_store()
    yield tmp_path
    artifact_store.reset_artifact_store()


async def _generate(db, **params):
    from app.reports.generator import generate_report

    return await generate_report(db, report_type="delivery_processing", persist=False,
                                 query_parameters=params)


def _csv_sections(csv_text: str) -> dict:
    """{section title: [rows]} for a '##'-sectioned report CSV."""
    sections, current = {}, None
    for row in csv.reader(io.StringIO(csv_text)):
        if not row:
            continue
        if row[0].startswith("## "):
            current = row[0][3:]
            sections[current] = []
        elif current and not row[0].startswith("#"):
            sections[current].append(row)
    return sections


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL))


# ── with a database ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_is_built_from_the_persisted_evidence_of_the_named_delivery(rolled_back_db):
    from app.reports.engine.accessibility import validate_html
    from app.reports.engine.chart_engine import TOKENS
    from app.reports.engine.template_engine import TEMPLATE_VERSION

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, job_id=str(ids["job_id"]))
    ds = result["dataset"]

    # identity: the job named, its intake resolved through source_intake_id
    assert ds["delivery"]["job_id"] == str(ids["job_id"])
    assert ds["delivery"]["intake_id"] == str(ids["intake_id"])
    assert ds["delivery"]["resolved_from"] == "job_id"
    assert ds["delivery"]["sha256"] == ids["sha256"]
    assert ds["delivery"]["record_count"] == ids["received"]

    # pinned to the LATEST snapshot, and the dataset says which
    latest = str(ids["snapshot_ids"][-1])
    assert ds["snapshot_id"] == latest and result["snapshot_id"] == latest
    assert ds["snapshot_is_latest"] is True
    assert ds["snapshot_created_at"] and ds["snapshot_hash"]
    assert ds["reconciliation"]["sequence"] == 2 and ds["reconciliation"]["history_count"] == 2

    # the equation and the current table agree
    eq = ds["reconciliation"]["equation"]
    assert eq["holds"] and eq["received"] == 5 and eq["accounted"] == 5
    counts = ds["dispositions"]["counts"]
    assert counts == {"CREATED": 1, "UPDATED": 1, "MATCHED_UNCHANGED": 0, "HELD": 0,
                      "REJECTED": 1, "MISSING_KEY": 1, "EXCLUDED": 1, "total": 5}
    assert ds["dispositions"]["agrees_with_snapshot"] is True
    assert ds["dispositions"]["records_without_disposition"] == 0
    assert ds["dispositions"]["human_count"] == 1
    rows = {r["line_number"]: r for r in ds["dispositions"]["rows"]}
    assert rows[2]["disposition"] == "CREATED" and rows[2]["submitted_npi"] == VALID_NPI
    assert rows[2]["entity_id"] == str(ids["created_entity_id"])
    assert rows[2]["warning_count"] == 1 and rows[2]["finding_count"] == 0
    assert rows[4]["disposition"] == "EXCLUDED" and rows[4]["sequence"] == 2
    assert rows[4]["actor_type"] == "HUMAN" and rows[4]["finding_count"] == 1
    assert rows[5]["disposition"] == "REJECTED" and rows[6]["disposition"] == "MISSING_KEY"

    # the other evidence blocks
    stages = [e["stage"] for e in ds["timeline"]["events"]]
    assert stages[0] == "REGISTERED" and "RECONCILIATION" in stages and stages[-1] == "READY_FOR_REVIEW"
    assert ds["timeline"]["summary"]["failed_stage"] is None
    assert ds["findings"]["total"] == 2 and ds["findings"]["open_high"] == 1
    assert ds["findings"]["rule_set_version"] == "1.2.0"
    assert [c["rule_id"] for c in ds["findings"]["by_code"]] == ["FMT-001", "NPI-003"] or \
        {c["rule_id"] for c in ds["findings"]["by_code"]} == {"FMT-001", "NPI-003"}
    conflict = ds["identifiers"]["conflicts"][0]
    assert conflict["identifier_type"] == "npi" and conflict["current_decision"] == "CONFIRM_EXISTING"
    assert conflict["selected_value"] == "1497758544" and ds["identifiers"]["unresolved"] == 0
    assert {s["name"] for s in ds["verification"]["sources"]} >= {"nppes", "pecos", "leie", "sam"}
    assert ds["analyst"]["counts"]["disposition_events"] == 1
    assert ds["analyst"]["counts"]["issue_resolutions"] == 1
    assert ds["analyst"]["counts"]["review_records"] == 1 and ds["analyst"]["counts"]["open"] == 1
    assert ds["lineage"]["corrections_total"] == 1 and ds["lineage"]["entities_promoted"] == 2
    assert ds["lineage"]["entity_versions_in_window"] == 1
    assert ds["outcome"]["code"] == "COMPLETED_WITH_EXCEPTIONS"
    assert any(b["criterion"] == "no_unresolved_findings" and not b["held"] for b in ds["outcome"]["basis"])
    assert ds["review"]["code"] in ("READY_FOR_ANALYST_REVIEW", "UNDER_REVIEW")
    assert isinstance(ds["limitations"], list)
    from support_delivery_api import alembic_head
    assert ds["build"]["migration_revision"] == alembic_head()
    assert ds["template_version"] == TEMPLATE_VERSION
    assert ds["chart_list"] == [] and ds["service_version"]

    # page one prints the identity block
    html = result["html"]
    from support_delivery_api import alembic_head as _head
    for needle in (str(ids["job_id"]), str(ids["intake_id"]), ids["sha256"], latest,
                   ds["snapshot_hash"], ds["build"]["git_sha"], _head(),
                   TEMPLATE_VERSION, "synthetic_dpr.psv", SYN, "Delivery Identity",
                   "Evidence limitations", "Audit note"):
        assert needle in html, needle
    assert html.count("<h1>") == 1
    a11y = validate_html(html, TOKENS).to_dict()
    assert a11y["automated_checks_passed"], a11y["errors"]
    assert result["accessibility"]["automated_checks_passed"]


@pytest.mark.asyncio
async def test_totals_agree_across_csv_html_and_dataset(rolled_back_db):
    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, job_id=str(ids["job_id"]))
    ds, html, csv_text = result["dataset"], result["html"], result["csv"]
    counts = ds["dispositions"]["counts"]
    order = ("CREATED", "UPDATED", "MATCHED_UNCHANGED", "HELD", "REJECTED", "MISSING_KEY", "EXCLUDED")

    sections = _csv_sections(csv_text)
    records = sections["Record-level dispositions (all rows)"]
    header, body = records[0], records[1:]
    assert header[0] == "Line" and "Disposition" in header
    assert len(body) == counts["total"] == len(ds["dispositions"]["rows"])
    col = header.index("Disposition")
    from_csv_rows = {k: sum(1 for r in body if r[col] == k) for k in order}
    from_csv_totals = {r[0]: int(r[1]) for r in sections["Disposition totals (current table)"]
                       if r[0] in order}
    from_dataset = {k: counts[k] for k in order}
    assert from_csv_rows == from_csv_totals == from_dataset

    # the HTML totals table states the same numbers
    for key in order:
        m = re.search(rf'<th scope="row">{key}</th><td>([\d,]+)</td>', html)
        assert m, key
        assert int(m.group(1).replace(",", "")) == counts[key], key
    assert f"{counts['total']} carry a disposition" in _text(html)

    # the CSV preamble pins the same snapshot and build the page prints
    assert f"# Reconciliation snapshot id: {ds['snapshot_id']}" in csv_text
    assert f"# Reconciliation snapshot hash: {ds['snapshot_hash']}" in csv_text
    assert f"# Job id: {ids['job_id']}" in csv_text and f"# Intake id: {ids['intake_id']}" in csv_text
    assert f"# Build SHA: {ds['build']['git_sha']}" in csv_text
    from support_delivery_api import alembic_head as _head2
    assert f"# Migration revision: {_head2()}" in csv_text
    eq_rows = {r[0]: r[1] for r in sections["Reconciliation equation (pinned snapshot)"]}
    assert eq_rows["Received"] == "5" and eq_rows["Accounted"] == "5" and eq_rows["Equation holds"] == "True"
    # every record row names the same line numbers as the dataset
    assert sorted(int(r[0]) for r in body) == sorted(r["line_number"] for r in ds["dispositions"]["rows"])


@pytest.mark.asyncio
async def test_regeneration_from_an_earlier_snapshot_states_which_one_it_used(rolled_back_db):
    db = rolled_back_db
    ids = await seed_delivery(db)
    first, latest = (str(s) for s in ids["snapshot_ids"])

    result = await _generate(db, job_id=str(ids["job_id"]), snapshot_id=first)
    ds = result["dataset"]
    assert ds["snapshot_id"] == first and result["snapshot_id"] == first
    assert ds["snapshot_is_latest"] is False
    assert ds["reconciliation"]["sequence"] == 1
    assert ds["reconciliation"]["equation"]["held"] == 1 and ds["reconciliation"]["equation"]["excluded"] == 0
    # the current table moved on (analyst decision) - reported, not smoothed over
    assert ds["dispositions"]["agrees_with_snapshot"] is False
    assert any("NOT the latest" in item for item in ds["limitations"])
    assert any("no longer sums to the pinned" in item for item in ds["limitations"])
    assert first in result["html"] and "NOT the latest" in result["html"]
    assert f"# Reconciliation snapshot id: {first}" in result["csv"]

    # the same request without snapshot_id uses the latest, and says so
    again = await _generate(db, job_id=str(ids["job_id"]))
    assert again["snapshot_id"] == latest and again["dataset"]["snapshot_is_latest"] is True


@pytest.mark.asyncio
async def test_identifier_resolution_and_refusals(rolled_back_db):
    from app.reports.generator import ReportParameterError

    db = rolled_back_db
    ids = await seed_delivery(db)
    other = await seed_delivery(db, label=SYN + "-B")

    # intake id resolves to the single job that produced it
    by_intake = await _generate(db, intake_id=str(ids["intake_id"]))
    assert by_intake["dataset"]["delivery"]["job_id"] == str(ids["job_id"])
    assert by_intake["dataset"]["delivery"]["resolved_from"] == "intake_id"

    # a snapshot of ANOTHER delivery is refused
    with pytest.raises(ReportParameterError) as exc:
        await _generate(db, job_id=str(ids["job_id"]), snapshot_id=str(other["snapshot_ids"][0]))
    assert exc.value.code == "SNAPSHOT_NOT_FOR_JOB" and exc.value.status == 422

    # unknown ids
    with pytest.raises(ReportParameterError) as exc:
        await _generate(db, job_id=str(uuid.uuid4()))
    assert exc.value.code == "DELIVERY_NOT_FOUND" and exc.value.status == 404
    with pytest.raises(ReportParameterError) as exc:
        await _generate(db, job_id="not-a-uuid")
    assert exc.value.code == "DELIVERY_IDENTIFIER_INVALID"
    with pytest.raises(ReportParameterError) as exc:
        await _generate(db, job_id=str(ids["job_id"]), intake_id=str(other["intake_id"]))
    assert exc.value.code == "DELIVERY_IDENTIFIER_MISMATCH"
    with pytest.raises(ReportParameterError) as exc:
        await _generate(db, job_id=str(ids["job_id"]), snapshot_id=str(uuid.uuid4()))
    assert exc.value.code == "SNAPSHOT_NOT_FOUND"


@pytest.mark.asyncio
async def test_data_quality_and_intake_reports_resolve_a_job_id_to_its_intake(rolled_back_db):
    from app.reports.generator import generate_report

    db = rolled_back_db
    ids = await seed_delivery(db)
    for report_type in ("data_quality", "intake"):
        result = await generate_report(db, report_type=report_type, persist=False,
                                       query_parameters={"job_id": str(ids["job_id"])})
        ds = result["dataset"]
        assert ds["intake"]["intake_id"] == str(ids["intake_id"]), report_type
        assert ds["delivery"] == {"resolved_from": "job_id", "job_id": str(ids["job_id"]),
                                  "intake_id": str(ids["intake_id"])}
        assert ds["snapshot_id"] == str(ids["snapshot_ids"][-1])
        assert result["snapshot_id"] == ds["snapshot_id"]


@pytest.mark.asyncio
async def test_a_delivery_without_snapshot_or_dispositions_states_its_limitations(rolled_back_db):
    db = rolled_back_db
    ids = await seed_delivery(db, with_snapshot=False)
    result = await _generate(db, job_id=str(ids["job_id"]))
    ds = result["dataset"]
    assert ds["snapshot_id"] is None and ds["reconciliation"]["available"] is False
    assert ds["outcome"]["code"] == "PARTIALLY_PROCESSED"
    assert any("No reconciliation snapshot" in item for item in ds["limitations"])
    assert "NONE" in result["html"] and "no reconciliation snapshot is persisted" in result["html"]
    assert "No reconciliation snapshot is persisted" in result["csv"]
    assert result["accessibility"]["automated_checks_passed"], result["accessibility"]["errors"]


@pytest.mark.asyncio
async def test_stored_csv_is_the_generated_csv(rolled_back_db, artifact_root):
    """The download regenerates the CSV from the STORED dataset; it must be the
    file the generator produced, byte for byte, so the numbers cannot move."""
    from sqlalchemy import select

    from app.reports.generator import generate_report
    from app.reports.routes import csv_for_stored_report
    from app.tefca_registry import models as reg

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await generate_report(db, report_type="delivery_processing", persist=True,
                                   query_parameters={"job_id": str(ids["job_id"])},
                                   generated_by="qa@synthetic.invalid")
    assert result["stored_id"]
    row = (await db.execute(select(reg.ReviewReport)
                            .where(reg.ReviewReport.report_id == result["report_id"]))).scalar_one()
    assert csv_for_stored_report(row) == result["csv"]
