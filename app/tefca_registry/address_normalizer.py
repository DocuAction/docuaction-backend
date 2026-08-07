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

    # ── Address parsing for the USPS v3 API ──────────────────────────────────

    def parse_line(self, address: Optional[str]) -> Dict[str, str]:
        """Split a one-line address into the components USPS v3 expects.

        Registry and NPPES addresses arrive as single strings, and the API takes
        streetAddress / city / state / ZIPCode separately. This is a heuristic,
        and it is the weakest link in the USPS path: it assumes the conventional
        "street, city, ST ZIP" ordering with the state immediately before the
        ZIP.

        When it cannot find a state or ZIP it returns what it did find rather
        than guessing. A wrong guess would be sent to USPS as fact and could come
        back "corrected" into a different real address, which is worse than not
        asking. Layer 1 has already run by this point, so returning little here
        costs a comparison we could not make, not a wrong answer.
        """
        raw = str(address or "").strip()
        if not raw:
            return {"street": "", "city": "", "state": "", "zip5": ""}

        zip5 = self.extract_zip(raw)
        state = self.extract_state(raw)

        # Prefer the comma structure when it is present — it is the only signal
        # that separates a city from the tail of a street name ("Kansas City").
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        street, city = "", ""
        if len(parts) >= 2:
            street = parts[0]
            # The last part usually holds "ST ZIP"; the city is the part before.
            city = parts[-2] if len(parts) >= 3 else ""
            if not city:
                tail_tokens = self.normalize(parts[-1]).split(" ")
                leading = [t for t in tail_tokens
                           if t not in _VALID_STATE_ABBRS and not t[:5].isdigit()]
                city = " ".join(leading)
            if len(parts) >= 3:
                street = " ".join(parts[:-2])
        else:
            # No commas: strip the trailing state and ZIP and treat the rest as
            # street. City is unrecoverable without guessing, so it stays empty.
            tokens = raw.split()
            while tokens and (self.extract_zip(tokens[-1])
                              or tokens[-1].upper() in _VALID_STATE_ABBRS):
                tokens.pop()
            street = " ".join(tokens)

        return {"street": street.strip(), "city": city.strip(),
                "state": state, "zip5": zip5}


# ── Layer 2/3: USPS-assisted comparison ──────────────────────────────────────

