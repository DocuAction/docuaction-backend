"""
Evidence provenance — the facts without which a determination cannot be re-checked.

THE QUESTION THIS EXISTS TO MAKE ANSWERABLE
───────────────────────────────────────────
    "Why was Entity X classified B2 on 22 August 2026?"

Six months later that answer must name WHICH edition of each source was
consulted. Today it cannot: NPPES records "2.1" (its API version) and OIG LEIE
records the literal string "CSV-UPDATED". Neither is a data version, and the
evidence table has no hash column at all.

API VERSION IS NOT DATASET VERSION
──────────────────────────────────
This module refuses to let one stand in for the other. `SourceVersionRef` keeps
`api_version` and `dataset_version` in separate fields, and a source that
publishes no data version gets `dataset_version=None` with
`is_point_in_time=False` — an explicit statement that the observation is NOT
reproducible, rather than a version string that implies it is.

    Manufacturing a version from a retrieval date would be worse than a null:
    a null says "we do not know"; a synthesised value says "we do know", and
    that is a claim nobody can withdraw later.

WHY THIS LIVES IN app/core/
Same reason as `evidence_vocabulary`: `app/core/__init__.py` is empty, so the
connectors (app/Tefca), the registry (app/tefca_registry) and the RCE pipeline
can all import it with no side effect and no cycle.

WHAT THIS MODULE DOES NOT DO
It records provenance. It does not fetch, ingest, classify, or decide anything.
There is no PPEF ingestion here and no observation store.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

#: Bumped when the provenance CAPTURE logic changes — separate from the
#: vocabulary version, because what we record and what we call it can move
#: independently.
PROVENANCE_MODEL_VERSION = "1.0"

#: Recorded when a source publishes no data version of any kind. A literal
#: sentinel rather than a null so a reader can tell "we asked and there is none"
#: from "nobody filled this in".
UNKNOWN_DATASET_VERSION = "UNKNOWN"


class RetrievalMethod(str, Enum):
    API = "API"                        # live request, no preserved copy
    DOWNLOAD = "DOWNLOAD"              # file fetched and preserved
    LOCAL_SNAPSHOT = "LOCAL_SNAPSHOT"  # read from an ingested snapshot
    LOCAL_FILE = "LOCAL_FILE"          # read from preserved bytes (Area 1)


class MatchMethod(str, Enum):
    """How a record was matched. LEVELS, not confidence — see the note."""

    EXACT_IDENTIFIER = "EXACT_IDENTIFIER"    # level 1
    STRUCTURED = "STRUCTURED"                # level 2
    FUZZY = "FUZZY"                          # level 3
    HUMAN = "HUMAN"                          # level 4
    NONE = "NONE"


#: Match level per method. Levels 2 and 3 may CORROBORATE a level-1 or level-4
#: conclusion; neither may establish identity alone. That constraint is recorded
#: here and enforced by the caller — this module states the level, it does not
#: decide what may be done with it.
MATCH_LEVEL: Dict[str, int] = {
    MatchMethod.EXACT_IDENTIFIER.value: 1,
    MatchMethod.STRUCTURED.value: 2,
    MatchMethod.FUZZY.value: 3,
    MatchMethod.HUMAN.value: 4,
    MatchMethod.NONE.value: 0,
}

#: Levels that may establish identity on their own.
LEVELS_THAT_MAY_ESTABLISH_IDENTITY = frozenset({1, 4})


# ── hashing ──────────────────────────────────────────────────────────────────

def canonical_json(payload: Any) -> str:
    """Deterministic JSON. Two equal payloads always produce the same string."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def observation_hash(payload: Any) -> str:
    """SHA-256 over the canonicalised raw response.

    This is what turns "the source said X" from an assertion into something
    checkable. The hash is taken over the RAW response, before any shaping —
    a hash of a projection only proves what we chose to keep.
    """
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── source version reference ─────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceVersionRef:
    """Which edition of a source answered, and whether that is reproducible.

    `api_version` and `dataset_version` are SEPARATE FIELDS ON PURPOSE. NPPES
    publishes an API version ("2.1") and no data version; recording the former
    in the latter's place is the specific defect this class exists to prevent.
    """

    source: str
    retrieval_method: RetrievalMethod
    retrieved_at: str
    #: The SOURCE's own version/edition label. None when it publishes none.
    dataset_version: Optional[str] = None
    #: The source's own as-of date. None when it publishes none.
    source_as_of: Optional[str] = None
    #: SHA-256 of the retrieved artefact, where an artefact exists.
    source_file_hash: Optional[str] = None
    #: Stable id of the dataset itself (e.g. a CMS dataset UUID).
    dataset_identifier: Optional[str] = None
    #: The API's own version. NEVER a substitute for dataset_version.
    api_version: Optional[str] = None
    #: Transport metadata. A CDN artefact, not the source's as-of date.
    http_last_modified: Optional[str] = None
    record_count: Optional[int] = None
    storage_uri: Optional[str] = None
    note: Optional[str] = None

    @property
    def is_point_in_time(self) -> bool:
        """True only when this observation could actually be reproduced.

        Requires either a preserved artefact (a file hash) or a stable dataset
        identifier plus a version. A live API call with neither is NOT
        point-in-time, and says so rather than implying otherwise.
        """
        if self.source_file_hash:
            return True
        return bool(self.dataset_identifier and self.dataset_version)

    @property
    def effective_dataset_version(self) -> str:
        """The version to record. UNKNOWN when the source publishes none.

        Deliberately does NOT fall back to `api_version` or `retrieved_at`.
        """
        return self.dataset_version or UNKNOWN_DATASET_VERSION

    def as_row(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "version_label": self.effective_dataset_version,
            "source_as_of": self.source_as_of,
            "source_file_hash": self.source_file_hash,
            "dataset_identifier": self.dataset_identifier,
            "api_version": self.api_version,
            "http_last_modified": self.http_last_modified,
            "record_count": self.record_count,
            "retrieved_at": self.retrieved_at,
            "retrieval_method": self.retrieval_method.value,
            "storage_uri": self.storage_uri,
            "is_point_in_time": self.is_point_in_time,
            "note": self.note,
        }


