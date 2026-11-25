"""Record-review export under REAL concurrency: requests and runners on separate database connections.

The other frozen-dataset tests run inside one rolled-back transaction, where an advisory lock can never block and
"concurrent" jobs are run one after another. Here every request and every runner has its OWN session on its OWN
connection and they are awaited together (`asyncio.gather`), so the two advisory locks in
`record_review_freeze` actually serialise independent transactions:

  * requests for csv / xlsx / pdf (and a duplicate csv) that arrive before any freeze exists must all name ONE
    freeze;
  * runners that all find no dataset registered, all load, and race to register must leave exactly ONE dataset
    artifact, and every job must render it: same dataset_job_id, same dataset_sha256, identical totals - while a
    hold is being released concurrently.

Rows are COMMITTED (separate connections cannot see uncommitted rows). Everything is synthetic, keyed to this
test's own delivery ids, and deleted again in the fixture's teardown. The PDF is stubbed at the engine boundary
only (`pdf_engine.render_pdf` returns the HTML it was given); everything above it is the production path.
"""
from __future__ import annotations

import asyncio
import csv
import html as html_lib
import io
import os
import re
import uuid
from datetime import datetime
from types import SimpleNamespace

import openpyxl
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

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
from rce_traceability_support import SYN, make_rows, seed_entity, seed_intake

CUTOFF_ISO = "2026-11-09T00:00:00"
T_OLD = datetime(2026, 11, 8, 10, 0, 0)
QA = SimpleNamespace(email="qalead-concurrent@synthetic-test.docuaction.invalid", role="qalead")
HDR = ds_mod.RECORD_COLUMNS
FORMATS = ("csv", "xlsx", "pdf")


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    from app.core.storage import artifact_store
    from app.reports.engine import pdf_engine

    monkeypatch.setattr(artifact_store, "_store",
                        artifact_store.LocalFilesystemArtifactStore(str(tmp_path / "artifacts")))
    # The engine boundary only: the bytes WeasyPrint would be given, returned as they are.
    monkeypatch.setattr(pdf_engine, "render_pdf", lambda source, **_kw: source.encode("utf-8"))
    return tmp_path / "artifacts"


