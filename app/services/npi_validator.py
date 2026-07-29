"""Canonical NPI validation — CMS check-digit standard.

WHY A SHARED MODULE
    Two implementations of this already existed, in app/Tefca/qa_engine.py and
    app/tefca_registry/seed.py. Two copies of a check-digit algorithm is two
    chances to disagree, and a validator that disagrees with itself is worse than
    none - it makes the same NPI valid in one code path and invalid in another.
    This is the one implementation; the others are verified against it.

THE ALGORITHM (45 CFR 162.406, CMS NPI Check Digit Calculation)
    An NPI is 10 digits: 9 base digits plus a check digit. The check digit is
    computed by prefixing the constant 80840 - the ISO 7812 issuer identifier
    assigned to CMS - to the 9 base digits and applying Luhn to the result.

    The prefix is the part people get wrong. Running Luhn over the bare 10 digits
    validates roughly one NPI in ten by accident, which looks like it works.
"""

from __future__ import annotations

import re

# ISO 7812 issuer identifier for CMS. Not arbitrary and not configurable.
CMS_PREFIX = "80840"

_DIGITS_ONLY = re.compile(r"^\d{10}$")


def _luhn_total(number: str) -> int:
    """Luhn sum. Doubles every second digit from the right, subtracting 9 when
    the doubled value exceeds 9."""
    total = 0
    for i, ch in enumerate(reversed(number)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total


def validate_npi(npi: str) -> tuple[bool, str]:
    """Validate NPI format and Luhn check digit.

    Returns (is_valid, error_message). The message is empty when valid and is
    written to be shown to a user or written to a log as-is.
    """
    if npi is None:
        return False, "NPI is missing"

    value = str(npi).strip()
    if not value:
        return False, "NPI is empty"

    if not _DIGITS_ONLY.match(value):
        if not value.isdigit():
            return False, f"NPI must contain digits only (got {len(value)} characters)"
        return False, f"NPI must be exactly 10 digits (got {len(value)})"

    if _luhn_total(CMS_PREFIX + value) % 10 != 0:
        return False, f"NPI {value} fails Luhn check digit validation"

    return True, ""


def is_valid_npi(npi: str) -> bool:
    """Boolean-only convenience wrapper."""
    return validate_npi(npi)[0]


def compute_check_digit(base9: str) -> str:
    """Return the check digit for a 9-digit base, so callers can build a valid
    synthetic NPI for test fixtures rather than guessing one."""
    base = str(base9).strip()
    if not base.isdigit() or len(base) != 9:
        raise ValueError("base must be exactly 9 digits")
    remainder = _luhn_total(CMS_PREFIX + base + "0") % 10
    return str((10 - remainder) % 10)


def make_valid_npi(base9: str) -> str:
    """A complete, Luhn-valid 10-digit NPI from a 9-digit base."""
    return f"{str(base9).strip()}{compute_check_digit(base9)}"


def validate_for_import(npi: str) -> dict:
    """Validation result shaped for entity import metadata.

    Deliberately does NOT reject. Existing seed and mock data contains NPIs with
    bad check digits, and refusing the import would break a working system to
    enforce a rule those records predate. The entity is flagged for review
    instead, which surfaces the problem without causing an outage.
    """
    ok, message = validate_npi(npi)
    return {
        "npi": (str(npi).strip() if npi is not None else None),
        "npi_valid": ok,
        "npi_validation_error": message or None,
        "requires_review": not ok,
        "validator": "CMS Luhn (80840 prefix)",
    }
