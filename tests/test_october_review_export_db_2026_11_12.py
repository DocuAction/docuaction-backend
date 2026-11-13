"""The record-review export path: authorized route -> queued job -> runner ->
registered artifact, all rendering ONE dataset from the database loader.

Database-backed (rolled-back transaction). `finalize_artifact` is replaced by a
recorder so no artifact store is touched; everything before it is real: the
loader, the dataset, the renderers and the job lifecycle.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.reports import routes as report_routes
from app.reports.data import export_jobs
from app.reports.data import october_review_dataset as ds_mod
from app.reports.data.export_job_model import ReportExportJob
from app.reports.engine.pdf_engine import pdf_available
from app.reports.export_runner import run_record_review_export_job
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

CUTOFF_ISO = "2026-11-09T00:00:00"
T_RUN_START = datetime(2026, 11, 7, 8, 0, 0)
T_RUN_DONE = datetime(2026, 11, 8, 9, 0, 0)
T_OLD = datetime(2026, 11, 8, 10, 0, 0)
QA = SimpleNamespace(email="qalead@synthetic-test.docuaction.invalid")


async def _run(db, intake_id, date_str="2026-11-08"):
    run = m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="test",
        rule_config_hash="0" * 64, started_at=T_RUN_START, completed_at=T_RUN_DONE,
        run_status="COMPLETE",
        assessment_metadata={"assessment_date": date_str, "source": "pinned_param"})
    db.add(run)
    await db.flush()
    return run


async def _seed(db, n=4, arc="9.99.777.60"):
    """n source records: #1 classified B1, #2 HELD, #3 unclassified, #4 no curated row."""
    intake_id, _ = await seed_intake(db, make_rows(n, arc=arc), with_job=False)
    run = await _run(db, intake_id)
    recs = {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
    ent = await seed_entity(db, oid=f"{SYN}-EXP-{uuid.uuid4().hex[:8]}", name=f"{SYN} exp")
    for i, status, eid, why in ((1, "CLEAN", ent, None), (2, "HELD", None, "blocker"),
                                (3, "CLEAN", None, None)):
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=recs[i].id,
            record_status=status, status_reason=why, rce_org_oid=recs[i].source_rce_id,
            transformation_version="test", canonical_entity_id=eid))
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id=f"REV-{uuid.uuid4().int % 10**4:04d}-000001",
        entity_id=ent, source_record_id=recs[1].id, classification_bucket="B1",
        classification_rule="RULE-001", classification_rule_version=2,
        created_at=T_OLD))
    await db.flush()
    return intake_id, run, recs


def _req(intake_id, fmt="csv", **kw):
    return report_routes.RecordReviewExportRequest(
        intake_id=str(intake_id), evidence_cutoff=kw.pop("cutoff", CUTOFF_ISO),
        format=fmt, **kw)


class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, **kw):
        self.calls.append(kw)
        return {"artifact_id": uuid.uuid4(), "artifact_version": 1,
                "rendered_sha256": "f" * 64, "size_bytes": len(kw["content"])}


async def _queue_and_run(db, monkeypatch, intake_id, fmt, rec):
    from app.reports.data import artifact_registry
    monkeypatch.setattr(artifact_registry, "finalize_artifact", rec)
    out = await report_routes.export_record_review(_req(intake_id, fmt), db=db, user=QA)
    job = await db.get(ReportExportJob, uuid.UUID(out["job_id"]))
    state = await run_record_review_export_job(db, job)
    return out, job, state


# ── authorization / request validation (before anything is queued) ──────────

def test_route_requires_a_signed_in_qalead_not_an_anonymous_caller(client):
    resp = client.post("/api/reports/exports/record-review",
                       json={"intake_id": str(uuid.uuid4()),
                             "evidence_cutoff": CUTOFF_ISO, "format": "csv"})
    assert resp.status_code in (401, 403), resp.status_code


def test_route_is_gated_at_qalead_in_its_declared_dependencies():
    route = next(r for r in report_routes.router.routes
                 if getattr(r, "path", "").endswith("/exports/record-review"))
    deps = [d.call for d in route.dependant.dependencies]
    wb = next(r for r in report_routes.router.routes
              if getattr(r, "path", "").endswith("/exports/onc-review-workbook"))
    wb_deps = [d.call for d in wb.dependant.dependencies]
    # same role gate as the certified workbook export: the closures are
    # different objects, so compare their qualified names and the role bound in them
    gate = lambda calls: [c for c in calls if "require_role" in getattr(c, "__qualname__", "")]
    assert gate(deps) and gate(wb_deps)
    roles = lambda calls: sorted(
        cell.cell_contents for c in gate(calls)
        for cell in (c.__closure__ or ()) if isinstance(cell.cell_contents, str))
    assert roles(deps) == roles(wb_deps)
    assert "qalead" in roles(deps)           # not viewer / contributor


