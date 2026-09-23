"""
Durable stage events for delivery jobs.

Every stage attempt is a row: opened STARTED, closed COMPLETED / FAILED /
SKIPPED. A retried stage gets attempt+1; nothing is overwritten. Instant
events (REGISTERED, SHA256, ...) are written already closed.

All helpers take the caller's session and commit, so an event is visible to a
watching operator as soon as it happens rather than at the end of the job.
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.core import request_context
from app.tefca_registry.rce import traceability_models as tm

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:128]


def safe_failure_text(exc: BaseException, limit: int = 2000) -> str:
    """Failure text for stage events and job detail: domain messages redacted,
    driver/library exceptions reduced to their class name. Served at viewer
    floor, so it must never carry SQL, parameters or delivered values."""
    from app.core.logging_config import safe_exception_text
    return safe_exception_text(exc, limit)


async def _next_attempt(db, job_id, stage: str) -> int:
    current = (await db.execute(
        select(func.max(tm.RceDeliveryStageEvent.attempt)).where(
            tm.RceDeliveryStageEvent.job_id == job_id,
            tm.RceDeliveryStageEvent.stage == stage))).scalar()
    return int(current or 0) + 1


async def open_stage(db, job_id, stage: str, *, intake_id=None,
                     input_count: Optional[int] = None,
                     detail: Optional[Dict[str, Any]] = None,
                     commit: bool = True) -> tm.RceDeliveryStageEvent:
    """Record that an attempt of `stage` has started."""
    event = tm.RceDeliveryStageEvent(
        job_id=job_id, intake_id=intake_id, stage=stage,
        attempt=await _next_attempt(db, job_id, stage),
        status="STARTED", started_at=_now(), input_count=input_count,
        detail=dict(detail or {}),
        correlation_id=request_context.correlation_id()[:64],
        worker_id=worker_id(), build_sha=request_context.build_sha())
    db.add(event)
    if commit:
        await db.commit()
    else:
        await db.flush()
    logger.info("stage started", extra={"stage": stage, "attempt": event.attempt,
                                        "job_id": str(job_id)})
    return event


async def close_stage(db, event: tm.RceDeliveryStageEvent, status: str, *,
                      input_count: Optional[int] = None,
                      output_count: Optional[int] = None,
                      warning_count: Optional[int] = None,
                      held_count: Optional[int] = None,
                      rejected_count: Optional[int] = None,
                      failure: Optional[BaseException] = None,
                      failure_reason: Optional[str] = None,
                      detail: Optional[Dict[str, Any]] = None,
                      commit: bool = True) -> tm.RceDeliveryStageEvent:
    """Close an attempt. `status` is COMPLETED, FAILED or SKIPPED."""
    if status not in tm.STAGE_EVENT_STATUSES or status == "STARTED":
        raise ValueError(f"cannot close a stage with status {status!r}")
    # The event may belong to a session that has since been rolled back; re-attach.
    event = await db.merge(event)
    # APP-DEFECT-001 (2026-09-23): `merge()` on an instance that is already
    # THIS session's own tracked copy of the row (the common case here - `ev`
    # was opened, committed once, then the PROMOTION follow-on work
    # (snapshot_effects/record_instant x2/review bridge) ran zero or more of
    # its OWN commits and rollbacks on this SAME shared session before this
    # close call) takes a fast path that returns the object as-is without
    # reloading it, even though every intervening commit/rollback on the
    # session already expired its attributes. The very next line used to read
    # `event.started_at` as a bare (non-awaited) attribute access; on an
    # expired attribute that raises `sqlalchemy.exc.MissingGreenlet`
    # ("greenlet_spawn has not been called") because the lazy load it
    # triggers happens outside the async greenlet SQLAlchemy's asyncio
    # extension sets up around awaited calls - which then leaves the session
    # in PendingRollbackError for every later statement. Reproduced end to
    # end via a duplicate-source-id delivery (SnapshotRefused during
    # PROMOTION's follow-on work): the job never reached a terminal state,
    # matching the fx1/fx3/fx1-retry 2026-09-21 incidents exactly. Loading
    # this column (and every other one this function and `to_dict()` below
    # read - `stage`, `attempt`, `job_id`, `intake_id`, `id`, ... - all the
    # same risk) through one explicit, awaited full refresh removes the bare
    # synchronous read entirely; it does not touch commit/rollback behaviour
    # or ownership of the caller's transaction anywhere else.
    await db.refresh(event)
    event.status = status
    event.completed_at = _now()
    if event.completed_at < event.started_at:
        event.completed_at = event.started_at
    if input_count is not None:
        event.input_count = input_count
    event.output_count = output_count
    event.warning_count = warning_count
    event.held_count = held_count
    event.rejected_count = rejected_count
    if failure is not None:
        event.failure_class = type(failure).__name__[:128]
        event.failure_reason = (failure_reason or safe_failure_text(failure))[:2000]
    elif failure_reason:
        event.failure_reason = failure_reason[:2000]
    if detail:
        merged = dict(event.detail or {})
        merged.update(detail)
        event.detail = merged
    if commit:
        await db.commit()
    else:
        await db.flush()
    logger.log(logging.ERROR if status == "FAILED" else logging.INFO,
               "stage %s", status.lower(),
               extra={"stage": event.stage, "attempt": event.attempt,
                      "job_id": str(event.job_id), "status": status,
                      "duration_ms": event.to_dict()["duration_ms"]})
    return event


async def close_dangling_started(db, job_id, stage: str, status: str = "COMPLETED",
                                 **kwargs) -> Optional[tm.RceDeliveryStageEvent]:
    """Close the newest STARTED attempt of `stage` for one job, if one exists.

    For a job the reaper is recovering: its worker died mid-stage, so the
    event never got its own close call and would otherwise sit at STARTED
    forever - a permanently dangling row even after the job itself reaches a
    terminal state.
    """
    event = (await db.execute(
        select(tm.RceDeliveryStageEvent)
        .where(tm.RceDeliveryStageEvent.job_id == job_id,
               tm.RceDeliveryStageEvent.stage == stage,
               tm.RceDeliveryStageEvent.status == "STARTED")
        .order_by(tm.RceDeliveryStageEvent.attempt.desc())
        .limit(1))).scalar_one_or_none()
    if event is None:
        return None
    return await close_stage(db, event, status, **kwargs)


async def record_instant(db, job_id, stage: str, status: str = "COMPLETED", *,
                         intake_id=None, input_count: Optional[int] = None,
                         output_count: Optional[int] = None,
                         detail: Optional[Dict[str, Any]] = None,
                         failure_reason: Optional[str] = None,
                         commit: bool = True) -> tm.RceDeliveryStageEvent:
    """A stage that starts and ends in one act (REGISTERED, SHA256, ...)."""
    now = _now()
    event = tm.RceDeliveryStageEvent(
        job_id=job_id, intake_id=intake_id, stage=stage,
        attempt=await _next_attempt(db, job_id, stage),
        status=status, started_at=now, completed_at=now,
        input_count=input_count, output_count=output_count,
        failure_reason=failure_reason, detail=dict(detail or {}),
        correlation_id=request_context.correlation_id()[:64],
        worker_id=worker_id(), build_sha=request_context.build_sha())
    db.add(event)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return event


async def timeline(db, job_id) -> List[Dict[str, Any]]:
    """Every attempt of every stage for one job, oldest first."""
    rows = (await db.execute(
        select(tm.RceDeliveryStageEvent)
        .where(tm.RceDeliveryStageEvent.job_id == job_id)
        .order_by(tm.RceDeliveryStageEvent.started_at,
                  tm.RceDeliveryStageEvent.attempt))).scalars().all()
    return [row.to_dict() for row in rows]


def summarise(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Latest attempt per stage, the failed stage if any, and completed set."""
    latest: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        latest[ev["stage"]] = ev  # ordered oldest→newest, so the last wins
    failed = next((ev["stage"] for ev in reversed(events) if ev["status"] == "FAILED"),
                  None)
    completed = sorted(s for s, ev in latest.items() if ev["status"] == "COMPLETED")
    return {"latest_by_stage": latest, "failed_stage": failed,
            "completed_stages": completed, "attempts": len(events)}
