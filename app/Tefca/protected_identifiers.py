"""Protected tax identifiers (EIN / TIN / FEIN): one deterministic boundary.

THE RULE
────────
A taxpayer identifier must not reach an AI service, an ordinary screen, an
ordinary export or a log line. `app/Tefca/identifier_boundary.py` already says
what AGT may CONCLUDE from such an identifier (nothing); this module is about
where the VALUE may travel.

WHAT THIS IS
────────────
Pure functions, no I/O, no model, no network:

  * `is_protected_field(name)`   - is this column / key a tax identifier?
  * `redact_mapping(obj)`        - a copy with every such value replaced
  * `find_in_text(text)`         - does free text carry one?
  * `assert_safe_for_ai(text)`   - fail closed before any AI transport

DETECTION IS BY NAME FIRST, BY SHAPE SECOND, AND THE LIMITS ARE STATED
──────────────────────────────────────────────────────────────────────
A column is protected because of what it is CALLED (`ORG_TAX_ID`, `EIN`,
`Tax Identifier`). A value with no label is only recognised in the written EIN
shape `NN-NNNNNNN`. Nine bare digits with no label are NOT recognised: that
shape is also a ZIP+4 without its hyphen, a routing number and a hundred other
things, and refusing every nine-digit number would make the guard something
people switch off. So this is a guard against the identifier travelling under
its own name or in its own format; it is not proof that a value is absent, and
nothing here says so.

ONE VOCABULARY
──────────────
Which identifiers are restricted is decided in one place,
`identifier_boundary.GOVERNMENT_RESTRICTED`. This module derives its name
tokens from that set and only adds the spellings a column header uses for the
same thing (`tax_id`, `taxpayer id`, ...).

WHO MAY SEE A RAW VALUE is not decided here. Nothing in the application serves
one today, and a permission that nothing enforces would be a claim, not a
control; the proposal is in the pull request, for a policy decision.

WHAT THIS DOES NOT COVER. The check in the TEFCA AI gateway guards that one
transport. Other modules of the platform call AI providers directly and do not
pass through it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List

from app.Tefca.identifier_boundary import GOVERNMENT_RESTRICTED

PROTECTED_IDENTIFIERS_VERSION = "1.0.0"

#: What replaces a protected value. Deliberately not a mask of the value: a
#: partial tax identifier is still part of a tax identifier.
REDACTED = "[PROTECTED TAX IDENTIFIER]"

#: A whole token that names a tax identifier on its own.
_SINGLE_TOKENS = (frozenset(i.lower() for i in GOVERNMENT_RESTRICTED)
                  | frozenset({"itin", "taxid", "taxpayerid", "taxpayer"}))
#: What may follow the token "tax" (or "taxpayer") to make it an identifier.
_ID_SUFFIXES = frozenset({"id", "ids", "identifier", "identification", "ident",
                          "number", "num", "nbr", "no", "nr", "cd", "code"})

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPLIT = re.compile(r"[^A-Za-z0-9]+")

#: A tax-identifier LABEL inside running or serialized text. It may follow a
#: separator (`ORG_TAX_ID`, `"EIN"`, `hco.tin`) or a lower-case letter in
#: camelCase (`orgTaxId`), and must not run on into another word (`routing`,
#: `protein`, `taxonomy` are not labels).
_LABEL = (
    r"(?:(?<![A-Za-z])|(?-i:(?<=[a-z])(?=[A-Z])))"
    r"(?:f?ein|i?tin"
    r"|tax[\s_\-]*payer(?:[\s_\-]*(?:id(?:entif(?:ier|ication))?|number|num|nbr|no))*"
    r"|tax[\s_\-]*(?:id(?:entif(?:ier|ication))?|number|num|nbr|no)"
    r"(?:[\s_\-]*(?:number|num|nbr|no))?"
    r"|employer[\s_\-]*identification(?:[\s_\-]*(?:number|num|nbr|no))?"
    r"|irs[\s_\-]*(?:id|number|num|nbr|no))"
    r"(?![A-Za-z])")
#: What may stand between a label and its value: punctuation of any
#: serialization (quotes, colon, equals, brackets, spaces) and a joining word.
_GAP = r"(?:[^A-Za-z0-9\n]|\b(?:is|no|number|num|value|val)\b){0,16}"
#: Nine digits as an EIN (NN-NNNNNNN, NN NNNNNNN, NNNNNNNNN) or in the
#: three-two-four grouping an ITIN is written in.
_VALUE = r"(?:\d{2}[-\s]?\d{7}|\d{3}-\d{2}-\d{4})(?![\d\-])"
_LABELLED = re.compile(f"({_LABEL}{_GAP})({_VALUE})", re.IGNORECASE)
#: The written EIN shape with no label.
_EIN_SHAPE = re.compile(r"(?<![\d\-])\d{2}-\d{7}(?![\d\-])")


class ProtectedIdentifierEgressRefused(RuntimeError):
    """Text bound for an AI transport carried a tax identifier. Never retried."""


def _tokens(name: Any) -> List[str]:
    text = _CAMEL.sub(" ", str(name or ""))
    return [t.lower() for t in _SPLIT.split(text) if t]


def is_protected_field(name: Any) -> bool:
    """True when a column or key NAME denotes an EIN / TIN / FEIN.

    Token based, so `routing`, `listing` and `taxonomy` are not matches: `tin`
    counts only as a whole token, and `tax` only when an identifier word
    follows it (`ORG_TAX_ID`, `Tax Identifier`, `taxNumber`).
    """
    tokens = _tokens(name)
    for index, token in enumerate(tokens):
        if token in _SINGLE_TOKENS:
            return True
        if token in ("tax", "taxpayer", "irs") and index + 1 < len(tokens) \
                and tokens[index + 1] in _ID_SUFFIXES:
            return True
        if token == "employer" and tokens[index + 1:index + 2] == ["identification"]:
            return True
    return False


def protected_fields(names: Iterable[Any]) -> List[str]:
    """The names, in their given order and spelling, that are protected."""
    return [str(n) for n in names if is_protected_field(n)]


def redact_mapping(obj: Any) -> Any:
    """A deep copy of `obj` with every protected value replaced by `REDACTED`.

    An empty or missing value stays empty: "no value was delivered" is a fact a
    reviewer may see, and replacing it would claim an identifier exists.
    Free-text values elsewhere in the structure are passed through `redact_text`.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if is_protected_field(key):
                out[key] = REDACTED if value not in (None, "", [], {}) else value
            else:
                out[key] = redact_mapping(value)
        return out
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_mapping(v) for v in obj)
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def find_in_text(text: Any) -> List[str]:
    """Reasons `text` is considered to carry a tax identifier. Empty = none
    found by these two rules (see the module docstring for what that means)."""
    if not isinstance(text, str) or not text:
        return []
    reasons = []
    if _LABELLED.search(text):
        reasons.append("labelled_tax_identifier")
    if _EIN_SHAPE.search(text):
        reasons.append("ein_shaped_value")
    return reasons


def redact_text(text: str) -> str:
    """`text` with labelled and EIN-shaped values replaced by `REDACTED`."""
    if not isinstance(text, str) or not text:
        return text
    # The label stays (it says what was withheld); only the value is replaced.
    return _EIN_SHAPE.sub(REDACTED, _LABELLED.sub(lambda m: m.group(1) + REDACTED, text))


def assert_safe_for_ai(text: Any, *, where: str = "ai") -> None:
    """Raise before `text` leaves the process for an AI service.

    The message names the rule that matched and never the value.
    """
    reasons = find_in_text(text if isinstance(text, str) else str(text or ""))
    if reasons:
        raise ProtectedIdentifierEgressRefused(
            f"{where}: refused - the text carries a protected tax identifier "
            f"({', '.join(reasons)}). EIN/TIN/FEIN values are never sent to an "
            f"AI service.")
