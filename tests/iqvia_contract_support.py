"""Synthetic IQVIA intake contracts and extracts for the tests.

The vendor layouts are not in this repository. The contracts used here are
INVENTED: each has the processing keys the importer already names in public
code plus made-up columns (`SYN_*`), with made-up lengths. They are generated
in a temporary directory by the same script that registers a real contract
(`scripts/iqvia/build_layout_contract.py`), and the registry is pointed at that
directory through the `IQVIA_CONTRACTS_DIR` setting.

Tests that need the real layouts run only when a private contracts directory is
named by `IQVIA_PRIVATE_CONTRACTS_DIR_FOR_TESTS`; nothing about those layouts is
written in any test file.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.tefca_registry.rce import iqvia_contract_registry as cr
from app.tefca_registry.rce import snapshot_models as sm

HCO, HCP, AFFIL = sm.SOURCE_IQVIA_HCO, sm.SOURCE_IQVIA_HCP, sm.SOURCE_IQVIA_AFFILIATION
SOURCES = (HCO, HCP, AFFIL)
EDITION_1, EDITION_2 = "2031-01-15", "2031-03-15"
PRIVATE_DIR_ENV = "IQVIA_PRIVATE_CONTRACTS_DIR_FOR_TESTS"

_T, _N = "Text", "Numeric"
#: (name, vendor type, vendor length). Lengths are invented.
SYNTHETIC_LAYOUTS: Dict[str, List[Tuple[str, str, Optional[int]]]] = {
    HCO: [("HCO_HCE_ID", _N, None), ("ORG_NPI", _T, 12), ("ORG_CCN_ID", _T, 30),
          ("BUS_NM", _T, 40), ("ZIP5_CD", _T, 5), ("SYN_NOTE", _T, 8), ("SYN_CODE", _T, 3),
          ("SYN_COUNT", _N, None), ("SYN_BIG_ID", _N, None), ("SYN_LONG_LIST", _T, 5000)],
    HCP: [("HCP_HCE_ID", _N, None), ("ADDR_ID", _N, None), ("NPI", _T, 12),
          ("CITY_NM", _T, 20), ("ST_CD", _T, 2), ("ZIP5_CD", _T, 5), ("SYN_NOTE", _T, 8),
          ("SYN_COUNT", _N, None)],
    AFFIL: [("HCP_HCE_ID", _N, None), ("HCO_HCE_ID", _N, None), ("AFFL_TYP_ID", _N, None),
            ("TITL_TYP_ID", _N, None), ("NPI", _T, 12), ("ORG_NPI", _T, 12), ("ORG_CCN_ID", _T, 30),
            ("ADDR_ID", _N, None), ("CITY_NM", _T, 20), ("ST_CD", _T, 2), ("ZIP5_CD", _T, 5),
            ("SYN_UNSTORED_NOTE", _T, 8), ("SYN_UNSTORED_COUNT", _N, None),
            ("SYN_UNSTORED_LIST", _T, 5000)],
}
#: The second synthetic edition adds these to the first.
SYNTHETIC_ADDED_IN_EDITION_2 = {
    HCO: [("SYN_ADDED_ONE", _T, 10), ("SYN_ADDED_TWO", _N, None)],
    HCP: [],                                                  # identical layout, two editions
    AFFIL: [("SYN_UNSTORED_ADDED", _T, 10)],
}

_NPI_COLUMNS = ("ORG_NPI", "NPI")
_KEY_COLUMNS = ("HCO_HCE_ID", "HCP_HCE_ID", "ADDR_ID")


def _generator():
    path = Path(__file__).resolve().parents[1] / "scripts" / "iqvia" / "build_layout_contract.py"
    spec = importlib.util.spec_from_file_location("build_layout_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_layout_csv(path: Path, columns: Sequence[Tuple[str, str, Optional[int]]]) -> Path:
    """A vendor-style layout file (metadata: one line per column)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["column_id", "Column_Name", "Field_Description", "Attribute_Of", "Data_Type",
                     "Data_Length", "Field_Definition"])
    for ordinal, (name, vtype, vlen) in enumerate(columns, start=1):
        writer.writerow([ordinal, name, f"synthetic {name}", "Synthetic", vtype,
                         "" if vlen is None else vlen, "invented for the tests"])
    path.write_bytes(out.getvalue().encode("utf-8"))
    return path


