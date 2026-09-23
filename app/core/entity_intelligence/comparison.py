"""Identity comparison — structured signals, never a merged truth and never a vote.

Each dimension (identifier, name, location) compares the DELIVERED observation
(the subject under review) against independent-evidence observations and
returns a signal plus a controlled explanation. A signal says what the
evidence shows; it does not say what the analyst should conclude, and every
result carries `requires_human_review = True`.

NO SOURCE VOTING
    A conflict from one authoritative source is reported as a conflict even if
    two other sources agree. Agreement is recorded per source, never counted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .explanations import explain
from .normalize import (address_is_usable, name_core, normalize_address,
                        normalize_name)
from .observations import (ADMINISTRATIVE_ROLES, CARE_SITE_ROLES, EvidenceObservation, LocationRole, NameKind,
                           ObservationType, SourceAuthority)

COMPARISON_RULES_VERSION = "1.0"


class Dimension(str, Enum):
    ORGANIZATION_IDENTITY = "ORGANIZATION_IDENTITY"
    NAME_IDENTITY = "NAME_IDENTITY"
    LOCATION_IDENTITY = "LOCATION_IDENTITY"
    RELATIONSHIP_IDENTITY = "RELATIONSHIP_IDENTITY"
    #: Historical identity is expressed through deltas (delta.py), not a comparison.
    #: Participation: the entity's observed relationship to a program. Compared
    #: only within one "<PROGRAM>:<KIND>" role; a Medicare enrollment is never
    #: evidence for or against a TEFCA Participant relationship.
    PARTICIPATION_IDENTITY = "PARTICIPATION_IDENTITY"


class IdentifierSignal(str, Enum):
    IDENTIFIER_CORROBORATED = "IDENTIFIER_CORROBORATED"
    IDENTIFIER_CONFLICT = "IDENTIFIER_CONFLICT"
    MULTIPLE_CANDIDATE_ENTITIES = "MULTIPLE_CANDIDATE_ENTITIES"
    MISSING_IDENTIFIER = "MISSING_IDENTIFIER"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class NameSignal(str, Enum):
    DIRECT_NAME_MATCH = "DIRECT_NAME_MATCH"
    NORMALIZED_NAME_MATCH = "NORMALIZED_NAME_MATCH"
    OTHER_NAME_MATCH = "OTHER_NAME_MATCH"
    DBA_MATCH_IDENTIFIED = "DBA_MATCH_IDENTIFIED"
    FORMER_NAME_MATCH = "FORMER_NAME_MATCH"
    NAME_CONFLICT = "NAME_CONFLICT"
    AMBIGUOUS_NAME = "AMBIGUOUS_NAME"
    INSUFFICIENT_NAME_EVIDENCE = "INSUFFICIENT_NAME_EVIDENCE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class LocationSignal(str, Enum):
    PRIMARY_LOCATION_MATCH = "PRIMARY_LOCATION_MATCH"
    ADDITIONAL_PRACTICE_LOCATION_MATCH = "ADDITIONAL_PRACTICE_LOCATION_MATCH"
    NORMALIZED_LOCATION_MATCH = "NORMALIZED_LOCATION_MATCH"
    MAILING_LOCATION_MATCH = "MAILING_LOCATION_MATCH"
    LOCATION_CONFLICT = "LOCATION_CONFLICT"
    #: The address is the same but the source assigns it a non-care role
    #: (registered agent, headquarters, principal office). Not a match, not a conflict.
    ROLE_ASSIGNMENT_DIFFERS = "ROLE_ASSIGNMENT_DIFFERS"
    AMBIGUOUS_LOCATION = "AMBIGUOUS_LOCATION"
    INSUFFICIENT_LOCATION_EVIDENCE = "INSUFFICIENT_LOCATION_EVIDENCE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class RelationshipSignal(str, Enum):
    RELATIONSHIP_CORROBORATED = "RELATIONSHIP_CORROBORATED"
    RELATIONSHIP_CONFLICT = "RELATIONSHIP_CONFLICT"
    #: Same relationship, same object, but the sources state different validity periods.
    RELATIONSHIP_PERIOD_DIFFERS = "RELATIONSHIP_PERIOD_DIFFERS"
    RELATIONSHIP_NOT_COMPARABLE = "RELATIONSHIP_NOT_COMPARABLE"   # different kinds
    INSUFFICIENT_RELATIONSHIP_EVIDENCE = "INSUFFICIENT_RELATIONSHIP_EVIDENCE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


#: Signals that mean "the evidence explains the delivered value even though
#: it is not the source's primary value".
class ParticipationSignal(str, Enum):
    PARTICIPATION_OBSERVED = "PARTICIPATION_OBSERVED"                      # source records the same program relationship
    PARTICIPATION_CONFLICT = "PARTICIPATION_CONFLICT"                      # source records a different value for the same kind
    PARTICIPATION_NOT_COMPARABLE = "PARTICIPATION_NOT_COMPARABLE"          # source only has other programs/kinds
    PARTICIPATION_EVIDENCE_NOT_FOUND = "PARTICIPATION_EVIDENCE_NOT_FOUND"  # searched; the applicable dataset has no record
    INSUFFICIENT_PARTICIPATION_EVIDENCE = "INSUFFICIENT_PARTICIPATION_EVIDENCE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


EXPLAINING_NAME_SIGNALS = {NameSignal.DBA_MATCH_IDENTIFIED, NameSignal.OTHER_NAME_MATCH,
                           NameSignal.FORMER_NAME_MATCH}
EXPLAINING_LOCATION_SIGNALS = {LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH,
                               LocationSignal.MAILING_LOCATION_MATCH}
CORROBORATING_SIGNALS = {ParticipationSignal.PARTICIPATION_OBSERVED, IdentifierSignal.IDENTIFIER_CORROBORATED,
                         NameSignal.DIRECT_NAME_MATCH, NameSignal.NORMALIZED_NAME_MATCH,
                         LocationSignal.PRIMARY_LOCATION_MATCH,
                         LocationSignal.NORMALIZED_LOCATION_MATCH,
                         RelationshipSignal.RELATIONSHIP_CORROBORATED}
CONFLICT_SIGNALS = {ParticipationSignal.PARTICIPATION_CONFLICT, IdentifierSignal.IDENTIFIER_CONFLICT, NameSignal.NAME_CONFLICT,
                    LocationSignal.LOCATION_CONFLICT, RelationshipSignal.RELATIONSHIP_CONFLICT}
UNAVAILABLE_SIGNALS = {ParticipationSignal.SOURCE_UNAVAILABLE, IdentifierSignal.SOURCE_UNAVAILABLE, NameSignal.SOURCE_UNAVAILABLE,
                       LocationSignal.SOURCE_UNAVAILABLE, RelationshipSignal.SOURCE_UNAVAILABLE}
INSUFFICIENT_SIGNALS = {ParticipationSignal.PARTICIPATION_EVIDENCE_NOT_FOUND,
                        ParticipationSignal.INSUFFICIENT_PARTICIPATION_EVIDENCE,
                        ParticipationSignal.PARTICIPATION_NOT_COMPARABLE,
                        IdentifierSignal.MISSING_IDENTIFIER, NameSignal.INSUFFICIENT_NAME_EVIDENCE,
                        LocationSignal.INSUFFICIENT_LOCATION_EVIDENCE,
                        RelationshipSignal.INSUFFICIENT_RELATIONSHIP_EVIDENCE,
                        RelationshipSignal.RELATIONSHIP_NOT_COMPARABLE}
AMBIGUOUS_SIGNALS = {LocationSignal.ROLE_ASSIGNMENT_DIFFERS, RelationshipSignal.RELATIONSHIP_PERIOD_DIFFERS,
                     IdentifierSignal.MULTIPLE_CANDIDATE_ENTITIES, NameSignal.AMBIGUOUS_NAME,
                     LocationSignal.AMBIGUOUS_LOCATION}


@dataclass(frozen=True)
class ComparisonResult:
    dimension: Dimension
    signal: Enum
    source_id: str
    delivered_observation_id: Optional[str]
    matched_observation_id: Optional[str] = None
    candidate_observation_ids: List[str] = field(default_factory=list)
    explanation: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    requires_human_review: bool = True     # always; a system signal is never a determination
    rules_version: str = COMPARISON_RULES_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {"dimension": self.dimension.value, "signal": self.signal.value,
                "source_id": self.source_id,
                "delivered_observation_id": self.delivered_observation_id,
                "matched_observation_id": self.matched_observation_id,
                "candidate_observation_ids": list(self.candidate_observation_ids),
                "explanation": self.explanation, "detail": dict(self.detail),
                "requires_human_review": self.requires_human_review,
                "rules_version": self.rules_version}


def _delivered(observations: List[EvidenceObservation], kind: ObservationType,
               role: Optional[str] = None) -> Optional[EvidenceObservation]:
    for o in observations:
        if (o.observation_type is kind and o.source_authority is SourceAuthority.PROGRAM_DELIVERY
                and (role is None or o.role == role)):
            return o
    return None


def _unavailable(observations: List[EvidenceObservation], source_id: str) -> bool:
    """A source records its unavailability as an observation with
    observed_value {"unavailable": True}; that is a fact about access."""
    return any(o.source_id == source_id and o.observed_value.get("unavailable") for o in observations)


# ── organisation identity (identifier) ─────────────────────────────────────

def compare_identifier(observations: List[EvidenceObservation], *, source_id: str,
                       identifier_system: str = "NPI") -> ComparisonResult:
    delivered = _delivered(observations, ObservationType.IDENTIFIER, identifier_system)
    dim = Dimension.ORGANIZATION_IDENTITY
    if _unavailable(observations, source_id):
        return ComparisonResult(dim, IdentifierSignal.SOURCE_UNAVAILABLE, source_id,
                                delivered.observation_id if delivered else None,
                                explanation=explain("SOURCE_UNAVAILABLE", source=source_id))
    if delivered is None or not str(delivered.observed_value.get("value", "")).strip():
        return ComparisonResult(dim, IdentifierSignal.MISSING_IDENTIFIER, source_id, None,
                                explanation=explain("MISSING_IDENTIFIER", system=identifier_system))
    value = str(delivered.observed_value["value"]).strip()
    found = [o for o in observations
             if o.source_id == source_id and o.observation_type is ObservationType.IDENTIFIER
             and o.role == identifier_system]
    same = [o for o in found if str(o.observed_value.get("value", "")).strip() == value]
    if len(same) == 1:
        entity_type = same[0].observed_value.get("entity_type")
        return ComparisonResult(dim, IdentifierSignal.IDENTIFIER_CORROBORATED, source_id,
                                delivered.observation_id, same[0].observation_id,
                                explanation=explain("IDENTIFIER_CORROBORATED", system=identifier_system,
                                                    source=source_id, entity_type=entity_type or "unstated"),
                                detail={"entity_type": entity_type})
    if len(same) > 1:
        return ComparisonResult(dim, IdentifierSignal.MULTIPLE_CANDIDATE_ENTITIES, source_id,
                                delivered.observation_id,
                                candidate_observation_ids=[o.observation_id for o in same],
                                explanation=explain("MULTIPLE_CANDIDATE_ENTITIES", system=identifier_system,
                                                    source=source_id, count=len(same)))
    if found:
        return ComparisonResult(dim, IdentifierSignal.IDENTIFIER_CONFLICT, source_id,
                                delivered.observation_id,
                                candidate_observation_ids=[o.observation_id for o in found],
                                explanation=explain("IDENTIFIER_CONFLICT", system=identifier_system,
                                                    source=source_id),
                                detail={"delivered": value,
                                        "source_values": [o.observed_value.get("value") for o in found]})
    return ComparisonResult(dim, IdentifierSignal.MISSING_IDENTIFIER, source_id,
                            delivered.observation_id,
                            explanation=explain("NO_SOURCE_RECORD", system=identifier_system, source=source_id))


# ── name identity ───────────────────────────────────────────────────────────

_NAME_KIND_SIGNAL = {
    NameKind.DOING_BUSINESS_AS.value: NameSignal.DBA_MATCH_IDENTIFIED,
    NameKind.FORMER_LEGAL_BUSINESS_NAME.value: NameSignal.FORMER_NAME_MATCH,
    NameKind.OTHER_NAME.value: NameSignal.OTHER_NAME_MATCH,
    NameKind.TRADE_NAME.value: NameSignal.OTHER_NAME_MATCH,
}


def compare_names(observations: List[EvidenceObservation], *, source_id: str) -> ComparisonResult:
    delivered = _delivered(observations, ObservationType.NAME)
    dim = Dimension.NAME_IDENTITY
    if _unavailable(observations, source_id):
        return ComparisonResult(dim, NameSignal.SOURCE_UNAVAILABLE, source_id,
                                delivered.observation_id if delivered else None,
                                explanation=explain("SOURCE_UNAVAILABLE", source=source_id))
    if delivered is None or not str(delivered.observed_value.get("name", "")).strip():
        return ComparisonResult(dim, NameSignal.INSUFFICIENT_NAME_EVIDENCE, source_id, None,
                                explanation=explain("NO_DELIVERED_NAME"))
    delivered_name = str(delivered.observed_value["name"])
    d_norm = normalize_name(delivered_name)
    names = [o for o in observations
             if o.source_id == source_id and o.observation_type is ObservationType.NAME]
    if not names:
        return ComparisonResult(dim, NameSignal.INSUFFICIENT_NAME_EVIDENCE, source_id,
                                delivered.observation_id,
                                explanation=explain("NO_SOURCE_NAMES", source=source_id))

    legal = [o for o in names if o.role == NameKind.LEGAL_BUSINESS_NAME.value]
    # 1. legal name, exact then normalised
    for o in legal:
        if str(o.observed_value.get("name", "")) == delivered_name:
            return ComparisonResult(dim, NameSignal.DIRECT_NAME_MATCH, source_id,
                                    delivered.observation_id, o.observation_id,
                                    explanation=explain("DIRECT_NAME_MATCH", source=source_id))
    for o in legal:
        if normalize_name(o.observed_value.get("name")) == d_norm and d_norm:
            return ComparisonResult(dim, NameSignal.NORMALIZED_NAME_MATCH, source_id,
                                    delivered.observation_id, o.observation_id,
                                    explanation=explain("NORMALIZED_NAME_MATCH", source=source_id),
                                    detail={"normalized": d_norm})
    # 2. other names — the signal depends on the SOURCE'S type code, never assumed DBA
    other = [o for o in names if o.role != NameKind.LEGAL_BUSINESS_NAME.value]
    hits = [o for o in other if normalize_name(o.observed_value.get("name")) == d_norm and d_norm]
    if hits:
        kinds = {o.role for o in hits}
        if len(hits) > 1 and len(kinds) > 1:
            return ComparisonResult(dim, NameSignal.AMBIGUOUS_NAME, source_id, delivered.observation_id,
                                    candidate_observation_ids=[o.observation_id for o in hits],
                                    explanation=explain("AMBIGUOUS_NAME_KINDS", source=source_id,
                                                        kinds=", ".join(sorted(k or "UNKNOWN" for k in kinds))))
        hit = hits[0]
        signal = _NAME_KIND_SIGNAL.get(hit.role or "", NameSignal.OTHER_NAME_MATCH)
        legal_name = legal[0].observed_value.get("name") if legal else None
        key = {NameSignal.DBA_MATCH_IDENTIFIED: "DBA_MATCH_IDENTIFIED",
               NameSignal.FORMER_NAME_MATCH: "FORMER_NAME_MATCH"}.get(signal, "OTHER_NAME_MATCH")
        return ComparisonResult(dim, signal, source_id, delivered.observation_id, hit.observation_id,
                                candidate_observation_ids=[o.observation_id for o in hits],
                                explanation=explain(key, source=source_id, legal_name=legal_name or "unstated",
                                                    kind=hit.role or "UNKNOWN"),
                                detail={"legal_name": legal_name, "matched_kind": hit.role})
    # 3. same core with different suffix → ambiguous, not a match
    if legal and any(name_core(o.observed_value.get("name")) == name_core(delivered_name) and name_core(delivered_name)
                     for o in legal):
        return ComparisonResult(dim, NameSignal.AMBIGUOUS_NAME, source_id, delivered.observation_id,
                                candidate_observation_ids=[o.observation_id for o in legal],
                                explanation=explain("AMBIGUOUS_NAME_SUFFIX", source=source_id))
    return ComparisonResult(dim, NameSignal.NAME_CONFLICT, source_id, delivered.observation_id,
                            candidate_observation_ids=[o.observation_id for o in names],
                            explanation=explain("NAME_CONFLICT", source=source_id,
                                                legal_name=(legal[0].observed_value.get("name") if legal else "unstated"),
                                                other_count=len(other)),
                            detail={"delivered": delivered_name,
                                    "source_names": [{"kind": o.role, "name": o.observed_value.get("name")} for o in names]})


# ── location identity ───────────────────────────────────────────────────────

_ROLE_SIGNAL = {
    LocationRole.PRIMARY_PRACTICE_LOCATION.value: LocationSignal.PRIMARY_LOCATION_MATCH,
    LocationRole.ADDITIONAL_PRACTICE_LOCATION.value: LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH,
    LocationRole.MAILING_LOCATION.value: LocationSignal.MAILING_LOCATION_MATCH,
}


def _loc_norm(o: EvidenceObservation) -> Dict[str, str]:
    return o.normalized_value or normalize_address(o.observed_value)


def _same_address(a: Dict[str, str], b: Dict[str, str]) -> bool:
    same_street = (a.get("street") or a["line1"]) == (b.get("street") or b["line1"])
    return same_street and a["zip5"] == b["zip5"] and a["zip5"] != "" and a["line1"] != ""


def _same_locality(a: Dict[str, str], b: Dict[str, str]) -> bool:
    return (a["city"] == b["city"] and a["state"] == b["state"] and a["city"] != "") or \
           (a["zip5"] == b["zip5"] and a["zip5"] != "")


def compare_locations(observations: List[EvidenceObservation], *, source_id: str) -> ComparisonResult:
    delivered = _delivered(observations, ObservationType.LOCATION)
    dim = Dimension.LOCATION_IDENTITY
    if _unavailable(observations, source_id):
        return ComparisonResult(dim, LocationSignal.SOURCE_UNAVAILABLE, source_id,
                                delivered.observation_id if delivered else None,
                                explanation=explain("SOURCE_UNAVAILABLE", source=source_id))
    if delivered is None or not address_is_usable(_loc_norm(delivered)):
        return ComparisonResult(dim, LocationSignal.INSUFFICIENT_LOCATION_EVIDENCE, source_id,
                                delivered.observation_id if delivered else None,
                                explanation=explain("NO_DELIVERED_ADDRESS"))
    d = _loc_norm(delivered)
    locs = [o for o in observations
            if o.source_id == source_id and o.observation_type is ObservationType.LOCATION]
    usable = [o for o in locs if address_is_usable(_loc_norm(o))]
    if not usable:
        return ComparisonResult(dim, LocationSignal.INSUFFICIENT_LOCATION_EVIDENCE, source_id,
                                delivered.observation_id,
                                explanation=explain("NO_SOURCE_ADDRESSES", source=source_id))
    exact = [o for o in usable if _same_address(_loc_norm(o), d)]
    if exact:
        # Prefer the primary role if several roles carry the same address.
        order = [LocationRole.PRIMARY_PRACTICE_LOCATION.value, LocationRole.SITE_OF_CARE.value,
                 LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, LocationRole.BRANCH_LOCATION.value,
                 LocationRole.MOBILE_HOME_BASE.value, LocationRole.MOBILE_FACILITY.value,
                 LocationRole.MAILING_LOCATION.value]
        exact.sort(key=lambda o: order.index(o.role) if o.role in order else 99)
        hit = exact[0]
        if (hit.role or "") in ADMINISTRATIVE_ROLES and not any((o.role or "") in CARE_SITE_ROLES or o.role == LocationRole.MAILING_LOCATION.value for o in exact):
            # SAME ADDRESS != SAME ROLE: a registered-agent or corporate address that equals the
            # delivered site is neither a site-of-care match nor a conflict.
            return ComparisonResult(dim, LocationSignal.ROLE_ASSIGNMENT_DIFFERS, source_id, delivered.observation_id,
                                    hit.observation_id, candidate_observation_ids=[o.observation_id for o in exact],
                                    explanation=explain("ROLE_ASSIGNMENT_DIFFERS", source=source_id, role=hit.role,
                                                        delivered_role=delivered.role or LocationRole.DELIVERED_LOCATION.value),
                                    detail={"matched_role": hit.role, "delivered_role": delivered.role, "normalized": d})
        signal = _ROLE_SIGNAL.get(hit.role or "", LocationSignal.NORMALIZED_LOCATION_MATCH)
        key = {LocationSignal.PRIMARY_LOCATION_MATCH: "PRIMARY_LOCATION_MATCH",
               LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH: "ADDITIONAL_PRACTICE_LOCATION_MATCH",
               LocationSignal.MAILING_LOCATION_MATCH: "MAILING_LOCATION_MATCH"}.get(signal, "NORMALIZED_LOCATION_MATCH")
        return ComparisonResult(dim, signal, source_id, delivered.observation_id, hit.observation_id,
                                candidate_observation_ids=[o.observation_id for o in exact],
                                explanation=explain(key, source=source_id, role=hit.role or "UNKNOWN"),
                                detail={"matched_role": hit.role, "normalized": d})
    locality = [o for o in usable if _same_locality(_loc_norm(o), d)]
    if locality:
        return ComparisonResult(dim, LocationSignal.AMBIGUOUS_LOCATION, source_id, delivered.observation_id,
                                candidate_observation_ids=[o.observation_id for o in locality],
                                explanation=explain("AMBIGUOUS_LOCATION", source=source_id, count=len(locality)),
                                detail={"delivered": d})
    return ComparisonResult(dim, LocationSignal.LOCATION_CONFLICT, source_id, delivered.observation_id,
                            candidate_observation_ids=[o.observation_id for o in usable],
                            explanation=explain("LOCATION_CONFLICT", source=source_id, count=len(usable)),
                            detail={"delivered": d,
                                    "source_locations": [{"role": o.role, **_loc_norm(o)} for o in usable]})


# ── relationship identity ───────────────────────────────────────────────────

def compare_relationships(observations: List[EvidenceObservation], *, source_id: str,
                          kind_code: str) -> ComparisonResult:
    """Only observations of the SAME relationship kind are compared. A
    corporate-parent relationship from a commercial source is never compared
    with a program's QHIN→Participant relationship."""
    dim = Dimension.RELATIONSHIP_IDENTITY
    delivered = [o for o in observations if o.observation_type is ObservationType.RELATIONSHIP
                 and o.source_authority is SourceAuthority.PROGRAM_DELIVERY and o.role == kind_code]
    if _unavailable(observations, source_id):
        return ComparisonResult(dim, RelationshipSignal.SOURCE_UNAVAILABLE, source_id,
                                delivered[0].observation_id if delivered else None,
                                explanation=explain("SOURCE_UNAVAILABLE", source=source_id))
    source_rels = [o for o in observations if o.observation_type is ObservationType.RELATIONSHIP
                   and o.source_id == source_id]
    same_kind = [o for o in source_rels if o.role == kind_code]
    if not delivered:
        return ComparisonResult(dim, RelationshipSignal.INSUFFICIENT_RELATIONSHIP_EVIDENCE, source_id, None,
                                explanation=explain("NO_DELIVERED_RELATIONSHIP", kind=kind_code))
    if not same_kind:
        signal = (RelationshipSignal.RELATIONSHIP_NOT_COMPARABLE if source_rels
                  else RelationshipSignal.INSUFFICIENT_RELATIONSHIP_EVIDENCE)
        return ComparisonResult(dim, signal, source_id, delivered[0].observation_id,
                                candidate_observation_ids=[o.observation_id for o in source_rels],
                                explanation=explain("RELATIONSHIP_NOT_COMPARABLE" if source_rels else "NO_SOURCE_RELATIONSHIP",
                                                    source=source_id, kind=kind_code,
                                                    other_kinds=", ".join(sorted({o.role or "UNKNOWN" for o in source_rels}))))
    d_targets = {normalize_name(o.observed_value.get("related_entity_name")) for o in delivered}
    s_targets = {normalize_name(o.observed_value.get("related_entity_name")) for o in same_kind}
    if d_targets & s_targets:
        hit = next(o for o in same_kind if normalize_name(o.observed_value.get("related_entity_name")) in d_targets)
        d_hit = next(o for o in delivered if normalize_name(o.observed_value.get("related_entity_name")) in s_targets)
        d_period = (d_hit.observed_value.get("valid_from"), d_hit.observed_value.get("valid_to"))
        s_period = (hit.observed_value.get("valid_from"), hit.observed_value.get("valid_to"))
        if any(d_period) and any(s_period) and d_period != s_period:
            # DIFFERENT VALIDITY PERIODS != AUTOMATIC CONTRADICTION
            return ComparisonResult(dim, RelationshipSignal.RELATIONSHIP_PERIOD_DIFFERS, source_id,
                                    delivered[0].observation_id, hit.observation_id,
                                    explanation=explain("RELATIONSHIP_PERIOD_DIFFERS", source=source_id, kind=kind_code,
                                                        delivered_period=f"{d_period[0] or 'unstated'}..{d_period[1] or 'open'}",
                                                        source_period=f"{s_period[0] or 'unstated'}..{s_period[1] or 'open'}"),
                                    detail={"delivered_period": d_period, "source_period": s_period})
        return ComparisonResult(dim, RelationshipSignal.RELATIONSHIP_CORROBORATED, source_id,
                                delivered[0].observation_id, hit.observation_id,
                                explanation=explain("RELATIONSHIP_CORROBORATED", source=source_id, kind=kind_code))
    return ComparisonResult(dim, RelationshipSignal.RELATIONSHIP_CONFLICT, source_id, delivered[0].observation_id,
                            candidate_observation_ids=[o.observation_id for o in same_kind],
                            explanation=explain("RELATIONSHIP_CONFLICT", source=source_id, kind=kind_code),
                            detail={"delivered": sorted(d_targets), "source": sorted(s_targets)})


