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

ONE VOCABULARY, ONE PARSER
──────────────────────────
Which identifiers are restricted is decided in one place,
`identifier_boundary.GOVERNMENT_RESTRICTED`. This module derives its name
tokens from that set and only adds the spellings a column header uses for the
same thing (`tax_id`, `taxpayer id`, ...). A NAME is parsed in one place too
(1.6.0): `_token_spans` is the only tokenizer and `_protection_horizon` the only
rule; the text scanner chains tokens into names and asks that rule, so a shape
the field rule accepts is a shape the text scanner accepts.

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

PROTECTED_IDENTIFIERS_VERSION = "1.6.0"

#: What replaces a protected value. Deliberately not a mask of the value: a
#: partial tax identifier is still part of a tax identifier.
REDACTED = "[PROTECTED TAX IDENTIFIER]"

#: A whole token that names a tax identifier on its own.
_SINGLE_TOKENS = (frozenset(i.lower() for i in GOVERNMENT_RESTRICTED)
                  | frozenset({"itin", "taxid", "taxpayerid", "taxpayer"}))
#: What may follow the token "tax" (or "taxpayer") to make it an identifier.
_ID_SUFFIXES = frozenset({"id", "ids", "identifier", "identification", "ident",
                          "number", "num", "nbr", "no", "nr", "cd", "code"})

# ═══ ONE NAME PARSER (1.6.0) ═══════════════════════════════════════════════════════════════════════════════════════════
# Before 1.6.0 a name was parsed three times: `is_protected_field` (tokens), the text label regex (the same word lists
# rebuilt as a pattern with its own separators and a bound on trailing tokens), and a second "name run" pass added to
# close the bound. Every input shape one parser accepted and another did not was found one review round at a time
# (1.4.1 bound, 1.5.1 separators, 1.5.2 long names, then quoted names with the other quote character and a value
# followed by punctuation). There is now ONE tokenizer (`_token_spans`) and ONE rule (`_protection_horizon`), and the
# text scanner is built on them: it chains tokens into names, asks the rule whether the chain is a protected name, and
# only then looks for a VALUE. What follows a name (the gap to its value, and the value shapes) is not name parsing and is
# stated separately below.

#: Token boundaries inside one run of letters and digits: lower/digit -> Upper (camelCase), letter <-> digit.
_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
_WORD = re.compile(r"[A-Za-z0-9]+")


def _token_spans(text: str):
    """THE tokenizer: (start, end, lower-cased token) for every token of `text`, in order. A token is a run of letters or
    of digits inside a word; `taxID` is `tax` + `ID`, `EIN000000001` is `EIN` + `000000001`. Linear."""
    for word in _WORD.finditer(text):
        base, piece, prev = word.start(), word.group(0), 0
        for cut in _BOUNDARY.finditer(piece):
            yield base + prev, base + cut.start(), piece[prev:cut.start()].lower()
            prev = cut.start()
        yield base + prev, word.end(), piece[prev:].lower()


def _tokens(name: Any) -> List[str]:
    return [t for _s, _e, t in _token_spans(str(name or ""))]


#: A name written WITHOUT separators (`taxcode`, `irsnumber`, `employeridentificationnumber`, `taxidnumber`): one token that is a
#: head word plus one or two identifier words. A header or a text label may spell a name this way, so the rule knows them.
_HEADS = ("tax", "irs", "taxpayer")
_CONCATENATED = frozenset(
    [h + a for h in _HEADS for a in _ID_SUFFIXES]
    + [h + a + b for h in _HEADS for a in _ID_SUFFIXES for b in _ID_SUFFIXES]
    + ["taxpayer" + a for a in _ID_SUFFIXES] + ["employeridentification"]
    + ["employeridentification" + a for a in _ID_SUFFIXES])


def _protection_horizon(tokens: List[str]):
    """THE rule: the smallest number of leading tokens that already form a protected name, or None. A name is protected
    when any WHOLE token is protected: a token that names an identifier on its own, `tax`/`taxpayer`/`irs` followed by an
    identifier word (`tax payer` + identifier word too), or `employer identification`."""
    best = None
    count = len(tokens)
    for i, tok in enumerate(tokens):
        if best is not None and i + 1 >= best:
            break
        nxt = tokens[i + 1] if i + 1 < count else None
        reach = None
        if tok in _SINGLE_TOKENS or tok in _CONCATENATED:
            reach = i + 1
        elif tok in ("tax", "taxpayer", "irs") and nxt in _ID_SUFFIXES:
            reach = i + 2
        elif tok == "tax" and nxt == "payer" and i + 2 < count and tokens[i + 2] in _ID_SUFFIXES:
            reach = i + 3
        elif tok == "employer" and nxt == "identification":
            reach = i + 2
        if reach is not None and (best is None or reach < best):
            best = reach
    return best


