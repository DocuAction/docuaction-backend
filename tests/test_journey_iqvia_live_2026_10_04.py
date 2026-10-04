"""IQVIA journey, route-level against the LIVE running server
(http://127.0.0.1:8103, DATABASE_URL=test_journey_1003). Seeds a tiny
synthetic HCO fixture via the proven stage_source() path (server-local file,
not a large import), reuses the DB-level stage call under pytest (for
reliable env/monkeypatch handling, same lesson as the QA journey), then
drives approve/match over the LIVE HTTP server with journey-qalead/
journey-analyst. Does not touch the completed 6,817,131-row real IQVIA
import.
"""
from __future__ import annotations

import csv

import httpx
import pytest

import app.tefca_registry.rce.iqvia_routes as routes
import app.tefca_registry.rce.iqvia_upload_jobs as jobs
import app.tefca_registry.rce.iqvia_upload_models as um
import app.tefca_registry.rce.source_matching as sm
import test_sam_e2e_delivery_path as sam

pytestmark = pytest.mark.asyncio

LIVE = "http://127.0.0.1:8103"
SYN = "JOURNEY-IQVIA"

HCO_HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


async def _run_one_queued_job(db) -> str:
    job = await jobs.claim_next_queued(db)
    assert job is not None, "expected a QUEUED IQVIA import job to claim"
    await routes.run_import_job(str(job.id))
    await db.refresh(job)
    return job.state


async def test_journey_iqvia_live(tmp_path, monkeypatch, db_required):
    from app.core.database import async_session_maker
    from app.models.database import User
    from app.core.security import hash_password

    await sam._ensure_journey_users()
    import uuid as _uuid

    monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")

    # ---- stage + run the import job directly (DB-level, same reliable
    # pattern as the proven test_iqvia_routes.py helper). Content must be
    # unique per run -- the snapshot-identity guard correctly refuses a
    # byte-for-byte repeat of an earlier run's fixture. Written into
    # IQVIA_IMPORT_DIR, not tmp_path -- stage_source confines a stage
    # request's file_path to that directory (CodeQL py/path-injection fix,
    # 2026-10-04); a pytest tmp_path lives outside it. ----
    from pathlib import Path

    from app.core.config import settings
    run_tag = _uuid.uuid4().hex[:10]
    import_dir = Path(settings.IQVIA_IMPORT_DIR)
    import_dir.mkdir(parents=True, exist_ok=True)
    f = _write_csv(import_dir / f"journey-hco-{run_tag}.csv", HCO_HEADER, [
        [f"{SYN}-{run_tag}-HCO-{i}", "", "", f"{SYN} Journey Org {run_tag}-{i}", "02101"] for i in range(2)
    ])
    from sqlalchemy import select
    async with async_session_maker() as db:
        body = routes.StageRequest(file_path=str(f), label=f"{SYN}-stage-{run_tag}")
        reviewer_row = (await db.execute(
            select(User).where(
                User.email == "journey-analyst@synthetic-test.docuaction.invalid"))
        ).scalars().first()
        result = await routes.stage_source("hco", body, db, user=reviewer_row)
        snapshot_id = result["snapshot_id"]
        print(f"[iqvia] staged snapshot_id={snapshot_id} job_id={result['job_id']}")
        state = await _run_one_queued_job(db)
    print(f"[iqvia] import job finished: {state}")
    assert state == um.IqviaImportJob.STATE_SUCCEEDED

    # ---- now exercise the LIVE server for status/approve/match ----
    with httpx.Client(timeout=20) as client:
        analyst_tok = client.post(f"{LIVE}/api/auth/login", json={
            "email": "journey-analyst@synthetic-test.docuaction.invalid",
            "password": "JourneyAnalyst!2026"}).json()["access_token"]
        qalead_tok = client.post(f"{LIVE}/api/auth/login", json={
            "email": "journey-qalead@synthetic-test.docuaction.invalid",
            "password": "JourneyQALead!2026"}).json()["access_token"]
        HA = {"Authorization": f"Bearer {analyst_tok}"}
        HQ = {"Authorization": f"Bearer {qalead_tok}"}

        r = client.get(f"{LIVE}/api/tefca/rce/iqvia/snapshots/{snapshot_id}", headers=HA)
        print(f"[iqvia] snapshot status: {r.status_code} {r.text[:500]}")
        assert r.status_code == 200

        # Analyst (reviewer role) attempting approve -> should be refused,
        # approve requires qalead per iqvia_routes.py:475.
        r = client.post(f"{LIVE}/api/tefca/rce/iqvia/snapshots/{snapshot_id}/approve", headers=HA,
                        json={"approval_ref": "journey-live-approval-attempt"})
        print(f"[iqvia] analyst approve attempt (expect 403 role-gate): {r.status_code} {r.text[:300]}")
        assert r.status_code == 403

        # QA lead approves for real.
        r = client.post(f"{LIVE}/api/tefca/rce/iqvia/snapshots/{snapshot_id}/approve", headers=HQ,
                        json={"approval_ref": "journey-live-approval"})
        print(f"[iqvia] qalead approve: {r.status_code} {r.text[:500]}")
        assert r.status_code == 200, f"approve should succeed for qalead: {r.text}"

        # Matching: with no HCP/affiliation data loaded this round, confirm
        # the match call returns a real, understandable response rather
        # than a raw 500 -- labelled, not fabricated.
        r = client.post(f"{LIVE}/api/tefca/rce/iqvia/snapshots/{snapshot_id}/match", headers=HA)
        print(f"[iqvia] match attempt (no HCP/affiliation data loaded): {r.status_code} {r.text[:600]}")
        assert r.status_code < 500, f"match should not 500 even with no affiliation data: {r.text}"
