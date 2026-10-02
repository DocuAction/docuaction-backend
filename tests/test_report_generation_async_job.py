"""Report generation as a durable background job (item 2 of the reporting-
architecture task): `POST /generate/jobs` answers 202 with a receipt instead
of rendering inline, a worker (`run_report_generation_job`) takes the SAME
`generate_report()` call the synchronous route makes, and `GET
/generate/jobs/{id}` polls the result -- all on the existing
`report_export_jobs` table (added for the ONC review workbook export), not a
new one.
"""
from __future__ import annotations

import pytest

from support_delivery_api import cleanup, headers_for, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/reports"


@pytest.fixture(scope="module")
def delivery():
    d = seed_delivery(state="SUCCEEDED", issues=0)
    yield d

    async def _delete_jobs_this_module_left_queued():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        # `report_export_jobs` is not part of `cleanup()`'s delete list (it is
        # shared across every delivery, not scoped to one). A job this module
        # created and never drove to completion stays QUEUED forever and
        # `claim_next_queued()` -- used by both this module and the ONC
        # workbook's own tests -- picks whichever QUEUED row is oldest, so a
        # leftover here would silently steal another test's claim.
        async with async_session_maker() as db:
            await db.execute(text(
                "DELETE FROM report_export_jobs WHERE source_intake_id = CAST(:i AS uuid)"),
                {"i": d["intake_id"]})
            await db.commit()
    run(_delete_jobs_this_module_left_queued())
    cleanup()


def test_a_review_cycle_only_scope_is_refused_not_queued_with_no_intake(client, delivery):
    """`report_export_jobs.source_intake_id` is NOT NULL on purpose (migration
    review). A review-cycle-only scope is valid for the synchronous route but
    names no delivery, so the async route must refuse it before it ever
    reaches `request_job` -- never attempt an insert with no intake."""
    resp = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                       json={"report_type": "delivery_processing", "format": "html",
                             "review_cycle_id": "some-cycle", "parameters": {}})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "ASYNC_GENERATION_REQUIRES_DELIVERY"


def test_queueing_returns_202_with_a_receipt_immediately(client, delivery):
    resp = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                       json={"report_type": "delivery_processing", "format": "html",
                             "parameters": {"job_id": delivery["job_id"]}})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] in ("QUEUED", "RUNNING")
    assert body["report_type"] == "delivery_processing"
    assert body["status_url"] == f"/api/reports/generate/jobs/{body['job_id']}"
    assert body["retry_after"] == 2


def test_viewer_cannot_queue_generation(client, delivery):
    resp = client.post(f"{BASE}/generate/jobs", headers=headers_for("viewer"),
                       json={"report_type": "delivery_processing", "format": "html",
                             "parameters": {"job_id": delivery["job_id"]}})
    assert resp.status_code == 403


def test_missing_scope_is_refused_422_before_any_job_is_created(client, delivery):
    resp = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                       json={"report_type": "delivery_processing", "format": "html",
                             "parameters": {}})
    assert resp.status_code == 422
    assert resp.json()["code"] == "REPORT_SCOPE_REQUIRED"


def test_a_second_call_with_the_same_idempotency_key_returns_the_same_job(client, delivery):
    key = "test-key-00000001"
    first = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                        json={"report_type": "delivery_processing", "format": "html",
                              "parameters": {"job_id": delivery["job_id"]},
                              "idempotency_key": key}).json()
    second = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                         json={"report_type": "delivery_processing", "format": "html",
                               "parameters": {"job_id": delivery["job_id"]},
                               "idempotency_key": key}).json()
    assert second["job_id"] == first["job_id"]
    assert second["reused_existing_job"] is True


def test_status_is_404_not_403_for_someone_else(client, delivery):
    created = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                          json={"report_type": "delivery_processing", "format": "html",
                                "parameters": {"job_id": delivery["job_id"]},
                                "idempotency_key": "test-key-00000002"}).json()
    resp = client.get(f"{BASE}/generate/jobs/{created['job_id']}",
                      headers=headers_for("contributor"))
    assert resp.status_code == 404


def test_worker_takes_a_queued_job_to_succeeded_and_the_report_is_servable(client, delivery):
    created = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                         json={"report_type": "delivery_processing", "format": "html",
                               "parameters": {"job_id": delivery["job_id"]},
                               "idempotency_key": "test-key-00000003"}).json()

    async def _drive_to_completion():
        import uuid as uuid_mod
        from datetime import datetime

        from app.core.database import async_session_maker
        from app.reports.data.export_job_model import ReportExportJob
        from app.reports.export_runner import run_report_generation_job

        # Claims THIS job specifically rather than `claim_next_queued` (which
        # takes whichever QUEUED job is oldest -- other tests in this module
        # leave their own QUEUED jobs behind uncompleted).
        async with async_session_maker() as db:
            job = await db.get(ReportExportJob, uuid_mod.UUID(created["job_id"]))
            assert job is not None and job.state == ReportExportJob.STATE_QUEUED
            job.state = ReportExportJob.STATE_RUNNING
            job.started_at = datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            job.attempt_count = (job.attempt_count or 0) + 1
            await db.commit()
            await db.refresh(job)
            state = await run_report_generation_job(db, job)
            return state
    state = run(_drive_to_completion())
    assert state == "SUCCEEDED"

    status = client.get(f"{BASE}/generate/jobs/{created['job_id']}",
                        headers=headers_for("reviewer")).json()
    assert status["state"] == "SUCCEEDED"
    assert status["report"]["requested_format"] == "html"
    report_id = status["report"]["report_id"]

    html = client.get(f"{BASE}/{report_id}/html", headers=headers_for("reviewer"))
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]


def test_a_nonexistent_delivery_is_refused_before_any_job_is_created(client):
    """`resolve_delivery` runs inline in the route, before `request_job` --
    an unresolvable scope is a 404 on the POST itself, not a job that gets
    queued and then fails."""
    resp = client.post(f"{BASE}/generate/jobs", headers=headers_for("reviewer"),
                      json={"report_type": "delivery_processing", "format": "html",
                            "parameters": {"job_id": "00000000-0000-0000-0000-000000000000"},
                            "idempotency_key": "test-key-00000004"})
    assert resp.status_code == 404, resp.text
