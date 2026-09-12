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

from .schema import (MAIN_FILE_COLUMN_COUNT, OTHER_NAME_COLUMNS, PRACTICE_LOCATION_COLUMNS,
                     SCHEMA_VERSION)

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


@dataclass
class ParseReport:
    file_kind: str
    schema_version: str = SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    rows_read: int = 0
    rows_ok: int = 0
    rows_malformed: int = 0
    header_ok: bool = True
    header_note: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    return v or None


def _address(row: List[str], first: int) -> Dict[str, Optional[str]]:
    return {"line1": _clean(row[first]), "line2": _clean(row[first + 1]), "city": _clean(row[first + 2]),
            "state": _clean(row[first + 3]), "postal_code": _clean(row[first + 4]),
            "country_code": _clean(row[first + 5])}


def _reader(text: str) -> Iterable[List[str]]:
    return csv.reader(io.StringIO(text))


def parse_main_file(text: str, *, organizations_only: bool = True):
    """Yield NppesOrganizationRow for each data row. Type 1 (individual) rows
    are skipped when organizations_only, because this capability corroborates
    organisational identity."""
    report = ParseReport("npidata")
    rows: List[NppesOrganizationRow] = []
    reader = _reader(text)
    header = next(reader, None)
    if not header or header[0] != "NPI" or len(header) != MAIN_FILE_COLUMN_COUNT:
        report.header_ok = False
        report.header_note = (f"expected {MAIN_FILE_COLUMN_COUNT} V2 columns starting with NPI, "
                              f"got {len(header) if header else 0}")
    for n, row in enumerate(reader, start=2):
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
    if header != names:
        report.header_ok = False
        report.header_note = f"header differs from readme layout: {header}"


def parse_other_name_file(text: str):
    report = ParseReport("othername")
    rows: List[NppesOtherNameRow] = []
    reader = _reader(text)
    _check_header(next(reader, None), OTHER_NAME_COLUMNS, report)
    for n, row in enumerate(reader, start=2):
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
    for n, row in enumerate(reader, start=2):
        report.rows_read += 1
        if len(row) != len(PRACTICE_LOCATION_COLUMNS):
            report.rows_malformed += 1
            rows.append(NppesPracticeLocationRow(_clean(row[0]) if row else "", {}, None, n,
                                                 f"malformed: {len(row)} columns"))
            continue
        report.rows_ok += 1
        rows.append(NppesPracticeLocationRow(_clean(row[0]) or "", _address(row, 1), _clean(row[7]), n))
    return rows, report
