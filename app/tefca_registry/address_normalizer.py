"""USPS Publication 28 address normalization — code only, no API, no cost.

Two entities that are the same organization routinely carry addresses that differ
only in formatting: "123 North Main Street, Suite 400" vs "123 N MAIN ST STE 400".
A literal string comparison calls those a mismatch and sends a reviewer chasing a
difference that isn't one.

This module normalizes both sides to USPS Publication 28 form first — standard
suffix abbreviations, directionals, secondary-unit designators, two-letter states,
ZIP+4 — and compares the normalized values. That resolves the overwhelming
majority of formatting-only differences at zero cost and with no network call.

Deliberately NOT a validator: it does not assert an address exists, only that two
renderings mean the same thing. Existence checking is usps_connector's job, and
it is optional.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ── USPS Publication 28 Appendix C1 — street suffix abbreviations ────────────
SUFFIX_MAP: Dict[str, str] = {
    "street": "ST", "st": "ST", "str": "ST",
    "avenue": "AVE", "ave": "AVE", "av": "AVE",
    "boulevard": "BLVD", "blvd": "BLVD", "boul": "BLVD",
    "drive": "DR", "dr": "DR", "driv": "DR",
    "road": "RD", "rd": "RD",
    "lane": "LN", "ln": "LN",
    "court": "CT", "ct": "CT", "crt": "CT",
    "circle": "CIR", "cir": "CIR", "circl": "CIR",
    "place": "PL", "pl": "PL",
    "highway": "HWY", "hwy": "HWY", "highwy": "HWY",
    "parkway": "PKWY", "pkwy": "PKWY", "parkwy": "PKWY",
    "plaza": "PLZ", "plz": "PLZ",
    "square": "SQ", "sq": "SQ",
    "terrace": "TER", "ter": "TER", "terr": "TER",
    "trail": "TRL", "trl": "TRL",
    "way": "WAY",
    "expressway": "EXPY", "expy": "EXPY",
    "freeway": "FWY", "fwy": "FWY",
    "junction": "JCT", "jct": "JCT",
    "turnpike": "TPKE", "tpke": "TPKE",
    "center": "CTR", "centre": "CTR", "ctr": "CTR",
    "crossing": "XING", "xing": "XING",
    "extension": "EXT", "ext": "EXT",
    "loop": "LOOP",
    "path": "PATH",
    "pike": "PIKE",
    "point": "PT", "pt": "PT",
    "ridge": "RDG", "rdg": "RDG",
    "run": "RUN",
    "trace": "TRCE", "trce": "TRCE",
}

# ── USPS Publication 28 Appendix C2 — secondary unit designators ─────────────
UNIT_MAP: Dict[str, str] = {
    "suite": "STE", "ste": "STE",
    "apartment": "APT", "apt": "APT",
    "floor": "FL", "fl": "FL", "flr": "FL",
    "building": "BLDG", "bldg": "BLDG",
    "unit": "UNIT",
    "room": "RM", "rm": "RM",
    "department": "DEPT", "dept": "DEPT",
    "space": "SPC", "spc": "SPC",
    "stop": "STOP",
    "trailer": "TRLR", "trlr": "TRLR",
    "hangar": "HNGR", "hngr": "HNGR",
    "lot": "LOT",
    "pier": "PIER",
    "slip": "SLIP",
    "basement": "BSMT", "bsmt": "BSMT",
    "lobby": "LBBY", "lbby": "LBBY",
    "penthouse": "PH", "ph": "PH",
    "office": "OFC", "ofc": "OFC",
}

DIRECTIONAL_MAP: Dict[str, str] = {
    "north": "N", "n": "N",
    "south": "S", "s": "S",
    "east": "E", "e": "E",
    "west": "W", "w": "W",
    "northeast": "NE", "ne": "NE",
    "northwest": "NW", "nw": "NW",
    "southeast": "SE", "se": "SE",
    "southwest": "SW", "sw": "SW",
}

STATE_MAP: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC",
    # Territories that appear in NPPES data.
    "puerto rico": "PR", "guam": "GU", "virgin islands": "VI",
    "american samoa": "AS", "northern mariana islands": "MP",
}

_VALID_STATE_ABBRS = set(STATE_MAP.values())

_PUNCT = re.compile(r"[.,#]+")
_WS = re.compile(r"\s+")
_ZIP = re.compile(r"\b(\d{5})(?:-?(\d{4}))?\b")


@dataclass
class AddressMatch:
    """Result of comparing two addresses."""
    is_match: bool
    confidence: float                  # 0.0 - 1.0
    normalized_a: str = ""
    normalized_b: str = ""
    differences: List[str] = field(default_factory=list)
    method: str = "usps_normalization"


class USPSNormalizer:
    """Normalize US addresses to USPS Publication 28 form.

    $0, instant, no API key, no network. Deterministic — the same input always
    produces the same output, which matters because these results feed a
    compliance audit trail.
    """

    def normalize(self, address: Optional[str]) -> str:
        """Return the USPS-standardized form of ``address`` (uppercase)."""
        if not address:
            return ""
        text = _PUNCT.sub(" ", str(address))
        text = _WS.sub(" ", text).strip().lower()
        if not text:
            return ""

        # ZIP+4 is normalized to a hyphenated form before tokenizing so the digits
        # don't get treated as separate tokens.
        text = _ZIP.sub(lambda m: f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1), text)

        # Multi-word states must be collapsed before token mapping, or "new york"
        # tokenizes to "new" + "york" and never matches STATE_MAP.
        for full, abbr in STATE_MAP.items():
            if " " in full and full in text:
                text = text.replace(full, abbr.lower())

        out: List[str] = []
        for token in text.split(" "):
            if not token:
                continue
            if token in DIRECTIONAL_MAP:
                out.append(DIRECTIONAL_MAP[token])
            elif token in SUFFIX_MAP:
                out.append(SUFFIX_MAP[token])
            elif token in UNIT_MAP:
                out.append(UNIT_MAP[token])
            elif token in STATE_MAP:
                out.append(STATE_MAP[token])
            else:
                out.append(token.upper())
        return " ".join(out)

    def extract_zip(self, address: Optional[str]) -> str:
        """Return the 5-digit ZIP, or "" when absent. ZIP+4 is truncated to 5."""
        if not address:
            return ""
        m = _ZIP.search(str(address))
        return m.group(1) if m else ""

    def extract_state(self, address: Optional[str]) -> str:
        """Return the two-letter state code, or "" when not determinable."""
        norm = self.normalize(address)
        for token in reversed(norm.split(" ")):
            if token in _VALID_STATE_ABBRS:
                return token
        return ""

    def compare(self, addr1: Optional[str], addr2: Optional[str]) -> AddressMatch:
        """Compare two addresses after normalization.

        Confidence is deliberately coarse — this is a formatting comparison, not a
        probabilistic model, and a fabricated-looking score (0.87) would imply a
        rigor the method does not have.
        """
        n1, n2 = self.normalize(addr1), self.normalize(addr2)

        if not n1 or not n2:
            return AddressMatch(False, 0.0, n1, n2,
                                ["one or both addresses are empty"])

        if n1 == n2:
            return AddressMatch(True, 1.0, n1, n2)

        t1, t2 = set(n1.split(" ")), set(n2.split(" "))
        overlap = len(t1 & t2) / max(len(t1 | t2), 1)

        diffs: List[str] = []
        z1, z2 = self.extract_zip(addr1), self.extract_zip(addr2)
        if z1 and z2 and z1 != z2:
            diffs.append(f"ZIP differs: {z1} vs {z2}")
        s1, s2 = self.extract_state(addr1), self.extract_state(addr2)
        if s1 and s2 and s1 != s2:
            diffs.append(f"state differs: {s1} vs {s2}")
        if not diffs:
            diffs.append(f"token overlap {overlap:.0%}")

        # A differing ZIP or state is disqualifying regardless of token overlap:
        # two suites in the same building share almost every token, and so do two
        # branches of one chain in different cities.
        if z1 and z2 and z1 != z2:
            return AddressMatch(False, 0.0, n1, n2, diffs)
        if s1 and s2 and s1 != s2:
            return AddressMatch(False, 0.0, n1, n2, diffs)

        return AddressMatch(overlap >= 0.85, round(overlap, 4), n1, n2, diffs)
