"""IQVIA snapshot Open/status, exact background reconciliation, and gated approval
(2026-11-24). Entirely synthetic ids and rows; no IQVIA content anywhere.

Background: the status route used to run an inline `count(*)` that took 42-181 s
on DEV and timed the portal out. These tests pin the replacement:

* Open / status returns the importer's RECORDED counters and never counts;
* the exact count is a queued job, single-flight per snapshot and globally;
* approval needs a PASSED result bound to the CURRENT staged data;
* maker/checker and the audit rows are unchanged.

Handlers are called directly (as test_iqvia_routes.py does); the scheduler's
claim-and-run is `rec.claim_next` + `rec.run_reconciliation`.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import func, select, text

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_reconciliation as rec
from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN  # noqa: F401
from test_iqvia_routes import (  # noqa: F401  (fixtures + helpers shared with the route tests)
    HCO_HEADER, _created_snapshot_ids, _iqvia_drop_dir_is_tmp_path, _iqvia_sources, _reconcile,
    _run_one_queued_job, _stage_and_complete, _User, db, write_csv)

pytestmark = pytest.mark.regression

REVIEWER = lambda: _User("reviewer")  # noqa: E731


async def _status(db, snapshot_id):
    db.expire_all()
    return await routes.snapshot_status(uuid.UUID(str(snapshot_id)), db, user=REVIEWER())


async def _latest(db, snapshot_id):
    db.expire_all()
    row, _ = await rec.latest_reconciliation(db, uuid.UUID(str(snapshot_id)))
    return row


async def _approve(db, snapshot_id, user):
    return await routes.approve(uuid.UUID(str(snapshot_id)),
                                routes.ApproveRequest(approval_ref=f"{SYN}-ref"), db, user=user)


async def _expect_409(coro, code):
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == 409, exc.value.detail
    assert exc.value.detail["code"] == code, exc.value.detail
    return exc.value.detail


# ── 1. Open / status never counts ────────────────────────────────────────────

class TestOpenReturnsRecordedCountersWithoutCounting:
    async def test_completed_import_open_does_not_touch_the_count_and_writes_nothing(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=4)

        async def boom(*a, **k):
            raise AssertionError("Open/status must not run the exact count")
        monkeypatch.setattr(ii, "verify_snapshot_staged_completely", boom)

        before = (await db.execute(select(func.count()).select_from(rec.IqviaSnapshotReconciliation)
                                   .where(rec.IqviaSnapshotReconciliation.snapshot_id
                                          == uuid.UUID(snapshot_id)))).scalar()
        for _ in range(3):  # polling repeatedly starts nothing and counts nothing
            status = await _status(db, snapshot_id)
        after = (await db.execute(select(func.count()).select_from(rec.IqviaSnapshotReconciliation)
                                  .where(rec.IqviaSnapshotReconciliation.snapshot_id
                                         == uuid.UUID(snapshot_id)))).scalar()
        assert before == after == 0
        c = status["counters"]
        assert c["label"] == "Recorded at import" and c["basis"] == "recorded_at_import"
        assert c["rows_read"] == 4 and c["rows_inserted_latest_attempt"] == 4
        assert c["rows_already_present"] == 0 and c["rows_processed_at_import"] == 4
        assert c["resumed"] is False
        assert "not a table count" in c["rows_processed_label"]
        assert status["reconciliation"]["status"] == "not_checked"
        assert status["reconciliation"]["label"] == "Not checked"

    async def test_resumed_import_separates_latest_attempt_inserts_from_rows_already_present(
            self, db, tmp_path, monkeypatch):
        _iqvia_sources(monkeypatch, True)
        n = 6
        f = write_csv(tmp_path / "hco.csv", HCO_HEADER, [
            [f"{SYN}-RESUME-{i}", "", "", f"{SYN} Resume Org {i}", "02101"] for i in range(n)])
        real = ii._stage_rows
        calls = {"n": 0}

        async def half_then_die(db_, *, snapshot_id, model, rows):
            calls["n"] += 1
            if calls["n"] == 1:   # attempt 1 stages 3 rows, then the worker dies
                await real(db_, snapshot_id=snapshot_id, model=model, rows=rows[:3])
                await db_.commit()
                raise RuntimeError("simulated worker death after a partial chunk")
            return await real(db_, snapshot_id=snapshot_id, model=model, rows=rows)
        monkeypatch.setattr(ii, "_stage_rows", half_then_die)

        result = await routes.stage_source(
            "hco", routes.StageRequest(file_path=str(f), label=f"{SYN}-resume-{uuid.uuid4().hex[:6]}"),
            db, user=REVIEWER())
        sid = uuid.UUID(result["snapshot_id"])
        _created_snapshot_ids.append(sid)
        job = await jobs.claim_next_queued(db)
        await routes.run_import_job(str(job.id))            # attempt 1: dies, requeued
        await db.refresh(job)
        assert job.state == um.IqviaImportJob.STATE_QUEUED
        job = await jobs.claim_next_queued(db)
        await routes.run_import_job(str(job.id))            # attempt 2: resumes, completes
        await db.refresh(job)
        assert job.state == um.IqviaImportJob.STATE_SUCCEEDED

        status = await _status(db, sid)
        c = status["counters"]
        assert c["rows_inserted_latest_attempt"] == 3      # what attempt 2 newly inserted
        assert c["rows_already_present"] == 3              # what attempt 1 had left
        assert c["rows_processed_at_import"] == 6
        assert c["resumed"] is True
        # The exact staged total is NOT claimed until the reconciliation runs...
        assert "staged_row_count" not in status["reconciliation"]
        await _reconcile(db, sid)
        status = await _status(db, sid)
        assert status["reconciliation"]["status"] == "passed"
        assert status["reconciliation"]["staged_row_count"] == 6     # ...and then it is exact

    async def test_open_is_a_pure_read_even_when_a_result_is_stale_or_a_worker_is_lost(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        await routes.start_reconciliation(uuid.UUID(snapshot_id), Response(), db, user=REVIEWER())
        row = await rec.claim_next(db)
        # The worker "dies": heartbeat far in the past on the database clock.
        await db.execute(text("UPDATE iqvia_snapshot_reconciliation SET heartbeat_at = now() - interval '1 hour' "
                              "WHERE id = :i"), {"i": row.id})
        await db.commit()
        status = await _status(db, snapshot_id)
        assert status["reconciliation"]["status"] == "failed"          # described as Failed...
        raw = (await db.execute(text("SELECT state FROM iqvia_snapshot_reconciliation WHERE id=:i"),
                                {"i": row.id})).scalar()
        assert raw == "CHECKING"                                        # ...but GET repaired nothing
        assert await rec.reap_stale(db) == 1                            # the reaper owns the repair
        assert (await _latest(db, snapshot_id)).state == "FAILED"


# ── 2. Single-flight ─────────────────────────────────────────────────────────

class TestSingleFlight:
    async def test_opening_twice_starts_one_count(self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        sid = uuid.UUID(snapshot_id)
        a = await routes.start_reconciliation(sid, Response(), db, user=REVIEWER())
        b = await routes.start_reconciliation(sid, Response(), db, user=REVIEWER())
        assert (a["outcome"], b["outcome"]) == ("started", "already_active")
        assert a["reconciliation"]["reconciliation_id"] == b["reconciliation"]["reconciliation_id"]
        assert a["reconciliation"]["status"] == "checking"
        n = (await db.execute(select(func.count()).select_from(rec.IqviaSnapshotReconciliation)
                              .where(rec.IqviaSnapshotReconciliation.snapshot_id == sid))).scalar()
        assert n == 1

    async def test_simultaneous_requests_still_produce_one_row(self, db, tmp_path, monkeypatch):
        from app.core.database import async_session_maker
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        sid = uuid.UUID(snapshot_id)

        async def one():
            async with async_session_maker() as s:
                return await routes.start_reconciliation(sid, Response(), s, user=REVIEWER())
        results = await asyncio.gather(*[one() for _ in range(6)])
        assert sorted(r["outcome"] for r in results).count("started") == 1
        assert len({r["reconciliation"]["reconciliation_id"] for r in results}) == 1
        n = (await db.execute(select(func.count()).select_from(rec.IqviaSnapshotReconciliation)
                              .where(rec.IqviaSnapshotReconciliation.snapshot_id == sid))).scalar()
        assert n == 1

    async def test_the_database_itself_refuses_a_second_active_row(self, db, tmp_path, monkeypatch):
        from sqlalchemy.exc import IntegrityError
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        sid = uuid.UUID(snapshot_id)
        await routes.start_reconciliation(sid, Response(), db, user=REVIEWER())
        db.add(rec.IqviaSnapshotReconciliation(
            id=uuid.uuid4(), snapshot_id=sid, state="QUEUED", staging_token="x", active_marker=True,
            requested_by="x"))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()

    async def test_only_one_check_runs_at_a_time_across_snapshots(self, db, tmp_path, monkeypatch):
        s1 = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        f2 = write_csv(tmp_path / "hco2.csv", HCO_HEADER, [
            [f"{SYN}-SECOND-{i}", "", "", f"{SYN} Second Org {i}", "02101"] for i in range(7)])
        r2 = await routes.stage_source(
            "hco", routes.StageRequest(file_path=str(f2), label=f"{SYN}-second"), db, user=REVIEWER())
        s2 = r2["snapshot_id"]
        _created_snapshot_ids.append(uuid.UUID(s2))
        assert await _run_one_queued_job(db) == um.IqviaImportJob.STATE_SUCCEEDED
        await routes.start_reconciliation(uuid.UUID(s1), Response(), db, user=REVIEWER())
        await routes.start_reconciliation(uuid.UUID(s2), Response(), db, user=REVIEWER())
        first = await rec.claim_next(db)
        assert first is not None
        assert await rec.claim_next(db) is None      # a live CHECKING row blocks any other claim
        await rec.run_reconciliation(str(first.id))
        second = await rec.claim_next(db)
        assert second is not None and second.snapshot_id != first.snapshot_id

    async def test_a_passed_current_result_is_returned_not_recounted_and_failure_has_a_cooldown(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        sid = uuid.UUID(snapshot_id)
        await _reconcile(db, snapshot_id)
        again = await routes.start_reconciliation(sid, Response(), db, user=REVIEWER())
        assert again["outcome"] == "already_passed" and again["reconciliation"]["status"] == "passed"

        # A failing check cannot be restarted in a tight loop.
        await db.execute(text("DELETE FROM iqvia_hco_observation WHERE source_record_key = :k"),
                         {"k": f"{SYN}-ROUTE-HCO-0"})
        await db.execute(text("UPDATE iqvia_snapshot_reconciliation SET staging_token='old' "
                              "WHERE snapshot_id=:s"), {"s": sid})   # make the old PASSED result stale
        await db.commit()
        await _reconcile(db, snapshot_id)
        assert (await _latest(db, snapshot_id)).state == "FAILED"
        with pytest.raises(HTTPException) as exc:
            await routes.start_reconciliation(sid, Response(), db, user=REVIEWER())
        assert exc.value.status_code == 429 and exc.value.detail["code"] == "RECONCILIATION_COOLDOWN"


# ── 3. Failure, staleness, and the approval gate ─────────────────────────────

class TestReconciliationFailureAndApprovalGate:
    async def test_approval_without_any_check_is_refused(self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        await _expect_409(_approve(db, snapshot_id, _User("qalead")), "RECONCILIATION_REQUIRED")

    async def test_approval_while_checking_is_refused(self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        await routes.start_reconciliation(uuid.UUID(snapshot_id), Response(), db, user=REVIEWER())
        await _expect_409(_approve(db, snapshot_id, _User("qalead")), "RECONCILIATION_IN_PROGRESS")

    async def test_a_count_that_does_not_reconcile_is_failed_and_blocks_approval(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=5)
        # One staged row goes missing after the import (synthetic damage).
        await db.execute(text("DELETE FROM iqvia_hco_observation WHERE source_record_key = :k"),
                         {"k": f"{SYN}-ROUTE-HCO-1"})
        await db.commit()
        await _reconcile(db, snapshot_id)
        row = await _latest(db, snapshot_id)
        assert row.state == "FAILED" and row.failure_kind == "MISMATCH"
        assert (row.staged_count, row.expected_count) == (4, 5)
        status = await _status(db, snapshot_id)
        assert status["reconciliation"]["status"] == "failed" and status["reconciliation"]["label"] == "Failed"
        assert status["reconciliation"]["refusal_reasons"]
        detail = await _expect_409(_approve(db, snapshot_id, _User("qalead")), "RECONCILIATION_FAILED")
        assert detail["reconciliation"]["staged_row_count"] == 4
        # The refusal is audited with its code.
        from app.models.database import AuditLog
        audits = (await db.execute(select(AuditLog).where(
            AuditLog.resource_id == snapshot_id, AuditLog.action == "IQVIA_SNAPSHOT_APPROVAL"))).scalars().all()
        assert any((a.details or {}).get("code") == "RECONCILIATION_FAILED" for a in audits)

    async def test_a_check_error_stores_a_code_never_the_exception_text(self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)

        async def broken(*a, **k):
            raise RuntimeError("value 1234567893 leaked in message")
        monkeypatch.setattr(ii, "verify_snapshot_staged_completely", broken)
        await _reconcile(db, snapshot_id)
        row = await _latest(db, snapshot_id)
        assert row.state == "FAILED" and row.failure_kind == "CHECK_ERROR"
        assert row.error_reason == "CHECK_ERROR:RuntimeError"
        assert "1234567893" not in (row.error_reason or "")
        await _expect_409(_approve(db, snapshot_id, _User("qalead")), "RECONCILIATION_FAILED")

    async def test_staged_data_changing_during_the_count_discards_the_result(
            self, db, tmp_path, monkeypatch):
        from app.core.database import async_session_maker
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=3)
        real = ii.verify_snapshot_staged_completely

        async def count_then_a_resume_lands(db_, sid):
            result = await real(db_, sid)
            async with async_session_maker() as other:      # a resumed import completes mid-count
                snap = await other.get(sm.SourceSnapshot, sid)
                meta = dict(snap.metadata_ or {})
                meta["import_summary"] = {**meta["import_summary"], "rows_already_staged": 99}
                snap.metadata_ = meta
                await other.commit()
            return result
        monkeypatch.setattr(ii, "verify_snapshot_staged_completely", count_then_a_resume_lands)
        await _reconcile(db, snapshot_id)
        row = await _latest(db, snapshot_id)
        assert row.state == "FAILED" and row.failure_kind == "CHANGED_DURING_CHECK"
        # The data moved on, so the discarded result is also stale: a fresh check is required.
        await _expect_409(_approve(db, snapshot_id, _User("qalead")), "RECONCILIATION_STALE")

    async def test_a_passed_result_is_invalidated_when_the_staged_data_changes(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=3)
        await _reconcile(db, snapshot_id)
        assert (await _status(db, snapshot_id))["reconciliation"]["status"] == "passed"
        # A resumed import finishes again: the recorded summary changes.
        snap = await db.get(sm.SourceSnapshot, uuid.UUID(snapshot_id))
        meta = dict(snap.metadata_)
        meta["import_summary"] = {**meta["import_summary"], "rows_staged": 1, "rows_already_staged": 2}
        snap.metadata_ = meta
        await db.commit()
        status = await _status(db, snapshot_id)
        assert status["reconciliation"]["status"] == "not_checked"
        assert status["reconciliation"]["stale"] is True
        await _expect_409(_approve(db, snapshot_id, _User("qalead")), "RECONCILIATION_STALE")
        # A fresh check for the current data re-opens the gate.
        again = await _reconcile(db, snapshot_id)
        assert again["outcome"] == "started"
        # (the staged rows still equal read-minus-rejected, so it passes again)
        assert (await _status(db, snapshot_id))["reconciliation"]["status"] == "passed"
        approved = await _approve(db, snapshot_id, _User("qalead"))
        assert approved["status"] == sm.SNAPSHOT_APPROVED

    async def test_crash_window_completed_status_with_a_live_import_job_is_refused(
            self, db, tmp_path, monkeypatch):
        """`import_status: completed` is committed before the job is released; a
        crash between them leaves 'completed' with an ACTIVE job that the reaper
        requeues. Neither starting a check nor approving may proceed then."""
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        await _reconcile(db, snapshot_id)
        job = await jobs.latest_job_for_snapshot(db, uuid.UUID(snapshot_id))
        await db.execute(text("UPDATE iqvia_import_job SET state='QUEUED', active_marker=true, "
                              "completed_at=NULL WHERE id=:i"), {"i": job.id})
        await db.commit()
        await _expect_409(_approve(db, snapshot_id, _User("qalead")), "IMPORT_IN_PROGRESS")
        with pytest.raises(HTTPException) as exc:
            await routes.start_reconciliation(uuid.UUID(snapshot_id), Response(), db, user=REVIEWER())
        assert exc.value.detail["code"] == "IMPORT_IN_PROGRESS"


# ── 4. Approval: success, maker/checker, audit ───────────────────────────────

class TestApproval:
    async def test_a_different_qalead_approves_after_the_check_passes_and_the_audit_links_it(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=3)
        await _reconcile(db, snapshot_id)
        row = await _latest(db, snapshot_id)
        approved = await _approve(db, snapshot_id, _User("qalead"))
        _created_snapshot_ids.append(uuid.UUID(approved["snapshot_id"]))
        assert approved["status"] == sm.SNAPSHOT_APPROVED
        from app.models.database import AuditLog
        audits = (await db.execute(select(AuditLog).where(
            AuditLog.resource_id == snapshot_id, AuditLog.action == "IQVIA_SNAPSHOT_APPROVED"))).scalars().all()
        assert len(audits) == 1
        d = audits[0].details
        assert d["reconciliation_id"] == str(row.id) and d["staged_row_count"] == 3
        assert d["reconciliation"]["status"] == "passed"

    async def test_the_uploader_cannot_approve_even_with_a_passed_check(self, db, tmp_path, monkeypatch):
        _iqvia_sources(monkeypatch, True)
        f = write_csv(tmp_path / "hco.csv", HCO_HEADER, [
            [f"{SYN}-UP-{i}", "", "", f"{SYN} Up {i}", "02101"] for i in range(2)])
        maker = _User("qalead", email=f"{SYN}-uploader-qalead@synthetic-test.docuaction.invalid")
        result = await routes.stage_source(
            "hco", routes.StageRequest(file_path=str(f), label=f"{SYN}-up"), db, user=maker)
        sid = uuid.UUID(result["snapshot_id"])
        _created_snapshot_ids.append(sid)
        assert await _run_one_queued_job(db) == um.IqviaImportJob.STATE_SUCCEEDED
        await _reconcile(db, sid, user=maker)
        assert (await _status(db, sid))["reconciliation"]["status"] == "passed"
        detail = await _expect_409(_approve(db, sid, maker), "MAKER_CHECKER_VIOLATION")
        assert detail["registrant"] == maker.email
        status = await _status(db, sid)                    # nothing changed
        assert status["status"] == sm.SNAPSHOT_PENDING and status["decision"] is None
        # A different QA lead can.
        approved = await _approve(db, sid, _User("qalead", email=f"{SYN}-other-qalead@synthetic-test.docuaction.invalid"))
        _created_snapshot_ids.append(uuid.UUID(approved["snapshot_id"]))
        assert approved["status"] == sm.SNAPSHOT_APPROVED

    async def test_below_reviewer_cannot_start_a_check_and_a_non_qalead_cannot_approve(
            self, db, tmp_path, monkeypatch):
        """Role floors are enforced by `require_role` dependencies; asserted on the
        route declarations so a refactor cannot drop them silently."""
        import inspect
        def role_of(fn):
            for p in inspect.signature(fn).parameters.values():
                dep = getattr(p.default, "dependency", None)
                if dep is not None and "role" in getattr(dep, "__qualname__", ""):
                    return dep.__closure__[0].cell_contents if dep.__closure__ else None
        assert role_of(routes.start_reconciliation) == "reviewer"
        assert role_of(routes.approve) == "qalead"


# -- 5. Restart / retry, multi-instance claim, and what an approved snapshot shows (2026-11-24 review) --

class TestRestartRetryAndApprovedView:
    async def test_a_lost_check_is_reaped_and_a_new_request_then_runs_and_passes(self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=3)
        sid = uuid.UUID(snapshot_id)
        await routes.start_reconciliation(sid, Response(), db, user=REVIEWER())
        lost_id = (await rec.claim_next(db)).id
        await db.execute(text("UPDATE iqvia_snapshot_reconciliation SET heartbeat_at = now() - interval '1 hour' "
                              "WHERE id = :i"), {"i": lost_id})
        await db.commit()
        # The dead worker's row no longer blocks the single slot, but approval is still refused.
        await _expect_409(_approve(db, snapshot_id, _User("qalead")), "RECONCILIATION_FAILED")
        assert await rec.reap_stale(db) == 1
        closed = await _latest(db, snapshot_id)
        assert closed.state == "FAILED" and closed.error_reason == "WORKER_LOST" and closed.active_marker is None
        # Retry: refused inside the cooldown, then a fresh row runs to PASSED.
        with pytest.raises(HTTPException) as exc:
            await routes.start_reconciliation(sid, Response(), db, user=REVIEWER())
        assert exc.value.status_code == 429
        await db.execute(text("UPDATE iqvia_snapshot_reconciliation SET finished_at = now() - interval '1 hour' "
                              "WHERE id = :i"), {"i": lost_id})
        await db.commit()
        retried = await _reconcile(db, snapshot_id)
        assert retried["outcome"] == "started"
        row = await _latest(db, snapshot_id)
        assert row.id != lost_id and row.state == "PASSED" and row.staged_count == 3
        total = (await db.execute(select(func.count()).select_from(rec.IqviaSnapshotReconciliation)
                                  .where(rec.IqviaSnapshotReconciliation.snapshot_id == sid))).scalar()
        assert total == 2                                   # the failed attempt is kept as history

    async def test_a_second_app_instance_cannot_claim_while_another_is_claiming(self, db, tmp_path, monkeypatch):
        from app.core.database import async_session_maker

        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=2)
        await routes.start_reconciliation(uuid.UUID(snapshot_id), Response(), db, user=REVIEWER())
        async with async_session_maker() as other:          # another instance, mid-claim
            assert (await other.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                                        {"k": rec.CLAIM_LOCK_KEY})).scalar() is True
            assert await rec.claim_next(db) is None         # this instance backs off, claims nothing
            assert (await _latest(db, snapshot_id)).state == "QUEUED"
            await other.rollback()
        claimed = await rec.claim_next(db)
        assert claimed is not None and claimed.state == "CHECKING"
        await rec.run_reconciliation(str(claimed.id))
        assert (await _latest(db, snapshot_id)).state == "PASSED"

    async def test_approval_never_counts_and_the_approved_snapshot_shows_the_check_it_rested_on(
            self, db, tmp_path, monkeypatch):
        snapshot_id = await _stage_and_complete(db, tmp_path, monkeypatch, n=3)
        await _reconcile(db, snapshot_id)
        row = await _latest(db, snapshot_id)

        async def boom(*a, **k):
            raise AssertionError("approval and Open must not run the exact count")
        monkeypatch.setattr(ii, "verify_snapshot_staged_completely", boom)

        approved = await _approve(db, snapshot_id, _User("qalead"))
        _created_snapshot_ids.append(uuid.UUID(approved["snapshot_id"]))
        for sid in (approved["snapshot_id"], snapshot_id):  # the approved row, and the row it was approved from
            view = (await _status(db, sid))["reconciliation"]
            assert view["status"] == "passed_before_approval" and view["label"] == "Passed before approval"
            assert view["reconciles"] is True and view["staged_row_count"] == 3
            assert view["reconciliation_id"] == str(row.id) and view["checked_snapshot_id"] == snapshot_id
        # An approved snapshot cannot be approved again, whatever its recorded check says.
        with pytest.raises(HTTPException) as exc:
            await _approve(db, snapshot_id, _User("qalead", email=f"{SYN}-third@synthetic-test.docuaction.invalid"))
        assert exc.value.status_code == 409
