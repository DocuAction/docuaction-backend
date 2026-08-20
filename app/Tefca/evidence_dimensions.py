"""
Six evidence dimensions, and the disposition vocabulary they speak.

WHY DIMENSIONS AND NOT SOURCES
──────────────────────────────
The question a reviewer has to answer is not "how many APIs responded", it is
"does each thing we are required to establish have sufficient authoritative
evidence behind it". Those are different questions and they give different
answers: four CMS lookups against one relational dataset are ONE piece of
Medicare evidence, not four independent votes. Organising by dimension makes
that structurally impossible to get wrong — a dimension has one disposition no
matter how many rows, components or pages backed it.

Nothing in this module produces a score, a percentage, or a count of passing
sources. There is deliberately no arithmetic here at all.

DISPOSITIONS
────────────
Five core states for authoritative controls: PASS, FAIL, REVIEW,
NOT_APPLICABLE, UNAVAILABLE. Supplemental evidence (website corroboration, and
the corroborative half of relationship evidence) may additionally use
CORROBORATED, CONFLICT, INSUFFICIENT_EVIDENCE, NOT_FOUND — because forcing
"the website was down" into PASS/FAIL is how an unreachable web server turns
into a compliance finding against an entity that did nothing wrong.

UNAVAILABLE is not FAIL. NOT_APPLICABLE is not PASS. Neither is a silent
success, and both are visible to the analyst as what they are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Dimension(str, Enum):
    """The six verification dimensions. Order is the review reading order."""

    D1_IDENTITY = "IDENTITY"
    D2_MEDICARE_ENROLLMENT = "MEDICARE_ENROLLMENT"
    D3_EXCLUSION_REVOCATION = "EXCLUSION_REVOCATION"
    D4_ADDRESS = "ADDRESS"
    D5_TEFCA_ALIGNMENT = "TEFCA_ALIGNMENT"
    D6_PROVIDER_ORG_RELATIONSHIP = "PROVIDER_ORG_RELATIONSHIP"


DIMENSION_ORDER: List[Dimension] = [
    Dimension.D1_IDENTITY,
    Dimension.D2_MEDICARE_ENROLLMENT,
    Dimension.D3_EXCLUSION_REVOCATION,
    Dimension.D4_ADDRESS,
    Dimension.D5_TEFCA_ALIGNMENT,
    Dimension.D6_PROVIDER_ORG_RELATIONSHIP,
]

DIMENSION_LABELS: Dict[str, str] = {
    Dimension.D1_IDENTITY.value: "Identity",
    Dimension.D2_MEDICARE_ENROLLMENT.value: "Medicare Enrollment",
    Dimension.D3_EXCLUSION_REVOCATION.value: "Exclusion / Debarment / Revocation",
    Dimension.D4_ADDRESS.value: "Address",
    Dimension.D5_TEFCA_ALIGNMENT.value: "TEFCA Alignment",
    Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value: "Provider ↔ Organization Relationship",
}


class Disposition(str, Enum):
    # Core five — authoritative verification controls.
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"
    # Supplemental — corroborative evidence only.
    CORROBORATED = "CORROBORATED"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_FOUND = "NOT_FOUND"


CORE_DISPOSITIONS = frozenset({
    Disposition.PASS, Disposition.FAIL, Disposition.REVIEW,
    Disposition.NOT_APPLICABLE, Disposition.UNAVAILABLE,
})

SUPPLEMENTAL_DISPOSITIONS = frozenset({
    Disposition.CORROBORATED, Disposition.CONFLICT,
    Disposition.INSUFFICIENT_EVIDENCE, Disposition.NOT_FOUND,
})

#: Dispositions that must never be reached automatically. FAIL against a TEFCA
#: entity is an analyst act or an explicit deterministic condition in the
#: approved ARC methodology — never the by-product of a lookup that missed.
NEVER_AUTOMATIC = frozenset({Disposition.FAIL})


class Applicability(str, Enum):
    REQUIRED = "REQUIRED"          # methodology requires this dimension for this entity
    CORROBORATIVE = "CORROBORATIVE"  # useful if present, cannot fail the entity
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class EvidenceItem:
    """One source's contribution to one dimension, with full provenance.

    This is the reproducibility unit. Everything needed to re-run the same
    lookup and get the same answer — or to explain why the answer changed — is
    on this object, including the values as the source gave them.
    """

    dimension: str
    source: str
    disposition: str
    source_dataset: Optional[str] = None
    ppef_component: Optional[str] = None
    source_record_identifier: Optional[str] = None
    query_identifier: Optional[str] = None
    query_timestamp: Optional[str] = None
    dataset_version_anchor: Optional[str] = None
    http_last_modified: Optional[str] = None
    update_cadence: Optional[str] = None
    realtime: bool = False
    record_count: int = 0
    records_truncated: bool = False
    fields_evaluated: List[str] = field(default_factory=list)
    field_matches: List[Dict[str, Any]] = field(default_factory=list)
    field_conflicts: List[Dict[str, Any]] = field(default_factory=list)
    original_values: Dict[str, Any] = field(default_factory=dict)
    normalized_values: Dict[str, Any] = field(default_factory=dict)
    rule_applied: Optional[str] = None
    note: Optional[str] = None
    retrieved_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "source": self.source,
            "disposition": self.disposition,
            "source_dataset": self.source_dataset,
            "ppef_component": self.ppef_component,
            "source_record_identifier": self.source_record_identifier,
            "query_identifier": self.query_identifier,
            "query_timestamp": self.query_timestamp,
            "dataset_version_anchor": self.dataset_version_anchor,
            "http_last_modified": self.http_last_modified,
            "update_cadence": self.update_cadence,
            "realtime": self.realtime,
            "record_count": self.record_count,
            "records_truncated": self.records_truncated,
            "fields_evaluated": list(self.fields_evaluated),
            "field_matches": list(self.field_matches),
            "field_conflicts": list(self.field_conflicts),
            "original_values": dict(self.original_values),
            "normalized_values": dict(self.normalized_values),
            "rule_applied": self.rule_applied,
            "note": self.note,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def from_provenance(cls, dimension: str, source: str, disposition: str,
                        provenance: Optional[Dict[str, Any]], **kwargs) -> "EvidenceItem":
        """Build an item from a connector's `provenance` block (see cms_ppef)."""
        p = provenance or {}
        # Provenance supplies the defaults; an explicit keyword from the caller
        # wins. The caller knows things the provenance block does not — which
        # PPEF component it asked for, for instance, when the lookup itself
        # never got far enough to record one.
        fields: Dict[str, Any] = {
            "source_dataset": p.get("source_dataset"),
            "ppef_component": p.get("ppef_component"),
            "query_identifier": p.get("query_identifier"),
            "query_timestamp": p.get("query_timestamp"),
            "dataset_version_anchor": p.get("dataset_version_anchor"),
            "http_last_modified": p.get("http_last_modified"),
            "update_cadence": p.get("update_cadence"),
            "realtime": bool(p.get("realtime", False)),
            "record_count": int(p.get("row_count") or 0),
            "records_truncated": bool(p.get("records_truncated", False)),
        }
        fields.update(kwargs)
        return cls(dimension=dimension, source=source, disposition=disposition, **fields)


