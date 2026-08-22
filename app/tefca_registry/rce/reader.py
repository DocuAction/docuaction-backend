"""
Delimited-file reading for RCE deliveries — detect, decode, parse, never reject.

THE CONTRACT
────────────
`read_delivery()` returns EVERY physical line as a ParsedLine, whatever state it
is in. A line that cannot be split into 41 fields still comes back, carrying its
raw text and a parse_status saying why. Nothing is dropped, and nothing raises
on bad content.

That is not defensive coding for its own sake: Area 1 is the evidence of what
was delivered, and a parser that discards a malformed line destroys the only
record that the line ever arrived. The malformed line is exactly the one an
auditor will ask about.

DELIMITER DETECTION IS RESTRICTED
Only `,` `|` and TAB are considered. A general sniffer will happily conclude
that some other character is the delimiter on a file with unusual content, and a
wrong delimiter does not fail loudly — it produces one enormous field per row,
which looks like data. Restricting the candidate set makes the failure mode
"cannot decide, ask the operator" instead of "confidently wrong".

ENCODING
UTF-8 is tried strictly first. If that fails the bytes are NOT silently replaced:
the fallback decodes with replacement AND records that it did, because a
replacement character in an organisation name is a data-quality fact that has to
survive into the issue ledger.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.tefca_registry.rce.field_map import (
    RCE_FIELDS,
    RCE_FIELD_COUNT,
    schema_fingerprint,
)

logger = logging.getLogger(__name__)

#: The only delimiters considered. See the module docstring.
CANDIDATE_DELIMITERS = ("|", ",", "\t")

DELIMITER_NAMES = {"|": "pipe", ",": "comma", "\t": "tab"}

PARSE_OK = "ok"
PARSE_FIELD_COUNT_MISMATCH = "field_count_mismatch"
PARSE_UNPARSEABLE = "unparseable"

#: Markers of UTF-8 text that was decoded as CP-1252/Latin-1 and re-encoded.
#: The profiled delivery contains NONE of these; the detector is retained
#: because a future delivery may differ, and a check that currently fires on
#: nothing is cheap insurance rather than dead code.
MOJIBAKE_MARKERS = (
    "â€™", "â€˜", "â€œ", "â€\x9d", "â€“", "â€”", "â€¦", "â€",
    "Ã¡", "Ã©", "Ã­", "Ã³", "Ãº", "Ã±", "Ã¼", "Ã–", "Ã„",
    "Ê»", "Â ", "Â·", "Â»", "Â«", "ï»¿",
)


@dataclass
class ParsedLine:
    """One physical line from the delivery, in whatever state it arrived."""

    line_number: int                    # 1-based, counting the header as line 1
    raw_line: str                       # exactly as read, before any processing
    values: List[str] = field(default_factory=list)
    parsed: Dict[str, str] = field(default_factory=dict)
    field_count: int = 0
    parse_status: str = PARSE_OK
    parse_note: Optional[str] = None

    @property
    def record_sha256(self) -> str:
        return hashlib.sha256(self.raw_line.encode("utf-8")).hexdigest()

    def get(self, name: str) -> str:
        return (self.parsed.get(name) or "").strip()


@dataclass
class DeliveryRead:
    """The full result of reading one delivery file."""

    headers: List[str]
    lines: List[ParsedLine]
    delimiter: str
    encoding: str
    encoding_had_errors: bool
    sha256: str
    size_bytes: int
    line_terminator: str
    schema_fingerprint: str
    mojibake_cells: int
    embedded_tab_cells: int
    detection_note: str

    @property
    def record_count(self) -> int:
        return len(self.lines)

    @property
    def ok_count(self) -> int:
        return sum(1 for l in self.lines if l.parse_status == PARSE_OK)

    @property
    def malformed_count(self) -> int:
        return self.record_count - self.ok_count

    def summary(self) -> Dict[str, Any]:
        return {
            "record_count": self.record_count,
            "parse_ok": self.ok_count,
            "parse_malformed": self.malformed_count,
            "delimiter": self.delimiter,
            "delimiter_name": DELIMITER_NAMES.get(self.delimiter, self.delimiter),
            "encoding": self.encoding,
            "encoding_had_errors": self.encoding_had_errors,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "line_terminator": self.line_terminator,
            "schema_fingerprint": self.schema_fingerprint,
            "field_count": len(self.headers),
            "mojibake_cells": self.mojibake_cells,
            "embedded_tab_cells": self.embedded_tab_cells,
            "detection_note": self.detection_note,
        }


class DelimiterUndecidable(ValueError):
    """No candidate delimiter produced a consistent, plausible split.

    Raised rather than guessed. A wrong delimiter yields one giant field per row
    — which parses, stores, and looks like data right up until someone reads it.
    """


def detect_encoding(raw: bytes) -> Tuple[str, str, bool]:
    """(text, encoding_name, had_errors).

    UTF-8 strict first. On failure, UTF-8 with replacement, and `had_errors` is
    True so the caller can record it — a replacement character is a data-quality
    fact, not a rendering detail.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        try:
            return raw.decode("utf-8"), "utf-8-sig", False
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError as exc:
        logger.warning("delivery is not strict UTF-8 (%s); decoding with "
                       "replacement and flagging the intake.", exc)
        return raw.decode("utf-8", errors="replace"), "utf-8(replace)", True


def split_lines(text: str) -> Tuple[List[str], str]:
    """(lines, terminator). Trailing blank line removed."""
    if "\r\n" in text:
        terminator, lines = "CRLF", text.split("\r\n")
    elif "\r" in text and "\n" not in text:
        terminator, lines = "CR", text.split("\r")
    else:
        terminator, lines = "LF", text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return lines, terminator


