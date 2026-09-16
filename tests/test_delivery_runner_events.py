"""The delivery runner writes the durable timeline, the snapshot and the status.

Runs the recoverable stages (`_run_after_area1`) against a seeded synthetic
intake, and the Area 1 failure path (`run_delivery_job` with a missing preserved
file) so that FAILED is persisted rather than merely returned.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.core import request_context
from app.tefca_registry.rce import delivery_jobs as jobs
from app.tefca_registry.rce import delivery_runner, dispositions as disp
from app.tefca_registry.rce import reconciliation, stage_events
from app.tefca_registry.rce import traceability_models as tm
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import (  # noqa: F401
    NPI_REGISTERED, NPI_VALID_OTHER, SYN, make_rows, rolled_back_db, seed_entity,
    seed_intake,
)


async def _run_recoverable(db, job, intake_id, n):
    received = {"intake_id": str(intake_id), "record_count": n, "records_stored": n,
                "parse_ok": n, "parse_malformed": 0, "schema_drift": False,
                "every_line_stored": True}
    detail = {}
    # The harness skips Area 1 (the intake is seeded); write the two events the
    # real runner writes there so the stage set is the production one.
    with request_context.bind(job_id=job.id, attempt=1):
        for stage in ("SCHEMA_VALIDATION", "PARSING"):
            await stage_events.record_instant(db, job.id, stage, intake_id=intake_id,
                                              input_count=n, output_count=n)
        state = await delivery_runner._run_after_area1(db, job, detail, intake_id,
                                                       received, SYN)
    return state, detail


async def test_a_clean_delivery_writes_every_stage_event_and_is_ready(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.61")
    intake_id, job = await seed_intake(db, rows)

    state, detail = await _run_recoverable(db, job, intake_id, 3)
    assert state == RceDeliveryJob.STATE_SUCCEEDED

    await db.refresh(job)
    assert job.state == RceDeliveryJob.STATE_SUCCEEDED
    assert job.stage == RceDeliveryJob.STAGE_READY
    assert job.reconciliation_passed is True
    assert job.active_marker is None
    # stage_detail is still written for backward compatibility.
    assert job.stage_detail["PROMOTION"]["entities_created"] == 3
    assert job.stage_detail["RECONCILIATION"]["passed"] is True
    assert job.stage_detail["RECONCILIATION"]["snapshot_id"]
    assert job.stage_detail["CURATION"]["review_bridge"]["completed"] is True

    events = await stage_events.timeline(db, job.id)
    stages = [e["stage"] for e in events]
    # PROMOTION is opened before pass 1; MATCHING and RELATIONSHIPS are recorded
    # as instants from its counts once it returns, so they start after it.
    assert stages == ["SCHEMA_VALIDATION", "PARSING", "QUALITY", "CURATION", "PROMOTION",
                      "MATCHING", "RELATIONSHIPS", "VERIFICATION_READINESS",
                      "RECONCILIATION", "READY_FOR_REVIEW"], stages
    assert all(e["status"] == "COMPLETED" for e in events)
    assert all(e["intake_id"] == str(intake_id) for e in events)
    assert all(e["correlation_id"] == str(job.id) for e in events)
    by_stage = {e["stage"]: e for e in events}
    assert by_stage["QUALITY"]["output_count"] == 3
    assert by_stage["CURATION"]["output_count"] == 3
    assert by_stage["PROMOTION"]["output_count"] == 3
    assert by_stage["PROMOTION"]["detail"]["dispositions"]["CREATED"] == 3
    assert by_stage["RELATIONSHIPS"]["output_count"] == 3
    assert by_stage["RECONCILIATION"]["detail"]["passed"] is True
    assert by_stage["RECONCILIATION"]["output_count"] == 3
    assert by_stage["READY_FOR_REVIEW"]["detail"]["snapshot_id"]

    snapshot = await reconciliation.latest_snapshot(db, job.id)
    assert snapshot is not None and snapshot.passed and snapshot.trigger == "PIPELINE"
    assert snapshot.sequence == 1 and snapshot.created == 3
    assert str(snapshot.id) == by_stage["READY_FOR_REVIEW"]["detail"]["snapshot_id"]

    status = await jobs.status_for_job(db, job)
    assert status["processing_outcome"]["code"] == "COMPLETED_CLEAN", status
    assert status["review_state"]["code"] == "READY_FOR_ANALYST_REVIEW"
    assert status["inputs"]["records_without_disposition"] == 0
    assert job.to_dict()["stage"] == "READY_FOR_REVIEW"


async def test_a_delivery_with_a_conflict_completes_with_exceptions(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.62")
    rows[0]["NPI"] = NPI_VALID_OTHER
    await seed_entity(db, oid=rows[0]["id"], name=rows[0]["name"], npi=NPI_REGISTERED)
    intake_id, job = await seed_intake(db, rows)

    state, detail = await _run_recoverable(db, job, intake_id, 2)
    assert state == RceDeliveryJob.STATE_SUCCEEDED
    await db.refresh(job)
    assert job.reconciliation_passed is True, detail["RECONCILIATION"]
    assert job.stage == RceDeliveryJob.STAGE_READY

    events = {e["stage"]: e for e in await stage_events.timeline(db, job.id)}
    assert events["MATCHING"]["detail"]["conflicts_raised"] == 1
    assert events["PROMOTION"]["held_count"] == 1
    assert events["PROMOTION"]["detail"]["review_bridge"]["cases_created"] == 1, \
        "the conflict entered the work queue from the post-promotion bridge"
    counts = await disp.counts_for_intake(db, intake_id)
    assert counts["HELD"] == 1 and counts["CREATED"] == 1

    status = await jobs.status_for_job(db, job)
    assert status["processing_outcome"]["code"] == "COMPLETED_WITH_EXCEPTIONS"
    failed = {b["criterion"] for b in status["processing_outcome"]["basis"] if not b["held"]}
    assert failed == {"no_unresolved_findings", "no_held_records", "no_unresolved_conflicts"}
    assert status["review_state"]["code"] == "READY_FOR_ANALYST_REVIEW"
    assert status["inputs"]["review_counts"]["open"] == 1


async def test_a_stage_failure_closes_the_event_failed_and_fails_the_job(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.63")
    intake_id, job = await seed_intake(db, rows)

    async def boom(db_, intake_id_, actor):
        raise RuntimeError("synthetic curation failure")

    monkeypatch.setattr(delivery_runner, "_stage_curation", boom)
    state, detail = await _run_recoverable(db, job, intake_id, 1)
    assert state == RceDeliveryJob.STATE_FAILED

    await db.refresh(job)
    assert job.state == RceDeliveryJob.STATE_FAILED
    assert "CURATION did not complete: RuntimeError" in job.error_reason
    events = await stage_events.timeline(db, job.id)
    by_stage = {e["stage"]: e for e in events}
    assert by_stage["QUALITY"]["status"] == "COMPLETED"
    assert by_stage["CURATION"]["status"] == "FAILED"
    assert by_stage["CURATION"]["failure_class"] == "RuntimeError"
    assert "RECONCILIATION" in by_stage, "reconciliation still runs after a stage error"
    assert "READY_FOR_REVIEW" not in by_stage
    assert "PROMOTION" not in by_stage, "later stages do not run on an un-curated delivery"
    summary = stage_events.summarise(events)
    assert summary["failed_stage"] == "CURATION"
    status = await jobs.status_for_job(db, job)
    assert status["processing_outcome"]["code"] == "FAILED"
    assert status["processing_outcome"]["failed_stage"] == "CURATION"
    assert status["review_state"]["code"] == "NOT_READY"


async def test_reconciliation_not_passing_leaves_the_stage_at_reconciliation(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.64")
    intake_id, job = await seed_intake(db, rows)

    real = delivery_runner._stage_reconciliation

    async def not_passing(db_, intake_id_):
        summary, full = await real(db_, intake_id_)
        full = dict(full)
        full["passed"] = False
        full["checks"] = list(full["checks"]) + [
            {"check": "synthetic failing check", "passed": False, "detail": "x"}]
        full["failed_checks"] = [c for c in full["checks"] if not c["passed"]]
        summary = dict(summary, passed=False)
        return summary, full

    monkeypatch.setattr(delivery_runner, "_stage_reconciliation", not_passing)
    state, detail = await _run_recoverable(db, job, intake_id, 1)
    assert state == RceDeliveryJob.STATE_SUCCEEDED
    await db.refresh(job)
    assert job.reconciliation_passed is False
    assert job.stage == RceDeliveryJob.STAGE_RECONCILIATION, \
        "READY_FOR_REVIEW is written only when reconciliation passed"
    stages = [e["stage"] for e in await stage_events.timeline(db, job.id)]
    assert "READY_FOR_REVIEW" not in stages
    snapshot = await reconciliation.latest_snapshot(db, job.id)
    assert snapshot.passed is False and "synthetic failing check" in snapshot.failure_reason
    status = await jobs.status_for_job(db, job)
    assert status["processing_outcome"]["code"] == "PARTIALLY_PROCESSED"
    assert status["review_state"]["code"] == "NOT_READY"


async def test_area1_failure_persists_failed_with_both_events(rolled_back_db, tmp_path):
    db = rolled_back_db
    missing = tmp_path / "does-not-exist.txt"
    job = RceDeliveryJob(
        id=uuid.uuid4(), identity=uuid.uuid4().hex, delivery_label=f"{SYN}-MISSING",
        original_filename="missing.txt", storage_path=str(missing), sha256="0" * 64,
        file_size_bytes=1, state=RceDeliveryJob.STATE_RUNNING,
        stage=RceDeliveryJob.STAGE_PARSING, active_marker=True, registered_by=SYN,
        created_at=datetime.utcnow(), started_at=datetime.utcnow(),
        heartbeat_at=datetime.utcnow(), attempt_count=1, stage_detail={})
    db.add(job)
    await db.commit()

    state = await delivery_runner.run_delivery_job(db, job)
    assert state == RceDeliveryJob.STATE_FAILED
    await db.refresh(job)
    assert job.state == RceDeliveryJob.STATE_FAILED
    assert job.error_reason.startswith("PARSING did not complete: FileNotFoundError")
    assert job.active_marker is None
    events = {e["stage"]: e for e in await stage_events.timeline(db, job.id)}
    assert set(events) == {"SCHEMA_VALIDATION", "PARSING"}
    assert all(e["status"] == "FAILED" for e in events.values())
    assert all(e["correlation_id"] == str(job.id) for e in events.values())
    assert events["PARSING"]["failure_class"] == "FileNotFoundError"


async def test_runner_exception_path_persists_failed(rolled_back_db, monkeypatch):
    """The outer guard must write FAILED, not just return it."""
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.65")
    intake_id, job = await seed_intake(db, rows)

    async def explode(db_, job_, detail_):
        raise RuntimeError("runner blew up before any stage")

    monkeypatch.setattr(delivery_runner, "_run_stages", explode)
    state = await delivery_runner.run_delivery_job(db, job)
    assert state == RceDeliveryJob.STATE_FAILED
    await db.refresh(job)
    assert job.state == RceDeliveryJob.STATE_FAILED
    assert "RUNNER did not complete: RuntimeError" in job.error_reason
