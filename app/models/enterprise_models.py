"""
DocuAction Enterprise Data Models
Stateful, Auditable, Tenant-Isolated System of Record

EXTENDS existing models — does NOT replace.
All tables use tenant_id for strict isolation.
All state changes logged to StateAuditLog (append-only).

Tables:
  1. Tenant           — Organization isolation boundary
  2. TenantUser       — User-to-tenant mapping with roles
  3. Context          — Source material (transcript/document) registry
  4. ProcessJob       — Idempotent job tracking with retry
  5. Decision         — System of Record for all AI-extracted decisions
  6. Action           — Execution layer linked to decisions
  7. Traceability     — Source attribution (page, quote, confidence)
  8. PolicyValidation — Governance gate results per entity
  9. StateAuditLog    — Immutable append-only state transition log
  10. ExecutionQueue  — Async action execution queue
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime,
    ForeignKey, JSON, Index, Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, declarative_base

# Use existing Base if available, otherwise create new
try:
    from app.models.database import Base
except ImportError:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


# ═══════════════════════════════════════════════════════
# 1. TENANT — Organization isolation boundary
# ═══════════════════════════════════════════════════════

class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = {"extend_existing": True}

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    domain = Column(String(255), nullable=True)  # e.g. "enterprise", "healthcare"
    plan = Column(String(50), default="free")  # free, pro, business, enterprise
    settings = Column(JSON, default=dict)  # org-level config
    governance_policy = Column(String(50), default="enterprise")  # policy domain
    strict_mode = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# 2. TENANT USER — User-to-tenant with role
# ═══════════════════════════════════════════════════════

class TenantUser(Base):
    __tablename__ = "tenant_users"
    __table_args__ = (
        Index("ix_tenant_users_tenant", "tenant_id"),
        Index("ix_tenant_users_user", "user_id"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String, nullable=False)  # References existing users table
    role = Column(String(50), default="member")  # owner, admin, member, viewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# 3. CONTEXT — Source material registry
# ═══════════════════════════════════════════════════════

class Context(Base):
    __tablename__ = "contexts"
    __table_args__ = (
        Index("ix_contexts_tenant", "tenant_id"),
        Index("ix_contexts_type", "type"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    type = Column(String(50), nullable=False)  # transcript, document, meeting, audio
    source_name = Column(String(500), nullable=False)  # filename or meeting title
    source_id = Column(String, nullable=True)  # links to existing documents table
    content_hash = Column(String(64), nullable=True)  # SHA-256 for dedup
    word_count = Column(Integer, default=0)
    extra_data = Column("metadata", JSON, default=dict)  # file_type, size, duration, etc.
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# 4. PROCESS JOB — Idempotent job tracking
# ═══════════════════════════════════════════════════════

class ProcessJob(Base):
    __tablename__ = "process_jobs"
    __table_args__ = (
        Index("ix_jobs_tenant", "tenant_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_idempotency", "idempotency_key", unique=True),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    context_id = Column(String, ForeignKey("contexts.id"), nullable=True)
    idempotency_key = Column(String(255), nullable=False, unique=True)  # prevents duplicate processing
    job_type = Column(String(50), nullable=False)  # summary, actions, insights, email, brief, meeting
    status = Column(String(50), default="pending")  # pending → processing → completed → failed
    priority = Column(Integer, default=5)  # 1=highest, 10=lowest
    input_params = Column(JSON, default=dict)  # action_type, domain, context_focus, etc.
    output_id = Column(String, nullable=True)  # links to existing outputs table
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    failure_reason = Column(Text, nullable=True)
    processing_time_ms = Column(Integer, nullable=True)
    model_used = Column(String(100), nullable=True)
    created_by = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Valid state transitions
    VALID_TRANSITIONS = {
        "pending": ["processing", "cancelled"],
        "processing": ["completed", "failed"],
        "failed": ["pending"],  # retry
        "completed": [],  # terminal
        "cancelled": [],  # terminal
    }


# ═══════════════════════════════════════════════════════
# 5. DECISION — System of Record
# ═══════════════════════════════════════════════════════

class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_tenant", "tenant_id"),
        Index("ix_decisions_context", "context_id"),
        Index("ix_decisions_status", "status"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    context_id = Column(String, ForeignKey("contexts.id"), nullable=True)
    job_id = Column(String, ForeignKey("process_jobs.id"), nullable=True)

    # Decision content
    decision_text = Column(Text, nullable=False)
    options_considered = Column(JSON, default=list)  # ["option A", "option B"]
    selected_option = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    decided_by = Column(String(255), nullable=True)  # person who made the decision
    decision_date = Column(DateTime, nullable=True)  # when decision was made (from content)

    # AI metadata
    confidence_score = Column(Float, default=0.0)
    accuracy_score = Column(Float, nullable=True)
    reliability = Column(String(20), nullable=True)  # HIGH, MEDIUM, LOW
    model_name = Column(String(100), nullable=True)
    model_version = Column(String(50), nullable=True)
    prompt_version = Column(String(50), default="v1")

    # Lifecycle
    status = Column(String(50), default="draft")  # draft → pending_review → approved → superseded
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    superseded_by = Column(String, nullable=True)  # points to newer decision ID

    # Stakeholder alignment
    stakeholders = Column(JSON, default=list)
    alignment_score = Column(Float, nullable=True)

    # Audit
    version = Column(Integer, default=1)
    is_immutable = Column(Boolean, default=False)  # True once approved
    correlation_id = Column(String(20), nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    VALID_TRANSITIONS = {
        "draft": ["pending_review", "approved", "rejected"],
        "pending_review": ["approved", "rejected", "revision_requested"],
        "revision_requested": ["draft", "pending_review"],
        "approved": ["superseded"],  # can only be superseded, never deleted
        "rejected": ["draft"],  # can go back to draft for rework
        "superseded": [],  # terminal — replaced by newer version
    }


# ═══════════════════════════════════════════════════════
# 6. ACTION — Execution layer linked to decisions
# ═══════════════════════════════════════════════════════

class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (
        Index("ix_actions_tenant", "tenant_id"),
        Index("ix_actions_decision", "decision_id"),
        Index("ix_actions_status", "status"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    decision_id = Column(String, ForeignKey("decisions.id"), nullable=True)
    context_id = Column(String, ForeignKey("contexts.id"), nullable=True)
    job_id = Column(String, ForeignKey("process_jobs.id"), nullable=True)

    # Action content
    type = Column(String(50), nullable=False)  # email, jira_ticket, servicenow, calendar, notification, manual
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    owner = Column(String(255), nullable=True)
    deadline = Column(DateTime, nullable=True)
    priority = Column(String(20), default="medium")  # critical, high, medium, low

    # Execution
    payload = Column(JSON, default=dict)  # email body, ticket fields, etc.
    status = Column(String(50), default="draft")  # draft → approved → queued → executing → completed → failed
    executed_by = Column(String, nullable=True)  # user or "system"
    executed_at = Column(DateTime, nullable=True)
    execution_result = Column(JSON, nullable=True)  # API response, confirmation, etc.
    failure_reason = Column(Text, nullable=True)

    # HITL
    requires_approval = Column(Boolean, default=True)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)

    # Audit
    correlation_id = Column(String(20), nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    VALID_TRANSITIONS = {
        "draft": ["approved", "rejected", "cancelled"],
        "approved": ["queued"],
        "queued": ["executing"],
        "executing": ["completed", "failed"],
        "failed": ["queued"],  # retry
        "completed": [],  # terminal
        "rejected": [],  # terminal
        "cancelled": [],  # terminal
    }


# ═══════════════════════════════════════════════════════
# 7. TRACEABILITY — Source attribution
# ═══════════════════════════════════════════════════════

class Traceability(Base):
    __tablename__ = "traceability"
    __table_args__ = (
        Index("ix_trace_decision", "decision_id"),
        Index("ix_trace_tenant", "tenant_id"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    decision_id = Column(String, ForeignKey("decisions.id"), nullable=True)
    action_id = Column(String, ForeignKey("actions.id"), nullable=True)
    output_id = Column(String, nullable=True)  # links to existing outputs table

    # Source reference
    source_document = Column(String(500), nullable=True)
    source_type = Column(String(50), nullable=True)  # document, transcript, meeting
    page = Column(Integer, nullable=True)
    timestamp_ref = Column(String(50), nullable=True)  # for audio: "04:15"
    quote = Column(Text, nullable=True)  # exact supporting text
    section = Column(String(255), nullable=True)  # heading or section name

    # Verification
    confidence = Column(Float, default=0.0)
    match_method = Column(String(50), nullable=True)  # phrase_match, sequence_match, weak_match
    verified = Column(Boolean, default=False)
    verified_by = Column(String, nullable=True)  # user who verified, or "system"
    verified_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# 8. POLICY VALIDATION — Governance gate results
# ═══════════════════════════════════════════════════════

class PolicyValidation(Base):
    __tablename__ = "policy_validations"
    __table_args__ = (
        Index("ix_pv_entity", "entity_id", "entity_type"),
        Index("ix_pv_tenant", "tenant_id"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    entity_id = Column(String, nullable=False)  # decision_id, action_id, or output_id
    entity_type = Column(String(50), nullable=False)  # decision, action, output

    # Policy applied
    policy_name = Column(String(100), nullable=True)
    domain = Column(String(50), nullable=True)
    strict_mode = Column(Boolean, default=True)

    # Results
    validation_result = Column(JSON, nullable=False)  # full governance gate output
    gate_result = Column(String(50), nullable=True)  # approved, review_required, blocked
    accuracy_score = Column(Float, nullable=True)
    reliability = Column(String(20), nullable=True)
    violations_count = Column(Integer, default=0)

    # Certificate
    certificate_id = Column(String(50), nullable=True)
    certificate_hash = Column(String(64), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# 9. STATE AUDIT LOG — Immutable append-only
# ═══════════════════════════════════════════════════════

class StateAuditLog(Base):
    __tablename__ = "state_audit_log"
    __table_args__ = (
        Index("ix_audit_entity", "entity_id", "entity_type"),
        Index("ix_audit_tenant", "tenant_id"),
        Index("ix_audit_timestamp", "timestamp"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    entity_type = Column(String(50), nullable=False)  # decision, action, job, policy
    old_state = Column(String(50), nullable=True)
    new_state = Column(String(50), nullable=False)
    change_reason = Column(Text, nullable=True)
    changed_by = Column(String, nullable=False)  # user_id or "system"
    ip_address = Column(String(50), nullable=True)
    extra_data = Column("metadata", JSON, default=dict)  # additional context
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    # This table is APPEND-ONLY. No updates. No deletes.
    # Enforced at the application layer.


# ═══════════════════════════════════════════════════════
# 10. EXECUTION QUEUE — Async action execution
# ═══════════════════════════════════════════════════════

class ExecutionQueue(Base):
    __tablename__ = "execution_queue"
    __table_args__ = (
        Index("ix_eq_status", "status"),
        Index("ix_eq_tenant", "tenant_id"),
        Index("ix_eq_priority", "priority"),
        {"extend_existing": True},
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    action_id = Column(String, ForeignKey("actions.id"), nullable=False)
    status = Column(String(50), default="pending")  # pending → processing → completed → failed
    priority = Column(Integer, default=5)  # 1=highest
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    last_error = Column(Text, nullable=True)
    scheduled_at = Column(DateTime, nullable=True)  # for delayed execution
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# TABLE CREATION HELPER
# ═══════════════════════════════════════════════════════

async def create_enterprise_tables(engine):
    """Create all enterprise tables. Safe to call multiple times."""
    from sqlalchemy.ext.asyncio import AsyncConnection
    import logging
    logger = logging.getLogger("docuaction.enterprise_models")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Enterprise tables created/verified")
        return True
    except Exception as e:
        logger.error(f"Enterprise table creation failed: {e}")
        return False