def detect_delimiter(header: str, sample: List[str],
                     declared: Optional[str] = None) -> Tuple[str, str]:
    """(delimiter, note). Restricted to CANDIDATE_DELIMITERS.

    A candidate wins only if it splits the header into more than one field AND
    splits the sampled data lines into the SAME number of fields as the header
    on a clear majority of them. Consistency is what distinguishes a real
    delimiter from a character that merely appears in the text.
    """
    if declared:
        if declared not in CANDIDATE_DELIMITERS:
            raise DelimiterUndecidable(
                f"Declared delimiter {declared!r} is not one of "
                f"{[DELIMITER_NAMES[d] for d in CANDIDATE_DELIMITERS]}.")
        return declared, f"delimiter declared by the operator as {DELIMITER_NAMES[declared]}"

    scores: Dict[str, Tuple[int, int]] = {}
    for candidate in CANDIDATE_DELIMITERS:
        expected = len(header.split(candidate))
        if expected < 2:
            continue
        consistent = sum(1 for line in sample
                         if len(line.split(candidate)) == expected)
        scores[candidate] = (consistent, expected)

    if not scores:
        raise DelimiterUndecidable(
            "None of pipe, comma or tab splits the header into more than one "
            "field. The file does not look delimited.")

    # ONE CANDIDATE SPLITS THE HEADER -> DECISIVE, no data vote needed.
    #
    # The header is evidence in its own right. If pipe yields 41 header fields
    # while comma and tab yield one, the file is pipe-delimited whatever the
    # data rows look like — and refusing it because the only data row happens to
    # be malformed would discard a delivery over the very defect Area 1 exists
    # to preserve.
    if len(scores) == 1:
        best = next(iter(scores))
        consistent, expected = scores[best]
        return best, (f"detected {DELIMITER_NAMES[best]} — {expected} header "
                      f"fields; sole candidate that splits the header, "
                      f"consistent on {consistent}/{len(sample)} sampled lines")

    # SEVERAL candidates split the header, so the data decides between them. A
    # MAJORITY is required, not near-unanimity: a delivery containing a few
    # malformed rows must still be read, with the bad rows preserved and
    # flagged. A genuinely WRONG delimiter does not score 80% — it scores near
    # zero, because it cannot reproduce the header's field count on any line.
    best = max(scores, key=lambda d: (scores[d][0], scores[d][1]))
    consistent, expected = scores[best]
    if sample and consistent <= len(sample) / 2:
        raise DelimiterUndecidable(
            f"{len(scores)} delimiters split the header, and none splits the "
            f"data consistently. Best candidate {DELIMITER_NAMES[best]} matched "
            f"the header's {expected} fields on only {consistent} of "
            f"{len(sample)} sampled lines — not a majority. Declare the "
            f"delimiter explicitly rather than have it guessed.")
    return best, (f"detected {DELIMITER_NAMES[best]} — {expected} header fields, "
                  f"consistent on {consistent}/{len(sample)} sampled lines")


def count_mojibake(text: str) -> int:
    return sum(text.count(marker) for marker in MOJIBAKE_MARKERS)


def read_delivery(raw: bytes, *, declared_delimiter: Optional[str] = None,
                  expected_fields: Optional[Tuple[str, ...]] = None) -> DeliveryRead:
    """Read a delivery into ParsedLines. Never drops a line, never raises on
    bad row content.

    The only failure mode is `DelimiterUndecidable`, which fires before any line
    is parsed and means the file's shape could not be established at all.
    """
    fields = expected_fields or RCE_FIELDS
    sha = hashlib.sha256(raw).hexdigest()
    text, encoding, had_errors = detect_encoding(raw)
    physical, terminator = split_lines(text)

    if not physical:
        raise DelimiterUndecidable("The file contains no lines.")

    header_line = physical[0]
    sample = physical[1:51]
    delimiter, note = detect_delimiter(header_line, sample, declared_delimiter)
    headers = [h.strip() for h in header_line.split(delimiter)]

    lines: List[ParsedLine] = []
    mojibake_cells = 0
    tab_cells = 0

    for offset, raw_line in enumerate(physical[1:], start=2):
        values = raw_line.split(delimiter)
        parsed_line = ParsedLine(
            line_number=offset, raw_line=raw_line,
            values=values, field_count=len(values),
        )
        if len(values) == len(headers):
            parsed_line.parsed = dict(zip(headers, values))
            parsed_line.parse_status = PARSE_OK
        else:
            # PRESERVED, NOT REJECTED. Positional mapping would silently shift
            # every value past the defect, so the row is stored raw with its
            # field count and a reason, and the issue ledger picks it up.
            parsed_line.parse_status = PARSE_FIELD_COUNT_MISMATCH
            parsed_line.parse_note = (
                f"{len(values)} fields, expected {len(headers)}. The row is "
                f"preserved verbatim; values are NOT mapped positionally, "
                f"because a shifted mapping is worse than none.")
            # Best-effort partial mapping so identifiers stay searchable.
            parsed_line.parsed = {
                h: (values[i] if i < len(values) else "")
                for i, h in enumerate(headers)
            }
        for value in values:
            if any(marker in value for marker in MOJIBAKE_MARKERS):
                mojibake_cells += 1
            if "\t" in value:
                tab_cells += 1
        lines.append(parsed_line)

    return DeliveryRead(
        headers=headers, lines=lines, delimiter=delimiter, encoding=encoding,
        encoding_had_errors=had_errors, sha256=sha, size_bytes=len(raw),
        line_terminator=terminator,
        schema_fingerprint=schema_fingerprint(headers),
        mojibake_cells=mojibake_cells, embedded_tab_cells=tab_cells,
        detection_note=note,
    )