# ── participation / program identity ────────────────────────────────────────

def _participation_value(o: EvidenceObservation) -> str:
    v = o.observed_value
    return normalize_name(str(v.get("status") or v.get("value") or v.get("related_entity_name") or ""))


def compare_participation(observations: List[EvidenceObservation], *, source_id: str,
                          participation_role: str) -> ComparisonResult:
    """Compare a delivered program relationship with a source's statement of
    the SAME "<PROGRAM>:<KIND>" role only.

    An absence observation (observed_value["absent"]) becomes
    PARTICIPATION_EVIDENCE_NOT_FOUND with its reason and applicability echoed —
    never a conflict and never "not enrolled". A source that carries only other
    programs or kinds is NOT_COMPARABLE.
    """
    dim = Dimension.PARTICIPATION_IDENTITY
    delivered = [o for o in observations if o.observation_type is ObservationType.PROGRAM_PARTICIPATION
                 and o.source_authority is SourceAuthority.PROGRAM_DELIVERY and o.role == participation_role]
    if _unavailable(observations, source_id):
        return ComparisonResult(dim, ParticipationSignal.SOURCE_UNAVAILABLE, source_id,
                                delivered[0].observation_id if delivered else None,
                                explanation=explain("SOURCE_UNAVAILABLE", source=source_id))
    source_obs = [o for o in observations if o.observation_type is ObservationType.PROGRAM_PARTICIPATION
                  and o.source_id == source_id]
    same_role = [o for o in source_obs if o.role == participation_role]
    absent = [o for o in same_role if o.observed_value.get("absent")]
    present = [o for o in same_role if not o.observed_value.get("absent")]
    if absent and not present:
        a = absent[0]
        return ComparisonResult(dim, ParticipationSignal.PARTICIPATION_EVIDENCE_NOT_FOUND, source_id,
                                delivered[0].observation_id if delivered else None,
                                candidate_observation_ids=[a.observation_id],
                                explanation=explain("PARTICIPATION_EVIDENCE_NOT_FOUND", source=source_id,
                                                    role=participation_role, reason=a.observed_value.get("reason"),
                                                    applicability=a.applicability.value,
                                                    dataset_version=a.observed_value.get("dataset_version")),
                                detail={"reason": a.observed_value.get("reason"), "applicability": a.applicability.value})
    if not delivered:
        # Nothing delivered to compare against: a source statement alone is context, not corroboration.
        return ComparisonResult(dim, ParticipationSignal.INSUFFICIENT_PARTICIPATION_EVIDENCE, source_id, None,
                                candidate_observation_ids=[o.observation_id for o in present],
                                explanation=explain("NO_DELIVERED_PARTICIPATION", role=participation_role))
    if not present:
        signal = (ParticipationSignal.PARTICIPATION_NOT_COMPARABLE if source_obs
                  else ParticipationSignal.INSUFFICIENT_PARTICIPATION_EVIDENCE)
        key = "PARTICIPATION_NOT_COMPARABLE" if source_obs else "NO_SOURCE_PARTICIPATION"
        return ComparisonResult(dim, signal, source_id, delivered[0].observation_id,
                                candidate_observation_ids=[o.observation_id for o in source_obs],
                                explanation=explain(key, source=source_id, role=participation_role,
                                                    other_roles=", ".join(sorted({o.role or "UNKNOWN" for o in source_obs}))))
    d_values = {_participation_value(o) for o in delivered}
    hits = [o for o in present if _participation_value(o) in d_values or not _participation_value(o)]
    if hits:
        return ComparisonResult(dim, ParticipationSignal.PARTICIPATION_OBSERVED, source_id,
                                delivered[0].observation_id, hits[0].observation_id,
                                candidate_observation_ids=[o.observation_id for o in hits],
                                explanation=explain("PARTICIPATION_OBSERVED", source=source_id, role=participation_role))
    return ComparisonResult(dim, ParticipationSignal.PARTICIPATION_CONFLICT, source_id, delivered[0].observation_id,
                            candidate_observation_ids=[o.observation_id for o in present],
                            explanation=explain("PARTICIPATION_CONFLICT", source=source_id, role=participation_role),
                            detail={"delivered": sorted(d_values), "source": sorted({_participation_value(o) for o in present})})


def compare_all(observations: List[EvidenceObservation], *, source_id: str,
                identifier_system: str = "NPI",
                relationship_kinds: Optional[List[str]] = None,
                participation_roles: Optional[List[str]] = None) -> List[ComparisonResult]:
    results = [compare_identifier(observations, source_id=source_id, identifier_system=identifier_system),
               compare_names(observations, source_id=source_id),
               compare_locations(observations, source_id=source_id)]
    for kind in relationship_kinds or []:
        results.append(compare_relationships(observations, source_id=source_id, kind_code=kind))
    for role in participation_roles or []:
        results.append(compare_participation(observations, source_id=source_id, participation_role=role))
    return results