@dataclass
class DimensionResult:
    """The disposition of one dimension, and every item that informed it."""

    dimension: str
    disposition: str
    applicability: str
    rationale: str
    items: List[EvidenceItem] = field(default_factory=list)
    analyst_notes: Optional[str] = None
    #: Set when the disposition depends on something a human must decide.
    requires_analyst: bool = False

    @property
    def label(self) -> str:
        return DIMENSION_LABELS.get(self.dimension, self.dimension)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "label": self.label,
            "disposition": self.disposition,
            "applicability": self.applicability,
            "rationale": self.rationale,
            "requires_analyst": self.requires_analyst,
            "analyst_notes": self.analyst_notes,
            "evidence": [i.to_dict() for i in self.items],
            "sources": sorted({i.source for i in self.items}),
        }


def sufficiency_summary(results: List[DimensionResult]) -> Dict[str, Any]:
    """Whether each REQUIRED dimension has sufficient authoritative evidence.

    Deliberately NOT a score. It answers, per dimension, "is this settled,
    waiting on a human, or unreachable" — and the entity-level readout is the
    list of dimensions that are not settled, not a number. There is no
    weighting, no percentage and no vote counting anywhere in this function,
    because correlated CMS components are not independent evidence and any
    arithmetic over them would imply that they are.
    """
    outstanding = []
    unavailable = []
    for r in results:
        if r.applicability != Applicability.REQUIRED.value:
            continue
        if r.disposition == Disposition.UNAVAILABLE.value:
            unavailable.append(r.dimension)
        elif r.disposition in (Disposition.REVIEW.value, Disposition.INSUFFICIENT_EVIDENCE.value):
            outstanding.append(r.dimension)
    return {
        "required_dimensions": [
            r.dimension for r in results if r.applicability == Applicability.REQUIRED.value
        ],
        "dimensions_awaiting_analyst": outstanding,
        "dimensions_unavailable": unavailable,
        "all_required_dimensions_settled": not outstanding and not unavailable,
        "note": (
            "Sufficiency is per dimension. No score, percentage or source count "
            "is derived from this structure; correlated CMS components are one "
            "body of evidence, not independent votes."
        ),
    }
