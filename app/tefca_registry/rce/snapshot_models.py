"""ORM for the September-2026 snapshot evidence tables and the Release-1
source-layer foundation (migration 20260921_september_snapshot).

Every table here is APPEND-ONLY for the runtime role: a later fact is a new
row that names what it supersedes. Nothing is edited, nothing is deleted.

    RceDeliveryDelta              per-id classification between two deliveries
    RceEntityPresence             entity x intake: present/absent + active
    TefcaRelationshipObservation  versioned relationship observation per snapshot
    ArcStaleMark                  "re-evaluation required" mark, resolvable by a
                                  later RESOLVED row; historical reports untouched
    SourceSnapshot                layer 1: immutable snapshot registry + approval gate
    EntitySourceMatch             layer 4: versioned many-to-many crosswalk
    ArcAssessmentRun              layer 5: snapshot-specific ARC run provenance
"""
from __future__ import annotations

import uuid

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
                        Integer, Numeric, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# delta classifications (mirrors delivery_delta vocabulary; persisted here)
DELTA_NEW = "NEW"
DELTA_CHANGED = "CHANGED"
DELTA_UNCHANGED = "UNCHANGED"
DELTA_NOT_PRESENT = "NOT_PRESENT_IN_CURRENT_DELIVERY"

# relationship observations
OBS_ASSERTED = "ASSERTED"
OBS_SUPERSEDED = "SUPERSEDED"
OBS_UNRESOLVED_PARENT = "UNRESOLVED_PARENT"
OBS_CROSS_QHIN_REFUSED = "CROSS_QHIN_REFUSED"
OBS_SNAPSHOT_MISMATCH = "SNAPSHOT_MISMATCH"
OBS_ABSENT = "ABSENT"
OBS_ROLLED_BACK = "ROLLED_BACK"     # a replacement edge retired by compensation
OBS_RESTORED = "RESTORED"           # a superseded edge made current again

# stale-mark reasons: the MATERIAL changes that require ARC re-evaluation.
STALE_PART_OF_CHANGED = "PART_OF_CHANGED"
STALE_QHIN_CHANGED = "QHIN_CHANGED"
STALE_ACTIVE_CHANGED = "ACTIVE_CHANGED"
STALE_PURPOSES_CHANGED = "PURPOSES_CHANGED"
STALE_NPI_CHANGED = "NPI_CHANGED"
STALE_CCN_EVIDENCE_CHANGED = "CCN_EVIDENCE_CHANGED"
STALE_SANCTION_CHANGED = "SANCTION_CHANGED"
STALE_MATCH_CHANGED = "MATCH_CHANGED"
STALE_ABSENT_FROM_DELIVERY = "ABSENT_FROM_DELIVERY"

# source snapshot
SNAPSHOT_PENDING = "PENDING"        # registered; the system never approves
SNAPSHOT_APPROVED = "APPROVED"      # an authorised human approved, after reconciliation
SNAPSHOT_REJECTED = "REJECTED"
SNAPSHOT_SUPERSEDED = "SUPERSEDED"
SNAPSHOT_FAILED = "FAILED"          # snapshot effects did not complete; retry allowed
SNAPSHOT_ROLLED_BACK = "ROLLED_BACK"  # relationship compensation applied
#: A snapshot whose chain tip is one of these took effect at some point and
#: its evidence belongs in CURRENT views. PENDING/FAILED/REJECTED/ROLLED_BACK do not.
SNAPSHOT_EFFECTIVE = (SNAPSHOT_APPROVED, SNAPSHOT_SUPERSEDED)
SOURCE_ONC_RCE = "ONC_RCE"
SOURCE_IQVIA_HCO = "IQVIA_HCO"
SOURCE_IQVIA_HCP = "IQVIA_HCP"
SOURCE_IQVIA_AFFILIATION = "IQVIA_AFFILIATION"

# entity source match
METHOD_NPI_EXACT_TYPE2 = "NPI_EXACT_TYPE2"
METHOD_CCN_CANDIDATE = "CCN_CANDIDATE"
METHOD_EXACT_NAME_ADDRESS_PHONE = "EXACT_NAME_ADDRESS_PHONE"
METHOD_FUZZY_DISCOVERY = "FUZZY_DISCOVERY"
METHOD_ANALYST = "ANALYST"
MATCH_AUTO_APPROVED = "AUTO_APPROVED"
MATCH_CANDIDATE = "CANDIDATE"
MATCH_ANALYST_APPROVED = "ANALYST_APPROVED"
MATCH_QA_APPROVED = "QA_APPROVED"
MATCH_REJECTED = "REJECTED"
MATCH_EXCEPTION = "EXCEPTION"
MATCH_SUPERSEDED = "SUPERSEDED"


class RceDeliveryDelta(Base):
    __tablename__ = "rce_delivery_delta"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    previous_intake_id = Column(UUID(as_uuid=True),
                                ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                                nullable=False)
    current_intake_id = Column(UUID(as_uuid=True),
                               ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                               nullable=False, index=True)
    rce_org_oid = Column(Text, nullable=False)
    classification = Column(String(48), nullable=False)
    changed_fields = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    field_changes = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    material = Column(Boolean, nullable=False, server_default=text("false"))
    previous_sha256 = Column(String(64))
    current_sha256 = Column(String(64))
    current_source_record_id = Column(UUID(as_uuid=True),
                                      ForeignKey("rce_source_records.id", ondelete="RESTRICT"))
    delta_version = Column(String(16), nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))

    __table_args__ = (
        UniqueConstraint("previous_intake_id", "current_intake_id", "rce_org_oid",
                         name="uq_rce_delivery_delta_pair_oid"),
        CheckConstraint("previous_intake_id <> current_intake_id",
                        name="ck_rce_delivery_delta_pair"),
    )


