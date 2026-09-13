"""Deterministic normalisers for names and postal addresses.

Normalisation removes FORMATTING differences only (case, punctuation, common
suffix spellings, street-type abbreviations). It never decides that two
different organisations are the same: "ABC Healthcare LLC" and "ABC Mobile
Clinic" stay different after normalisation, and only an explicit source
statement (an Other Name record) can relate them.

RAW VALUE != COMPARISON KEY
    A source field is coerced to text only for the comparison key. The raw
    value the source stated is kept beside it by the caller
    (`location_observation()` stores `raw_address`). When a value cannot be
    turned into an unambiguous key (an integer ZIP that may have lost a
    leading zero, a non-US postal format, a dict where a string was expected)
    the key is EMPTY and the reason is recorded; nothing is invented and the
    field simply does not compare. Normalisation is never an evidence judgment.

Core-owned so the comparison engine does not depend on a program module.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional, Tuple

#: Bump when any normalisation rule changes. Every normalized address key
#: carries it so a comparison can be re-derived and a rule change is visible.
#: 1.2: non-string fields are coerced or marked not comparable instead of
#: raising; ambiguous integer ZIPs and non-US postal codes yield no ZIP key;
#: Unicode compatibility forms (fullwidth digits) and zero-width characters
#: are folded before digit extraction.
NORMALIZATION_VERSION = "addr-norm-1.2"

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_ASCII_LETTER = re.compile(r"[A-Za-z]")
_US_COUNTRY = frozenset({"", "US", "USA", "UNITED STATES"})

#: Why a postal code did or did not yield a comparison key.
ZIP_OK = "OK"
ZIP_NOT_STATED = "NOT_STATED"
ZIP_UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
ZIP_AMBIGUOUS_LEADING_ZERO = "AMBIGUOUS_LEADING_ZERO"
ZIP_NON_US_FORMAT = "NON_US_FORMAT"
ZIP_NO_DIGITS = "NO_DIGITS"
ZIP_INCOMPLETE = "INCOMPLETE"

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


def as_text(value: Any) -> str:
    """Coerce a source field to text for KEY DERIVATION ONLY, without inventing
    content. str: Unicode compatibility-folded (fullwidth digits become ASCII)
    with zero-width characters removed. int (not bool): its decimal digits.
    Anything else (None, bool, float, bytes, dict, list, ...) is treated as
    not stated and yields "". The raw value is never modified by this function."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, int):
        text = str(value)
    else:
        return ""
    return _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))


def normalize_name(value: Any) -> str:
    """Casefold, strip punctuation, collapse whitespace, canonicalise suffixes."""
    text = as_text(value)
    if not text:
        return ""
    # "L.L.C." -> "llc": dots inside abbreviations are formatting, not words.
    text = text.casefold().replace(".", "")
    text = _WS.sub(" ", _PUNCT.sub(" ", text)).strip()
    words = text.split(" ")
    if words and words[-1] in _ORG_SUFFIXES:
        words[-1] = _ORG_SUFFIXES[words[-1]]
    return " ".join(w for w in words if w)


def name_core(value: Any) -> str:
    """The name with a trailing organisation suffix removed, for AMBIGUOUS
    detection only (never for a match)."""
    words = normalize_name(value).split(" ")
    if words and words[-1] in set(_ORG_SUFFIXES.values()):
        words = words[:-1]
    return " ".join(words)


def normalize_line(value: Any) -> str:
    text = as_text(value)
    if not text:
        return ""
    text = _WS.sub(" ", _PUNCT.sub(" ", text.casefold())).strip()
    return " ".join(_STREET_TYPES.get(w, w) for w in text.split(" ") if w)


def normalize_state(value: Any) -> str:
    """Two-letter state key; digits or other junk yield no key rather than a
    misleading one."""
    text = as_text(value).strip().upper()[:2]
    return text if text.isalpha() else ""


def normalize_country(value: Any) -> str:
    return as_text(value).strip().upper()


def postal_comparison(value: Any, country_code: Any = None) -> Tuple[str, str]:
    """Derive the five-digit ZIP comparison key and say why when it cannot be.

    * str: digits are extracted after Unicode folding; ZIP+4 in any spelling
      ("21201-1234", "212011234") keys to the first five digits. A code that
      contains letters is a non-US format and yields no ZIP key (the raw value
      stays with the observation); fewer than five digits is INCOMPLETE.
    * int: only an exact five- or nine-digit integer is unambiguous. A shorter
      integer may have lost a leading zero (02101 -> 2101) and is NOT repaired:
      no key, AMBIGUOUS_LEADING_ZERO.
    * any other type: UNSUPPORTED_TYPE, no key.
    """
    country = normalize_country(country_code)
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", ZIP_NOT_STATED
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return "", ZIP_UNSUPPORTED_TYPE
    if country not in _US_COUNTRY:
        return "", ZIP_NON_US_FORMAT
    if isinstance(value, int):
        digits = str(value)
        if len(digits) in (5, 9):
            return digits[:5], ZIP_OK
        if len(digits) < 5:
            return "", ZIP_AMBIGUOUS_LEADING_ZERO
        return "", ZIP_INCOMPLETE
    text = as_text(value)
    if _ASCII_LETTER.search(text):
        return "", ZIP_NON_US_FORMAT
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return "", ZIP_NO_DIGITS
    if len(digits) < 5:
        return "", ZIP_INCOMPLETE
    return digits[:5], ZIP_OK


def normalize_zip5(value: Any, country_code: Any = None) -> str:
    return postal_comparison(value, country_code)[0]


def normalize_address(address: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Keys: line1, line2, street, city, state, zip5, zip_status, country,
    normalization_version. Missing or non-comparable parts are empty strings.
    Never raises for any field type; the raw address is the caller's to keep."""
    address = address if isinstance(address, dict) else {}
    line1 = normalize_line(address.get("line1"))
    line2 = normalize_line(address.get("line2"))
    country = normalize_country(address.get("country_code"))
    postal = address.get("postal_code")
    if postal is None or (isinstance(postal, str) and not postal.strip()):
        postal = address.get("zip")
    zip5, zip_status = postal_comparison(postal, country)
    return {
        "line1": line1,
        "line2": line2,
        # Sources split "100 Main St, Ste 200" differently; the street key joins
        # both lines so a suite on line 2 equals a suite inside line 1.
        "street": (line1 + " " + line2).strip(),
        "city": normalize_line(address.get("city")),
        "state": normalize_state(address.get("state")),
        "zip5": zip5,
        "zip_status": zip_status,
        "country": country,
        "normalization_version": NORMALIZATION_VERSION,
    }


def address_is_usable(normalized: Dict[str, str]) -> bool:
    """Enough to compare: a first line plus either city+state or zip5."""
    return bool(normalized.get("line1")) and (
        bool(normalized.get("zip5")) or (bool(normalized.get("city")) and bool(normalized.get("state"))))
