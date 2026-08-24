"""
Where a report's numbers came from, stated so it survives being questioned.

THE DEFECT THIS REPLACES
────────────────────────
Every report snapshot generated before this module stamped
`rce_source_file_sha256 = "cafe"`. Not as a deliberate placeholder — the old
`latest_rce_source_sha256()` read `tefca_import_batches` ordered by
`created_at desc`, and the newest row in that table is a July 2026 unit-test
fixture whose checksum literally is the string "cafe". The authoritative
delivery hash sat in Area 1, unread.

A four-character checksum is obviously wrong to a human and completely
invisible to a machine that only checks the field is non-empty. That is the
worse failure mode: the report *looked* provenanced. So this module refuses
anything that is not a real SHA-256 rather than passing it through, and says
why it refused.

WHERE PROVENANCE ACTUALLY LIVES
───────────────────────────────
`rce_source_intakes` — Area 1 — is the only authoritative record of a delivery.
It is immutable by construction (no update path in its repository, and the
database revokes UPDATE and DELETE on the table), it stores the SHA-256 of the
bytes as received, and the file at `storage_path` still reproduces that hash.
`tefca_import_batches` is a legacy development table that records local import
attempts; it is not a delivery record and must not be cited as one.

DATA CLASSIFICATION IS PART OF PROVENANCE
─────────────────────────────────────────
A hash says *which bytes*. It does not say *whose bytes*. The Government entity
CSV has not been delivered, so every artefact currently in Area 1 is
development data, and a report that cites a real SHA-256 without saying that is
more misleading than one citing "cafe" — it looks authoritative. The
classification travels with the hash, in the same object, so the two cannot be
separated by a caller that only reads one field.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

#: A SHA-256 is 64 lowercase hex characters. Nothing else is one.
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Development artefacts. Government delivery, when it happens, changes this.
CLASSIFICATION_DEVELOPMENT = "DEVELOPMENT_TEST"
CLASSIFICATION_GOVERNMENT = "GOVERNMENT"
#: No dataset of any kind — the clean production state before first intake.
#: Distinct from DEVELOPMENT_TEST, which asserts that development data exists.
CLASSIFICATION_NONE = "NO_DATASET_LOADED"

#: Why no authoritative hash was available, when that is the answer.
REASON_NO_INTAKE = "NO_DELIVERY_RECORDED"
REASON_UNUSABLE = "RECORDED_CHECKSUM_IS_NOT_A_SHA256"


def is_real_sha256(value: Any) -> bool:
    """True only for a genuine 64-character hex digest.

    Deliberately strict. "cafe", "deadbeef", "x", "" and None are all rejected,
    which is the entire point — each of those has been observed in this
    codebase's provenance fields, and each passed a truthiness check.
    """
    return bool(value) and bool(_SHA256.match(str(value).strip().lower()))


@dataclass(frozen=True)
class SourceProvenance:
    """The delivery a report's population was computed from.

    Frozen: a report that has been generated cannot have its provenance edited
    afterwards, and the type should make that awkward rather than merely
    discouraged.
    """

    #: None when no usable delivery hash exists. Never a placeholder.
    sha256: Optional[str] = None
    original_filename: Optional[str] = None
    record_count: Optional[int] = None
    schema_fingerprint: Optional[str] = None
    intake_id: Optional[str] = None
    received_at: Optional[str] = None
    status: Optional[str] = None
    #: DEVELOPMENT_TEST until the Government delivery is the artefact in Area 1.
    data_classification: str = CLASSIFICATION_DEVELOPMENT
    #: Set when `sha256` is None, so a reader can tell "not tracked" from
    #: "tracked and unusable" — different facts with different remedies.
    unavailable_reason: Optional[str] = None

    @property
    def is_government_data(self) -> bool:
        return self.data_classification == CLASSIFICATION_GOVERNMENT

    @property
    def has_no_dataset(self) -> bool:
        """True for a clean deployment holding no dataset at all."""
        return self.data_classification == CLASSIFICATION_NONE

    @property
    def has_authoritative_hash(self) -> bool:
        return is_real_sha256(self.sha256)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_government_data"] = self.is_government_data
        d["has_no_dataset"] = self.has_no_dataset
        d["has_authoritative_hash"] = self.has_authoritative_hash
        return d


def _classification() -> str:
    """GOVERNMENT only when an authorised Government dataset is actually loaded.

    This used to read `is_running_mock()` and treat "not mock" as "Government".
    Once that flag stopped meaning "no dataset configured" and started meaning
    "this deployment serves mock data", the inverted reading would have labelled
    a clean PRODUCTION report as GOVERNMENT — the opposite of the defect being
    corrected, and a worse one.

    It now reads the data-state model, where GOVERNMENT requires a controlled
    intake with complete provenance. Anything else is development: an empty
    production deployment produces no Government-classified report, because it
    has no Government data to report on.
    """
    try:
        from app.Tefca.data_state import DataIdentity, data_state_sync

        identity = data_state_sync().data_identity
        if identity is DataIdentity.GOVERNMENT:
            return CLASSIFICATION_GOVERNMENT
        if identity is DataIdentity.NONE:
            # Empty production. Saying DEVELOPMENT_TEST here would assert that
            # development evidence exists, which is the same class of untruth
            # this correction exists to remove — just pointing the other way.
            return CLASSIFICATION_NONE
        return CLASSIFICATION_DEVELOPMENT
    except Exception as exc:  # noqa: BLE001
        # Unknown means development. Defaulting the other way would let an
        # import failure silently upgrade a development report to a
        # Government-labelled one.
        logger.warning("data classification unavailable, assuming development: %s", exc)
        return CLASSIFICATION_DEVELOPMENT


async def authoritative_source_provenance(db) -> SourceProvenance:
    """The Area-1 delivery behind the current population.

    Reads the most recent successfully parsed intake. A placeholder or
    malformed checksum is reported as *unavailable with a reason*, never
    forwarded — a report may honestly say it does not know its source hash, and
    may not say a wrong one.
    """
    from app.tefca_registry.rce.models import RceSourceIntake

    classification = _classification()
    try:
        row = (await db.execute(
            select(RceSourceIntake)
            .where(RceSourceIntake.duplicate_of_intake_id.is_(None))
            .order_by(RceSourceIntake.received_at.desc())
            .limit(1)
        )).scalars().first()
    except Exception as exc:  # noqa: BLE001
        logger.info("Area 1 intake unavailable: %s", exc)
        return SourceProvenance(data_classification=classification,
                                unavailable_reason=REASON_NO_INTAKE)

    if row is None:
        return SourceProvenance(data_classification=classification,
                                unavailable_reason=REASON_NO_INTAKE)

    digest = (row.sha256 or "").strip().lower()
    if not is_real_sha256(digest):
        # The delivery is recorded but its checksum is unusable. Say exactly
        # that; do not fall back to another table hoping for a better answer.
        logger.error("Area 1 intake %s has a non-SHA256 checksum %r", row.id, row.sha256)
        return SourceProvenance(
            original_filename=row.original_filename,
            record_count=row.record_count,
            schema_fingerprint=row.schema_fingerprint,
            intake_id=str(row.id),
            received_at=row.received_at.isoformat() if row.received_at else None,
            status=row.status,
            data_classification=classification,
            unavailable_reason=REASON_UNUSABLE)

    return SourceProvenance(
        sha256=digest,
        original_filename=row.original_filename,
        record_count=row.record_count,
        schema_fingerprint=row.schema_fingerprint,
        intake_id=str(row.id),
        received_at=row.received_at.isoformat() if row.received_at else None,
        status=row.status,
        data_classification=classification)


# ── Report cycle ────────────────────────────────────────────────────────────
#
# A report cycle answers "which run of the review does this report belong to".
# The contract's reporting rhythm (weekly D3.1, the Task 3 retrospective, Task 4
# ongoing, Task 5 priority) is program-defined, and the COR has not yet issued
# the cycle labelling to use — so inventing a contractual cycle identifier here
# would be manufacturing a contractual artefact.
#
# What is available, and is genuinely useful, is a *development* cycle that is
# deterministic: the same evidence version over the same delivery always
# produces the same identifier, and any change to either produces a different
# one. That gives report reproducibility something to key on without pretending
# to be a contract cycle. It is prefixed so it can never be mistaken for one.

DEV_CYCLE_PREFIX = "DEV-CYCLE"

#: Set when the report engine is asked for a contractual cycle it cannot know.
CYCLE_GUIDANCE_PENDING = "PROGRAM_GUIDANCE_PENDING"


def development_cycle_id(evidence_version: Optional[str],
                         source_sha256: Optional[str]) -> str:
    """A deterministic, obviously-non-contractual cycle identifier.

    `DEV-CYCLE-<evidence version>-<first 12 of the source hash>`. Reproducible
    from the two things that actually determine what a report can say, and
    unmistakably not a Government cycle label.
    """
    version = (evidence_version or "unversioned").strip()
    digest = (source_sha256 or "").strip().lower()
    anchor = digest[:12] if is_real_sha256(digest) else "nosource"
    return f"{DEV_CYCLE_PREFIX}-{version}-{anchor}"


def resolve_cycle_id(explicit: Optional[str], *, evidence_version: Optional[str],
                     source_sha256: Optional[str]) -> str:
    """The cycle a report belongs to.

    An explicitly supplied cycle wins — a caller working inside a real review
    cycle knows better than this function. Otherwise a development cycle is
    derived, so the field is never null. A null cycle was the previous
    behaviour and it made a stored report impossible to scope after the fact.
    """
    if explicit:
        return str(explicit)
    return development_cycle_id(evidence_version, source_sha256)
