"""Durable state for the IQVIA chunked upload + staging journey.

WHY THIS EXISTS
----------------
The original chunked-upload design (`iqvia_routes.py`, 2026-10-02) kept
`_UPLOAD_STATE` -- which chunks had arrived, the staging job -- in this
process's memory. That survives a retry or a dropped connection; it does NOT
survive the server process restarting mid-upload or mid-import, which is
exactly the failure a multi-gigabyte, minutes-to-hours-long transfer is most
likely to hit. This module replaces that in-memory dict with the same
pattern `app/reports/data/export_job_model.py` already proved for controlled
exports: every fact lives in the database, a partial unique index makes
concurrent duplicate work impossible, and a heartbeat plus a reaper turn "the
worker died" into "FAILED, retry permitted" instead of "stuck forever".

THREE TABLES, THREE QUESTIONS
------------------------------
  IqviaUploadSession   "does this upload exist, and what is its plan?"
                       (one row per upload_id -- source, chunk plan, the
                       on-disk temp file path, overall status)
  IqviaUploadChunk     "which byte ranges have actually arrived?"
                       (one row per received chunk -- composite PK
                       (upload_id, chunk_index) makes a retried PUT an
                       idempotent upsert, never a duplicate)
  IqviaImportJob       "is the staging/import work queued, running, done,
                       or failed -- and whose turn is it to do it?"
                       (claimed via SELECT ... FOR UPDATE SKIP LOCKED, the
                       exact mechanism `export_jobs.claim_next_queued` uses,
                       so two workers can never import the same snapshot
                       twice; a partial unique index on
                       (snapshot_id) WHERE active_marker IS TRUE is the
                       maker-checker-adjacent guarantee that only one
                       import job is ever live for a given snapshot)

THE FILE ITSELF IS ALREADY DURABLE
------------------------------------
Each chunk is written directly to its byte OFFSET in a server-local temp
file (unchanged from the original design) -- that file survives a process
restart on its own; only the BOOKKEEPING of which chunks landed in it needed
to move out of memory. `IqviaUploadChunk` is the source of truth for the
resume contract (`GET /uploads/{id}` now computes "missing" from this table,
not from memory).

IMPORT WORK WAS ALREADY ROW-LEVEL RESUMABLE; THE TRIGGER WAS NOT
---------------------------------------------------------------------
`iqvia_import._import_csv` already resumes correctly at the row level when
called again with the same `snapshot_id` (`ON CONFLICT DO NOTHING` against
the table's own unique constraint) -- that was never the gap. The gap was
that nothing EVER CALLED IT AGAIN after a `BackgroundTasks` task died with
the process. `IqviaImportJob` plus `iqvia_import_scheduler.py`'s poller is
that missing trigger: a QUEUED or an orphaned RUNNING-but-silent job gets
claimed and the importer is invoked with the job's `snapshot_id`, which is
exactly the resume call the importer already supported and nothing used.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

# `IqviaUploadSession.snapshot_id` / `IqviaImportJob.snapshot_id` are string
# `ForeignKey("source_snapshot.id", ...)` targets, resolved lazily by
# SQLAlchemy on first mapper use -- guarantee `SourceSnapshot`
# (snapshot_models.py) has been imported by the time anything in this module
# is used, the same reasoning snapshot_models.py itself documents for its
# own `rce_source_intakes` FK.
from app.tefca_registry.rce import snapshot_models as _snapshot_models  # noqa: F401


class IqviaUploadSession(Base):
    """One chunked upload. The chunk plan and the on-disk temp path, durably."""

    __tablename__ = "iqvia_upload_session"

    STATUS_UPLOADING = "UPLOADING"
    STATUS_COMPLETE = "COMPLETE"          # all chunks received, not yet staged
    STATUS_STAGING_QUEUED = "STAGING_QUEUED"
    STATUS_STAGED = "STAGED"
    STATUS_FAILED = "FAILED"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(32), nullable=False)
    label = Column(String(200), nullable=False)
    total_size = Column(BigInteger, nullable=False)
    chunk_size = Column(Integer, nullable=False)
    total_chunks = Column(Integer, nullable=False)
    temp_path = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default=STATUS_UPLOADING, index=True)
    #: Set once `/complete` registers the PENDING snapshot this upload feeds.
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("source_snapshot.id",
                         ondelete="RESTRICT"), nullable=True)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        nullable=False)

    def to_dict(self):
        return {
            "upload_id": str(self.id), "source": self.source, "label": self.label,
            "total_size": self.total_size, "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks, "status": self.status,
            "snapshot_id": str(self.snapshot_id) if self.snapshot_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IqviaUploadChunk(Base):
    """One received chunk. Composite PK makes a retried PUT idempotent --
    `ON CONFLICT (upload_id, chunk_index) DO NOTHING`, never a duplicate
    row, never a second write counted as progress."""

    __tablename__ = "iqvia_upload_chunk"

    upload_id = Column(UUID(as_uuid=True),
                       ForeignKey("iqvia_upload_session.id", ondelete="CASCADE"),
                       primary_key=True)
    chunk_index = Column(Integer, primary_key=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class IqviaImportJob(Base):
    """One request to stage/import one snapshot. Durable trigger for work
    that `iqvia_import._import_csv` already knows how to resume at the row
    level -- this table is what makes sure it gets CALLED again."""

    __tablename__ = "iqvia_import_job"

    STATE_QUEUED = "QUEUED"
    STATE_RUNNING = "RUNNING"
    STATE_SUCCEEDED = "SUCCEEDED"
    STATE_FAILED = "FAILED"

    ACTIVE_STATES = (STATE_QUEUED, STATE_RUNNING)
    TERMINAL_STATES = (STATE_SUCCEEDED, STATE_FAILED)

    #: Bounded retries -- an import that keeps dying (bad file, disk full,
    #: whatever) must stop being retried automatically and surface as FAILED
    #: for a human, not spin forever. Comfortably more than one transient
    #: blip, well short of "retry forever".
    MAX_ATTEMPTS = 5

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_id = Column(UUID(as_uuid=True),
                       ForeignKey("iqvia_upload_session.id", ondelete="SET NULL"),
                       nullable=True)
    snapshot_id = Column(UUID(as_uuid=True),
                         ForeignKey("source_snapshot.id", ondelete="RESTRICT"),
                         nullable=False)
    source = Column(String(32), nullable=False)
    file_path = Column(Text, nullable=False)
    label = Column(String(200), nullable=False)
    created_by = Column(String(255), nullable=False)

    state = Column(String(20), nullable=False, default=STATE_QUEUED, index=True)
    phase = Column(String(64))
    #: True while QUEUED/RUNNING, NULL when terminal -- keys the partial
    #: unique index that makes "only one live import job per snapshot" a
    #: database guarantee, not a code promise.
    active_marker = Column(Boolean, nullable=True)

    attempt_count = Column(Integer, nullable=False, server_default=text("0"))
    error_reason = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = Column(DateTime)
    heartbeat_at = Column(DateTime, index=True)
    completed_at = Column(DateTime)
    failed_at = Column(DateTime)

    __table_args__ = (
        Index("idx_iqvia_import_job_state_heartbeat", "state", "heartbeat_at"),
        Index("uq_iqvia_import_job_active_snapshot", "snapshot_id", "active_marker",
             unique=True, postgresql_where=text("active_marker IS TRUE")),
    )

    def to_dict(self):
        return {
            "job_id": str(self.id), "upload_id": str(self.upload_id) if self.upload_id else None,
            "snapshot_id": str(self.snapshot_id), "state": self.state, "phase": self.phase,
            "attempt_count": self.attempt_count, "error_reason": self.error_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "failed_at": self.failed_at.isoformat() if self.failed_at else None,
        }
