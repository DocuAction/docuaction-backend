"""Append-only, NON-DECISION comments anchored to a delivered source record.

WHAT THIS IS
    A dedicated store for reviewer commentary about a delivered record: the
    original QA comments, a second reviewer's comments (QA_IZAAN), and generated
    report clarifications - three KINDS, never merged. Each row keeps the text
    VERBATIM with its hash, the file/sheet/column/row it came from, any author
    attribution that is actually available, the REAL import actor and import
    timestamp. An unknown authored timestamp stays NULL.

WHAT IT IS NOT
    Not a determination, an issue resolution, an accepted correction or a QA
    approval. `non_decision` is pinned TRUE by a CHECK; nothing in this table is
    read by the classifier, the QA gate, the issue ledger or the hold logic. A
    comment cannot close a finding, accept a supplemental NPI, change a bucket,
    release a hold, or stand in for independent QA.

ANCHOR
    `source_record_id` is the immutable `rce_source_records.id` (AGT_Record_ID)
    and `source_intake_id` the delivery it belongs to; the database refuses a row
    whose record is not in that intake. HELD, REJECTED and unpromoted records are
    ordinary targets - no review/classification row is created or needed.

APPEND-ONLY
    UPDATE and DELETE are rejected by trigger (independent of roles). A change to
    a comment is a NEW row whose `supersedes_comment_id` names the version it
    amends; the chain is linear (at most one successor) and stays on the same
    record and kind. "Current" is read by excluding superseded rows - nothing is
    ever overwritten.

Migration: 20261112_rce_record_comments. Until it is applied through the governed
DEV release path the service refuses cleanly (see record_comments.py) and no
existing behaviour changes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

TABLE = "rce_record_comments"

KIND_QA_ORIGINAL = "QA_ORIGINAL"
KIND_QA_IZAAN = "QA_IZAAN"
KIND_REPORT_CLARIFICATION = "REPORT_CLARIFICATION"
COMMENT_KINDS = (KIND_QA_ORIGINAL, KIND_QA_IZAAN, KIND_REPORT_CLARIFICATION)


class RceRecordComment(Base):
    __tablename__ = TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_intake_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_intakes.id", ondelete="RESTRICT"),
        nullable=False)
    source_record_id = Column(
        UUID(as_uuid=True), ForeignKey("rce_source_records.id", ondelete="RESTRICT"),
        nullable=False)
    comment_kind = Column(String(24), nullable=False)
    #: VERBATIM: never stripped, normalised or truncated.
    comment_text = Column(Text, nullable=False)
    text_sha256 = Column(String(64), nullable=False)

    source_file_name = Column(Text, nullable=False)
    source_file_sha256 = Column(String(64), nullable=False)
    source_sheet = Column(Text)
    source_column = Column(Text)
    source_row = Column(Integer)

    #: Only what is actually available; NULL otherwise. `attribution_basis` says
    #: how the attribution is known (e.g. an owner designation of the source file).
    author_attribution = Column(Text)
    attribution_basis = Column(Text)
    #: When the comment was WRITTEN, if the source says so; NULL = unknown. Never
    #: defaulted to the import time.
    authored_at = Column(DateTime(timezone=True))

    #: The identity facts the import validated against the immutable source
    #: record (source_line, hcid, tefca_id) - kept as audit evidence.
    identity_check = Column(JSONB, nullable=False)

    imported_by = Column(String(320), nullable=False)
    #: clock_timestamp(): the real per-row insertion time (now() would be the
    #: transaction start and tie every row of a batch).
    imported_at = Column(DateTime(timezone=True), nullable=False,
                         server_default=func.clock_timestamp())
    import_batch_id = Column(UUID(as_uuid=True), nullable=False)
    #: sha256(source_record_id | kind | text_sha256 | supersedes_comment_id or '')
    idempotency_key = Column(String(64), nullable=False)
    supersedes_comment_id = Column(
        UUID(as_uuid=True), ForeignKey(f"{TABLE}.id", ondelete="RESTRICT"))
    non_decision = Column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_rce_record_comment_idem"),
        Index("uq_rce_record_comment_successor", "supersedes_comment_id", unique=True,
              postgresql_where=text("supersedes_comment_id IS NOT NULL")),
        Index("ix_rce_record_comment_record", "source_intake_id", "source_record_id",
              "comment_kind", "imported_at"),
        Index("ix_rce_record_comment_batch", "import_batch_id"),
        CheckConstraint(
            "comment_kind IN ('QA_ORIGINAL','QA_IZAAN','REPORT_CLARIFICATION')",
            name="ck_rce_record_comment_kind"),
        CheckConstraint("non_decision = true", name="ck_rce_record_comment_not_a_decision"),
        CheckConstraint("comment_text <> ''", name="ck_rce_record_comment_text"),
        CheckConstraint(
            "char_length(text_sha256) = 64 AND char_length(source_file_sha256) = 64",
            name="ck_rce_record_comment_hashes"),
    )
