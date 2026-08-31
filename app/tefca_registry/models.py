"""
TEFCA registry models — Phase 1B (10 tables).

The new normalized TEFCA entity registry. All tables use the shared application
Base (``app.core.database.Base``) and are physically independent of the legacy
``app.Tefca`` tables — nothing here references a legacy table and no legacy table
references anything here. The main entity table is ``tefca_reg_entities`` (the
legacy ``tefca_entities`` is deliberately NOT touched).

Conventions (match the platform_config layer):
* Enumerated fields are plain ``VARCHAR`` with allowed values documented inline.
* ``server_default`` for DDL defaults (timestamps, booleans, statuses, counts).
* Partial indexes use ``postgresql_where``.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, Date, DateTime, Float, Text,
    ForeignKey, Index, CheckConstraint, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


# ─── 1. tefca_reg_entities ────────────────────────────────────────────────────

class TefcaRegEntity(Base):
    """Master TEFCA entity record (QHIN / Participant / Sub-Participant / child)."""
    __tablename__ = "tefca_reg_entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(500), nullable=False)
    display_name = Column(String(500))
    # qhin, participant, sub_participant, child
    entity_level = Column(String(50), nullable=False)
    # health_information_network, hospital_system, health_plan,
    # health_information_exchange, provider, laboratory, pharmacy, vendor,
    # government_agency, clearinghouse, other
    entity_type = Column(String(100), nullable=False)
    # active, inactive, pending, suspended, designated, onboarding
    operational_status = Column(String(50), nullable=False, server_default=text("'active'"))
    # verified, in_review, not_verified, rejected, exception, expired
    verification_status = Column(String(50), nullable=False, server_default=text("'not_verified'"))
    state = Column(String(2))
    address = Column(Text)
    city = Column(String(200))
    zip = Column(String(10))
    county = Column(String(200))
    designation_date = Column(Date)
    onboarding_date = Column(Date)
    fhir_resource = Column(JSONB)
    exchange_purposes = Column(JSONB)
    current_version = Column(Integer, nullable=False, server_default=text("1"))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    # Weighted verification confidence, 0.0-1.0, written by the verify endpoint.
    # Nullable with no default on purpose: NULL means "never verified", which is
    # a different statement from 0.0 ("verified and every source disagreed").
    # Backfilling it would erase that distinction for every existing row.
    confidence_score = Column(Float)
    # Soft delete. Rows are never physically removed: review_records,
    # tefca_verifications and sample_entities all reference an entity, and a
    # hard delete would orphan the evidence behind a classification that has
    # already been reported. Distinct from is_active, which means "not currently
    # operating" — a legitimate state for a real participant.
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"))
    deleted_at = Column(DateTime)

    # ── RCE-delivered attributes (migration 20260822_rce_pipeline) ───────────
    #
    # These are COLUMNS rather than identifier rows because the delivered data
    # is not unique the way `tefca_entity_identifiers` assumes.
    #
    # TEFCAID was profiled at 23,566 populated / 23,325 distinct: 43 values
    # repeat across 241 rows, one of them 69 times, and each repeat is a health
    # system sharing one TEFCAID with its facilities. It identifies an
    # ORGANISATION FAMILY, not a record. Forcing it through the unique index on
    # (identifier_type, identifier_value, system_uri) fails on the 241st row —
    # and "fixing" that by relaxing the index would remove the guarantee that
    # protects NPI and the other genuinely unique identifiers.
    #
    # So: every entity carries its family TEFCAID here (indexed, NOT unique, so
    # a lookup returns the whole family, which is the correct answer), and an
    # identifier ROW is written only where the value is unique in the delivery.
    rce_org_oid = Column(String(200))          # the delivery's `id` — unique
    rce_tefcaid = Column(String(100))          # family identifier — NOT unique
    rce_hcid = Column(String(100))
    rce_aaid = Column(String(100))
    sequoia_org_type = Column(String(50))      # Participant | Subparticipant
    #: Technical exchange behaviour. NEVER the TEFCA hierarchy — see
    #: app/Tefca/applicability.py:tefca_class_of.
    org_node_type = Column(String(100))
    hl7_org_role = Column(String(100))
    org_managing_org = Column(String(200))     # QHIN OID
    is_test_record = Column(Boolean, nullable=False, server_default=text("false"))
    rce_attributes = Column(JSONB)
    #: Provenance back to the exact delivered line this entity came from.
    source_record_id = Column(UUID(as_uuid=True))

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_tefca_reg_ent_level", "entity_level"),
        Index("idx_tefca_reg_ent_op_status", "operational_status"),
        Index("idx_tefca_reg_ent_ver_status", "verification_status"),
        Index("idx_tefca_reg_ent_state", "state"),
        Index("idx_tefca_reg_ent_name", "name"),
        Index("idx_tefca_reg_ent_active", "is_active",
              postgresql_where=text("is_active = true")),
        # Non-unique on purpose — see the column comments above.
        Index("idx_tefca_reg_ent_rce_oid", "rce_org_oid"),
        Index("idx_tefca_reg_ent_rce_tefcaid", "rce_tefcaid"),
        Index("idx_tefca_reg_ent_rce_hcid", "rce_hcid"),
        Index("idx_tefca_reg_ent_source_record", "source_record_id"),
        Index("idx_tefca_reg_ent_test", "is_test_record",
              postgresql_where=text("is_test_record = true")),
    )


# ─── 2. tefca_entity_identifiers ──────────────────────────────────────────────

class TefcaEntityIdentifier(Base):
    """Identifiers attached to an entity (TEFCAID, HCID, NPI, CCN, CLIA, ...)."""
    __tablename__ = "tefca_entity_identifiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tefca_reg_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    # tefcaid, hcid, npi, ccn, clia, naic, tin, ein, fhir_id, oid, ptan, local, uuid
    identifier_type = Column(String(50), nullable=False)
    identifier_value = Column(String(500), nullable=False)
    system_uri = Column(String(500))
    is_primary = Column(Boolean, nullable=False, server_default=text("false"))
    # active, retired, expired, error
    identifier_status = Column(String(20), server_default=text("'active'"))
    effective_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        # NOTE: Postgres treats NULLs as distinct, so this UNIQUE index only
        # constrains rows where system_uri IS NOT NULL (per spec).
        Index("idx_tefca_ident_unique", "identifier_type", "identifier_value",
              "system_uri", unique=True),
        Index("idx_tefca_ident_entity", "entity_id"),
        Index("idx_tefca_ident_value", "identifier_value"),
        Index("idx_tefca_ident_type", "identifier_type"),
        Index("idx_tefca_ident_npi", "identifier_value",
              postgresql_where=text("identifier_type = 'npi'")),
    )


# ─── 3. tefca_entity_relationships ────────────────────────────────────────────

class TefcaEntityRelationship(Base):
    """Directed parent→child relationships between entities."""
    __tablename__ = "tefca_entity_relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"), nullable=False)
    child_entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"), nullable=False)
    # belongs_to, participates_in, sub_participant_of, downstream_of, member_of,
    # affiliated_with, merged_into, replaced_by, contracts_with, delegates_to,
    # historical_parent
    relationship_type = Column(String(50), nullable=False)
    effective_date = Column(Date, nullable=False)
    end_date = Column(Date)
    # active, inactive, historical, pending
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    # import, manual, fhir_sync, migration
    source = Column(String(50), server_default=text("'import'"))
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("parent_entity_id <> child_entity_id",
                        name="ck_tefca_rel_no_self"),
        UniqueConstraint("parent_entity_id", "child_entity_id", "relationship_type",
                         "effective_date", name="uq_tefca_rel_edge"),
        Index("idx_tefca_rel_parent", "parent_entity_id"),
        Index("idx_tefca_rel_child", "child_entity_id"),
        Index("idx_tefca_rel_active", "status",
              postgresql_where=text("status = 'active'")),
        Index("idx_tefca_rel_type", "relationship_type"),
    )


# ─── 4. tefca_entity_versions ─────────────────────────────────────────────────

class TefcaEntityVersion(Base):
    """Immutable snapshot of an entity at a version number."""
    __tablename__ = "tefca_entity_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    snapshot_data = Column(JSONB, nullable=False)
    # initial_import, data_update, verification, review_completed, correction,
    # merge, status_change
    change_reason = Column(String(100))
    changed_by = Column(UUID(as_uuid=True))
    change_summary = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_ver_entity", "entity_id", "version_number", unique=True),
        Index("idx_tefca_ver_entity_id", "entity_id"),
    )


# ─── 5. tefca_entity_endpoints ────────────────────────────────────────────────

class TefcaEntityEndpoint(Base):
    """Technical exchange endpoints for an entity."""
    __tablename__ = "tefca_entity_endpoints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"), nullable=False)
    # fhir_r4, direct_messaging, ihe_xcpd, ihe_xca, ihe_xdr, soap, rest
    endpoint_type = Column(String(50), nullable=False)
    url = Column(String(1000))
    connection_type = Column(String(100))
    name = Column(String(500))
    description = Column(Text)
    # active, suspended, error, off, test
    status = Column(String(20), server_default=text("'active'"))
    payload_type = Column(String(200))
    # production, validation, test
    environment = Column(String(20), server_default=text("'production'"))
    managing_org_id = Column(UUID(as_uuid=True))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_tefca_ep_entity", "entity_id"),
        Index("idx_tefca_ep_type", "endpoint_type"),
    )


# ─── 6. tefca_verification_jobs ───────────────────────────────────────────────

class TefcaVerificationJob(Base):
    """One verification run against an entity (fan-out to checks)."""
    __tablename__ = "tefca_verification_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"), nullable=False)
    entity_version_id = Column(UUID(as_uuid=True), ForeignKey("tefca_entity_versions.id"))
    # pending, running, completed, failed, cancelled
    status = Column(String(20), nullable=False, server_default=text("'pending'"))
    # manual, import, scheduled, re_verification
    trigger_type = Column(String(50), server_default=text("'manual'"))
    initiated_by = Column(UUID(as_uuid=True))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_ms = Column(Integer)
    summary = Column(JSONB)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_vj_entity", "entity_id"),
        Index("idx_tefca_vj_status", "status"),
    )


# ─── 7. tefca_verification_checks ─────────────────────────────────────────────

class TefcaVerificationCheck(Base):
    """Individual authoritative-source check within a verification job."""
    __tablename__ = "tefca_verification_checks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("tefca_verification_jobs.id"), nullable=False)
    # nppes, leie, pecos, sam, rce_directory, manual
    source = Column(String(50), nullable=False)
    identifier_used = Column(String(500))
    identifier_type = Column(String(50))
    # pass, fail, not_found, error, skipped, not_applicable
    result = Column(String(20), nullable=False)
    evidence_hash = Column(String(128))
    response_data = Column(JSONB)
    discrepancies = Column(JSONB)
    checked_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_vc_job", "job_id"),
        Index("idx_tefca_vc_source", "source"),
        Index("idx_tefca_vc_result", "result"),
    )


# ─── 8. tefca_entity_findings ─────────────────────────────────────────────────

class TefcaEntityFinding(Base):
    """A discrepancy / issue raised against an entity."""
    __tablename__ = "tefca_entity_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"), nullable=False)
    verification_check_id = Column(UUID(as_uuid=True), ForeignKey("tefca_verification_checks.id"))
    # name_mismatch, address_mismatch, npi_invalid, npi_duplicate, hcid_duplicate,
    # identifier_missing, identifier_retired, exclusion_leie, exclusion_sam,
    # enrollment_expired, enrollment_mismatch, orphan_entity, inactive_parent,
    # circular_relationship, broken_hierarchy, duplicate_entity,
    # identifier_conflict, zip_mismatch, state_mismatch
    finding_type = Column(String(100), nullable=False)
    # critical, high, medium, low, info
    severity = Column(String(20), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    evidence = Column(JSONB)
    # open, acknowledged, resolved, false_positive, deferred
    status = Column(String(20), nullable=False, server_default=text("'open'"))
    resolved_by = Column(UUID(as_uuid=True))
    resolved_at = Column(DateTime)
    resolution_notes = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_find_entity", "entity_id"),
        Index("idx_tefca_find_type", "finding_type"),
        Index("idx_tefca_find_severity", "severity"),
        Index("idx_tefca_find_status", "status"),
    )


# ─── 9. tefca_import_batches ──────────────────────────────────────────────────

class TefcaImportBatch(Base):
    """One import run — audit record for every entity-import attempt."""
    __tablename__ = "tefca_import_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # csv, fhir_bundle, fhir_api, json, manual
    source_type = Column(String(50), nullable=False)
    filename = Column(String(500))
    file_checksum = Column(String(128))
    file_size_bytes = Column(BigInteger)
    # pending, processing, completed, failed, partial
    status = Column(String(20), nullable=False, server_default=text("'pending'"))
    total_records = Column(Integer, server_default=text("0"))
    imported_count = Column(Integer, server_default=text("0"))
    updated_count = Column(Integer, server_default=text("0"))
    skipped_count = Column(Integer, server_default=text("0"))
    error_count = Column(Integer, server_default=text("0"))
    errors = Column(JSONB)
    imported_by = Column(UUID(as_uuid=True))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_ms = Column(Integer)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_imp_status", "status"),
        Index("idx_tefca_imp_source", "source_type"),
    )


# ─── 10. tefca_reg_audit_log ──────────────────────────────────────────────────

class TefcaRegAuditLog(Base):
    """APPEND-ONLY TEFCA-specific audit trail for entity operations.

    Separate from the platform ``audit_logs`` table. The append-only contract is
    enforced at the application layer (no UPDATE/DELETE code paths); this model
    is write-once by convention.
    """
    __tablename__ = "tefca_reg_audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable for batch/import-level events not tied to a single entity.
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"))
    # entity_created, entity_updated, entity_deactivated, entity_reactivated,
    # identifier_added, identifier_removed, relationship_created,
    # relationship_ended, verification_started, verification_completed,
    # finding_created, finding_resolved, review_submitted, import_started,
    # entity_import (QA-1.7; formerly import_completed), version_created,
    # status_changed, merge_executed
    action = Column(String(100), nullable=False)
    actor_id = Column(UUID(as_uuid=True))
    actor_email = Column(String(500))
    metadata_ = Column("metadata", JSONB)  # 'metadata' is reserved on Declarative
    ip_address = Column(String(45))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_audit_entity", "entity_id"),
        Index("idx_tefca_audit_action", "action"),
        Index("idx_tefca_audit_actor", "actor_id"),
        Index("idx_tefca_audit_created", "created_at"),
    )


# ─── 11. review_rules ─────────────────────────────────────────────────────────

class ReviewRule(Base):
    """A versioned B1-B4 classification rule.

    Versioned rather than mutable because a classification made last quarter has
    to stay explainable after ONC changes its guidance. Retiring sets
    retired_date and inserts a new row at version+1; the old row stays so an old
    review still resolves to the rule text that actually produced it. There is
    no DELETE for the same reason.
    """
    __tablename__ = "review_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_code = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    bucket = Column(String(2), nullable=False)          # B1 | B2 | B3 | B4
    priority = Column(Integer, nullable=False)          # lower evaluates first
    conditions = Column(JSONB, nullable=False)
    description = Column(Text)
    version = Column(Integer, nullable=False, server_default=text("1"))
    effective_date = Column(Date)
    retired_date = Column(Date)                          # NULL = current
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rule_code", "version", name="uq_review_rule_code_version"),
        Index("idx_review_rules_active", "is_active"),
        Index("idx_review_rules_priority", "priority"),
        Index("idx_review_rules_bucket", "bucket"),
    )


# ─── 12. review_records ───────────────────────────────────────────────────────

class ReviewRecord(Base):
    """One completed review, addressable by a stable REV-YYYY-NNNNNN id.

    verification_results is a SNAPSHOT taken at review time, not a pointer to
    live state. A report issued in week 31 must keep saying what it said even
    after the entity is re-verified in week 32.
    """
    __tablename__ = "review_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id = Column(String(20), nullable=False, unique=True)   # REV-2026-000001

    #: NULLABLE SINCE 20260831. A review may exist BEFORE a canonical entity does.
    #:
    #: A curated record is HELD precisely because it carries an unresolved
    #: substantive problem, and a HELD record is never promoted — so it has no
    #: entity. Requiring one here meant the records held BECAUSE they need human
    #: judgement were the only ones human review could not represent. On the
    #: delivered population that was all four HIGH-severity identity findings.
    #:
    #: The foreign key is retained: when an entity IS named it must be a real
    #: one. Only the NOT NULL was lifted.
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"),
                       nullable=True)

    #: The Area 1 anchor: the delivered line this review is about.
    #:
    #: Deliberately no foreign key, matching `tefca_entity_contacts
    #: .source_record_id` — the existing convention for a registry table
    #: referencing Area 1. Area 1 has no delete path, so the reference cannot
    #: dangle, and the registry does not take a dependency on Area 1's owner.
    source_record_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    #: WHO HOLDS THIS CASE. NULL means available.
    #:
    #: A plain UUID with no foreign key, matching `review_decision_events
    #: .actor_user_id` and `reclassified_by`. That is the existing convention
    #: and it is the right one here: a case must stay attributable after the
    #: person who held it is deactivated, and a FK would make deactivation a
    #: referential problem instead of an HR one.
    #:
    #: There is deliberately NO `case_status` column. Ownership is this column;
    #: every other state — submitted, returned, escalated, approved — is already
    #: owned by `review_decision_events` and derived from it. A second copy
    #: would be a second answer to the same question.
    assigned_to_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    #: When the current holder took it. Cleared on release. The MANNER of
    #: acquisition — self-claim or supervisor assignment — is in the audit log,
    #: not duplicated here.
    assigned_at = Column(DateTime)

    sample_id = Column(UUID(as_uuid=True), ForeignKey("review_samples.id"))
    verification_results = Column(JSONB)
    classification_bucket = Column(String(2))
    classification_rule = Column(String(20))
    classification_rule_version = Column(Integer)
    classification_rationale = Column(Text)
    # NULL until a human acts. "confirmed" | "reclassified"
    reviewer_resolution = Column(String(20))
    reclassified_to = Column(String(2))
    reclassified_by = Column(UUID(as_uuid=True))
    reclassified_at = Column(DateTime)
    resolution_rationale = Column(Text)
    reviewed_at = Column(DateTime)
    #: Set ONLY by a QA APPROVE event (B2). NULL means the determination has not
    #: passed QA and is not reportable — which is true of all 43 existing rows,
    #: correctly: they are system recommendations no human has resolved, and the
    #: gate must not be back-dated for them.
    reportable_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_review_records_entity", "entity_id"),
        Index("idx_review_records_bucket", "classification_bucket"),
        Index("idx_review_records_sample", "sample_id"),
        Index("idx_review_records_resolution", "reviewer_resolution"),
        Index("idx_review_records_created", "created_at"),
        Index("idx_review_records_assignee", "assigned_to_user_id"),
        Index("idx_review_records_source_record", "source_record_id"),
        # A review is ABOUT something. With entity_id no longer mandatory, this
        # is what stops a case from being about nothing at all — the one thing
        # dropping the NOT NULL would otherwise have permitted.
        CheckConstraint(
            "entity_id IS NOT NULL OR source_record_id IS NOT NULL",
            name="ck_review_record_has_subject"),
    )


# ─── 13. tefca_verifications ──────────────────────────────────────────────────

class TefcaVerification(Base):
    """Minimal per-source audit record for one connector call.

    Deliberately NOT full provenance — identifier used, outcome, timestamp and
    the source label, which is what an auditor needs to retrace a decision.

    verification_status carries FIVE states, not pass/fail. The distinction that
    matters for statistics: `unavailable` (source unreachable) must never count
    against an entity, while `not_found` (source reached, no record) must. A
    two-state model silently converts an outage into a finding.
    """
    __tablename__ = "tefca_verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"),
                       nullable=False)
    review_id = Column(String(20))
    source = Column(String(50), nullable=False)
    lookup_identifier = Column(String(50))
    # verified | not_found | not_checked | unavailable | failed
    verification_status = Column(String(20), nullable=False)
    detail = Column(Text)
    data_source_label = Column(String(100))
    verified_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_verif_entity", "entity_id"),
        Index("idx_tefca_verif_source", "source"),
        Index("idx_tefca_verif_status", "verification_status"),
        Index("idx_tefca_verif_review", "review_id"),
    )


# ─── 14. review_samples ───────────────────────────────────────────────────────

class ReviewSample(Base):
    """A drawn statistical sample plus the EXACT configuration behind it.

    Every parameter is stored rather than assumed from today's defaults —
    confidence, margin, proportion, FPC, seed and the rule-set version. A sample
    drawn in Q3 must be reproducible in Q4 even if the defaults have moved.
    """
    __tablename__ = "review_samples"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sample_name = Column(String(100))
    review_type = Column(String(20))                    # weekly | quarterly | priority
    population_size = Column(Integer, nullable=False)
    sample_size = Column(Integer, nullable=False)
    confidence_level = Column(Float, nullable=False)
    margin_of_error = Column(Float, nullable=False)
    proportion = Column(Float, nullable=False)
    use_fpc = Column(Boolean, nullable=False, server_default=text("true"))
    random_seed = Column(BigInteger)
    rule_set_version = Column(Integer)
    strata_config = Column(JSONB)
    strata_distribution = Column(JSONB)
    status = Column(String(20), nullable=False, server_default=text("'drawn'"))
    drawn_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime)
    created_by = Column(UUID(as_uuid=True))

    __table_args__ = (
        Index("idx_review_samples_type", "review_type"),
        Index("idx_review_samples_status", "status"),
        Index("idx_review_samples_drawn", "drawn_at"),
    )


# ─── 15. sample_entities ──────────────────────────────────────────────────────

class SampleEntity(Base):
    """Membership of one entity in one drawn sample."""
    __tablename__ = "sample_entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sample_id = Column(UUID(as_uuid=True), ForeignKey("review_samples.id"),
                       nullable=False)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"),
                       nullable=False)
    review_status = Column(String(20), nullable=False, server_default=text("'pending'"))
    review_id = Column(String(20))
    discrepancy_bucket = Column(String(2))
    stratum = Column(String(100))
    reviewed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sample_id", "entity_id", name="uq_sample_entity"),
        Index("idx_sample_entities_sample", "sample_id"),
        Index("idx_sample_entities_entity", "entity_id"),
        Index("idx_sample_entities_status", "review_status"),
    )


# ─── 16. review_reports ───────────────────────────────────────────────────────

class ReviewReport(Base):
    """An archived report, stored exactly as delivered.

    Both the structured data and the rendered HTML are kept. Reports are never
    regenerated: if the underlying entities change next week, the report issued
    this week must still say what the client received. Regenerating on read
    would quietly rewrite history.
    """
    __tablename__ = "review_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id = Column(String(30), nullable=False, unique=True)   # WR-2026-W31
    report_type = Column(String(20), nullable=False)              # weekly|quarterly|priority
    period_start = Column(Date)
    period_end = Column(Date)
    sample_id = Column(UUID(as_uuid=True), ForeignKey("review_samples.id"))
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tefca_reg_entities.id"))
    rule_set_version = Column(Integer)
    report_data = Column(JSONB)
    report_html = Column(Text)
    generated_at = Column(DateTime, nullable=False, server_default=func.now())
    generated_by = Column(UUID(as_uuid=True))

    __table_args__ = (
        Index("idx_review_reports_type", "report_type"),
        Index("idx_review_reports_period", "period_start", "period_end"),
        Index("idx_review_reports_generated", "generated_at"),
    )


# ─── 17. review_cycles ────────────────────────────────────────────────────────

class ReviewCycle(Base):
    """Ties a drawn sample to the report produced from it.

    Without this the chain is only inferable — a report references a sample, and
    the sample references entities, but nothing names the review period as a
    single traceable unit. An auditor asking "which sample backs the Q3 report,
    and which cycle was that" should get one row, not a join they have to
    reconstruct.
    """
    __tablename__ = "review_cycles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cycle_type = Column(String(20), nullable=False)   # retrospective|ongoing|priority
    cycle_number = Column(Integer)
    cycle_start = Column(Date)
    cycle_end = Column(Date)
    sample_id = Column(UUID(as_uuid=True), ForeignKey("review_samples.id"))
    report_id = Column(UUID(as_uuid=True), ForeignKey("review_reports.id"))
    status = Column(String(20), nullable=False, server_default=text("'open'"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_review_cycles_type", "cycle_type"),
        Index("idx_review_cycles_status", "status"),
        Index("idx_review_cycles_period", "cycle_start", "cycle_end"),
    )


# ─── 18. review_decision_events ───────────────────────────────────────────────

class ReviewDecisionEvent(Base):
    """Every human act on a determination. APPEND-ONLY.

    WHY AN EVENT TABLE AND NOT COLUMNS ON review_records
    ────────────────────────────────────────────────────
    QA can RETURN a determination, the analyst then issues a new one, and QA
    reviews again. That is a LOOP, and a fixed column set cannot hold a loop —
    a second QA pass would overwrite the first, which is precisely what this
    design exists to prevent. Seven columns can record one cycle; this table
    records all of them.

    NOTHING IS EVER OVERWRITTEN
    A correction is a NEW event. A superseding determination is a NEW event that
    POINTS AT the one it supersedes via `supersedes_decision_id`; the superseded
    event keeps its own actor, timestamp and rationale forever. There is
    deliberately no `override` column and no MODIFY action — an overwritten
    decision cannot be audited, and "who decided what, when" is the only
    question this table exists to answer.

    ROLE IS CAPTURED AS AT THE TIME OF THE ACT
    `actor_role` records the authority the decision was actually made under. The
    authorisation check reads the LIVE database role (so a demotion takes effect
    on the next request), but a later role change must not rewrite what a past
    decision was authorised by.

    THE 43 EXISTING DETERMINATIONS HAVE NO EVENTS, AND MUST NOT BE GIVEN ANY.
    They are system recommendations that no human has resolved. Back-dating an
    analyst or QA event for them would manufacture a human decision that never
    happened.
    """

    __tablename__ = "review_decision_events"

    #: Event types. A superseding determination is its own type, not a flag.
    ANALYST_DETERMINATION = "ANALYST_DETERMINATION"
    QA_REVIEW = "QA_REVIEW"
    SUPERSEDING_DETERMINATION = "SUPERSEDING_DETERMINATION"

    #: QA actions. No "override" — see the class docstring.
    QA_APPROVE = "APPROVE"
    QA_RETURN = "RETURN"
    QA_ESCALATE = "ESCALATE"

    DETERMINATION_EVENTS = (ANALYST_DETERMINATION, SUPERSEDING_DETERMINATION)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id = Column(String(20), ForeignKey("review_records.review_id"),
                       nullable=False, index=True)
    #: 1, 2, 3 … per review. Ordering is DATA, not an implicit timestamp sort:
    #: two events in the same transaction can share a timestamp.
    sequence_number = Column(Integer, nullable=False)
    event_type = Column(String(30), nullable=False, index=True)

    actor_user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    actor_email = Column(String(320), nullable=False)
    #: The role held WHEN THE DECISION WAS MADE, not the role held now.
    actor_role = Column(String(30), nullable=False)
    occurred_at = Column(DateTime, nullable=False, server_default=func.now())

    # determination events
    determination = Column(String(12))          # CONFIRM | RECLASSIFY
    determined_bucket = Column(String(2))       # B1..B4 when RECLASSIFY
    #: Mandatory on every event. A decision without a reason is not reviewable.
    rationale = Column(Text, nullable=False)

    # QA events
    qa_action = Column(String(10))              # APPROVE | RETURN | ESCALATE
    qa_reason = Column(Text)
    escalated_to_user_id = Column(UUID(as_uuid=True))
    escalation_reason = Column(Text)

    # supersession
    supersedes_decision_id = Column(UUID(as_uuid=True),
                                    ForeignKey("review_decision_events.id"))
    supersession_reason = Column(Text)

    #: Segregation-of-duties exception. Requires an admin grant, is counted in
    #: reconciliation, and should be disabled by configuration in production.
    sod_exception_granted_by = Column(UUID(as_uuid=True))
    sod_exception_reason = Column(Text)

    ip_address = Column(String(45))
    correlation_id = Column(UUID(as_uuid=True))

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("review_id", "sequence_number", name="uq_review_event_seq"),
        # The invariants, enforced by the database rather than by convention.
        CheckConstraint(
            "event_type <> 'QA_REVIEW' OR qa_action IS NOT NULL",
            name="ck_review_event_qa_action"),
        CheckConstraint(
            "qa_action IS NULL OR qa_action IN ('APPROVE','RETURN','ESCALATE')",
            name="ck_review_event_qa_action_vocab"),
        CheckConstraint(
            "qa_action <> 'ESCALATE' OR "
            "(escalated_to_user_id IS NOT NULL AND escalation_reason IS NOT NULL)",
            name="ck_review_event_escalation_complete"),
        CheckConstraint(
            "supersedes_decision_id IS NULL OR supersession_reason IS NOT NULL",
            name="ck_review_event_supersession_reason"),
        CheckConstraint(
            "length(btrim(rationale)) >= 10", name="ck_review_event_rationale"),
        CheckConstraint(
            "event_type IN ('ANALYST_DETERMINATION','QA_REVIEW',"
            "'SUPERSEDING_DETERMINATION')", name="ck_review_event_type"),
        CheckConstraint(
            "determination IS NULL OR determination IN ('CONFIRM','RECLASSIFY')",
            name="ck_review_event_determination"),
        Index("idx_review_event_review_seq", "review_id", "sequence_number"),
        Index("idx_review_event_supersedes", "supersedes_decision_id"),
        Index("idx_review_event_qa_action", "qa_action"),
    )


# Ordered parents-first for FK-scoped create_all / drop_all and the migration.
TEFCA_REG_TABLE_ORDER = [
    "tefca_reg_entities",
    "tefca_entity_identifiers",
    "tefca_entity_relationships",
    "tefca_entity_versions",
    "tefca_entity_endpoints",
    "tefca_verification_jobs",
    "tefca_verification_checks",
    "tefca_entity_findings",
    "tefca_import_batches",
    "tefca_reg_audit_log",
    # Tasks 3-5 operational engine.
    "review_rules",
    "review_samples",        # before review_records / sample_entities (FK parent)
    "review_records",
    "tefca_verifications",
    "sample_entities",
    "review_reports",
    "review_cycles",         # after samples + reports (FK parents)
    # B2 QA gate. After review_records (FK parent).
    "review_decision_events",
]
