"""OFFLINE prototype: screening against GSA's official daily public Exclusions extract.

STATUS: PROTOTYPE. Not imported by any request path, scheduler or connector; no flag enables it. It exists so that
candidate volumes can be measured (scripts/sam_extract_dry_run.py) and the behaviour reviewed BEFORE anyone decides to
use it. Enabling it in the application is a separate, explicitly approved change.

THE SOURCE
----------
`SAM_Exclusions_Public_Extract_V2_<yy><ddd>.ZIP`, refreshed daily, public, no key. Listing:
    https://sam.gov/api/prod/fileextractservices/v1/api/listfiles?domain=Exclusions/Public%20V2&privacy=Public
Download (303 to the file store):
    https://sam.gov/api/prod/fileextractservices/v1/api/download/Exclusions/Public%20V2/<file>?privacy=Public
It is served from `sam.gov`, NOT `api.sam.gov`, so it does not depend on the API gateway that has been returning an
empty 404 (investigation 2026-10-07). One CSV, 31 columns, all records Active. A record carries `NPI` (about 12% of
records), `Unique Entity ID` (about 93% of firms) and `CAGE` (about 5% of firms).

IDENTIFIER-FIRST, AND WHY ABSENCE BY IDENTIFIER IS WEAK
-------------------------------------------------------
Order: UEI -> NPI -> CAGE -> name. A hit by a strong identifier on ONE identity is CONFIRMED_MATCH (unless the names
flatly disagree, in which case it is POTENTIAL_MATCH with reason IDENTIFIER_NAME_DISAGREE). But most records lack most
identifiers, so "no record with this NPI" does NOT mean "not excluded": the search therefore always continues to the
name stage, and a name-only hit is never better than POTENTIAL_MATCH. A clean screen is NO_HIT (never a stronger word).

NOTHING IS DISCARDED
--------------------
Records are grouped into identities (a party listed by several agencies is ONE identity) but every record stays as an
`ExclusionAction` on that identity. Counts are reported for both identities and actions.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.Tefca import sam_screening as ss

EXTRACT_SCHEMA = "sam-exclusions-public-v2"

#: Columns the loader needs. Missing any of them is a hard failure (a changed layout must never read as "no hits").
REQUIRED_COLUMNS = ("Classification", "Name", "First", "Last", "City", "State / Province", "Zip Code",
                    "Unique Entity ID", "Exclusion Program", "Excluding Agency", "Exclusion Type",
                    "Active Date", "Termination Date", "Record Status", "SAM Number", "CAGE", "NPI")
OPTIONAL_COLUMNS = ("CT Code", "Cross-Reference", "Additional Comments", "Creation_Date")
ORG_CLASSES = ("Firm", "Special Entity Designation")

_FILE_RE = re.compile(r"SAM_Exclusions_Public_Extract_V2_(\d{2})(\d{3})", re.I)
_NPI_RE = re.compile(r"^\d{10}$")


class ExtractLayoutError(ValueError):
    """The file is not a readable exclusions extract. Never degrade to an empty index."""


def extract_date_from_name(name: str) -> Optional[date]:
    """`..._V2_26279.ZIP` -> 2026, day 279 -> 2026-10-06."""
    m = _FILE_RE.search(name or "")
    if not m:
        return None
    yy, ddd = int(m.group(1)), int(m.group(2))
    try:
        return date(2000 + yy, 1, 1) + timedelta(days=ddd - 1)
    except ValueError:
        return None


@dataclass
class ExtractMeta:
    file_name: str
    extract_date: Optional[str]
    sha256: str
    zip_bytes: int
    row_count: int
    active_rows: int
    columns: List[str]

    def anchor(self) -> Dict[str, Any]:
        """The dataset anchor recorded on evidence: which file, which day, which bytes."""
        return {"schema": EXTRACT_SCHEMA, "file": self.file_name, "extract_date": self.extract_date,
                "sha256": self.sha256, "rows": self.row_count}


@dataclass
class ScreenResult:
    outcome: str
    reason: str
    matched_by: str
    identities: List[ss.ExclusionIdentity] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    corroboration: Dict[str, bool] = field(default_factory=dict)

    @property
    def distinct_identities(self) -> int:
        return len(self.identities)

    @property
    def action_count(self) -> int:
        return sum(i.action_count for i in self.identities)

    def to_check_exclusions_data(self) -> Dict[str, Any]:
        """The shape `SAMGovConnector.check_exclusions` returns, plus the new fields, so a screener could sit behind
        the same interface later. `ambiguous` means MORE THAN ONE DISTINCT IDENTITY - several actions on one identity
        are not ambiguity."""
        return {
            "excluded": self.outcome in (ss.POTENTIAL_MATCH, ss.CONFIRMED_MATCH),
            "match_count": self.action_count,
            "matched_by": self.matched_by,
            "ambiguous": self.distinct_identities > 1,
            "screening_outcome": self.outcome,
            "screening_reason": self.reason,
            "distinct_identities": self.distinct_identities,
            "exclusions": [{"agency": a.agency, "type": a.exclusion_type, "program": a.program,
                            "active_date": a.active_date, "termination_date": a.termination_date}
                           for i in self.identities for a in i.actions][:25],
        }


class SamExtractIndex:
    """Parsed, validated and indexed extract. Built once, queried many times."""

    def __init__(self, meta: ExtractMeta, records: List[Dict[str, str]]):
        self.meta = meta
        self._records = records
        self._by_uei: Dict[str, List[int]] = {}
        self._by_npi: Dict[str, List[int]] = {}
        self._by_cage: Dict[str, List[int]] = {}
        self._by_name: Dict[str, List[int]] = {}
        self._by_person: Dict[str, List[int]] = {}
        from app.tefca_registry.entity_resolver import normalize_org_name
        self._norm = normalize_org_name
        for i, r in enumerate(records):
            if r["uei"]:
                self._by_uei.setdefault(r["uei"].upper(), []).append(i)
            if r["npi"]:
                self._by_npi.setdefault(r["npi"], []).append(i)
            if r["cage"]:
                self._by_cage.setdefault(r["cage"].upper(), []).append(i)
            if r["classification"] in ORG_CLASSES and r["name"]:
                self._by_name.setdefault(self._norm(r["name"]), []).append(i)
            elif r["classification"] == "Individual":
                self._by_person.setdefault(self._person_key(r["last"], r["first"]), []).append(i)

    @staticmethod
    def _person_key(last: str, first: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", f"{(last or '').lower()} {(first or '').lower()}").strip()

    # -- lookup ---------------------------------------------------------------
    def _identities(self, idxs: List[int]) -> List[ss.ExclusionIdentity]:
        return ss.group_identities((self._records[i] for i in idxs), normalize_name=self._norm)

    def screen(self, *, uei: str = "", npi: str = "", cage: str = "", name: str = "", first: str = "",
               last: str = "", state: str = "", zip5: str = "", kind: str = "organization") -> ScreenResult:
        """Identifier-first screen of ONE entity. Never raises on a miss; a miss is NO_HIT."""
        anchor = self.meta.anchor()
        tried: List[str] = []
        for ident_kind, value, table, normalise in (
                ("uei", (uei or "").strip().upper(), self._by_uei, lambda v: v),
                ("npi", (npi or "").strip(), self._by_npi, lambda v: v if _NPI_RE.match(v) else ""),
                ("cage", (cage or "").strip().upper(), self._by_cage, lambda v: v)):
            value = normalise(value)
            if not value:
                continue
            tried.append(ident_kind)
            idxs = table.get(value)
            if idxs:
                return self._finish(idxs, ident_kind, name=name, first=first, last=last, state=state,
                                    zip5=zip5, kind=kind, anchor=anchor, tried=tried)
        # strong identifiers absent or silent: ABSENCE BY IDENTIFIER IS WEAK, so always search by name too.
        if kind == "individual":
            key = self._person_key(last, first)
            idxs = self._by_person.get(key) if key else None
        else:
            key = self._norm(name) if name else ""
            idxs = self._by_name.get(key) if key else None
        if key:
            tried.append("name")
        if not tried:
            prov = ss.build_provenance(leg="exclusion", channel="DAILY_EXTRACT", endpoint_version=EXTRACT_SCHEMA,
                                       identifier_used="none", query_mode="none", dataset_anchor=anchor,
                                       failure={"failure_class": ss.NO_IDENTIFIER,
                                                "failure_reason": "no usable identifier or name to search on"})
            return ScreenResult(ss.INCOMPLETE, "NO_USABLE_IDENTIFIER", "none", provenance=prov)
        if idxs:
            return self._finish(idxs, "name", name=name, first=first, last=last, state=state, zip5=zip5,
                                kind=kind, anchor=anchor, tried=tried)
        prov = ss.build_provenance(leg="exclusion", channel="DAILY_EXTRACT", endpoint_version=EXTRACT_SCHEMA,
                                   identifier_used=tried[0] if tried else "name",
                                   query_mode="exact_then_name", records_returned=0, total_records=0,
                                   distinct_identities=0, truncated=False, dataset_anchor=anchor)
        prov["identifiers_tried"] = tried
        outcome, reason = ss.classify_outcome(answered=True, record_count=0)
        return ScreenResult(outcome, reason, "name", provenance=prov)

    def _finish(self, idxs: List[int], matched_by: str, *, name: str, first: str, last: str, state: str,
                zip5: str, kind: str, anchor: Dict[str, Any], tried: List[str]) -> ScreenResult:
        identities = self._identities(idxs)
        outcome, reason = ss.classify_outcome(answered=True, record_count=len(idxs),
                                              distinct_identities=len(identities), matched_by=matched_by)
        corroboration: Dict[str, bool] = {}
        if state:
            corroboration["state"] = any(state.strip().upper() in i.states for i in identities)
        if matched_by in ss.STRONG_IDENTIFIERS and outcome == ss.CONFIRMED_MATCH:
            # a strong identifier whose holder's NAME flatly disagrees is not a confirmation
            want = self._norm(name) if kind != "individual" else self._person_key(last, first)
            have = {(self._norm(n) if kind != "individual" else self._person_key("", n)) for i in identities
                    for n in i.names} if want else set()
            if want and have and want not in have and not any(want in h or h in want for h in have if h):
                outcome, reason = ss.POTENTIAL_MATCH, "IDENTIFIER_NAME_DISAGREE"
        prov = ss.build_provenance(leg="exclusion", channel="DAILY_EXTRACT", endpoint_version=EXTRACT_SCHEMA,
                                   identifier_used=matched_by, query_mode="exact" if matched_by != "name" else "name_exact_normalized",
                                   records_returned=len(idxs), total_records=len(idxs),
                                   distinct_identities=len(identities), truncated=False, dataset_anchor=anchor)
        prov["identifiers_tried"] = tried
        return ScreenResult(outcome, reason, matched_by, identities=identities, provenance=prov,
                            corroboration=corroboration)


# ── loading ──────────────────────────────────────────────────────────────────

def _open_csv_text(path: Path) -> Tuple[str, str, int]:
    """(csv_text, inner_name, zip_bytes). Reads through a ZIP or a bare CSV, with a size sanity bound."""
    raw = path.read_bytes()
    if path.suffix.lower() == ".zip":
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise ExtractLayoutError(f"not a zip file: {exc}") from exc
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ExtractLayoutError(f"expected exactly one CSV in the zip, found {names}")
        info = z.getinfo(names[0])
        if info.file_size > 600 * 1024 * 1024:
            raise ExtractLayoutError("CSV larger than the 600 MB sanity bound")
        return z.read(names[0]).decode("utf-8-sig", "replace"), names[0], len(raw)
    return raw.decode("utf-8-sig", "replace"), path.name, len(raw)


def load_extract(path: Path, *, min_rows: int = 1) -> SamExtractIndex:
    """Parse and validate the extract. Raises `ExtractLayoutError` on ANY structural problem, so a changed or
    truncated file can never be mistaken for 'nobody is excluded'."""
    path = Path(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    text, inner, zbytes = _open_csv_text(path)
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ExtractLayoutError("empty file") from exc
    pos = {h.strip(): i for i, h in enumerate(header)}
    missing = [c for c in REQUIRED_COLUMNS if c not in pos]
    if missing:
        raise ExtractLayoutError(f"required columns missing: {missing}")

    def cell(row: List[str], name: str) -> str:
        i = pos.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    records: List[Dict[str, str]] = []
    active = 0
    for row in reader:
        if not any(row):
            continue
        if len(row) < len(REQUIRED_COLUMNS):          # a row shorter than the layout is corruption, not data
            raise ExtractLayoutError(f"row {len(records) + 2} has {len(row)} fields")
        rec = {"classification": cell(row, "Classification"), "name": cell(row, "Name"),
               "first": cell(row, "First"), "last": cell(row, "Last"), "city": cell(row, "City"),
               "state": cell(row, "State / Province"), "zip": cell(row, "Zip Code"),
               "uei": cell(row, "Unique Entity ID"), "program": cell(row, "Exclusion Program"),
               "agency": cell(row, "Excluding Agency"), "exclusion_type": cell(row, "Exclusion Type"),
               "ct_code": cell(row, "CT Code"), "active_date": cell(row, "Active Date"),
               "termination_date": cell(row, "Termination Date"), "record_status": cell(row, "Record Status"),
               "sam_number": cell(row, "SAM Number"), "cross_reference": cell(row, "Cross-Reference"),
               "additional_comments": cell(row, "Additional Comments"), "cage": cell(row, "CAGE"),
               "npi": cell(row, "NPI")}
        if rec["record_status"].lower() == "active":
            active += 1
        records.append(rec)
    if len(records) < min_rows:
        raise ExtractLayoutError(f"only {len(records)} records (minimum {min_rows})")
    d = extract_date_from_name(inner) or extract_date_from_name(path.name)
    meta = ExtractMeta(file_name=path.name, extract_date=d.isoformat() if d else None, sha256=sha,
                       zip_bytes=zbytes, row_count=len(records), active_rows=active, columns=list(header))
    return SamExtractIndex(meta, records)
