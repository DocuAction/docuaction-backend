"""ORM for the preflight and shadow-reassessment evidence tables
(migration 20261003_preflight_shadow_workspace).

Every table here is APPEND-ONLY for the runtime role (SELECT + INSERT by
grant, same discipline as 20260921_september_snapshot). A later fact is a
new row that names what it supersedes; nothing is edited or deleted.

    RcePreflightRun             one preflight pass over one delivery (Area 1)
    RcePreflightFinding         one finding, four SEPARATE dimensions
                                (applicability / execution / evidence /
                                disposition) -- never collapsed into a status
    RcePreflightNormalization   a DERIVED value recorded beside its ORIGINAL,
                                with the method; the original is never edited
    RceShadowComparison         one pinned baseline-vs-candidate reassessment
                                package (ruleset versions, evaluation date,
                                delivery, evidence references, package hash)
    RceShadowFindingDelta       per-entity NEW / REMOVED / CHANGED / UNCHANGED
                                with a direction (STRICTER / MORE_PERMISSIVE /
                                NEUTRAL) and a manual-review flag
    RceShadowApproval           analyst and independent-QA approvals, bound to
                                the exact package hash; different persons
    RceSuccessorPublicationEvent every publication attempt, refused or not,
                                keyed on the package hash so a retry is
                                idempotent

The OFFICIAL findings (`review_records`, `rce_issues`, `tefca_dimension_
evidence`) are never written by any of this. A successor record, when
publication is permitted (local test mode only -- see
shadow_reassessment.py), is a NEW `review_records` row that names its
predecessor; the predecessor is untouched.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
                        Integer, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# ── preflight vocabularies ───────────────────────────────────────────────────

PREFLIGHT_CATEGORIES = ("SCHEMA", "IDENTIFIER", "CONDITIONAL_BLANK", "MISSING_CONTEXT")

#: Applicability -- does the check even apply to this record/field?
APPLIES = "applies"
DOES_NOT_APPLY = "does_not_apply"
UNRESOLVED = "unresolved"
APPLICABILITY = (APPLIES, DOES_NOT_APPLY, UNRESOLVED)

#: Execution -- did a check actually run?
EXEC_DONE = "done"
EXEC_UNAVAILABLE = "unavailable"      # no check exists / no source wired
EXEC_INSUFFICIENT = "insufficient"    # inputs this delivery does not carry
#: Added 2026-10-04 (Round 22, docs/review/DELTA-2026-10-04.md §5): the
#: check could not run because the record/delivery is HELD pending
#: resolution of something else -- distinct from EXEC_UNAVAILABLE (no
#: check exists at all) and EXEC_INSUFFICIENT (this delivery's inputs
#: cannot support the check). Additive: existing rows/values unchanged;
#: no existing finding is retroactively re-labeled by adding this.
EXEC_HELD = "held"
EXECUTION = (EXEC_DONE, EXEC_UNAVAILABLE, EXEC_INSUFFICIENT, EXEC_HELD)

#: Disposition -- what happens next. NOT a pass/fail.
DISP_OPEN = "open"                    # a human must resolve it
DISP_INFORMATIONAL = "informational"  # recorded; nothing to resolve
DISP_BLOCKED = "blocked"              # final classification must not proceed
DISPOSITION = (DISP_OPEN, DISP_INFORMATIONAL, DISP_BLOCKED)

PREFLIGHT_STATUS = ("RUNNING", "COMPLETE", "FAILED")
GATE_CLEAR = "CLEAR"
GATE_CLEAR_WITH_FINDINGS = "CLEAR_WITH_FINDINGS"
GATE_BLOCKED = "BLOCKED"

# ── shadow vocabularies ──────────────────────────────────────────────────────

DELTA_NEW = "NEW"
DELTA_REMOVED = "REMOVED"
DELTA_CHANGED = "CHANGED"
DELTA_UNCHANGED = "UNCHANGED"
DELTA_NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"   # baseline rules did not reproduce the official row
DELTA_KINDS = (DELTA_NEW, DELTA_REMOVED, DELTA_CHANGED, DELTA_UNCHANGED, DELTA_NOT_REPRODUCIBLE)

DIR_STRICTER = "STRICTER"
DIR_MORE_PERMISSIVE = "MORE_PERMISSIVE"
DIR_NEUTRAL = "NEUTRAL"
DIRECTIONS = (DIR_STRICTER, DIR_MORE_PERMISSIVE, DIR_NEUTRAL)

APPROVAL_ANALYST = "ANALYST"
APPROVAL_INDEPENDENT_QA = "INDEPENDENT_QA"
APPROVAL_ROLES = (APPROVAL_ANALYST, APPROVAL_INDEPENDENT_QA)

PUB_REFUSED = "REFUSED"
PUB_PUBLISHED = "PUBLISHED"
PUB_ALREADY_PUBLISHED = "ALREADY_PUBLISHED"
PUB_EVENTS = (PUB_REFUSED, PUB_PUBLISHED, PUB_ALREADY_PUBLISHED)


class RcePreflightRun(Base):
    __tablename__ = "rce_preflight_run"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_intake_id = Column(UUID(as_uuid=True),
                              ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                              nullable=False, index=True)
    field_map_version = Column(String(16), nullable=False)
    rule_set_version = Column(String(16), nullable=False)
    preflight_version = Column(String(16), nullable=False)
    status = Column(String(12), nullable=False, server_default=text("'RUNNING'"))
    classification_gate = Column(String(24))
    records_evaluated = Column(Integer, nullable=False, server_default=text("0"))
    findings_count = Column(Integer, nullable=False, server_default=text("0"))
    normalizations_count = Column(Integer, nullable=False, server_default=text("0"))
    summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    error = Column(Text)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    actor = Column(String(320), nullable=False)
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))

    __table_args__ = (
        CheckConstraint("status IN ('RUNNING','COMPLETE','FAILED')",
                        name="ck_rce_preflight_run_status"),
    )


class RcePreflightFinding(Base):
    __tablename__ = "rce_preflight_finding"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True),
                    ForeignKey("rce_preflight_run.id", ondelete="RESTRICT"),
                    nullable=False, index=True)
    #: NULL for a delivery-level (schema) finding.
    source_record_id = Column(UUID(as_uuid=True),
                              ForeignKey("rce_source_records.id", ondelete="RESTRICT"),
                              index=True)
    line_number = Column(Integer)
    sequence = Column(Integer, nullable=False)
    category = Column(String(24), nullable=False)
    code = Column(String(32), nullable=False)
    #: The quality rule REUSED to produce this finding, when one exists.
    rule_ref = Column(String(20))
    field_name = Column(String(100))
    applicability = Column(String(16), nullable=False)
    execution = Column(String(16), nullable=False)
    evidence = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    disposition = Column(String(16), nullable=False)
    description = Column(Text, nullable=False)
    #: The ORIGINAL value, verbatim. Never a derived one.
    original_value = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_rce_preflight_finding_seq"),
        CheckConstraint("category IN ('SCHEMA','IDENTIFIER','CONDITIONAL_BLANK','MISSING_CONTEXT')",
                        name="ck_rce_preflight_finding_category"),
        CheckConstraint("applicability IN ('applies','does_not_apply','unresolved')",
                        name="ck_rce_preflight_finding_applicability"),
        CheckConstraint("execution IN ('done','unavailable','insufficient')",
                        name="ck_rce_preflight_finding_execution"),
        CheckConstraint("disposition IN ('open','informational','blocked')",
                        name="ck_rce_preflight_finding_disposition"),
    )


class RcePreflightNormalization(Base):
    __tablename__ = "rce_preflight_normalization"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True),
                    ForeignKey("rce_preflight_run.id", ondelete="RESTRICT"),
                    nullable=False, index=True)
    source_record_id = Column(UUID(as_uuid=True),
                              ForeignKey("rce_source_records.id", ondelete="RESTRICT"),
                              nullable=False, index=True)
    line_number = Column(Integer, nullable=False)
    field_name = Column(String(100), nullable=False)
    original_value = Column(Text)
    derived_value = Column(Text)
    method = Column(String(40), nullable=False)
    rule_ref = Column(String(20))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RceShadowComparison(Base):
    __tablename__ = "rce_shadow_comparison"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    intake_id = Column(UUID(as_uuid=True),
                       ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
                       nullable=False, index=True)
    baseline_rule_version = Column(Integer, nullable=False)
    #: NULL when `candidate_rules` carries a local what-if rule set instead.
    candidate_rule_version = Column(Integer)
    candidate_rules = Column(JSONB)
    candidate_rules_hash = Column(String(64), nullable=False)
    evaluation_date = Column(Date, nullable=False)
    #: Pinned references: review records, their evidence generation stamps,
    #: and the source snapshots in force. References only -- never values.
    evidence_pinned = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    #: SHA-256 over the official classification state at build time. A later
    #: change to any pinned official row makes the package STALE.
    official_baseline_hash = Column(String(64), nullable=False)
    #: SHA-256 over the canonical JSON of the whole delta. Approvals and
    #: publication bind to THIS value.
    package_hash = Column(String(64), nullable=False, index=True)
    summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    built_by = Column(String(320), nullable=False)
    built_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)
    build_sha = Column(String(40), nullable=False, server_default=text("'unknown'"))


class RceShadowFindingDelta(Base):
    __tablename__ = "rce_shadow_finding_delta"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comparison_id = Column(UUID(as_uuid=True),
                           ForeignKey("rce_shadow_comparison.id", ondelete="RESTRICT"),
                           nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True),
                       ForeignKey("tefca_reg_entities.id", ondelete="RESTRICT"))
    #: The OFFICIAL review record this delta is measured against. NULL is
    #: legitimate for a NEW finding that has no predecessor.
    predecessor_review_record_id = Column(UUID(as_uuid=True),
                                          ForeignKey("review_records.id", ondelete="RESTRICT"))
    predecessor_review_id = Column(String(20))
    delta_kind = Column(String(20), nullable=False)
    direction = Column(String(16), nullable=False)
    baseline_bucket = Column(String(2))
    baseline_rule = Column(String(20))
    baseline_rule_version = Column(Integer)
    candidate_bucket = Column(String(2))
    candidate_rule = Column(String(20))
    candidate_rule_version = Column(Integer)
    reason = Column(Text, nullable=False)
    #: TRUE when the change touches an EIN/FEIN-related signal, or when the
    #: baseline could not be reproduced. Never auto-superseded.
    manual_review_required = Column(Boolean, nullable=False, server_default=text("false"))
    detail = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("delta_kind IN ('NEW','REMOVED','CHANGED','UNCHANGED','NOT_REPRODUCIBLE')",
                        name="ck_rce_shadow_delta_kind"),
        CheckConstraint("direction IN ('STRICTER','MORE_PERMISSIVE','NEUTRAL')",
                        name="ck_rce_shadow_delta_direction"),
    )


class RceShadowApproval(Base):
    __tablename__ = "rce_shadow_approval"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comparison_id = Column(UUID(as_uuid=True),
                           ForeignKey("rce_shadow_comparison.id", ondelete="RESTRICT"),
                           nullable=False, index=True)
    package_hash = Column(String(64), nullable=False)
    approval_role = Column(String(16), nullable=False)
    actor_id = Column(UUID(as_uuid=True))
    actor_email = Column(String(320), nullable=False)
    actor_role = Column(String(64), nullable=False)
    rationale = Column(Text, nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)

    __table_args__ = (
        # One standing approval per role per package. A re-built package has a
        # new hash and therefore needs fresh approvals.
        UniqueConstraint("comparison_id", "package_hash", "approval_role",
                         name="uq_rce_shadow_approval_role"),
        CheckConstraint("approval_role IN ('ANALYST','INDEPENDENT_QA')",
                        name="ck_rce_shadow_approval_role"),
    )


class RceSuccessorPublicationEvent(Base):
    __tablename__ = "rce_successor_publication_event"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comparison_id = Column(UUID(as_uuid=True),
                           ForeignKey("rce_shadow_comparison.id", ondelete="RESTRICT"),
                           nullable=False, index=True)
    package_hash = Column(String(64), nullable=False, index=True)
    event_type = Column(String(20), nullable=False)
    mode = Column(String(24), nullable=False)
    reason = Column(Text, nullable=False)
    #: review_ids of the predecessors superseded, and of the successors written.
    predecessor_review_ids = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    successor_review_ids = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    actor = Column(String(320), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    correlation_id = Column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint("event_type IN ('REFUSED','PUBLISHED','ALREADY_PUBLISHED')",
                        name="ck_rce_successor_pub_event_type"),
    )


TABLES = (
    "rce_preflight_run", "rce_preflight_finding", "rce_preflight_normalization",
    "rce_shadow_comparison", "rce_shadow_finding_delta", "rce_shadow_approval",
    "rce_successor_publication_event",
)
