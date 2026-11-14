"""One synthetic run through the reference-data leg of the Review Hub, start to
finish, through the real routes and the real durable job:

    interrupted upload -> resume -> complete -> automatic staging (preflight
    recorded) -> approval by a second, authorised person -> comparison ->
    the hub overview at each step

and the ways it must NOT proceed: an unauthorised role, the registrant
approving their own file, approval before the evidence exists, and a staging
job that fails.

The delivery leg (official delivery -> case review -> QA -> report -> download)
is exercised by the existing journey suites, which this file does not repeat:
tests/test_journey_qa_live_2026_10_04.py, test_journey_reporting_live_2026_10_04.py,
test_e2e_monday_workflow.py and test_final_e2e_acceptance.py.

Synthetic files under pytest tmp dirs; every row created is removed by the
`db` fixture borrowed from test_iqvia_routes.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

from app.tefca_registry import review_hub as hub
from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN  # noqa: F401
from test_iqvia_routes import (HCO_HEADER, _created_snapshot_ids,  # noqa: F401
                               _created_upload_ids, _FakeRequest,
                               _iqvia_drop_dir_is_tmp_path, _iqvia_sources,
                               _run_one_queued_job, _User, db, write_csv)

pytestmark = pytest.mark.regression

UPLOADER = "hub-flow-uploader@synthetic-test.docuaction.invalid"
APPROVER = "hub-flow-approver@synthetic-test.docuaction.invalid"


async def _hub_iqvia(db, role="reviewer"):
    out = await hub.overview(Response(), job_id=None, db=db, user=_User(role))
    return out["iqvia"]


def _synthetic_file(tmp_path, rows=6, tag="flow"):
    unique = uuid.uuid4().hex[:8]
    return write_csv(tmp_path / f"{tag}-{unique}.csv", HCO_HEADER, [
        [f"{SYN}-HUB-{unique}-{i}", "", "", f"{SYN} Hub Org {i}", "02101"]
        for i in range(rows)]).read_bytes()


async def _start(db, content, chunk_size, who=UPLOADER):
    start = await routes.start_upload(
        routes.StartUploadRequest(source="hco", label=f"{SYN}-hub-{uuid.uuid4().hex[:8]}",
                                  total_size=len(content), chunk_size=chunk_size),
        db, user=_User("reviewer", who))
    upload_id = uuid.UUID(start["upload_id"])
    _created_upload_ids.append(upload_id)
    return upload_id, start["total_chunks"]


class TestTheReferenceLegEndToEnd:
    async def test_upload_resume_stage_approve_compare(self, db, tmp_path, monkeypatch):
        _iqvia_sources(monkeypatch, True)
        uploader = _User("reviewer", UPLOADER)
        approver = _User("qalead", APPROVER)
        content = _synthetic_file(tmp_path)
        chunk = 53
        upload_id, total = await _start(db, content, chunk)
        assert total >= 4

        # 1. The browser closes after two chunks. Nothing is staged.
        for i in (0, 2):
            await routes.upload_chunk(upload_id, i, _FakeRequest(content[i * chunk:(i + 1) * chunk]),
                                      db, user=uploader)
        with pytest.raises(HTTPException) as exc:
            await routes.complete_upload(upload_id, db, user=uploader)
        assert exc.value.detail["code"] == "UPLOAD_INCOMPLETE"

        # 2. A new session asks what is missing and sends only that.
        status = await routes.upload_status(upload_id, db, user=uploader)
        missing = status["missing_chunk_indices"]
        assert missing == [i for i in range(total) if i not in (0, 2)] and not status["complete"]
        for i in missing:
            await routes.upload_chunk(upload_id, i, _FakeRequest(content[i * chunk:(i + 1) * chunk]),
                                      db, user=uploader)
        session = await jobs.get_upload_session(db, upload_id)
        assert Path(session.temp_path).read_bytes() == content, "source bytes preserved exactly"

        # 3. Complete: a receipt, a PENDING snapshot and a queued durable job.
        done = await routes.complete_upload(upload_id, db, user=uploader)
        snapshot_id = uuid.UUID(done["snapshot_id"])
        _created_snapshot_ids.append(snapshot_id)
        assert done["status"] == "PENDING" and done["job_state"] == um.IqviaImportJob.STATE_QUEUED
        assert (await _hub_iqvia(db))["status"]["state"] in ("IMPORT_INCOMPLETE",
                                                             "STAGED_APPROVAL_PENDING",
                                                             "APPROVED_NOT_CONSUMED",
                                                             "APPROVED_CONSUMPTION_ENABLED")

        # 4. Approval before the evidence exists is refused.
        with pytest.raises(HTTPException) as exc:
            await routes.approve(snapshot_id, routes.ApproveRequest(approval_ref=f"{SYN}-early"),
                                 db, user=approver)
        assert exc.value.status_code == 409 and exc.value.detail["code"] == "IMPORT_NOT_COMPLETE"

        # 5. Automatic processing, exactly as the scheduler runs it.
        assert await _run_one_queued_job(db) == um.IqviaImportJob.STATE_SUCCEEDED
        # The worker committed through its OWN session. In the application every request has a
        # fresh session; this test keeps one for the whole run, so it drops what it cached in step 4.
        db.expire_all()
        staged = await routes.snapshot_status(snapshot_id, db, user=uploader)
        assert staged["import_status"] == "completed"
        assert staged["import_summary"]["rows_staged"] == 6
        assert staged["sha256"] if "sha256" in staged else True
        pre = staged.get("reference_preflight")
        assert pre is not None and pre.get("gate") == "CLEAR", pre

        # 6. Who may NOT approve: the uploader's role, and the registrant even as QA Lead.
        body = routes.ApproveRequest(approval_ref=f"{SYN}-hub-approval")
        with pytest.raises(HTTPException) as exc:
            await routes.approve(snapshot_id, body, db, user=_User("qalead", UPLOADER))
        assert exc.value.detail["code"] == "MAKER_CHECKER_VIOLATION"
        still = await routes.snapshot_status(snapshot_id, db, user=uploader)
        assert still["status"] == sm.SNAPSHOT_PENDING

        # 7. Authorised approval by a second person.
        approved = await routes.approve(snapshot_id, body, db, user=approver)
        _created_snapshot_ids.append(uuid.UUID(approved["snapshot_id"]))
        assert approved["status"] == sm.SNAPSHOT_APPROVED
        inventory = (await _hub_iqvia(db))["status"]
        assert inventory["state"] in ("APPROVED_NOT_CONSUMED", "APPROVED_CONSUMPTION_ENABLED")
        assert inventory["affects_delivery_coverage"] is False

        # 8. Comparison: advisory, counts every staged observation, assigns no bucket.
        compared = await routes.match_snapshot(uuid.UUID(approved["snapshot_id"]), db=db,
                                               user=uploader)
        assert compared["observations_considered"] == 6
        assert "bucket" not in {k.lower() for k in compared}

        # 9. The same upload cannot be re-opened or re-staged.
        again = await routes.complete_upload(upload_id, db, user=uploader)
        assert again["already_completed"] is True and again["snapshot_id"] == done["snapshot_id"]


class TestWhereItMustStop:
    @pytest.mark.parametrize("role", ["viewer", "contributor", "manager"])
    async def test_a_role_below_reviewer_cannot_start_or_see_an_upload(self, db, tmp_path,
                                                                     monkeypatch, role):
        _iqvia_sources(monkeypatch, True)
        content = _synthetic_file(tmp_path, rows=2)
        with pytest.raises(HTTPException) as exc:
            await routes.start_upload(
                routes.StartUploadRequest(source="hco", label=f"{SYN}-denied",
                                          total_size=len(content), chunk_size=64),
                db, user=_User(role))
        assert exc.value.status_code == 403
        assert (await _hub_iqvia(db, role)) == {"availability": "requires_role:reviewer",
                                                "status": None, "page": "/tefca-arc/iqvia"}

    @pytest.mark.parametrize("role", ["reviewer", "senior_analyst"])
    async def test_a_reviewer_cannot_approve(self, db, tmp_path, monkeypatch, role):
        from test_official_delivery_workflow import _role_floors
        from app.core.security import ROLE_HIERARCHY

        floors = _role_floors(routes.router, "/snapshots/{snapshot_id}/approve", method="POST")
        assert floors and max(floors) == ROLE_HIERARCHY["qalead"]
        assert ROLE_HIERARCHY[role] < ROLE_HIERARCHY["qalead"]

    async def test_a_staging_job_that_fails_leaves_nothing_approvable(self, db, tmp_path,
                                                                     monkeypatch):
        _iqvia_sources(monkeypatch, True)
        uploader = _User("reviewer", UPLOADER)
        content = _synthetic_file(tmp_path, rows=3, tag="fail")
        upload_id, _total = await _start(db, content, len(content))
        await routes.upload_chunk(upload_id, 0, _FakeRequest(content), db, user=uploader)
        done = await routes.complete_upload(upload_id, db, user=uploader)
        snapshot_id = uuid.UUID(done["snapshot_id"])
        _created_snapshot_ids.append(snapshot_id)

        # The staged file disappears before the worker reaches it.
        session = await jobs.get_upload_session(db, upload_id)
        os.remove(session.temp_path)
        job = await jobs.claim_next_queued(db)
        # The queue is shared: an older QUEUED job left by an interrupted run would be
        # claimed instead, and every assertion below would describe the wrong job.
        assert job is not None and job.snapshot_id == snapshot_id, (
            "claimed a job that is not this test's; clear leftover QUEUED import jobs")
        await routes.run_import_job(str(job.id))
        db.expire_all()
        await db.refresh(job)
        assert job.state != um.IqviaImportJob.STATE_SUCCEEDED
        assert job.attempt_count >= 1

        status = await routes.snapshot_status(snapshot_id, db, user=uploader)
        assert status["status"] == sm.SNAPSHOT_PENDING
        assert status["import_status"] != "completed"
        with pytest.raises(HTTPException) as exc:
            await routes.approve(snapshot_id, routes.ApproveRequest(approval_ref=f"{SYN}-x"),
                                 db, user=_User("qalead", APPROVER))
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException):
            await routes.match_snapshot(snapshot_id, db=db, user=uploader)
