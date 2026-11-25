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
from app.tefca_registry.rce import iqvia_reconciliation as rec
from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN  # noqa: F401

# 2026-11-25: these files predate intake contracts; see the fixture in conftest.py.
pytestmark = [pytest.mark.regression, pytest.mark.usefixtures("iqvia_narrow_test_layouts_bind")]

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
        await cleanup.execute(delete(rec.IqviaSnapshotReconciliation).where(
            rec.IqviaSnapshotReconciliation.snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()])))
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


def _iqvia_sources(monkeypatch, on: bool) -> None:
    """Set the IQVIA programme flag the way the gate actually reads it.

    ENABLE_IQVIA_SOURCES is a DECLARED settings field (app/core/config.py),
    so `licensed_source_enabled()`'s `getattr(settings, ...)` never falls
    through to os.environ once the process has started -- a bare
    monkeypatch.setenv is shadowed by the field and every route call 403s.
    Patch the settings attribute itself (the same mechanism
    test_iqvia_advisory_routes_2026_11_08.py already uses); monkeypatch
    restores the field at teardown.
    """
    from app.core.config import settings
    monkeypatch.setattr(settings, "ENABLE_IQVIA_SOURCES", on, raising=False)


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

        _iqvia_sources(monkeypatch, True)
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
        _iqvia_sources(monkeypatch, True)
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

        # Approval needs the exact reconciliation to have passed for the
        # RESUMED snapshot; it is queued and run the way the scheduler does it.
        from fastapi import Response
        await routes.start_reconciliation(snapshot_id, Response(), db, user=_User("reviewer"))
        claimed = await rec.claim_next(db)
        await rec.run_reconciliation(str(claimed.id))
        await db.rollback()

        # Approval now succeeds.
        approved = await routes.approve(snapshot_id, approve_body, db, user=_User("qalead"))
        assert approved["status"] == sm.SNAPSHOT_APPROVED


class TestBoundedRetries:
    """A job that keeps failing stops being retried automatically once
    `MAX_ATTEMPTS` is exhausted -- FAILED for a human, never retried
    forever."""

    async def test_exhausting_max_attempts_ends_in_failed_not_infinite_retry(
            self, db, tmp_path, monkeypatch):
        _iqvia_sources(monkeypatch, True)
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

        _iqvia_sources(monkeypatch, True)
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
        _iqvia_sources(monkeypatch, True)
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
        _iqvia_sources(monkeypatch, True)
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


