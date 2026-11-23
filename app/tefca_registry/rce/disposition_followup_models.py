"""Durable state for the delivery-level work that follows an analyst decision.

WHY THIS TABLE EXISTS (2026-11-18)
──────────────────────────────────
A decision that changes a record's status has a delivery-wide consequence:
re-promotion, full reconciliation and a persisted snapshot. Until now that ran
inside the browser's save request (DEV logs 2026-11-16..18: 51 s alone, 78 s
when two overlapped). Moving it out of the request is only safe if the accepted
work is PERSISTED before the request is acknowledged, so that a closed browser,
a lost response or a restarted worker cannot lose it. That fact has to live in
the database; this is where.

Same pattern as `rce_delivery_jobs` and `rce_recheck_job`: every fact in a row,
a heartbeat plus a reaper turn "the worker died" into a bounded retry, and a
unique key makes a repeated submission return the SAME row.

It is NOT the delivery-registration job. `delivery_job_id` is a reference to
that job (snapshots belong to it); this table never re-queues or mutates it.

It holds no Government-delivered value. `decision_reason` is the reviewer's
own text, already stored on the issue; it is carried here only so the
disposition event the worker writes says exactly what a synchronous save would
have said.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

TABLE = "rce_disposition_followups"

#: The decision is being applied inside the request that submitted it.
STATE_RECEIVED = "RECEIVED"
#: The decision is recorded; delivery-level processing is waiting for the worker.
STATE_QUEUED = "QUEUED"
#: A worker holds the delivery and is promoting / reconciling.
STATE_PROCESSING = "PROCESSING"
#: Promotion, reconciliation and the snapshot are done.
STATE_COMPLETED = "COMPLETED"
#: Processing could not be completed; `error_code` says why, safely.
STATE_FAILED = "FAILED"
#: The decision itself was refused after part of it had been recorded.
STATE_REFUSED = "REFUSED"
#: The decision changed no record status, so there was nothing to process.
STATE_NOT_REQUIRED = "NOT_REQUIRED"

STATES = (
    STATE_RECEIVED,
    STATE_QUEUED,
    STATE_PROCESSING,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_REFUSED,
    STATE_NOT_REQUIRED,
)
#: Work that is accepted and not finished. A reconciliation snapshot is not
#: current while any row for the delivery is in one of these.
PENDING_STATES = (STATE_RECEIVED, STATE_QUEUED, STATE_PROCESSING)
TERMINAL_STATES = (STATE_COMPLETED, STATE_FAILED, STATE_REFUSED, STATE_NOT_REQUIRED)

KIND_ISSUE_DISPOSITION = "ISSUE_DISPOSITION"
KIND_IDENTIFIER_DECISION = "IDENTIFIER_DECISION"
KINDS = (KIND_ISSUE_DISPOSITION, KIND_IDENTIFIER_DECISION)

#: How the disposition event for the released record is attributed. HUMAN is
#: what `curation.apply_disposition` has always written; SYSTEM is what the
#: `/identifier-decisions` follow-up has always written. Deferral keeps both.
ATTRIBUTION_HUMAN = "HUMAN"
ATTRIBUTION_SYSTEM = "SYSTEM"


class RceDispositionFollowup(Base):
    __tablename__ = TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(String(24), nullable=False, default=KIND_ISSUE_DISPOSITION)
    intake_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False
    )
    #: The registration job the snapshot will belong to. Reference only.
    delivery_job_id = Column(UUID(as_uuid=True))
    issue_id = Column(UUID(as_uuid=True), ForeignKey("rce_issues.id", ondelete="RESTRICT"))
    source_record_id = Column(UUID(as_uuid=True))

    decision = Column(String(32), nullable=False)
    decision_reason = Column(Text)
    attribution = Column(String(8), nullable=False, default=ATTRIBUTION_HUMAN)
    actor = Column(String(320), nullable=False)
    actor_id = Column(UUID(as_uuid=True))

    #: Client-supplied. UNIQUE with the finding: the same key twice is one row.
    idempotency_key = Column(String(128))
    #: sha256 of (decision, reason, corrected value): the same key with a
    #: different payload is refused, never answered with the stored row.
    payload_fingerprint = Column(String(64))

    state = Column(String(16), nullable=False, default=STATE_RECEIVED)
    batch_id = Column(UUID(as_uuid=True))
    attempt_count = Column(Integer, nullable=False, default=0)
    recovered = Column(Boolean, nullable=False, default=False)

    record_status_before = Column(String(16))
    record_status_after = Column(String(16))
    #: The synchronous response body, so a repeated submission can be answered
    #: with what the first one was told. References and states only.
    decision_result = Column(JSONB)
    snapshot_id = Column(UUID(as_uuid=True))
    snapshot_passed = Column(Boolean)
    disposition_event_id = Column(UUID(as_uuid=True))
    #: Enumerated and safe to show (see `disposition_followups.SAFE_EXPLANATIONS`).
    error_code = Column(String(48))
    #: Exception class and stage only; for operators, never shown to a reviewer.
    error_detail = Column(Text)

    received_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    queued_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    heartbeat_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    failed_at = Column(DateTime(timezone=True))
    correlation_id = Column(String(64))
    build_sha = Column(String(40))

    __table_args__ = (
        CheckConstraint(
            "state IN ('RECEIVED','QUEUED','PROCESSING','COMPLETED','FAILED',"
            "'REFUSED','NOT_REQUIRED')",
            name="ck_rce_disp_followup_state",
        ),
        CheckConstraint(
            "kind IN ('ISSUE_DISPOSITION','IDENTIFIER_DECISION')", name="ck_rce_disp_followup_kind"
        ),
        CheckConstraint(
            "attribution IN ('HUMAN','SYSTEM')", name="ck_rce_disp_followup_attribution"
        ),
        Index(
            "uq_rce_disp_followup_issue_key",
            "issue_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND issue_id IS NOT NULL"),
        ),
        Index(
            "ix_rce_disp_followup_pending",
            "intake_id",
            "state",
            "received_at",
            postgresql_where=text("state IN ('RECEIVED','QUEUED','PROCESSING')"),
        ),
        Index("ix_rce_disp_followup_intake", "intake_id", "received_at"),
        Index("ix_rce_disp_followup_issue", "issue_id", "received_at"),
        Index("ix_rce_disp_followup_record", "source_record_id", "received_at"),
    )
