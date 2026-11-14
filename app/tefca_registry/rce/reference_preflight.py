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


def _reader_error(exc: csv.Error) -> str:
    message = str(exc)
    if "field larger than field limit" in message:
        return READER_FIELD_TOO_LONG
    if "unexpected end of data" in message:
        return READER_ENDS_IN_QUOTES
    return READER_NOT_WELL_FORMED


def _read_as_records(path) -> Dict[str, Any]:
    """The whole file read as CSV records, strictly, to its end.

    Streaming: one record is held at a time. `records` counts non-empty records
    (the header is one); `last` is the final one. When the file is not complete
    CSV the read stops: `error` says why and `line` where the reader was.
    strict=True: a quoted field still open at end of data is an error, not a
    field - that is a file cut INSIDE a quoted value. The csv field limit is
    whatever the process has (the importer's reader has the same)."""
    records, last = 0, []
    with open_no_follow(path, "rb") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as text:
            reader = csv.reader(text, strict=True)
            try:
                for row in reader:
                    if row:
                        records += 1
                        last = row
            except csv.Error as exc:
                return {"records": records, "last": last, "error": _reader_error(exc),
                        "line": reader.line_num}
    return {"records": records, "last": last, "error": None, "line": None}


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

    rows = list(csv.reader(io.StringIO(head_text, newline="")))
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
    read = _read_as_records(path)
    out["records_read"] = read["records"]
    # What was actually read, not what could have been (see 1.1.0 above).
    out["bound"] = (f"header in full; field counts checked for the first {sampled} record(s) "
                    f"- those within the first {_TAIL_BYTES // 1024} KiB, at most {SAMPLE_ROWS} "
                    f"- and for the final record; the whole file was read as CSV records "
                    f"(strict) to establish the final record")
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
    if bad:
        findings.append(_finding(
            "REF-SCH-007", f"The record at line {bad['line']} has {bad['fields']} "
            f"field(s); the header has {bad['expected']}. Record boundaries cannot be "
            "trusted, so values are not read by position.", disposition=pm.DISP_BLOCKED,
            field_name="__record__", evidence=bad))

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
        limit = csv.field_size_limit()
        evidence["reader_field_limit"] = limit
        findings.append(_finding(
            "REF-SCH-008", f"A field is longer than the CSV reader's limit of {limit} "
            f"characters (near line {read['line']}). The importer reads with the same "
            "limit and cannot read this file as records past that point; it is blocked "
            "here, as ONE technical issue, rather than failing part-way through an import.",
            disposition=pm.DISP_BLOCKED, field_name="__record__", evidence=evidence))
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