async def test_route_refuses_a_request_that_could_never_succeed_before_queuing(rolled_back_db):
    db = rolled_back_db
    intake_id, run, _ = await _seed(db, arc="9.99.777.61")
    other_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.62"), with_job=False)
    other_run = await _run(db, other_id)
    bare_id, _ = await seed_intake(db, make_rows(1, arc="9.99.777.63"), with_job=False)
    bad = [
        (_req(uuid.uuid4()), "No delivery"),
        (_req("not-a-uuid"), "not a UUID"),
        (_req(intake_id, cutoff="yesterday"), "not an ISO datetime"),
        (_req(intake_id, run_id=str(other_run.id)), "unrelated run"),
        (_req(bare_id), "no completed assessment run"),
    ]
    for request, needle in bad:
        with pytest.raises(HTTPException) as exc:
            await report_routes.export_record_review(request, db=db, user=QA)
        assert exc.value.status_code == 409 and needle in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await report_routes.export_record_review(_req(intake_id, fmt="docx"), db=db, user=QA)
    assert exc.value.status_code == 422
    queued = (await db.execute(select(ReportExportJob).where(
        ReportExportJob.export_type.like("record_review:%")))).scalars().all()
    assert queued == []                                   # nothing was queued


async def test_route_queues_one_job_per_delivery_run_cutoff_format_and_reuses_it(rolled_back_db):
    db = rolled_back_db
    intake_id, run, _ = await _seed(db, arc="9.99.777.64")
    first = await report_routes.export_record_review(_req(intake_id, "csv"), db=db, user=QA)
    again = await report_routes.export_record_review(_req(intake_id, "csv"), db=db, user=QA)
    other_fmt = await report_routes.export_record_review(_req(intake_id, "xlsx"), db=db, user=QA)
    assert first["export_type"] == "record_review:csv" and first["state"] == "QUEUED"
    assert first["reused_existing_job"] is False and again["reused_existing_job"] is True
    assert again["job_id"] == first["job_id"] and other_fmt["job_id"] != first["job_id"]
    assert first["assessment_date"] == "2026-11-08" and first["assessment_run_id"] == str(run.id)
    assert "DRAFT / NOT FINAL / INDEPENDENT QA PENDING" == first["status"]
    job = await db.get(ReportExportJob, uuid.UUID(first["job_id"]))
    assert job.request_parameters == {"format": "csv", "evidence_cutoff": CUTOFF_ISO,
                                      "run_id": str(run.id)}
    assert str(job.source_intake_id) == str(intake_id)
    # a different cutoff is a different request
    moved = await report_routes.export_record_review(
        _req(intake_id, "csv", cutoff="2026-11-09T12:00:00"), db=db, user=QA)
    assert moved["job_id"] != first["job_id"]


# ── the runner: loader -> dataset -> format -> registered artifact ───────────

