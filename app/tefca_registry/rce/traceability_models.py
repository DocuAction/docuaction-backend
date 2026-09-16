"""
Delivery traceability tables (2026-09-17 remediation).

Five append-only evidence tables. None of them alters an existing table: on
DEV the Area 2 and registry tables are owned by the runtime role, so a column
add would need an operator ownership window; a new table created by the
migration role needs only a grant. Every table carries actor, timestamp,
correlation id and build SHA so that a row can be tied to the request or job
and to the exact application build that wrote it.

    rce_delivery_stage_events         one row per stage ATTEMPT (start + end)
    rce_disposition_events            one row per terminal-accounting decision
    rce_reconciliation_snapshots      one row per reconciliation run
    tefca_identifier_decision_events  one row per identifier conflict/decision
    rce_delivery_report_links         one row per generated delivery report

"Current" state is DERIVED (highest sequence per subject) and exposed by the
view rce_current_dispositions. History is never rewritten.

The design supports defensible audit lineage, evidence integrity, authenticity,
reproducibility and operational accountability. It does not claim that any
contract clause mandates this particular database shape.
"""

import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
# The job table is declared in its own module and is not imported by
# rce/models.py; the foreign keys below need it on the same metadata.
import app.tefca_registry.rce.delivery_job_model  # noqa: F401

STAGE_EVENT_STAGES = (
    "REGISTERED", "RECEIPT_PRESERVED", "SHA256", "SCHEMA_VALIDATION", "PARSING",
    "QUALITY", "CURATION", "MATCHING", "PROMOTION", "RELATIONSHIPS",
    "VERIFICATION_READINESS", "RECONCILIATION", "READY_FOR_REVIEW",
    "REPORT_GENERATION",
)
STAGE_EVENT_STATUSES = ("STARTED", "COMPLETED", "FAILED", "SKIPPED")

DISPOSITIONS = ("CREATED", "UPDATED", "MATCHED_UNCHANGED", "HELD", "REJECTED",
                "MISSING_KEY", "EXCLUDED")

IDENTIFIER_DECISIONS = (
    "CONFLICT_RAISED", "CONFIRM_EXISTING", "CONFIRM_SUBMITTED", "CORRECTED",
    "REQUEST_EVIDENCE", "DEFERRED", "ESCALATED", "REJECTED",
)

SNAPSHOT_TRIGGERS = ("PIPELINE", "DISPOSITION", "MANUAL", "RECONSTRUCTION",
                    # Added 2026-09-18 (pre-merge review Decision 2): a NEW
                    # snapshot created because a post-promotion finding was
                    # recorded or resolved, distinct from a re-promotion
                    # DISPOSITION snapshot — no disposition changed either
                    # time. See migration 20260918_pp_verification for the
                    # CHECK constraint that must widen alongside this tuple.
                    "POST_PROMOTION_VERIFICATION", "POST_PROMOTION_RESOLUTION")

ACTOR_TYPES = ("SYSTEM", "HUMAN")

CURRENT_DISPOSITIONS_VIEW = "rce_current_dispositions"

#: Tables that ONLY Alembic 20260917_delivery_traceability may create. Startup
#: `create_all` excludes them: a table the runtime role created would be owned
#: by the runtime role, and "append-only by grant" would be void.
MIGRATION_OWNED_TABLES = (
    "rce_delivery_stage_events", "rce_disposition_events",
    "rce_reconciliation_snapshots", "tefca_identifier_decision_events",
    "rce_delivery_report_links",
)


def _in_list(values) -> str:
    return ", ".join("'%s'" % v for v in values)


def _iso(value):
    return value.isoformat() if value else None


