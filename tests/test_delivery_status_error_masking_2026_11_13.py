"""A failed delivery's stored failure reason is masked wherever it is returned.

`RceDeliveryJob.to_dict()` has masked `error_reason` since PR #85. The derived
processing outcome embedded the RAW column in its `detail` sentence, and the
delivery dashboard returned it as-is, so a row written before that change (a
driver's rendering of the failed statement and its parameters) still reached
the browser through the delivery list, the job detail and the dashboard.

Synthetic rows only; the database tests run in a transaction that is rolled back.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi import Response

from app.core.logging_config import sanitize_stored_error
from app.tefca_registry.rce import delivery_dashboard as dash
from app.tefca_registry.rce import delivery_jobs as jobs
from app.tefca_registry.rce import delivery_routes as routes
from app.tefca_registry.rce import status_model
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401

SECRET = "SYNTHETIC SECRET VALUE 4711"
DRIVER_SECRET = "SYNTHETIC DRIVER MESSAGE 9257"
RAW = ("PROMOTION: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class "
       f"'asyncpg.exceptions.UniqueViolationError'>: duplicate key {DRIVER_SECRET}\n[SQL: INSERT INTO "
       f"tefca_entities (name) VALUES ($1)]\n[parameters: ('{SECRET}',)]")
DRIVER_ONLY = ("<class 'asyncpg.exceptions.UniqueViolationError'>: " + DRIVER_SECRET)
CONTROLLED = "PARSING: ValueError: header row is missing 3 documented columns"


class _User:
    def __init__(self, role):
        self.role = role
        self.email = f"mask-{role}@synthetic-test.docuaction.invalid"
        self.id = None


def test_the_fixture_is_something_the_sanitizer_masks():
    assert SECRET in RAW and SECRET not in sanitize_stored_error(RAW)
    assert DRIVER_SECRET in RAW and DRIVER_SECRET not in sanitize_stored_error(RAW)
    assert sanitize_stored_error(CONTROLLED) == CONTROLLED


class TestTheDerivationMasksWhatItEmbeds:
    def test_a_driver_payload_is_not_in_the_failed_outcome(self):
        out = status_model.processing_outcome(job_state="FAILED", job_stage="PROMOTION",
                                              error_reason=RAW, snapshot=None)
        text = json.dumps(out)
        assert DRIVER_SECRET not in text
        assert SECRET not in text and "[parameters" not in text and "[SQL" not in text
        assert out["value"] == status_model.OUTCOME_FAILED and "PROMOTION" in out["detail"]

    def test_a_controlled_reason_is_shown_unchanged(self):
        out = status_model.processing_outcome(job_state="FAILED", job_stage="PARSING",
                                              error_reason=CONTROLLED, snapshot=None)
        assert out["detail"] == f"Processing stopped at PARSING: {CONTROLLED}."

    def test_no_reason_still_reads_as_before(self):
        out = status_model.processing_outcome(job_state="FAILED", job_stage="QUALITY",
                                              error_reason=None, snapshot=None)
        assert out["detail"] == "Processing stopped at QUALITY: no reason recorded."

    def test_other_outcomes_are_untouched(self):
        for state in ("QUEUED", "RUNNING"):
            out = status_model.processing_outcome(job_state=state, job_stage="QUALITY",
                                                  error_reason=RAW, snapshot=None)
            assert SECRET not in json.dumps(out)

    def test_the_dashboard_state_masks_it_too(self):
        class _Job:
            state, stage, error_reason = "FAILED", "PROMOTION", RAW

        out = dash._status(_Job(), None)
        assert out["state"] == "FAILED" and SECRET not in json.dumps(out)
        assert DRIVER_SECRET not in json.dumps(out)


@pytest.fixture
async def failed_job(rolled_back_db):  # noqa: F811
    db = rolled_back_db
    _intake, job = await seed_intake(db, make_rows(2))
    job.state = RceDeliveryJob.STATE_FAILED
    job.active_marker = None
    job.failed_at = datetime.utcnow()
    job.error_reason = RAW
    await db.commit()
    jobs.reset_list_status_cache()
    return db, job


@pytest.mark.regression
class TestTheRoutesThatReturnIt:
    @pytest.mark.parametrize("role", ["viewer", "reviewer", "program_manager", "admin"])
    async def test_delivery_list(self, failed_job, role):
        db, job = failed_job
        out = await routes.list_delivery_jobs(limit=50, offset=0, state="FAILED", db=db,
                                              user=_User(role))
        mine = [i for i in out["items"] if i["job_id"] == str(job.id)]
        assert mine and mine[0]["processing_outcome"]["value"] == status_model.OUTCOME_FAILED
        text = json.dumps(out, default=str)
        assert DRIVER_SECRET not in text
        assert SECRET not in text and "[parameters" not in text, role

    @pytest.mark.parametrize("role", ["viewer", "reviewer", "admin"])
    async def test_job_and_job_detail(self, failed_job, role):
        db, job = failed_job
        one = await routes.get_delivery_job(str(job.id), db=db, user=_User(role))
        assert SECRET not in json.dumps(one, default=str)
        status = await jobs.status_for_job(db, job)
        assert SECRET not in json.dumps(status, default=str)
        detail_fn = next(r.endpoint for r in routes.router.routes
                         if r.path.endswith("/delivery-jobs/{job_id}/detail"))
        import inspect
        kwargs = {}
        for name, param in inspect.signature(detail_fn).parameters.items():
            if name in ("job_id", "ident"):
                kwargs[name] = str(job.id)
            elif name == "db":
                kwargs[name] = db
            elif name == "user":
                kwargs[name] = _User(role)
            elif name == "response":
                kwargs[name] = Response()
            elif param.default is not inspect.Parameter.empty:
                default = param.default
                kwargs[name] = getattr(default, "default", default)
        detail = await detail_fn(**kwargs)
        body = detail.body.decode() if hasattr(detail, "body") else json.dumps(detail, default=str)
        assert str(job.id) in body, "the detail answered for this job"
        assert SECRET not in body and "[parameters" not in body, role


@pytest.mark.parametrize("payload", [RAW, DRIVER_ONLY])
def test_driver_message_is_masked_independently_of_sql_parameter_stripping(payload):
    derived = status_model.processing_outcome(job_state="FAILED", job_stage="PROMOTION",
        error_reason=payload, snapshot=None)
    job = type("Job", (), {"state": "FAILED", "stage": "PROMOTION", "error_reason": payload})()
    dashboard = dash._status(job, None)
    for result in (derived, dashboard):
        assert DRIVER_SECRET not in json.dumps(result)
        assert SECRET not in json.dumps(result)