async def test_runner_registers_the_csv_rendering_of_the_loaded_dataset(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, run, recs = await _seed(db, arc="9.99.777.65")
    rec = _Recorder()
    out, job, state = await _queue_and_run(db, monkeypatch, intake_id, "csv", rec)
    assert state == ReportExportJob.STATE_SUCCEEDED
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["content_type"] == "text/csv"
    assert call["report_type"] == "record_review:csv"
    assert call["report_id"].startswith("RECORD-REVIEW-")
    assert call["review_cycle_id"] == str(intake_id)
    assert call["contains_reportable_findings"] is False     # DRAFT: nothing reportable
    assert call["source_artifact_sha256"] and len(call["report_data_hash"]) == 64
    assert call["data_classification"] == job.classification
    lines = list(csv.reader(io.StringIO(call["content"].decode("utf-8-sig"))))
    assert "INCOMPLETE" in lines[0][0] and "not included in this export" in lines[0][0]
    assert lines[1] == ds_mod.RECORD_COLUMNS
    body = lines[2:]
    assert len(body) == 4 == len({r[1] for r in body})        # every source record, once
    assert {r[1] for r in body} == {str(r.id) for r in recs.values()}
    by = {r[1]: r for r in body}
    b = ds_mod.RECORD_COLUMNS.index("Bucket")
    assert by[str(recs[1].id)][b] == "B1"
    assert by[str(recs[2].id)][b] == "B4"                     # HELD, reporting only
    assert by[str(recs[3].id)][b] == ds_mod.NOT_CLASSIFIED
    assert by[str(recs[4].id)][b] == ds_mod.NOT_CLASSIFIED
    done = await db.get(ReportExportJob, job.id)
    assert done.state == ReportExportJob.STATE_SUCCEEDED and done.report_id == call["report_id"]


async def test_excel_and_csv_artifacts_from_the_same_delivery_reconcile_cell_for_cell(rolled_back_db, monkeypatch):
    import openpyxl
    db = rolled_back_db
    intake_id, run, recs = await _seed(db, arc="9.99.777.66")
    csv_rec, xl_rec = _Recorder(), _Recorder()
    await _queue_and_run(db, monkeypatch, intake_id, "csv", csv_rec)
    await _queue_and_run(db, monkeypatch, intake_id, "xlsx", xl_rec)
    csv_rows = list(csv.reader(io.StringIO(csv_rec.calls[0]["content"].decode("utf-8-sig"))))[2:]
    ws = openpyxl.load_workbook(io.BytesIO(xl_rec.calls[0]["content"]))["Records"]
    hdr = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1)
               if r and r[0] == "Source_Line")
    xl_rows = [["" if v is None else str(v) for v in r]
               for r in ws.iter_rows(min_row=hdr + 1, values_only=True)]
    assert xl_rows == csv_rows and len(csv_rows) == 4
    # same data hash => the two artifacts describe the same dataset
    assert csv_rec.calls[0]["report_data_hash"] == xl_rec.calls[0]["report_data_hash"]
    assert xl_rec.calls[0]["content_type"].endswith("spreadsheetml.sheet")


