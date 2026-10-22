"""Persistence for the isolated capability — on its OWN declarative base.

WHY A SEPARATE BASE
    `app.core.database.Base` is what the application's startup `create_all()`
    and Alembic's `target_metadata` see. Registering these tables there would
    create them in whatever database the app starts against — including the
    shared DEV QA database — the moment the code is deployed, flag or no flag.
    `EntityIntelligenceBase` is invisible to both. Its tables exist only where
    a migration from `migrations/` is applied deliberately (local / CI in this
    sprint).

PORTABLE TYPES
    String(36) identifiers and generic JSON, not PostgreSQL UUID/JSONB, so the
    schema can be created and tested on SQLite in CI as well as PostgreSQL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class EntityIntelligenceBase(DeclarativeBase):
    """Deliberately NOT app.core.database.Base."""


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EiSourceDelivery(EntityIntelligenceBase):
    """One preserved evidence delivery (a file we downloaded, or one handed
    over by a third party). Hash, path and schema fingerprint; never edited."""
    __tablename__ = "ei_source_deliveries"

    id = Column(String(36), primary_key=True, default=_uuid)
    source_id = Column(String(64), nullable=False, index=True)
    source_owner = Column(String(120), nullable=False)
    delivery_path = Column(String(32), nullable=False)      # DeliveryPath
    received_by = Column(String(120), nullable=False, default="DocuAction")
    received_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    file_name = Column(Text)
    file_sha256 = Column(String(64), index=True)
    schema_fingerprint = Column(String(64), index=True)
    schema_fields = Column(JSON, default=list)
    record_count = Column(Integer)
    source_version = Column(JSON)                           # SourceVersionRef.as_row()
    mapping_status = Column(String(32), nullable=False, default="UNMAPPED")   # UNMAPPED | PROPOSED | APPROVED
    note = Column(Text)


class EiEvidenceObservation(EntityIntelligenceBase):
    __tablename__ = "ei_evidence_observations"
    __table_args__ = (Index("ix_ei_obs_entity_type", "canonical_entity_id", "observation_type"),)

    observation_id = Column(String(36), primary_key=True, default=_uuid)
    canonical_entity_id = Column(String(36), index=True)
    source_id = Column(String(64), nullable=False, index=True)
    source_delivery_id = Column(String(36), index=True)
    source_record_id = Column(String(200))
    source_field = Column(String(200))
    observation_type = Column(String(24), nullable=False)
    role = Column(String(64))
    observed_value = Column(JSON, nullable=False)
    normalized_value = Column(JSON)
    observed_at = Column(String(40))
    effective_from = Column(String(40))
    effective_to = Column(String(40))
    ingested_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    source_authority = Column(String(32), nullable=False)
    applicability = Column(String(32), nullable=False)
    provenance = Column(JSON, nullable=False)
    content_hash = Column(String(64), index=True)
    model_version = Column(String(10), nullable=False)


class EiEvidenceComparison(EntityIntelligenceBase):
    __tablename__ = "ei_evidence_comparisons"

    id = Column(String(36), primary_key=True, default=_uuid)
    canonical_entity_id = Column(String(36), index=True)
    run_id = Column(String(36), index=True)
    dimension = Column(String(32), nullable=False)
    signal = Column(String(48), nullable=False)
    source_id = Column(String(64), nullable=False)
    delivered_observation_id = Column(String(36))
    matched_observation_id = Column(String(36))
    candidate_observation_ids = Column(JSON, default=list)
    explanation = Column(Text, nullable=False)
    detail = Column(JSON, default=dict)
    requires_human_review = Column(Boolean, nullable=False, default=True)
    rules_version = Column(String(10), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class EiHistoricalDelta(EntityIntelligenceBase):
    __tablename__ = "ei_historical_deltas"

    id = Column(String(36), primary_key=True, default=_uuid)
    canonical_entity_id = Column(String(36), index=True)
    run_id = Column(String(36), index=True)
    delta_type = Column(String(32), nullable=False)
    observation_type = Column(String(24), nullable=False)
    source_id = Column(String(64), nullable=False)
    role = Column(String(64))
    before = Column(JSON)
    after = Column(JSON)
    prior_observation_id = Column(String(36))
    current_observation_id = Column(String(36))
    variation = Column(String(48), nullable=False)
    variation_basis = Column(JSON, default=list)
    explanation = Column(Text, nullable=False)
    rules_version = Column(String(10), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class EiSystemEvidenceAssessment(EntityIntelligenceBase):
    """Append-only. A later run adds a row; nothing updates an earlier one."""
    __tablename__ = "ei_system_evidence_assessments"

    id = Column(String(36), primary_key=True, default=_uuid)
    canonical_entity_id = Column(String(36), index=True)
    run_id = Column(String(36), index=True)
    assessment = Column(String(48), nullable=False)
    basis = Column(JSON, default=list)
    open_questions = Column(JSON, default=list)
    requires_human_review = Column(Boolean, nullable=False, default=True)
    rules_version = Column(String(10), nullable=False)
    comparison_rules_version = Column(String(10))
    delta_rules_version = Column(String(10))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


EI_TABLES = tuple(sorted(EntityIntelligenceBase.metadata.tables))
