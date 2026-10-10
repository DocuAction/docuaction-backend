"""Evidence inquiry and Entity Evidence Profile.

An inquiry names a SUBJECT (organization, identifier, location, relationship)
and the questions being asked. A profile is the structured, faceted view of
everything observed about one canonical entity: identity, business/corporate
identity, location, healthcare authority, typed relationships and history.
It is an arrangement of observations with their provenance — it adds no
facts, decides nothing, and every facet carries the sources it came from.

Acquisition / interpretation firewall: nothing here knows whether an
observation came from a CSV, a ZIP, an API, a manual retrieval or a
commercial delivery; it reads observations only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .delta import HistoricalDelta, compute_deltas
from .observations import EvidenceObservation, ObservationType, SourceAuthority


class InquirySubjectType(str, Enum):
    ORGANIZATION = "ORGANIZATION"
    IDENTIFIER = "IDENTIFIER"
    LOCATION = "LOCATION"
    RELATIONSHIP = "RELATIONSHIP"


@dataclass(frozen=True)
class EvidenceInquiry:
    subject_type: InquirySubjectType
    subject_value: Dict[str, Any]              # e.g. {"npi": "…"}, {"name": "…"}, {"line1": …}, {"kind": …}
    questions: List[str] = field(default_factory=list)
    canonical_entity_id: Optional[str] = None
    program_context: Optional[str] = None
    requested_by: Optional[str] = None
    inquiry_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"inquiry_id": self.inquiry_id, "subject_type": self.subject_type.value,
                "subject_value": dict(self.subject_value), "questions": list(self.questions),
                "canonical_entity_id": self.canonical_entity_id, "program_context": self.program_context,
                "requested_by": self.requested_by}


class ProfileFacet(str, Enum):
    IDENTITY = "IDENTITY"                          # identifiers and program/registry names
    BUSINESS_IDENTITY = "BUSINESS_IDENTITY"        # corporate legal name, registration, domestic/foreign, agent
    LOCATION = "LOCATION"
    HEALTHCARE_AUTHORITY = "HEALTHCARE_AUTHORITY"  # federal program enrollment, exclusion/integrity records
    RELATIONSHIPS = "RELATIONSHIPS"
    HISTORY = "HISTORY"


_BUSINESS_AUTHORITIES = {SourceAuthority.STATE_REGISTRY}
_HEALTHCARE_AUTHORITIES = {SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT, SourceAuthority.FEDERAL_EXCLUSION_OR_INTEGRITY}


def facet_of(o: EvidenceObservation) -> ProfileFacet:
    if o.observation_type is ObservationType.LOCATION:
        return ProfileFacet.LOCATION
    if o.observation_type is ObservationType.RELATIONSHIP:
        return ProfileFacet.RELATIONSHIPS
    if o.observation_type is ObservationType.PROGRAM_PARTICIPATION or o.source_authority in _HEALTHCARE_AUTHORITIES:
        return ProfileFacet.HEALTHCARE_AUTHORITY
    if o.source_authority in _BUSINESS_AUTHORITIES:
        return ProfileFacet.BUSINESS_IDENTITY
    return ProfileFacet.IDENTITY


@dataclass
class EntityEvidenceProfile:
    canonical_entity_id: Optional[str]
    facets: Dict[ProfileFacet, List[EvidenceObservation]] = field(default_factory=dict)
    history: List[HistoricalDelta] = field(default_factory=list)
    sources: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # source_id → {authority, editions, count}

    @classmethod
    def from_observations(cls, canonical_entity_id: Optional[str], current: List[EvidenceObservation],
                          prior: Optional[List[EvidenceObservation]] = None) -> "EntityEvidenceProfile":
        p = cls(canonical_entity_id=canonical_entity_id, facets={f: [] for f in ProfileFacet})
        for o in current:
            p.facets[facet_of(o)].append(o)
            src = p.sources.setdefault(o.source_id, {"authority": o.source_authority.value, "editions": set(), "count": 0})
            src["count"] += 1
            if o.source_delivery_id:
                src["editions"].add(o.source_delivery_id)
        if prior is not None:
            p.history = compute_deltas(prior, current)
        return p

    def observations(self, facet: ProfileFacet) -> List[EvidenceObservation]:
        return list(self.facets.get(facet, []))

    def to_dict(self) -> Dict[str, Any]:
        return {"canonical_entity_id": self.canonical_entity_id,
                "facets": {f.value: [o.to_dict() for o in obs] for f, obs in self.facets.items()},
                "history": [d.to_dict() for d in self.history],
                "sources": {s: {"authority": v["authority"], "editions": sorted(v["editions"]), "count": v["count"]}
                            for s, v in self.sources.items()},
                "note": "Entity Evidence Profile: observations arranged by facet with provenance. No determination."}
