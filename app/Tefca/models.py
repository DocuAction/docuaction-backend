"""
DocuAction TEFCA Review Protocol Module
Database Models (SQLAlchemy ORM)
AGT — ONC TEFCA Review Protocol
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime,
    Enum, ForeignKey, Text, JSON, Index, text
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


class TEFCAImportHistory(Base):
    """Audit record for every entity-import attempt.

    Written on EVERY upload — including rejected and failed ones. An import that
    imported nothing still produced a record, because "nothing happened" is a
    fact a reviewer needs to be able to see. Storing only successes would make
    the history a highlight reel rather than an audit trail (P2, P7).

    The table is created automatically at startup by Base.metadata.create_all.
    """
    __tablename__ = "tefca_import_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(500))
    record_count = Column(Integer, default=0)      # rows parsed from the file
    imported_count = Column(Integer, default=0)    # rows accepted and inserted
    rejected_count = Column(Integer, default=0)    # rows that failed validation
    uploaded_by = Column(String(255), index=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, index=True)
    # QA-1.6 / QA-4.2 — SHA-256 of the uploaded bytes. Integrity evidence: it is
    # what lets a reviewer prove months later that the file in the record is the
    # file that was processed. NULL on rows written before this column existed;
    # backfilling it is impossible because the original bytes were never kept.
    file_hash = Column(String(64), index=True)
    status = Column(String(20), index=True)        # completed / partial / failed
    errors = Column(JSONB, default=list)           # [{row, field, reason}]


class TEFCADimensionEvidence(Base):
    """Dimension-organised evidence, one row per (dimension, source) item.

    APPEND-ONLY BY CONTRACT
    ───────────────────────
    Rows here are INSERTED and never updated or deleted by the evidence layer.
    Re-running a verification writes a NEW generation, distinguished by
    `generation_timestamp`; the prior generation stays exactly as it was. That
    is not tidiness, it is the requirement: a determination made in March cited
    the CMS dataset published in January, and after CMS publishes the April
    extract the only way to explain that determination is for the January
    evidence to still be there, unmodified.

    `dataset_version_anchor` is what pins the evidence to a specific CMS
    publication — CMS mints a new dataset UUID per quarterly release, so the
    UUID identifies the exact extract a lookup ran against.

    Analyst fields (reviewed_by / reviewed_at / analyst_notes) are the ONE
    exception to append-only: a human annotating a row is a distinct act from
    the system rewriting evidence, and the annotation is itself audited.
    """
    __tablename__ = "tefca_dimension_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    entity_id = Column(String(255), nullable=False, index=True)
    review_id = Column(String(255), index=True)
    review_cycle_id = Column(UUID(as_uuid=True), ForeignKey("tefca_review_cycles.cycle_id"))

    evidence_dimension = Column(String(64), nullable=False, index=True)
    dimension_disposition = Column(String(32))
    dimension_applicability = Column(String(32))

    source = Column(String(64), nullable=False, index=True)
    source_dataset = Column(String(128))
    ppef_component = Column(String(64))
    source_record_identifier = Column(Text)
    query_identifier = Column(Text)
    query_timestamp = Column(String(64))
    dataset_version_anchor = Column(String(128))
    http_last_modified = Column(String(64))

    disposition = Column(String(32), nullable=False)
    fields_evaluated = Column(JSONB, default=list)
    field_matches = Column(JSONB, default=list)
    field_conflicts = Column(JSONB, default=list)
    original_values = Column(JSONB, default=dict)
    normalized_values = Column(JSONB, default=dict)
    rule_applied = Column(String(128))
    note = Column(Text)

    retrieved_at = Column(String(64))
    generation_timestamp = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Analyst annotation — see the class docstring for why these are writable.
    analyst_notes = Column(Text)
    reviewed_by = Column(String(255))
    reviewed_at = Column(DateTime)

    __table_args__ = (
        Index("idx_dim_evidence_entity_dimension", "entity_id", "evidence_dimension"),
        Index("idx_dim_evidence_generation", "entity_id", "generation_timestamp"),
    )


class TEFCAPPEFSnapshot(Base):
    """One ingested CMS PPEF component file, with everything needed to reproduce it.

    CMS states that PPEF carries CURRENT enrollment information, not historical.
    That single sentence is why this table exists: the moment CMS publishes the
    next quarter, the data behind an earlier determination is gone from the
    source. Preserving the snapshot — and the checksum of the exact bytes — is
    what lets a determination say "evaluated against CMS PPEF Q3 2026, file
    PPEF_Practice_Location_Extract_2026.07.17.csv, SHA-256 abc..., retrieved
    2026-08-19" and have that mean something a year later.

    Snapshots are append-only. A re-ingest of the same quarter writes a new row;
    nothing is updated in place.
    """
    __tablename__ = "tefca_ppef_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    component = Column(String(40), nullable=False, index=True)
    # The CMS display title, preserved EXACTLY as published — "Address Sub-File
    # Q3 2026" for the component normalised internally as PRACTICE_LOCATION.
    cms_title = Column(String(255))
    file_name = Column(String(255))
    resource_id = Column(String(64))            # CMS file_uuid (a media id, not a dataset id)
    parent_dataset_id = Column(String(64))
    download_url = Column(Text)
    api_endpoint = Column(Text)
    transport = Column(String(20))              # DATA_API | DOWNLOAD | BOTH

    resource_version = Column(String(32), index=True)   # e.g. 2026.07.17
    as_of_label = Column(String(64))                    # e.g. "Q3 2026"
    file_size = Column(Integer)
    sha256 = Column(String(64), index=True)
    schema_fields = Column(JSONB, default=list)
    record_count = Column(Integer, default=0)
    rows_truncated = Column(Boolean, default=False)

    http_last_modified = Column(String(64))
    retrieved_at = Column(DateTime, default=datetime.utcnow)
    ingested_at = Column(DateTime, default=datetime.utcnow)
    ingest_status = Column(String(20), default="pending")   # pending|complete|failed
    error = Column(Text)
    ingested_by = Column(String(255))

    __table_args__ = (
        Index("idx_ppef_snapshot_component_version", "component", "resource_version"),
    )


class TEFCAPPEFRecord(Base):
    """One row from a PPEF sub-file, keyed for ENRLMT_ID joins.

    Deliberately ONE table for every component rather than five. The components
    share a join key and are queried the same way; five near-identical tables
    would mean five migrations, five query paths and five places for the join to
    drift. The component-specific columns live in `payload`.

    `related_enrollment_id` exists for REASSIGNMENT, whose two identifiers BOTH
    join back to ENROLLMENT.ENRLMT_ID: `enrollment_id` holds
    REASGN_BNFT_ENRLMT_ID (the practitioner) and `related_enrollment_id` holds
    RCV_BNFT_ENRLMT_ID (the entity receiving the reassigned benefits). Keeping
    them in named columns rather than only in the payload is what makes the
    Amendment 5 traversal a query instead of a scan.
    """
    __tablename__ = "tefca_ppef_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("tefca_ppef_snapshots.id"),
                         nullable=False, index=True)
    component = Column(String(40), nullable=False, index=True)

    enrollment_id = Column(String(32), index=True)
    related_enrollment_id = Column(String(32), index=True)
    npi = Column(String(10), index=True)
    payload = Column(JSONB, default=dict)

    __table_args__ = (
        Index("idx_ppef_record_component_enrollment", "component", "enrollment_id"),
        Index("idx_ppef_record_component_related", "component", "related_enrollment_id"),
        Index("idx_ppef_record_snapshot_component", "snapshot_id", "component"),
    )


class TEFCAPPEFIngestJob(Base):
    """Durable job record for one PPEF component ingestion.

    THE DATABASE IS THE AUTHORITATIVE STATE STORE.
    APScheduler triggers and polls; it never holds job state. Its default
    MemoryJobStore loses everything on process death, and that is precisely the
    failure this table exists to survive: five dev snapshots sat at `pending`
    forever because the worker was recycled and nothing recorded that the work
    had stopped.

    CONCURRENCY IS ENFORCED HERE, NOT BY THE SCHEDULER.
    `uq_ppef_job_active_component` is a PARTIAL UNIQUE INDEX over
    (component, resource_version) covering only NON-TERMINAL states. Two workers
    racing to queue the same component for the same quarter cannot both win: the
    second INSERT violates the constraint and is rejected by Postgres. That holds
    whether the app runs one worker or twenty, which matters because nothing in
    the deployment currently *enforces* single-worker — it is merely the default.

    Terminal states (COMPLETE, FAILED) leave the index, so a clean retry after a
    failure is always permitted.
    """
    __tablename__ = "tefca_ppef_ingest_jobs"

    # Lifecycle states. Order is meaningful and asserted by tests.
    STATE_QUEUED = "QUEUED"
    STATE_STARTED = "STARTED"
    STATE_DOWNLOADING = "DOWNLOADING"
    STATE_VALIDATING = "VALIDATING"
    STATE_LOADING = "LOADING"
    STATE_COMPLETE = "COMPLETE"
    STATE_FAILED = "FAILED"

    TERMINAL_STATES = (STATE_COMPLETE, STATE_FAILED)
    ACTIVE_STATES = (STATE_QUEUED, STATE_STARTED, STATE_DOWNLOADING,
                     STATE_VALIDATING, STATE_LOADING)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    component = Column(String(40), nullable=False, index=True)
    #: CMS quarterly version, e.g. "2026.07.17". Part of the concurrency key so
    #: a new quarter can be ingested while an older job is still terminal.
    resource_version = Column(String(32), index=True)
    quarter = Column(String(32))                    # e.g. "Q3 2026"

    state = Column(String(20), nullable=False, default=STATE_QUEUED, index=True)
    #: Set for ACTIVE states, NULL once terminal. This is the column the partial
    #: unique index keys on — see __table_args__.
    active_marker = Column(Boolean, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime)
    #: Written repeatedly while work is in flight. A stale value is how the
    #: reaper tells "still working" from "worker died".
    heartbeat_at = Column(DateTime, index=True)
    completed_at = Column(DateTime)
    failed_at = Column(DateTime)

    attempt_count = Column(Integer, default=0)
    error_reason = Column(Text)

    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("tefca_ppef_snapshots.id"))
    checksum = Column(String(64))
    row_count = Column(Integer)

    requested_by = Column(String(255))
    max_rows = Column(Integer)

    __table_args__ = (
        Index("idx_ppef_job_state_heartbeat", "state", "heartbeat_at"),
        Index("idx_ppef_job_component_version", "component", "resource_version"),
        # THE concurrency guard. active_marker is True only while non-terminal,
        # so at most one active job may exist per (component, version).
        Index("uq_ppef_job_active_component", "component", "resource_version",
              "active_marker", unique=True,
              postgresql_where=text("active_marker IS TRUE")),
    )
