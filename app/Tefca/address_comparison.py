"""Compare a delivered RCE address against an authoritative one, reproducibly.

WHY THIS EXISTS AS A MODULE AND NOT A REPORT QUERY
    Phase 6 reported "230 address mismatches". That figure was computed inline
    while writing the report and never persisted, so nobody could reproduce it,
    review it, or disagree with it. A number that cannot be re-derived from
    stored evidence is not a finding — it is an anecdote. Everything here is a
    pure function so the same inputs always yield the same verdict, and the
    verdict is stored.

WHAT COUNTS AS A CONFLICT, AND WHAT DELIBERATELY DOES NOT
    A formatting difference is not a conflict. `123 Main St.` and `123 MAIN
    STREET` are the same address written twice; reporting that as a discrepancy
    against a TEFCA participant would be a false accusation produced by a
    string comparison. Normalisation is applied first and a difference that
    survives it is what gets called a conflict.

    Absent data is not a conflict either. If the authoritative source holds no
    address, that is INSUFFICIENT_DATA — a fact about the source, not a
    disagreement with the entity.

THE PPEF ASYMMETRY, WHICH IS A SOURCE LIMITATION AND NOT A DEFECT
    The PPEF practice-location extract publishes ENRLMT_ID, CITY_NAME, STATE_CD
    and ZIP_CD. It publishes NO street line. A street-level comparison against
    PPEF is therefore impossible, and claiming EXACT_MATCH against PPEF would
    assert agreement on a field the source never supplied. PPEF comparisons are
    scoped to city/state/ZIP and say so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

#: Bump when a normalisation or comparison rule changes.
ADDRESS_RULE_VERSION = "1.0.0"


class AddressResult(str, Enum):
    """Six outcomes. INSUFFICIENT_DATA is never folded into CONFLICT."""

    EXACT_MATCH = "EXACT_MATCH"
    NORMALIZED_MATCH = "NORMALIZED_MATCH"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


#: USPS street-suffix and directional equivalences. Deliberately small: every
#: entry is a documented USPS abbreviation, so expanding one cannot change which
#: place an address refers to. A fuzzy matcher would.
_STREET_WORDS = {
    "STREET": "ST", "ST": "ST", "AVENUE": "AVE", "AVE": "AVE", "ROAD": "RD",
    "RD": "RD", "DRIVE": "DR", "DR": "DR", "BOULEVARD": "BLVD", "BLVD": "BLVD",
    "LANE": "LN", "LN": "LN", "COURT": "CT", "CT": "CT", "PLACE": "PL",
    "PL": "PL", "SUITE": "STE", "STE": "STE", "PARKWAY": "PKWY", "PKWY": "PKWY",
    "HIGHWAY": "HWY", "HWY": "HWY", "CIRCLE": "CIR", "CIR": "CIR",
    "TERRACE": "TER", "TER": "TER", "NORTH": "N", "SOUTH": "S", "EAST": "E",
    "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE",
    "SOUTHWEST": "SW", "FLOOR": "FL", "FL": "FL", "BUILDING": "BLDG",
    "BLDG": "BLDG", "APARTMENT": "APT", "APT": "APT", "UNIT": "UNIT",
}


def norm_line(value: Optional[str]) -> str:
    """Street line reduced to comparable tokens. Punctuation carries no meaning."""
    text = re.sub(r"[^A-Z0-9 ]+", " ", (value or "").upper())
    return " ".join(_STREET_WORDS.get(tok, tok) for tok in text.split())


def norm_text(value: Optional[str]) -> str:
    """City or any free-text field: case and punctuation removed."""
    return " ".join(re.sub(r"[^A-Z0-9 ]+", " ", (value or "").upper()).split())


def norm_state(value: Optional[str]) -> str:
    return (value or "").strip().upper()[:2]


def norm_zip5(value: Optional[str]) -> str:
    """First five digits, zero-padded.

    Zero-padding matters here: 6.9% of delivered ZIPs lost a leading zero to a
    spreadsheet round-trip upstream, and comparing `2718` to `02718` as strings
    would manufacture a conflict out of a formatting artefact that FMT-001
    already documents.
    """
    digits = re.sub(r"\D", "", value or "")
    return digits[:5].zfill(5) if digits else ""


@dataclass
class AddressComparison:
    """One comparison, with the evidence needed to re-run it."""

    result: AddressResult
    #: Fields compared and found equal after normalisation.
    field_matches: List[str] = field(default_factory=list)
    #: Fields compared and found different after normalisation.
    field_conflicts: List[str] = field(default_factory=list)
    #: Fields NOT compared, and why. An uncompared field is never a match.
    fields_not_compared: List[str] = field(default_factory=list)
    normalized_left: Dict[str, str] = field(default_factory=dict)
    normalized_right: Dict[str, str] = field(default_factory=dict)
    note: Optional[str] = None
    rule_version: str = ADDRESS_RULE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {"result": self.result.value, "field_matches": self.field_matches,
                "field_conflicts": self.field_conflicts,
                "fields_not_compared": self.fields_not_compared,
                "normalized_left": self.normalized_left,
                "normalized_right": self.normalized_right,
                "note": self.note, "rule_version": self.rule_version}


def _compare(left: Dict[str, str], right: Dict[str, str],
             comparable: List[str], raw_equal: bool,
             uncompared: List[str], note: Optional[str]) -> AddressComparison:
    matches, conflicts = [], []
    for f in comparable:
        if not left.get(f) or not right.get(f):
            uncompared = uncompared + [f]
            continue
        (matches if left[f] == right[f] else conflicts).append(f)

    if not matches and not conflicts:
        return AddressComparison(
            AddressResult.INSUFFICIENT_DATA, [], [], uncompared, left, right,
            note or "No field was populated on both sides; nothing was compared.")
    if conflicts:
        return AddressComparison(AddressResult.CONFLICT, matches, conflicts,
                                 uncompared, left, right, note)
    if raw_equal:
        return AddressComparison(AddressResult.EXACT_MATCH, matches, [],
                                 uncompared, left, right, note)
    return AddressComparison(AddressResult.NORMALIZED_MATCH, matches, [],
                             uncompared, left, right, note)


def compare_to_nppes(rce: Dict[str, Any],
                     nppes: Optional[Dict[str, Any]]) -> AddressComparison:
    """RCE delivered address against the NPPES practice location.

    NPPES publishes a street line, so all four fields are comparable.
    """
    if nppes is None:
        return AddressComparison(
            AddressResult.SOURCE_UNAVAILABLE, note=(
                "NPPES returned no record for this NPI, so there is no address "
                "to compare. Not a disagreement."))
    left = {"line": norm_line(rce.get("address_line")),
            "city": norm_text(rce.get("address_city")),
            "state": norm_state(rce.get("address_state")),
            "zip5": norm_zip5(rce.get("address_postalCode"))}
    right = {
        "line": norm_line(nppes.get("Provider First Line Business Practice Location Address")),
        "city": norm_text(nppes.get("Provider Business Practice Location Address City Name")),
        "state": norm_state(nppes.get("Provider Business Practice Location Address State Name")),
        # NPPES postal code is absent from the profiled column set; recorded as
        # uncompared rather than silently treated as agreeing.
        "zip5": norm_zip5(nppes.get("Provider Business Practice Location Postal Code"))}
    raw_equal = (
        (rce.get("address_line") or "").strip()
        == (nppes.get("Provider First Line Business Practice Location Address") or "").strip()
        and (rce.get("address_city") or "").strip()
        == (nppes.get("Provider Business Practice Location Address City Name") or "").strip())
    return _compare(left, right, ["line", "city", "state", "zip5"], raw_equal, [], None)


def compare_to_ppef(rce: Dict[str, Any],
                    locations: Optional[List[Dict[str, Any]]]) -> AddressComparison:
    """RCE delivered address against PPEF practice locations.

    ONE MATCHING LOCATION IS A MATCH. A provider may legitimately enrol several
    practice locations; calling the entity mismatched because the third one
    differs would be an artefact of iteration order, not a finding. The best
    result across the published locations is taken, and the count is recorded.
    """
    if not locations:
        return AddressComparison(
            AddressResult.INSUFFICIENT_DATA, note=(
                "No PPEF practice-location row for this enrolment; nothing to "
                "compare against."))
    left = {"city": norm_text(rce.get("address_city")),
            "state": norm_state(rce.get("address_state")),
            "zip5": norm_zip5(rce.get("address_postalCode"))}
    uncompared = ["line"]
    note = (f"PPEF publishes no street line, so the comparison is scoped to "
            f"city/state/ZIP. Best of {len(locations)} published "
            f"practice location(s).")
    best: Optional[AddressComparison] = None
    rank = {AddressResult.EXACT_MATCH: 0, AddressResult.NORMALIZED_MATCH: 1,
            AddressResult.CONFLICT: 2, AddressResult.INSUFFICIENT_DATA: 3,
            AddressResult.NOT_APPLICABLE: 4, AddressResult.SOURCE_UNAVAILABLE: 5}
    for loc in locations:
        right = {"city": norm_text(loc.get("CITY_NAME")),
                 "state": norm_state(loc.get("STATE_CD")),
                 "zip5": norm_zip5(loc.get("ZIP_CD"))}
        # Never EXACT: the street line was never published, so full agreement
        # cannot be asserted. raw_equal is deliberately False.
        cmp_ = _compare(left, right, ["city", "state", "zip5"], False,
                        list(uncompared), note)
        if best is None or rank[cmp_.result] < rank[best.result]:
            best = cmp_
    return best
