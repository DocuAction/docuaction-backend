"""A job the reaper already failed cannot be flipped to SUCCEEDED by its late worker (2026-11-25).

`export_jobs.finish_succeeded` used to write SUCCEEDED onto whatever state the row had. These tests are in their
own file because `tests/test_export_jobs.py` is exempt from the database CI job (its database tests skip in CI);
this file is wired into pr-tests.yml's isolation-postgres list, so the guard is exercised against PostgreSQL.

Database-backed (rolled-back transaction); synthetic rows only.
"""
from __future__ import annotations

from app.reports.data import export_jobs
from app.reports.data.export_job_model import ReportExportJob
from test_export_jobs import _estate, _request, rolled_back_db  # noqa: F401


async def test_a_job_the_reaper_failed_stays_failed_when_its_worker_reports_success_late(
        rolled_back_db):
    """The worker went quiet past the threshold, was reaped, and then finished after all. Its slot was released
    and a replacement may be running: the late success must not overwrite FAILED or the recorded reason."""
    from datetime import datetime, timedelta

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)
    claimed = await export_jobs.claim_next_queued(db)
    claimed.heartbeat_at = datetime.utcnow() - timedelta(seconds=export_jobs.STALE_HEARTBEAT_SECONDS + 60)
    await db.commit()
    assert [r["job_id"] for r in await export_jobs.reap_stale_jobs(db)] == [str(job.id)]

    recorded = await export_jobs.finish_succeeded(
        db, job.id, report_id="LATE-REPORT", artifact_id="late-artifact", artifact_version=1,
        rendered_sha256="a" * 64, size_bytes=10)

    assert recorded is False
    after = await export_jobs.get_job(db, job.id)
    assert after.state == ReportExportJob.STATE_FAILED and after.error_reason == export_jobs.REAPED_REASON
    assert after.report_id is None and after.artifact_id is None and after.rendered_sha256 is None
    assert after.active_marker is None and after.completed_at is None


async def test_a_running_job_still_succeeds_and_a_second_success_changes_nothing(rolled_back_db):
    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)
    await export_jobs.claim_next_queued(db)
    first = await export_jobs.finish_succeeded(
        db, job.id, report_id="REPORT-1", artifact_id="artifact-1", artifact_version=1,
        rendered_sha256="b" * 64, size_bytes=10)
    again = await export_jobs.finish_succeeded(
        db, job.id, report_id="REPORT-2", artifact_id="artifact-2", artifact_version=2,
        rendered_sha256="c" * 64, size_bytes=20)
    after = await export_jobs.get_job(db, job.id)
    assert first is True and again is False
    assert after.state == ReportExportJob.STATE_SUCCEEDED
    assert (after.report_id, after.artifact_id, after.rendered_sha256) == ("REPORT-1", "artifact-1", "b" * 64)