class RceDeliveryStageEvent(Base):
    """One attempt of one pipeline stage for one delivery job.

    A stage that is retried produces a NEW row with attempt+1; nothing is
    overwritten. `completed_at` is NULL while the attempt is in flight.
    """

    __tablename__ = "rce_delivery_stage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True),
                    ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"),
                    nullable=False, index=True)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                       nullable=True, index=True)
    stage = Column(String(32), nullable=False)
    attempt = Column(Integer, nullable=False, server_default=text("1"))
    status = Column(String(16), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    input_count = Column(Integer)
    output_count = Column(Integer)
    warning_count = Column(Integer)
    held_count = Column(Integer)
    rejected_count = Column(Integer)
    failure_class = Column(String(128))
    failure_reason = Column(Text)
    detail = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    correlation_id = Column(String(64), nullable=False)
    worker_id = Column(String(128))
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))

    __table_args__ = (
        UniqueConstraint("job_id", "stage", "attempt",
                         name="uq_rce_stage_event_attempt"),
        Index("idx_rce_stage_event_job_started", "job_id", "started_at"),
        CheckConstraint("stage IN (%s)" % _in_list(STAGE_EVENT_STAGES),
                        name="ck_rce_stage_event_stage"),
        CheckConstraint("status IN (%s)" % _in_list(STAGE_EVENT_STATUSES),
                        name="ck_rce_stage_event_status"),
        CheckConstraint("attempt > 0", name="ck_rce_stage_event_attempt_pos"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at",
                        name="ck_rce_stage_event_times"),
        CheckConstraint(
            "coalesce(input_count,0) >= 0 AND coalesce(output_count,0) >= 0 AND "
            "coalesce(warning_count,0) >= 0 AND coalesce(held_count,0) >= 0 AND "
            "coalesce(rejected_count,0) >= 0",
            name="ck_rce_stage_event_counts_nonneg"),
    )

    def to_dict(self):
        duration = None
        if self.completed_at and self.started_at:
            duration = int((self.completed_at - self.started_at).total_seconds() * 1000)
        return {
            "id": str(self.id), "job_id": str(self.job_id),
            "intake_id": str(self.intake_id) if self.intake_id else None,
            "stage": self.stage, "attempt": self.attempt, "status": self.status,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "duration_ms": duration,
            "input_count": self.input_count, "output_count": self.output_count,
            "warning_count": self.warning_count, "held_count": self.held_count,
            "rejected_count": self.rejected_count,
            "failure_class": self.failure_class,
            "failure_reason": self.failure_reason,
            "detail": self.detail or {},
            "correlation_id": self.correlation_id, "worker_id": self.worker_id,
            "build_sha": self.build_sha,
        }


class RceDispositionEvent(Base):
    """One terminal-accounting decision for one delivered line.

    Append-only. The pipeline writes sequence 1 (SYSTEM); every later analyst
    decision appends sequence n+1 (HUMAN). The current disposition of a source
    record is the row with the highest sequence - see rce_current_dispositions.
    """

    __tablename__ = "rce_disposition_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                       nullable=False, index=True)
    source_record_id = Column(UUID(as_uuid=True),
                              ForeignKey("rce_source_records.id", ondelete="RESTRICT"),
                              nullable=False)
    curated_record_id = Column(UUID(as_uuid=True),
                               ForeignKey("rce_curated_records.id", ondelete="RESTRICT"))
    job_id = Column(UUID(as_uuid=True),
                    ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"),
                    index=True)
    sequence = Column(Integer, nullable=False)
    disposition = Column(String(20), nullable=False)
    reason_code = Column(String(64), nullable=False)
    reason = Column(Text)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"))
    changed_fields = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    actor = Column(String(320), nullable=False)
    actor_type = Column(String(8), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))
    reconstructed = Column(Boolean, nullable=False, server_default=text("false"))
    reconstruction = Column(JSONB)

    __table_args__ = (
        UniqueConstraint("source_record_id", "sequence",
                         name="uq_rce_disposition_sequence"),
        Index("idx_rce_disposition_intake_disp", "intake_id", "disposition"),
        Index("idx_rce_disposition_record_seq", "source_record_id",
              text("sequence DESC")),
        CheckConstraint("disposition IN (%s)" % _in_list(DISPOSITIONS),
                        name="ck_rce_disposition_value"),
        CheckConstraint("actor_type IN (%s)" % _in_list(ACTOR_TYPES),
                        name="ck_rce_disposition_actor_type"),
        CheckConstraint("sequence > 0", name="ck_rce_disposition_seq_pos"),
    )

    def to_dict(self):
        return {
            "id": str(self.id), "intake_id": str(self.intake_id),
            "source_record_id": str(self.source_record_id),
            "curated_record_id": (str(self.curated_record_id)
                                  if self.curated_record_id else None),
            "job_id": str(self.job_id) if self.job_id else None,
            "sequence": self.sequence, "disposition": self.disposition,
            "reason_code": self.reason_code, "reason": self.reason,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "changed_fields": self.changed_fields or [],
            "actor": self.actor, "actor_type": self.actor_type,
            "decided_at": _iso(self.decided_at),
            "correlation_id": self.correlation_id, "build_sha": self.build_sha,
            "reconstructed": bool(self.reconstructed),
            "reconstruction": self.reconstruction,
        }