def unknown_version(source: str, *, api_version: Optional[str] = None,
                    retrieved_at: Optional[str] = None,
                    note: Optional[str] = None) -> SourceVersionRef:
    """A source that publishes no data version. Honest, not empty.

    Used for NPPES (API version only) and for OIG LEIE before its downloaded
    bytes are hashed. `is_point_in_time` is False, which is the whole point.
    """
    return SourceVersionRef(
        source=source,
        retrieval_method=RetrievalMethod.API,
        retrieved_at=retrieved_at or datetime.now(timezone.utc).isoformat(),
        dataset_version=None,
        api_version=api_version,
        note=note or (f"{source} publishes no dataset version or as-of date. "
                      f"Recorded as UNKNOWN; this observation is NOT reproducible "
                      f"from the source and must not be described as though it were."),
    )


# ── PPEF relational lineage ──────────────────────────────────────────────────

class PpefRelationship(str, Enum):
    """The enrolment-level relationships PPEF publishes.

    Recorded as an ordered HOP LIST rather than columns, because the reality is
    one-to-many at every level: a provider may hold several enrolments, an
    enrolment several practice locations. A fixed column set would force one of
    each and quietly discard the rest.
    """

    ENROLLED_AS = "enrolled_as"                        # NPI -> ENRLMT_ID
    HAS_PRACTICE_LOCATION = "has_practice_location"    # ENRLMT_ID -> address
    HAS_SECONDARY_SPECIALTY = "has_secondary_specialty"
    HAS_ADDITIONAL_NPI = "has_additional_npi"
    #: from = REASGN_BNFT_ENRLMT_ID (practitioner)
    #: to   = RCV_BNFT_ENRLMT_ID    (receiving entity)
    REASSIGNS_BENEFITS_TO = "reassigns_benefits_to"


