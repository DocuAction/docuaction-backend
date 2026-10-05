"""Durable state for controlled, bounded rechecks (2026-10-04, Part B).

Same pattern as `iqvia_upload_models.IqviaImportJob` and
`report_export_jobs`: every fact in the database, a unique key makes a
repeated trigger return the SAME job, item rows make a crash resume exact,
a heartbeat plus a reaper turn "the worker died" into a bounded retry.

    rce_recheck_job    one row per (delivery, trigger, source, trigger ref).
                       `idempotency_key` is UNIQUE: the same trigger twice is
                       one job, never two.
    rce_recheck_item   one row per targeted entity. UNIQUE (job_id,
                       entity_id). An item is processed at most once; its
                       state is committed in the same transaction as the
                       evidence it produced.

Neither table holds a Government-delivered value: references, states,
counts and hashes only.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# Why a recheck is being asked for.
TRIGGER_SOURCE_RECOVERY = "SOURCE_RECOVERY"
TRIGGER_NEW_APPROVED_SNAPSHOT = "NEW_APPROVED_SNAPSHOT"
TRIGGER_APPROVED_MAPPING_CHANGE = "APPROVED_MAPPING_CHANGE"
TRIGGER_KINDS = (TRIGGER_SOURCE_RECOVERY, TRIGGER_NEW_APPROVED_SNAPSHOT,
                 TRIGGER_APPROVED_MAPPING_CHANGE)

STATE_PENDING_APPROVAL = "PENDING_APPROVAL"   # requested; a DIFFERENT person must approve
STATE_QUEUED = "QUEUED"
STATE_RUNNING = "RUNNING"
STATE_SUCCEEDED = "SUCCEEDED"
STATE_STOPPED_SOURCE_UNAVAILABLE = "STOPPED_SOURCE_UNAVAILABLE"
STATE_REFUSED_STALE = "REFUSED_STALE"
STATE_FAILED = "FAILED"
JOB_STATES = (STATE_PENDING_APPROVAL, STATE_QUEUED, STATE_RUNNING, STATE_SUCCEEDED,
              STATE_STOPPED_SOURCE_UNAVAILABLE, STATE_REFUSED_STALE, STATE_FAILED)
TERMINAL_STATES = (STATE_SUCCEEDED, STATE_REFUSED_STALE, STATE_FAILED)

ITEM_PENDING = "PENDING"
ITEM_DONE = "DONE"
ITEM_ERROR = "ERROR"

# What re-evaluation found for the rechecked source on one entity. None of
# these is a compliance approval.
OUTCOME_ANSWERED_NO_SIGNAL = "ANSWERED_NO_SIGNAL"
OUTCOME_RISK_SIGNAL = "RISK_SIGNAL"
OUTCOME_STILL_UNAVAILABLE = "STILL_UNAVAILABLE"
OUTCOME_NOT_RESOLVED = "ENTITY_NOT_RESOLVED"
ITEM_OUTCOMES = (OUTCOME_ANSWERED_NO_SIGNAL, OUTCOME_RISK_SIGNAL,
                 OUTCOME_STILL_UNAVAILABLE, OUTCOME_NOT_RESOLVED)


class RceRecheckJob(Base):
    __tablename__ = "rce_recheck_job"

    MAX_ATTEMPTS = 3

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                       nullable=False)
    trigger_kind = Column(String(40), nullable=False)
    source_id = Column(String(64), nullable=False)        # source_policy id
    trigger_ref = Column(String(255), nullable=False)     # snapshot id / mapping version / recovery ref
    idempotency_key = Column(String(64), nullable=False)
    state = Column(String(40), nullable=False, default=STATE_PENDING_APPROVAL)

    requested_by = Column(String(320), nullable=False)
    requested_by_id = Column(String(64))
    rationale = Column(Text, nullable=False)
    approved_by = Column(String(320))
    approved_by_id = Column(String(64))
    approved_at = Column(DateTime)

    #: Versions and the baseline this job was approved against.
    pinned = Column(JSONB, nullable=False, default=dict)
    baseline_hash = Column(String(64), nullable=False)

    target_count = Column(Integer, nullable=False, default=0)
    untargeted_remaining = Column(Integer, nullable=False, default=0)
    processed_count = Column(Integer, nullable=False, default=0)
    batch_size = Column(Integer, nullable=False, default=50)
    attempt_count = Column(Integer, nullable=False, default=0)
    summary = Column(JSONB, nullable=False, default=dict)
    error_reason = Column(Text)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime)
    heartbeat_at = Column(DateTime)
    completed_at = Column(DateTime)
    correlation_id = Column(String(64))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_rce_recheck_job_idempotency"),
        Index("idx_rce_recheck_job_state", "state", "heartbeat_at"),
        Index("idx_rce_recheck_job_intake", "intake_id"),
    )


class RceRecheckItem(Base):
    __tablename__ = "rce_recheck_item"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True),
                    ForeignKey("rce_recheck_job.id", ondelete="RESTRICT"), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    entity_ref = Column(String(255), nullable=False)
    state = Column(String(16), nullable=False, default=ITEM_PENDING)
    prior_evidence_id = Column(UUID(as_uuid=True))
    prior_disposition = Column(String(32))
    new_disposition = Column(String(32))
    outcome = Column(String(32))
    detail = Column(JSONB, nullable=False, default=dict)
    processed_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("job_id", "entity_id", name="uq_rce_recheck_item_job_entity"),
        Index("idx_rce_recheck_item_job_state", "job_id", "state"),
    )
