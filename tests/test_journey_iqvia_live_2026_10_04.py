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

import asyncio
import csv
import time

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


async def _run_one_queued_job(db, job_id: str) -> str:
    """Claim and run the job this test just staged -- or, when the live
    server wins the claim first, wait for the server to finish it.

    `stage_source` COMMITS the enqueued job, and the server under test runs
    `iqvia_import_scheduler` (a 5-second poller) against the same database,
    so from that commit on there are two legal claimers -- the documented
    multi-worker contract (`claim_next_queued` uses FOR UPDATE SKIP LOCKED
    precisely so concurrent claimers are safe). Observed in CI 2026-10-30
    (run 41141806371): journey-server.log shows "IQVIA import poller claimed
    job ..." in the gap between the enqueue commit and this call, and the
    old sole-claimer assertion here failed on a correctly-behaving system.

    Both arms stay strict: the staged job must exist and be claimed by
    someone (a None claim with the job still QUEUED is the real defect the
    old assertion guarded against, and still fails), claimed exactly once,
    and the caller still requires SUCCEEDED.
    """
    import uuid as _uuid

    job = await jobs.claim_next_queued(db)
    if job is not None:
        assert str(job.id) == job_id, (
            f"claimed {job.id}, not the job this test staged ({job_id})")
        await routes.run_import_job(str(job.id))
        await db.refresh(job)
        return job.state

    # claim returned None: only legal if the server's poller already took
    # OUR job. Anything else is still an assertion failure.
    staged = await db.get(um.IqviaImportJob, _uuid.UUID(job_id))
    assert staged is not None, "expected a QUEUED IQVIA import job to claim"
    await db.refresh(staged)
    assert staged.state != um.IqviaImportJob.STATE_QUEUED, (
        "claim_next_queued returned None while the staged job was still "
        "QUEUED -- a genuine claim defect, not the poller race")
    deadline = time.monotonic() + 60
    while (staged.state not in um.IqviaImportJob.TERMINAL_STATES
           and time.monotonic() < deadline):
        await asyncio.sleep(0.5)
        await db.refresh(staged)
    assert staged.state in um.IqviaImportJob.TERMINAL_STATES, (
        f"import job stuck in {staged.state} after the server claimed it")
    assert staged.attempt_count == 1, (
        "the staged job must be claimed exactly once")
    return staged.state


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
        state = await _run_one_queued_job(db, result["job_id"])
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
