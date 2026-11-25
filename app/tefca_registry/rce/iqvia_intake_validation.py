"""Full-file intake validation of an IQVIA extract, measured while it streams.

Pure: no database, no logging. The importer feeds it every record once and
stores `as_metadata()` on the snapshot under `import_validation` when the file
has been read to its end.

WHAT IT MEASURES, AND WHAT IT DOES NOT CLAIM
Against the contract the header is bound to (or, for an unregistered layout,
the newest contract of that source, by column name):
  * cells longer than the vendor length, per column. Length is counted in
    characters; the vendor layout does not state a unit.
  * non-blank cells of a vendor-Numeric column that are not a plain decimal
    lexeme (an optional minus sign, ASCII digits, an optional fraction). The
    cell is tested exactly as delivered, so surrounding spaces count.
  * records whose cell count differs from the header's.
  * what happened to the lifted identifier columns (npi, ccn).
A vendor "Numeric" has no length and says nothing about range, precision,
nullability or uniqueness, so none of those is measured. Nothing is coerced,
trimmed or converted, and a count never changes what is stored.

NO VALUE IS EVER RECORDED: only column names, counts, character counts and
record numbers. A record number is the 1-based ordinal of the data record in
the file (the header is not a record, blank lines are not counted), and each
finding keeps at most `MAX_RECORD_NUMBERS` of them.
"""
from __future__ import annotations

import csv
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.upload_security import open_no_follow
from app.services.npi_validator import is_valid_npi
from app.tefca_registry.rce.reference_preflight import REFERENCE_FILE_ENCODING

VALIDATION_VERSION = "1.0.0"
MAX_RECORD_NUMBERS = 10
#: Per-column entries written to metadata (a real layout has at most 194 columns).
MAX_COLUMNS_REPORTED = 400

HEADER_NOT_UTF8 = "not_valid_utf8"
HEADER_NOT_CSV = "not_well_formed_csv"

_PLAIN_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def read_reference_header(path) -> Tuple[Optional[List[str]], Optional[str]]:
    """The first row of the file as `csv.reader` returns it - before any
    name-keyed mapping, so duplicate names and their order are still visible.
    (header, None), (None, None) for a file with no row at all, or
    (None, reason) when the first row cannot be read."""
    try:
        with open_no_follow(path, "r", encoding=REFERENCE_FILE_ENCODING, newline="") as fh:
            return next(csv.reader(fh), None), None
    except UnicodeDecodeError:
        return None, HEADER_NOT_UTF8
    except csv.Error:
        return None, HEADER_NOT_CSV


def legacy_row(header: Sequence[str], cells: Sequence[str]) -> Dict[Any, Any]:
    """The row `csv.DictReader` builds from one record, exactly: a duplicate
    name keeps its LAST cell, a short record has None for the missing cells and
    a long record carries the extra cells as a list under the key None."""
    row: Dict[Any, Any] = dict(zip(header, cells))
    width, got = len(header), len(cells)
    if width < got:
        row[None] = list(cells[width:])
    elif width > got:
        for name in header[got:]:
            row[name] = None
    return row


class Finding:
    __slots__ = ("count", "records", "max_characters")

    def __init__(self) -> None:
        self.count = 0
        self.records: List[int] = []
        self.max_characters = 0

    def add(self, record_number: int) -> None:
        self.count += 1
        if len(self.records) < MAX_RECORD_NUMBERS:
            self.records.append(record_number)


