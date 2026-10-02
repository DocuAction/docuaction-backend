"""IQVIA application journey routes: stage -> status -> approve -> match,
and the durable chunked-upload / import-job mechanism (2026-10-03).

Calls the route handler functions directly (not through TestClient/HTTP) --
they are plain `async def`s that take their dependencies as ordinary
parameters, so this exercises the real logic without needing a running
server. Entirely synthetic data; no real IQVIA content anywhere.

Since the durable design moved all upload/job bookkeeping into the database
(no more `_UPLOAD_STATE`), "run the background import" is now modeled as the
scheduler's own poll tick would do it: `jobs.claim_next_queued(db)` then
`routes.run_import_job(str(job.id))` -- see `test_iqvia_import_durability.py`
for the restart/retry/concurrent-claim proofs specifically.
"""
from __future__ import annotations

import csv
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN  # noqa: F401

pytestmark = pytest.mark.regression


#: Ids created by the current test, cleaned up by the `db` fixture after
#: each test (respecting FK order -- jobs and upload sessions reference
#: source_snapshot with ON DELETE RESTRICT, so they go first).
_created_snapshot_ids: list = []
_created_upload_ids: list = []


@pytest.fixture
async def db(db_required):
    """A REAL, committed session -- not the savepoint-isolated
    `rolled_back_db`. Required here specifically: the durable import job
    runner (`routes.run_import_job`, the same way the real scheduler/live
    server calls it) opens its OWN session via `async_session_maker()`,
    which a savepoint-only transaction is invisible to from outside its own
    connection. Cleaned up manually, by id, rather than relying on
    rollback."""
    from app.core.database import async_session_maker

    _created_snapshot_ids.clear()
    _created_upload_ids.clear()
    async with async_session_maker() as session:
        yield session
    async with async_session_maker() as cleanup:
        await cleanup.execute(delete(um.IqviaImportJob).where(
            um.IqviaImportJob.snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()])))
        await cleanup.execute(delete(um.IqviaUploadSession).where(
            um.IqviaUploadSession.id.in_(_created_upload_ids or [uuid.uuid4()])))
        await cleanup.execute(delete(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()])))
        await cleanup.execute(delete(sm.IqviaHcoObservation).where(
            sm.IqviaHcoObservation.source_snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()])))
        await cleanup.execute(delete(sm.SourceSnapshot).where(
            (sm.SourceSnapshot.id.in_(_created_snapshot_ids or [uuid.uuid4()])) |
            (sm.SourceSnapshot.supersedes_snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()]))))
        await cleanup.commit()
    _created_snapshot_ids.clear()
    _created_upload_ids.clear()


class _User:
    def __init__(self, role, email=None):
        # Role-specific by default so a maker/checker check (the registrant
        # cannot approve their own snapshot) is exercised correctly rather
        # than accidentally tripped by two test users sharing one email.
        self.role = role
        self.email = email or f"iqvia-route-test-{role}@synthetic-test.docuaction.invalid"
        self.id = None


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


HCO_HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]


async def _run_one_queued_job(db) -> str:
    """Exactly what the scheduler's poll tick does: claim the oldest QUEUED
    job, then run it to completion. Returns the job's final state."""
    job = await jobs.claim_next_queued(db)
    assert job is not None, "expected a QUEUED job to claim"
    await routes.run_import_job(str(job.id))
    await db.refresh(job)
    return job.state


async def _stage_and_complete(db, tmp_path, monkeypatch, *, n=3):
    """Stage, then run the durable job exactly as the scheduler would."""
    monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
    f = write_csv(tmp_path / "hco.csv", HCO_HEADER, [
        [f"{SYN}-ROUTE-HCO-{i}", "", "", f"{SYN} Route Org {i}", "02101"] for i in range(n)
    ])
    body = routes.StageRequest(file_path=str(f), label=f"{SYN}-route-stage")
    reviewer = _User("reviewer")
    result = await routes.stage_source("hco", body, db, user=reviewer)
    _created_snapshot_ids.append(uuid.UUID(result["snapshot_id"]))
    state = await _run_one_queued_job(db)
    assert state == um.IqviaImportJob.STATE_SUCCEEDED
    return result["snapshot_id"]


