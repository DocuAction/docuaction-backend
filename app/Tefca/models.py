"""
DocuAction TEFCA Review Protocol Module
Database Models (SQLAlchemy ORM)
AGT — ONC TEFCA Review Protocol
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime,
    Enum, ForeignKey, Text, JSON, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum

# FIX 4: use the SHARED application Base so these tables register with the same
# metadata that main.py's create_all and Alembic operate on. Previously this
# module declared its own declarative_base(), so the TEFCA tables were invisible
# to migrations and were never created in any database.
from app.core.database import Base


# ─── Enums ───────────────────────────────────────────────────────────────────

class EntityType(str, enum.Enum):
    QHIN = "QHIN"
    PARTICIPANT = "PARTICIPANT"
    SUBPARTICIPANT = "SUBPARTICIPANT"

class BucketClassification(str, enum.Enum):
    BUCKET_1 = "1"
    BUCKET_2 = "2"
    BUCKET_3 = "3"
    BUCKET_4 = "4"

class BucketLabel(str, enum.Enum):
    NO_DISCREPANCY = "No Discrepancy"
    MINOR_ADMINISTRATIVE = "Minor or Administrative"
    INEXPLICABLE = "Inexplicable"
    NON_COMPLIANT = "Non-Compliant"

class TierAssignment(int, enum.Enum):
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3

class EntityStatus(str, enum.Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    REVIEWED_COMPLETE = "REVIEWED_COMPLETE"
    CORRECTIVE_ACTION_OPEN = "CORRECTIVE_ACTION_OPEN"
    ESCALATED = "ESCALATED"

class CycleType(str, enum.Enum):
    TASK3_RETROSPECTIVE = "TASK3_RETROSPECTIVE"
    TASK4_ONGOING = "TASK4_ONGOING"
    TASK5_PRIORITY = "TASK5_PRIORITY"

class CycleStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    REPORT_GENERATED = "REPORT_GENERATED"

class RecordStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    REVIEWED = "REVIEWED"
    FINALIZED = "FINALIZED"

class CaseSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class CaseStatus(str, enum.Enum):
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_COR = "PENDING_COR"
    RESOLVED_ACTION = "RESOLVED_ACTION"
    RESOLVED_NO_ACTION = "RESOLVED_NO_ACTION"
    ESCALATED = "ESCALATED"

class DispositionRecommendation(str, enum.Enum):
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
    QHIN_NOTIFICATION_MINOR = "QHIN_NOTIFICATION_MINOR"
    QHIN_CORRECTIVE_ACTION_REQUIRED = "QHIN_CORRECTIVE_ACTION_REQUIRED"
    ESCALATE_TO_ONC_REVIEW = "ESCALATE_TO_ONC_REVIEW"


# ─── Models ──────────────────────────────────────────────────────────────────

class TEFCAEntity(Base):
    """Master record for each TEFCA entity in scope."""
    __tablename__ = "tefca_entities"

    entity_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rce_organization_id = Column(String(255), unique=True, nullable=False, index=True)
    qhin_name = Column(String(255), nullable=False, index=True)
    entity_type = Column(Enum(EntityType), nullable=False)
    legal_name_submitted = Column(String(500), nullable=False)
    npi_submitted = Column(String(10), index=True)
    uei_submitted = Column(String(12))
    address_submitted = Column(JSONB)
    identifiers_submitted = Column(JSONB)
    endpoints_submitted = Column(JSONB)
    part_of_rce_id = Column(String(255))
    fhir_resource_raw = Column(JSONB)
    date_first_seen = Column(DateTime, default=datetime.utcnow)
    date_last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    current_status = Column(Enum(EntityStatus), default=EntityStatus.PENDING_REVIEW)
    latest_bucket = Column(Enum(BucketClassification))
    latest_confidence = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    evidence_records = relationship("TEFCAEvidenceRecord", back_populates="entity")

    __table_args__ = (
        Index("idx_tefca_entities_npi_qhin", "npi_submitted", "qhin_name"),
        Index("idx_tefca_entities_status_bucket", "current_status", "latest_bucket"),
    )


class TEFCAReviewCycle(Base):
    """One row per review cycle — Task 3 (weekly), Task 4 (bi-weekly), Task 5 (priority)."""
    __tablename__ = "tefca_review_cycles"

    cycle_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cycle_type = Column(Enum(CycleType), nullable=False)
    cycle_start_date = Column(DateTime, nullable=False)
    cycle_end_date = Column(DateTime)
    cycle_number = Column(Integer)
    total_entities_sampled = Column(Integer, default=0)
    total_entities_completed = Column(Integer, default=0)
    sample_confidence_level = Column(Float, default=0.95)
    sample_method = Column(String(100), default="STATISTICAL_95_CONFIDENCE")
    bucket_1_count = Column(Integer, default=0)
    bucket_2_count = Column(Integer, default=0)
    bucket_3_count = Column(Integer, default=0)
    bucket_4_count = Column(Integer, default=0)
    auto_completed_count = Column(Integer, default=0)
    tier2_queue_count = Column(Integer, default=0)
    tier3_queue_count = Column(Integer, default=0)
    avg_confidence_score = Column(Float)
    cycle_status = Column(Enum(CycleStatus), default=CycleStatus.PLANNED)
    methodology_version = Column(String(20), default="1.0")
    created_by = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    evidence_records = relationship("TEFCAEvidenceRecord", back_populates="cycle")


class TEFCAEvidenceRecord(Base):
    """Five-element evidence record — primary deliverable output per entity per cycle."""
    __tablename__ = "tefca_evidence_records"

    record_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_entities.entity_id"), nullable=False)
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("tefca_review_cycles.cycle_id"), nullable=False)

    # Tier and Classification
    tier_assigned = Column(Integer, nullable=False)
    auto_classified = Column(Boolean, default=True)
    bucket_classification = Column(Enum(BucketClassification), nullable=False)
    bucket_label = Column(Enum(BucketLabel), nullable=False)
    confidence_score = Column(Float, nullable=False)
    finding_codes = Column(JSONB, default=list)

    # Five Elements — stored as JSONB
    element_1_entity_identification = Column(JSONB)
    element_2_finding_classification = Column(JSONB)
    element_3_source_comparison = Column(JSONB)
    element_4_supporting_citations = Column(JSONB)
    element_5_disposition_recommendation = Column(JSONB)

    # Review tracking
    reviewer_id = Column(String(255))
    reviewer_tier = Column(Integer)
    review_timestamp = Column(DateTime)
    review_notes = Column(Text)
    analyst_override_reason = Column(Text)
    supervisor_review_required = Column(Boolean, default=False)
    supervisor_reviewer_id = Column(String(255))
    supervisor_review_timestamp = Column(DateTime)
    supervisor_notes = Column(Text)

    record_status = Column(Enum(RecordStatus), default=RecordStatus.DRAFT)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entity = relationship("TEFCAEntity", back_populates="evidence_records")
    cycle = relationship("TEFCAReviewCycle", back_populates="evidence_records")

    __table_args__ = (
        Index("idx_evidence_entity_cycle", "entity_id", "cycle_id"),
        Index("idx_evidence_status_tier", "record_status", "tier_assigned"),
        Index("idx_evidence_bucket", "bucket_classification"),
    )


class TEFCASourceCache(Base):
    """Caches authoritative source API responses for reproducibility and audit."""
    __tablename__ = "tefca_source_cache"

    cache_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_entities.entity_id"))
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("tefca_review_cycles.cycle_id"))
    source_name = Column(String(50), nullable=False)
    query_parameters = Column(JSONB)
    response_data = Column(JSONB)
    response_hash = Column(String(64))
    query_timestamp = Column(DateTime, default=datetime.utcnow)
    data_freshness_date = Column(DateTime)
    api_version = Column(String(20))
    cache_expires_at = Column(DateTime)
    query_success = Column(Boolean, default=True)
    error_message = Column(Text)

    __table_args__ = (
        Index("idx_cache_entity_source", "entity_id", "source_name"),
    )


class TEFCAPriorityCase(Base):
    """COR-directed priority reviews — Task 5."""
    __tablename__ = "tefca_priority_cases"

    case_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cor_reference = Column(String(100), nullable=False)
    qhin = Column(String(100))                         # QHIN attribution (Task 5)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_entities.entity_id"))
    assigned_by = Column(String(255), nullable=False)
    assigned_date = Column(DateTime, nullable=False)
    deadline_date = Column(DateTime)
    issue_description = Column(Text, nullable=False)
    case_status = Column(Enum(CaseStatus), default=CaseStatus.ASSIGNED)
    severity = Column(Enum(CaseSeverity))
    root_cause_determination = Column(String(50))
    root_cause_description = Column(Text)
    recommendations = Column(JSONB)
    prevention_recommendation = Column(Text)
    resolution_notes = Column(Text)
    assigned_reviewer_id = Column(String(255))
    related_evidence_record_id = Column(UUID(as_uuid=True), ForeignKey("tefca_evidence_records.record_id"))
    completed_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TEFCAReport(Base):
    """Generated report records."""
    __tablename__ = "tefca_reports"

    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_type = Column(String(50), nullable=False)
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("tefca_review_cycles.cycle_id"))
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    report_data = Column(JSONB)
    file_path_pdf = Column(String(500))
    file_path_docx = Column(String(500))
    generated_by = Column(String(255))
    generated_at = Column(DateTime, default=datetime.utcnow)
    methodology_version = Column(String(20))


class QueueStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    COMPLETE = "COMPLETE"


class TEFCAAnalystQueue(Base):
    """
    Human-in-the-loop review queue. A queue item is created whenever an entity is
    classified B2/B3/B4 or INDETERMINATE (a required source was unavailable), so
    no finding is finalized without an analyst step. Tier-2 items are worked by
    reviewers/senior_analysts; Tier-3 (Bucket-4 / hard escalations) by senior
    analysts and above. (FIX 4)
    """
    __tablename__ = "tefca_analyst_queue"

    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id = Column(UUID(as_uuid=True), ForeignKey("tefca_evidence_records.record_id"), nullable=False)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_entities.entity_id"))
    cycle_id = Column(UUID(as_uuid=True), ForeignKey("tefca_review_cycles.cycle_id"))

    tier = Column(Integer, nullable=False)                 # 2 or 3
    assigned_role = Column(String(30), nullable=False)     # reviewer / senior_analyst / qalead
    priority = Column(Integer, nullable=False, default=50) # higher = more urgent (B4=100 ... B2=40)
    bucket_classification = Column(Enum(BucketClassification))
    queue_reason = Column(Text)                            # e.g. "Source unavailable: PECOS"

    status = Column(Enum(QueueStatus), default=QueueStatus.PENDING, nullable=False)
    claimed_by = Column(String(255))
    claimed_at = Column(DateTime)
    completed_by = Column(String(255))
    completed_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_queue_status_tier_priority", "status", "tier", "priority"),
        Index("idx_queue_assigned_role", "assigned_role", "status"),
    )


# ─── Dashboard / connector-log tables ────────────────────────────────────────
# Lightweight, denormalized tables requested for the executive dashboard. The
# authoritative review data lives in TEFCAEvidenceRecord (rich 5-element model);
# the dashboard aggregates from there. TEFCAConnectorLog is actively written on
# every connector probe to power uptime/availability trends.

class TEFCAConnectorLog(Base):
    """One row per connector health probe — powers connector_uptime trends."""
    __tablename__ = "tefca_connector_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_name = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False)        # available / unavailable
    response_time_ms = Column(Integer)
    checked_at = Column(DateTime, default=datetime.utcnow, index=True)


class TEFCAReview(Base):
    """Denormalized one-row-per-review summary (entity + outcome). Mirrors the
    authoritative TEFCAEvidenceRecord; provided for the requested dashboard
    schema and lighter reporting queries."""
    __tablename__ = "tefca_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_name = Column(String(500))
    npi = Column(String(10), index=True)
    uei = Column(String(12))
    status = Column(String(20), index=True)            # pass / fail / pending / indeterminate
    risk_level = Column(String(20))                    # low / medium / high / critical
    reviewer_id = Column(String(255))
    qhin = Column(String(100))                         # QHIN attribution (RFQ Task 1)
    is_mock_data = Column(Boolean, default=False)      # MOCK rows: replace with real COR data
    entity_type = Column(String(50))                   # participant / subparticipant
    entity_state = Column(String(2))                   # 2-letter state code
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TEFCAFinding(Base):
    """Per-connector finding attached to a review (denormalized companion to the
    finding_codes / element_3 stored on TEFCAEvidenceRecord)."""
    __tablename__ = "tefca_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reviews.id"), index=True)
    connector = Column(String(50))                     # nppes / leie / sam_gov / pecos
    finding_type = Column(String(100))
    detail = Column(Text)
    severity = Column(String(20))                      # low / medium / high / critical
