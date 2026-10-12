"""Parse the three NPPES V2 files into typed rows. No network, no database.

Handles: valid rows, malformed rows (wrong column count → recorded with a
parse note, never silently dropped), missing optional fields (empty string →
None), duplicate rows (kept; de-duplication is a comparison-time concern with
provenance, not a parse-time deletion), and the exact CMS header text.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .schema import (MAIN_FILE_COLUMN_COUNT, OTHER_NAME_COLUMNS, PLACEHOLDER_VALUES,
                     PRACTICE_LOCATION_COLUMNS, SCHEMA_VERSION)

PARSER_VERSION = "nppes-v2-1.0"


@dataclass(frozen=True)
class NppesOrganizationRow:
    npi: str
    entity_type_code: Optional[str]
    legal_business_name: Optional[str]
    other_organization_name: Optional[str]
    other_organization_name_type_code: Optional[str]
    mailing: Dict[str, Optional[str]]
    primary_practice_location: Dict[str, Optional[str]]
    enumeration_date: Optional[str]
    last_update_date: Optional[str]
    deactivation_date: Optional[str]
    reactivation_date: Optional[str]
    replacement_npi: Optional[str]
    line_number: int
    parse_note: Optional[str] = None


@dataclass(frozen=True)
class NppesOtherNameRow:
    npi: str
    other_organization_name: Optional[str]
    type_code: Optional[str]
    created_date: Optional[str]
    line_number: int
    parse_note: Optional[str] = None


@dataclass(frozen=True)
class NppesPracticeLocationRow:
    npi: str
    address: Dict[str, Optional[str]]
    telephone: Optional[str]
    line_number: int
    parse_note: Optional[str] = None


PARSE_OK = "OK"                 # every row parsed against the expected layout
PARSE_PARTIAL = "PARTIAL"       # some rows malformed; the good rows are usable, the bad ones are listed
PARSE_FAILED = "FAILED"         # header did not match the layout or the file was empty; nothing usable


@dataclass
class ParseReport:
    file_kind: str
    schema_version: str = SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    rows_read: int = 0
    rows_ok: int = 0
    rows_malformed: int = 0
    rows_skipped: int = 0          # e.g. Type 1 rows when organisations_only
    header_ok: bool = True
    header_note: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    #: Set when reading stopped before the end (csv error such as an oversized
    #: field). Rows after this line were NOT read — stated, never silent.
    stopped_at_line: Optional[int] = None

    @property
    def status(self) -> str:
        """Explicit. A file that half-parsed is PARTIAL, never OK; a file whose
        header is wrong is FAILED even if rows happened to have the right width."""
        if not self.header_ok or self.rows_read == 0:
            return PARSE_FAILED
        return PARSE_PARTIAL if (self.rows_malformed or self.stopped_at_line) else PARSE_OK

    def to_dict(self) -> dict:
        return {"file_kind": self.file_kind, "status": self.status, "schema_version": self.schema_version,
                "parser_version": self.parser_version, "rows_read": self.rows_read, "rows_ok": self.rows_ok,
                "rows_malformed": self.rows_malformed, "rows_skipped": self.rows_skipped,
                "stopped_at_line": self.stopped_at_line,
                "header_ok": self.header_ok, "header_note": self.header_note, "notes": list(self.notes)}


def _clean(value: Optional[str]) -> Optional[str]:
    """Trim; empty -> None; a CMS placeholder such as "<UNAVAIL>" -> None. The
    placeholder means the value is withheld or unavailable: it is not a name
    or an address and must never become an observation."""
    if value is None:
        return None
    v = value.strip()
    if not v or v in PLACEHOLDER_VALUES:
        return None
    return v


def _address(row: List[str], first: int) -> Dict[str, Optional[str]]:
    return {"line1": _clean(row[first]), "line2": _clean(row[first + 1]), "city": _clean(row[first + 2]),
            "state": _clean(row[first + 3]), "postal_code": _clean(row[first + 4]),
            "country_code": _clean(row[first + 5])}


def _reader(text: str) -> Iterable[List[str]]:
    return csv.reader(io.StringIO(text))


def _rows(reader, report: ParseReport, start: int = 2):
    """Iterate data rows; a csv-module error (e.g. a field above the 128 KiB
    limit) ends reading with an explicit note and stopped_at_line instead of
    an exception half-way through the file."""
    n = start - 1
    it = iter(reader)
    while True:
        n += 1
        try:
            row = next(it)
        except StopIteration:
            return
        except csv.Error as exc:
            report.notes.append(f"line {n}: csv error ({exc}); reading stopped, later rows not read")
            report.stopped_at_line = n
            return
        yield n, row


def parse_main_file(text: str, *, organizations_only: bool = True):
    """Yield NppesOrganizationRow for each data row. Type 1 (individual) rows
    are skipped when organizations_only, because this capability corroborates
    organisational identity."""
    report = ParseReport("npidata")
    rows: List[NppesOrganizationRow] = []
    reader = _reader(text)
    header = next(reader, None)
    if header is None:
        report.header_ok = False
        report.header_note = "empty file: no header row"
        return rows, report
    if header[0] != "NPI" or len(header) != MAIN_FILE_COLUMN_COUNT:
        report.header_ok = False
        report.header_note = (f"expected {MAIN_FILE_COLUMN_COUNT} V2 columns starting with NPI, "
                              f"got {len(header)}: schema drift — stop, do not map")
    for n, row in _rows(reader, report):
        report.rows_read += 1
        if len(row) != MAIN_FILE_COLUMN_COUNT:
            report.rows_malformed += 1
            rows.append(NppesOrganizationRow(npi=_clean(row[0]) if row else "", entity_type_code=None,
                                             legal_business_name=None, other_organization_name=None,
                                             other_organization_name_type_code=None, mailing={},
                                             primary_practice_location={}, enumeration_date=None,
                                             last_update_date=None, deactivation_date=None,
                                             reactivation_date=None, replacement_npi=None,
                                             line_number=n, parse_note=f"malformed: {len(row)} columns"))
            continue
        entity_type = _clean(row[1])
        if organizations_only and entity_type != "2":
            report.rows_skipped += 1
            continue
        report.rows_ok += 1
        rows.append(NppesOrganizationRow(
            npi=_clean(row[0]) or "", entity_type_code=entity_type,
            legal_business_name=_clean(row[4]), other_organization_name=_clean(row[11]),
            other_organization_name_type_code=_clean(row[12]),
            mailing=_address(row, 20), primary_practice_location=_address(row, 28),
            enumeration_date=_clean(row[36]), last_update_date=_clean(row[37]),
            deactivation_date=_clean(row[39]), reactivation_date=_clean(row[40]),
            replacement_npi=_clean(row[2]), line_number=n))
    return rows, report


def _check_header(header: Optional[List[str]], expected, report: ParseReport) -> None:
    names = [c[0] for c in expected]
    if header is None:
        report.header_ok = False
        report.header_note = "empty file: no header row"
    elif header != names:
        report.header_ok = False
        missing = [n for n in names if n not in header]
        extra = [h for h in header if h not in names]
        report.header_note = (f"header differs from readme layout; missing={missing} extra={extra}: "
                              f"schema drift — stop, do not map")


def parse_other_name_file(text: str):
    report = ParseReport("othername")
    rows: List[NppesOtherNameRow] = []
    reader = _reader(text)
    _check_header(next(reader, None), OTHER_NAME_COLUMNS, report)
    for n, row in _rows(reader, report):
        report.rows_read += 1
        if len(row) != len(OTHER_NAME_COLUMNS):
            report.rows_malformed += 1
            rows.append(NppesOtherNameRow(_clean(row[0]) if row else "", None, None, None, n,
                                          f"malformed: {len(row)} columns"))
            continue
        report.rows_ok += 1
        rows.append(NppesOtherNameRow(_clean(row[0]) or "", _clean(row[1]), _clean(row[2]), _clean(row[3]), n))
    return rows, report


def parse_practice_location_file(text: str):
    report = ParseReport("pl")
    rows: List[NppesPracticeLocationRow] = []
    reader = _reader(text)
    _check_header(next(reader, None), PRACTICE_LOCATION_COLUMNS, report)
    for n, row in _rows(reader, report):
        report.rows_read += 1
        if len(row) != len(PRACTICE_LOCATION_COLUMNS):
            report.rows_malformed += 1
            rows.append(NppesPracticeLocationRow(_clean(row[0]) if row else "", {}, None, n,
                                                 f"malformed: {len(row)} columns"))
            continue
        report.rows_ok += 1
        rows.append(NppesPracticeLocationRow(_clean(row[0]) or "", _address(row, 1), _clean(row[7]), n))
    return rows, report
