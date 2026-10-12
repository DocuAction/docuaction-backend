"""Source observations — one source's statement about one entity, kept apart.

NO MERGED TRUTH RECORD
    ONC/RCE says "ABC Mobile Clinic"; NPPES legal says "ABC Healthcare LLC";
    NPPES Other Name (DBA) says "ABC Mobile Clinic". Those are three
    observations with three provenances. Nothing here collapses them; the
    comparison layer relates them and the analyst reads them side by side.

WHAT AN OBSERVATION CARRIES
    Only what the source actually stated. Optional fields are None when the
    source does not publish them (a file delivery has no `observed_at` beyond
    its file date; an API lookup has no `effective_from`). Nothing is filled in
    to look complete.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.evidence_provenance import SourceVersionRef, observation_hash

OBSERVATION_MODEL_VERSION = "1.0"


class ObservationType(str, Enum):
    IDENTIFIER = "IDENTIFIER"
    NAME = "NAME"
    LOCATION = "LOCATION"
    RELATIONSHIP = "RELATIONSHIP"
    #: The entity's observed relationship to a PROGRAM (e.g. a Medicare
    #: enrollment observed in a CMS public file, a TEFCA Participant listing
    #: delivered by the RCE). `role` carries "<PROGRAM>:<KIND>" so no program
    #: vocabulary is hard-coded here; "MEDICARE:ENROLLMENT" and
    #: "TEFCA:PARTICIPANT" are never compared with each other.
    PROGRAM_PARTICIPATION = "PROGRAM_PARTICIPATION"


class SourceAuthority(str, Enum):
    """Whose statement it is — a controlled DESCRIPTIVE classification, not a
    ranking. No numeric weight exists and none is derived from this enum.
    Program delivery is the SUBJECT under review and never corroborates itself."""
    PROGRAM_DELIVERY = "PROGRAM_DELIVERY"                      # ONC/RCE delivered data (the subject)
    RCE_GOVERNING_MATERIAL = "RCE_GOVERNING_MATERIAL"          # Common Agreement / SOP text: establishes RULES, never facts
    RCE_PROVIDED_THIRD_PARTY = "RCE_PROVIDED_THIRD_PARTY"      # e.g. IQVIA file handed over by the RCE
    FEDERAL_REGISTRY = "FEDERAL_REGISTRY"                      # federal IDENTITY reference (NPPES)
    FEDERAL_PROGRAM_ENROLLMENT = "FEDERAL_PROGRAM_ENROLLMENT"  # federal program enrollment data (CMS PPEF, provider-type files)
    FEDERAL_EXCLUSION_OR_INTEGRITY = "FEDERAL_EXCLUSION_OR_INTEGRITY"  # OIG LEIE, SAM exclusions, CMS revocations
    STATE_REGISTRY = "STATE_REGISTRY"                          # state public business registries
    COMMERCIAL_REFERENCE = "COMMERCIAL_REFERENCE"              # licensed commercial evidence obtained by AGT
    SUPPLEMENTAL = "SUPPLEMENTAL"                              # website, geocoding and similar corroboration
    DOCUACTION_HISTORICAL = "DOCUACTION_HISTORICAL"            # DocuAction's own prior observation
    PRIOR_HUMAN_DETERMINATION = "PRIOR_HUMAN_DETERMINATION"    # a recorded analyst/QA decision (reference only)
    UNKNOWN = "UNKNOWN"


class AbsenceReason(str, Enum):
    """Why a source produced no record. Absence is an observation with a
    reason, never a finding: NO CMS ENROLLMENT RECORD != NOT ENROLLED."""
    NOT_APPLICABLE = "NOT_APPLICABLE"                  # the source cannot answer for this kind of entity
    NOT_IN_POPULATION = "NOT_IN_POPULATION"            # the source's population does not include this entity type
    SOURCE_LIMITATION = "SOURCE_LIMITATION"            # the source omits this field/edition/history
    IDENTIFIER_NOT_AVAILABLE = "IDENTIFIER_NOT_AVAILABLE"  # no key to search with
    NOT_FOUND = "NOT_FOUND"                            # searched with a valid key; nothing returned
    DATA_ISSUE = "DATA_ISSUE"                          # the source flagged or omitted the record for quality reasons


class ValueHandling(str, Enum):
    """How the observed value may be held. Public data may be stored raw;
    licensed or restricted sources may only permit a hash or a reference, or
    transient processing with nothing persisted."""
    RAW_PERMITTED = "RAW_PERMITTED"
    HASHED_REFERENCE_ONLY = "HASHED_REFERENCE_ONLY"
    TRANSIENT_ONLY = "TRANSIENT_ONLY"
    RESTRICTED_DISPLAY = "RESTRICTED_DISPLAY"


class DeliveryPath(str, Enum):
    """How the evidence reached DocuAction. Distinct from who owns it: an IQVIA
    file handed over by the RCE is THIRD_PARTY_DELIVERY, not a query."""
    DIRECT_QUERY = "DIRECT_QUERY"
    FILE_DOWNLOAD = "FILE_DOWNLOAD"
    THIRD_PARTY_DELIVERY = "THIRD_PARTY_DELIVERY"
    OPERATOR_UPLOAD = "OPERATOR_UPLOAD"


class EvidenceApplicability(str, Enum):
    REQUIRED = "REQUIRED"
    APPLICABLE = "APPLICABLE"
    CONDITIONALLY_APPLICABLE = "CONDITIONALLY_APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class NameKind(str, Enum):
    DELIVERED = "DELIVERED"
    LEGAL_BUSINESS_NAME = "LEGAL_BUSINESS_NAME"
    DOING_BUSINESS_AS = "DOING_BUSINESS_AS"
    FORMER_LEGAL_BUSINESS_NAME = "FORMER_LEGAL_BUSINESS_NAME"
    OTHER_NAME = "OTHER_NAME"
    TRADE_NAME = "TRADE_NAME"
    UNKNOWN = "UNKNOWN"


class LocationRole(str, Enum):
    """Assigned ONLY when the source says so. A mobile facility is never
    inferred from an address string. SAME ADDRESS != SAME ROLE."""
    DELIVERED_LOCATION = "DELIVERED_LOCATION"
    CORPORATE_HEADQUARTERS = "CORPORATE_HEADQUARTERS"
    PRINCIPAL_OFFICE = "PRINCIPAL_OFFICE"
    PRIMARY_PRACTICE_LOCATION = "PRIMARY_PRACTICE_LOCATION"
    ADDITIONAL_PRACTICE_LOCATION = "ADDITIONAL_PRACTICE_LOCATION"
    SITE_OF_CARE = "SITE_OF_CARE"
    MOBILE_HOME_BASE = "MOBILE_HOME_BASE"
    REGISTERED_AGENT_ADDRESS = "REGISTERED_AGENT_ADDRESS"
    MAILING_ADDRESS = "MAILING_ADDRESS"
    BRANCH_LOCATION = "BRANCH_LOCATION"
    UNKNOWN_SOURCE_ROLE = "UNKNOWN_SOURCE_ROLE"
    # retained from the foundation (NPPES mailing block; generic registered location; source-stated mobile)
    MAILING_LOCATION = "MAILING_LOCATION"
    REGISTERED_LOCATION = "REGISTERED_LOCATION"
    MOBILE_FACILITY = "MOBILE_FACILITY"
    UNKNOWN = "UNKNOWN"


#: Roles at which care is delivered or a service site is stated. A match on a
#: role outside this set is ROLE_ASSIGNMENT_DIFFERS, never a location match.
CARE_SITE_ROLES = frozenset({
    LocationRole.DELIVERED_LOCATION.value, LocationRole.PRIMARY_PRACTICE_LOCATION.value,
    LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, LocationRole.SITE_OF_CARE.value,
    LocationRole.BRANCH_LOCATION.value, LocationRole.MOBILE_HOME_BASE.value, LocationRole.MOBILE_FACILITY.value,
})
#: Roles that describe a legal/administrative address, not a site of care.
ADMINISTRATIVE_ROLES = frozenset({
    LocationRole.CORPORATE_HEADQUARTERS.value, LocationRole.PRINCIPAL_OFFICE.value,
    LocationRole.REGISTERED_AGENT_ADDRESS.value, LocationRole.REGISTERED_LOCATION.value,
    LocationRole.MAILING_ADDRESS.value,
})


class RelationshipDirection(str, Enum):
    SUBJECT_TO_OBJECT = "SUBJECT_TO_OBJECT"
    OBJECT_TO_SUBJECT = "OBJECT_TO_SUBJECT"
    UNDIRECTED = "UNDIRECTED"


@dataclass(frozen=True)
class RelationshipKind:
    """Relationship semantics are OPEN and program-owned. A program registers
    the kinds it delivers ("QHIN_PARTICIPANT", "PARTICIPANT_SUBPARTICIPANT");
    a commercial source registers its own ("CORPORATE_PARENT_HCO",
    "HCO_PRACTICE_LOCATION"). They are never interchangeable: comparison only
    relates observations of the SAME kind."""
    code: str
    source_authority: SourceAuthority
    description: str = ""


@dataclass(frozen=True)
class Provenance:
    """Where an observation came from, precisely enough to re-check it."""
    source_owner: str                       # e.g. "CMS NPPES", "IQVIA OneKey", the program
    delivery_path: DeliveryPath
    received_by: str = "DocuAction"
    source_version: Optional[SourceVersionRef] = None
    source_file_sha256: Optional[str] = None
    source_record_ref: Optional[str] = None   # line number, row key, API request
    parser_version: Optional[str] = None
    schema_version: Optional[str] = None
    #: How the observed value may be held (see ValueHandling).
    value_handling: ValueHandling = ValueHandling.RAW_PERMITTED
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_owner": self.source_owner,
            "delivery_path": self.delivery_path.value,
            "received_by": self.received_by,
            "source_version": self.source_version.as_row() if self.source_version else None,
            "source_file_sha256": self.source_file_sha256,
            "source_record_ref": self.source_record_ref,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "value_handling": self.value_handling.value,
            "note": self.note,
        }


@dataclass(frozen=True)
class EvidenceObservation:
    canonical_entity_id: Optional[str]
    source_id: str                          # canonical source key, e.g. "NPPES_V2"
    observation_type: ObservationType
    observed_value: Dict[str, Any]          # the source's fields, as stated
    source_authority: SourceAuthority
    provenance: Provenance
    source_delivery_id: Optional[str] = None
    source_record_id: Optional[str] = None
    source_field: Optional[str] = None
    #: NAME: NameKind; LOCATION: LocationRole; RELATIONSHIP: RelationshipKind.code;
    #: IDENTIFIER: the identifier system (e.g. "NPI").
    role: Optional[str] = None
    normalized_value: Optional[Dict[str, Any]] = None
    applicability: EvidenceApplicability = EvidenceApplicability.APPLICABLE
    observed_at: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    ingested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    observation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def content_hash(self) -> str:
        return observation_hash({
            "source_id": self.source_id, "type": self.observation_type.value,
            "role": self.role, "value": self.observed_value,
            "record": self.source_record_id, "field": self.source_field})

    def safe_value(self) -> Dict[str, Any]:
        """The value as it may be persisted or displayed under the source's
        value-handling rule. RAW_PERMITTED returns the value; HASHED returns a
        hash only; TRANSIENT returns nothing; RESTRICTED returns the value
        (display control is the consumer's duty and is flagged)."""
        vh = self.provenance.value_handling
        if vh is ValueHandling.RAW_PERMITTED or vh is ValueHandling.RESTRICTED_DISPLAY:
            return dict(self.observed_value)
        if vh is ValueHandling.HASHED_REFERENCE_ONLY:
            return {"value_hash": observation_hash(self.observed_value), "kind": self.observed_value.get("kind")}
        return {"withheld": "TRANSIENT_ONLY", "kind": self.observed_value.get("kind")}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "canonical_entity_id": self.canonical_entity_id,
            "source_id": self.source_id,
            "source_delivery_id": self.source_delivery_id,
            "source_record_id": self.source_record_id,
            "source_field": self.source_field,
            "observation_type": self.observation_type.value,
            "role": self.role,
            "observed_value": self.safe_value(),
            "normalized_value": dict(self.normalized_value) if self.normalized_value else None,
            "observed_at": self.observed_at,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "ingested_at": self.ingested_at,
            "source_authority": self.source_authority.value,
            "applicability": self.applicability.value,
            "provenance": self.provenance.to_dict(),
            "content_hash": self.content_hash,
            "model_version": OBSERVATION_MODEL_VERSION,
        }


