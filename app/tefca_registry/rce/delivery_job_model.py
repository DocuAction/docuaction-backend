"""The official ONC/RCE delivery processing job.

WHY THIS TABLE EXISTS
─────────────────────
`ingest_delivery` walks every delivered line and writes one Area 1 row per line
in 2,000-row batches, then quality, curation, promotion, verification and
reconciliation each walk the population again. On the delivered 23,566-record
file that is minutes of work; on a 100K delivery it is considerably more. A
browser request cannot own it — a gateway times out, an operator refreshes, a
worker recycles, and the delivery goes with it while nothing records that it
stopped.

What Data Operations needs back from registering a delivery is not the outcome.
It is a RECEIPT: the delivery was accepted, here is where to watch it.

WHY THIS IS NOT A THIRD JOB FRAMEWORK
─────────────────────────────────────
`Tefca/ppef_jobs.py` solved this for PPEF ingestion and `reports/data/
export_jobs.py` solved it again for controlled exports, both the same way:
durable state in the database, a partial unique index that makes concurrent
duplicates impossible, `FOR UPDATE SKIP LOCKED` for multi-worker claiming, and a
heartbeat plus a reaper that turns "the worker died" into "FAILED, retry
permitted". That mechanism is reused here verbatim in shape.

What is NOT reused is either table. `report_export_jobs` is keyed on an export
identity and names an artifact; `tefca_ppef_ingest_jobs` is keyed on a CMS
component and quarter. A delivery is neither, and a column that means one thing
for an export and another for an ingestion is how a shared table stops being
shared and starts being ambiguous.

THE BYTES ARE NOT IN THIS TABLE
───────────────────────────────
`storage_path` names the preserved original, which `intake.preserve_original`
has already written to immutable storage before the job row is created. The job
therefore carries a POINTER to evidence, never a copy of it: a second copy of a
Government delivery sitting in a job queue is a second copy to protect, and it
would be the one nobody remembers to protect.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Index, Integer, String, Text,
                        text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class RceDeliveryJob(Base):
    """One registered official delivery, and the processing run that serves it."""

    __tablename__ = "rce_delivery_jobs"

    #: Lifecycle. The same four the export job uses, and for the same reason: a
    #: fifth state would have to mean something a caller could act on
    #: differently, and none does. QUEUED and RUNNING are "watch it"; SUCCEEDED
    #: is "it is ready for review"; FAILED is "read the reason".
    STATE_QUEUED = "QUEUED"
    STATE_RUNNING = "RUNNING"
    STATE_SUCCEEDED = "SUCCEEDED"
    STATE_FAILED = "FAILED"

    ACTIVE_STATES = (STATE_QUEUED, STATE_RUNNING)
    TERMINAL_STATES = (STATE_SUCCEEDED, STATE_FAILED)

    #: The pipeline stages, in the order the runner performs them. These are the
    #: names the delivery dashboard shows; they are real transitions written by
    #: the runner as it finishes each stage, not a percentage. A progress bar
    #: that cannot measure progress is a decoration that lies — so what is
    #: reported is the stage, plus the row counts the stage itself produced.
    STAGE_ACCEPTED = "ACCEPTED"
    STAGE_PARSING = "PARSING"
    STAGE_QUALITY = "QUALITY"
    STAGE_CURATION = "CURATION"
    STAGE_PROMOTION = "PROMOTION"
    STAGE_VERIFICATION = "VERIFICATION"
    STAGE_RECONCILIATION = "RECONCILIATION"
    STAGE_READY = "READY_FOR_REVIEW"

    STAGE_ORDER = (STAGE_ACCEPTED, STAGE_PARSING, STAGE_QUALITY, STAGE_CURATION,
                   STAGE_PROMOTION, STAGE_VERIFICATION, STAGE_RECONCILIATION,
                   STAGE_READY)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: What makes two registrations the SAME registration. See
    #: `delivery_jobs.job_identity`.
    identity = Column(String(64), nullable=False, index=True)

    # ── what was registered ──────────────────────────────────────────────────
    delivery_label = Column(String(255))
    original_filename = Column(String(255), nullable=False)
    #: The preserved original, written before this row existed. Never the bytes.
    storage_path = Column(Text, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    file_size_bytes = Column(Integer, nullable=False)
    declared_delimiter = Column(String(8))

    #: The date the delivery was RECEIVED from ONC/RCE, which is not the date it
    #: was registered. A Government delivery can sit before anyone is authorised
    #: to load it.
    received_date = Column(DateTime)
    #: Operator-entered provenance: a Government reference (a transmittal
    #: number, an email subject) and any note. Never a Government data value.
    government_reference = Column(String(255))
    notes = Column(Text)
    source_name = Column(String(120))

    # ── lifecycle ────────────────────────────────────────────────────────────
    state = Column(String(20), nullable=False, default=STATE_QUEUED, index=True)
    stage = Column(String(32), nullable=False, default=STAGE_ACCEPTED)

    #: True while in flight, NULL when terminal. Keys the partial unique index.
    #: NULL rather than False deliberately: PostgreSQL's partial index on
    #: `active_marker IS TRUE` excludes NULL rows, so any number of finished
    #: jobs may share an identity while at most one live job may — which is
    #: exactly what lets ONC legitimately re-deliver the same bytes later.
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

    registered_by = Column(String(255), nullable=False)

    # ── what the run produced ────────────────────────────────────────────────
    #: Set the moment Area 1 exists. From here on the delivery is addressable
    #: through the existing `/deliveries/{intake_id}` surface, and everything
    #: downstream reads THAT rather than this table.
    source_intake_id = Column(UUID(as_uuid=True), index=True)

    #: Counts observed by the stages themselves, for the operational dashboard.
    #: Written as the run goes so a watching operator sees movement. This is a
    #: progress READOUT, never a source of truth: reconciliation recomputes
    #: every population from the rows, and its answer is the one that counts.
    records_received = Column(Integer)
    records_processed = Column(Integer)

    #: The reconciliation verdict, stored so the dashboard need not re-run the
    #: gate on every poll. `stage_detail` carries per-stage observations
    #: (issue counts, promoted counts, the reconciliation populations) exactly
    #: as the stage returned them.
    reconciliation_passed = Column(Boolean)
    stage_detail = Column(JSONB, default=dict)

    __table_args__ = (
        Index("idx_rce_delivery_job_state_heartbeat", "state", "heartbeat_at"),
        Index("idx_rce_delivery_job_registered_by", "registered_by"),
        # The concurrency guard. See the class docstring.
        Index("uq_rce_delivery_job_active_identity", "identity", "active_marker",
              unique=True, postgresql_where=text("active_marker IS TRUE")),
    )

    def to_dict(self):
        """The job as a caller may see it.

        Carries no Government data value: a filename, a hash, counts and stage
        names. `error_reason` is a controlled string written by this
        application, not an exception's text.
        """
        return {
            "job_id": str(self.id),
            "state": self.state,
            "stage": self.stage,
            "delivery_label": self.delivery_label,
            "original_filename": self.original_filename,
            "sha256": self.sha256,
            "file_size_bytes": self.file_size_bytes,
            "source_name": self.source_name,
            "government_reference": self.government_reference,
            "notes": self.notes,
            "received_date": (self.received_date.isoformat()
                              if self.received_date else None),
            "registered_by": self.registered_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (self.completed_at.isoformat()
                             if self.completed_at else None),
            "failed_at": self.failed_at.isoformat() if self.failed_at else None,
            "attempt_count": self.attempt_count,
            "error_reason": self.error_reason,
            "intake_id": (str(self.source_intake_id)
                          if self.source_intake_id else None),
            "records_received": self.records_received,
            "records_processed": self.records_processed,
            "reconciliation_passed": self.reconciliation_passed,
            "stage_detail": self.stage_detail or {},
        }