class FullFileValidation:
    """One pass over one file. `contract` is an iqvia_contract_registry.Contract
    or None; `lifted_limits` maps a lifted column ("npi", "ccn") to its
    database length."""

    def __init__(self, *, header: Optional[Sequence[str]], contract, basis: str,
                 lifted_limits: Dict[str, int], lifted_sources: Dict[str, str]):
        self.header = list(header or [])
        self.width = len(self.header)
        self.contract_id = contract.contract_id if contract is not None else None
        self.basis = basis
        fields = {f.name: f for f in contract.fields} if contract is not None else {}
        self._length_checks: List[Tuple[int, int]] = []
        self._numeric_checks: List[int] = []
        self.columns_without_contract_field = 0
        for index, name in enumerate(self.header):
            spec = fields.get(name)
            if spec is None:
                self.columns_without_contract_field += 1
                continue
            if spec.vendor_length is not None:
                self._length_checks.append((index, spec.vendor_length))
            if spec.vendor_type == "Numeric":
                self._numeric_checks.append(index)
        self._over_length: Dict[int, Finding] = {}
        self._not_decimal: Dict[int, Finding] = {}
        self._short, self._long = Finding(), Finding()
        self._quarantined = Finding()
        self.blank_lines = 0
        self.records_measured = 0
        self._lifted_limits = dict(lifted_limits)
        self._lifted_sources = dict(lifted_sources)
        self._lifted = {col: {"lifted": 0, "blank": 0, "longer_than_column": Finding(),
                              "not_a_valid_npi": 0} for col in self._lifted_limits}

    # ── per record ───────────────────────────────────────────────────────────
    def width_differs(self, record_number: int, cells: Sequence[str]) -> None:
        (self._short if len(cells) < self.width else self._long).add(record_number)

    def quarantine(self, record_number: int) -> None:
        """A record of another width that was NOT staged."""
        self._quarantined.add(record_number)

    @property
    def quarantined(self) -> int:
        return self._quarantined.count

    def measure(self, record_number: int, cells: Sequence[str]) -> None:
        """Per-column checks for a record whose width equals the header's (in a
        record of another width a cell cannot be attributed to a column)."""
        self.records_measured += 1
        for index, limit in self._length_checks:
            characters = len(cells[index])
            if characters > limit:
                finding = self._over_length.get(index)
                if finding is None:
                    finding = self._over_length[index] = Finding()
                finding.add(record_number)
                if characters > finding.max_characters:
                    finding.max_characters = characters
        for index in self._numeric_checks:
            cell = cells[index]
            if not cell or (cell.isascii() and cell.isdigit()):
                continue
            if cell.strip() and _PLAIN_DECIMAL.fullmatch(cell) is None:
                finding = self._not_decimal.get(index)
                if finding is None:
                    finding = self._not_decimal[index] = Finding()
                finding.add(record_number)

    def lifted(self, record_number: int, built: Dict[str, Any]) -> List[str]:
        """Record what the lifted identifiers of one accepted row are. Returns
        the lifted columns whose text does not fit the column."""
        too_long: List[str] = []
        for column, limit in self._lifted_limits.items():
            if column not in built:
                continue
            value = built[column]
            tally = self._lifted[column]
            if value is None:
                tally["blank"] += 1
                continue
            if len(value) > limit:
                tally["longer_than_column"].add(record_number)
                too_long.append(column)
            else:
                tally["lifted"] += 1
            if column == "npi" and not is_valid_npi(value):
                tally["not_a_valid_npi"] += 1
        return too_long

    # ── the stored summary ───────────────────────────────────────────────────
    def _columns(self, findings: Dict[int, Finding], *, with_length: bool) -> Dict[str, Any]:
        limits = dict(self._length_checks)
        rows = []
        for index in sorted(findings):
            f = findings[index]
            row = {"column": self.header[index], "position": index + 1, "count": f.count,
                   "record_numbers": list(f.records)}
            if with_length:
                row["vendor_length"] = limits[index]
                row["longest_characters"] = f.max_characters
            rows.append(row)
        return {"columns_affected": len(rows), "cells": sum(r["count"] for r in rows),
                "by_column": rows[:MAX_COLUMNS_REPORTED],
                "by_column_truncated": len(rows) > MAX_COLUMNS_REPORTED}

    def as_metadata(self, *, rows: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        lifted = {}
        for column, tally in self._lifted.items():
            over = tally["longer_than_column"]
            lifted[column] = {
                "source_column": self._lifted_sources.get(column),
                "column_characters": self._lifted_limits[column],
                "lifted": tally["lifted"], "blank": tally["blank"],
                "longer_than_column": over.count, "record_numbers": list(over.records)}
            if column == "npi":
                lifted[column]["not_a_valid_npi"] = tally["not_a_valid_npi"]
        out = {
            "validation_version": VALIDATION_VERSION,
            "scope": "full file: every record was read", "completed": True,
            "measured_against": {"contract_id": self.contract_id, "basis": self.basis,
                                 "columns_without_contract_field": self.columns_without_contract_field,
                                 "length_unit": "characters (the vendor layout states no unit)"},
            "rows": rows,
            "record_width": {
                "header_columns": self.width,
                "short_records": self._short.count, "short_record_numbers": list(self._short.records),
                "long_records": self._long.count, "long_record_numbers": list(self._long.records),
                "quarantined_records": self._quarantined.count,
                "quarantined_record_numbers": list(self._quarantined.records)},
            "blank_lines_skipped": self.blank_lines,
            "records_measured_per_column": self.records_measured,
            "over_vendor_length": self._columns(self._over_length, with_length=True),
            "numeric_not_plain_decimal": self._columns(self._not_decimal, with_length=False),
            "lifted_identifiers": lifted,
            "record_numbers": {"basis": "1-based data record ordinal; the header is not a record and "
                                        "blank lines are not counted",
                               "kept_per_finding": MAX_RECORD_NUMBERS},
            "values_recorded": False,
        }
        if extra:
            out.update(extra)
        return out
