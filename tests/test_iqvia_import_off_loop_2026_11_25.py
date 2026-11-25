"""The whole-file hash and the reference preflight leave the event loop, and
the job keeps a heartbeat while they run (2026-11-25).

Before: `_import_csv` hashed a multi-GB file and ran the preflight inline, so
the single worker answered nothing for minutes, and it wrote no heartbeat until
the first chunk was staged - a hash longer than the reaper threshold would have
had the job requeued while it was still running.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import datetime, timedelta

import pytest

import iqvia_contract_support as support
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import reference_preflight as rp
from iqvia_contract_support import committed_db  # noqa: F401
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression

HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]


def small_file(tmp_path, name="f.csv", rows=3):
    return support.write_cells(tmp_path / name, HEADER,
                               [[f"{SYN}-OFF-{uuid.uuid4().hex[:8]}-{n}", "", "", "N", "02101"]
                                for n in range(rows)])


def slow(monkeypatch, seconds: float):
    """Make the hash and the preflight take `seconds` each, and record the
    thread each ran on."""
    threads = {}
    real_hash, real_preflight = ii.file_sha256, rp.preflight_reference_file

    def slow_hash(path, **kw):
        threads["hash"] = threading.current_thread()
        time.sleep(seconds)
        return real_hash(path, **kw)

    def slow_preflight(path, source_system):
        threads["preflight"] = threading.current_thread()
        time.sleep(seconds)
        return real_preflight(path, source_system)

    monkeypatch.setattr(ii, "file_sha256", slow_hash)
    monkeypatch.setattr(rp, "preflight_reference_file", slow_preflight)
    return threads


class TestTheEventLoopStaysFree:
    async def test_other_coroutines_run_while_the_file_is_hashed_and_preflighted(
            self, rolled_back_db, tmp_path, monkeypatch):
        threads = slow(monkeypatch, 0.4)
        ticks, stop = [], asyncio.Event()

        async def ticker():
            while not stop.is_set():
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        task = asyncio.create_task(ticker())
        started = time.monotonic()
        done = await ii.import_hco_csv(rolled_back_db, file_path=small_file(tmp_path),
                                       label=f"{SYN}-off-loop", created_by=SYN)
        stop.set()
        await task
        assert done.completed and done.rows_staged == 3
        main = threading.main_thread()
        assert threads["hash"] is not main and threads["preflight"] is not main
        # 0.8 s of blocking work: an inline hash would have allowed no tick at all in that window.
        during = [t for t in ticks if t - started < 0.8]
        assert len(during) >= 15, len(during)
        assert max(b - a for a, b in zip(during, during[1:])) < 0.3

    async def test_the_heartbeat_callback_beats_through_both_phases(
            self, rolled_back_db, tmp_path, monkeypatch):
        slow(monkeypatch, 0.3)
        monkeypatch.setattr(ii, "OFF_LOOP_HEARTBEAT_SECONDS", 0.05)
        beats = []

        async def beat(phase):
            beats.append(phase)

        await ii.import_hco_csv(rolled_back_db, file_path=small_file(tmp_path), label=f"{SYN}-beats",
                                created_by=SYN, heartbeat_cb=beat)
        assert beats.count("hashing the file") >= 3
        assert beats.count("reference preflight (whole-file read)") >= 3
        assert beats[0] == "hashing the file"            # the first beat is immediate

    async def test_a_failing_heartbeat_does_not_stop_the_import(self, rolled_back_db, tmp_path, monkeypatch):
        slow(monkeypatch, 0.05)

        async def beat(phase):
            raise RuntimeError("heartbeat store unavailable")

        done = await ii.import_hco_csv(rolled_back_db, file_path=small_file(tmp_path), label=f"{SYN}-hb-fail",
                                       created_by=SYN, heartbeat_cb=beat)
        assert done.completed and done.rows_staged == 3

    async def test_an_exception_in_the_hash_still_stops_the_beat(self, rolled_back_db, tmp_path, monkeypatch):
        def broken(path, **kw):
            raise OSError("unreadable")

        monkeypatch.setattr(ii, "file_sha256", broken)
        monkeypatch.setattr(ii, "OFF_LOOP_HEARTBEAT_SECONDS", 0.01)
        beats = []

        async def beat(phase):
            beats.append(phase)

        with pytest.raises(OSError):
            await ii.import_hco_csv(rolled_back_db, file_path=small_file(tmp_path), label=f"{SYN}-hash-fail",
                                    created_by=SYN, heartbeat_cb=beat)
        seen = len(beats)
        await asyncio.sleep(0.05)
        assert len(beats) == seen


class TestTheReaperDoesNotRequeueAHashingJob:
    async def test_a_job_whose_heartbeat_was_stale_is_alive_again_while_it_hashes(
            self, committed_db, tmp_path, monkeypatch):
        """The job starts with a heartbeat already older than the reaper's
        threshold (as after a long wait in the queue). While the hash runs, the
        reaper finds nothing to reap."""
        db = committed_db
        slow(monkeypatch, 0.6)
        monkeypatch.setattr(ii, "OFF_LOOP_HEARTBEAT_SECONDS", 0.05)
        f = small_file(tmp_path, "reap.csv")
        staged = await routes.stage_source(
            "hco", routes.StageRequest(file_path=str(f), label=f"{SYN}-reap-{uuid.uuid4().hex[:6]}"),
            db, user=support.StageUser())
        support.created_snapshot_ids.append(uuid.UUID(staged["snapshot_id"]))
        job = await jobs.claim_next_queued(db)
        while job is not None and str(job.snapshot_id) != staged["snapshot_id"]:
            job = await jobs.claim_next_queued(db)      # another module's leftover; not ours to run
        assert job is not None
        job.heartbeat_at = datetime.utcnow() - timedelta(seconds=jobs.STALE_HEARTBEAT_SECONDS + 600)
        await db.commit()
        job_id = job.id

        observed = {}

        async def reaper_during_the_hash():
            from app.core.database import async_session_maker
            await asyncio.sleep(0.3)                    # inside the 0.6 s hash
            async with async_session_maker() as other:
                reaped = await jobs.reap_stale_jobs(other)
                observed["reaped"] = [r for r in reaped if r["job_id"] == str(job_id)]
                row = await other.get(um.IqviaImportJob, job_id)
                observed["state"], observed["phase"] = row.state, row.phase

        reaper = asyncio.create_task(reaper_during_the_hash())
        await routes.run_import_job(str(job_id))
        await reaper
        assert observed["reaped"] == []
        assert observed["state"] == um.IqviaImportJob.STATE_RUNNING
        assert observed["phase"] == "hashing the file"
        await db.refresh(job)
        assert job.state == um.IqviaImportJob.STATE_SUCCEEDED and job.attempt_count == 1