class RceEntityPresence(Base):
    __tablename__ = "rce_entity_presence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False)
    rce_org_oid = Column(Text, nullable=False)
    present = Column(Boolean, nullable=False)
    active_raw = Column(String(16))
    active_normalized = Column(String(1))
    source_record_id = Column(UUID(as_uuid=True),
                              ForeignKey("rce_source_records.id", ondelete="RESTRICT"))
    snapshot_received_at = Column(DateTime(timezone=True), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("entity_id", "intake_id", name="uq_rce_presence_entity_intake"),
    )


class TefcaRelationshipObservation(Base):
    __tablename__ = "tefca_relationship_observations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    relationship_id = Column(UUID(as_uuid=True),
                             ForeignKey("tefca_entity_relationships.id", ondelete="RESTRICT"))
    child_entity_id = Column(UUID(as_uuid=True),
                             ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"),
                             nullable=False)
    parent_entity_id = Column(UUID(as_uuid=True),
                              ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"))
    relationship_type = Column(String(50), nullable=False)
    observation = Column(String(24), nullable=False)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False)
    effective_boundary = Column(Date, nullable=False)
    supersedes_relationship_id = Column(
        UUID(as_uuid=True), ForeignKey("tefca_entity_relationships.id", ondelete="RESTRICT"))
    delivered_parent_oid = Column(Text)
    reason = Column(Text, nullable=False)
    actor = Column(String(320), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))


class ArcStaleMark(Base):
    __tablename__ = "arc_stale_marks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False)
    review_id = Column(String(20))
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(String(12), nullable=False)
    reason = Column(String(48), nullable=False)
    changed_fields = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    detail = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    resolves_mark_id = Column(UUID(as_uuid=True),
                              ForeignKey("arc_stale_marks.id", ondelete="RESTRICT"))
    actor = Column(String(320), nullable=False)
    marked_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))


class SourceSnapshot(Base):
    __tablename__ = "source_snapshot"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_system = Column(String(32), nullable=False)
    snapshot_label = Column(String(200), nullable=False)
    sha256 = Column(String(64), nullable=False)
    record_count = Column(Integer, nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"))
    status = Column(String(16), nullable=False, server_default=text("'PENDING'"))
    approved_by = Column(String(320))
    approved_role = Column(String(64))
    approved_at = Column(DateTime(timezone=True))
    approval_ref = Column(String(120))
    reconciliation_snapshot_id = Column(UUID(as_uuid=True))
    reconciliation_hash = Column(String(64))
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))
    request_id = Column(String(64))
    supersedes_snapshot_id = Column(UUID(as_uuid=True),
                                    ForeignKey("source_snapshot.id", ondelete="RESTRICT"))
    metadata_ = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_by = Column(String(320), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)


class EntitySourceMatch(Base):
    __tablename__ = "entity_source_match"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"), nullable=False)
    source_system = Column(String(32), nullable=False)
    source_snapshot_id = Column(UUID(as_uuid=True),
                                ForeignKey("source_snapshot.id", ondelete="RESTRICT"),
                                nullable=False)
    source_record_key = Column(Text, nullable=False)
    match_method = Column(String(32), nullable=False)
    match_status = Column(String(16), nullable=False)
    confidence = Column(Numeric(5, 4))
    evidence = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    matching_model_version = Column(String(32), nullable=False)
    proposed_by = Column(String(320), nullable=False)
    reviewed_by = Column(String(320))
    reviewed_at = Column(DateTime(timezone=True))
    qa_by = Column(String(320))
    qa_at = Column(DateTime(timezone=True))
    valid_from = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    valid_to = Column(DateTime(timezone=True))
    supersedes_match_id = Column(UUID(as_uuid=True),
                                 ForeignKey("entity_source_match.id", ondelete="RESTRICT"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)


class ArcAssessmentRun(Base):
    __tablename__ = "arc_assessment_run"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tefca_snapshot_id = Column(UUID(as_uuid=True),
                               ForeignKey("source_snapshot.id", ondelete="RESTRICT"),
                               nullable=False)
    iqvia_hco_snapshot_id = Column(UUID(as_uuid=True),
                                   ForeignKey("source_snapshot.id", ondelete="RESTRICT"))
    iqvia_hcp_snapshot_id = Column(UUID(as_uuid=True),
                                   ForeignKey("source_snapshot.id", ondelete="RESTRICT"))
    iqvia_affiliation_snapshot_id = Column(
        UUID(as_uuid=True), ForeignKey("source_snapshot.id", ondelete="RESTRICT"))
    matching_model_version = Column(String(32), nullable=False)
    rule_set_version = Column(String(16), nullable=False)
    delivery_job_id = Column(UUID(as_uuid=True),
                             ForeignKey("rce_delivery_jobs.id", ondelete="RESTRICT"))
    review_cycle_id = Column(UUID(as_uuid=True))
    report_id = Column(String(64))
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    status = Column(String(16), nullable=False, server_default=text("'STARTED'"))
    summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    actor = Column(String(320), nullable=False)
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))