class ThreeLayerAddressNormalizer:
    """Code normalization first, USPS only when code cannot decide.

    Layer 1  code normalization    always, $0, instant
    Layer 2  USPS APIs v3          only when layer 1 is inconclusive
    Layer 3  code-only result      when USPS is unconfigured or circuit-open

    The ordering is a cost control, not a preference. Layer 1 already resolves
    the overwhelming majority of real differences — formatting, abbreviations,
    directionals — and every one of those it settles is a USPS call not made. On
    a 383-entity sample, calling USPS first would mean 766 requests to answer
    questions that a dictionary lookup answers for free.

    Layer 2 is therefore reached only when two addresses disagree after
    normalization, which is exactly the population where USPS adds something:
    real-world aliases, renamed streets, and ZIP+4 agreement that string
    comparison cannot see.
    """

    def __init__(self, normalizer: Optional["USPSNormalizer"] = None, client=None):
        self.normalizer = normalizer or USPSNormalizer()
        self._client = client

    def _usps(self):
        if self._client is not None:
            return self._client
        from app.tefca_registry.usps_client import get_usps_client
        return get_usps_client()

    async def standardize_and_compare(self, submitted: Optional[str],
                                      registry: Optional[str]):
        """Compare two addresses across all three layers.

        Returns `usps_client.AddressMatch` (the Pydantic model), NOT the
        `AddressMatch` dataclass defined above in this module. The two share a
        name and nothing else.
        """
        from app.tefca_registry.usps_client import AddressMatch as USPSAddressMatch

        # ── Layer 1 ──────────────────────────────────────────────────────────
        code = self.normalizer.compare(submitted, registry)
        if code.is_match:
            return USPSAddressMatch(
                match=True, confidence=code.confidence, method="code_normalization",
                submitted_normalized=code.normalized_a,
                registry_normalized=code.normalized_b)

        # Escalate only what is genuinely inconclusive. USPS cannot standardize an
        # address that is not there; and a differing ZIP or state is a conclusive
        # answer from layer 1, not an uncertain one — two suites in one building
        # share almost every token, and so do two branches of a chain in
        # different cities, which is exactly why `compare` disqualifies on those
        # fields. Sending them to USPS spends two calls per entity to be told
        # what a dictionary lookup already established.
        def _decided() -> bool:
            z1 = self.normalizer.extract_zip(submitted)
            z2 = self.normalizer.extract_zip(registry)
            if z1 and z2 and z1 != z2:
                return True
            s1 = self.normalizer.extract_state(submitted)
            s2 = self.normalizer.extract_state(registry)
            return bool(s1 and s2 and s1 != s2)

        if not code.normalized_a or not code.normalized_b or _decided():
            return USPSAddressMatch(
                match=False, confidence=code.confidence, method="code_normalization",
                submitted_normalized=code.normalized_a,
                registry_normalized=code.normalized_b)

        client = self._usps()
        if not getattr(client, "configured", False) or client.circuit.is_open():
            # ── Layer 3 ──────────────────────────────────────────────────────
            return USPSAddressMatch(
                match=False, confidence=code.confidence, method="code_normalization",
                submitted_normalized=code.normalized_a,
                registry_normalized=code.normalized_b)

        # ── Layer 2 ──────────────────────────────────────────────────────────
        a = self.normalizer.parse_line(submitted)
        b = self.normalizer.parse_line(registry)
        res_a = await client.standardize(a["street"], city=a["city"],
                                         state=a["state"], zip5=a["zip5"])
        res_b = await client.standardize(b["street"], city=b["city"],
                                         state=b["state"], zip5=b["zip5"])

        if not (res_a.available and res_b.available):
            return USPSAddressMatch(
                match=False, confidence=code.confidence,
                method="code_normalization_usps_unavailable",
                submitted_normalized=code.normalized_a,
                registry_normalized=code.normalized_b)

        std_a = self._joined(res_a)
        std_b = self._joined(res_b)
        zip4_match = (bool(res_a.zip4) and bool(res_b.zip4)
                      and res_a.zip4 == res_b.zip4)
        dpv = res_a.dpv_confirmed and res_b.dpv_confirmed

        if std_a and std_a == std_b:
            # USPS standardized both to the same address. That is a stronger
            # statement than token overlap, but not a certainty — DPV confirms
            # deliverability, not that these are the same organisation — so the
            # confidence stops short of 1.0 unless DPV agrees on both sides.
            return USPSAddressMatch(
                match=True, confidence=1.0 if dpv else 0.95, method="usps_api",
                usps_zip4_match=zip4_match, dpv_confirmed=dpv,
                submitted_normalized=std_a, registry_normalized=std_b)

        if zip4_match:
            # ZIP+4 is a delivery point, often a single building or floor. Equal
            # ZIP+4 with differing street text is a strong secondary signal, but
            # it is secondary: it is reported as a match at reduced confidence
            # rather than treated as equivalence.
            return USPSAddressMatch(
                match=True, confidence=0.9, method="usps_zip4",
                usps_zip4_match=True, dpv_confirmed=dpv,
                submitted_normalized=std_a, registry_normalized=std_b)

        return USPSAddressMatch(
            match=False, confidence=code.confidence, method="usps_api",
            usps_zip4_match=False, dpv_confirmed=dpv,
            submitted_normalized=std_a, registry_normalized=std_b)

    @staticmethod
    def _joined(result) -> str:
        parts = [result.standardized_street, result.standardized_city,
                 result.standardized_state, result.zip5]
        return " ".join(p.strip().upper() for p in parts if p)