class IdentifierType(str, Enum):
    NPI = "npi"
    PAC_ID = "pac_id"
    ENROLLMENT_ID = "enrollment_id"
    TEFCAID = "tefcaid"
    HCID = "hcid"
    UEI = "uei"
    ADDRESS = "address"
    TAXONOMY = "taxonomy"
    ORG_NAME = "org_name"


@dataclass(frozen=True)
class LineageHop:
    """One traversal step, with the artefact that supplied it.

    `source_version_id` is on EVERY hop, not on the evidence item, because
    different PPEF components are different files with different hashes and a
    single evidence item can legitimately traverse two of them.
    """

    hop_sequence: int
    from_identifier_type: str
    from_identifier_value: str
    relationship_type: str
    to_identifier_type: Optional[str] = None
    to_identifier_value: Optional[str] = None
    ppef_component: Optional[str] = None
    source_row_key: Optional[str] = None
    source_version_id: Optional[str] = None

    def as_row(self) -> Dict[str, Any]:
        return {
            "hop_sequence": self.hop_sequence,
            "from_identifier_type": self.from_identifier_type,
            "from_identifier_value": self.from_identifier_value,
            "relationship_type": self.relationship_type,
            "to_identifier_type": self.to_identifier_type,
            "to_identifier_value": self.to_identifier_value,
            "ppef_component": self.ppef_component,
            "source_row_key": self.source_row_key,
            "source_version_id": self.source_version_id,
        }


def build_ppef_lineage(
    npi: str,
    enrollments: List[Dict[str, Any]],
    *,
    source_version_id: Optional[str] = None,
) -> List[LineageHop]:
    """Turn a PPEF traversal into an ordered hop list. Flattens nothing.

    `enrollments` is a list of dicts shaped like::

        {"enrollment_id": "I2004...", "pac_id": "123...",
         "practice_locations": [...], "secondary_specialties": [...],
         "additional_npis": [...], "reassignments": [{"receiving_enrollment_id": ...}]}

    EVERY enrolment produces a hop, and every child of every enrolment produces
    its own hop. A provider with three enrolments and five locations yields
    eight hops, not one summary row — because "PECOS matched" is not an answer
    to "which enrolment, and via which relationship".
    """
    hops: List[LineageHop] = []
    seq = 0

    def add(**kw) -> None:
        nonlocal seq
        seq += 1
        hops.append(LineageHop(hop_sequence=seq, source_version_id=source_version_id, **kw))

    for enr in enrollments or []:
        enrollment_id = enr.get("enrollment_id")
        if not enrollment_id:
            continue
        add(from_identifier_type=IdentifierType.NPI.value,
            from_identifier_value=npi,
            relationship_type=PpefRelationship.ENROLLED_AS.value,
            to_identifier_type=IdentifierType.ENROLLMENT_ID.value,
            to_identifier_value=enrollment_id,
            ppef_component="ENROLLMENT",
            source_row_key=enr.get("row_key") or enrollment_id)

        # PAC ID is a first-class identifier of the enrolling provider and may
        # span several enrolments. Recorded as its own hop so provider-level
        # aggregation is a query rather than a JSONB scan.
        if enr.get("pac_id"):
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=enrollment_id,
                relationship_type=PpefRelationship.ENROLLED_AS.value,
                to_identifier_type=IdentifierType.PAC_ID.value,
                to_identifier_value=str(enr["pac_id"]),
                ppef_component="ENROLLMENT",
                source_row_key=enr.get("row_key") or enrollment_id)

        for loc in enr.get("practice_locations") or []:
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=enrollment_id,
                relationship_type=PpefRelationship.HAS_PRACTICE_LOCATION.value,
                to_identifier_type=IdentifierType.ADDRESS.value,
                to_identifier_value=_address_key(loc),
                ppef_component="PRACTICE_LOCATION",
                source_row_key=loc.get("row_key"))

        for spec in enr.get("secondary_specialties") or []:
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=enrollment_id,
                relationship_type=PpefRelationship.HAS_SECONDARY_SPECIALTY.value,
                to_identifier_type=IdentifierType.TAXONOMY.value,
                to_identifier_value=str(spec.get("taxonomy") or spec.get("code") or ""),
                ppef_component="SECONDARY_SPECIALTY",
                source_row_key=spec.get("row_key"))

        for extra in enr.get("additional_npis") or []:
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=enrollment_id,
                relationship_type=PpefRelationship.HAS_ADDITIONAL_NPI.value,
                to_identifier_type=IdentifierType.NPI.value,
                to_identifier_value=str(extra.get("npi") or extra),
                ppef_component="ADDITIONAL_NPIS",
                source_row_key=(extra.get("row_key") if isinstance(extra, dict) else None))

        for re_asgn in enr.get("reassignments") or []:
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=enrollment_id,
                relationship_type=PpefRelationship.REASSIGNS_BENEFITS_TO.value,
                to_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                to_identifier_value=str(re_asgn.get("receiving_enrollment_id")
                                        or re_asgn.get("RCV_BNFT_ENRLMT_ID") or ""),
                ppef_component="REASSIGNMENT",
                source_row_key=re_asgn.get("row_key"))
    return hops


