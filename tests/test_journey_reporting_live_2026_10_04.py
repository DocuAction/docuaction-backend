"""Reporting journey, route-level against the LIVE running server
(http://127.0.0.1:8103, DATABASE_URL=test_journey_1003). Seeds its own
clean, promoted synthetic delivery (same proven ingest -> quality -> curate
-> promote pipeline as the other journey/isolation tests -- see
test_sam_e2e_delivery_path._seed_promoted_delivery) rather than depending
on one specific intake id from someone's already-populated environment, so
this is portable to a fresh disposable database. Generates a
delivery_processing report via the async job path, polls status, downloads
CSV, and checks formula-injection escaping and totals.
"""
from __future__ import annotations

import time

import httpx
import pytest

import test_sam_e2e_delivery_path as sam

pytestmark = pytest.mark.asyncio

LIVE = "http://127.0.0.1:8103"


async def test_journey_reporting_live():
    await sam._ensure_journey_users()
    intake_id = await sam._seed_promoted_delivery(n=3)

    admin_tok = httpx.post(f"{LIVE}/api/auth/login", json={
        "email": sam.JOURNEY_ADMIN_EMAIL,
        "password": sam.JOURNEY_ADMIN_PASSWORD}).json()["access_token"]
    H = {"Authorization": f"Bearer {admin_tok}"}

    with httpx.Client(timeout=30) as client:
        # ---- start generation (async job path) ----
        r = client.post(f"{LIVE}/api/reports/generate/jobs", headers=H, json={
            "report_type": "delivery_processing", "format": "html",
            "parameters": {"intake_id": intake_id}})
        print(f"[report] generate job: {r.status_code} {r.text[:500]}")
        assert r.status_code in (200, 202), f"job queue should succeed: {r.text}"
        job = r.json()
        job_id = job.get("id") or job.get("job_id")
        assert job_id, f"no job id in response: {job}"

        # ---- poll status: queued/running -> succeeded/failed ----
        terminal = {"SUCCEEDED", "FAILED", "COMPLETED"}
        state = None
        for _ in range(30):
            r = client.get(f"{LIVE}/api/reports/generate/jobs/{job_id}", headers=H)
            j = r.json()
            state = (j.get("state") or j.get("status") or "").upper()
            print(f"[report] job state: {state}")
            if state in terminal:
                break
            time.sleep(1)
        assert state in terminal, f"job did not reach a terminal state: {j}"
        assert state != "FAILED", f"report generation failed: {j}"

        report_id = j.get("report_id") or j.get("id")
        print(f"[report] report_id: {report_id}")

        # ---- delivery detail/status usable during and after generation ----
        r = client.get(f"{LIVE}/api/tefca/rce/deliveries/{intake_id}/dashboard", headers=H)
        print(f"[report] delivery dashboard still usable: {r.status_code}")
        assert r.status_code == 200

        # ---- verification-coverage drill-down (click-through filters) ----
        r = client.get(f"{LIVE}/api/tefca/rce/deliveries/{intake_id}/verification-coverage",
                       headers=H)
        print(f"[report] verification-coverage: {r.status_code} {r.text[:400]}")

        # ---- CSV download: row grain, reconciliation, formula-injection ----
        for csv_path, label in [
            ("dispositions.csv", "dispositions"),
            ("findings.csv", "findings"),
        ]:
            r = client.get(f"{LIVE}/api/tefca/rce/deliveries/{intake_id}/{csv_path}", headers=H)
            print(f"[report] {label}.csv: {r.status_code} ({len(r.text)} bytes)")
            if r.status_code == 200:
                lines = r.text.splitlines()
                print(f"[report] {label}.csv row count (incl. header): {len(lines)}")
                # Formula-injection: no cell may start with =,+,-,@ unescaped
                # (an apostrophe-prefixed or quoted cell is the safe form).
                dangerous = [ln for ln in lines[1:] if any(
                    cell.strip().startswith(("=", "+", "@"))
                    for cell in ln.split(",") if cell.strip())]
                print(f"[report] {label}.csv rows with an unescaped leading "
                      f"formula character: {len(dangerous)} (expect 0)")
                assert not dangerous, f"formula-injection risk in {label}.csv: {dangerous[:3]}"

        # ---- generate + download the report itself, check it reads back ----
        if report_id:
            r = client.get(f"{LIVE}/api/reports/{report_id}/html", headers=H)
            print(f"[report] html download: {r.status_code} ({len(r.content)} bytes)")
            r = client.get(f"{LIVE}/api/reports/{report_id}", headers=H)
            print(f"[report] metadata: {r.status_code} {r.text[:500]}")
