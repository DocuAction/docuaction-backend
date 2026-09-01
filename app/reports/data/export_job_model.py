"""The controlled export job row.

One table, in its own module for the same reason `artifact_registry` keeps its
model beside the code that uses it: the reports package owns its own storage and
does not reach into the TEFCA registry's models to describe a reporting concern.

THE CONCURRENCY GUARD IS THE INDEX, NOT THE CODE
────────────────────────────────────────────────
`uq_export_job_active_identity` is a PARTIAL unique index over
(identity) WHERE active_marker IS TRUE. It is the whole of the duplicate
protection. A SELECT-then-INSERT has a window between the two statements and a
disabled button has no bearing on a second browser tab; the index has neither
problem, and it holds if the deployment ever runs more than one worker.

`active_marker` is True while the job is in flight and NULL once it is terminal.
NULL rather than False deliberately: in PostgreSQL a partial index on
`active_marker IS TRUE` excludes NULL rows, so any number of finished jobs may
share an identity while at most one live job may.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Index, Integer, String, Text,
                        text)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ReportExportJob(Base):
    """One request to produce one controlled export."""

    __tablename__ = "report_export_jobs"

    #: Lifecycle. Four states, because a fifth would have to mean something a
    #: caller could act on differently and none does. QUEUED and RUNNING are
    #: "wait"; SUCCEEDED is "download"; FAILED is "look at the reason".
    STATE_QUEUED = "QUEUED"
    STATE_RUNNING = "RUNNING"
    STATE_SUCCEEDED = "SUCCEEDED"
    STATE_FAILED = "FAILED"

    ACTIVE_STATES = (STATE_QUEUED, STATE_RUNNING)
    TERMINAL_STATES = (STATE_SUCCEEDED, STATE_FAILED)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: What makes two requests the same request. See `export_jobs.job_identity`.
    identity = Column(String(64), nullable=False, index=True)
    export_type = Column(String(64), nullable=False)

    source_intake_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    classification = Column(String(32), nullable=False)
    generator_version = Column(String(128), nullable=False)

    state = Column(String(20), nullable=False, default=STATE_QUEUED, index=True)
    #: Where the run actually is, in words. Written only from real transitions;
    #: there is no percentage because nothing measures one.
    phase = Column(String(64))

    #: True while in flight, NULL when terminal. Keys the partial unique index.
    active_marker = Column(Boolean, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False,
                        index=True)
    started_at = Column(DateTime)
    #: Written repeatedly while work is in flight. A stale value is how the
    #: reaper tells "still working" from "worker died".
    heartbeat_at = Column(DateTime, index=True)
    completed_at = Column(DateTime)
    failed_at = Column(DateTime)

    attempt_count = Column(Integer, nullable=False, server_default=text("0"))
    error_reason = Column(Text)

    requested_by = Column(String(255), nullable=False)

    #: Populated on success only. A FAILED job names no artifact, because a
    #: partial workbook that can be downloaded is worse than one that cannot.
    report_id = Column(String(64), index=True)
    artifact_id = Column(String(128))
    artifact_version = Column(Integer)
    rendered_sha256 = Column(String(64))
    size_bytes = Column(Integer)

    __table_args__ = (
        Index("idx_export_job_state_heartbeat", "state", "heartbeat_at"),
        Index("idx_export_job_requested_by", "requested_by"),
        Index("uq_export_job_active_identity", "identity", "active_marker",
              unique=True, postgresql_where=text("active_marker IS TRUE")),
    )

    def to_dict(self):
        """The job as a caller may see it.

        Deliberately omits nothing sensitive because nothing sensitive is here:
        a job names an artifact, never where its bytes live. `error_reason` is a
        controlled string written by this application, not an exception's text.
        """
        return {
            "job_id": str(self.id),
            "state": self.state,
            "phase": self.phase,
            "export_type": self.export_type,
            "classification": self.classification,
            "generator_version": self.generator_version,
            "requested_by": self.requested_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (self.completed_at.isoformat()
                             if self.completed_at else None),
            "failed_at": self.failed_at.isoformat() if self.failed_at else None,
            "attempt_count": self.attempt_count,
            "error_reason": self.error_reason,
            "report_id": self.report_id,
            "artifact_version": self.artifact_version,
            "rendered_sha256": self.rendered_sha256,
            "size_bytes": self.size_bytes,
        }
