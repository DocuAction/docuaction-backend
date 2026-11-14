"""Preflight for REFERENCE SNAPSHOTS (2026-10-04, Part B).

Part A enforced preflight for ONC deliveries only and named reference
snapshots as its open gap. This closes it for the ingestion mechanism that
exists: IQVIA extracts. Both entry points (`/sources/{source}/stage`, a
server-local file, and `/uploads/{id}/complete`, a chunked upload) and the
scheduler-run durable job all converge on `iqvia_import._import_csv`, so
that one function is the enforcement point.

WHAT WENT WRONG WITHOUT IT (reproduced in the tests)
A file whose identity column is missing or renamed was imported "successfully":
every row was rejected one at a time, zero rows were staged, and
`verify_snapshot_staged_completely` then reported `reconciles: True`
(0 expected == 0 staged) -- an approvable, EMPTY reference snapshot, against
which every entity would later "not match". One schema fault became N
row-level rejections and a clean-looking reconciliation.

WHAT THIS CHECKS, AND ITS BOUND
The header in full; the first `SAMPLE_ROWS` records; the final record of
the file. It does not re-read a multi-GB extract end to end (the importer
already does, and a mid-file decode error still fails that import) -- the
bound is stated in every result (`rows_sampled`, `tail_checked`).

    REF-SCH-001  required identity column missing                 BLOCKED
    REF-SCH-002  expected non-identity column missing             OPEN
                 (the checks that need it are HELD for this snapshot;
                  everything else proceeds)
    REF-SCH-003  duplicate header column                          BLOCKED
    REF-SCH-004  a required column present only under a
                 different case/spacing -- never auto-mapped       BLOCKED
    REF-SCH-005  empty file / no header                           BLOCKED
    REF-SCH-006  not valid UTF-8 where checked                    BLOCKED
    REF-SCH-007  a sampled record's field count != header         BLOCKED
    REF-SCH-008  truncated final record                           BLOCKED
    REF-SCH-009  wrong delimiter (header is one field)            BLOCKED
    REF-SCH-010  UTF-8 byte-order mark                            INFORMATIONAL
    REF-SCH-011  column not in the documented layout              INFORMATIONAL
    REF-SCH-012  header present but no data record                BLOCKED

1.1.0 (2026-11-13) - three gaps reproduced with synthetic files:
  * a header-only extract was CLEAR, i.e. exactly the approvable EMPTY snapshot
    this module exists to prevent (REF-SCH-005 caught only a file with no
    header). Now REF-SCH-012, BLOCKED.
  * a VALID extract whose final record carries a quoted line break was BLOCKED
    as truncated (REF-SCH-008): the last PHYSICAL line was parsed on its own.
    The final record is now read by the CSV reader, as a record.
  * `bound` said "first 1000 records" whatever was read. Records are read from
    the first 64 KiB only, so on wide rows far fewer are sampled (290 of 3,000
    in the reproduction). `bound` now states what was actually read.

1.1.0, first review (2026-11-14) - four more, each reproduced first:
  * the final record was read from a GUESSED alignment of the last 64 KiB. A
    tail cannot know whether it starts inside a quoted field, so a file cut
    inside a quoted value opened before the tail was CLEAR (a later line of
    the open field read as a complete record), and a valid long quoted field
    at the start of the tail was BLOCKED. (The first fix counted the quote
    characters in front of the tail; see the second review below.)
  * only LF was a record boundary; a CR-only file was BLOCKED as truncated.
  * a header, blank lines beyond the sample window, then data was BLOCKED as
    empty; and a LARGE file of a header and blank lines only was CLEAR.
    Whether a data record exists is now decided separately from how many
    records were sampled.

1.1.0, second review (2026-11-14):
  * quote PARITY is not CSV field state. A literal quote in an unquoted field
    and the opening quote of a final, never-closed field make the count in
    front of the tail even, and a later line of the open field was accepted
    as the final record - CLEAR for a file a strict parse rejects. No tail
    heuristic remains: the final record is established ONLY by reading the
    whole file as CSV records (strict, streaming, one record held at a
    time). The gate's verdict on the final record is, by construction, a
    strict whole-file parse's.
  * the read uses the csv module's field limit as it stands - the limit the
    importer's own reader has. A field beyond it is BLOCKED (the importer
    could not read it either) and REF-SCH-008 says that a field is too long,
    not that the file is truncated. The process-wide limit is not changed.

1.1.0, third pass (2026-11-14) - the reader and the importer, case by case
(tests/test_reference_preflight_parser_agreement_2026_11_14.py):
  * a field beyond the csv reader's limit inside the SAMPLE made this function
    raise. It is now the same REF-SCH-008 the whole-file read gives.
  * every record is read, so every record's field count is now checked
    against the header, not only the sampled records and the final one. Still
    ONE REF-SCH-007: a count and the first few line numbers, never a row.
  * the whole-file read decoded with replacement characters while the
    importer decodes strictly, so invalid UTF-8 beyond the first 64 KiB was
    CLEAR here and failed there. It is decoded strictly now and reported as
    REF-SCH-006 with the byte offset.
  * NOT changed, pinned by a test: text after a closing quote ("ab"c) is
    blocked by this strict reader and read by the importer's non-strict one.

No positional fallback, no renaming, no coercion. The original bytes are
only read. One technical finding per fault, with the affected scope, never
one compliance finding per row.
"""
from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Dict, List