@pytest.mark.skipif(not pdf_available(), reason="WeasyPrint native libraries are not available here")
async def test_pdf_artifact_is_registered_from_the_same_dataset(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, _, _ = await _seed(db, arc="9.99.777.67")
    rec = _Recorder()
    _, _, state = await _queue_and_run(db, monkeypatch, intake_id, "pdf", rec)
    assert state == ReportExportJob.STATE_SUCCEEDED
    assert rec.calls[0]["content_type"] == "application/pdf"
    assert rec.calls[0]["content"].startswith(b"%PDF")


async def test_runner_refusal_is_a_controlled_failure_that_registers_nothing(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, _, _ = await _seed(db, arc="9.99.777.68")
    from app.reports.data import artifact_registry
    rec = _Recorder()
    monkeypatch.setattr(artifact_registry, "finalize_artifact", rec)
    out = await report_routes.export_record_review(_req(intake_id, "csv"), db=db, user=QA)
    job = await db.get(ReportExportJob, uuid.UUID(out["job_id"]))
    job.request_parameters = {"format": "csv", "evidence_cutoff": None, "run_id": None}
    await db.flush()
    state = await run_record_review_export_job(db, job)
    assert state == ReportExportJob.STATE_FAILED and rec.calls == []
    done = await db.get(ReportExportJob, job.id)
    assert "evidence_cutoff is required" in done.error_reason
    assert done.artifact_id is None and done.active_marker is None


async def test_unattributed_comment_text_can_never_reach_an_artifact(rolled_back_db, monkeypatch):
    """When NO comment is stored for the delivery, both comment columns are empty in
    every row: the export never fabricates or borrows comment text."""
    db = rolled_back_db
    intake_id, _, _ = await _seed(db, arc="9.99.777.69")
    rec = _Recorder()
    await _queue_and_run(db, monkeypatch, intake_id, "csv", rec)
    rows = list(csv.reader(io.StringIO(rec.calls[0]["content"].decode("utf-8-sig"))))[2:]
    o = ds_mod.RECORD_COLUMNS.index(ds_mod.ORIGINAL_COMMENTS_COLUMN)
    i = ds_mod.RECORD_COLUMNS.index(ds_mod.IZAAN_COMMENTS_COLUMN)
    assert all(r[o] == "" and r[i] == "" for r in rows)


# ── scheduler dispatch ──────────────────────────────────────────────────────

async def test_scheduler_dispatches_record_review_jobs_to_their_own_runner(monkeypatch):
    from app.reports import export_runner, export_scheduler
    from app.reports.data import export_jobs as ej

    seen = []

    async def _fake_claim(db):
        return SimpleNamespace(id=uuid.uuid4(), export_type=_fake_claim.kind)

    def _runner(name):
        async def run(db, job):
            seen.append(name)
            return "SUCCEEDED"
        return run

    monkeypatch.setattr(ej, "claim_next_queued", _fake_claim)
    monkeypatch.setattr(export_runner, "run_export_job", _runner("workbook"))
    monkeypatch.setattr(export_runner, "run_report_generation_job", _runner("report"))
    monkeypatch.setattr(export_runner, "run_record_review_export_job", _runner("record_review"))
    for kind, want in (("record_review:csv", "record_review"), ("record_review:pdf", "record_review"),
                       ("onc_review_workbook", "workbook"), ("report:delivery", "report")):
        _fake_claim.kind = kind
        seen.clear()
        await export_scheduler._poll_tick()
        assert seen == [want], (kind, seen)


async def test_stored_comments_reach_the_registered_artifact_in_separate_verbatim_columns(
        rolled_back_db, monkeypatch):
    from app.tefca_registry.rce import record_comments as rcm
    db = rolled_back_db
    intake_id, run, recs = await _seed(db, arc="9.99.777.70")
    src = {"file_name": "c.xlsx", "file_sha256": "ab" * 32, "sheet": "S", "column": "QA Comments", "row": 2}

    def e(rec, kind, body, **o):
        d = {"source_record_id": str(rec.id), "comment_kind": kind, "comment_text": body, "source": src,
             "expected": {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid}}
        d.update(o)
        return d
    orig_text, izaan_text = "  original,  with \"quotes\"\nand a newline ", "izaan wording"
    await rcm.import_comments(db, intake_id=intake_id, actor="q@x.invalid", entries=[
        e(recs[1], "QA_ORIGINAL", orig_text),
        e(recs[1], "QA_IZAAN", izaan_text, author_attribution="Izaan", attribution_basis="owner designation"),
        e(recs[2], "QA_ORIGINAL", "held record comment")])        # recs[2] is HELD in this fixture
    rec = _Recorder()
    _, _, state = await _queue_and_run(db, monkeypatch, intake_id, "csv", rec)
    assert state == ReportExportJob.STATE_SUCCEEDED
    rows = list(csv.reader(io.StringIO(rec.calls[0]["content"].decode("utf-8-sig"))))[2:]
    o = ds_mod.RECORD_COLUMNS.index(ds_mod.ORIGINAL_COMMENTS_COLUMN)
    i = ds_mod.RECORD_COLUMNS.index(ds_mod.IZAAN_COMMENTS_COLUMN)
    b = ds_mod.RECORD_COLUMNS.index("Bucket")
    by = {r[1]: r for r in rows}
    assert by[str(recs[1].id)][o] == orig_text and by[str(recs[1].id)][i] == izaan_text
    assert by[str(recs[2].id)][o] == "held record comment" and by[str(recs[2].id)][b] == "B4"
    assert by[str(recs[3].id)][o] == "" == by[str(recs[3].id)][i]


# ── 2026-11-13: the status route's download link names the REGISTERED content type ──

@pytest.mark.parametrize("fmt,content_type", [("csv", "text/csv"), ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")])
async def test_status_download_link_carries_the_formats_own_content_type(rolled_back_db, monkeypatch, fmt, content_type):
    """Before the fix every succeeded export job linked with the workbook's xlsx
    content type, so a record-review CSV/PDF job's link resolved to
    ARTIFACT_NOT_REGISTERED (the download route looks up by report_id + content_type)."""
    db = rolled_back_db
    intake_id, _, _ = await _seed(db, arc=f"9.99.777.{70 if fmt == 'csv' else 71}")
    rec = _Recorder()
    _, job, state = await _queue_and_run(db, monkeypatch, intake_id, fmt, rec)
    assert state == ReportExportJob.STATE_SUCCEEDED
    assert rec.calls[0]["content_type"] == content_type          # what was registered
    payload = await report_routes.export_job_status(str(job.id), db=db, user=QA)
    assert payload["download"] == (f"/api/reports/artifacts/{job.report_id}/download"
                                   f"?content_type={content_type}")


def test_runner_and_status_route_share_one_content_type_table():
    from app.reports.export_runner import RECORD_REVIEW_CONTENT_TYPES
    from app.reports.engine.xlsx_engine import XLSX_CONTENT_TYPE
    assert set(RECORD_REVIEW_CONTENT_TYPES) == {"xlsx", "csv", "pdf"}
    assert RECORD_REVIEW_CONTENT_TYPES["xlsx"] == XLSX_CONTENT_TYPE == report_routes.XLSX_CONTENT_TYPE_LITERAL
