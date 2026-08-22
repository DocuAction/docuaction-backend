"""
Address reconciliation across the evidence hierarchy (D4).

THE RULE THAT SHAPES THIS FILE: COMPARE, NEVER OVERWRITE
────────────────────────────────────────────────────────
The ONC/HHS submitted address is the thing under review. It is never replaced,
never "corrected", and never silently normalised away. Each source contributes
its own row — original value as the source gave it, normalised value, match
result, retrieval time, dataset anchor — and the analyst sees all of them.

Hierarchy (spec D4):
    ONC/HHS/RCE supplied  →  NPPES  →  PECOS Practice Location  →  USPS
    →  Official entrant website (supplemental only)

Normalisation reuses `app.tefca_registry.address_normalizer.USPSNormalizer`,
which is already the shipped, tested normaliser for this codebase (USPS
Publication 28, deterministic, no network). Writing a second normaliser would
mean two definitions of "same address" in one compliance product.

A CONFLICT here is REVIEW, not FAIL. Two legitimate addresses for one
organisation is an ordinary fact — a billing office and a clinical site — and
the reviewer is the one who decides what it means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.tefca_registry.address_normalizer import USPSNormalizer

#: Source keys in hierarchy order. Position in this list IS the precedence.
SOURCE_ONC = "ONC_RCE_SUBMITTED"
SOURCE_NPPES = "NPPES"
SOURCE_PECOS_PRACTICE_LOCATION = "CMS_PPEF_PRACTICE_LOCATION"
SOURCE_USPS = "USPS"
SOURCE_WEBSITE = "ENTRANT_WEBSITE"

ADDRESS_HIERARCHY = [
    SOURCE_ONC,
    SOURCE_NPPES,
    SOURCE_PECOS_PRACTICE_LOCATION,
    SOURCE_USPS,
    SOURCE_WEBSITE,
]

#: Sources that are supplemental only — they can corroborate, never contradict
#: an entity into a finding.
SUPPLEMENTAL_ADDRESS_SOURCES = frozenset({SOURCE_WEBSITE})


class AddressComparison:
    """The LAYER 2 address-comparison vocabulary.

    Registered in `app.core.evidence_vocabulary` as Layer 2, and DELIBERATELY NOT
    RENAMED. Three of these five names also exist at Layer 3 (NOT_FOUND,
    UNAVAILABLE, CONFLICT) and two exist nowhere else (MATCH, PARTIAL_MATCH) —
    but 176 MATCH and 76 PARTIAL_MATCH rows are already persisted in the shared
    disposition column, and the terms are clear in their own context. They are
    grandfathered pre-1.0; the contract check fails on NEW collisions, not these.
    """

    VOCABULARY_LAYER = "LAYER_2"

    MATCH = "MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"


_normalizer = USPSNormalizer()


def flatten_address(addr: Optional[Dict[str, Any]]) -> str:
    """FHIR-ish address dict → one comparable line. Empty stays empty."""
    if not addr:
        return ""
    if isinstance(addr, str):
        return addr.strip()
    lines = addr.get("line") or []
    if isinstance(lines, str):
        lines = [lines]
    parts = [
        " ".join(str(x) for x in lines if x),
        addr.get("city") or "",
        addr.get("state") or "",
        addr.get("postalCode") or addr.get("zip") or addr.get("postal_code") or "",
    ]
    return " ".join(p for p in parts if p).strip()


def _components(addr: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    if not addr or isinstance(addr, str):
        return {"city": None, "state": None, "postal_code": None}
    return {
        "city": (addr.get("city") or None),
        "state": (addr.get("state") or None),
        "postal_code": (addr.get("postalCode") or addr.get("zip") or addr.get("postal_code") or None),
    }


@dataclass
class AddressSourceRow:
    """One source's address, preserved exactly as required by the spec."""

    source: str
    original_value: Optional[str]
    normalized_value: Optional[str]
    comparison: str
    retrieved_at: str
    dataset_anchor: Optional[str] = None
    query_timestamp: Optional[str] = None
    supplemental: bool = False
    differences: List[str] = field(default_factory=list)
    components: Dict[str, Optional[str]] = field(default_factory=dict)
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "original_value": self.original_value,
            "normalized_value": self.normalized_value,
            "comparison": self.comparison,
            "retrieved_at": self.retrieved_at,
            "dataset_anchor": self.dataset_anchor,
            "query_timestamp": self.query_timestamp,
            "supplemental": self.supplemental,
            "differences": list(self.differences),
            "components": dict(self.components),
            "note": self.note,
        }


_ZIP_PLUS_FOUR = re.compile(r"\b(\d{5})-\d{4}\b")


def _comparable(line: str) -> str:
    """Canonical form used ONLY for comparison — ZIP+4 collapsed to ZIP5.

    The shared USPSNormalizer tokenises "21201-0000" and "21201" as different
    tokens, so an address that differs only in its +4 route code scored as a
    non-match. Two addresses differing only in +4 are the same address for
    verification purposes: +4 is a delivery-route refinement, not a different
    place.

    Done here rather than inside USPSNormalizer on purpose. That normaliser is
    shared with the TEFCA registry and has its own behavioural tests; widening
    what it calls equal would change results for callers that never asked for
    it. The original and USPS-normalised values are still stored in full — only
    the comparison uses this canonical form.
    """
    return _ZIP_PLUS_FOUR.sub(r"\1", line or "")


