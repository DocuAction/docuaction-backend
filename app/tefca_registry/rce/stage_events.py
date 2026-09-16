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
        event.failure_reason = (failure_reason or str(failure))[:2000]
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