#: Name words that head a phrase whose next word may follow across WHITESPACE (a column heading or a label in running
#: text: `Tax ID`, `taxpayer identification number`). Any other pair of words is joined only by `_ - . / | :` or by a
#: camel/digit boundary.
_PHRASE_HEADS = frozenset({"tax", "taxpayer", "irs", "payer"})


def _space_continues(tokens: List[str], nxt: str) -> bool:
    """May the word `nxt` follow `tokens` across whitespace and still be part of one name? Derived from the rule's own
    vocabulary: a head word takes an identifier word (`payer` after `tax`), `employer` takes `identification`, a
    single-token name takes one identifier word, and a head phrase takes a second identifier word (`tax id number`)."""
    last = tokens[-1]
    before = tokens[-2] if len(tokens) > 1 else None
    if last in ("tax", "irs"):
        return nxt in _ID_SUFFIXES or nxt == "payer"
    if last == "payer" and before == "tax":
        return nxt in _ID_SUFFIXES
    if last == "employer":
        return nxt == "identification"
    if last in _SINGLE_TOKENS:                       # includes `taxpayer`
        return nxt in _ID_SUFFIXES
    if last in _ID_SUFFIXES and before is not None:
        if before in _PHRASE_HEADS:                  # `tax id` + `number`
            return nxt in _ID_SUFFIXES
        if before == "identification" or before == "employer":
            return nxt in _ID_SUFFIXES               # `employer identification` + `number`
    return False


#: The separator between two words of a name that is joined by whitespace: optional `|`/`:` NOT followed by whitespace
#: (prose `Tax: ID` is not a label), then any run of whitespace, `_ - . /`. Possessive, so linear at any length.
_SEP = r"(?:[|:](?!\s))?[\s_\-./]*+"
_PHRASE_SEP = re.compile(_SEP + r"\Z")
#: The separator between two words of ONE run (`ORG_TAX_ID`, `hco.tin`, `tax|id`): any mix of `_ - . / | :`, no whitespace.
_JOINER_RUN = re.compile(r"[_\-./|:]+\Z")


def _name_chains(text: str):
    """Chain the tokens of `text` into names: (start, end, tokens, token_spans). Each token is read once. Linear."""
    toks: List[str] = []
    spans: List[tuple] = []
    for s, e, t in _token_spans(text):
        if spans:
            gap = text[spans[-1][1]:s]
            if (gap == "" or _JOINER_RUN.match(gap)
                    or (_PHRASE_SEP.match(gap) and _space_continues(toks, t))):
                toks.append(t)
                spans.append((s, e))
                continue
            yield spans[0][0], spans[-1][1], toks, spans
            toks, spans = [], []
        toks.append(t)
        spans.append((s, e))
    if spans:
        yield spans[0][0], spans[-1][1], toks, spans


# ═══ WHAT FOLLOWS A NAME (not name parsing) ═══════════════════════════════════════════════════════════════════════════
#: The value shapes: nine digits as an EIN (NN-NNNNNNN, NN NNNNNNN, NNNNNNNNN) or in the three-two-four grouping an ITIN is
#: written in.
_VALUE = r"(?:\d{2}[-\s]?\d{7}|\d{3}-\d{2}-\d{4})(?![\d\-])"
#: What may stand between a name and its value: punctuation of any serialization (quotes, colon, equals, brackets, spaces) and
#: a joining word. PADDING IS NOT CONTENT: a run of horizontal white space, underscores or hyphens of any length is free
#: (`_PAD`, possessive, read once and never given back); the limit of 16 counts only the punctuation marks and joining words
#: between such runs. The gap does not cross a line break.
_PAD = r"(?:[^\S\r\n]|[_\-])*+"
_JOINING = r"(?<![A-Za-z0-9])(?:is|no|number|num|value|val)(?![A-Za-z0-9])"
_GAP = (rf"{_PAD}(?:(?:[^A-Za-z0-9\s_\-]|{_JOINING}){_PAD})"
        r"{0,16}")
