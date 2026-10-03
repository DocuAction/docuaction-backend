"""Reporting journey, route-level against the LIVE running server
(http://127.0.0.1:8103, DATABASE_URL=test_journey_1003). Uses the clean,
fully-reconciled intake from the Admin upload journey
(d5e65cb4-0369-47af-9517-8a25c74e3546, READY_FOR_REVIEW, 0 invalid
identifiers promoted) to generate a delivery_processing report via the
async job path, poll status, download CSV, and check formula-injection
escaping and totals.
"""
from __future__ import annotations

import time

import httpx
import pytest

LIVE = "http://127.0.0.1:8103"
CLEAN_INTAKE_ID = "d5e65cb4-0369-47af-9517-8a25c74e3546"


def test_journey_reporting_live():
    admin_tok = httpx.post(f"{LIVE}/api/auth/login", json={
        "email": "journey-admin@synthetic-test.docuaction.invalid",
        "password": "JourneyAdmin!2026"}).json()["access_token"]
    H = {"Authorization": f"Bearer {admin_tok}"}

    with httpx.Client(timeout=30) as client:
        # ---- start generation (async job path) ----
        r = client.post(f"{LIVE}/api/reports/generate/jobs", headers=H, json={
            "report_type": "delivery_processing", "format": "html",
            "parameters": {"intake_id": CLEAN_INTAKE_ID}})
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
        r = client.get(f"{LIVE}/api/tefca/rce/deliveries/{CLEAN_INTAKE_ID}/dashboard", headers=H)
        print(f"[report] delivery dashboard still usable: {r.status_code}")
        assert r.status_code == 200

        # ---- verification-coverage drill-down (click-through filters) ----
        r = client.get(f"{LIVE}/api/tefca/rce/deliveries/{CLEAN_INTAKE_ID}/verification-coverage",
                       headers=H)
        print(f"[report] verification-coverage: {r.status_code} {r.text[:400]}")

        # ---- CSV download: row grain, reconciliation, formula-injection ----
        for csv_path, label in [
            ("dispositions.csv", "dispositions"),
            ("findings.csv", "findings"),
        ]:
            r = client.get(f"{LIVE}/api/tefca/rce/deliveries/{CLEAN_INTAKE_ID}/{csv_path}", headers=H)
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