from app.core.upload_security import open_no_follow
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import snapshot_models as sm

REFERENCE_PREFLIGHT_VERSION = "1.1.0"
SAMPLE_ROWS = 1000
_TAIL_BYTES = 64 * 1024

#: Per source: the identity column(s) the importer keys on (required), the
#: columns specific checks depend on (expected), and what is held when one of
#: those is absent. Names are the importer's own constants, restated here by
#: value so a layout change is one visible edit, pinned by a version.
REFERENCE_SCHEMAS: Dict[str, Dict[str, Any]] = {
    sm.SOURCE_IQVIA_HCO: {
        "schema_version": "iqvia-hco-demographic/2026-10-02",
        "required": ("HCO_HCE_ID",),
        "expected": {"ORG_NPI": "NPI-keyed organisation matching",
                     "ORG_CCN_ID": "CCN corroboration"},
    },
    sm.SOURCE_IQVIA_HCP: {
        "schema_version": "iqvia-hcp-addr/2026-10-02",
        "required": ("HCP_HCE_ID", "ADDR_ID"),
        "expected": {"NPI": "NPI-keyed practitioner matching"},
    },
    sm.SOURCE_IQVIA_AFFILIATION: {
        "schema_version": "iqvia-hcp-affil/2026-10-06",
        "required": ("HCP_HCE_ID", "HCO_HCE_ID"),
        "expected": {"AFFL_TYP_ID": "provider-affiliation type classification",
                     "TITL_TYP_ID": "contact-affiliation title classification"},
    },
}


#: Why a strict read of the file stopped, in this module's own words (the csv
#: module's message is classified, never passed on).
READER_ENDS_IN_QUOTES = "ends_inside_quoted_field"
READER_FIELD_TOO_LONG = "field_longer_than_reader_limit"
READER_NOT_WELL_FORMED = "not_well_formed_csv"
READER_NOT_UTF8 = "not_valid_utf8"
#: How many offending line numbers a field-count finding lists (never rows).
_WRONG_COUNT_LINES = 5


def _reader_error(exc: csv.Error) -> str:
    message = str(exc)
    if "field larger than field limit" in message:
        return READER_FIELD_TOO_LONG
    if "unexpected end of data" in message:
        return READER_ENDS_IN_QUOTES
    return READER_NOT_WELL_FORMED