@pytest.fixture
async def committed(db_required):
    """A session factory on its own engine (one connection per session) and a committed synthetic delivery:
    #1 CLEAN B1, #2 HELD, #3 CLEAN B4, #4 CLEAN B3. Everything created is deleted afterwards."""
    from app.core.database import _normalize_url

    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)

    def session():
        return AsyncSession(engine, expire_on_commit=False)

    arc = f"9.98.{uuid.uuid4().int % 9000 + 1000}.{uuid.uuid4().int % 9000 + 1000}"
    entities = []
    async with session() as db:
        intake_id, _ = await seed_intake(db, make_rows(4, arc=arc), with_job=False)
        db.add(m.RceIngestionRun(
            id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="test", rule_config_hash="0" * 64,
            started_at=datetime(2026, 11, 7, 8), completed_at=datetime(2026, 11, 8, 9), run_status="COMPLETE",
            assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"}))
        recs = {r.line_number - 1: r for r in (await db.execute(select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
        for i, (status, bucket) in {1: ("CLEAN", "B1"), 2: ("HELD", None), 3: ("CLEAN", "B4"),
                                    4: ("CLEAN", "B3")}.items():
            ent = None
            if bucket:
                ent = await seed_entity(db, oid=f"{SYN}-CONC-{uuid.uuid4().hex[:12]}", name=f"{SYN} concurrent {i}")
                entities.append(ent)
            db.add(m.RceCuratedRecord(
                id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=recs[i].id, record_status=status,
                status_reason=None if bucket else "blocking identity issue", rce_org_oid=recs[i].source_rce_id,
                transformation_version="test", canonical_entity_id=ent))
            if bucket:
                db.add(reg.ReviewRecord(
                    id=uuid.uuid4(), review_id=f"REV-C{uuid.uuid4().hex[:6]}-{uuid.uuid4().int % 10**6:06d}",
                    entity_id=ent, source_record_id=recs[i].id, classification_bucket=bucket,
                    classification_rule="RULE-001", classification_rule_version=2, created_at=T_OLD))
        await db.commit()
    record_ids = [r.id for r in recs.values()]
    try:
        yield SimpleNamespace(session=session, intake_id=intake_id, held_record_id=recs[2].id, entities=entities)
    finally:
        async with session() as db:
            for stmt in (
                    delete(ReportExportJob).where(ReportExportJob.source_intake_id == intake_id),
                    delete(ReportArtifact).where(ReportArtifact.review_cycle_id == str(intake_id)),
                    delete(reg.ReviewRecord).where(reg.ReviewRecord.source_record_id.in_(record_ids)),
                    delete(m.RceCuratedRecord).where(m.RceCuratedRecord.source_intake_id == intake_id),
                    delete(m.RceIngestionRun).where(m.RceIngestionRun.source_intake_id == intake_id),
                    delete(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id),
                    delete(m.RceSourceIntake).where(m.RceSourceIntake.id == intake_id),
                    delete(reg.TefcaEntityIdentifier).where(reg.TefcaEntityIdentifier.entity_id.in_(entities)),
                    delete(reg.TefcaRegEntity).where(reg.TefcaRegEntity.id.in_(entities))):
                try:
                    await db.execute(stmt)
                    await db.commit()
                except Exception:  # noqa: BLE001 - teardown is best effort; the rows are synthetic and labelled
                    await db.rollback()
        await engine.dispose()


async def _request(env, fmt, **kw):
    async with env.session() as db:                    # its own connection and transaction
        return await report_routes.export_record_review(
            report_routes.RecordReviewExportRequest(intake_id=str(env.intake_id), evidence_cutoff=CUTOFF_ISO,
                                                    format=fmt, **kw), db=db, user=QA)


async def _run(env, job_id):
    async with env.session() as db:                    # its own connection and transaction
        job = await db.get(ReportExportJob, uuid.UUID(str(job_id)))
        return await run_record_review_export_job(db, job)


async def _release_hold(env):
    async with env.session() as db:
        held = (await db.execute(select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_record_id == env.held_record_id))).scalar_one()
        held.record_status, held.status_reason = "CLEAN", None
        await db.commit()


async def _jobs(env):
    async with env.session() as db:
        return list((await db.execute(select(ReportExportJob).where(
            ReportExportJob.source_intake_id == env.intake_id).order_by(ReportExportJob.created_at))).scalars().all())


async def _datasets(env):
    async with env.session() as db:
        return list((await db.execute(select(ReportArtifact).where(
            ReportArtifact.review_cycle_id == str(env.intake_id),
            ReportArtifact.content_type == freeze.DATASET_CONTENT_TYPE))).scalars().all())


async def _rendered(env, job):
    fmt = job.request_parameters["format"]
    async with env.session() as db:
        got = await artifact_registry.retrieve_artifact(
            db, job.report_id, content_type=RECORD_REVIEW_CONTENT_TYPES[fmt])
    return fmt, got["content"], got["artifact"]


def _totals(fmt, content):
    """(bucket totals, HELD count, the dataset hash the file itself prints) read back from one rendering."""
    if fmt == "csv":
        lines = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        rows, text = lines[2:], lines[0][0]
    elif fmt == "xlsx":
        wb = openpyxl.load_workbook(io.BytesIO(content))
        ws = wb["Records"]
        hdr = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1) if r and r[0] == "Source_Line")
        rows = [["" if v is None else str(v) for v in r] for r in ws.iter_rows(min_row=hdr + 1, values_only=True)]
        text = str(ws.cell(row=1, column=1).value)
    else:                                              # the PDF's source: read its own Summary table
        text = html_lib.unescape(content.decode("utf-8"))
        summary = dict(re.findall(r"<tr><th scope='row'>([^<]+)</th><td class='num'>([^<]*)</td></tr>",
                                  content.decode("utf-8")))
        buckets = {b: int(summary[b]) for b in ("B1", "B2", "B3", "B4")}
        held = int(summary["of which HELD (counted once, inside B4)"])
        return buckets, held, re.search(r"SHA-256 ([0-9a-f]{64})", text).group(1)
    buckets = {b: 0 for b in ("B1", "B2", "B3", "B4")}
    for r in rows:
        if r[HDR.index("Bucket")] in buckets:
            buckets[r[HDR.index("Bucket")]] += 1
    held = sum(1 for r in rows if r[HDR.index("Status")] == "HELD")
    return buckets, held, re.search(r"SHA-256 ([0-9a-f]{64})", text).group(1)


async def test_concurrent_first_requests_on_separate_connections_name_one_freeze(committed):
    env = committed
    outs = await asyncio.gather(*[_request(env, fmt) for fmt in ("csv", "xlsx", "pdf", "csv", "xlsx")])
    assert len({o["dataset_job_id"] for o in outs}) == 1, [o["dataset_job_id"] for o in outs]
    jobs = await _jobs(env)
    assert len(jobs) == 3 and {j.request_parameters["format"] for j in jobs} == set(FORMATS)   # duplicates reused
    assert sorted(o["reused_existing_job"] for o in outs) == [False, False, False, True, True]
    freeze_id = outs[0]["dataset_job_id"]
    assert freeze_id in {str(j.id) for j in jobs}                    # the freeze is one of the jobs: the opener
    assert all(j.request_parameters["dataset_job_id"] == freeze_id for j in jobs)
    assert [o["dataset_reused"] for o in outs].count(False) == 1     # exactly one request opened it
    assert await _datasets(env) == []                                # nothing is frozen by a request


async def test_concurrent_runners_register_exactly_one_dataset_and_render_identical_totals(committed, monkeypatch):
    env = committed
    outs = await asyncio.gather(*[_request(env, fmt) for fmt in FORMATS])
    # Every runner must find nothing registered and load for itself before any of them registers: each load
    # waits until all three have started. This is the first-writer-wins race, not a lucky ordering.
    real_load = ld.load_record_facts
    started, all_started = [], asyncio.Event()

    async def gated_load(db, **kw):
        started.append(1)
        if len(started) >= len(FORMATS):
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=30)
        return await real_load(db, **kw)

    monkeypatch.setattr(ld, "load_record_facts", gated_load)
    results = await asyncio.gather(*[_run(env, o["job_id"]) for o in outs], _release_hold(env))
    assert results[:3] == [ReportExportJob.STATE_SUCCEEDED] * 3
    assert len(started) == 3                                         # all three loaded; only one load was kept

    datasets = await _datasets(env)
    assert len(datasets) == 1                                        # exactly one dataset artifact
    sha = datasets[0].rendered_sha256
    jobs = await _jobs(env)
    assert len(jobs) == 3 and all(j.state == ReportExportJob.STATE_SUCCEEDED for j in jobs)
    assert {j.request_parameters["dataset_job_id"] for j in jobs} == {outs[0]["dataset_job_id"]}
    assert {j.request_parameters["dataset_sha256"] for j in jobs} == {sha}
    assert datasets[0].report_id == freeze.dataset_report_id(outs[0]["dataset_job_id"])
    assert len({j.report_id for j in jobs}) == 3
    assert all(j.report_id == f"RECORD-REVIEW-{str(j.id).upper()}" for j in jobs)

    read = {}
    for job in jobs:
        fmt, content, artifact = await _rendered(env, job)
        assert artifact["report_data_hash"] == sha
        read[fmt] = _totals(fmt, content)
    assert set(read) == set(FORMATS)
    assert read["csv"] == read["xlsx"] == read["pdf"], read          # totals, HELD count and printed hash
    assert read["csv"][2] == sha
    # whichever load won: record 2 is either still HELD (inside B4) or released and unclassified - in ALL formats
    assert sum(read["csv"][0].values()) == (4 if read["csv"][1] == 1 else 3)

    # a format requested afterwards joins the same freeze and reads the same totals, hold released or not
    later = await _request(env, "csv", new_freeze=False)
    assert later["dataset_reused"] is True and later["dataset_state"] == "frozen" and later["dataset_sha256"] == sha


async def test_repeated_concurrent_export_rounds_never_register_a_second_dataset_for_a_freeze(committed):
    """No gate here: whatever interleaving the event loop and the database produce, three rounds in a row."""
    env = committed
    seen = set()
    for _round in range(3):
        outs = await asyncio.gather(*[_request(env, fmt, new_freeze=(fmt == "csv")) for fmt in FORMATS])
        states = await asyncio.gather(*[_run(env, o["job_id"]) for o in outs])
        assert states == [ReportExportJob.STATE_SUCCEEDED] * 3
        for o in outs:
            seen.add(o["dataset_job_id"])
    datasets = await _datasets(env)
    by_id = {}
    for d in datasets:
        by_id.setdefault(d.report_id, []).append(d.rendered_sha256)
    assert all(len(v) == 1 for v in by_id.values()), by_id           # never two datasets under one freeze id
    assert set(by_id) <= {freeze.dataset_report_id(f) for f in seen}
    jobs = await _jobs(env)
    for d_id, (sha,) in by_id.items():
        named = [j for j in jobs if freeze.dataset_report_id(j.request_parameters["dataset_job_id"]) == d_id]
        assert named and {j.request_parameters["dataset_sha256"] for j in named} == {sha}
