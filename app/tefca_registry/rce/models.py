"""
RCE pipeline tables — Area 1 (immutable), processing, issues, Area 2 (curated).

THE SHAPE OF THE PIPELINE
─────────────────────────
    rce_source_intakes      one immutable DELIVERY EVENT
      └── rce_source_records   one immutable row per DELIVERED LINE
            ├── rce_issues        what the quality rules found
            └── rce_curated_records  the working copy, one per source record
                  └── rce_correction_details  every field-level change, with why

    rce_ingestion_runs         one PROCESSING pass over a delivery
      └── rce_rule_execution_history  which rule ran, at which version

DELIVERY IS NOT PROCESSING. A delivery arrives once and never changes; it may be
processed many times as rules improve. Collapsing the two would make "we re-ran
the rules" indistinguishable from "they sent it again", and would destroy the
ability to say which rule version produced a given issue.

SHA-256 IS INDEXED, NOT UNIQUE
ONC may legitimately resend byte-identical content — a re-delivery is still a
distinct historical event with its own timestamp and operator. A UNIQUE
constraint would reject the second delivery, and the system would have no record
that it ever arrived. Instead the duplicate is ACCEPTED as its own intake and
linked back through `duplicate_of_intake_id`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# ── vocabularies ─────────────────────────────────────────────────────────────

INTAKE_STATUS = ("RECEIVED", "PARSED", "PROFILED", "FAILED")

PARSE_STATUS = ("ok", "field_count_mismatch", "unparseable")

PROMOTION_STATUS = ("pending", "promoted", "held", "excluded")

RUN_STATUS = ("RUNNING", "COMPLETE", "FAILED")

SEVERITY = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")

#: Who may apply a correction. This is an AUTHORITY, not a confidence score —
#: see the note on `rce_issues.correction_authority`.
CORRECTION_AUTHORITY = ("AUTO_SAFE", "HUMAN_REQUIRED", "QA_REQUIRED", "NO_CORRECTION")

#: OPEN → PROPOSED → UNDER_REVIEW → APPROVED/REJECTED/WAIVED → RESOLVED
RESOLUTION_STATUS = ("OPEN", "PROPOSED", "UNDER_REVIEW", "APPROVED", "REJECTED",
                     "WAIVED", "RESOLVED")

CURATED_STATUS = ("CLEAN", "CORRECTED", "HELD", "REJECTED")


# ── P2 — Area 1: the immutable delivery ──────────────────────────────────────

class RceSourceIntake(Base):
    """One delivery event. Immutable once written.

    A delivery is a historical fact: these bytes arrived, at this time, from this
    operator. Nothing in the application updates a row here after creation — see
    `repository.py`, which exposes create and read and has no update path.
    """

    __tablename__ = "rce_source_intakes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    delivery_label = Column(String(200))
    original_filename = Column(String(500), nullable=False)
    #: Where the ORIGINAL bytes are preserved, unmodified. The file at this path
    #: is never rewritten; re-reading it must reproduce `sha256`.
    storage_path = Column(Text, nullable=False)

    #: INDEXED, NOT UNIQUE. See the module docstring.
    sha256 = Column(String(64), nullable=False, index=True)
    file_size_bytes = Column(BigInteger, nullable=False)

    delimiter = Column(String(4))
    encoding = Column(String(32))
    #: True when decoding required replacement characters, or when mojibake
    #: markers were seen. A rendering artefact is a data-quality fact.
    encoding_anomaly = Column(Boolean, nullable=False, server_default=text("false"))
    line_terminator = Column(String(8))

    #: Headers EXACTLY as delivered, in order. Order matters: a positional
    #: parser depends on it, so two files with the same columns in a different
    #: order are different schemas.
    headers = Column(JSONB, nullable=False)
    schema_fingerprint = Column(String(64), nullable=False, index=True)

    record_count = Column(Integer, nullable=False, server_default=text("0"))
    received_at = Column(DateTime, nullable=False, server_default=func.now())
    received_by = Column(String(320))
    source_metadata = Column(JSONB, server_default=text("'{}'::jsonb"))

    status = Column(String(20), nullable=False, server_default=text("'RECEIVED'"))
    error = Column(Text)

    #: Set when this delivery's bytes match an earlier intake. The delivery is
    #: still recorded in full — it happened, and that is the point.
    duplicate_of_intake_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_intakes.id"), nullable=True)
    duplicate_content = Column(Boolean, nullable=False, server_default=text("false"))

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_rce_intake_sha", "sha256"),
        Index("idx_rce_intake_received", "received_at"),
        Index("idx_rce_intake_status", "status"),
        Index("idx_rce_intake_duplicate", "duplicate_of_intake_id"),
        CheckConstraint("record_count >= 0", name="ck_rce_intake_count_nonneg"),
    )


class RceSourceRecord(Base):
    """One delivered line, preserved exactly as it arrived.

    EVERY physical line lands here — including a line that could not be split
    into the expected number of fields. A parser that discarded a malformed line
    would destroy the only evidence that the line was ever delivered, and that
    line is precisely the one an auditor asks about.

    `raw_line` is the authoritative artefact. `parsed` is a convenience view of
    it and is deliberately allowed to be partial when `parse_status` says so.
    """

    __tablename__ = "rce_source_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_intake_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_intakes.id"),
        nullable=False, index=True)

    #: 1-based within the file, counting the header as line 1.
    line_number = Column(Integer, nullable=False)
    raw_line = Column(Text, nullable=False)
    parsed = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    record_sha256 = Column(String(64), nullable=False, index=True)

    #: Lifted out of `parsed` so identifiers are indexable. `source_rce_id` is
    #: the delivery's `id` column — the only field observed to be unique.
    source_rce_id = Column(String(200), index=True)
    tefcaid = Column(String(100), index=True)
    hcid = Column(String(100), index=True)
    npi = Column(String(40), index=True)

    field_count = Column(Integer, nullable=False)
    parse_status = Column(String(30), nullable=False,
                          server_default=text("'ok'"))
    parse_note = Column(Text)

    promotion_status = Column(String(20), nullable=False,
                              server_default=text("'pending'"))
    canonical_entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        # A line number occurs once per delivery. This is the guard that makes
        # "every line landed exactly once" checkable rather than hoped for.
        UniqueConstraint("source_intake_id", "line_number",
                         name="uq_rce_source_record_line"),
        Index("idx_rce_record_intake_status", "source_intake_id", "promotion_status"),
        Index("idx_rce_record_parse_status", "parse_status"),
    )


# ── P3 — processing ──────────────────────────────────────────────────────────

class RceIngestionRun(Base):
    """One processing pass over one delivery.

    Separate from the delivery because a delivery may be processed repeatedly as
    rules change. `rule_config_hash` pins the exact rule configuration, so two
    runs that produced different issue counts can be explained by pointing at
    the configuration rather than at chance.
    """

    __tablename__ = "rce_ingestion_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_intake_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_intakes.id"),
        nullable=False, index=True)

    rule_set_version = Column(Text, nullable=False)
    rule_config_hash = Column(String(64), nullable=False)
    field_map_version = Column(Text)

    started_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime)

    records_evaluated = Column(Integer, server_default=text("0"))
    issues_generated = Column(Integer, server_default=text("0"))

    run_status = Column(String(20), nullable=False, server_default=text("'RUNNING'"))
    error = Column(Text)
    executed_by = Column(Text, nullable=False, server_default=text("'SYSTEM'"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_rce_run_intake_started", "source_intake_id", "started_at"),
        Index("idx_rce_run_status", "run_status"),
    )


class RceRuleExecutionHistory(Base):
    """One rule's execution within one run.

    Answers "which rules ran, at which version, and did every record get
    evaluated?" — a question the issue table alone cannot answer, because a rule
    that found nothing writes no issues and would otherwise be indistinguishable
    from a rule that never ran.
    """

    __tablename__ = "rce_rule_execution_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("rce_ingestion_runs.id"),
                    nullable=False, index=True)

    rule_id = Column(Text, nullable=False, index=True)
    rule_version = Column(Text, nullable=False)
    rule_category = Column(String(8))

    records_evaluated = Column(Integer, server_default=text("0"))
    issues_generated = Column(Integer, server_default=text("0"))
    execution_status = Column(Text, nullable=False)
    execution_duration_ms = Column(Integer)
    error = Column(Text)
    executed_by = Column(Text, nullable=False, server_default=text("'SYSTEM'"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "rule_id", name="uq_rce_rule_exec_run_rule"),
        Index("idx_rce_rule_exec_rule", "rule_id"),
    )


# ── P5 — the Issue Ledger ────────────────────────────────────────────────────

class RceIssue(Base):
    """One finding against one source record.

    CORRECTION AUTHORITY IS NOT CONFIDENCE.
    `suggested_confidence` says how sure the rule is about what the value should
    be. `correction_authority` says who is allowed to act on that. They are
    independent, and the system treats them that way: a HIGH-confidence NPI
    correction is still HUMAN_REQUIRED, because confidence about a value says
    nothing about the authority to change an identity.

    AUTO_SAFE is confined to deterministic, non-substantive normalisation —
    whitespace, case of a state code, ZIP zero-padding, date canonical form.
    Anything touching identity, organisation name or relationship requires a
    human, whatever the rule thinks it knows.
    """

    __tablename__ = "rce_issues"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: DQ-YYYYMMDD-NNNNNN
    issue_code = Column(String(30), nullable=False, unique=True, index=True)

    source_intake_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_intakes.id"),
        nullable=False, index=True)
    source_record_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_records.id"),
        nullable=True, index=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("rce_ingestion_runs.id"),
                    nullable=True, index=True)

    rule_id = Column(Text, nullable=False, index=True)
    rule_version = Column(Text)
    issue_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)

    field_name = Column(String(100))
    original_value = Column(Text)
    suggested_value = Column(Text)
    suggested_source = Column(Text)
    suggested_confidence = Column(String(10))

    correction_authority = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)

    resolution = Column(String(20), nullable=False, server_default=text("'OPEN'"))
    resolved_by = Column(Text)
    resolved_at = Column(DateTime)
    resolution_notes = Column(Text)
    #: Set when correction_authority is QA_REQUIRED and QA has signed off.
    qa_approved_by = Column(Text)
    qa_approved_at = Column(DateTime)

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_rce_issue_intake_severity", "source_intake_id", "severity"),
        Index("idx_rce_issue_resolution", "resolution"),
        Index("idx_rce_issue_open", "source_intake_id",
              postgresql_where=text("resolution = 'OPEN'")),
        Index("idx_rce_issue_authority", "correction_authority"),
        CheckConstraint(
            "correction_authority IN "
            "('AUTO_SAFE','HUMAN_REQUIRED','QA_REQUIRED','NO_CORRECTION')",
            name="ck_rce_issue_authority"),
        CheckConstraint(
            "severity IN ('CRITICAL','HIGH','MEDIUM','LOW','INFORMATIONAL')",
            name="ck_rce_issue_severity"),
    )


# ── P6 — Area 2: the Curated Working Dataset ─────────────────────────────────

class RceCuratedRecord(Base):
    """The working copy of one source record.

    NOT "the fixed file". Area 2 may normalise, correct, enrich, reconcile and
    adjudicate; calling it fixed implies the source was broken and that this
    replaces it. It does not — Area 1 remains the record of what was delivered,
    and every curated row points back to exactly one source row.

    The 1:1 link is what makes reconciliation arithmetic possible: every Area 2
    record traces to Area 1, and any Area 1 record with no Area 2 counterpart is
    either held or rejected, with a reason.
    """

    __tablename__ = "rce_curated_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_intake_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_intakes.id"),
        nullable=False, index=True)
    source_record_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_records.id"),
        nullable=False, index=True)

    record_status = Column(String(20), nullable=False)
    issue_count = Column(Integer, nullable=False, server_default=text("0"))
    correction_count = Column(Integer, nullable=False, server_default=text("0"))
    #: Why a record is HELD or REJECTED, in words a reviewer can act on.
    status_reason = Column(Text)

    #: Curated entity fields. Names mirror the canonical registry rather than the
    #: delivery, because this is the shape that gets promoted.
    rce_org_oid = Column(String(200), index=True)
    tefcaid = Column(String(100), index=True)
    hcid = Column(String(100), index=True)
    aaid = Column(String(100))
    npi = Column(String(40), index=True)
    name = Column(String(500))
    entity_level = Column(String(50))
    sequoia_org_type = Column(String(50))
    org_node_type = Column(String(100))
    hl7_org_role = Column(String(100))
    operational_status = Column(String(50))
    is_active = Column(Boolean)
    address_line = Column(Text)
    address_city = Column(String(200))
    address_state = Column(String(10))
    address_postal_code = Column(String(20))
    address_country = Column(String(10))
    exchange_purposes = Column(JSONB, server_default=text("'[]'::jsonb"))
    part_of = Column(String(200), index=True)
    org_managing_org = Column(String(200), index=True)
    contact = Column(JSONB, server_default=text("'{}'::jsonb"))
    rce_attributes = Column(JSONB, server_default=text("'{}'::jsonb"))
    is_test_record = Column(Boolean, nullable=False, server_default=text("false"))

    transformation_version = Column(Text, nullable=False)
    canonical_entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    promoted_at = Column(DateTime)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    reviewed_by = Column(Text)
    reviewed_at = Column(DateTime)

    __table_args__ = (
        # Exactly one curated row per source row, per the P6 contract.
        UniqueConstraint("source_record_id", name="uq_rce_curated_source_record"),
        Index("idx_rce_curated_intake_status", "source_intake_id", "record_status"),
        CheckConstraint(
            "record_status IN ('CLEAN','CORRECTED','HELD','REJECTED')",
            name="ck_rce_curated_status"),
    )


class RceCorrectionDetail(Base):
    """One field-level change, with the authority that permitted it.

    `original_value_hash` is a STALENESS GUARD, not decoration. A reviewer
    approves a correction against a value they read. If the underlying value has
    changed between approval and application, the approval was given for
    something else, and applying it would attribute a decision to a human who
    never made it. `curation.apply_correction` re-checks the hash and invalidates
    the approval on mismatch.
    """

    __tablename__ = "rce_correction_details"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    curated_record_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_curated_records.id"),
        nullable=False, index=True)
    source_record_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_records.id"),
        nullable=False, index=True)
    issue_id = Column(UUID(as_uuid=True), ForeignKey("rce_issues.id"),
                      nullable=True, index=True)

    column_name = Column(Text, nullable=False)
    original_value = Column(Text)
    original_value_hash = Column(String(64), nullable=False)
    corrected_value = Column(Text)

    correction_reason = Column(Text, nullable=False)
    correction_rule_id = Column(Text)
    correction_authority = Column(String(20), nullable=False)
    corrected_by = Column(Text, nullable=False)
    approval_actor = Column(Text)
    confidence = Column(String(10))
    qa_status = Column(String(20))

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_rce_correction_curated", "curated_record_id"),
        Index("idx_rce_correction_authority", "correction_authority"),
        CheckConstraint(
            "correction_authority IN "
            "('AUTO_SAFE','HUMAN_REQUIRED','QA_REQUIRED','NO_CORRECTION')",
            name="ck_rce_correction_authority"),
    )


# ── P8 support — contacts (PII) ──────────────────────────────────────────────

class TefcaEntityContact(Base):
    """Contact details promoted from the delivery's contact_* fields.

    PII: a named individual, their phone and their email. Held in its own table
    rather than on the entity row so it can be excluded from a projection by
    omitting a join, instead of by remembering to drop columns.
    """

    __tablename__ = "tefca_entity_contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    source_record_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    contact_purpose = Column(String(100))
    company = Column(String(500))
    name = Column(String(500))
    phone = Column(String(50))
    email = Column(String(320))
    address_text = Column(Text)
    address_line = Column(Text)
    address_city = Column(String(200))
    address_state = Column(String(20))
    address_postal_code = Column(String(20))
    address_country = Column(String(20))

    source = Column(String(50), nullable=False, server_default=text("'rce_import'"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_tefca_contact_entity", "entity_id"),
    )


#: Parents first — for scoped create_all and the migration.
RCE_TABLE_ORDER = [
    "rce_source_intakes",
    "rce_source_records",
    "rce_ingestion_runs",
    "rce_rule_execution_history",
    "rce_issues",
    "rce_curated_records",
    "rce_correction_details",
    "tefca_entity_contacts",
]