class RceReconciliationSnapshot(Base):
    """One reconciliation run, persisted.

    The equation is enforced by a CHECK when `passed` is true, so a snapshot
    that claims to pass cannot carry counts that do not sum.
    """

    __tablename__ = "rce_reconciliation_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True),
                    ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"),
                    nullable=False, index=True)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                       nullable=False)
    sequence = Column(Integer, nullable=False)
    passed = Column(Boolean, nullable=False)
    failure_reason = Column(Text)
    received = Column(Integer, nullable=False)
    created = Column(Integer, nullable=False, server_default=text("0"))
    updated = Column(Integer, nullable=False, server_default=text("0"))
    matched_unchanged = Column(Integer, nullable=False, server_default=text("0"))
    held = Column(Integer, nullable=False, server_default=text("0"))
    rejected = Column(Integer, nullable=False, server_default=text("0"))
    missing_key = Column(Integer, nullable=False, server_default=text("0"))
    excluded = Column(Integer, nullable=False, server_default=text("0"))
    dimensions = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    checks = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_evidence = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    actor = Column(String(320), nullable=False)
    trigger = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    hash = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))
    migration_revision = Column(String(64), nullable=False,
                                server_default=text("'unknown'"))
    correlation_id = Column(String(64), nullable=False)
    reconstructed = Column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_rce_snapshot_sequence"),
        Index("idx_rce_snapshot_intake_created", "intake_id",
              text("created_at DESC")),
        CheckConstraint("trigger IN (%s)" % _in_list(SNAPSHOT_TRIGGERS),
                        name="ck_rce_snapshot_trigger"),
        CheckConstraint("sequence > 0", name="ck_rce_snapshot_seq_pos"),
        CheckConstraint(
            "received >= 0 AND created >= 0 AND updated >= 0 AND "
            "matched_unchanged >= 0 AND held >= 0 AND rejected >= 0 AND "
            "missing_key >= 0 AND excluded >= 0",
            name="ck_rce_snapshot_counts_nonneg"),
        CheckConstraint(
            "passed = false OR received = created + updated + matched_unchanged "
            "+ held + rejected + missing_key + excluded",
            name="ck_rce_snapshot_equation"),
    )

    @property
    def accounted(self) -> int:
        return ((self.created or 0) + (self.updated or 0)
                + (self.matched_unchanged or 0) + (self.held or 0)
                + (self.rejected or 0) + (self.missing_key or 0)
                + (self.excluded or 0))

    def to_dict(self):
        return {
            "id": str(self.id), "job_id": str(self.job_id),
            "intake_id": str(self.intake_id), "sequence": self.sequence,
            "passed": bool(self.passed), "failure_reason": self.failure_reason,
            "equation": {
                "received": self.received, "created": self.created,
                "updated": self.updated, "matched_unchanged": self.matched_unchanged,
                "held": self.held, "rejected": self.rejected,
                "missing_key": self.missing_key, "excluded": self.excluded,
                "accounted": self.accounted,
                "holds": self.accounted == self.received,
            },
            "dimensions": self.dimensions or {},
            "checks": self.checks or [],
            "source_evidence": self.source_evidence or {},
            "actor": self.actor, "trigger": self.trigger,
            "created_at": _iso(self.created_at),
            "hash": self.hash, "build_sha": self.build_sha,
            "migration_revision": self.migration_revision,
            "correlation_id": self.correlation_id,
            "reconstructed": bool(self.reconstructed),
        }


