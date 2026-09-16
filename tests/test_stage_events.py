"""Stage events: one row per attempt, never overwritten; timeline; summarise."""

from __future__ import annotations

import pytest

from app.core import request_context
from app.tefca_registry.rce import stage_events
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401


async def test_repeated_attempts_are_distinct_rows(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await seed_intake(db, make_rows(1))

    first = await stage_events.open_stage(db, job.id, "QUALITY", intake_id=intake_id,
                                          input_count=1)
    await stage_events.close_stage(db, first, "FAILED",
                                   failure=ValueError("rule engine raised"))
    second = await stage_events.open_stage(db, job.id, "QUALITY", intake_id=intake_id,
                                           input_count=1)
    await stage_events.close_stage(db, second, "COMPLETED", output_count=1,
                                   warning_count=0)

    events = await stage_events.timeline(db, job.id)
    quality = [e for e in events if e["stage"] == "QUALITY"]
    assert [e["attempt"] for e in quality] == [1, 2]
    assert quality[0]["status"] == "FAILED"
    assert quality[0]["failure_class"] == "ValueError"
    assert "rule engine raised" in quality[0]["failure_reason"]
    assert quality[1]["status"] == "COMPLETED"
    assert quality[1]["failure_class"] is None
    assert quality[0]["id"] != quality[1]["id"], "a retry is a new row, not a rewrite"


async def test_failed_close_records_class_reason_and_duration(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await seed_intake(db, make_rows(1))

    ev = await stage_events.open_stage(db, job.id, "PROMOTION", intake_id=intake_id)
    closed = await stage_events.close_stage(db, ev, "FAILED",
                                            failure=ValueError("x" * 3000))
    d = closed.to_dict()
    assert d["status"] == "FAILED"
    assert d["failure_class"] == "ValueError"
    assert len(d["failure_reason"]) == 2000, "reason is truncated, never dropped"
    assert d["failure_reason"].startswith("ValueError: xxx")
    # A driver/library exception keeps its class only: its message carries SQL,
    # parameters and delivered values (independent review F3, 2026-09-16).
    from sqlalchemy.exc import IntegrityError
    ev2 = await stage_events.open_stage(db, job.id, "PROMOTION", intake_id=intake_id)
    closed2 = await stage_events.close_stage(
        db, ev2, "FAILED", failure=IntegrityError(
            "INSERT INTO t VALUES (1982916078)", {"npi": "1982916078"},
            Exception("Key (npi)=(1982916078)")))
    d2 = closed2.to_dict()
    assert d2["failure_class"] == "IntegrityError"
    assert "1982916078" not in d2["failure_reason"] and "INSERT" not in d2["failure_reason"]
    assert d["duration_ms"] is not None and d["duration_ms"] >= 0
    assert d["completed_at"] >= d["started_at"]


async def test_a_started_status_cannot_close_a_stage(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await seed_intake(db, make_rows(1))
    ev = await stage_events.open_stage(db, job.id, "CURATION", intake_id=intake_id)
    with pytest.raises(ValueError):
        await stage_events.close_stage(db, ev, "STARTED")
    with pytest.raises(ValueError):
        await stage_events.close_stage(db, ev, "DONE")


async def test_timeline_is_in_start_order_and_summarise_reads_it(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await seed_intake(db, make_rows(1))

    await stage_events.record_instant(db, job.id, "REGISTERED", intake_id=intake_id)
    q = await stage_events.open_stage(db, job.id, "QUALITY", intake_id=intake_id)
    await stage_events.close_stage(db, q, "COMPLETED")
    c = await stage_events.open_stage(db, job.id, "CURATION", intake_id=intake_id)
    await stage_events.close_stage(db, c, "COMPLETED")
    p = await stage_events.open_stage(db, job.id, "PROMOTION", intake_id=intake_id)
    await stage_events.close_stage(db, p, "FAILED", failure=RuntimeError("no"))

    events = await stage_events.timeline(db, job.id)
    assert [e["stage"] for e in events] == ["REGISTERED", "QUALITY", "CURATION",
                                            "PROMOTION"]
    starts = [e["started_at"] for e in events]
    assert starts == sorted(starts)

    summary = stage_events.summarise(events)
    assert summary["attempts"] == 4
    assert summary["failed_stage"] == "PROMOTION"
    assert summary["completed_stages"] == ["CURATION", "QUALITY", "REGISTERED"]
    assert summary["latest_by_stage"]["PROMOTION"]["status"] == "FAILED"


async def test_summarise_uses_the_latest_attempt_per_stage(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await seed_intake(db, make_rows(1))
    a = await stage_events.open_stage(db, job.id, "RECONCILIATION", intake_id=intake_id)
    await stage_events.close_stage(db, a, "FAILED", failure=RuntimeError("first"))
    b = await stage_events.open_stage(db, job.id, "RECONCILIATION", intake_id=intake_id)
    await stage_events.close_stage(db, b, "COMPLETED", output_count=1)

    summary = stage_events.summarise(await stage_events.timeline(db, job.id))
    latest = summary["latest_by_stage"]["RECONCILIATION"]
    assert latest["attempt"] == 2 and latest["status"] == "COMPLETED"
    assert "RECONCILIATION" in summary["completed_stages"]


async def test_events_carry_the_bound_correlation_and_build(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await seed_intake(db, make_rows(1))
    with request_context.bind(job_id=job.id, stage="QUALITY", attempt=1):
        ev = await stage_events.open_stage(db, job.id, "QUALITY", intake_id=intake_id)
    assert ev.correlation_id == str(job.id)
    assert ev.build_sha == request_context.build_sha()
    assert ev.worker_id


async def test_summarise_on_an_empty_timeline():
    assert stage_events.summarise([]) == {"latest_by_stage": {}, "failed_stage": None,
                                          "completed_stages": [], "attempts": 0}