class TestAByteOrderMarkedFileImportsAndResumes:
    """Fifth pass on #216 (2026-11-14). The reference preflight tolerates a
    leading UTF-8 byte-order mark (REF-SCH-010, informational, decided
    2026-10-04) and answered CLEAR, while the importer kept the mark in the
    first column's name and rejected every row. The importer now decodes the
    file as the preflight does. What is computed on the raw bytes - the
    snapshot's sha256, which is also its duplicate key - is unchanged, and a
    resumed import of a marked file stages each row exactly once."""

    BOM = b"\xef\xbb\xbf"

    def _marked(self, tmp_path, n, tag):
        import hashlib

        f = write_csv(tmp_path / f"bom-{tag}.csv", HCO_HEADER, [
            [f"{SYN}-BOM-{tag}-{uuid.uuid4().hex[:6]}-{i}", "", "", f"{SYN} Bom Org {i}", "02101"]
            for i in range(n)])
        delivered = self.BOM + f.read_bytes()
        f.write_bytes(delivered)
        return f, delivered, hashlib.sha256(delivered).hexdigest()

    async def test_every_row_is_staged_under_the_documented_column_name(self, db, tmp_path):
        n = 5
        f, delivered, sha = self._marked(tmp_path, n, "a")
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-bom-a-{uuid.uuid4().hex[:8]}",
                                          created_by=SYN)
        _created_snapshot_ids.append(summary.snapshot_id)
        assert (summary.rows_read, summary.rows_rejected, summary.rows_staged) == (n, 0, n)
        assert summary.completed and summary.file_sha256 == sha

        db.expire_all()
        snapshot = await db.get(sm.SourceSnapshot, summary.snapshot_id)
        assert snapshot.sha256 == sha, "the snapshot identity is the hash of the delivered bytes"
        assert snapshot.record_count == n
        recorded = snapshot.metadata_["reference_preflight"]
        assert recorded["gate"] == "CLEAR"
        assert "REF-SCH-010" in [x["code"] for x in recorded["findings"]]
        rows = (await db.execute(select(sm.IqviaHcoObservation).where(
            sm.IqviaHcoObservation.source_snapshot_id == summary.snapshot_id))).scalars().all()
        assert len(rows) == n
        for row in rows:
            assert row.source_record_key == row.payload["HCO_HCE_ID"]
            assert not [k for k in row.payload if k.startswith("﻿")]
        assert f.read_bytes() == delivered, "the staged file is not rewritten"

        # Asked again with the same snapshot: the same file, nothing new, nothing lost.
        again = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                        snapshot_id=summary.snapshot_id)
        assert (again.rows_read, again.rows_staged, again.rows_already_staged) == (n, 0, n)

    async def test_an_import_interrupted_midway_resumes_without_a_duplicate_or_a_gap(
            self, db, tmp_path, monkeypatch):
        n = 6
        f, _delivered, sha = self._marked(tmp_path, n, "b")
        real_stage_rows = ii._stage_rows
        calls = {"n": 0}

        async def second_chunk_fails(db_, *, snapshot_id, model, rows):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated worker crash after the first chunk")
            return await real_stage_rows(db_, snapshot_id=snapshot_id, model=model, rows=rows)

        monkeypatch.setattr(ii, "_stage_rows", second_chunk_fails)
        label = f"{SYN}-bom-b-{uuid.uuid4().hex[:8]}"
        with pytest.raises(RuntimeError):
            await ii.import_hco_csv(db, file_path=f, label=label, created_by=SYN, chunk_size=2)
        await db.rollback()
        snapshot = (await db.execute(select(sm.SourceSnapshot).where(
            sm.SourceSnapshot.snapshot_label == label))).scalar_one()
        _created_snapshot_ids.append(snapshot.id)
        assert snapshot.sha256 == sha

        async def staged():
            return (await db.execute(
                select(func.count()).select_from(sm.IqviaHcoObservation)
                .where(sm.IqviaHcoObservation.source_snapshot_id == snapshot.id))).scalar()

        assert await staged() == 2, "the first chunk was committed before the crash"

        resumed = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                          chunk_size=2, snapshot_id=snapshot.id)
        assert (resumed.rows_read, resumed.rows_rejected) == (n, 0)
        assert (resumed.rows_already_staged, resumed.rows_staged) == (2, n - 2)
        assert await staged() == n
        keys = (await db.execute(select(sm.IqviaHcoObservation.source_record_key).where(
            sm.IqviaHcoObservation.source_snapshot_id == snapshot.id))).scalars().all()
        assert len(set(keys)) == n


