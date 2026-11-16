"""ORM model for rce_supplemental_npi_research (migration 20261115_rce_npi_research). See supplemental_research.py.

Append-only by trigger: UPDATE and DELETE are refused by the database. Never created at runtime: the table is in
MIGRATION_OWNED_TABLES, so startup create_all skips it and only the governed migration can create it."""
from __future__ import annotations

import uuid

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

TABLE = "rce_supplemental_npi_research"


class RceSupplementalNpiResearch(Base):
    __tablename__ = TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_intake_id = Column(UUID(as_uuid=True), ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"), nullable=False)
    source_record_id = Column(UUID(as_uuid=True), ForeignKey("rce_source_records.id", ondelete="RESTRICT"), nullable=False)
    source_line = Column(Integer, nullable=False)
    hcid = Column(String(100))
    tefca_id = Column(String(100))
    original_submitted_npi = Column(Text, nullable=False, server_default=text("''"))
    entry_action = Column(String(12), nullable=False)
    researched_npi = Column(String(10))
    nppes_reference = Column(Text)
    nppes_check_date = Column(Date)
    match_rationale = Column(Text)
    change_reason = Column(Text)
    researcher = Column(String(320), nullable=False)
    researcher_user_id = Column(UUID(as_uuid=True))
    entered_at = Column(DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())
    supersedes_id = Column(UUID(as_uuid=True), ForeignKey(f"{TABLE}.id", ondelete="RESTRICT"))
    idempotency_key = Column(String(64), nullable=False)
    non_decision = Column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_rce_npi_research_idem"),
        CheckConstraint("non_decision = true", name="ck_rce_npi_research_not_a_decision"),
    )