def _first_invalid_utf8_offset(path) -> int:
    """Byte offset in the file of the first byte that is not valid UTF-8.
    Streaming; called only after a strict decode of the file has failed."""
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    consumed = 0
    with open_no_follow(path, "rb") as raw:
        while True:
            chunk = raw.read(1024 * 1024)
            pending = len(decoder.getstate()[0])
            try:
                decoder.decode(chunk, final=not chunk)
            except UnicodeDecodeError as exc:
                return consumed - pending + exc.start
            if not chunk:
                return consumed
            consumed += len(chunk)


def _read_as_records(path, expected_fields: int = 0) -> Dict[str, Any]:
    """The whole file read as CSV records, strictly, to its end.

    Streaming: one record is held at a time. `records` counts non-empty records
    (the header is one); `last` is the final one. When the file is not complete
    CSV the read stops: `error` says why and `line` where the reader was.
    strict=True: a quoted field still open at end of data is an error, not a
    field - that is a file cut INSIDE a quoted value. The csv field limit is
    whatever the process has (the importer's reader has the same). The bytes
    are decoded strictly as UTF-8, as the importer decodes them; a byte that is
    not UTF-8 stops the read (`byte_offset` says where).

    With `expected_fields`, every data record's field count is compared with
    it in the same pass. The FINAL record is left out of that count: it is
    judged separately, as the final record. `wrong_count` is how many records
    differ; `wrong_first` holds the line and field count of the first few -
    never a record's content."""
    records, last = 0, []
    wrong_count, wrong_first = 0, []
    held = None                              # the newest record's (line, fields) if it differs

    def commit() -> None:
        nonlocal wrong_count
        if held is not None:
            wrong_count += 1
            if len(wrong_first) < _WRONG_COUNT_LINES:
                wrong_first.append({"line": held[0], "fields": held[1]})

    def result(error=None, line=None, byte_offset=None) -> Dict[str, Any]:
        return {"records": records, "last": last, "error": error, "line": line,
                "byte_offset": byte_offset, "wrong_count": wrong_count,
                "wrong_first": wrong_first}

    with open_no_follow(path, "rb") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="strict", newline="") as text:
            reader = csv.reader(text, strict=True)
            try:
                for row in reader:
                    if not row:
                        continue
                    commit()                 # the previous record was not the final one
                    held = None
                    records += 1
                    last = row
                    if expected_fields and records > 1 and len(row) != expected_fields:
                        held = (reader.line_num, len(row))
            except csv.Error as exc:
                commit()                     # the read stopped: nothing here is "the final record"
                return result(_reader_error(exc), reader.line_num)
            except UnicodeDecodeError:
                commit()
                return result(READER_NOT_UTF8, reader.line_num,
                              _first_invalid_utf8_offset(path))
    return result()


class ReferencePreflightBlocked(RuntimeError):
    """Raised by the importer -- only when ENABLE_PREFLIGHT_ENFORCEMENT is on
    -- when a reference file's gate is BLOCKED. Deterministic: retrying the
    same bytes cannot succeed."""


def _fold(name: str) -> str:
    return re.sub(r"\s+", "", name).lower()


def _finding(code: str, description: str, *, disposition: str, evidence: Dict[str, Any],
             field_name: str = "__header__", execution: str = pm.EXEC_DONE,
             applicability: str = pm.APPLIES) -> Dict[str, Any]:
    return {"category": "SCHEMA", "code": code, "field_name": field_name,
            "applicability": applicability, "execution": execution,
            "disposition": disposition, "evidence": evidence, "description": description}


def _unreadable_finding(reader_error: str, line: int, evidence: Dict[str, Any]) -> Dict[str, Any]:
    """REF-SCH-008 for a read the csv reader could not continue. Used by the
    sample (when the header row itself cannot be read) and by the whole-file
    read, so the two say the same thing about the same fault."""
    evidence = {**evidence, "reader_error": reader_error, "reader_line": line}
    if reader_error == READER_FIELD_TOO_LONG:
        limit = csv.field_size_limit()
        evidence["reader_field_limit"] = limit
        return _finding(
            "REF-SCH-008", f"A field is longer than the CSV reader's limit of {limit} "
            f"characters (near line {line}). The importer reads with the same "
            "limit and cannot read this file as records past that point; it is blocked "
            "here, as ONE technical issue, rather than failing part-way through an import.",
            disposition=pm.DISP_BLOCKED, field_name="__record__", evidence=evidence)
    return _finding(
        "REF-SCH-008", f"The file is not well-formed CSV near line {line}. Record "
        "boundaries cannot be established, so the final record cannot be confirmed "
        "complete.", disposition=pm.DISP_BLOCKED, field_name="__record__", evidence=evidence)