def location_observation(*, canonical_entity_id: Optional[str], source_id: str, role: str, raw_address: Dict[str, Any],
                         source_authority: SourceAuthority, provenance: Provenance, kind: Optional[str] = None,
                         effective_from: Optional[str] = None, effective_to: Optional[str] = None,
                         observed_at: Optional[str] = None, source_delivery_id: Optional[str] = None,
                         applicability: EvidenceApplicability = EvidenceApplicability.APPLICABLE) -> EvidenceObservation:
    """An address observation that preserves the RAW address exactly as the
    source stated it and carries the normalized comparison key beside it, with
    the normalisation rule version. Normalisation assists comparison; it never
    rewrites the source evidence."""
    from .normalize import normalize_address
    value = {"raw_address": dict(raw_address), **{k: raw_address.get(k) for k in ("line1", "line2", "city", "state", "postal_code", "country_code")}}
    if kind:
        value["kind"] = kind
    return EvidenceObservation(canonical_entity_id=canonical_entity_id, source_id=source_id,
                               observation_type=ObservationType.LOCATION, role=role, observed_value=value,
                               normalized_value=normalize_address(raw_address), source_authority=source_authority,
                               provenance=provenance, effective_from=effective_from, effective_to=effective_to,
                               observed_at=observed_at, source_delivery_id=source_delivery_id, applicability=applicability)


