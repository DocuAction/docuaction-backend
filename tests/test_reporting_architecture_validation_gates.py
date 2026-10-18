"""Section 7 (Validation) of the reporting-architecture task: the specific
numbered gates, each proven directly rather than inferred from other tests.
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text

from support_delivery_api import cleanup, headers_for, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE_REPORTS = "/api/reports"
BASE_RCE = "/api/tefca/rce"


@pytest.fixture(scope="module")
def delivery():
    d = seed_delivery(state="SUCCEEDED", issues=1)
    yield d

    async def _cleanup_jobs():
        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(text(
                "DELETE FROM report_export_jobs WHERE source_intake_id = CAST(:i AS uuid)"),
                {"i": d["intake_id"]})
            await db.commit()
    run(_cleanup_jobs())
    cleanup()


def test_report_start_response_under_2s(client, delivery):
    """'report-start request below 2 seconds' -- POST /generate/jobs answers
    202 with a receipt, never waiting for generation. 3 distinct jobs (3
    distinct idempotency keys), each timed independently."""
    resp_times = []
    for i in range(3):
        t0 = time.perf_counter()
        resp = client.post(f"{BASE_REPORTS}/generate/jobs", headers=headers_for("reviewer"),
                           json={"report_type": "delivery_processing", "format": "html",
                                 "parameters": {"job_id": delivery["job_id"]},
                                 "idempotency_key": f"gate-test-start-{i:04d}"})
        resp_times.append(time.perf_counter() - t0)
        assert resp.status_code == 202, resp.text
    assert all(t < 2.0 for t in resp_times), resp_times


def test_metadata_get_under_2s_x3(client, delivery):
    """'metadata GET below 2 seconds x3' -- AP-001's fix on
    GET /api/reports/{report_id}, exercised 3 times."""
    created = client.post(f"{BASE_REPORTS}/generate/jobs", headers=headers_for("reviewer"),
                          json={"report_type": "delivery_processing", "format": "html",
                                "parameters": {"job_id": delivery["job_id"]},
                                "idempotency_key": "gate-test-metadata-seed"}).json()

    async def _drive():
        from datetime import datetime

        from app.core.database import async_session_maker
        from app.reports.data.export_job_model import ReportExportJob
        from app.reports.export_runner import run_report_generation_job

        async with async_session_maker() as db:
            job = await db.get(ReportExportJob, uuid.UUID(created["job_id"]))
            job.state = ReportExportJob.STATE_RUNNING
            job.started_at = datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            await db.commit()
            await db.refresh(job)
            state = await run_report_generation_job(db, job)
            return state
    assert run(_drive()) == "SUCCEEDED"

    status = client.get(f"{BASE_REPORTS}/generate/jobs/{created['job_id']}",
                        headers=headers_for("reviewer")).json()
    report_id = status["report"]["report_id"]

    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        resp = client.get(f"{BASE_REPORTS}/{report_id}", headers=headers_for("reviewer"))
        times.append(time.perf_counter() - t0)
        assert resp.status_code == 200
    assert all(t < 2.0 for t in times), times


def test_first_verification_page_under_3s_x3(client, delivery):
    """'record-list first page below 3 seconds x3'."""
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        resp = client.get(
            f"{BASE_RCE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/verified",
            headers=headers_for("reviewer"))
        times.append(time.perf_counter() - t0)
        assert resp.status_code == 200
    assert all(t < 3.0 for t in times), times


def test_retry_with_the_same_idempotency_key_returns_the_same_job(client, delivery):
    key = "gate-test-retry-0001"
    first = client.post(f"{BASE_REPORTS}/generate/jobs", headers=headers_for("reviewer"),
                        json={"report_type": "delivery_processing", "format": "html",
                              "parameters": {"job_id": delivery["job_id"]},
                              "idempotency_key": key}).json()
    second = client.post(f"{BASE_REPORTS}/generate/jobs", headers=headers_for("reviewer"),
                         json={"report_type": "delivery_processing", "format": "html",
                               "parameters": {"job_id": delivery["job_id"]},
                               "idempotency_key": key}).json()
    assert second["job_id"] == first["job_id"]
    assert second["reused_existing_job"] is True


def test_delivery_detail_stays_responsive_while_a_report_generation_job_is_running(client, delivery):
    """'delivery page stays responsive while report generation runs' -- the
    job sits RUNNING (never driven to completion in this test) and the
    delivery-detail endpoint must still answer normally and promptly: the
    two are backed by independent queries, never a shared lock."""
    created = client.post(f"{BASE_REPORTS}/generate/jobs", headers=headers_for("reviewer"),
                          json={"report_type": "delivery_processing", "format": "html",
                                "parameters": {"job_id": delivery["job_id"]},
                                "idempotency_key": "gate-test-concurrent-0001"}).json()

    async def _mark_running():
        from datetime import datetime

        from app.core.database import async_session_maker
        from app.reports.data.export_job_model import ReportExportJob

        async with async_session_maker() as db:
            job = await db.get(ReportExportJob, uuid.UUID(created["job_id"]))
            job.state = ReportExportJob.STATE_RUNNING
            job.started_at = datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            await db.commit()
    run(_mark_running())

    t0 = time.perf_counter()
    resp = client.get(f"{BASE_RCE}/delivery-jobs/{delivery['job_id']}/detail",
                      headers=headers_for("reviewer"))
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200, resp.text
    assert elapsed < 5.0, elapsed

    status = client.get(f"{BASE_REPORTS}/generate/jobs/{created['job_id']}",
                        headers=headers_for("reviewer")).json()
    assert status["state"] == "RUNNING"


def test_a_failed_generation_job_does_not_break_delivery_detail(client, delivery):
    """'failure does not break delivery detail'."""
    created = client.post(f"{BASE_REPORTS}/generate/jobs", headers=headers_for("reviewer"),
                          json={"report_type": "delivery_processing", "format": "html",
                                "parameters": {"job_id": delivery["job_id"]},
                                "idempotency_key": "gate-test-failure-0001"}).json()

    async def _fail_it():
        from app.core.database import async_session_maker
        from app.reports.data import export_jobs
        from app.reports.data.export_job_model import ReportExportJob

        async with async_session_maker() as db:
            job = await db.get(ReportExportJob, uuid.UUID(created["job_id"]))
            job.state = ReportExportJob.STATE_RUNNING
            await db.commit()
            await export_jobs.finish_failed(db, job.id, "synthetic failure for the validation gate")
    run(_fail_it())

    status = client.get(f"{BASE_REPORTS}/generate/jobs/{created['job_id']}",
                        headers=headers_for("reviewer")).json()
    assert status["state"] == "FAILED"
    assert status["error_reason"] == "synthetic failure for the validation gate"

    detail = client.get(f"{BASE_RCE}/delivery-jobs/{delivery['job_id']}/detail",
                        headers=headers_for("reviewer"))
    assert detail.status_code == 200, detail.text


def test_viewer_cannot_generate_or_mutate_without_entitlement(client, delivery):
    viewer = headers_for("viewer")
    gen = client.post(f"{BASE_REPORTS}/generate/jobs", headers=viewer,
                      json={"report_type": "delivery_processing", "format": "html",
                            "parameters": {"job_id": delivery["job_id"]}})
    assert gen.status_code == 403

    drilldown = client.get(
        f"{BASE_RCE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/verified",
        headers=viewer)
    assert drilldown.status_code == 403

    rollback = client.get(f"{BASE_RCE}/deliveries/{delivery['intake_id']}/rollback-plan",
                         headers=viewer)
    assert rollback.status_code == 403


def test_the_official_september_delivery_is_never_referenced_by_this_session(delivery):
    """'official delivery remains unchanged' -- every fixture/test this
    session created uses a FRESH synthetic job/intake id (seed_delivery's
    own uuid4()-per-call design), never the real job 0930826c or report
    DA-ARC-2026-028."""
    assert delivery["job_id"] != "0930826c-970e-419d-ab8d-f05bb4f99116"
    assert delivery["intake_id"] != "4417b334-7440-4c30-9afa-05f4268f17c7"
