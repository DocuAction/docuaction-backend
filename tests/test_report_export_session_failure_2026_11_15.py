"""CodeRabbit on #233 (app/reports/export_runner.py:343 and app/reports/routes.py:2181), reproduced.

ONE cause: a request/worker-scoped session is used after a database failure rolled it back or left it aborted.
 * export runner: a statement failure inside the run leaves the transaction aborted; the generic handler wrote the
   terminal FAILED state on the same aborted session, which raised, so the job stayed RUNNING with its
   active_marker set and no reason shown.
 * export route: the losing side of a duplicate-request race rolls the session back (expiring every ORM instance
   loaded earlier, including the intake); the route then read `intake.delivery_label` and failed with a 500
   instead of returning the winning job.
Synthetic data only; the session is the rolled-back test session.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.reports import routes as report_routes
from app.reports.data import export_jobs
from app.reports.data import october_review_loader as loader
from app.reports.data.export_job_model import ReportExportJob
from app.reports.export_runner import run_record_review_export_job
from test_october_review_export_db_2026_11_12 import QA, _req, _seed, rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression


async def test_a_failed_statement_inside_the_run_still_ends_the_job_failed_with_a_reason(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, _, _ = await _seed(db, arc="9.99.777.91")
    out = await report_routes.export_record_review(_req(intake_id, "csv"), db=db, user=QA)
    job = await db.get(ReportExportJob, uuid.UUID(out["job_id"]))
    job_id = job.id

    async def poisoned(session, **_kw):
        await session.execute(text("select 1/0"))      # a failed statement: the transaction is now aborted
    monkeypatch.setattr(loader, "load_dataset", poisoned)

    state = await run_record_review_export_job(db, job)

    assert state == ReportExportJob.STATE_FAILED
    again = await db.get(ReportExportJob, job_id)
    assert again.state == ReportExportJob.STATE_FAILED
    assert again.active_marker is None, "the export slot must be released"
    assert "could not be produced" in (again.error_reason or "")


async def test_a_duplicate_request_race_returns_the_winning_job_instead_of_a_500(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, _, _ = await _seed(db, arc="9.99.777.92")
    first = await report_routes.export_record_review(_req(intake_id, "csv"), db=db, user=QA)

    real_active_job = export_jobs.active_job
    calls = {"n": 0}

    async def stale_view(session, identity):
        calls["n"] += 1
        return None if calls["n"] <= 2 else await real_active_job(session, identity)   # the race: both checks miss
    monkeypatch.setattr(export_jobs, "active_job", stale_view)

    second = await report_routes.export_record_review(_req(intake_id, "csv"), db=db, user=QA)

    assert second["job_id"] == first["job_id"], "the loser of the race is given the winner's job"
    assert second["delivery"], "the delivery label is still returned after the rollback"