class TestAccessGating:
    async def test_flag_off_is_refused(self, db, tmp_path, monkeypatch):
        monkeypatch.delenv("ENABLE_IQVIA_SOURCES", raising=False)
        f = write_csv(tmp_path / "x.csv", HCO_HEADER, [[f"{SYN}-X", "", "", "X", "02101"]])
        body = routes.StageRequest(file_path=str(f), label="x")
        with pytest.raises(HTTPException) as exc:
            await routes.stage_source("hco", body, db, user=_User("reviewer"))
        assert exc.value.status_code == 403

    async def test_below_reviewer_floor_is_refused(self, db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        f = write_csv(tmp_path / "x.csv", HCO_HEADER, [[f"{SYN}-X", "", "", "X", "02101"]])
        body = routes.StageRequest(file_path=str(f), label="x")
        with pytest.raises(HTTPException) as exc:
            await routes.stage_source("hco", body, db, user=_User("contributor"))
        assert exc.value.status_code == 403

    async def test_unknown_source_is_422(self, db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        f = write_csv(tmp_path / "x.csv", HCO_HEADER, [[f"{SYN}-X", "", "", "X", "02101"]])
        body = routes.StageRequest(file_path=str(f), label="x")
        with pytest.raises(HTTPException) as exc:
            await routes.stage_source("bogus", body, db, user=_User("reviewer"))
        assert exc.value.status_code == 422

    async def test_missing_file_is_422(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        body = routes.StageRequest(file_path="C:/does/not/exist.csv", label="x")
        with pytest.raises(HTTPException) as exc:
            await routes.stage_source("hco", body, db, user=_User("reviewer"))
        assert exc.value.status_code == 422


class TestStageStatusApprove:
    async def test_stage_then_status_shows_completed_and_reconciles(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=3)
        status = await routes.snapshot_status(uuid.UUID(snapshot_id), db, user=_User("reviewer"))
        assert status["status"] == sm.SNAPSHOT_PENDING
        assert status["import_status"] == "completed"
        assert status["import_summary"]["rows_staged"] == 3
        assert status["reconciliation"]["reconciles"] is True
        assert status["job"]["state"] == um.IqviaImportJob.STATE_SUCCEEDED

    async def test_approve_refused_before_import_completes(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        from app.tefca_registry.rce import source_matching as sx
        snapshot = await sx.register_snapshot(
            db=db, source_system=sm.SOURCE_IQVIA_HCO, label=f"{SYN}-incomplete",
            sha256="0" * 64, record_count=10,
            received_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc),
            created_by=SYN)
        _created_snapshot_ids.append(snapshot.id)
        body = routes.ApproveRequest(approval_ref=f"{SYN}-ref")
        with pytest.raises(HTTPException) as exc:
            await routes.approve(snapshot.id, body, db, user=_User("qalead"))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "IMPORT_NOT_COMPLETE"

    async def test_approve_succeeds_after_completion_and_reconciliation(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        body = routes.ApproveRequest(approval_ref=f"{SYN}-ref")
        approved = await routes.approve(uuid.UUID(snapshot_id), body, db, user=_User("qalead"))
        assert approved["status"] == sm.SNAPSHOT_APPROVED


class TestMatchRoute:
    async def test_match_hco_runs(self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        body = routes.ApproveRequest(approval_ref=f"{SYN}-ref")
        approved = await routes.approve(uuid.UUID(snapshot_id), body, db, user=_User("qalead"))
        result = await routes.match_snapshot(uuid.UUID(approved["snapshot_id"]), db,
                                             user=_User("reviewer"))
        assert result["observations_considered"] == 2

    async def test_match_hcp_explicitly_refused_as_unavailable(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        from app.tefca_registry.rce import source_matching as sx
        snapshot = await sx.register_snapshot(
            db=db, source_system=sm.SOURCE_IQVIA_HCP, label=f"{SYN}-hcp",
            sha256="1" * 64, record_count=0,
            received_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc), created_by=SYN)
        _created_snapshot_ids.append(snapshot.id)
        with pytest.raises(HTTPException) as exc:
            await routes.match_snapshot(snapshot.id, db, user=_User("reviewer"))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "AFFILIATION_DATA_UNAVAILABLE"

    async def test_match_affiliation_explicitly_refused_as_unavailable(
            self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        from app.tefca_registry.rce import source_matching as sx
        snapshot = await sx.register_snapshot(
            db=db, source_system=sm.SOURCE_IQVIA_AFFILIATION, label=f"{SYN}-affil",
            sha256="2" * 64, record_count=0,
            received_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc), created_by=SYN)
        _created_snapshot_ids.append(snapshot.id)
        with pytest.raises(HTTPException) as exc:
            await routes.match_snapshot(snapshot.id, db, user=_User("reviewer"))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "AFFILIATION_DATA_UNAVAILABLE"


class _FakeRequest:
    """Satisfies `await request.body()` without a real ASGI request --
    `upload_chunk` reads nothing else off the Request object."""
    def __init__(self, body: bytes):
        self._body = body

    async def body(self) -> bytes:
        return self._body


class TestChunkedUpload:
    """The chunked upload mechanism, now durable: every assertion below
    reads its state back from the DATABASE (`jobs.*`), never from an
    in-process dict -- there is no `_UPLOAD_STATE` any more."""

    async def test_full_upload_then_complete_stages_the_file(
            self, db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        content = write_csv(tmp_path / "source.csv", HCO_HEADER, [
            [f"{SYN}-UP-HCO-{i}", "", "", f"{SYN} Upload Org {i}", "02101"] for i in range(5)
        ]).read_bytes()
        chunk_size = 37  # deliberately small/odd, to force several chunks
        reviewer = _User("reviewer")
        start = await routes.start_upload(
            routes.StartUploadRequest(source="hco", label=f"{SYN}-chunked",
                                      total_size=len(content), chunk_size=chunk_size),
            db, user=reviewer)
        upload_id = uuid.UUID(start["upload_id"])
        _created_upload_ids.append(upload_id)
        assert start["total_chunks"] == -(-len(content) // chunk_size)

        for i in range(start["total_chunks"]):
            chunk = content[i * chunk_size:(i + 1) * chunk_size]
            await routes.upload_chunk(upload_id, i, _FakeRequest(chunk), db, user=reviewer)

        status = await routes.upload_status(upload_id, db, user=reviewer)
        assert status["complete"] is True
        assert status["missing_chunk_count"] == 0

        result = await routes.complete_upload(upload_id, db, user=reviewer)
        _created_snapshot_ids.append(uuid.UUID(result["snapshot_id"]))
        assert result["job_state"] == um.IqviaImportJob.STATE_QUEUED

        state = await _run_one_queued_job(db)
        assert state == um.IqviaImportJob.STATE_SUCCEEDED

        session = await jobs.get_upload_session(db, upload_id)
        assert session.status == um.IqviaUploadSession.STATUS_STAGED

        final_status = await routes.snapshot_status(uuid.UUID(result["snapshot_id"]), db,
                                                     user=reviewer)
        assert final_status["import_status"] == "completed"
        assert final_status["import_summary"]["rows_staged"] == 5

    async def test_retrying_a_chunk_overwrites_not_duplicates(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        payload = b"A" * 10 + b"B" * 10
        start = await routes.start_upload(
            routes.StartUploadRequest(source="hco", label=f"{SYN}-retry-chunk",
                                      total_size=len(payload), chunk_size=10),
            db, user=_User("reviewer"))
        upload_id = uuid.UUID(start["upload_id"])
        _created_upload_ids.append(upload_id)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(b"X" * 10), db, user=_User("reviewer"))
        # Retry chunk 0 with the CORRECT bytes -- must overwrite, not append.
        await routes.upload_chunk(upload_id, 0, _FakeRequest(b"A" * 10), db, user=_User("reviewer"))
        await routes.upload_chunk(upload_id, 1, _FakeRequest(b"B" * 10), db, user=_User("reviewer"))
        session = await jobs.get_upload_session(db, upload_id)
        assembled = Path(session.temp_path).read_bytes()
        assert assembled == payload, "a retried chunk must overwrite its own offset exactly"
        # The retry must not be double-counted as two received chunks.
        received = await jobs.received_chunk_indices(db, upload_id)
        assert received == {0, 1}

    async def test_complete_refused_while_chunks_missing(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        start = await routes.start_upload(
            routes.StartUploadRequest(source="hco", label=f"{SYN}-incomplete-chunks",
                                      total_size=100, chunk_size=10),
            db, user=_User("reviewer"))
        upload_id = uuid.UUID(start["upload_id"])
        _created_upload_ids.append(upload_id)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(b"0" * 10), db, user=_User("reviewer"))
        with pytest.raises(HTTPException) as exc:
            await routes.complete_upload(upload_id, db, user=_User("reviewer"))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "UPLOAD_INCOMPLETE"

    async def test_unknown_upload_id_is_404_everywhere(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        bogus = uuid.uuid4()
        with pytest.raises(HTTPException) as exc:
            await routes.upload_status(bogus, db, user=_User("reviewer"))
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(bogus, 0, _FakeRequest(b"x"), db, user=_User("reviewer"))
        assert exc.value.status_code == 404
