"""Deterministic normalisers for names and postal addresses.

Normalisation removes FORMATTING differences only (case, punctuation, common
suffix spellings, street-type abbreviations). It never decides that two
different organisations are the same: "ABC Healthcare LLC" and "ABC Mobile
Clinic" stay different after normalisation, and only an explicit source
statement (an Other Name record) can relate them.

Core-owned so the comparison engine does not depend on a program module.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

#: Organisation suffixes and their canonical spellings. Kept short and literal.
_ORG_SUFFIXES = {
    "incorporated": "inc", "inc": "inc", "corporation": "corp", "corp": "corp",
    "company": "co", "co": "co", "limited": "ltd", "ltd": "ltd",
    "llc": "llc", "l l c": "llc", "pllc": "pllc", "llp": "llp", "lp": "lp",
    "pc": "pc", "pa": "pa", "plc": "plc",
}
_STREET_TYPES = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave", "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr", "boulevard": "blvd", "blvd": "blvd", "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct", "place": "pl", "pl": "pl", "parkway": "pkwy", "pkwy": "pkwy",
    "highway": "hwy", "hwy": "hwy", "suite": "ste", "ste": "ste", "floor": "fl", "fl": "fl",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def normalize_name(value: Optional[str]) -> str:
    """Casefold, strip punctuation, collapse whitespace, canonicalise suffixes."""
    if not value:
        return ""
    # "L.L.C." -> "llc": dots inside abbreviations are formatting, not words.
    text = str(value).casefold().replace(".", "")
    text = _WS.sub(" ", _PUNCT.sub(" ", text)).strip()
    words = text.split(" ")
    if words and words[-1] in _ORG_SUFFIXES:
        words[-1] = _ORG_SUFFIXES[words[-1]]
    return " ".join(w for w in words if w)


def name_core(value: Optional[str]) -> str:
    """The name with a trailing organisation suffix removed, for AMBIGUOUS
    detection only (never for a match)."""
    words = normalize_name(value).split(" ")
    if words and words[-1] in set(_ORG_SUFFIXES.values()):
        words = words[:-1]
    return " ".join(words)


def normalize_line(value: Optional[str]) -> str:
    if not value:
        return ""
    text = _WS.sub(" ", _PUNCT.sub(" ", str(value).casefold())).strip()
    return " ".join(_STREET_TYPES.get(w, w) for w in text.split(" ") if w)


def normalize_state(value: Optional[str]) -> str:
    return (value or "").strip().upper()[:2]


def normalize_zip5(value: Optional[str]) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[:5]


def normalize_address(address: Dict[str, Any]) -> Dict[str, str]:
    """Keys: line1, line2, city, state, zip5. Missing parts are empty strings."""
    line1 = normalize_line(address.get("line1"))
    line2 = normalize_line(address.get("line2"))
    return {
        "line1": line1,
        "line2": line2,
        # Sources split "100 Main St, Ste 200" differently; the street key joins
        # both lines so a suite on line 2 equals a suite inside line 1.
        "street": (line1 + " " + line2).strip(),
        "city": normalize_line(address.get("city")),
        "state": normalize_state(address.get("state")),
        "zip5": normalize_zip5(address.get("postal_code") or address.get("zip")),
    }


def address_is_usable(normalized: Dict[str, str]) -> bool:
    """Enough to compare: a first line plus either city+state or zip5."""
    return bool(normalized.get("line1")) and (
        bool(normalized.get("zip5")) or (bool(normalized.get("city")) and bool(normalized.get("state"))))