class TestResumeAcrossTheReaderChange:
    """Sixth pass on #216 (review finding, 2026-11-14). Before the importer
    decoded a byte-order-marked file as the preflight does, it read it as plain
    utf-8: the mark stayed in the FIRST column's name. Where the identity
    column is not first (ORG_NPI,HCO_HCE_ID,...), rows were still staged - with
    the first column missing (npi None) and a record hash of that payload. A
    resume under the current reader skips those rows on the per-snapshot key
    and stages the rest correctly: one snapshot holding rows from two readers.

    Such a resume is refused, before anything further is staged. Rows already
    staged are not rewritten or deleted, and the snapshot can never reconcile.
    A file without a mark reads identically under both readers and resumes as
    it always did."""

    BOM = b"\xef\xbb\xbf"
    #: The identity column is NOT first, so the legacy reader still staged rows.
    HEADER = ["ORG_NPI", "HCO_HCE_ID", "ORG_CCN_ID", "BUS_NM", "PHYS_ZIP"]

    def _file(self, tmp_path, n, tag, *, marked=True):
        import hashlib

        f = write_csv(tmp_path / f"reader-{tag}.csv", self.HEADER, [
            ["1234567893", f"{SYN}-RDR-{tag}-{uuid.uuid4().hex[:6]}-{i}", "", f"{SYN} Org {i}", "02101"]
            for i in range(n)])
        if marked:
            f.write_bytes(self.BOM + f.read_bytes())
        return f, hashlib.sha256(f.read_bytes()).hexdigest()

    @staticmethod
    def _legacy_reader_rows(path):
        """THE LEGACY READER, copied from iqvia_import._import_csv as it stood
        before 634a1ab: the file opened as plain utf-8, so a leading mark is
        part of the first column's name. Used only to stage what that code
        staged."""
        with open(path, "r", encoding="utf-8", newline="") as fh:
            yield from enumerate(csv.DictReader(fh), start=2)

    async def _snapshot_staged_by_the_legacy_reader(self, db, f, sha, *, rows):
        """A PENDING snapshot with its first `rows` rows staged the way the
        legacy importer staged them, and no reader mark in its metadata."""
        from datetime import timezone

        from app.tefca_registry.rce.source_matching import register_snapshot

        snapshot = await register_snapshot(
            db, source_system=sm.SOURCE_IQVIA_HCO,
            label=f"{SYN}-reader-{uuid.uuid4().hex[:8]}", sha256=sha, record_count=0,
            received_at=datetime.now(timezone.utc), created_by=SYN,
            metadata={"original_path": str(f)})
        _created_snapshot_ids.append(snapshot.id)
        staged = []
        for _line, row in self._legacy_reader_rows(f):
            if len(staged) >= rows:
                break
            built = ii._hco_payload_and_keys(row)
            assert built is not None, "the legacy reader did stage this row"
            staged.append({"id": uuid.uuid4(), "source_snapshot_id": snapshot.id,
                           "record_sha256": ii._row_sha256(row),
                           "observed_at": datetime.now(timezone.utc),
                           "correlation_id": "legacy", "payload": row, **built})
        if staged:
            await ii._stage_rows(db, snapshot_id=snapshot.id, model=sm.IqviaHcoObservation,
                                 rows=staged)
        await db.commit()
        return snapshot

    async def _rows(self, db, snapshot_id):
        await db.rollback()
        rows = (await db.execute(
            select(sm.IqviaHcoObservation)
            .where(sm.IqviaHcoObservation.source_snapshot_id == snapshot_id)
            .execution_options(populate_existing=True))).scalars().all()
        return {r.source_record_key: (r.npi, r.record_sha256, dict(r.payload)) for r in rows}

    async def test_a_marked_file_partly_staged_by_the_legacy_reader_is_not_resumed(
            self, db, tmp_path):
        n = 6
        f, sha = self._file(tmp_path, n, "a")
        snapshot = await self._snapshot_staged_by_the_legacy_reader(db, f, sha, rows=2)
        before = await self._rows(db, snapshot.id)
        assert len(before) == 2
        assert all(npi is None for npi, _sha, _payload in before.values()), \
            "what the legacy reader left: the first column was lost"

        with pytest.raises(ii.ReaderChangeRefused) as refused:
            await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                    chunk_size=2, snapshot_id=snapshot.id)
        message = str(refused.value)
        assert str(snapshot.id) in message and "byte-order mark" in message
        assert "1234567893" not in message and SYN not in message, "no row content in the reason"
        assert len(f"{type(refused.value).__name__}: {message}") <= 300, \
            "the job runner keeps 300 characters of the reason"

        after = await self._rows(db, snapshot.id)
        assert after == before, "nothing further staged; the staged rows are untouched"
        refreshed = await db.get(sm.SourceSnapshot, snapshot.id)
        await db.refresh(refreshed)
        assert refreshed.status == sm.SNAPSHOT_PENDING
        recorded = refreshed.metadata_["reference_reader_refused"]
        assert recorded["code"] == "READER_CHANGED_AFTER_STAGING"
        assert recorded["rows_already_staged"] == 2
        assert "reference_reader" not in refreshed.metadata_

        # Asked again (the job runner retries): the same refusal, still nothing staged.
        with pytest.raises(ii.ReaderChangeRefused):
            await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                    snapshot_id=snapshot.id)
        assert await self._rows(db, snapshot.id) == before

        # It can never be reconciled, so it can never be approved.
        verdict = await ii.verify_snapshot_staged_completely(db, snapshot.id)
        assert verdict["reconciles"] is False
        assert any("earlier reader" in reason for reason in verdict["refusal_reasons"])

    async def test_an_unmarked_snapshot_with_nothing_staged_proceeds_and_is_marked(
            self, db, tmp_path):
        """How every upload starts: the snapshot is registered first and the
        importer is then called with its id. Nothing was staged by anyone."""
        n = 4
        f, sha = self._file(tmp_path, n, "b")
        snapshot = await self._snapshot_staged_by_the_legacy_reader(db, f, sha, rows=0)
        done = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                       snapshot_id=snapshot.id)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (n, n, 0)
        rows = await self._rows(db, snapshot.id)
        assert all(npi == "1234567893" for npi, _sha, _payload in rows.values())
        refreshed = await db.get(sm.SourceSnapshot, snapshot.id)
        await db.refresh(refreshed)
        assert refreshed.metadata_["reference_reader"] == ii.REFERENCE_READER
        assert "reference_reader_refused" not in refreshed.metadata_
        # Marked now: a later resume of the same marked file is not refused.
        again = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                        snapshot_id=snapshot.id)
        assert (again.rows_staged, again.rows_already_staged) == (0, n)

    async def test_a_file_without_a_mark_resumes_exactly_as_before(self, db, tmp_path):
        n = 6
        f, sha = self._file(tmp_path, n, "c", marked=False)
        legacy = [(line, row, ii._row_sha256(row)) for line, row in self._legacy_reader_rows(f)]
        current = [(line, row, ii._row_sha256(row)) for line, row in ii.iter_reference_rows(f)]
        assert legacy == current, "both readers read an unmarked file identically"

        snapshot = await self._snapshot_staged_by_the_legacy_reader(db, f, sha, rows=2)
        before = await self._rows(db, snapshot.id)
        resumed = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                          chunk_size=2, snapshot_id=snapshot.id)
        assert (resumed.rows_already_staged, resumed.rows_staged) == (2, n - 2)
        after = await self._rows(db, snapshot.id)
        assert len(after) == n and all(after[k] == v for k, v in before.items())
        assert all(npi == "1234567893" for npi, _sha, _payload in after.values())
        refreshed = await db.get(sm.SourceSnapshot, snapshot.id)
        await db.refresh(refreshed)
        assert refreshed.metadata_["reference_reader"] == ii.REFERENCE_READER

    async def test_a_new_import_is_marked_when_it_is_registered(self, db, tmp_path):
        f, _sha = self._file(tmp_path, 3, "d")
        label = f"{SYN}-reader-d-{uuid.uuid4().hex[:8]}"
        done = await ii.import_hco_csv(db, file_path=f, label=label, created_by=SYN)
        _created_snapshot_ids.append(done.snapshot_id)
        snapshot = await db.get(sm.SourceSnapshot, done.snapshot_id)
        assert snapshot.metadata_["reference_reader"] == ii.REFERENCE_READER

    async def test_rows_staged_under_a_different_recorded_reader_are_not_resumed(
            self, db, tmp_path):
        """The mark is what a future reader change will be checked against."""
        f, sha = self._file(tmp_path, 4, "e", marked=False)
        snapshot = await self._snapshot_staged_by_the_legacy_reader(db, f, sha, rows=2)
        snapshot.metadata_ = {**snapshot.metadata_, "reference_reader": "iqvia_import/some-other"}
        await db.commit()
        with pytest.raises(ii.ReaderChangeRefused):
            await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                    snapshot_id=snapshot.id)
        assert len(await self._rows(db, snapshot.id)) == 2