def _address_key(loc: Dict[str, Any]) -> str:
    parts = [loc.get("ADR_LN_1") or loc.get("line1") or "",
             loc.get("CITY_NAME") or loc.get("city") or "",
             loc.get("STATE_CD") or loc.get("state") or "",
             loc.get("ZIP_CD") or loc.get("postal_code") or ""]
    return "|".join(str(p).strip() for p in parts)


# ── the provenance record ────────────────────────────────────────────────────

@dataclass
class ObservationProvenance:
    """Everything needed to re-check one observation, six months later.

    Assembled by the caller and flattened onto the evidence row. This class
    performs no lookup and reaches no conclusion — it records.
    """

    source: str
    entity_id: str
    dimension: str
    identifier_searched: Optional[str] = None
    identifier_type: Optional[str] = None
    observation_result: Optional[str] = None
    version: Optional[SourceVersionRef] = None
    raw_payload: Optional[Any] = None
    raw_observation_ref: Optional[str] = None
    match_method: str = MatchMethod.NONE.value
    match_version: Optional[str] = None
    rule_version: Optional[str] = None
    correlation_id: Optional[str] = None
    vocabulary_version: Optional[str] = None
    lineage: List[LineageHop] = field(default_factory=list)

    @property
    def match_level(self) -> int:
        return MATCH_LEVEL.get(self.match_method, 0)

    @property
    def observation_hash(self) -> Optional[str]:
        return observation_hash(self.raw_payload) if self.raw_payload is not None else None

    def as_evidence_columns(self) -> Dict[str, Any]:
        """The additive columns on `tefca_dimension_evidence`.

        `source_version_id` is resolved by the caller after the version row is
        written, so it is deliberately absent here.
        """
        return {
            "observation_result": self.observation_result,
            "identifier_searched": self.identifier_searched,
            "identifier_type": self.identifier_type,
            "observation_hash": self.observation_hash,
            "raw_observation_ref": self.raw_observation_ref,
            "match_method": self.match_method,
            "match_level": self.match_level,
            "match_version": self.match_version,
            "rule_version": self.rule_version,
            "correlation_id": self.correlation_id,
            "vocabulary_version": self.vocabulary_version,
        }


def new_correlation_id() -> str:
    """One id for every observation produced by a single run."""
    return str(uuid.uuid4())