def compare_to_submitted(
    submitted: Optional[Dict[str, Any]],
    candidate: Optional[Dict[str, Any]],
) -> tuple[str, List[str]]:
    """Compare one candidate address against the ONC-submitted one.

    PARTIAL_MATCH is a real, separate outcome and not a rounding of either
    neighbour: same city/state/ZIP with a differing street line is a suite
    number or a renamed street far more often than it is a different
    organisation, and calling that CONFLICT would bury real conflicts in noise.
    """
    sub_line = flatten_address(submitted)
    cand_line = flatten_address(candidate)
    if not cand_line:
        return AddressComparison.NOT_FOUND, []
    if not sub_line:
        return AddressComparison.NOT_FOUND, ["no ONC-submitted address to compare against"]

    match = _normalizer.compare(_comparable(sub_line), _comparable(cand_line))
    if match.is_match:
        return AddressComparison.MATCH, list(match.differences or [])

    sub_c, cand_c = _components(submitted), _components(candidate)

    def eq(a: Optional[str], b: Optional[str]) -> bool:
        return bool(a) and bool(b) and str(a).strip().upper() == str(b).strip().upper()

    def zip5(v: Optional[str]) -> Optional[str]:
        return str(v).strip()[:5] if v else None

    same_state = eq(sub_c["state"], cand_c["state"])
    same_city = eq(sub_c["city"], cand_c["city"])
    same_zip = eq(zip5(sub_c["postal_code"]), zip5(cand_c["postal_code"]))

    differences = list(match.differences or [])
    if same_state and (same_city or same_zip):
        return AddressComparison.PARTIAL_MATCH, differences
    return AddressComparison.CONFLICT, differences


def build_address_rows(
    submitted: Optional[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> List[AddressSourceRow]:
    """Assemble the per-source address evidence table.

    `candidates` entries: {source, address|None, unavailable: bool,
                           dataset_anchor, query_timestamp, note}
    The submitted address is always row one, always unmodified.
    """
    now = datetime.utcnow().isoformat()
    sub_line = flatten_address(submitted)
    rows: List[AddressSourceRow] = [
        AddressSourceRow(
            source=SOURCE_ONC,
            original_value=sub_line or None,
            normalized_value=_normalizer.normalize(sub_line) or None if sub_line else None,
            comparison=AddressComparison.MATCH if sub_line else AddressComparison.NOT_FOUND,
            retrieved_at=now,
            components=_components(submitted),
            note="ONC/HHS submitted value — the address under review. Never replaced.",
        )
    ]

    for cand in candidates:
        source = cand.get("source", "UNKNOWN")
        if cand.get("unavailable"):
            rows.append(AddressSourceRow(
                source=source,
                original_value=None,
                normalized_value=None,
                comparison=AddressComparison.UNAVAILABLE,
                retrieved_at=now,
                dataset_anchor=cand.get("dataset_anchor"),
                query_timestamp=cand.get("query_timestamp"),
                supplemental=source in SUPPLEMENTAL_ADDRESS_SOURCES,
                note=cand.get("note") or "Source did not answer. Not a finding against the entity.",
            ))
            continue
        addr = cand.get("address")
        line = flatten_address(addr)
        comparison, differences = compare_to_submitted(submitted, addr)
        rows.append(AddressSourceRow(
            source=source,
            original_value=line or None,
            normalized_value=_normalizer.normalize(line) or None if line else None,
            comparison=comparison,
            retrieved_at=now,
            dataset_anchor=cand.get("dataset_anchor"),
            query_timestamp=cand.get("query_timestamp"),
            supplemental=source in SUPPLEMENTAL_ADDRESS_SOURCES,
            differences=differences,
            components=_components(addr),
            note=cand.get("note"),
        ))
    return rows


def reconcile(rows: List[AddressSourceRow]) -> Dict[str, Any]:
    """Roll per-source rows up into one D4 comparison result.

    Supplemental sources are excluded from the roll-up entirely: a website that
    disagrees cannot move the dimension. It is shown, and it is not counted.
    """
    authoritative = [r for r in rows if r.source != SOURCE_ONC and not r.supplemental]
    if not authoritative:
        return {
            "result": AddressComparison.NOT_FOUND,
            "rationale": "No authoritative address source returned a value to compare.",
            "rows": [r.to_dict() for r in rows],
        }

    comparisons = [r.comparison for r in authoritative]
    if any(c == AddressComparison.CONFLICT for c in comparisons):
        result = AddressComparison.CONFLICT
        rationale = ("At least one authoritative source reports a materially different "
                     "address. Presented for analyst review — a second legitimate "
                     "location is not a finding on its own.")
    elif any(c == AddressComparison.PARTIAL_MATCH for c in comparisons):
        result = AddressComparison.PARTIAL_MATCH
        rationale = "Same city/state/ZIP with a differing street line in at least one source."
    elif any(c == AddressComparison.MATCH for c in comparisons):
        result = AddressComparison.MATCH
        rationale = "The submitted address matches at least one authoritative source after normalisation."
    elif all(c == AddressComparison.UNAVAILABLE for c in comparisons):
        result = AddressComparison.UNAVAILABLE
        rationale = "Every authoritative address source was unavailable. Not a finding."
    else:
        result = AddressComparison.NOT_FOUND
        rationale = "No authoritative source held an address for this entity."

    return {
        "result": result,
        "rationale": rationale,
        "rows": [r.to_dict() for r in rows],
        "hierarchy": ADDRESS_HIERARCHY,
    }
