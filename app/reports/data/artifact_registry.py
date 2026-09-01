"""
The program's index of finalised report artifacts.

DIVISION OF RESPONSIBILITY
──────────────────────────
`app/core/storage/artifact_store.py` stores bytes: hash, write once, hand back,
never overwrite. It knows nothing about reports, cycles or evidence, and it must
stay that way or the next program cannot use it.

This module is the other half. It records the *program* facts that make a stored
object findable and meaningful — which report it is, which cycle it belongs to,
which evidence generation and rule version produced it, which analyst
determinations and QA events stand behind it, and what the underlying source
delivery was. The core store holds the artifact; this table says what the
artifact IS.

WHY BOTH A CONTENT HASH AND A DATA HASH
───────────────────────────────────────
`rendered_sha256` is the hash of the delivered bytes — it answers "is this the
document that was sent". `report_data_hash` is the hash of the dataset the
document was rendered from — it answers "do these numbers still come from the
same evidence". They move independently: a template change alters the rendered
bytes while the data is untouched, and that difference should be visible rather
than collapsed into one number.

FINALISATION
────────────
A row here is written when a report is finalised, and is not updated afterwards.
Regenerating identical content is idempotent — the store deduplicates and this
registry returns the existing row. Regenerating *different* content creates a
new artifact version, and the previous row stays exactly as it was.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (Boolean, Column, DateTime, Integer, String, Text,
                        UniqueConstraint, select, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base
from app.core.storage.artifact_store import (RetentionPolicy, StoredArtifact,
                                             get_artifact_store)

logger = logging.getLogger(__name__)


class ReportArtifact(Base):
    """One finalised, stored rendering of one report."""

    __tablename__ = "report_artifacts"
    __table_args__ = (
        # The same report, at the same version, cannot be registered twice.
        UniqueConstraint("report_id", "content_type", "artifact_version",
                         name="uq_report_artifact_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: The core store's identifier for the bytes.
    artifact_id = Column(String(64), nullable=False, index=True)
    artifact_version = Column(Integer, nullable=False, server_default="1")
    storage_backend = Column(String(32), nullable=False)
    storage_locator = Column(Text, nullable=False)

    # ── what this is ─────────────────────────────────────────────────────────
    report_id = Column(String(64), nullable=False, index=True)
    report_type = Column(String(64), nullable=False)
    #: Which program owns it. The store is shared; the reports are not.
    program = Column(String(32), nullable=False, server_default="TEFCA_ARC")
    review_cycle_id = Column(String(128), nullable=False)

    # ── provenance ───────────────────────────────────────────────────────────
    generated_at = Column(DateTime(timezone=True), nullable=False)
    generated_by = Column(String(320))
    template_version = Column(String(32))
    #: The canonical evidence rule version the numbers came from.
    evidence_rule_version = Column(String(64))
    methodology_version = Column(String(64))
    #: SHA-256 of the delivery in Area 1 behind the population.
    source_artifact_sha256 = Column(String(64))
    #: SHA-256 of the dataset rendered.
    report_data_hash = Column(String(64))
    #: SHA-256 of the delivered bytes.
    rendered_sha256 = Column(String(64), nullable=False, index=True)

    content_type = Column(String(128), nullable=False)
    size_bytes = Column(Integer, nullable=False)

    #: DEVELOPMENT_TEST or GOVERNMENT. Stored so a retrieved artifact cannot be
    #: read back without knowing what data it was computed over.
    data_classification = Column(String(32), nullable=False,
                                 server_default="DEVELOPMENT_TEST")

    # ── human decisions standing behind it ───────────────────────────────────
    determination_event_ids = Column(JSONB, server_default=text("'[]'::jsonb"))
    qa_event_ids = Column(JSONB, server_default=text("'[]'::jsonb"))
    #: False when the report contains no QA-approved determination — which is
    #: the normal state for a population report of observations.
    contains_reportable_findings = Column(Boolean, nullable=False,
                                          server_default="false")

    # ── retention ────────────────────────────────────────────────────────────
    retention_classification = Column(String(64), nullable=False,
                                      server_default="PROGRAM_GUIDANCE_REQUESTED")
    retention_period_days = Column(Integer)
    #: Never set true until an approved period exists. D8 is open.
    retention_worm_locked = Column(Boolean, nullable=False, server_default="false")
    retention_basis = Column(Text)

    finalized = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "storage_backend": self.storage_backend,
            "storage_locator": self.storage_locator,
            "report_id": self.report_id,
            "report_type": self.report_type,
            "program": self.program,
            "review_cycle_id": self.review_cycle_id,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "generated_by": self.generated_by,
            "template_version": self.template_version,
            "evidence_rule_version": self.evidence_rule_version,
            "methodology_version": self.methodology_version,
            "source_artifact_sha256": self.source_artifact_sha256,
            "report_data_hash": self.report_data_hash,
            "rendered_sha256": self.rendered_sha256,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "data_classification": self.data_classification,
            "determination_event_ids": list(self.determination_event_ids or []),
            "qa_event_ids": list(self.qa_event_ids or []),
            "contains_reportable_findings": bool(self.contains_reportable_findings),
            "retention": {
                "classification": self.retention_classification,
                "period_days": self.retention_period_days,
                "worm_locked": bool(self.retention_worm_locked),
                "basis": self.retention_basis,
            },
            "finalized": bool(self.finalized),
        }


#: File extension per stored content type. ONE map, because there were two —
#: the store key and the download filename each had their own copy, and when the
#: workbook format arrived only one of them would have been remembered.
ARTIFACT_SUFFIXES = {
    "text/html": "html",
    "application/pdf": "pdf",
    "text/csv": "csv",
    ("application/vnd.openxmlformats-officedocument"
     ".spreadsheetml.sheet"): "xlsx",
}


#: Registry fields that describe WHERE the bytes live. They belong to the store,
#: not to any caller: `storage_locator` is a filesystem path on the local backend
#: and a container path on Azure, and neither tells a reviewer anything they can
#: act on. `to_dict()` keeps them because retrieval needs them; the API boundary
#: drops them.
INTERNAL_ARTIFACT_FIELDS = ("storage_backend", "storage_locator")


def public_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """One registry row, safe to hand to a client."""
    return {k: v for k, v in artifact.items()
            if k not in INTERNAL_ARTIFACT_FIELDS}


def artifact_key(report_id: str, content_type: str) -> str:
    """The store key for one report in one format.

    Format is part of the key so the HTML and the PDF of the same report are
    separate objects rather than versions of each other — they are different
    documents, and a PDF that appeared to supersede its own HTML would be
    nonsense.
    """
    return f"{report_id}-{ARTIFACT_SUFFIXES.get(content_type, 'bin')}"


async def finalize_artifact(
    db, *, report_id: str, report_type: str, content: bytes,
    content_type: str = "text/html",
    review_cycle_id: str, generated_by: str = "SYSTEM",
    template_version: Optional[str] = None,
    evidence_rule_version: Optional[str] = None,
    methodology_version: Optional[str] = None,
    source_artifact_sha256: Optional[str] = None,
    report_data_hash: Optional[str] = None,
    data_classification: str = "DEVELOPMENT_TEST",
    determination_event_ids: Optional[List[str]] = None,
    qa_event_ids: Optional[List[str]] = None,
    contains_reportable_findings: bool = False,
    program: str = "TEFCA_ARC",
    retention: Optional[RetentionPolicy] = None,
    store=None,
) -> Dict[str, Any]:
    """Store the rendered bytes and register what they are.

    Returns the registry row as a dict. Idempotent: finalising byte-identical
    content for the same report returns the existing registration rather than
    creating a second one.
    """
    store = store or get_artifact_store()
    retention = retention or RetentionPolicy()

    stored: StoredArtifact = store.put(
        artifact_key(report_id, content_type), content,
        content_type=content_type, retention=retention,
        metadata={"report_id": report_id, "report_type": report_type,
                  "program": program, "review_cycle_id": review_cycle_id,
                  "data_classification": data_classification})

    existing = (await db.execute(
        select(ReportArtifact).where(
            ReportArtifact.report_id == report_id,
            ReportArtifact.content_type == content_type,
            ReportArtifact.rendered_sha256 == stored.content_sha256)
    )).scalars().first()
    if existing is not None:
        return existing.to_dict()

    row = ReportArtifact(
        id=uuid.uuid4(),
        artifact_id=stored.artifact_id,
        artifact_version=stored.version,
        storage_backend=store.backend,
        storage_locator=stored.locator,
        report_id=report_id, report_type=report_type, program=program,
        review_cycle_id=review_cycle_id,
        generated_at=datetime.now(timezone.utc), generated_by=generated_by,
        template_version=template_version,
        evidence_rule_version=evidence_rule_version,
        methodology_version=methodology_version,
        source_artifact_sha256=source_artifact_sha256,
        report_data_hash=report_data_hash,
        rendered_sha256=stored.content_sha256,
        content_type=content_type, size_bytes=stored.size_bytes,
        data_classification=data_classification,
        determination_event_ids=list(determination_event_ids or []),
        qa_event_ids=list(qa_event_ids or []),
        contains_reportable_findings=bool(contains_reportable_findings),
        retention_classification=retention.classification,
        retention_period_days=retention.period_days,
        retention_worm_locked=retention.worm_locked,
        retention_basis=retention.basis,
        finalized=True)
    db.add(row)
    await db.flush()
    return row.to_dict()


async def artifact_versions(db, report_id: str,
                            content_type: str = "text/html") -> List[Dict[str, Any]]:
    """Every registered version of one report, oldest first."""
    rows = (await db.execute(
        select(ReportArtifact)
        .where(ReportArtifact.report_id == report_id,
               ReportArtifact.content_type == content_type)
        .order_by(ReportArtifact.artifact_version)
    )).scalars().all()
    return [r.to_dict() for r in rows]


async def retrieve_artifact(db, report_id: str, *,
                            content_type: str = "text/html",
                            version: Optional[int] = None,
                            store=None) -> Dict[str, Any]:
    """Fetch a stored artifact and verify it still hashes to what was recorded.

    The verification is the point. A stored hash nobody recomputes is a claim.
    """
    store = store or get_artifact_store()
    stmt = (select(ReportArtifact)
            .where(ReportArtifact.report_id == report_id,
                   ReportArtifact.content_type == content_type))
    stmt = (stmt.where(ReportArtifact.artifact_version == version)
            if version is not None
            else stmt.order_by(ReportArtifact.artifact_version.desc()))
    row = (await db.execute(stmt)).scalars().first()
    if row is None:
        raise LookupError(
            f"no stored artifact for {report_id} ({content_type}"
            f"{f', version {version}' if version else ''})")

    content = store.get(row.storage_locator)
    from app.core.storage.artifact_store import content_sha256

    actual = content_sha256(content)
    if actual != row.rendered_sha256:
        # Loud, not a warning. The bytes are not the bytes that were issued.
        raise RuntimeError(
            f"INTEGRITY FAILURE: {row.storage_locator} hashes to {actual}, "
            f"registered as {row.rendered_sha256}.")
    return {"content": content, "artifact": row.to_dict(), "verified": True}