def build_synthetic_contracts(directory: Path, *, sources: Sequence[str] = SOURCES,
                              editions: Sequence[str] = (EDITION_1, EDITION_2)) -> Path:
    """Register the synthetic layouts in `directory` with the real generator."""
    generator = _generator()
    directory.mkdir(parents=True, exist_ok=True)
    work = directory.parent / (directory.name + "-layouts")
    work.mkdir(exist_ok=True)
    for source in sources:
        for edition in editions:
            columns = list(SYNTHETIC_LAYOUTS[source])
            if edition == EDITION_2:
                columns += SYNTHETIC_ADDED_IN_EDITION_2[source]
            layout = write_layout_csv(work / f"{source}_{edition}_layout.csv", columns)
            generator.register(layout, source, edition, directory)
    return directory


def use_contracts(monkeypatch, directory) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, cr.DIRECTORY_SETTING, "" if directory is None else str(directory),
                        raising=False)


def private_contracts_dir() -> Optional[Path]:
    raw = (os.environ.get(PRIVATE_DIR_ENV) or "").strip()
    return Path(raw) if raw and Path(raw).is_dir() else None


def contract_for(source_system: str, edition: str) -> cr.Contract:
    for contract in cr.get_registry().contracts(source_system):
        if edition in contract.editions:
            return contract
    raise AssertionError(f"no registered contract for {source_system} {edition}")


def synthetic_npi(n: int) -> str:
    """A ten-digit identifier with a correct check digit, from a counter."""
    body = f"1{n % 100_000_000:08d}"
    total = 24                                   # the constant for the 80840 prefix
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


def field_value(field: cr.ContractField, record: int, *, fill: bool = False) -> str:
    """A deterministic value for one cell. `fill=True` pads a Text value to
    exactly its vendor length."""
    if field.name in _NPI_COLUMNS:
        return synthetic_npi(record * 7 + field.ordinal)
    if field.name in _KEY_COLUMNS:
        return str(90_000_000 + record * 1_000 + field.ordinal)
    if field.name == "ORG_CCN_ID":
        return f"0{record % 100_000:05d}"                    # leading zero
    if field.name == "ZIP5_CD":
        return f"0{record % 10_000:04d}"                     # leading zero
    if field.vendor_type == "Numeric":
        return str(1_000_000 + record * 1_000 + field.ordinal)
    text = f"S{field.ordinal}R{record}"
    limit = field.vendor_length
    if limit is None:
        return text
    if fill:
        return (text + "x" * limit)[:limit]
    return text[:limit]


def contract_row(contract: cr.Contract, record: int, *, fill: bool = False,
                 overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    row = {f.name: field_value(f, record, fill=fill) for f in contract.fields}
    row.update(overrides or {})
    return row


def csv_bytes(header: Sequence[str], rows: Iterable[Sequence[str]], *, bom: bool = False,
              lineterminator: str = "\r\n") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator=lineterminator)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return (b"\xef\xbb\xbf" if bom else b"") + out.getvalue().encode("utf-8")


def write_contract_file(path, contract: cr.Contract, rows: Sequence[Dict[str, str]], *,
                        header: Optional[Sequence[str]] = None, bom: bool = False):
    """Rows (dicts keyed by column name) written under `header` (default: the
    contract columns in contract order)."""
    names = list(header) if header is not None else list(contract.names)
    path.write_bytes(csv_bytes(names, [[r.get(n, "") for n in names] for r in rows], bom=bom))
    return path


def write_cells(path, header: Sequence[str], records: Sequence[Sequence[str]], *, bom: bool = False):
    """Records given cell by cell, so a record may be shorter or longer than the header."""
    path.write_bytes(csv_bytes(header, records, bom=bom))
    return path


IMPORTERS = {HCO: "import_hco_csv", HCP: "import_hcp_csv", AFFIL: "import_affiliation_csv"}
MODELS = {HCO: sm.IqviaHcoObservation, HCP: sm.IqviaHcpObservation,
          AFFIL: sm.IqviaAffiliationObservation}


def all_strings(value) -> List[str]:
    """Every string anywhere inside a JSON-like value, keys included."""
    found: List[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            found.extend(all_strings(k))
            found.extend(all_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            found.extend(all_strings(v))
    return found