def _gate(findings: List[Dict[str, Any]]) -> str:
    dispositions = {f["disposition"] for f in findings}
    if pm.DISP_BLOCKED in dispositions:
        return pm.GATE_BLOCKED
    if pm.DISP_OPEN in dispositions:
        return pm.GATE_CLEAR_WITH_FINDINGS
    return pm.GATE_CLEAR


def preflight_reference_file(path, source_system: str) -> Dict[str, Any]:
    """Bounded structural preflight of one reference extract. Never raises
    for a property of the FILE (that is a finding); raises only for an
    unknown `source_system` (a programming error)."""
    if source_system not in REFERENCE_SCHEMAS:
        raise ValueError(f"no reference schema registered for {source_system!r}")
    schema = REFERENCE_SCHEMAS[source_system]
    findings: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {
        "preflight_version": REFERENCE_PREFLIGHT_VERSION, "source_system": source_system,
        "schema_version": schema["schema_version"], "rows_sampled": 0,
        "tail_checked": False, "held_checks": [], "originals_modified": False,
        "bound": (f"header in full; records within the first {_TAIL_BYTES // 1024} KiB "
                  f"(at most {SAMPLE_ROWS}); final record; not a full-file scan"),
    }

    def done() -> Dict[str, Any]:
        out["findings"] = findings
        out["gate"] = _gate(findings)
        return out

    size = os.path.getsize(path)
    with open_no_follow(path, "rb") as fh:
        head = fh.read(_TAIL_BYTES)
        last_byte = b""
        if size:
            fh.seek(size - 1)
            last_byte = fh.read(1)
    if not head.strip():
        findings.append(_finding(
            "REF-SCH-005", "The file is empty: there is no header row. Nothing can be "
            "staged from it, and it must not become an approvable empty snapshot.",
            disposition=pm.DISP_BLOCKED, evidence={"size_bytes": size}))
        return done()

    bom = head.startswith(b"\xef\xbb\xbf")
    if bom:
        head = head[3:]
        findings.append(_finding(
            "REF-SCH-010", "The file begins with a UTF-8 byte-order mark. The first "
            "column name is matched with the mark removed; the bytes are unchanged.",
            disposition=pm.DISP_INFORMATIONAL, evidence={"bom": True}))

    # Decode only complete lines of the head sample (a line ends in LF or CR).
    cut = max(head.rfind(b"\n"), head.rfind(b"\r"))
    head_text_bytes = head if (size <= _TAIL_BYTES or cut < 0) else head[:cut + 1]
    try:
        head_text = head_text_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        findings.append(_finding(
            "REF-SCH-006", "The start of the file is not valid UTF-8. Values are not "
            "re-decoded or guessed; the extract must be re-delivered or its encoding "
            "confirmed.", disposition=pm.DISP_BLOCKED, field_name="__encoding__",
            evidence={"byte_offset": exc.start, "expected": "utf-8"}))
        return done()

    # The sample is read with the process's csv field limit, like every other
    # read here. A field beyond it stops the sample; that is a property of the
    # file, reported below - never an exception out of this function.
    rows: List[List[str]] = []
    sample_reader = csv.reader(io.StringIO(head_text, newline=""))
    try:
        for sample_row in sample_reader:
            rows.append(sample_row)
    except csv.Error as exc:
        if not rows:                         # the header row itself could not be read
            out["header"] = []
            findings.append(_unreadable_finding(
                _reader_error(exc), sample_reader.line_num,
                {"final_record_fields": 0, "expected": 0,
                 "ends_with_newline": last_byte in (b"\n", b"\r")}))
            return done()
    header = [h.strip() for h in (rows[0] if rows else [])]
    out["header"] = header
    if len(header) == 1 and any(d in header[0] for d in ("|", "\t", ";")):
        findings.append(_finding(
            "REF-SCH-009", "The header parses as a single comma-separated field but "
            "contains another delimiter. Reading it positionally would mis-assign every "
            "value; no alternative delimiter is assumed.", disposition=pm.DISP_BLOCKED,
            field_name="__delimiter__", evidence={"header_sample": header[0][:120]}))
        return done()

    dupes = sorted({h for h in header if h and header.count(h) > 1})
    if dupes:
        findings.append(_finding(
            "REF-SCH-003", f"Column name(s) appear more than once: {', '.join(dupes)}. "
            "Which column's value a name-keyed reader keeps is undefined.",
            disposition=pm.DISP_BLOCKED, evidence={"duplicate_columns": dupes}))

    present = set(header)
    folded = {_fold(h): h for h in header}
    for col in schema["required"]:
        if col in present:
            continue
        near = folded.get(_fold(col))
        if near:
            findings.append(_finding(
                "REF-SCH-004", f"Required identity column {col!r} is present only as "
                f"{near!r}. It is NOT auto-mapped: a renamed column may carry a changed "
                "definition.", disposition=pm.DISP_BLOCKED, field_name=col,
                evidence={"delivered_name": near, "expected_name": col}))
        else:
            findings.append(_finding(
                "REF-SCH-001", f"Required identity column {col!r} is absent. Every row "
                "would be rejected individually and the snapshot would reconcile as an "
                "empty set; the whole extract is blocked instead, as ONE technical issue.",
                disposition=pm.DISP_BLOCKED, field_name=col,
                evidence={"missing_column": col, "delivered_columns": len(header)}))
    for col, dependent in schema["expected"].items():
        if col not in present:
            out["held_checks"].append({"column": col, "held_check": dependent})
            findings.append(_finding(
                "REF-SCH-002", f"Expected column {col!r} is absent. {dependent} cannot "
                "run against this snapshot and is HELD; checks that do not need the "
                "column proceed.", disposition=pm.DISP_OPEN, field_name=col,
                execution=pm.EXEC_HELD,
                evidence={"missing_column": col, "held_check": dependent}))
    known = set(schema["required"]) | set(schema["expected"])
    extra = [h for h in header if h and h not in known]
    if extra:
        findings.append(_finding(
            "REF-SCH-011", f"{len(extra)} column(s) outside the documented key layout "
            "are present. Preserved as delivered in each row's payload; no rule reads "
            "them.", disposition=pm.DISP_INFORMATIONAL,
            evidence={"extra_column_count": len(extra), "sample": extra[:10]}))

    # Record boundaries: sampled rows must match the header's field count.
    sampled, bad = 0, None
    for line_no, row in enumerate(rows[1:1 + SAMPLE_ROWS], start=2):
        if not row:
            continue
        sampled += 1
        if len(row) != len(header) and bad is None:
            bad = {"line": line_no, "fields": len(row), "expected": len(header)}
    out["rows_sampled"] = sampled
    # The whole file, as records. This is the ONLY thing the final record is
    # judged from: no tail window, no alignment guess, no quote counting.
    read = _read_as_records(path, expected_fields=len(header))
    out["records_read"] = read["records"]
    # What was actually read, not what could have been (see 1.1.0 above).
    out["bound"] = (f"header in full; the first {sampled} record(s) - those within the first "
                    f"{_TAIL_BYTES // 1024} KiB, at most {SAMPLE_ROWS} - were sampled; the "
                    f"whole file was read as CSV records (strict, decoded as UTF-8)"
                    + (f" up to line {read['line']}, where the read stopped; every record's "
                       f"field count up to there was checked against the header"
                       if read["error"] else
                       ": every record's field count was checked against the header and "
                       "the final record established"))
    # Whether ANY data record exists is a separate question from how many were
    # sampled: the sample window can hold only blank lines. A file that cannot
    # be read to its end is not "empty"; the final-record check reports it.
    has_data = bool(sampled) or read["records"] > 1 or read["error"] is not None
    if not has_data:
        findings.append(_finding(
            "REF-SCH-012", "The file has a header row and no data record. Nothing would "
            "be staged, and the snapshot would reconcile as an empty set against which "
            "every record 'does not match'; an empty reference extract is blocked as ONE "
            "technical issue instead.", disposition=pm.DISP_BLOCKED, field_name="__record__",
            evidence={"data_records": 0, "size_bytes": size}))
    # Field counts: the sampled records, and every other record of the whole
    # read except the final one (judged below). ONE finding, whatever the count.
    if bad is None and read["wrong_first"]:
        first = read["wrong_first"][0]
        bad = {"line": first["line"], "fields": first["fields"], "expected": len(header)}
    if bad:
        wrong = max(read["wrong_count"], 1)
        bad["records_with_wrong_field_count"] = wrong
        bad["first_lines"] = [w["line"] for w in read["wrong_first"]] or [bad["line"]]
        findings.append(_finding(
            "REF-SCH-007", f"The record at line {bad['line']} has {bad['fields']} "
            f"field(s); the header has {bad['expected']}"
            + (f" ({wrong} records differ from the header)" if wrong > 1 else "")
            + ". Record boundaries cannot be "
            "trusted, so values are not read by position.", disposition=pm.DISP_BLOCKED,
            field_name="__record__", evidence=bad))
    if read["error"] == READER_NOT_UTF8:
        findings.append(_finding(
            "REF-SCH-006", f"The file is not valid UTF-8 at byte offset "
            f"{read['byte_offset']}. Values are not re-decoded or guessed, and the "
            "importer decodes the file strictly and would stop there; the extract must "
            "be re-delivered or its encoding confirmed.", disposition=pm.DISP_BLOCKED,
            field_name="__encoding__",
            evidence={"byte_offset": read["byte_offset"], "expected": "utf-8"}))
        out["tail_checked"] = True
        return done()                      # the encoding finding says what is wrong

    # The final record of the file.
    out["tail_checked"] = True
    ends_with_newline = last_byte in (b"\n", b"\r")
    if not has_data:
        return done()                      # REF-SCH-012 already says what is wrong
    last = [] if read["error"] else read["last"]
    evidence = {"final_record_fields": len(last), "expected": len(header),
                "ends_with_newline": ends_with_newline}
    if read["error"]:
        evidence.update({"reader_error": read["error"], "reader_line": read["line"]})
    partial = ("a partial reference snapshot would answer 'not present' for every "
               "record in the missing part.")
    if read["error"] == READER_FIELD_TOO_LONG:
        findings.append(_unreadable_finding(read["error"], read["line"], evidence))
    elif read["error"] == READER_ENDS_IN_QUOTES:
        findings.append(_finding(
            "REF-SCH-008", "The file ends inside a quoted value: a quoted field is opened "
            "and never closed, so the final record is not complete. The file appears "
            "truncated" + ("" if ends_with_newline else " (no terminating newline)")
            + "; " + partial, disposition=pm.DISP_BLOCKED, field_name="__record__",
            evidence=evidence))
    elif read["error"]:
        findings.append(_finding(
            "REF-SCH-008", f"The file is not well-formed CSV near line {read['line']} "
            "(text follows a closing quote before the next delimiter, or similar). Record "
            "boundaries after that point cannot be established, so the final record "
            "cannot be confirmed complete.", disposition=pm.DISP_BLOCKED,
            field_name="__record__", evidence=evidence))
    elif len(last) != len(header):
        findings.append(_finding(
            "REF-SCH-008", f"The final record has {len(last)} field(s); the header "
            f"has {len(header)}. The file appears truncated"
            + ("" if ends_with_newline else " (no terminating newline)")
            + "; " + partial, disposition=pm.DISP_BLOCKED,
            field_name="__record__", evidence=evidence))
    return done()
