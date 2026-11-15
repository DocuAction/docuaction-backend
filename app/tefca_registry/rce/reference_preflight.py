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

1.1.0, fourth pass (2026-11-14) - nothing is judged from a window of the file
(tests/test_reference_preflight_sample_boundary_2026_11_14.py):
  * the first 64 KiB were still parsed, non-strictly, for the header and for a
    field-count check of the "sampled" records. When that window ended inside
    a quoted multi-line record the partial record had too few fields and a
    VALID file was BLOCKED (REF-SCH-007). The header, every field count, the
    encoding and the final record now all come from the ONE strict read of the
    whole file. `rows_sampled` remains as a count of complete records in the
    window and is informational only.
  * column names are matched exactly as delivered, as the importer's reader
    keys them. A required column delivered with a space around its name was
    matched after trimming and the file was CLEAR; it is now REF-SCH-004
    (present only under another spelling), which is what the importer sees.
  * a byte-order mark (changed 2026-11-14, fifth pass, on the IMPORTER's side).
    The mark is removed before the first column name is matched here
    (REF-SCH-010, informational, as decided 2026-10-04). The importer opened
    the file as utf-8 and could retain the mark in the first column's name. If
    that column was required for identity, rows could be rejected; if the
    identity columns were elsewhere, rows could stage while the first field was
    mis-keyed, omitted from a projection, or unavailable to downstream logic.
    Which happened to a real snapshot is a matter of evidence, not inference
    (see iqvia_reader_assessment). The importer now decodes with
    REFERENCE_FILE_ENCODING, the same as this gate, so the two read a marked
    file identically. This gate's verdicts are unchanged.
  * consequences of the same change: a header longer than the window is read
    whole; a file larger than the window whose start is blank is no longer
    called empty; a field-count finding's `line` is always the physical line
    on which the record ends; a final record with the wrong field count is
    reported once (REF-SCH-008), not also as REF-SCH-007.

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
#: How the bytes of a staged reference file become text, for THIS gate and for
#: the importer alike (iqvia_import.iter_reference_rows uses this name). A
#: UTF-8 byte-order mark at the very start of the file is not part of the
#: first column name; a U+FEFF anywhere else is data. Nothing computed on the
#: raw bytes (the sha256, the preserved original) is affected by it.
REFERENCE_FILE_ENCODING = "utf-8-sig"

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


def _read_as_records(path) -> Dict[str, Any]:
    """The whole file read as CSV records, strictly, to its end. EVERY verdict
    about the file's records comes from this one read; nothing is judged from
    a window of the file.

    Streaming: one record is held at a time. `first` is the first row the
    reader yields - the header, exactly as the importer's DictReader takes it
    (a blank first line is an EMPTY header, not skipped) - or None when the
    read stopped before any row. `records` counts non-empty records (a
    non-empty header is one), `data_records` the non-empty records after the
    first row, and `last` is the final non-empty record. When the file is not
    complete CSV the read stops: `error` says why and `line` where the reader
    was. strict=True: a quoted field still open at end of data is an error, not
    a field - that is a file cut INSIDE a quoted value. The csv field limit is
    whatever the process has (the importer's reader has the same). The bytes
    are decoded strictly as UTF-8, as the importer decodes them; a byte that is
    not UTF-8 stops the read (`byte_offset` says where).

    Every data record's field count is compared with the header's in the same
    pass. The FINAL record is left out of that count: it is judged separately,
    as the final record. `wrong_count` is how many records differ;
    `wrong_first` holds the line and field count of the first few - never a
    record's content. A record's line is the physical line on which it ENDS
    (the reader's `line_num`; LF, CR and CRLF each end one line)."""
    first = None
    records, data_records, last = 0, 0, []
    expected_fields = 0
    wrong_count, wrong_first = 0, []
    held = None                              # the newest record's (line, fields) if it differs

    def commit() -> None:
        nonlocal wrong_count
        if held is not None:
            wrong_count += 1
            if len(wrong_first) < _WRONG_COUNT_LINES:
                wrong_first.append({"line": held[0], "fields": held[1]})

    def result(error=None, line=None, byte_offset=None) -> Dict[str, Any]:
        return {"first": first, "records": records, "data_records": data_records,
                "last": last, "error": error, "line": line,
                "byte_offset": byte_offset, "wrong_count": wrong_count,
                "wrong_first": wrong_first}

    with open_no_follow(path, "rb") as raw:
        with io.TextIOWrapper(raw, encoding=REFERENCE_FILE_ENCODING, errors="strict",
                              newline="") as text:
            reader = csv.reader(text, strict=True)
            try:
                for row in reader:
                    if first is None:
                        first = row
                        expected_fields = len(row)
                        if row:
                            records += 1
                            last = row
                        continue
                    if not row:
                        continue
                    commit()                 # the previous record was not the final one
                    held = None
                    records += 1
                    data_records += 1
                    last = row
                    if expected_fields and len(row) != expected_fields:
                        held = (reader.line_num, len(row))
            except csv.Error as exc:
                commit()                     # the read stopped: nothing here is "the final record"
                return result(_reader_error(exc), reader.line_num)
            except UnicodeDecodeError:
                commit()
                return result(READER_NOT_UTF8, reader.line_num,
                              _first_invalid_utf8_offset(path))
    return result()


def _complete_records_in_sample(head: bytes, whole_file: bool) -> int:
    """How many COMPLETE, non-empty data records lie within the first rows of
    the first 64 KiB. INFORMATIONAL: it is reported as `rows_sampled` and
    decides nothing - no finding is taken from this view of the file, because
    a window can end inside a quoted multi-line record (review finding
    2026-11-14: a partial record read from the window blocked a valid file).

    The window is read strictly, so a record still open at its end is not
    returned at all rather than returned short. Only the first SAMPLE_ROWS
    rows after the header are looked at, blank ones included."""
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if not whole_file:
        cut = max(head.rfind(b"\n"), head.rfind(b"\r"))
        if cut < 0:
            return 0
        head = head[:cut + 1]
    count = 0
    window = csv.reader(io.StringIO(head.decode("utf-8", errors="replace"), newline=""),
                        strict=True)
    try:
        for index, row in enumerate(window):
            if index == 0:
                continue                     # the header row
            if index > SAMPLE_ROWS:
                break
            if row:
                count += 1
    except csv.Error:
        pass                                 # the window ends inside a record, or cannot be read on
    return count


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
        "bound": "nothing was read: the file is empty",
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
    # "Empty" is decided only when the whole file is in hand. A larger file
    # whose first 64 KiB are blank is not empty; the whole-file read judges it.
    if size <= _TAIL_BYTES and not head.strip():
        findings.append(_finding(
            "REF-SCH-005", "The file is empty: there is no header row. Nothing can be "
            "staged from it, and it must not become an approvable empty snapshot.",
            disposition=pm.DISP_BLOCKED, evidence={"size_bytes": size}))
        return done()

    if head.startswith(b"\xef\xbb\xbf"):
        findings.append(_finding(
            "REF-SCH-010", "The file begins with a UTF-8 byte-order mark. The first "
            "column name is matched with the mark removed; the bytes are unchanged.",
            disposition=pm.DISP_INFORMATIONAL, evidence={"bom": True}))

    # The whole file, as records. This ONE strict read is what every verdict
    # below is taken from: the header (its first row), every record's field
    # count, the encoding, and the final record. No window of the file, no
    # non-strict reader, no tail, no alignment guess, no quote counting.
    read = _read_as_records(path)
    out["records_read"] = read["records"]
    sampled = _complete_records_in_sample(head, whole_file=size <= _TAIL_BYTES)
    out["rows_sampled"] = sampled
    # What was actually read, not what could have been (see 1.1.0 above).
    out["bound"] = ("header in full; the whole file was read as CSV records (strict, decoded "
                    "as UTF-8)"
                    + (f" up to line {read['line']}, where the read stopped; every record's "
                       f"field count up to there was checked against the header"
                       if read["error"] else
                       ": every record's field count was checked against the header and "
                       "the final record established")
                    + f"; rows_sampled counts the first {sampled} record(s) - complete records "
                      f"within the first {_TAIL_BYTES // 1024} KiB, at most {SAMPLE_ROWS} rows - "
                      "and is informational: no finding is taken from that window")

    header_known = read["first"] is not None
    # Column NAMES are judged exactly as delivered, because that is how the
    # importer's DictReader keys each row: a name with a space around it is not
    # the documented column (it was matched after strip() before, and the file
    # was CLEAR although the importer would find no such key in any row). Such
    # a name is reported below as present "only as" its delivered form.
    # `header` (reported, and used for the delimiter check) stays trimmed.
    delivered = list(read["first"] or [])
    header = [h.strip() for h in delivered]
    out["header"] = header
    if header_known and len(header) == 1 and any(d in header[0] for d in ("|", "\t", ";")):
        findings.append(_finding(
            "REF-SCH-009", "The header parses as a single comma-separated field but "
            "contains another delimiter. Reading it positionally would mis-assign every "
            "value; no alternative delimiter is assumed.", disposition=pm.DISP_BLOCKED,
            field_name="__delimiter__", evidence={"header_sample": header[0][:120]}))
        return done()

    dupes = sorted({h for h in delivered if h and delivered.count(h) > 1})
    if dupes:
        findings.append(_finding(
            "REF-SCH-003", f"Column name(s) appear more than once: {', '.join(dupes)}. "
            "Which column's value a name-keyed reader keeps is undefined.",
            disposition=pm.DISP_BLOCKED, evidence={"duplicate_columns": dupes}))

    present = set(delivered)
    folded = {_fold(h): h for h in delivered}
    # When the read stopped before it returned any row, the header is unknown,
    # not empty: the columns are not reported as absent. What stopped the read
    # is reported below.
    for col in (schema["required"] if header_known else ()):
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
    for col, dependent in (schema["expected"].items() if header_known else ()):
        if col not in present:
            out["held_checks"].append({"column": col, "held_check": dependent})
            findings.append(_finding(
                "REF-SCH-002", f"Expected column {col!r} is absent. {dependent} cannot "
                "run against this snapshot and is HELD; checks that do not need the "
                "column proceed.", disposition=pm.DISP_OPEN, field_name=col,
                execution=pm.EXEC_HELD,
                evidence={"missing_column": col, "held_check": dependent}))
    known = set(schema["required"]) | set(schema["expected"])
    extra = [h for h in delivered if h.strip() and h not in known]
    if extra:
        findings.append(_finding(
            "REF-SCH-011", f"{len(extra)} column(s) outside the documented key layout "
            "are present. Preserved as delivered in each row's payload; no rule reads "
            "them.", disposition=pm.DISP_INFORMATIONAL,
            evidence={"extra_column_count": len(extra), "sample": extra[:10]}))

    # Whether ANY data record exists comes from the whole read, never from the
    # sampled count. A file that cannot be read to its end is not "empty"; the
    # final-record check reports it.
    has_data = read["data_records"] > 0 or read["error"] is not None
    if not has_data:
        findings.append(_finding(
            "REF-SCH-012", "The file has a header row and no data record. Nothing would "
            "be staged, and the snapshot would reconcile as an empty set against which "
            "every record 'does not match'; an empty reference extract is blocked as ONE "
            "technical issue instead.", disposition=pm.DISP_BLOCKED, field_name="__record__",
            evidence={"data_records": 0, "size_bytes": size}))
    # Field counts: every COMPLETE record of the whole read except the final one
    # (judged below). ONE finding, whatever the count. `line` is the physical
    # line on which the record ends.
    if read["wrong_first"]:
        first_wrong = read["wrong_first"][0]
        wrong = read["wrong_count"]
        bad = {"line": first_wrong["line"], "fields": first_wrong["fields"],
               "expected": len(header), "records_with_wrong_field_count": wrong,
               "first_lines": [w["line"] for w in read["wrong_first"]]}
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
