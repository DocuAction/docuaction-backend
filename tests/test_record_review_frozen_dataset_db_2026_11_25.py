"""Record-review export: ONE frozen dataset per delivery + assessment run + evidence cutoff (2026-11-25).

Before this change every format was its own job that re-read the database, and curated / hold status has no
history, so a hold released between the CSV and the Excel request gave two files with different totals for the
same delivery and cutoff. `row_count_matches` was computed and not enforced.

Real here: the request route (called directly, as in test_october_review_export_db), the job table, the runner,
the loader, the dataset, the CSV / XLSX renderers, `finalize_artifact`, the registry table and a
LocalFilesystemArtifactStore in pytest's tmp_path. The PDF renderer is replaced by its own HTML source in the one
test that needs a third format (WeasyPrint does not render on this platform; the real PDF path runs in CI).

Database-backed (rolled-back transaction); every row is synthetic.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import uuid
from datetime import datetime
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.reports import routes as report_routes
from app.reports.data import artifact_registry
from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import october_review_loader as ld
from app.reports.data import record_review_freeze as freeze
from app.reports.data.artifact_registry import ReportArtifact
from app.reports.data.export_job_model import ReportExportJob
from app.reports.export_runner import RECORD_REVIEW_CONTENT_TYPES, run_record_review_export_job
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

CUTOFF_ISO = "2026-11-09T00:00:00"
T_OLD = datetime(2026, 11, 8, 10, 0, 0)
T_AFTER_CUTOFF = datetime(2026, 11, 10, 9, 0, 0)
QA = SimpleNamespace(email="qalead@synthetic-test.docuaction.invalid", role="qalead")
HDR = ds_mod.RECORD_COLUMNS


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """A local artifact store in tmp_path, installed as the process-wide store."""
    from app.core.storage import artifact_store

    monkeypatch.setattr(artifact_store, "_store",
                        artifact_store.LocalFilesystemArtifactStore(str(tmp_path / "artifacts")))
    return tmp_path / "artifacts"


async def _seed(db, arc, n=4):
    """#1 CLEAN B1, #2 HELD, #3 CLEAN B4, #4 CLEAN B3; a completed assessment run."""
    intake_id, _ = await seed_intake(db, make_rows(n, arc=arc), with_job=False)
    db.add(m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="test", rule_config_hash="0" * 64,
        started_at=datetime(2026, 11, 7, 8), completed_at=datetime(2026, 11, 8, 9), run_status="COMPLETE",
        assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"}))
    recs = {r.line_number - 1: r for r in (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
    for i, (status, bucket) in {1: ("CLEAN", "B1"), 2: ("HELD", None), 3: ("CLEAN", "B4"), 4: ("CLEAN", "B3")}.items():
        ent = await seed_entity(db, oid=f"{SYN}-FRZ-{uuid.uuid4().hex[:10]}", name=f"{SYN} frozen {i}") if bucket else None
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=recs[i].id, record_status=status,
            status_reason=None if bucket else "blocking identity issue", rce_org_oid=recs[i].source_rce_id,
            transformation_version="test", canonical_entity_id=ent))
        if bucket:
            db.add(reg.ReviewRecord(
                id=uuid.uuid4(), review_id=f"REV-{uuid.uuid4().int % 10**4:04d}-{uuid.uuid4().int % 10**6:06d}",
                entity_id=ent, source_record_id=recs[i].id, classification_bucket=bucket,
                classification_rule="RULE-001", classification_rule_version=2, created_at=T_OLD))
    await db.flush()
    return intake_id, recs


async def _release_hold(db, rec):
    """What happens in the application between two export requests: the hold on one record is released."""
    held = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_record_id == (rec if isinstance(rec, uuid.UUID) else rec.id)))).scalar_one()
    ent = await seed_entity(db, oid=f"{SYN}-FRZ-{uuid.uuid4().hex[:10]}", name=f"{SYN} released")
    held.record_status, held.status_reason, held.canonical_entity_id = "CLEAN", None, ent
    await db.flush()


def _req(intake_id, fmt, **kw):
    return report_routes.RecordReviewExportRequest(
        intake_id=str(intake_id), evidence_cutoff=kw.pop("cutoff", CUTOFF_ISO), format=fmt, **kw)


async def _request(db, intake_id, fmt, **kw):
    return await report_routes.export_record_review(_req(intake_id, fmt, **kw), db=db, user=QA)


async def _run(db, out):
    job_id = uuid.UUID(out["job_id"])                # the runner may roll back, which expires `job`
    job = await db.get(ReportExportJob, job_id)
    state = await run_record_review_export_job(db, job)
    return await db.get(ReportExportJob, job_id), state


async def _export(db, intake_id, fmt, **kw):
    out = await _request(db, intake_id, fmt, **kw)
    job, state = await _run(db, out)
    assert state == ReportExportJob.STATE_SUCCEEDED, job.error_reason
    got = await artifact_registry.retrieve_artifact(
        db, job.report_id, content_type=RECORD_REVIEW_CONTENT_TYPES[fmt])
    return out, job, got["content"], got["artifact"]


def _csv(content):
    lines = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    return lines[0][0], lines[2:]


def _xlsx(content):
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb["Records"]
    hdr = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1) if r and r[0] == "Source_Line")
    rows = [["" if v is None else str(v) for v in r] for r in ws.iter_rows(min_row=hdr + 1, values_only=True)]
    summary = {str(r[0]): "" if r[1] is None else str(r[1])
               for r in wb["Summary"].iter_rows(values_only=True) if r and r[0] is not None and len(r) > 1}
    return str(ws.cell(row=1, column=1).value), rows, summary


