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

No positional fallback, no renaming, no coercion. The original bytes are
only read. One technical finding per fault, with the affected scope, never
one compliance finding per row.
"""
from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Dict, List, Optional

from app.core.upload_security import open_no_follow
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import snapshot_models as sm

REFERENCE_PREFLIGHT_VERSION = "1.0.0"
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
        "bound": (f"header in full, first {SAMPLE_ROWS} records, final record; "
                  f"not a full-file scan"),
    }

    def done() -> Dict[str, Any]:
        out["findings"] = findings
        out["gate"] = _gate(findings)
        return out

    size = os.path.getsize(path)
    with open_no_follow(path, "rb") as fh:
        head = fh.read(_TAIL_BYTES)
        tail = b""
        if size > len(head):
            fh.seek(max(0, size - _TAIL_BYTES))
            tail = fh.read()
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

    # Decode only complete lines of the head sample.
    cut = head.rfind(b"\n")
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
    if bad:
        findings.append(_finding(
            "REF-SCH-007", f"The record at line {bad['line']} has {bad['fields']} "
            f"field(s); the header has {bad['expected']}. Record boundaries cannot be "
            "trusted, so values are not read by position.", disposition=pm.DISP_BLOCKED,
            field_name="__record__", evidence=bad))

    # Truncation: the final record of the file.
    tail_bytes = tail or head
    out["tail_checked"] = True
    try:
        tail_text = tail_bytes.decode("utf-8", errors="strict" if not tail else "ignore")
    except UnicodeDecodeError:
        tail_text = tail_bytes.decode("utf-8", errors="ignore")
    tail_lines = [ln for ln in tail_text.splitlines() if ln.strip()]
    ends_with_newline = tail_bytes.endswith((b"\n", b"\r"))
    if tail_lines and (tail or len(rows) > 1):
        last = next(csv.reader(io.StringIO(tail_lines[-1])), [])
        if len(last) != len(header) and not (len(tail_lines) == 1 and not tail):
            findings.append(_finding(
                "REF-SCH-008", f"The final record has {len(last)} field(s); the header "
                f"has {len(header)}. The file appears truncated"
                + ("" if ends_with_newline else " (no terminating newline)")
                + "; a partial reference snapshot would answer 'not present' for every "
                "record in the missing part.", disposition=pm.DISP_BLOCKED,
                field_name="__record__",
                evidence={"final_record_fields": len(last), "expected": len(header),
                          "ends_with_newline": ends_with_newline}))
    return done()
