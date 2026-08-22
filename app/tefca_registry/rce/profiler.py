
"""
P0 — read-only profiling of an RCE delivery.

READ-ONLY BY CONSTRUCTION. This module opens a file, reads bytes, and returns a
report. It has no write path, no database dependency, and no import path. It
cannot alter the delivery it profiles because it has nothing to alter it with.

WHAT A PROFILE IS FOR
Every mapping decision, severity threshold and applicability rule downstream is
supposed to rest on what the file actually contains rather than on what a column
name suggests. This produces that evidence, in counted form, before anything is
ingested — so `field_map.py` can cite numbers instead of assumptions.

THE THREE-LAYER SEPARATION
A profile reports OBSERVED DATA FACTS only. It never states what a field means
in TEFCA and never states how DocuAction will use it. Those live in
`field_map.py` as `documented` and `docuaction`, deliberately in a different
file, so that reading a profile cannot accidentally read an interpretation as a
measurement.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.tefca_registry.rce.reader import (
    DeliveryRead,
    MOJIBAKE_MARKERS,
    PARSE_OK,
    read_delivery,
)

#: USPS state and territory codes. Used to COUNT how many values fall outside
#: the set — never to correct one.
USPS_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY "
    "DC PR VI GU AS MP".split()
)

#: Name patterns that look like test artefacts. A HEURISTIC, and counted as one:
#: the profile reports how many matched and which, so a human decides.
TEST_NAME_PATTERN = re.compile(
    r"(^|[\s\-_])test([\s\-_]|$)|doa[-_]test|donotuse|do[-_]not[-_]use|dummy",
    re.IGNORECASE,
)


@dataclass
class ColumnProfile:
    """Observed facts about one column. No interpretation."""

    name: str
    ordinal: int
    total: int
    populated: int
    empty: int
    distinct: int
    min_length: int
    max_length: int
    detected_type: str
    samples: List[str] = field(default_factory=list)
    top_values: List[Tuple[str, int]] = field(default_factory=list)
    mojibake_cells: int = 0
    embedded_tab_cells: int = 0
    leading_trailing_space_cells: int = 0
    suspicious: List[str] = field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        return round(self.populated / self.total * 100, 2) if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "ordinal": self.ordinal, "total": self.total,
            "populated": self.populated, "empty": self.empty,
            "coverage_pct": self.coverage_pct, "distinct": self.distinct,
            "min_length": self.min_length, "max_length": self.max_length,
            "detected_type": self.detected_type, "samples": list(self.samples),
            "top_values": [list(t) for t in self.top_values],
            "mojibake_cells": self.mojibake_cells,
            "embedded_tab_cells": self.embedded_tab_cells,
            "leading_trailing_space_cells": self.leading_trailing_space_cells,
            "suspicious": list(self.suspicious),
        }


_OID_RE = re.compile(r"^[0-9]+(\.[0-9]+)+$")
_UUID_URN_RE = re.compile(r"^urn:uuid:[0-9a-fA-F-]{36}$")
_OID_URN_RE = re.compile(r"^urn:oid:[0-9.]+$")
_INT_RE = re.compile(r"^-?[0-9]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _detect_type(values: List[str]) -> str:
    """A structural label for the populated values. Descriptive, not binding."""
    if not values:
        return "empty"
    sample = values[: min(len(values), 4000)]

    def ratio(predicate) -> float:
        return sum(1 for v in sample if predicate(v)) / len(sample)

    if ratio(lambda v: _UUID_URN_RE.match(v) is not None) > 0.95:
        return "urn:uuid"
    if ratio(lambda v: _OID_URN_RE.match(v) is not None) > 0.95:
        return "urn:oid"
    if ratio(lambda v: _OID_RE.match(v) is not None) > 0.95:
        return "oid"
    if ratio(lambda v: _EMAIL_RE.match(v) is not None) > 0.9:
        return "email"
    if ratio(lambda v: _INT_RE.match(v) is not None) > 0.95:
        return "integer-like"
    if len({len(v) for v in sample}) == 1:
        return f"fixed-width({len(sample[0])})"
    return "text"


def profile_column(name: str, ordinal: int, raw_values: List[str]) -> ColumnProfile:
    stripped = [v.strip() for v in raw_values]
    populated = [v for v in stripped if v]
    lengths = [len(v) for v in populated] or [0]
    counter = collections.Counter(populated)

    suspicious: List[str] = []
    if populated and len(counter) == 1 and len(populated) < len(raw_values) * 0.05:
        suspicious.append(
            f"single distinct value {next(iter(counter))!r} on only "
            f"{len(populated)} of {len(raw_values)} rows")
    dominant, dominant_n = (counter.most_common(1) or [(None, 0)])[0]
    if dominant and dominant_n > len(raw_values) * 0.5 and len(counter) > 1:
        suspicious.append(
            f"value {dominant!r} occupies {dominant_n} of {len(raw_values)} rows "
            f"({round(dominant_n / len(raw_values) * 100, 1)}%)")
    if populated and min(lengths) != max(lengths) and max(lengths) - min(lengths) > 30:
        suspicious.append(f"length varies widely ({min(lengths)}–{max(lengths)})")

    return ColumnProfile(
        name=name, ordinal=ordinal, total=len(raw_values),
        populated=len(populated), empty=len(raw_values) - len(populated),
        distinct=len(counter), min_length=min(lengths), max_length=max(lengths),
        detected_type=_detect_type(populated),
        samples=[v[:80] for v in populated[:5]],
        top_values=counter.most_common(8),
        mojibake_cells=sum(1 for v in stripped
                           if any(m in v for m in MOJIBAKE_MARKERS)),
        embedded_tab_cells=sum(1 for v in raw_values if "\t" in v),
        leading_trailing_space_cells=sum(1 for v in raw_values
                                         if v != v.strip() and v.strip()),
        suspicious=suspicious,
    )


@dataclass
class DeliveryProfile:
    """The complete P0 result for one delivery."""

    file_facts: Dict[str, Any]
    columns: List[ColumnProfile]
    integrity: Dict[str, Any]
    populations: Dict[str, Any]
    anomalies: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_facts": self.file_facts,
            "columns": [c.to_dict() for c in self.columns],
            "integrity": self.integrity,
            "populations": self.populations,
            "anomalies": self.anomalies,
        }


def profile_delivery(raw: bytes, *, filename: Optional[str] = None,
                     declared_delimiter: Optional[str] = None) -> DeliveryProfile:
    """Profile a delivery from its bytes. Nothing is written anywhere."""
    read: DeliveryRead = read_delivery(raw, declared_delimiter=declared_delimiter)
    headers = read.headers
    lines = read.lines

    columns = [
        profile_column(name, ordinal,
                       [line.parsed.get(name, "") for line in lines])
        for ordinal, name in enumerate(headers)
    ]
    by_name = {c.name: c for c in columns}

    def values(column: str) -> List[str]:
        return [line.get(column) for line in lines]

    def present(column: str) -> List[str]:
        return [v for v in values(column) if v]

    # ── integrity: which columns are actually unique ──
    integrity: Dict[str, Any] = {}
    for key in ("id", "TEFCAID", "HCID", "AAID", "NPI"):
        if key not in by_name:
            continue
        found = present(key)
        counts = collections.Counter(found)
        repeated = {v: n for v, n in counts.items() if n > 1}
        integrity[key] = {
            "populated": len(found),
            "distinct": len(counts),
            "repeated_values": len(repeated),
            "extra_rows_from_repeats": sum(repeated.values()) - len(repeated),
            "is_unique": not repeated and len(found) == len(lines),
            "worst_repeat": max(repeated.values()) if repeated else 0,
            "worst_repeat_value": (max(repeated, key=repeated.get)
                                   if repeated else None),
        }

    # ── populations ──
    populations: Dict[str, Any] = {
        "total_records": len(lines),
        "parse_ok": read.ok_count,
        "parse_malformed": read.malformed_count,
    }
    if "NPI" in by_name:
        populations["npi_populated"] = by_name["NPI"].populated
        populations["npi_empty"] = by_name["NPI"].empty
    if "sequoiaorgtype" in by_name:
        populations["sequoiaorgtype"] = dict(
            collections.Counter(present("sequoiaorgtype")))
    if "active" in by_name:
        populations["active"] = dict(collections.Counter(present("active")))
        if "sequoiaorgtype" in by_name:
            populations["active_by_type"] = {
                f"{a}|{t}": n for (a, t), n in collections.Counter(
                    (line.get("active"), line.get("sequoiaorgtype"))
                    for line in lines).items()
            }
    if "orgManagingOrg" in by_name:
        qhins = collections.Counter(present("orgManagingOrg"))
        populations["qhin_count"] = len(qhins)
        populations["qhin_distribution"] = dict(qhins.most_common())
    if "HCID" in by_name:
        populations["hcid_coverage_pct"] = by_name["HCID"].coverage_pct
    if "purposesofuse" in by_name:
        tokens = collections.Counter(
            token for v in present("purposesofuse")
            for token in (t.strip() for t in v.split(",")) if token)
        populations["purposesofuse_coverage_pct"] = by_name["purposesofuse"].coverage_pct
        populations["purposesofuse_missing"] = by_name["purposesofuse"].empty
        populations["purposesofuse_tokens"] = dict(tokens.most_common())
        populations["purposesofuse_combinations"] = dict(
            collections.Counter(present("purposesofuse")).most_common(20))
    contact_columns = [c for c in headers if c.startswith("contact_")]
    populations["contact_coverage"] = {
        c: by_name[c].coverage_pct for c in contact_columns if c in by_name}

    # ── anomalies ──
    anomalies: Dict[str, Any] = {}

    anomalies["mojibake_cells_total"] = read.mojibake_cells
    anomalies["mojibake_columns"] = {
        c.name: c.mojibake_cells for c in columns if c.mojibake_cells}
    anomalies["embedded_tab_cells_total"] = read.embedded_tab_cells
    anomalies["embedded_tab_columns"] = {
        c.name: c.embedded_tab_cells for c in columns if c.embedded_tab_cells}
    anomalies["embedded_tab_locations"] = [
        {"line_number": line.line_number, "column": name}
        for line in lines for name in headers if "\t" in line.parsed.get(name, "")
    ][:50]

    if "NPI" in by_name:
        npis = present("NPI")
        wrong_length = [n for n in npis if len(n) != 10]
        non_digit = [n for n in npis if not n.isdigit()]
        anomalies["npi"] = {
            "wrong_length_count": len(wrong_length),
            "wrong_length_samples": wrong_length[:10],
            "non_digit_count": len(non_digit),
            "non_digit_samples": non_digit[:10],
            "length_distribution": dict(
                collections.Counter(len(n) for n in npis).most_common()),
        }
        try:
            from app.services.npi_validator import validate_npi
            ten = [n for n in npis if len(n) == 10 and n.isdigit()]
            failing = [n for n in ten if not validate_npi(n)[0]]
            anomalies["npi"]["check_digit_failures"] = len(failing)
            anomalies["npi"]["check_digit_failure_samples"] = failing[:10]
        except Exception:  # noqa: BLE001 — profiling must not depend on it
            anomalies["npi"]["check_digit_failures"] = None

    if "address_postalCode" in by_name:
        zips = present("address_postalCode")
        short = [z for z in zips if len(z) < 5]
        anomalies["zip"] = {
            "shorter_than_five": len(short),
            "length_distribution": dict(
                collections.Counter(len(z) for z in zips).most_common()),
            "samples_short": short[:10],
            "non_numeric": [z for z in zips if not z.replace("-", "").isdigit()][:10],
        }

    if "address_state" in by_name:
        states = collections.Counter(present("address_state"))
        anomalies["state"] = {
            "distinct": len(states),
            "outside_usps_set": {s: n for s, n in states.items()
                                 if s not in USPS_STATES},
        }

    if "name" in by_name:
        matches = [line.get("name") for line in lines
                   if TEST_NAME_PATTERN.search(line.get("name") or "")]
        anomalies["test_records"] = {
            "count": len(matches),
            "names": matches[:25],
            "note": ("Heuristic name match. Flagged for analyst determination; "
                     "no record is excluded on this basis."),
        }

    # ── hierarchy ──
    if {"id", "partOf", "orgManagingOrg", "sequoiaorgtype"} <= set(headers):
        all_ids = set(present("id"))
        part_of = values("partOf")
        managing = values("orgManagingOrg")
        anomalies["hierarchy"] = {
            "partof_populated": sum(1 for p in part_of if p),
            "partof_distinct": len(set(p for p in part_of if p)),
            "partof_resolves_in_file": sum(1 for p in part_of if p in all_ids),
            "orgmanagingorg_resolves_in_file": sum(
                1 for m in managing if m in all_ids),
            "partof_equals_orgmanagingorg_by_type": {
                f"{t}|{same}": n for (t, same), n in collections.Counter(
                    (line.get("sequoiaorgtype"),
                     line.get("partOf") == line.get("orgManagingOrg"))
                    for line in lines).items()
            },
        }

    file_facts = dict(read.summary())
    file_facts["filename"] = filename
    return DeliveryProfile(
        file_facts=file_facts, columns=columns,
        integrity=integrity, populations=populations, anomalies=anomalies,
    )


def profile_path(path: str, *, declared_delimiter: Optional[str] = None
                 ) -> DeliveryProfile:
    """Profile a delivery on disk. Opened 'rb' — the file is never written."""
    import os

    with open(path, "rb") as handle:
        raw = handle.read()
    return profile_delivery(raw, filename=os.path.basename(path),
                            declared_delimiter=declared_delimiter)
