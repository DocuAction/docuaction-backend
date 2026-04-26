"""
DocuAction — Migration Intelligence Database Models
Isolated table namespace: migration_*

HARD CONSTRAINT: These models NEVER alter existing tables
(documents, outputs, audio_files, transcripts, etc.)

Shared tables (decisions, audit_log) are accessed via module_id='data_systems' scoping.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Float, Integer, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


# ═══════════════════════════════════════════════════════
# MIGRATION PROJECTS
# ═══════════════════════════════════════════════════════

class MigrationProject(Base):
    __tablename__ = "migration_projects"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(20), unique=True, nullable=False)  # MPRJ-XXXXXXXX
    tenant_id = Column(String(50), nullable=False, default="default")
    user_id = Column(UUID(as_uuid=True), nullable=False)  # Owner

    name = Column(String(300), nullable=False)
    description = Column(Text, default="")
    status = Column(String(30), default="active")  # active, paused, completed, archived

    source_system = Column(String(200), default="")  # e.g., "Legacy Oracle EBS"
    target_system = Column(String(200), default="")  # e.g., "Salesforce"

    # Metrics
    total_schemas = Column(Integer, default=0)
    total_fields = Column(Integer, default=0)
    total_mappings = Column(Integer, default=0)
    approved_mappings = Column(Integer, default=0)
    overall_risk_score = Column(Float, default=0)
    foia_readiness_score = Column(Float, default=0)

    # Governance
    correlation_id = Column(String(30))
    module_id = Column(String(30), default="data_systems")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# MIGRATION SCHEMAS (source and target)
# ═══════════════════════════════════════════════════════

class MigrationSchema(Base):
    __tablename__ = "migration_schemas"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schema_id = Column(String(20), unique=True, nullable=False)  # MSCH-XXXXXXXX
    project_id = Column(UUID(as_uuid=True), ForeignKey("migration_projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)

    name = Column(String(300), nullable=False)  # Schema/database name
    schema_type = Column(String(20), default="source")  # source, target
    system_type = Column(String(50), default="")  # oracle, salesforce, sap, postgresql, cobol, etc.

    # Ingestion
    input_type = Column(String(30), default="ddl")  # ddl, csv, api, cobol, sql_view, stored_proc
    file_path = Column(String(500), default="")
    file_hash = Column(String(64), default="")
    raw_content_length = Column(Integer, default=0)

    # Analysis results
    table_count = Column(Integer, default=0)
    field_count = Column(Integer, default=0)
    relationship_count = Column(Integer, default=0)
    pii_field_count = Column(Integer, default=0)

    # AI analysis
    analysis_result = Column(JSON, default={})  # Full schema intelligence report
    confidence = Column(Float, default=0)
    model_used = Column(String(50), default="")
    processing_time_ms = Column(Float, default=0)

    status = Column(String(20), default="uploaded")  # uploaded, analyzing, analyzed, failed
    module_id = Column(String(30), default="data_systems")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# MIGRATION FIELDS (per-field metadata + profiling)
# ═══════════════════════════════════════════════════════

class MigrationField(Base):
    __tablename__ = "migration_fields"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    field_id = Column(String(20), unique=True, nullable=False)  # MFLD-XXXXXXXX
    schema_id = Column(UUID(as_uuid=True), ForeignKey("migration_schemas.id", ondelete="CASCADE"), nullable=False)

    table_name = Column(String(200), nullable=False)
    field_name = Column(String(200), nullable=False)
    data_type = Column(String(100), default="")
    max_length = Column(Integer, nullable=True)
    is_nullable = Column(Boolean, default=True)
    is_primary_key = Column(Boolean, default=False)
    is_foreign_key = Column(Boolean, default=False)
    fk_references = Column(String(500), default="")  # table.field

    # Profiling results
    profiling_result = Column(JSON, default={})
    null_percentage = Column(Float, default=0)
    unique_percentage = Column(Float, default=0)
    pattern_detected = Column(String(100), default="")  # json_in_string, csv_in_string, etc.
    sample_values = Column(JSON, default=[])

    # PII/PHI detection
    is_pii = Column(Boolean, default=False)
    pii_type = Column(String(50), default="")  # ssn, email, phone, mrn, etc.
    foia_exemption = Column(String(20), default="")  # e.g., "6", "7C"

    # Business context (AI-generated)
    business_description = Column(Text, default="")
    confidence = Column(Float, default=0)

    module_id = Column(String(30), default="data_systems")
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# MIGRATION MAPPINGS (source → target field mappings)
# ═══════════════════════════════════════════════════════

class MigrationMapping(Base):
    __tablename__ = "migration_mappings"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mapping_id = Column(String(20), unique=True, nullable=False)  # MMAP-XXXXXXXX
    project_id = Column(UUID(as_uuid=True), ForeignKey("migration_projects.id", ondelete="CASCADE"), nullable=False)

    # Source
    source_schema_id = Column(UUID(as_uuid=True), ForeignKey("migration_schemas.id"), nullable=False)
    source_table = Column(String(200), nullable=False)
    source_field = Column(String(200), nullable=False)
    source_type = Column(String(100), default="")

    # Target
    target_schema_id = Column(UUID(as_uuid=True), ForeignKey("migration_schemas.id"), nullable=True)
    target_table = Column(String(200), default="")
    target_field = Column(String(200), default="")
    target_type = Column(String(100), default="")

    # Transformation
    transformation_rule = Column(Text, default="")  # SQL expression or transformation spec
    transformation_type = Column(String(50), default="direct")  # direct, convert, calculate, split, merge, custom

    # AI suggestion
    confidence = Column(Float, default=0)
    rationale = Column(Text, default="")  # Why AI suggested this
    alternatives = Column(JSON, default=[])  # Top 3 alternatives with scores
    risk_factors = Column(JSON, default=[])

    # Decision tracking
    status = Column(String(30), default="proposed")
    # States: proposed, in_review, conflicted, multi_approve, approved, implemented, validated, failed
    decision_id = Column(String(30), default="")  # Links to global Decision Bank
    assigned_to = Column(UUID(as_uuid=True), nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approval_justification = Column(Text, default="")
    approved_at = Column(DateTime, nullable=True)

    # Impact analysis
    impact_score = Column(Float, default=0)
    affected_reports = Column(JSON, default=[])
    affected_integrations = Column(JSON, default=[])
    rollback_risk = Column(String(20), default="low")  # low, medium, high, critical

    # Version tracking
    version = Column(Integer, default=1)
    supersedes = Column(String(20), default="")  # Previous mapping_id

    module_id = Column(String(30), default="data_systems")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# MAPPING VERSIONS (immutable history)
# ═══════════════════════════════════════════════════════

class MigrationMappingVersion(Base):
    __tablename__ = "migration_mapping_versions"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mapping_id = Column(UUID(as_uuid=True), ForeignKey("migration_mappings.id", ondelete="CASCADE"), nullable=False)

    version = Column(Integer, nullable=False)
    change_type = Column(String(30), default="created")  # created, modified, approved, overridden, reverted
    changed_by = Column(UUID(as_uuid=True), nullable=False)
    change_reason = Column(Text, default="")

    # Snapshot of mapping state at this version
    snapshot = Column(JSON, nullable=False, default={})

    module_id = Column(String(30), default="data_systems")
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# LOGIC ARTIFACTS (stored procs, views, triggers, COBOL)
# ═══════════════════════════════════════════════════════

class MigrationLogicArtifact(Base):
    __tablename__ = "migration_logic_artifacts"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = Column(String(20), unique=True, nullable=False)  # MART-XXXXXXXX
    schema_id = Column(UUID(as_uuid=True), ForeignKey("migration_schemas.id", ondelete="CASCADE"), nullable=False)

    artifact_type = Column(String(30), nullable=False)  # stored_procedure, view, trigger, cobol_copybook, shell_script
    name = Column(String(300), nullable=False)
    raw_content = Column(Text, default="")

    # AI extraction
    extracted_rules = Column(JSON, default=[])  # Business rules found
    dependencies = Column(JSON, default=[])  # Tables/fields referenced
    transformations = Column(JSON, default=[])  # Calculations, conversions found
    is_dead_code = Column(Boolean, default=False)
    severity = Column(String(20), default="advisory")  # critical, important, advisory

    confidence = Column(Float, default=0)
    model_used = Column(String(50), default="")

    module_id = Column(String(30), default="data_systems")
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# PROFILING RESULTS (deep data profiling per field)
# ═══════════════════════════════════════════════════════

class MigrationProfilingResult(Base):
    __tablename__ = "migration_profiling_results"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profiling_id = Column(String(20), unique=True, nullable=False)  # MPRF-XXXXXXXX
    field_id = Column(UUID(as_uuid=True), ForeignKey("migration_fields.id", ondelete="CASCADE"), nullable=False)
    schema_id = Column(UUID(as_uuid=True), ForeignKey("migration_schemas.id", ondelete="CASCADE"), nullable=False)

    # Statistical profile
    total_records = Column(Integer, default=0)
    null_count = Column(Integer, default=0)
    unique_count = Column(Integer, default=0)
    min_length = Column(Integer, default=0)
    max_length = Column(Integer, default=0)
    avg_length = Column(Float, default=0)

    # Pattern analysis
    patterns_detected = Column(JSON, default=[])  # [{pattern, percentage, sample}]
    value_distribution = Column(JSON, default={})  # top-N values with counts
    format_variants = Column(JSON, default=[])  # Date formats, phone formats, etc.
    outliers = Column(JSON, default=[])  # Statistical outliers

    # PII scan
    pii_detected = Column(Boolean, default=False)
    pii_type = Column(String(50), default="")
    pii_confidence = Column(Float, default=0)

    module_id = Column(String(30), default="data_systems")
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# VALIDATION RUNS
# ═══════════════════════════════════════════════════════

class MigrationValidationRun(Base):
    __tablename__ = "migration_validation_runs"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_id = Column(String(20), unique=True, nullable=False)  # MVAL-XXXXXXXX
    project_id = Column(UUID(as_uuid=True), ForeignKey("migration_projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)

    validation_type = Column(String(30), default="pre_migration")  # pre_migration, post_migration, reconciliation
    status = Column(String(20), default="running")  # running, passed, failed, partial

    # Results
    total_checks = Column(Integer, default=0)
    passed_checks = Column(Integer, default=0)
    failed_checks = Column(Integer, default=0)
    warnings = Column(Integer, default=0)
    results = Column(JSON, default=[])

    processing_time_ms = Column(Float, default=0)
    module_id = Column(String(30), default="data_systems")
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# MANIFEST VERSIONS (published ETL manifests)
# ═══════════════════════════════════════════════════════

class MigrationManifestVersion(Base):
    __tablename__ = "migration_manifest_versions"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manifest_id = Column(String(20), unique=True, nullable=False)  # MMAN-XXXXXXXX
    project_id = Column(UUID(as_uuid=True), ForeignKey("migration_projects.id", ondelete="CASCADE"), nullable=False)
    published_by = Column(UUID(as_uuid=True), nullable=False)

    version = Column(Integer, nullable=False)
    version_hash = Column(String(64), nullable=False)  # SHA-256 of manifest content

    # Content
    manifest_content = Column(JSON, nullable=False, default={})
    total_mappings = Column(Integer, default=0)
    approved_mappings = Column(Integer, default=0)

    status = Column(String(20), default="published")  # published, superseded, withdrawn
    module_id = Column(String(30), default="data_systems")
    created_at = Column(DateTime, default=datetime.utcnow)
