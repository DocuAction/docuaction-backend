"""Durability proofs for the IQVIA chunked-upload / import-job mechanism
(2026-10-03): state survives a restart, a worker death is a bounded retry
not a stuck job, concurrent claimers never double-run the same import, and
approval stays refused until the RESUMED run actually completes.

Entirely synthetic data; no real IQVIA content anywhere.
"""
from __future__ import annotations

import asyncio
import csv
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN  # noqa: F401

pytestmark = pytest.mark.regression

_created_snapshot_ids: list = []
_created_upload_ids: list = []


@pytest.fixture(autouse=True)
def _iqvia_drop_dir_is_tmp_path(tmp_path, monkeypatch):
    """The stage route confines a client-supplied file name to
    settings.IQVIA_IMPORT_DIR (upload_security.safe_existing_path, added for
    the CodeQL path-injection fix). These tests stage files they write under
    pytest's tmp_path, so the drop directory is pointed there for the test --
    the confinement itself stays fully in force. Added 2026-10-04: without it
    every stage call here returned 422, and because these tests need a
    database they never ran in the no-DB `pytest` CI job, so nothing reported
    it."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "IQVIA_IMPORT_DIR", str(tmp_path))


@pytest.fixture
async def db(db_required):
    """Real, committed session -- `run_import_job` opens its own session via
    `async_session_maker()`, the same way the live scheduler does, so a
    savepoint-only fixture would be invisible to it from outside its own
    connection."""
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
        await cleanup.execute(delete(sm.IqviaHcoObservation).where(
            sm.IqviaHcoObservation.source_snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()])))
        # `approve_snapshot` is append-only: approval creates a NEW row with
        # `supersedes_snapshot_id` pointing at the original PENDING row, so
        # the original cannot be deleted while that successor still
        # references it (RESTRICT) -- delete both in one statement, same as
        # `test_iqvia_routes.py`'s fixture.
        # Teardown of synthetic rows only. source_snapshot is guarded by trg_source_snapshot_guard
        # (migration 20261006_snapshot_bookkeeping), which refuses to delete a non-PENDING snapshot; the
        # approved successor row this test created is exactly that. `replica` skips ordinary triggers for
        # THIS cleanup transaction only (SET LOCAL; superuser-only, which the test role is). It is not a
        # production path - the application and the runtime role have no such privilege.
        await cleanup.execute(text("SET LOCAL session_replication_role = replica"))
        await cleanup.execute(delete(sm.SourceSnapshot).where(
            (sm.SourceSnapshot.id.in_(_created_snapshot_ids or [uuid.uuid4()])) |
            (sm.SourceSnapshot.supersedes_snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()]))))
        await cleanup.commit()
    _created_snapshot_ids.clear()
    _created_upload_ids.clear()


class _User:
    def __init__(self, role, email=None):
        self.role = role
        self.email = email or f"iqvia-durability-{role}@synthetic-test.docuaction.invalid"
        self.id = None


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


HCO_HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]


class TestUploadStateSurvivesANewSession:
    """(a) GET /uploads/{id} correctly reports missing chunks from the DB
    after a 'restart' -- modeled as a brand new, independent session with no
    shared in-process state with the one that uploaded the first chunk
    (there is no `_UPLOAD_STATE` any more for a restart to lose)."""

    async def test_resume_reads_correct_missing_chunks_from_a_fresh_session(
            self, db, monkeypatch):
        from app.core.database import async_session_maker

        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        payload = b"A" * 10 + b"B" * 10 + b"C" * 10
        start = await routes.start_upload(
            routes.StartUploadRequest(source="hco", label=f"{SYN}-restart-resume",
                                      total_size=len(payload), chunk_size=10),
            db, user=_User("reviewer"))
        upload_id = uuid.UUID(start["upload_id"])
        _created_upload_ids.append(upload_id)

        class _Req:
            def __init__(self, b): self._b = b
            async def body(self): return self._b

        await routes.upload_chunk(upload_id, 0, _Req(b"A" * 10), db, user=_User("reviewer"))
        await db.commit()

        # A brand new session, standing in for a different (post-restart)
        # worker process -- shares nothing with `db` except the database
        # itself.
        async with async_session_maker() as fresh:
            status = await routes.upload_status(upload_id, fresh, user=_User("reviewer"))
            assert status["missing_chunk_indices"] == [1, 2]
            assert status["received_chunks"] == 1

            # Resume: upload exactly the reported-missing chunks, on the
            # fresh session, and complete.
            await routes.upload_chunk(upload_id, 1, _Req(b"B" * 10), fresh, user=_User("reviewer"))
            await routes.upload_chunk(upload_id, 2, _Req(b"C" * 10), fresh, user=_User("reviewer"))
            status2 = await routes.upload_status(upload_id, fresh, user=_User("reviewer"))
            assert status2["complete"] is True

            result = await routes.complete_upload(upload_id, fresh, user=_User("reviewer"))
            _created_snapshot_ids.append(uuid.UUID(result["snapshot_id"]))
            await fresh.commit()


class TestWorkerDeathIsABoundedRetryNotADuplicate:
    """(b)+(c): a crash mid-import produces no duplicate staged rows on
    resume, and approval stays refused (409) until the RESUMED run actually
    finishes -- not merely until the first attempt stopped."""

    async def test_crash_then_resume_stages_each_row_exactly_once_and_blocks_approval(
            self, db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        n = 6
        f = write_csv(tmp_path / "hco.csv", HCO_HEADER, [
            [f"{SYN}-CRASH-HCO-{i}", "", "", f"{SYN} Crash Org {i}", "02101"]
            for i in range(n)
        ])

        # Make the FIRST staging call raise, simulating the worker dying
        # mid-import before it ever commits a row for this snapshot. The
        # real `_stage_rows` runs normally on every later call -- this is
        # exactly "attempt 1 crashed with zero effect, attempt 2 resumes".
        real_stage_rows = ii._stage_rows
        calls = {"n": 0}

        async def flaky_stage_rows(db_, *, snapshot_id, model, rows):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated worker crash mid-import")
            return await real_stage_rows(db_, snapshot_id=snapshot_id, model=model, rows=rows)

        monkeypatch.setattr(ii, "_stage_rows", flaky_stage_rows)

        # `register_snapshot`'s uniqueness is (source_system, sha256,
        # label) -- a label unique to this run (not just this test's fixed
        # content, which would otherwise collide if a leftover row from
        # an earlier interrupted run was not cleaned up) keeps this test
        # independent of exactly when/whether a prior run's teardown ran.
        body = routes.StageRequest(file_path=str(f),
                                   label=f"{SYN}-crash-resume-{uuid.uuid4().hex[:8]}")
        result = await routes.stage_source("hco", body, db, user=_User("reviewer"))
        snapshot_id = uuid.UUID(result["snapshot_id"])
        _created_snapshot_ids.append(snapshot_id)

        # Attempt 1: claimed, runs, crashes -> requeued (not FAILED yet --
        # one failure is well under MAX_ATTEMPTS).
        job = await jobs.claim_next_queued(db)
        assert job is not None
        await routes.run_import_job(str(job.id))
        await db.refresh(job)
        assert job.state == um.IqviaImportJob.STATE_QUEUED
        assert job.attempt_count == 1

        # Zero rows should have been staged by the crashed attempt.
        staged_after_crash = (await db.execute(
            select(func.count()).select_from(sm.IqviaHcoObservation)
            .where(sm.IqviaHcoObservation.source_snapshot_id == snapshot_id))).scalar()
        assert staged_after_crash == 0

        # Approval must still be refused -- the snapshot is still "staging",
        # not "completed", regardless of how many attempts have run.
        approve_body = routes.ApproveRequest(approval_ref=f"{SYN}-ref")
        with pytest.raises(HTTPException) as exc:
            await routes.approve(snapshot_id, approve_body, db, user=_User("qalead"))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "IMPORT_NOT_COMPLETE"

        # Attempt 2: claimed again (the SAME job, requeued, not a new one),
        # runs for real this time, succeeds.
        job2 = await jobs.claim_next_queued(db)
        assert job2 is not None
        assert job2.id == job.id, "the retry must reuse the same job row, not create a second one"
        await routes.run_import_job(str(job2.id))
        await db.refresh(job2)
        assert job2.state == um.IqviaImportJob.STATE_SUCCEEDED
        assert job2.attempt_count == 2

        # Exactly N rows staged -- the crashed attempt's zero effect plus
        # the resumed attempt's real effect, never double-counted.
        staged_final = (await db.execute(
            select(func.count()).select_from(sm.IqviaHcoObservation)
            .where(sm.IqviaHcoObservation.source_snapshot_id == snapshot_id))).scalar()
        assert staged_final == n

        # `run_import_job` committed the completed status on its OWN session
        # (the real scheduler's session, standing in for a separate worker
        # process) -- this test's `db` session still holds the snapshot
        # object it loaded earlier at `stage_source` time. `expire_on_commit`
        # is False on this app's session factory (required for async lazy
        # attributes), so without this the test would read its OWN stale
        # in-memory copy, not what a real second request would see. A real
        # live server never hits this: `Depends(get_db)` hands each HTTP
        # request a brand new session.
        db.expire_all()

        # Approval now succeeds.
        approved = await routes.approve(snapshot_id, approve_body, db, user=_User("qalead"))
        assert approved["status"] == sm.SNAPSHOT_APPROVED


class TestBoundedRetries:
    """A job that keeps failing stops being retried automatically once
    `MAX_ATTEMPTS` is exhausted -- FAILED for a human, never retried
    forever."""

    async def test_exhausting_max_attempts_ends_in_failed_not_infinite_retry(
            self, db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        f = write_csv(tmp_path / "hco.csv", HCO_HEADER,
                     [[f"{SYN}-ALWAYSFAIL-HCO-0", "", "", f"{SYN} Org", "02101"]])

        async def always_raises(*args, **kwargs):
            raise RuntimeError("simulated permanent failure")

        monkeypatch.setitem(routes._IMPORTER, "hco", always_raises)

        body = routes.StageRequest(file_path=str(f), label=f"{SYN}-always-fails")
        result = await routes.stage_source("hco", body, db, user=_User("reviewer"))
        snapshot_id = uuid.UUID(result["snapshot_id"])
        _created_snapshot_ids.append(snapshot_id)

        final_state = None
        for _ in range(um.IqviaImportJob.MAX_ATTEMPTS):
            job = await jobs.claim_next_queued(db)
            assert job is not None, "a FAILED job must stop being claimable"
            await routes.run_import_job(str(job.id))
            await db.refresh(job)
            final_state = job.state

        assert final_state == um.IqviaImportJob.STATE_FAILED
        assert job.attempt_count == um.IqviaImportJob.MAX_ATTEMPTS

        # FAILED jobs are never claimed again.
        nothing_left = await jobs.claim_next_queued(db)
        assert nothing_left is None

        snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
        assert (snapshot.metadata_ or {}).get("import_status") == "failed"


class TestConcurrentClaimIsSafe:
    """Two workers racing to claim the same QUEUED job must never both run
    it -- `FOR UPDATE SKIP LOCKED` plus the partial unique index on
    (snapshot_id) WHERE active_marker IS TRUE is the database guarantee,
    not application-level locking."""

    async def test_two_concurrent_claimers_only_one_wins(self, db, tmp_path, monkeypatch):
        from app.core.database import async_session_maker

        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        f = write_csv(tmp_path / "hco.csv", HCO_HEADER,
                     [[f"{SYN}-RACE-HCO-0", "", "", f"{SYN} Org", "02101"]])
        body = routes.StageRequest(file_path=str(f), label=f"{SYN}-race")
        result = await routes.stage_source("hco", body, db, user=_User("reviewer"))
        snapshot_id = uuid.UUID(result["snapshot_id"])
        _created_snapshot_ids.append(snapshot_id)
        await db.commit()

        async def claim_on_own_session():
            async with async_session_maker() as s:
                return await jobs.claim_next_queued(s)

        winner_a, winner_b = await asyncio.gather(
            claim_on_own_session(), claim_on_own_session())
        claimed = [j for j in (winner_a, winner_b) if j is not None]
        assert len(claimed) == 1, "exactly one concurrent claimer must win the only QUEUED job"


class TestReaperRequeuesOrFails:
    async def test_reaper_requeues_a_job_with_attempts_remaining(self, db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        f = write_csv(tmp_path / "hco.csv", HCO_HEADER,
                     [[f"{SYN}-REAP-HCO-0", "", "", f"{SYN} Org", "02101"]])
        body = routes.StageRequest(file_path=str(f), label=f"{SYN}-reap-requeue")
        result = await routes.stage_source("hco", body, db, user=_User("reviewer"))
        snapshot_id = uuid.UUID(result["snapshot_id"])
        _created_snapshot_ids.append(snapshot_id)

        job = await jobs.claim_next_queued(db)  # QUEUED -> RUNNING, attempt_count=1
        job.heartbeat_at = datetime.utcnow() - timedelta(seconds=jobs.STALE_HEARTBEAT_SECONDS + 1)
        await db.commit()

        reaped = await jobs.reap_stale_jobs(db)
        assert len(reaped) == 1
        await db.refresh(job)
        assert job.state == um.IqviaImportJob.STATE_QUEUED, (
            "attempt_count (1) is well under MAX_ATTEMPTS -- a stale RUNNING job must "
            "be requeued, not failed outright")

    async def test_reaper_fails_a_job_that_has_exhausted_attempts(self, db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        f = write_csv(tmp_path / "hco.csv", HCO_HEADER,
                     [[f"{SYN}-REAP2-HCO-0", "", "", f"{SYN} Org", "02101"]])
        body = routes.StageRequest(file_path=str(f), label=f"{SYN}-reap-fail")
        result = await routes.stage_source("hco", body, db, user=_User("reviewer"))
        snapshot_id = uuid.UUID(result["snapshot_id"])
        _created_snapshot_ids.append(snapshot_id)

        job = await jobs.claim_next_queued(db)
        job.attempt_count = um.IqviaImportJob.MAX_ATTEMPTS
        job.heartbeat_at = datetime.utcnow() - timedelta(seconds=jobs.STALE_HEARTBEAT_SECONDS + 1)
        await db.commit()

        reaped = await jobs.reap_stale_jobs(db)
        assert len(reaped) == 1
        await db.refresh(job)
        assert job.state == um.IqviaImportJob.STATE_FAILED
        assert job.active_marker is None