_GAP_VALUE = re.compile(rf"{_GAP}(?P<val>{_VALUE})", re.IGNORECASE)
#: What follows a QUOTED serialized key (JSON/YAML-style): `"EIN PRIMARY": "000000001"`.
_KEY_TAIL = re.compile(rf"""\s*[:=]\s*["']?(?P<val>{_VALUE})""")
_KEY_MAX = 80
#: The written EIN shape with no label.
_EIN_SHAPE = re.compile(r"(?<![\d\-])\d{2}-\d{7}(?![\d\-])")


def _quoted_key_value_spans(text: str):
    """Value spans after a quoted key the field rule protects. The key runs from an opening quote to the SAME quote
    character (a serialized backslash escape of it does not close the key), at most 80 characters, on one line, so the other
    quote character may appear inside it (`{"EIN owner's copy": "000000001"}`). The key is judged by `is_protected_field`:
    the one parser."""
    for opening in re.finditer(r"[\"']", text):
        quote, i = opening.group(0), opening.start()
        stop = i + _KEY_MAX + 2
        j = text.find(quote, i + 1, stop)
        while j != -1 and text[j - 1] == "\\":
            j = text.find(quote, j + 1, stop)
        if j == -1:
            continue
        hit = _KEY_TAIL.match(text, j + 1)            # cheap and usually fails: asked before the key is cut out and judged
        if not hit:
            continue
        key = text[i + 1:j]
        if "\n" in key or "\r" in key or not key.strip():
            continue
        if is_protected_field(key.replace("\\" + quote, quote)):
            yield hit.span("val")


def _value_spans(text: str):
    """Every value span `find_in_text` reports and `redact_text` replaces, sorted and distinct. A value follows a protected
    name through the gap above, wherever in the chain the name is complete: after the whole name (`EIN: 000000001`), or
    after any later token of the same chain (`EIN_a_b_c:000000001`, `EIN000000001`, `EIN_a_b_c:000000001.`)."""
    spans = set(_quoted_key_value_spans(text))
    for _start, _end, toks, tspans in _name_chains(text):
        horizon = _protection_horizon(toks)
        if horizon is None:
            continue
        for k in range(horizon - 1, len(toks)):
            if k + 1 < len(toks) and not toks[k + 1][:1].isdigit():
                continue                       # a value can only start at a digit token (or after the chain, below)
            hit = _GAP_VALUE.match(text, tspans[k][1])
            if hit:
                spans.add(hit.span("val"))
    return sorted(spans)


class ProtectedIdentifierEgressRefused(RuntimeError):
    """Text bound for an AI transport carried a tax identifier. Never retried."""


def is_protected_field(name: Any) -> bool:
    """True when a column or key NAME denotes an EIN / TIN / FEIN.

    Token based, so `routing`, `listing` and `taxonomy` are not matches: `tin`
    counts only as a whole token, and `tax` only when an identifier word
    follows it (`ORG_TAX_ID`, `Tax Identifier`, `taxNumber`).
    """
    return _protection_horizon(_tokens(name)) is not None


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
                if value in (None, "", [], {}):
                    # "no value was delivered" stays visible - but as a FRESH empty container, never the caller's own
                    # (appending to the returned list would otherwise write into the source).
                    out[key] = type(value)() if isinstance(value, (list, dict)) else value
                else:
                    out[key] = REDACTED
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
    if _value_spans(text):
        reasons.append("labelled_tax_identifier")
    if _EIN_SHAPE.search(text):
        reasons.append("ein_shaped_value")
    return reasons


def redact_text(text: str) -> str:
    """`text` with labelled and EIN-shaped values replaced by `REDACTED`."""
    if not isinstance(text, str) or not text:
        return text
    # The label stays (it says what was withheld); only the value is replaced. One pass: a text with tens of thousands of
    # values is not rebuilt once per value.
    pieces, pos = [], 0
    for start, end in _value_spans(text):
        if end <= pos:
            continue
        pieces.append(text[pos:max(start, pos)])
        pieces.append(REDACTED)
        pos = end
    pieces.append(text[pos:])
    return _EIN_SHAPE.sub(REDACTED, "".join(pieces))


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
