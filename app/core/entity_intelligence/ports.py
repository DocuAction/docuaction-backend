"""Ports a source adapter or a program implements. Core defines the shape;
adapters (app/evidence_sources/*) and programs supply the behaviour.

Nothing here names a real source. `StateBusinessRegistryConnector` is a
capability contract only — no jurisdiction is implemented and none is assumed
to publish every field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .observations import EvidenceObservation, Provenance, ValueHandling


class DataRightsClass(str, Enum):
    PUBLIC = "PUBLIC"
    GOVERNMENT_PROVIDED = "GOVERNMENT_PROVIDED"
    COMMERCIAL_LICENSED = "COMMERCIAL_LICENSED"
    RESTRICTED = "RESTRICTED"
    TRANSIENT_PROCESSING_ONLY = "TRANSIENT_PROCESSING_ONLY"


class RightsStatus(str, Enum):
    REVIEWED = "REVIEWED"                         # a named human reviewed the terms and recorded the decision
    ASSUMED_PUBLIC_DOMAIN = "ASSUMED_PUBLIC_DOMAIN"  # public by all appearances; NOT the same as REVIEWED
    TERMS_REVIEW_REQUIRED = "TERMS_REVIEW_REQUIRED"
    PENDING = "PENDING"                           # review requested, decision not made
    NOT_PERMITTED = "NOT_PERMITTED"
    UNKNOWN = "UNKNOWN"                           # UNKNOWN != PERMITTED
    # foundation-era names, kept so stored values still read; new code uses the six above
    DOCUMENTED = "DOCUMENTED"
    NOT_YET_APPROVED = "NOT_YET_APPROVED"
    AWAITING_DELIVERY_TERMS = "AWAITING_DELIVERY_TERMS"


#: Identities that can never authorise a right. RESEARCHED != AUTHORIZED.
NON_AUTHORIZING_REVIEWERS = ("system", "ai", "fable", "claude", "docuaction", "automation", "bot", "agent")


class AcquisitionMode(str, Enum):
    OFFICIAL_API = "OFFICIAL_API"
    OFFICIAL_BULK_DATA = "OFFICIAL_BULK_DATA"
    PERMITTED_OFFICIAL_SEARCH = "PERMITTED_OFFICIAL_SEARCH"
    OFFICIAL_DOCUMENT_RETRIEVAL = "OFFICIAL_DOCUMENT_RETRIEVAL"
    PAID_OFFICIAL_SERVICE = "PAID_OFFICIAL_SERVICE"
    CONTROLLED_MANUAL_VERIFICATION = "CONTROLLED_MANUAL_VERIFICATION"
    LICENSED_COMMERCIAL_SOURCE = "LICENSED_COMMERCIAL_SOURCE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class DataRights:
    """What a source's terms allow. Capabilities of the registry, not legal
    conclusions: every field is set from a reviewed term or left at its
    most restrictive default."""
    rights_class: DataRightsClass
    status: RightsStatus
    storage_allowed: bool = False
    raw_storage_allowed: bool = False
    display_allowed: bool = False
    redistribution_allowed: bool = False
    external_call_allowed: bool = False
    credential_required: bool = False
    attribution_required: bool = False
    retention_rule: Optional[str] = None
    value_handling: ValueHandling = ValueHandling.TRANSIENT_ONLY
    basis: Optional[str] = None                   # the document/term relied on
    # provenance of the rights decision itself
    official_reference: Optional[str] = None      # the terms/notice relied on
    reference_version: Optional[str] = None       # version or date of that reference
    review_date: Optional[str] = None
    reviewed_by: Optional[str] = None             # an authorized AGT human or designated legal/compliance authority
    snapshot_retention_allowed: bool = False
    historical_comparison_allowed: bool = False
    derived_observation_permission: bool = False
    client_display_allowed: bool = False
    limitations: Optional[str] = None

    def __post_init__(self) -> None:
        """Fable/System/AI may not self-authorize. Any of the three retention
        or redistribution rights requires a named human reviewer."""
        if self.snapshot_retention_allowed or self.historical_comparison_allowed or self.redistribution_allowed:
            who = (self.reviewed_by or "").strip()
            if not who or any(tag in who.lower() for tag in NON_AUTHORIZING_REVIEWERS):
                raise ValueError("snapshot retention, historical comparison and redistribution rights require "
                                 "reviewed_by to name an authorized AGT human or designated legal/compliance "
                                 f"authority (got {self.reviewed_by!r})")
            if self.status is not RightsStatus.REVIEWED:
                raise ValueError("those rights can only be granted with status REVIEWED")

    def to_dict(self) -> Dict[str, Any]:
        return {"rights_class": self.rights_class.value, "status": self.status.value,
                "storage_allowed": self.storage_allowed, "raw_storage_allowed": self.raw_storage_allowed,
                "display_allowed": self.display_allowed, "redistribution_allowed": self.redistribution_allowed,
                "external_call_allowed": self.external_call_allowed,
                "credential_required": self.credential_required,
                "attribution_required": self.attribution_required, "retention_rule": self.retention_rule,
                "value_handling": self.value_handling.value, "basis": self.basis,
                "official_reference": self.official_reference, "reference_version": self.reference_version,
                "review_date": self.review_date, "reviewed_by": self.reviewed_by,
                "snapshot_retention_allowed": self.snapshot_retention_allowed,
                "historical_comparison_allowed": self.historical_comparison_allowed,
                "derived_observation_permission": self.derived_observation_permission,
                "client_display_allowed": self.client_display_allowed, "limitations": self.limitations}


class AdapterStatus(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"
    AWAITING_SCHEMA = "AWAITING_SCHEMA"     # the delivering party's file layout is not yet known
    RESEARCH_ONLY = "RESEARCH_ONLY"         # documented; no code path may call out
    DESIGN_ONLY = "DESIGN_ONLY"


@dataclass(frozen=True)
class AdapterDescriptor:
    source_id: str
    source_owner: str
    status: AdapterStatus
    feature_flag: Optional[str]            # the subordinate flag that gates it
    description: str = ""
    makes_external_calls: bool = False
    requires_credential: bool = False
    data_rights: Optional[DataRights] = None


@runtime_checkable
class EvidenceSourceAdapter(Protocol):
    """Turns a preserved delivery (file bytes / parsed rows) into observations.
    Adapters never call out unless their descriptor says so AND their flag is
    on; the isolation tests assert both."""

    def describe(self) -> AdapterDescriptor: ...

    def observations_for(self, *, canonical_entity_id: Optional[str], identifier: str,
                         provenance: Provenance) -> List[EvidenceObservation]: ...


@dataclass(frozen=True)
class SchemaInventory:
    """What a delivered file actually contains, before anyone maps it."""
    source_id: str
    schema_fingerprint: str
    fields: List[str]
    record_count: int
    sample_values: Dict[str, List[str]] = field(default_factory=dict)
    note: str = ""


class CapabilityAvailability(str, Enum):
    """Per-field, per-jurisdiction answer. Not every state publishes every
    field, and some publish it only to a person at a counter."""
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_AVAILABLE = "NOT_AVAILABLE"        # exists but not obtainable now
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    MANUAL_ONLY = "MANUAL_ONLY"


_NS = CapabilityAvailability.NOT_SUPPORTED


@dataclass(frozen=True)
class StateRegistryCapability:
    """Optional capabilities a jurisdiction MAY publish. Nothing is assumed;
    every field defaults to NOT_SUPPORTED until a connector says otherwise."""
    jurisdiction: str
    legal_name: CapabilityAvailability = _NS
    trade_name: CapabilityAvailability = _NS
    entity_identifier: CapabilityAvailability = _NS
    entity_status: CapabilityAvailability = _NS
    formation_date: CapabilityAvailability = _NS
    registered_address: CapabilityAvailability = _NS
    source_reference: CapabilityAvailability = _NS
    source_timestamp: CapabilityAvailability = _NS
    acquisition: CapabilityAvailability = _NS      # the connector as a whole
    acquisition_mode: AcquisitionMode = AcquisitionMode.UNSUPPORTED

    def supported_fields(self) -> List[str]:
        return [f for f in ("legal_name", "trade_name", "entity_identifier", "entity_status",
                            "formation_date", "registered_address", "source_reference", "source_timestamp")
                if getattr(self, f) is CapabilityAvailability.SUPPORTED]


@runtime_checkable
class StateBusinessRegistryConnector(Protocol):
    """Design-only contract. No implementation exists in this sprint."""

    def capability(self) -> StateRegistryCapability: ...

    def observations_for(self, *, canonical_entity_id: Optional[str], legal_name: str,
                         jurisdiction: str, provenance: Provenance) -> List[EvidenceObservation]: ...
