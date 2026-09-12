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

from .observations import EvidenceObservation, Provenance


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


@dataclass(frozen=True)
class StateRegistryCapability:
    """Optional capabilities a jurisdiction MAY publish. None is assumed."""
    jurisdiction: str
    legal_name: bool = False
    trade_name: bool = False
    entity_identifier: bool = False
    entity_status: bool = False
    formation_date: bool = False
    registered_address: bool = False
    source_reference: bool = False
    source_timestamp: bool = False


@runtime_checkable
class StateBusinessRegistryConnector(Protocol):
    """Design-only contract. No implementation exists in this sprint."""

    def capability(self) -> StateRegistryCapability: ...

    def observations_for(self, *, canonical_entity_id: Optional[str], legal_name: str,
                         jurisdiction: str, provenance: Provenance) -> List[EvidenceObservation]: ...