def relationship_observation(*, canonical_entity_id: Optional[str], source_id: str, program_context: str, kind: str,
                             subject: str, obj: str, source_authority: SourceAuthority, provenance: Provenance,
                             direction: RelationshipDirection = RelationshipDirection.SUBJECT_TO_OBJECT,
                             raw_relationship: Optional[str] = None, valid_from: Optional[str] = None,
                             valid_to: Optional[str] = None, observed_at: Optional[str] = None,
                             source_delivery_id: Optional[str] = None) -> EvidenceObservation:
    """A typed relationship. role = "<PROGRAM_CONTEXT>:<KIND>" so kinds from
    different programs are never compared. Preserves subject, relationship,
    object, direction, validity, observation time, raw and normalized forms."""
    from .normalize import normalize_name
    role = f"{program_context}:{kind}"
    value = {"subject": subject, "relationship": kind, "object": obj, "related_entity_name": obj,
             "direction": direction.value, "program_context": program_context,
             "raw_relationship": raw_relationship or f"{subject} {kind} {obj}",
             "normalized_relationship": f"{normalize_name(subject)}|{kind.lower()}|{normalize_name(obj)}",
             "valid_from": valid_from, "valid_to": valid_to}
    return EvidenceObservation(canonical_entity_id=canonical_entity_id, source_id=source_id,
                               observation_type=ObservationType.RELATIONSHIP, role=role, observed_value=value,
                               normalized_value={"normalized_relationship": value["normalized_relationship"]},
                               source_authority=source_authority, provenance=provenance,
                               effective_from=valid_from, effective_to=valid_to, observed_at=observed_at,
                               source_delivery_id=source_delivery_id)


def absence(*, canonical_entity_id: Optional[str], source_id: str, observation_type: ObservationType,
            reason: AbsenceReason, source_authority: SourceAuthority, provenance: Provenance,
            role: Optional[str] = None, applicability: EvidenceApplicability = EvidenceApplicability.APPLICABLE,
            note: Optional[str] = None, dataset_version: Optional[str] = None) -> EvidenceObservation:
    """A recorded absence. Carries the reason and the applicability so the
    comparison engine can say 'not found in the applicable current CMS public
    enrollment dataset' and never 'not enrolled'."""
    return EvidenceObservation(canonical_entity_id=canonical_entity_id, source_id=source_id,
                               observation_type=observation_type, role=role,
                               observed_value={"absent": True, "reason": reason.value, "note": note,
                                               "dataset_version": dataset_version},
                               source_authority=source_authority, provenance=provenance,
                               applicability=applicability)


def observations_of(observations: List[EvidenceObservation],
                    kind: ObservationType,
                    *, source_id: Optional[str] = None) -> List[EvidenceObservation]:
    return [o for o in observations
            if o.observation_type is kind and (source_id is None or o.source_id == source_id)]