class TefcaIdentifierDecisionEvent(Base):
    """One identifier conflict or one human decision about it. Append-only."""

    __tablename__ = "tefca_identifier_decision_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"),
                       nullable=False, index=True)
    source_record_id = Column(UUID(as_uuid=True),
                              ForeignKey("rce_source_records.id", ondelete="RESTRICT"),
                              index=True)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                       index=True)
    issue_id = Column(UUID(as_uuid=True),
                      ForeignKey("rce_issues.id", ondelete="RESTRICT"), index=True)
    sequence = Column(Integer, nullable=False)
    identifier_type = Column(String(32), nullable=False)
    submitted_value = Column(Text)
    existing_value = Column(Text)
    verified_value = Column(Text)
    selected_value = Column(Text)
    decision = Column(String(32), nullable=False)
    reason = Column(Text, nullable=False)
    evidence_source = Column(String(64))
    confidence = Column(String(10))
    actor = Column(String(320), nullable=False)
    actor_type = Column(String(8), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    version_id = Column(UUID(as_uuid=True),
                        ForeignKey("tefca_entity_versions.id", ondelete="RESTRICT"))
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))

    __table_args__ = (
        UniqueConstraint("entity_id", "identifier_type", "sequence",
                         name="uq_tefca_identifier_decision_sequence"),
        CheckConstraint("decision IN (%s)" % _in_list(IDENTIFIER_DECISIONS),
                        name="ck_tefca_identifier_decision_value"),
        CheckConstraint("actor_type IN (%s)" % _in_list(ACTOR_TYPES),
                        name="ck_tefca_identifier_decision_actor_type"),
        CheckConstraint("sequence > 0", name="ck_tefca_identifier_decision_seq_pos"),
    )

    def to_dict(self):
        return {
            "id": str(self.id), "entity_id": str(self.entity_id),
            "source_record_id": (str(self.source_record_id)
                                 if self.source_record_id else None),
            "intake_id": str(self.intake_id) if self.intake_id else None,
            "issue_id": str(self.issue_id) if self.issue_id else None,
            "sequence": self.sequence, "identifier_type": self.identifier_type,
            "submitted_value": self.submitted_value,
            "existing_value": self.existing_value,
            "verified_value": self.verified_value,
            "selected_value": self.selected_value,
            "decision": self.decision, "reason": self.reason,
            "evidence_source": self.evidence_source, "confidence": self.confidence,
            "actor": self.actor, "actor_type": self.actor_type,
            "decided_at": _iso(self.decided_at),
            "version_id": str(self.version_id) if self.version_id else None,
            "correlation_id": self.correlation_id, "build_sha": self.build_sha,
        }


class RceDeliveryReportLink(Base):
    """Deterministic linkage: job, intake, snapshot, report, artifact, audit."""

    __tablename__ = "rce_delivery_report_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True),
                    ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"),
                    nullable=False, index=True)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                       nullable=False)
    snapshot_id = Column(UUID(as_uuid=True),
                         ForeignKey("rce_reconciliation_snapshots.id",
                                    ondelete="RESTRICT"),
                         nullable=False, index=True)
    report_id = Column(String(64), nullable=False, index=True)
    report_type = Column(String(64), nullable=False)
    #: `report_artifacts.id`. Not a declared FOREIGN KEY: `report_artifacts` is
    #: created by this migration chain (20260829) but its model lives in
    #: `app.reports`, outside the TEFCA metadata scope, and the chain's boundary
    #: check refuses foreign keys to tables it cannot see. Integrity is asserted
    #: instead by the reconciliation "zero orphan report links" check.
    artifact_id = Column(UUID(as_uuid=True), index=True)
    template_version = Column(String(32), nullable=False)
    generation_audit_id = Column(UUID(as_uuid=True),
                                 ForeignKey("audit_logs.id", ondelete="RESTRICT"))
    generated_by = Column(String(320), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False,
                          server_default=func.now())
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))
    correlation_id = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("report_id", "artifact_id",
                         name="uq_rce_report_link_artifact"),
        Index("idx_rce_report_link_intake_generated", "intake_id",
              text("generated_at DESC")),
    )

    def to_dict(self):
        return {
            "id": str(self.id), "job_id": str(self.job_id),
            "intake_id": str(self.intake_id), "snapshot_id": str(self.snapshot_id),
            "report_id": self.report_id, "report_type": self.report_type,
            "artifact_id": str(self.artifact_id) if self.artifact_id else None,
            "template_version": self.template_version,
            "generation_audit_id": (str(self.generation_audit_id)
                                    if self.generation_audit_id else None),
            "generated_by": self.generated_by,
            "generated_at": _iso(self.generated_at),
            "build_sha": self.build_sha, "correlation_id": self.correlation_id,
        }


#: The five traceability tables, parents first.
TRACEABILITY_TABLE_ORDER = [
    "rce_delivery_stage_events",
    "rce_disposition_events",
    "rce_reconciliation_snapshots",
    "tefca_identifier_decision_events",
    "rce_delivery_report_links",
]