def _totals(rows):
    out = {}
    for r in rows:
        out[r[HDR.index("Bucket")]] = out.get(r[HDR.index("Bucket")], 0) + 1
    return out, sum(1 for r in rows if r[HDR.index("Status")] == "HELD")


async def _datasets(db, intake_id):
    return (await db.execute(select(ReportArtifact).where(
        ReportArtifact.review_cycle_id == str(intake_id),
        ReportArtifact.content_type == freeze.DATASET_CONTENT_TYPE))).scalars().all()


# ── the defect: a hold released between two formats ────────────────────────────────────────────────────────────

async def test_a_hold_released_between_csv_and_xlsx_gives_identical_totals_and_the_same_dataset_hash(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.10")
    out_csv, job_csv, csv_bytes, csv_art = await _export(db, intake_id, "csv")
    await _release_hold(db, recs[2])                                 # between the two requests
    out_xl, job_xl, xl_bytes, xl_art = await _export(db, intake_id, "xlsx")

    banner, csv_rows = _csv(csv_bytes)
    note, xl_rows, summary = _xlsx(xl_bytes)
    assert xl_rows == csv_rows and len(csv_rows) == 4                # cell for cell
    assert _totals(csv_rows) == _totals(xl_rows) == ({"B1": 1, "B4": 2, "B3": 1}, 1)   # still HELD in both
    # one dataset, one hash - on the registry rows, on the jobs, and PRINTED in both files
    datasets = await _datasets(db, intake_id)
    assert len(datasets) == 1
    sha = datasets[0].rendered_sha256
    assert csv_art["report_data_hash"] == xl_art["report_data_hash"] == sha
    assert f"SHA-256 {sha}" in banner and f"SHA-256 {sha}" in note
    assert summary[ds_mod.FROZEN_SHA_ROW] == sha
    assert summary[ds_mod.FROZEN_ID_ROW] == datasets[0].report_id == freeze.dataset_report_id(job_csv.id)
    assert job_csv.request_parameters["dataset_sha256"] == job_xl.request_parameters["dataset_sha256"] == sha
    # the second request was told it reuses the frozen dataset
    assert out_csv["dataset_reused"] is False and out_csv["dataset_state"] == "pending"
    assert out_xl["dataset_reused"] is True and out_xl["dataset_state"] == "frozen"
    assert out_xl["dataset_job_id"] == out_csv["dataset_job_id"] == str(job_csv.id) and out_xl["dataset_sha256"] == sha
    # each format keeps its own report id: RECORD-REVIEW-<its job UUID>
    assert job_csv.report_id == f"RECORD-REVIEW-{str(job_csv.id).upper()}"
    assert job_xl.report_id == f"RECORD-REVIEW-{str(job_xl.id).upper()}" != job_csv.report_id


async def test_the_stated_hash_is_the_sha256_of_the_downloadable_dataset_and_status_is_labelled_freeze_time(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.11")
    # third format: the PDF's own HTML source stands in for the bytes WeasyPrint would render from it
    monkeypatch.setattr(ds_mod, "to_pdf", lambda d: ds_mod.to_html(d).encode("utf-8"))
    _, job, csv_bytes, _ = await _export(db, intake_id, "csv")
    _, pdf_job, pdf_source, pdf_art = await _export(db, intake_id, "pdf")
    stored = await artifact_registry.retrieve_artifact(
        db, freeze.dataset_report_id(job.id), content_type=freeze.DATASET_CONTENT_TYPE)
    sha = hashlib.sha256(stored["content"]).hexdigest()
    frozen = json.loads(stored["content"])
    banner, _rows = _csv(csv_bytes)
    html = pdf_source.decode("utf-8")
    assert f"SHA-256 {sha}" in banner and f"SHA-256 {sha}" in html and pdf_art["report_data_hash"] == sha
    assert frozen["schema"] == freeze.FROZEN_SCHEMA and frozen["curated_status_basis"] == "freeze_time"
    assert frozen["evidence_cutoff"] == CUTOFF_ISO and len(frozen["facts"]) == 4
    # the evidence limits travel inside the frozen dataset and are printed by the formats rendered from it
    notes = frozen["load_meta"]["evidence_notes"]
    assert [n["marker"] for n in notes] == ["N1", "N2", "N3", "N4", "N5"]
    for text in (banner, html):
        assert "Identifier match only" in text and "carries no delivery or record id" in text
        assert "carries NO carried label" in text and "shown without any attribution check" in text
    # hold / curated status is labelled with the time it was captured - not the cutoff, not "generation"
    for text in (banner, html):
        assert "as captured when the dataset was frozen" in text and frozen["frozen_at"] in text
        assert "NOT as of the evidence cutoff" in text and "as of report generation" not in text
    assert frozen["frozen_at"] != CUTOFF_ISO and frozen["frozen_at"].endswith("Z")
    # the status route names the dataset, its hash, its freeze time and where to download it
    payload = await report_routes.export_job_status(str(pdf_job.id), db=db, user=QA)
    assert payload["dataset_job_id"] == str(job.id) and payload["dataset_sha256"] == sha
    assert payload["dataset_frozen_at"] == frozen["frozen_at"] and payload["curated_status_basis"] == "freeze_time"
    assert payload["dataset_download"].startswith(f"/api/reports/artifacts/{freeze.dataset_report_id(job.id)}/download")
    assert payload["download"].endswith("content_type=application/pdf")


# ── concurrency ────────────────────────────────────────────────────────────────────────────────────────────────

async def test_two_formats_requested_before_either_runs_name_one_freeze_and_register_one_dataset(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.12")
    out_csv = await _request(db, intake_id, "csv")
    out_xl = await _request(db, intake_id, "xlsx")
    again = await _request(db, intake_id, "csv")                     # same user, same format: the same job
    assert out_xl["dataset_job_id"] == out_csv["dataset_job_id"] == out_csv["job_id"]
    assert out_xl["dataset_state"] == "pending" and out_xl["dataset_reused"] is True
    assert again["job_id"] == out_csv["job_id"] and again["reused_existing_job"] is True
    job_xl, state = await _run(db, out_xl)                           # the SECOND request runs first and freezes it
    assert state == ReportExportJob.STATE_SUCCEEDED
    await _release_hold(db, recs[2])
    job_csv, state = await _run(db, out_csv)
    assert state == ReportExportJob.STATE_SUCCEEDED
    datasets = await _datasets(db, intake_id)
    assert len(datasets) == 1 and datasets[0].report_id == freeze.dataset_report_id(out_csv["job_id"])
    assert job_csv.request_parameters["dataset_sha256"] == job_xl.request_parameters["dataset_sha256"]
    got = await artifact_registry.retrieve_artifact(db, job_csv.report_id, content_type="text/csv")
    assert _totals(_csv(got["content"])[1])[1] == 1                  # the released hold did not reach the CSV


async def test_a_job_that_loses_the_race_discards_its_own_load_and_renders_the_registered_dataset(
        rolled_back_db, monkeypatch):
    """Two workers, one freeze: both find nothing registered and both load. The first to register wins; the
    other re-checks under the lock, drops what it loaded and renders the registered bytes."""
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.13")
    out_a = await _request(db, intake_id, "csv")
    out_b = await _request(db, intake_id, "xlsx")
    real_load = ld.load_record_facts
    state_b = {}

    async def racing_load(db_, **kw):
        loaded = await real_load(db_, **kw)                           # A's own load: record 2 still HELD
        if not state_b:
            state_b["running"] = True
            await _release_hold(db_, recs[2])
            state_b["job"], state_b["state"] = await _run(db_, out_b)  # B loads (hold released) and registers first
        return loaded

    monkeypatch.setattr(ld, "load_record_facts", racing_load)
    job_a, state_a = await _run(db, out_a)
    assert state_a == state_b["state"] == ReportExportJob.STATE_SUCCEEDED
    assert len(await _datasets(db, intake_id)) == 1                   # never two datasets for one freeze
    sha = state_b["job"].request_parameters["dataset_sha256"]
    assert job_a.request_parameters["dataset_sha256"] == sha
    got = await artifact_registry.retrieve_artifact(db, job_a.report_id, content_type="text/csv")
    banner, rows = _csv(got["content"])
    assert f"SHA-256 {sha}" in banner and _totals(rows)[1] == 0       # B's dataset, not A's own load


# ── a deliberate new freeze; naming an existing one ────────────────────────────────────────────────────────────

async def test_new_freeze_takes_a_new_dataset_and_leaves_the_earlier_one_and_its_artifacts_untouched(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.14")
    _, first_job, first_csv, first_art = await _export(db, intake_id, "csv")
    first_sha = first_job.request_parameters["dataset_sha256"]
    await _release_hold(db, recs[2])

    out_new, new_job, new_csv, _ = await _export(db, intake_id, "csv", new_freeze=True)
    assert out_new["dataset_reused"] is False and out_new["dataset_job_id"] == str(new_job.id) != str(first_job.id)
    new_sha = new_job.request_parameters["dataset_sha256"]
    assert new_sha != first_sha and _totals(_csv(new_csv)[1])[1] == 0     # status captured again, now
    # later default requests use the NEW freeze; nobody is stuck on the earlier one
    out_default, default_job, default_xl, _ = await _export(db, intake_id, "xlsx")
    assert out_default["dataset_job_id"] == str(new_job.id) and out_default["dataset_sha256"] == new_sha
    assert _totals(_xlsx(default_xl)[1])[1] == 0
    # the earlier freeze can still be named, and still renders what it froze
    out_old, old_job, old_xl, _ = await _export(db, intake_id, "xlsx", dataset_job_id=str(first_job.id))
    assert out_old["dataset_reused"] is True and old_job.request_parameters["dataset_sha256"] == first_sha
    assert _totals(_xlsx(old_xl)[1])[1] == 1
    # nothing earlier was regenerated or altered
    again = await artifact_registry.retrieve_artifact(db, first_job.report_id, content_type="text/csv")
    assert again["content"] == first_csv and again["artifact"]["id"] == first_art["id"]
    assert again["artifact"]["artifact_version"] == 1
    assert sorted(d.rendered_sha256 for d in await _datasets(db, intake_id)) == sorted([first_sha, new_sha])
    # a different cutoff is a different freeze without any flag
    out_moved = await _request(db, intake_id, "csv", cutoff="2026-11-09T12:00:00")
    assert out_moved["dataset_reused"] is False and out_moved["dataset_job_id"] == out_moved["job_id"]


async def test_a_named_dataset_is_refused_unless_it_is_this_deliverys_and_this_cutoffs(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.15")
    other_id, _ = await _seed(db, "9.99.779.16")
    _, job, _, _ = await _export(db, intake_id, "csv")
    jobs_before = len((await db.execute(select(ReportExportJob.id).where(
        ReportExportJob.source_intake_id.in_([intake_id, other_id])))).all())
    for request, needle in (
            (_req(intake_id, "xlsx", dataset_job_id=str(uuid.uuid4())), "no usable frozen dataset"),
            (_req(other_id, "xlsx", dataset_job_id=str(job.id)), "no usable frozen dataset"),
            (_req(intake_id, "xlsx", dataset_job_id="not-a-uuid"), "not a UUID"),
            (_req(intake_id, "xlsx", dataset_job_id=str(job.id), cutoff="2026-11-09T12:00:00"),
             "a different cutoff is a different dataset")):
        with pytest.raises(HTTPException) as exc:
            await report_routes.export_record_review(request, db=db, user=QA)
        assert exc.value.status_code == 409 and needle in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await report_routes.export_record_review(
            _req(intake_id, "xlsx", dataset_job_id=str(job.id), new_freeze=True), db=db, user=QA)
    assert exc.value.status_code == 422
    assert len((await db.execute(select(ReportExportJob.id).where(
        ReportExportJob.source_intake_id.in_([intake_id, other_id])))).all()) == jobs_before   # nothing queued


# ── stored bytes gone or altered: fail closed, never rebuild under the same id ─────────────────────────────────

async def _assert_failed_safely_and_the_next_request_opens_a_new_freeze(db, intake_id, first_job, out_failed, why):
    """What every unreadable-dataset case must show: the job FAILED with the reason; nothing was rendered from
    unverified bytes; nothing was rebuilt under the freeze id; the next default request opens a NEW freeze with a
    new id and a new hash; and the failed job stays visible as failed."""
    first_sha = first_job.request_parameters["dataset_sha256"]
    first_id = str(first_job.id)                     # read before any rollback expires `first_job`
    failed, state = await _run(db, out_failed)
    assert state == ReportExportJob.STATE_FAILED and failed.state == ReportExportJob.STATE_FAILED
    assert failed.error_reason.startswith(freeze.UNAVAILABLE_MARK) and why in failed.error_reason
    assert "new_freeze=true" in failed.error_reason and "nothing was rebuilt under that id" in failed.error_reason
    # never a report from unverified bytes: the job names no artifact and none is registered under its report id
    assert failed.artifact_id is None and failed.report_id is None and failed.rendered_sha256 is None
    failed_report = f"RECORD-REVIEW-{str(failed.id).upper()}"
    assert (await db.execute(select(ReportArtifact).where(
        ReportArtifact.report_id == failed_report))).scalars().all() == []
    assert "dataset_sha256" not in failed.request_parameters        # it never accepted a dataset
    # never rebuilt under the same freeze id: still the one registration, with the original hash
    datasets = await _datasets(db, intake_id)
    assert [(d.report_id, d.rendered_sha256) for d in datasets] == [
        (freeze.dataset_report_id(first_id), first_sha)]
    # the next DEFAULT request opens a new freeze: new id, new hash, and it renders
    out_next, next_job, next_csv, next_art = await _export(db, intake_id, "csv")
    assert out_next["dataset_reused"] is False and out_next["dataset_state"] == "pending"
    assert out_next["dataset_job_id"] == str(next_job.id) != first_id
    next_sha = next_job.request_parameters["dataset_sha256"]
    assert next_sha != first_sha and next_art["report_data_hash"] == next_sha
    assert f"SHA-256 {next_sha}" in _csv(next_csv)[0] and first_sha not in _csv(next_csv)[0]
    assert sorted(d.report_id for d in await _datasets(db, intake_id)) == sorted(
        [freeze.dataset_report_id(first_id), freeze.dataset_report_id(next_job.id)])
    # the dead freeze cannot be named either
    with pytest.raises(HTTPException) as exc:
        await _request(db, intake_id, "xlsx", dataset_job_id=first_id)
    assert exc.value.status_code == 409 and "no usable frozen dataset" in exc.value.detail
    # the failed job stays visible as failed, with its reason and no download link
    status = await report_routes.export_job_status(str(failed.id), db=db, user=QA)
    assert status["state"] == ReportExportJob.STATE_FAILED and "download" not in status
    assert status["error_reason"].startswith(freeze.UNAVAILABLE_MARK)
    assert status["dataset_job_id"] == first_id and status["dataset_sha256"] is None
    assert status["dataset_download"] is None
    return next_csv


async def test_a_frozen_dataset_whose_bytes_are_gone_fails_the_job_and_is_never_rebuilt_under_its_id(
        rolled_back_db, store):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.17")
    _, first_job, _, _ = await _export(db, intake_id, "csv")
    out_xl = await _request(db, intake_id, "xlsx")                    # names the freeze while it is readable
    assert out_xl["dataset_state"] == "frozen"
    shutil.rmtree(store)                                              # the non-durable store loses its files
    await _release_hold(db, recs[2])
    next_csv = await _assert_failed_safely_and_the_next_request_opens_a_new_freeze(
        db, intake_id, first_job, out_xl, "its stored bytes are missing from the artifact store")
    assert _totals(_csv(next_csv)[1])[1] == 0                         # the new freeze captured status again, now


async def test_a_frozen_dataset_whose_bytes_were_altered_fails_the_integrity_check_and_the_job(rolled_back_db, store):
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.18")
    _, first_job, _, _ = await _export(db, intake_id, "csv")
    out_xl = await _request(db, intake_id, "xlsx")
    assert out_xl["dataset_state"] == "frozen"                        # the bytes are present, so it is offered
    # the stored dataset bytes (the store keeps a small sidecar record beside them)
    target = next(p for p in store.rglob("artifact.bin") if "RECORD-REVIEW-DATASET" in str(p))
    altered = target.read_bytes().replace(b'"HELD"', b'"CLEAN"')
    assert altered != target.read_bytes()
    target.write_bytes(altered)                                       # valid JSON, different content, same name
    next_csv = await _assert_failed_safely_and_the_next_request_opens_a_new_freeze(
        db, intake_id, first_job, out_xl, "no longer hash to the registered SHA-256")
    assert _totals(_csv(next_csv)[1])[1] == 1                         # read from the database, not the altered file


async def test_a_registered_dataset_whose_storage_cannot_be_read_fails_the_job_but_does_not_retire_the_freeze(
        rolled_back_db, store, monkeypatch):
    """The registry row exists but reading the bytes fails (I/O, permission, backend client). That is not
    evidence the dataset is bad. The job fails closed - never "could not read, so load again under that id",
    never unverified bytes - and the freeze stays on offer, so a retry renders from the SAME dataset."""
    from app.core.storage import artifact_store

    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.21")
    _, first_job, _, _ = await _export(db, intake_id, "csv")
    first_sha = first_job.request_parameters["dataset_sha256"]
    out_xl = await _request(db, intake_id, "xlsx")
    assert out_xl["dataset_state"] == "frozen"
    real_get = artifact_store._store.get
    dead = freeze.dataset_report_id(first_job.id)
    broken = {"on": True}

    def unreadable(locator):
        if broken["on"] and dead in locator:
            raise PermissionError(13, "synthetic: storage refused the read")
        return real_get(locator)

    monkeypatch.setattr(artifact_store._store, "get", unreadable)
    await _release_hold(db, recs[2])                                  # a rebuild would now show no HELD record
    failed, state = await _run(db, out_xl)
    assert state == ReportExportJob.STATE_FAILED and failed.artifact_id is None and failed.report_id is None
    assert failed.error_reason.startswith(freeze.READ_FAILED_MARK)
    assert not failed.error_reason.startswith(freeze.UNAVAILABLE_MARK)
    assert "could not return its bytes (PermissionError)" in failed.error_reason
    assert "Nothing was rendered from unverified bytes" in failed.error_reason
    assert "nothing was rebuilt under that id" in failed.error_reason
    assert (await db.execute(select(ReportArtifact).where(
        ReportArtifact.report_id == f"RECORD-REVIEW-{str(failed.id).upper()}"))).scalars().all() == []
    assert [(d.report_id, d.rendered_sha256) for d in await _datasets(db, intake_id)] == [(dead, first_sha)]
    # the freeze is NOT retired: while storage is still failing, a default request still names it
    out_while_down = await _request(db, intake_id, "xlsx")
    assert out_while_down["dataset_reused"] is True and out_while_down["dataset_job_id"] == str(first_job.id)
    assert out_while_down["dataset_state"] == "frozen" and out_while_down["dataset_sha256"] == first_sha
    # storage restored: the retry renders from the SAME dataset, same hash, the original hold status
    broken["on"] = False
    retried, state = await _run(db, out_while_down)
    assert state == ReportExportJob.STATE_SUCCEEDED and retried.request_parameters["dataset_sha256"] == first_sha
    got = await artifact_registry.retrieve_artifact(
        db, retried.report_id, content_type=RECORD_REVIEW_CONTENT_TYPES["xlsx"])
    note, rows, _summary = _xlsx(got["content"])
    assert f"SHA-256 {first_sha}" in note and _totals(rows)[1] == 1  # still HELD: nothing was re-read
    assert len(await _datasets(db, intake_id)) == 1
    # the failed job stays visible as failed
    status = await report_routes.export_job_status(str(failed.id), db=db, user=QA)
    assert status["state"] == ReportExportJob.STATE_FAILED and "download" not in status
    assert status["error_reason"].startswith(freeze.READ_FAILED_MARK)


async def test_a_store_that_does_not_answer_the_request_time_check_does_not_retire_the_freeze(
        rolled_back_db, monkeypatch):
    from app.core.storage import artifact_store

    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.24")
    _, first_job, _, _ = await _export(db, intake_id, "csv")

    def no_answer(_locator):
        raise TimeoutError("synthetic: the store did not answer")

    monkeypatch.setattr(artifact_store._store, "head", no_answer)
    out = await _request(db, intake_id, "xlsx")
    assert out["dataset_reused"] is True and out["dataset_job_id"] == str(first_job.id)
    job, state = await _run(db, out)                                  # the runner reads and verifies the bytes
    assert state == ReportExportJob.STATE_SUCCEEDED
    assert job.request_parameters["dataset_sha256"] == first_job.request_parameters["dataset_sha256"]


# ── a default request uses the NEWEST freeze only; it never falls back to an older one ─────────────────────────

async def test_when_the_newest_freeze_is_dead_a_default_request_opens_a_new_one_and_never_an_older_one(
        rolled_back_db, store):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.25")
    _, f1_job, _, _ = await _export(db, intake_id, "csv")                               # F1: usable throughout
    f1_sha = f1_job.request_parameters["dataset_sha256"]
    await _release_hold(db, recs[2])
    out_f2, f2_job, _, _ = await _export(db, intake_id, "csv", new_freeze=True)         # F2: the operator's choice
    f2_sha = f2_job.request_parameters["dataset_sha256"]
    assert out_f2["dataset_job_id"] == str(f2_job.id) and f2_sha != f1_sha
    # F2's bytes are lost; F1's are still there
    for path in store.rglob("artifact.bin"):
        if freeze.dataset_report_id(f2_job.id) in str(path):
            path.unlink()
    out_f2_xl = await _request(db, intake_id, "xlsx")                 # still names F2, the newest freeze
    assert out_f2_xl["dataset_job_id"] == str(f2_job.id)
    f2_failed, state = await _run(db, out_f2_xl)                      # ... and F2 fails: its bytes are gone
    assert state == ReportExportJob.STATE_FAILED and f2_failed.error_reason.startswith(freeze.UNAVAILABLE_MARK)
    out_xl = await _request(db, intake_id, "xlsx")                    # the next DEFAULT request
    assert out_xl["dataset_reused"] is False                          # a NEW freeze ...
    assert out_xl["dataset_job_id"] == out_xl["job_id"]
    assert out_xl["dataset_job_id"] not in (str(f1_job.id), str(f2_job.id))             # ... never F1
    f3_job, state = await _run(db, out_xl)
    assert state == ReportExportJob.STATE_SUCCEEDED
    assert f3_job.request_parameters["dataset_sha256"] not in (f1_sha, f2_sha)
    got = await artifact_registry.retrieve_artifact(
        db, f3_job.report_id, content_type=RECORD_REVIEW_CONTENT_TYPES["xlsx"])
    assert _totals(_xlsx(got["content"])[1])[1] == 0                  # current status, not F1's older HELD
    # F1 is untouched and may still be rendered - but only when it is NAMED
    out_named, named_job, named_xl, _ = await _export(db, intake_id, "xlsx", dataset_job_id=str(f1_job.id))
    assert out_named["dataset_reused"] is True and named_job.request_parameters["dataset_sha256"] == f1_sha
    assert _totals(_xlsx(named_xl)[1])[1] == 1
    # and naming F1 did not make it the default again: the default is still the newest-opened freeze (F3)
    out_default = await _request(db, intake_id, "csv")
    assert out_default["dataset_job_id"] == str(f3_job.id)
    assert (await db.get(ReportExportJob, f2_failed.id)).state == ReportExportJob.STATE_FAILED   # F2 stays failed


async def test_when_the_newest_freeze_failed_before_freezing_a_default_request_does_not_fall_back(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.779.26")
    held_id = recs[2].id                              # read before a rollback expires the seeded rows
    _, f1_job, _, _ = await _export(db, intake_id, "csv")             # F1: usable
    f1_id = str(f1_job.id)
    out_f2 = await _request(db, intake_id, "csv", new_freeze=True)    # F2 opened by the operator ...
    real_load = ld.load_record_facts

    async def refused(_db, **_kw):
        raise ld.LoaderRefused("synthetic refusal while loading the new freeze")

    monkeypatch.setattr(ld, "load_record_facts", refused)
    f2_job, state = await _run(db, out_f2)                            # ... and it failed before anything was frozen
    assert state == ReportExportJob.STATE_FAILED and "synthetic refusal" in f2_job.error_reason
    monkeypatch.setattr(ld, "load_record_facts", real_load)
    await _release_hold(db, held_id)
    out_next, f3_job, f3_csv, _ = await _export(db, intake_id, "csv")
    assert out_next["dataset_reused"] is False
    assert out_next["dataset_job_id"] == str(f3_job.id) not in (f1_id, str(f2_job.id))
    assert _totals(_csv(f3_csv)[1])[1] == 0                           # F1 (HELD) was not silently used
    assert (await db.get(ReportExportJob, f2_job.id)).state == ReportExportJob.STATE_FAILED


# ── a database error while reading the dataset back is an ordinary job failure, and the job always ends ────────

async def test_a_database_error_during_the_dataset_read_back_fails_the_job_without_retiring_the_freeze(
        rolled_back_db, monkeypatch):
    from sqlalchemy import text

    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.27")
    _, first_job, _, _ = await _export(db, intake_id, "csv")
    out_xl = await _request(db, intake_id, "xlsx")
    real_rows = freeze._registered_rows

    async def database_error(session, report_id):
        await session.execute(text("select 1/0"))                     # a failed statement: the transaction is aborted

    monkeypatch.setattr(freeze, "_registered_rows", database_error)
    job, state = await _run(db, out_xl)
    assert state == ReportExportJob.STATE_FAILED and job.state == ReportExportJob.STATE_FAILED
    assert job.active_marker is None and job.failed_at is not None    # terminal now, not left RUNNING for the reaper
    assert "could not be produced" in job.error_reason
    assert freeze.UNAVAILABLE_MARK not in job.error_reason and freeze.READ_FAILED_MARK not in job.error_reason
    monkeypatch.setattr(freeze, "_registered_rows", real_rows)
    out_retry = await _request(db, intake_id, "xlsx")                 # the freeze was not retired by a database error
    assert out_retry["dataset_reused"] is True and out_retry["dataset_job_id"] == str(first_job.id)
    retried, state = await _run(db, out_retry)
    assert state == ReportExportJob.STATE_SUCCEEDED
    assert retried.request_parameters["dataset_sha256"] == first_job.request_parameters["dataset_sha256"]


async def test_a_refusal_raised_on_an_aborted_transaction_still_ends_the_job_failed_with_its_reason(
        rolled_back_db, monkeypatch):
    """The refusal branch used to call finish_failed with no rollback: on an aborted transaction that write raised,
    the poller swallowed it and the job stayed RUNNING until the reaper."""
    from sqlalchemy import text

    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.28")
    out = await _request(db, intake_id, "csv")

    async def refuses_after_a_failed_statement(session, job, params):
        try:
            await session.execute(text("select 1/0"))
        except Exception:  # noqa: BLE001 - the transaction is now aborted
            pass
        raise ld.LoaderRefused("synthetic refusal on an aborted transaction")

    monkeypatch.setattr(freeze, "obtain", refuses_after_a_failed_statement)
    job, state = await _run(db, out)
    assert state == ReportExportJob.STATE_FAILED and job.state == ReportExportJob.STATE_FAILED
    assert job.error_reason == "synthetic refusal on an aborted transaction"
    assert job.active_marker is None and job.failed_at is not None


async def test_two_datasets_registered_under_one_freeze_id_are_refused_not_chosen_between(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.22")
    _, first_job, _, _ = await _export(db, intake_id, "csv")
    out_xl = await _request(db, intake_id, "xlsx")
    await artifact_registry.finalize_artifact(                        # a second registration that must never exist
        db, report_id=freeze.dataset_report_id(first_job.id), report_type=freeze.DATASET_REPORT_TYPE,
        content=b'{"schema":"other"}', content_type=freeze.DATASET_CONTENT_TYPE,
        review_cycle_id="synthetic-second-registration", generated_by=QA.email)
    failed, state = await _run(db, out_xl)
    assert state == ReportExportJob.STATE_FAILED and failed.artifact_id is None
    assert failed.error_reason.startswith(freeze.UNAVAILABLE_MARK)
    assert "2 datasets are registered under it where exactly one is allowed" in failed.error_reason


# ── row count is enforced ──────────────────────────────────────────────────────────────────────────────────────

async def test_a_row_count_mismatch_is_refused_before_queuing_and_fails_a_queued_job_with_the_two_numbers(
        rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.19")
    out = await _request(db, intake_id, "csv")                        # queued while the delivery still reconciles
    intake = await db.get(m.RceSourceIntake, intake_id)
    intake.record_count = 5                                           # the delivery says 5; 4 records were stored
    await db.flush()
    with pytest.raises(ld.LoaderRefused, match="row count mismatch"):
        await ld.load_record_facts(db, intake_id=intake_id, evidence_cutoff=CUTOFF_ISO)
    # the route refuses a request that can never succeed, before anything is queued
    jobs_before = len((await db.execute(select(ReportExportJob.id).where(
        ReportExportJob.source_intake_id == intake_id))).all())
    with pytest.raises(HTTPException) as exc:
        await _request(db, intake_id, "xlsx")
    assert exc.value.status_code == 409 and "row count mismatch" in exc.value.detail
    assert "records 5 record(s)" in exc.value.detail and "4 source record(s) are stored" in exc.value.detail
    assert len((await db.execute(select(ReportExportJob.id).where(
        ReportExportJob.source_intake_id == intake_id))).all()) == jobs_before
    # the loader is the authority: the job queued earlier fails with both numbers and registers nothing
    job, state = await _run(db, out)
    assert state == ReportExportJob.STATE_FAILED and job.artifact_id is None
    assert "row count mismatch" in job.error_reason and "records 5 record(s)" in job.error_reason
    assert "4 source record(s) were loaded" in job.error_reason
    assert await _datasets(db, intake_id) == []                       # nothing frozen, nothing rendered
    assert (await db.execute(select(ReportArtifact).where(
        ReportArtifact.review_cycle_id == str(intake_id)))).scalars().all() == []


# ── historical jobs and artifacts ──────────────────────────────────────────────────────────────────────────────

async def test_a_job_made_before_freezes_existed_is_never_reused_re_rendered_or_altered(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.20")
    run_id = (await db.execute(select(m.RceIngestionRun.id).where(
        m.RceIngestionRun.source_intake_id == intake_id))).scalar_one()
    old_id = uuid.uuid4()
    old_params = {"format": "csv", "evidence_cutoff": CUTOFF_ISO, "run_id": str(run_id)}
    old_report = f"RECORD-REVIEW-{str(old_id).upper()}"
    old_artifact = await artifact_registry.finalize_artifact(
        db, report_id=old_report, report_type="record_review:csv", content=b"historical csv bytes",
        content_type="text/csv", review_cycle_id=str(intake_id), generated_by=QA.email)
    db.add(ReportExportJob(
        id=old_id, identity="0" * 64, export_type="record_review:csv", source_intake_id=intake_id,
        classification="DEVELOPMENT_TEST", generator_version=freeze.generator_version(),
        state=ReportExportJob.STATE_SUCCEEDED, active_marker=None, requested_by=QA.email,
        created_at=datetime(2026, 11, 20), completed_at=datetime(2026, 11, 20), attempt_count=1,
        request_parameters=dict(old_params), report_id=old_report, artifact_id=old_artifact["artifact_id"],
        artifact_version=1, rendered_sha256=old_artifact["rendered_sha256"], size_bytes=20))
    await db.flush()

    out, job, _, _ = await _export(db, intake_id, "csv")
    assert out["dataset_reused"] is False and out["dataset_job_id"] == str(job.id) != str(old_id)
    old = await db.get(ReportExportJob, old_id)
    assert old.request_parameters == old_params and old.state == ReportExportJob.STATE_SUCCEEDED
    assert old.rendered_sha256 == old_artifact["rendered_sha256"] and old.report_id == old_report
    rows = (await db.execute(select(ReportArtifact).where(ReportArtifact.report_id == old_report))).scalars().all()
    assert [(r.content_type, r.artifact_version, r.rendered_sha256) for r in rows] == [
        ("text/csv", 1, old_artifact["rendered_sha256"])]
    assert (await artifact_registry.retrieve_artifact(db, old_report, content_type="text/csv"))["content"] \
        == b"historical csv bytes"
    # its status names no dataset: nothing is claimed about a report that was not rendered from one
    payload = await report_routes.export_job_status(str(old_id), db=db, user=QA)
    assert payload["dataset_job_id"] is None and payload["dataset_sha256"] is None
    assert payload["curated_status_basis"] is None and payload["dataset_download"] is None


# ── the dataset function itself (pure) ─────────────────────────────────────────────────────────────────────────

def _fact(i, **kw):
    base = {"agt_record_id": f"00000000-0000-0000-0000-{i:012d}", "source_line": i + 1, "bucket": "B1",
            "status": "CLEAN"}
    base.update(kw)
    return base


def test_a_dataset_built_from_a_frozen_copy_states_the_hash_and_the_freeze_time_in_every_rendering():
    frozen = {"sha256": "ab" * 32, "dataset_id": "RECORD-REVIEW-DATASET-X", "frozen_at": "2026-11-25T10:00:05Z",
              "capture_started_at": "2026-11-25T10:00:00Z"}
    facts = [_fact(1), _fact(2, bucket=None, status="HELD", held_reason="blocker")]
    built = ds_mod.build_dataset(facts, report_id="R-1", frozen=frozen)
    assert built["summary"]["curated_status_basis"] == "freeze_time"
    assert built["summary"]["curated_status_captured_at"] == "2026-11-25T10:00:05Z"
    assert built["summary"]["frozen_dataset"] == frozen
    rows = dict(built["sheets"]["Summary"]["rows"])
    assert rows[ds_mod.FROZEN_SHA_ROW] == "ab" * 32 and rows[ds_mod.FROZEN_AT_ROW] == "2026-11-25T10:00:05Z"
    assert rows[ds_mod.FROZEN_STATUS_ROW] == "2026-11-25T10:00:00Z to 2026-11-25T10:00:05Z"
    for text in (built["banner"], ds_mod.to_csv(built).decode("utf-8-sig"), ds_mod.to_html(built)):
        assert "SHA-256 " + "ab" * 32 in text and "2026-11-25T10:00:05Z" in text
        assert "as of report generation" not in text
    # the statement is made with or without a HELD record
    assert "FROZEN DATASET" in ds_mod.build_dataset([_fact(1)], report_id="R-2", frozen=frozen)["banner"]
    # a dataset built straight from the database (scripts) still says what it is
    direct = ds_mod.build_dataset(facts, report_id="R-3")
    assert direct["summary"]["curated_status_basis"] == "generation_time" and "FROZEN DATASET" not in direct["banner"]
    assert "as of report generation" in direct["banner"]
    with pytest.raises(ds_mod.DatasetRefused):
        ds_mod.build_dataset(facts, report_id="R-4", frozen={"sha256": "ab" * 32})


def test_the_dataset_has_its_own_id_and_content_type_never_a_formats():
    job_id = uuid.uuid4()
    assert freeze.dataset_report_id(job_id) == f"RECORD-REVIEW-DATASET-{str(job_id).upper()}"
    assert len(freeze.dataset_report_id(job_id)) <= 64
    assert freeze.DATASET_CONTENT_TYPE not in RECORD_REVIEW_CONTENT_TYPES.values()
    assert artifact_registry.ARTIFACT_SUFFIXES[freeze.DATASET_CONTENT_TYPE] == "json"
    a = freeze.canonical_bytes({"b": 1, "a": ["x", {"d": 2, "c": None}]})
    assert a == freeze.canonical_bytes({"a": ["x", {"c": None, "d": 2}], "b": 1})       # one serialisation


# ── a reaped record-review job is not flipped to SUCCEEDED by its late worker ──────────────────────────────────

async def test_a_record_review_job_reaped_while_rendering_stays_failed_and_the_run_reports_failed(
        rolled_back_db, monkeypatch):
    from app.reports.data import export_jobs

    db = rolled_back_db
    intake_id, _ = await _seed(db, "9.99.779.23")
    out = await _request(db, intake_id, "csv")
    real_finalize = artifact_registry.finalize_artifact

    async def reaped_before_the_rendering_is_registered(db_, **kw):
        if kw["content_type"] == "text/csv":
            # the reaper runs now: every job that has been silent "too long" (threshold in the future = this one)
            reaped = await export_jobs.reap_stale_jobs(db_, threshold_seconds=-60)
            assert out["job_id"] in [r["job_id"] for r in reaped]
        return await real_finalize(db_, **kw)

    monkeypatch.setattr(artifact_registry, "finalize_artifact", reaped_before_the_rendering_is_registered)
    job, state = await _run(db, out)
    assert state == ReportExportJob.STATE_FAILED                      # what the row says, not what the worker hoped
    assert job.state == ReportExportJob.STATE_FAILED and job.error_reason == export_jobs.REAPED_REASON
    assert job.report_id is None and job.artifact_id is None
    status = await report_routes.export_job_status(out["job_id"], db=db, user=QA)
    assert status["state"] == ReportExportJob.STATE_FAILED and "download" not in status
