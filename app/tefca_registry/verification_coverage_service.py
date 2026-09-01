"""Verification Coverage for one entity, from the observations already stored.

READS ONLY. This assembles what the methodology says about an entity from
records that already exist — it performs no source lookup, writes nothing, and
concludes nothing a human has not concluded.

WHY AN ADAPTER RATHER THAN A NEW EVIDENCE TABLE
───────────────────────────────────────────────
`tefca_verifications` already holds the observations, with source, status,
identifier, label and timestamp. Introducing a second evidence store to hold the
same facts in the new shape would create two answers to "what did LEIE say", and
the older one would go stale first. The methodology is a READING of the existing
evidence, so it is computed on demand from it.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.core.evidence_vocabulary import ObservationState
from app.tefca_registry.verification_methodology import (
    Observation, PreliminaryAssessment, preliminary_assessment)

#: How a stored `verification_status` maps onto what the source SAID.
#:
#: `unavailable` and `not_found` are the two the whole methodology turns on and
#: they are deliberately kept apart here, at the boundary, rather than
#: normalised into one "missing" value that no later layer could separate again.
STATUS_TO_OBSERVATION: Dict[str, str] = {
    "verified": ObservationState.MATCH_OBSERVED.value,
    "match": ObservationState.MATCH_OBSERVED.value,
    "matched": ObservationState.MATCH_OBSERVED.value,
    "not_found": ObservationState.NO_MATCH_OBSERVED.value,
    "no_match": ObservationState.NO_MATCH_OBSERVED.value,
    "unavailable": ObservationState.SOURCE_UNAVAILABLE.value,
    "source_unavailable": ObservationState.SOURCE_UNAVAILABLE.value,
    "not_applicable": ObservationState.LOOKUP_NOT_APPLICABLE.value,
    "ambiguous": ObservationState.AMBIGUOUS.value,
    "multiple": ObservationState.MULTIPLE_MATCHES.value,
    "error": ObservationState.ERROR.value,
}

#: Statuses that assert the evidence CONTRADICTS the entity. Only these may
#: reach CONFLICT, and none of them means "absent".
CONTRADICTING_STATUSES = frozenset({
    "conflict", "mismatch", "excluded", "adverse", "revoked",
})


def evidence_hash(*parts: Optional[str]) -> str:
    """A stable digest of what a source returned, for reproducibility.

    Hashes the OBSERVATION, not the entity — two identical answers about the
    same organisation produce the same digest, so an unchanged observation is
    visibly unchanged across runs.
    """
    material = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def observation_from_row(row) -> Observation:
    """One stored verification row, as a methodology observation."""
    status = (getattr(row, "verification_status", "") or "").strip().lower()
    contradicts = status in CONTRADICTING_STATUSES
    state = STATUS_TO_OBSERVATION.get(
        status,
        # An unmapped status must not silently become a match or a miss. It
        # goes to a human, which is what MANUAL_VERIFICATION_REQUIRED means one
        # layer up.
        ObservationState.AMBIGUOUS.value)

    retrieved = getattr(row, "verified_at", None)
    return Observation(
        source=(getattr(row, "source", "") or "").strip().lower(),
        state=state,
        contradicts=contradicts,
        matched_identifier=getattr(row, "lookup_identifier", None),
        match_method=getattr(row, "data_source_label", None),
        retrieved_at=retrieved.isoformat() if retrieved else None,
        detail=getattr(row, "detail", None),
        evidence_hash=evidence_hash(
            getattr(row, "source", None), status,
            getattr(row, "lookup_identifier", None),
            getattr(row, "detail", None)),
    )


async def coverage_for_entity(db, entity_id) -> Optional[PreliminaryAssessment]:
    """The methodology's reading of one entity. None if the entity is unknown."""
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    entity = (await db.execute(
        select(reg.TefcaRegEntity)
        .where(reg.TefcaRegEntity.id == entity_id))).scalars().first()
    if entity is None:
        return None

    rows = (await db.execute(
        select(reg.TefcaVerification)
        .where(reg.TefcaVerification.entity_id == entity_id))).scalars().all()
    observations = [observation_from_row(r) for r in rows]

    # The delivered record is the participation anchor and the classification
    # input — never an external source.
    curated = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.canonical_entity_id == entity_id)
    )).scalars().first()

    record: Dict[str, Any] = {"name": entity.name}
    if curated is not None:
        source = (await db.execute(
            select(m.RceSourceRecord)
            .where(m.RceSourceRecord.id == curated.source_record_id)
        )).scalars().first()
        if source is not None and source.parsed:
            record.update({
                "sequoia_org_type": source.parsed.get("sequoiaorgtype"),
                "name": source.parsed.get("name") or entity.name,
            })
        # The delivered relationship is itself the TEFCA anchor observation.
        observations.append(Observation(
            source="rce", state=ObservationState.MATCH_OBSERVED.value,
            match_method="delivered_population",
            detail="Present in the RCE/QHIN-delivered participant population.",
            evidence_hash=evidence_hash("rce", "delivered",
                                        curated.rce_org_oid)))

    return preliminary_assessment(
        entity.rce_org_oid or str(entity.id), record, observations)
