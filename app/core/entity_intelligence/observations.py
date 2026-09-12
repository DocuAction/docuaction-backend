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


class SourceAuthority(str, Enum):
    """Whose statement it is. Program delivery is the SUBJECT under review and
    is never corroborating evidence for itself."""
    PROGRAM_DELIVERY = "PROGRAM_DELIVERY"
    FEDERAL_REGISTRY = "FEDERAL_REGISTRY"
    STATE_REGISTRY = "STATE_REGISTRY"
    COMMERCIAL_REFERENCE = "COMMERCIAL_REFERENCE"
    SUPPLEMENTAL = "SUPPLEMENTAL"
    UNKNOWN = "UNKNOWN"


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
    inferred from an address string."""
    DELIVERED_LOCATION = "DELIVERED_LOCATION"
    PRIMARY_PRACTICE_LOCATION = "PRIMARY_PRACTICE_LOCATION"
    ADDITIONAL_PRACTICE_LOCATION = "ADDITIONAL_PRACTICE_LOCATION"
    MAILING_LOCATION = "MAILING_LOCATION"
    REGISTERED_LOCATION = "REGISTERED_LOCATION"
    MOBILE_FACILITY = "MOBILE_FACILITY"
    UNKNOWN = "UNKNOWN"


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
            "observed_value": dict(self.observed_value),
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


def observations_of(observations: List[EvidenceObservation],
                    kind: ObservationType,
                    *, source_id: Optional[str] = None) -> List[EvidenceObservation]:
    return [o for o in observations
            if o.observation_type is kind and (source_id is None or o.source_id == source_id)]
