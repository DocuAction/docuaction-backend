"""The record-review export, end to end over HTTP, for each format:

    POST /exports/record-review -> queued job -> claim -> runner -> artifact
    REGISTERED in a real (local-filesystem) store -> GET status -> the link the
    status returns -> the downloaded bytes.

WHAT IS REAL AND WHAT IS NOT
    Real: the reports router and its own role gates (real JWTs for users seeded
    in the transaction - no auth override), the exception handlers, the job
    table, `claim_next_queued`, the scheduler's dispatch rule, the runner, the
    loader, the dataset, the CSV/XLSX renderers, `finalize_artifact`, the
    registry table and a LocalFilesystemArtifactStore in pytest's tmp_path.
    Replaced: only `get_db` (so every write lands in the rolled-back
    transaction of `rolled_back_db`), and the PDF renderer in the ONE test
    named "stub" - that test proves routing only. The test named "real
    WeasyPrint" downloads a PDF the production engine rendered and reads it
    back; it skips where the engine's native stack is absent and FAILS instead
    when RECORD_REVIEW_REQUIRE_REAL_RENDER=1 (the CI job).

Synthetic data only; a disposable database. Nothing is committed: seeding
happens inside the outer transaction, review ids are unique per run, and the
store lives in tmp_path.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

import record_review_pdf_support as s
from app.reports import routes as report_routes
from app.reports.data import export_jobs
from app.reports.data import october_review_dataset as ds_mod
from app.reports.data.export_job_model import ReportExportJob
from app.reports.engine.pdf_engine import pdf_available, unavailable_reason
from app.reports.export_runner import run_record_review_export_job
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

REQUIRE = os.getenv("RECORD_REVIEW_REQUIRE_REAL_RENDER") == "1"
OUT = os.getenv("RECORD_REVIEW_PDF_OUT")
CUTOFF_ISO = "2099-01-01T00:00:00"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CONTENT_TYPES = {"xlsx": XLSX, "csv": "text/csv", "pdf": "application/pdf"}
LONG_COMMENT = ("Original QA comment: the submitted NPI did not resolve in NPPES on the assessment date; "
                "identifier string for wrap check: urn:uuid:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/"
                "ffffffffffffffffffffffffffffffffffffffffff")
IZAAN_COMMENT = ("Second-reviewer comment (Izaan): concur with hold.\nSecond paragraph kept verbatim, "
                 "with  double  spaces and a trailing tab\t.")
CLARIFICATION = "[PECOS-221] Other: test identifier not found in the CMS PPEF extract; no outage is asserted."


# ── environment: store, app, users ────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    """A local artifact store in tmp_path, installed as the process-wide store."""
    from app.core.storage import artifact_store

    local = artifact_store.LocalFilesystemArtifactStore(str(tmp_path / "artifacts"))
    monkeypatch.setattr(artifact_store, "_store", local)
    return tmp_path / "artifacts"


def _app(db) -> FastAPI:
    from app.core.database import get_db
    from app.core.error_handler import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(report_routes.router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db          # the ONLY override: auth is the real thing
    return app


async def _user(db, role: str):
    from app.core.security import create_access_token, hash_password
    from app.models.database import User

    user = User(id=uuid.uuid4(), tenant_id="synthetic-download-path",
                email=f"{role}-{uuid.uuid4().hex[:10]}@synthetic-test.docuaction.invalid",
                password_hash=hash_password(uuid.uuid4().hex), full_name=f"SYNTHETIC {role}",
                role=role, is_active=True, is_verified=True, status="active", allowed_modules=[])
    db.add(user)
    await db.flush()
    token = create_access_token({"sub": str(user.id), "role": role})
    return user, {"Authorization": f"Bearer {token}"}


def _client(db):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(db)), base_url="http://t")


# ── seeding (inside the rolled-back transaction) ──────────────────────────────

async def _seed(db, *, n=14, tag="A"):
    """One synthetic delivery of `n` records with a completed run: CLEAN records
    classified B1..B4 (one provisional B2), two HELD (one without a reason), the
    last two with no curated row (unclassified), and all three comment kinds on
    record 2. Review ids are unique per call, so re-seeding cannot collide."""
    from app.tefca_registry.rce import record_comments as rcm

    arc = f"9.99.781.{uuid.uuid4().int % 9000 + 1000}"
    rows = make_rows(n, arc=arc)
    for r in rows:
        r["name"] = f"SYNTHETIC TEST DATA {tag} {r['name']}"
    rows[1]["NPI"] = "1234567893"
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    db.add(m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="synthetic",
        rule_config_hash="0" * 64, started_at=datetime(2026, 11, 7, 8),
        completed_at=datetime(2026, 11, 8, 9), run_status="COMPLETE",
        assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"}))
    recs = {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
    plan = {1: ("CLEAN", "B1", "ok"), 2: ("CLEAN", "B1", "ok"),
            3: ("CLEAN", "B2", "Mailing address differs from the NPPES practice location; provisional"),
            4: ("CLEAN", "B3", "Out of ONC Treatment scope"), 5: ("CLEAN", "B4", "Submitted NPI blank"),
            6: ("HELD", None, f"blocker {tag}: shared NPI across two delivered records"),
            7: ("HELD", None, None)}
    for i in range(8, n - 1):
        plan[i] = ("CLEAN", ("B1", "B2", "B3", "B4")[i % 4], f"synthetic reason {tag}-{i}")
    for i, (status, bucket, why) in plan.items():
        if i > n:
            continue
        ent = await seed_entity(db, oid=f"{SYN}-DL-{uuid.uuid4().hex[:10]}", name=f"SYNTHETIC ent {tag}{i}")
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=recs[i].id,
            record_status=status, status_reason=why if status == "HELD" else None,
            rce_org_oid=recs[i].source_rce_id, transformation_version="synthetic",
            canonical_entity_id=ent if status == "CLEAN" else None))
        if bucket:
            db.add(reg.ReviewRecord(
                id=uuid.uuid4(), review_id=f"RS{uuid.uuid4().hex[:14]}{i:04d}", entity_id=ent,    # varchar(20), unique per run
                source_record_id=recs[i].id, classification_bucket=bucket,
                classification_rule="RULE-00X", classification_rule_version=4,
                classification_rationale=why, created_at=datetime(2026, 11, 8, 10)))
    await db.flush()

    def entry(rec, kind, body, **over):
        e = {"source_record_id": str(rec.id), "comment_kind": kind, "comment_text": body,
             "expected": {"source_line": rec.line_number, "hcid": rec.hcid, "tefca_id": rec.tefcaid},
             "source": {"file_name": "SYNTHETIC_comments.xlsx", "file_sha256": "ab" * 32,
                        "sheet": "S", "column": "QA Comments"}}
        e.update(over)
        return e

    izaan = dict(author_attribution="Izaan", attribution_basis="owner designation of the source file (synthetic)")
    out = await rcm.import_comments(db, intake_id=intake_id, actor="synthetic@synthetic-test.docuaction.invalid", entries=[
        entry(recs[2], "QA_ORIGINAL", f"{tag}: {LONG_COMMENT}"),
        entry(recs[2], "QA_IZAAN", f"{tag}: {IZAAN_COMMENT}", **izaan),
        entry(recs[2], "REPORT_CLARIFICATION", f"{tag}: {CLARIFICATION}"),
        entry(recs[6], "QA_ORIGINAL", f"{tag}: HELD record original comment - verbatim, kept with its hold")])
    assert out["counts"]["rejected"] == 0, out
    await db.flush()
    return intake_id, recs


async def _run_next_like_the_scheduler(db) -> str:
    """Claim the oldest queued job and dispatch it by the scheduler's own rule
    (app/reports/export_scheduler.py::_poll_tick), on this session."""
    job = await export_jobs.claim_next_queued(db)
    assert job is not None, "nothing was queued"
    assert (job.export_type or "").startswith(export_jobs.RECORD_REVIEW_EXPORT_TYPE_PREFIX)
    return await run_record_review_export_job(db, job)


async def _export(client, db, headers, intake_id, fmt):
    """request -> job -> registration -> status. Returns (status payload, job id)."""
    r = await client.post("/api/reports/exports/record-review", headers=headers,
                          json={"intake_id": str(intake_id), "evidence_cutoff": CUTOFF_ISO, "format": fmt})
    assert r.status_code == 200, r.text
    queued = r.json()
    assert queued["state"] == ReportExportJob.STATE_QUEUED and queued["format"] == fmt
    pending = await client.get(queued["status_url"], headers=headers)
    assert pending.status_code == 200 and "download" not in pending.json()     # nothing to fetch yet
    assert await _run_next_like_the_scheduler(db) == ReportExportJob.STATE_SUCCEEDED
    status = await client.get(queued["status_url"], headers=headers)
    assert status.status_code == 200, status.text
    return status.json(), queued["job_id"]


def _assert_download(resp, status, fmt):
    """The response is the registered artifact of THIS job, in THIS format."""
    assert resp.status_code == 200, resp.text[:300]
    assert resp.headers["content-type"].split(";")[0] == CONTENT_TYPES[fmt]
    report_id = status["report_id"]
    assert report_id == f"RECORD-REVIEW-{status['job_id'][:8].upper()}"
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="{report_id}-v{status["artifact_version"]}.{fmt}"')
    assert resp.headers["x-content-type-options"] == "nosniff"
    digest = hashlib.sha256(resp.content).hexdigest()
    assert digest == status["rendered_sha256"] == resp.headers["x-artifact-sha256"]
    assert len(resp.content) == status["size_bytes"]
    assert resp.headers["x-artifact-verified"] == "true"


def _csv_rows(content: bytes):
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))[2:]


def _xlsx_rows(content: bytes):
    import openpyxl

    ws = openpyxl.load_workbook(io.BytesIO(content))["Records"]
    header = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1) if r and r[0] == "Source_Line")
    return [["" if v is None else str(v) for v in r] for r in ws.iter_rows(min_row=header + 1, values_only=True)]


COL = {c: i for i, c in enumerate(ds_mod.RECORD_COLUMNS)}


def _assert_rows_are_the_seeded_delivery(rows, recs, tag):
    assert len(rows) == len(recs)
    assert [r[COL["AGT_Record_ID"]] for r in rows] == [str(recs[i].id) for i in sorted(recs)]
    by = {r[COL["AGT_Record_ID"]]: r for r in rows}
    two, six, seven = by[str(recs[2].id)], by[str(recs[6].id)], by[str(recs[7].id)]
    assert two[COL[ds_mod.ORIGINAL_COMMENTS_COLUMN]] == f"{tag}: {LONG_COMMENT}"
    assert two[COL[ds_mod.IZAAN_COMMENTS_COLUMN]] == f"{tag}: {IZAAN_COMMENT}"         # tab, newline, spaces
    assert two[COL["Report Clarifications"]] == f"{tag}: {CLARIFICATION}"
    assert two[COL["Submitted NPI (original, verbatim)"]] == "1234567893"
    assert (six[COL["Bucket"]], six[COL["Status"]]) == ("B4", "HELD") == (seven[COL["Bucket"]], seven[COL["Status"]])
    assert f"blocker {tag}" in six[COL["Bucket Basis"]]
    assert by[str(recs[3].id)][COL["Bucket"]] == "B2"
    last = by[str(recs[max(recs)].id)]
    assert last[COL["Bucket"]] == ds_mod.NOT_CLASSIFIED
    assert all(tag in r[COL["Organization"]] for r in rows)


# ── the three formats, end to end ─────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
async def test_request_job_registration_status_and_download_return_the_formats_own_artifact(
        rolled_back_db, store, fmt):
    db = rolled_back_db
    _qa, headers = await _user(db, "qalead")
    intake_id, recs = await _seed(db, tag="A")
    async with _client(db) as client:
        status, _job_id = await _export(client, db, headers, intake_id, fmt)
        assert status["download"] == (f"/api/reports/artifacts/{status['report_id']}/download"
                                      f"?content_type={CONTENT_TYPES[fmt]}")
        resp = await client.get(status["download"], headers=headers)
    _assert_download(resp, status, fmt)
    rows = _csv_rows(resp.content) if fmt == "csv" else _xlsx_rows(resp.content)
    _assert_rows_are_the_seeded_delivery(rows, recs, "A")
    assert any(store.rglob("*")), "the artifact was not written to the local store"


async def test_pdf_download_path_with_a_stub_renderer_routing_only(rolled_back_db, store, monkeypatch):
    """ROUTING ONLY: the PDF bytes are a stub, so this says nothing about the PDF
    itself. It runs on every host, including those without WeasyPrint."""
    db = rolled_back_db
    stub = b"%PDF-1.7\n% STUB RENDERER - routing test only, not a rendered document\n%%EOF\n"
    monkeypatch.setattr(ds_mod, "to_pdf", lambda dataset: stub)
    _qa, headers = await _user(db, "qalead")
    intake_id, _recs = await _seed(db, tag="A")
    async with _client(db) as client:
        status, _job_id = await _export(client, db, headers, intake_id, "pdf")
        assert status["download"].endswith("?content_type=application/pdf")
        resp = await client.get(status["download"], headers=headers)
    _assert_download(resp, status, "pdf")
    assert resp.content == stub


async def test_pdf_download_is_the_real_weasyprint_pdf_of_the_seeded_delivery(rolled_back_db, store):
    """REAL RENDER: no renderer is replaced. The downloaded bytes are what
    WeasyPrint produced for the delivery seeded above, read back by position."""
    if not pdf_available():
        if REQUIRE:
            pytest.fail("RECORD_REVIEW_REQUIRE_REAL_RENDER=1 but WeasyPrint cannot render here: "
                        + unavailable_reason())
        pytest.skip("WeasyPrint native libraries are not available here")
    db = rolled_back_db
    _qa, headers = await _user(db, "qalead")
    intake_id, recs = await _seed(db, tag="A")
    async with _client(db) as client:
        csv_status, _ = await _export(client, db, headers, intake_id, "csv")
        csv_resp = await client.get(csv_status["download"], headers=headers)
        status, _job_id = await _export(client, db, headers, intake_id, "pdf")
        resp = await client.get(status["download"], headers=headers)
    _assert_download(resp, status, "pdf")
    assert resp.content.startswith(b"%PDF-") and len(resp.content) > 20_000
    if OUT:
        Path(OUT).mkdir(parents=True, exist_ok=True)
        (Path(OUT) / "weasyprint-download-path.pdf").write_bytes(resp.content)
    # the PDF and the CSV of the same delivery describe the same records
    rows = _csv_rows(csv_resp.content)
    _assert_rows_are_the_seeded_delivery(rows, recs, "A")
    pages = s.read_pdf(resp.content)
    tables = s.table_words(pages)
    detail = s.reconstruct(pages, tables["detail"], [w for _c, w in ds_mod.PDF_DETAIL_COLUMNS], 0,
                           is_row_start=s.UUID_START)
    assert not detail["continued"] and not detail["outside"]
    labels = [c for c, _w in ds_mod.PDF_DETAIL_COLUMNS]
    by = {}
    for row in detail["rows"]:
        by.setdefault(row[0][:36], row)             # the id cell starts with the record's UUID
    two = by[str(recs[2].id)]
    assert two[labels.index(ds_mod.ORIGINAL_COMMENTS_COLUMN)] == s.squash(f"A: {LONG_COMMENT}")
    assert two[labels.index(ds_mod.IZAAN_COMMENTS_COLUMN)] == s.squash(f"A: {IZAAN_COMMENT}")
    assert two[labels.index("Report Clarifications")] == s.squash(f"A: {CLARIFICATION}")
    text = s.squash(" ".join(w["text"] for p in pages for w in p["words"]))
    for i in sorted(recs):
        assert s.squash(str(recs[i].id)) in text, f"record {i} is not in the downloaded PDF"
    for number, page in enumerate(pages, start=1):
        _top, _body, bottom = s.split_furniture(page)
        footer = " ".join(w["text"] for w in sorted(bottom, key=lambda w: w["x0"]))
        assert s.FOOTER_TEXT in footer and f"Page {number} of {len(pages)}" in footer


# ── one job's link never returns another job's artifact ───────────────────────

async def test_each_jobs_link_returns_its_own_artifact_not_another_jobs_or_another_formats(
        rolled_back_db, store):
    db = rolled_back_db
    _qa, headers = await _user(db, "qalead")
    intake_a, recs_a = await _seed(db, tag="A")
    intake_b, recs_b = await _seed(db, n=6, tag="B")
    async with _client(db) as client:
        a_csv, _ = await _export(client, db, headers, intake_a, "csv")
        b_csv, _ = await _export(client, db, headers, intake_b, "csv")
        a_xlsx, _ = await _export(client, db, headers, intake_a, "xlsx")
        got = {name: await client.get(st["download"], headers=headers)
               for name, st in (("a_csv", a_csv), ("b_csv", b_csv), ("a_xlsx", a_xlsx))}
        # the csv job's report id asked for as a workbook: not registered, not "the other one"
        crossed = await client.get(f"/api/reports/artifacts/{a_csv['report_id']}/download"
                                   f"?content_type={XLSX}", headers=headers)
    assert len({a_csv["report_id"], b_csv["report_id"], a_xlsx["report_id"]}) == 3
    _assert_download(got["a_csv"], a_csv, "csv")
    _assert_download(got["b_csv"], b_csv, "csv")
    _assert_download(got["a_xlsx"], a_xlsx, "xlsx")
    _assert_rows_are_the_seeded_delivery(_csv_rows(got["a_csv"].content), recs_a, "A")
    _assert_rows_are_the_seeded_delivery(_xlsx_rows(got["a_xlsx"].content), recs_a, "A")
    b_rows = _csv_rows(got["b_csv"].content)
    assert [r[COL["AGT_Record_ID"]] for r in b_rows] == [str(recs_b[i].id) for i in sorted(recs_b)]
    assert all(" B " in r[COL["Organization"]] for r in b_rows)
    assert got["a_csv"].content != got["b_csv"].content
    assert crossed.status_code == 404 and crossed.json()["code"] == "ARTIFACT_NOT_REGISTERED"


# ── authorization and the not-there cases are unchanged ───────────────────────

async def test_role_gates_ownership_and_expired_sessions_are_enforced_on_every_step(rolled_back_db, store):
    from app.core.security import create_token

    db = rolled_back_db
    owner, owner_h = await _user(db, "qalead")
    _other, other_qalead_h = await _user(db, "qalead")
    _rev, reviewer_h = await _user(db, "reviewer")
    _con, contributor_h = await _user(db, "contributor")
    _pm, pm_h = await _user(db, "program_manager")
    expired_h = {"Authorization": "Bearer " + create_token(
        {"sub": str(owner.id), "role": "qalead", "type": "access"}, expires=timedelta(seconds=-60))}
    intake_id, _recs = await _seed(db, tag="A")
    body = {"intake_id": str(intake_id), "evidence_cutoff": CUTOFF_ISO, "format": "csv"}
    async with _client(db) as client:
        status, _job_id = await _export(client, db, owner_h, intake_id, "csv")
        status_url = f"/api/reports/exports/jobs/{status['job_id']}"
        # request: qalead floor
        assert (await client.post("/api/reports/exports/record-review", json=body)).status_code in (401, 403)
        for h in (reviewer_h, contributor_h):
            assert (await client.post("/api/reports/exports/record-review", json=body, headers=h)).status_code == 403
        assert (await client.post("/api/reports/exports/record-review", json=body,
                                  headers=expired_h)).status_code == 401
        # status: the requester and supervisors; anyone else gets 404, not 403
        assert (await client.get(status_url)).status_code in (401, 403)
        assert (await client.get(status_url, headers=reviewer_h)).status_code == 403
        assert (await client.get(status_url, headers=other_qalead_h)).status_code == 404
        assert (await client.get(status_url, headers=pm_h)).status_code == 200
        assert (await client.get(status_url, headers=expired_h)).status_code == 401
        # download: reviewer floor
        assert (await client.get(status["download"])).status_code in (401, 403)
        assert (await client.get(status["download"], headers=contributor_h)).status_code == 403
        assert (await client.get(status["download"], headers=expired_h)).status_code == 401
        assert (await client.get(status["download"], headers=reviewer_h)).status_code == 200
        _assert_download(await client.get(status["download"], headers=owner_h), status, "csv")


async def test_unregistered_missing_and_altered_artifacts_are_refused_as_before(rolled_back_db, store):
    db = rolled_back_db
    _qa, headers = await _user(db, "qalead")
    intake_id, _recs = await _seed(db, tag="A")
    async with _client(db) as client:
        never = await client.get("/api/reports/artifacts/RECORD-REVIEW-00000000/download"
                                 "?content_type=text/csv", headers=headers)
        assert never.status_code == 404 and never.json()["code"] == "ARTIFACT_NOT_REGISTERED"
        assert (await client.get(f"/api/reports/exports/jobs/{uuid.uuid4()}", headers=headers)).status_code == 404

        altered, _ = await _export(client, db, headers, intake_id, "csv")
        files = [p for p in store.rglob("*") if p.is_file() and p.read_bytes()[:3] == b"\xef\xbb\xbf"]
        assert len(files) == 1, [str(p) for p in store.rglob("*")]
        files[0].write_bytes(files[0].read_bytes() + b"tampered\n")
        refused = await client.get(altered["download"], headers=headers)
        assert refused.status_code == 500 and b"tampered" not in refused.content

        gone, _ = await _export(client, db, headers, intake_id, "xlsx")
        for p in [p for p in store.rglob("*") if p.is_file() and p.read_bytes()[:2] == b"PK"]:
            p.unlink()
        missing = await client.get(gone["download"], headers=headers)
        assert missing.status_code == 410 and missing.json()["code"] == "ARTIFACT_MISSING"
        # the job row still records what was issued
        still = await client.get(f"/api/reports/exports/jobs/{gone['job_id']}", headers=headers)
        assert still.status_code == 200 and still.json()["rendered_sha256"] == gone["rendered_sha256"]


async def test_a_failed_job_offers_no_download_link(rolled_back_db, store, monkeypatch):
    db = rolled_back_db
    _qa, headers = await _user(db, "qalead")
    intake_id, _recs = await _seed(db, tag="A")

    def boom(dataset):
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(ds_mod, "to_csv", boom)
    async with _client(db) as client:
        r = await client.post("/api/reports/exports/record-review", headers=headers,
                              json={"intake_id": str(intake_id), "evidence_cutoff": CUTOFF_ISO, "format": "csv"})
        assert await _run_next_like_the_scheduler(db) == ReportExportJob.STATE_FAILED
        status = (await client.get(r.json()["status_url"], headers=headers)).json()
    assert status["state"] == ReportExportJob.STATE_FAILED and "download" not in status
    assert not [p for p in store.rglob("*") if p.is_file()], "a failed job left bytes in the store"

def test_the_registered_template_version_fits_the_registry_column():
    """The label that broke registration: 41 characters into varchar(32)."""
    from app.reports.data import october_review_loader as loader
    from app.reports.data.artifact_registry import ReportArtifact
    from app.reports.export_runner import record_review_template_version

    label = record_review_template_version(ds_mod.DATASET_VERSION, loader.LOADER_VERSION)
    assert len(label) <= ReportArtifact.__table__.c.template_version.type.length
    assert ds_mod.DATASET_VERSION in label and loader.LOADER_VERSION in label
