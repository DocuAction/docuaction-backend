"""SAM.gov public exclusions extract -- a minimal, flag-gated screening
connector modeled directly on `OIGLEIEConnector` (app/Tefca/connectors.py).

WHY A SEPARATE EXTRACT CONNECTOR, AND WHY IT IS NOT WIRED IN ANYWHERE YET
──────────────────────────────────────────────────────────────────────────
`SAMGovConnector` (connectors.py) targets `api.sam.gov`, which requires a key
this deployment does not have; it is correctly reported UNAVAILABLE, not a
bug. GSA separately publishes the SAME underlying exclusions data as a daily,
key-less CSV extract ("Exclusions/Public V2") via a documented listfiles +
signed-download API. This module reads THAT extract, the same way
`_load_leie_csv` reads OIG's CSV: mandatory schema preflight, NPI + normalized
name indexing, 24h TTL, single-flight load.

This is prepared for review, not activated. `ENABLE_SAM_EXTRACT_SCREENING`
(default OFF) gates every network call this module makes -- with it unset,
`lookup_by_npi`/`lookup_by_name` return UNAVAILABLE immediately, no fetch
attempted. No caller in `evidence_service.py`, `arc_pipeline.py`, or anywhere
else in the live pipeline has been changed to invoke this module. Wiring it
in, and deciding the flag's deployed value, is a separate, later decision.

CANDIDATE, NEVER EXCLUSION
───────────────────────────
A hit against this extract is `candidate_match` -- distinct from LEIE's/SAM
API's `excluded`. Matching here (NPI or normalized business name) is exactly
the same class of safeguard as `OIGLEIEConnector.lookup_by_name`: a punctuation
or designator variant must not hide a candidate, and a match must never
collapse into an automatic exclusion finding. A person adjudicates every hit.
"""

from __future__ import annotations

import asyncio
import csv as _csv
import hashlib
import io as _io
import logging
import os
import time as _time
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

from app.Tefca.connectors import HTTP_HEADERS, SourceResult, normalize_org_name
from app.services.npi_validator import npi_rejection_reason

logger = logging.getLogger(__name__)

#: Off by default -- see the module docstring. Set to a truthy value ("1",
#: "true", "yes") to let this module actually fetch and screen anything.
ENV_FLAG = "ENABLE_SAM_EXTRACT_SCREENING"

_SAM_LISTFILES_URL = "https://sam.gov/api/prod/fileextractservices/v1/api/listfiles"
_SAM_DOMAIN = "Exclusions/Public V2"
_SAM_DOWNLOAD_ROOT = "https://sam.gov/api/prod/fileextractservices/v1/api/download"
_SAM_TTL_SECONDS = 86400  # the source itself refreshes daily

API_VERSION = "GSA-Public-Extract-V2"

#: The 31-column schema recorded in
#: qa-evidence/2026-10-28-october-readiness/sam_extract_preserved/
#: PRESERVATION_AND_INTEGRATION_PLAN_2026-10-28.md, verbatim. Required by
#: exact name -- no positional fallback, no silent renaming.
SAM_EXTRACT_REQUIRED_COLUMNS = (
    "Classification", "Name", "Prefix", "First", "Middle", "Last", "Suffix",
    "Address 1", "Address 2", "Address 3", "Address 4", "City",
    "State / Province", "Country", "Zip Code", "Open Data Flag",
    "Blank (Deprecated)", "Unique Entity ID", "Exclusion Program",
    "Excluding Agency", "CT Code", "Exclusion Type", "Additional Comments",
    "Active Date", "Termination Date", "Record Status", "Cross-Reference",
    "SAM Number", "CAGE", "NPI", "Creation_Date",
)
SAM_EXTRACT_SCHEMA_VERSION = "gsa-sam-exclusions-public-v2/required-columns-v1"

_SAM_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "by_npi": {}, "by_name": {},
                               "row_count": 0, "edition_date": None, "sha256": None,
                               "source_url": None}
_SAM_LOAD_LOCK: Optional[asyncio.Lock] = None


def sam_extract_schema_problem(fieldnames) -> Optional[str]:
    """Why a downloaded body is NOT a usable SAM exclusions extract, or None.

    Modeled exactly on `leie_schema_problem`: a mismatch here must REFUSE the
    body, never silently index zero usable rows as a clean list.
    """
    names = [str(f).strip() for f in (fieldnames or [])]
    if not names:
        return "empty body: no header row"
    missing = [c for c in SAM_EXTRACT_REQUIRED_COLUMNS if c not in names]
    if missing:
        return (f"header does not match the preserved extract schema; missing "
                f"column(s): {', '.join(missing)} (first header cell: {names[0][:40]!r})")
    dupes = sorted({n for n in names if names.count(n) > 1 and n})
    if dupes:
        return f"duplicate header column(s): {', '.join(dupes)}"
    return None


def _sam_cache_fresh(now: float) -> bool:
    return (_SAM_CACHE["row_count"] > 0
            and (now - _SAM_CACHE["loaded_at"]) < _SAM_TTL_SECONDS)


async def _find_current_extract_file(client: httpx.AsyncClient) -> Optional[str]:
    """Ask the listfiles API which file is current today. Returns the exact
    filename, or None if the listing itself failed/was unusable."""
    try:
        resp = await client.get(_SAM_LISTFILES_URL,
                                 params={"domain": _SAM_DOMAIN, "privacy": "Public"},
                                 headers=HTTP_HEADERS)
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("SAM extract listfiles failed: %s", e)
        return None
    # The API's exact response shape is not pinned here beyond "it has
    # file entries with names" -- tolerate the two most likely shapes rather
    # than assume one, but refuse (return None) if neither is present.
    entries = payload if isinstance(payload, list) else payload.get("files") or payload.get("data") or []
    names = [e.get("name") or e.get("fileName") for e in entries if isinstance(e, dict)]
    names = [n for n in names if n]
    if not names:
        return None
    # Exactly one Public V2 extract file is expected; if more than one is
    # listed, take the lexicographically last (filenames carry a date-coded
    # suffix) rather than guess -- this is diagnosable from the cache's
    # recorded source_url if it ever matters.
    return sorted(names)[-1]


async def _load_sam_extract() -> bool:
    """Download + index the SAM public extract if the cache is empty or
    stale. Returns False (fail-closed) if it cannot be retrieved or fails
    the schema preflight. Concurrent callers on a cold cache share ONE load,
    same single-flight pattern as `_ensure_leie_loaded`."""
    global _SAM_LOAD_LOCK
    now = _time.time()
    if _sam_cache_fresh(now):
        return True
    if _SAM_LOAD_LOCK is None:
        _SAM_LOAD_LOCK = asyncio.Lock()
    async with _SAM_LOAD_LOCK:
        if _sam_cache_fresh(_time.time()):
            return True
        return await _fetch_and_index_sam_extract()


async def _fetch_and_index_sam_extract() -> bool:
    now = _time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
            filename = await _find_current_extract_file(client)
            if not filename:
                logger.warning("SAM extract: could not determine current file from listfiles")
                return False
            download_url = f"{_SAM_DOWNLOAD_ROOT}/{quote(_SAM_DOMAIN)}/{quote(filename)}"
            resp = await client.get(download_url, params={"privacy": "Public"}, headers=HTTP_HEADERS)
            if resp.status_code != 200:
                logger.warning("SAM extract download failed: HTTP %s", resp.status_code)
                return False
            raw_bytes = resp.content
            edition_date = resp.headers.get("Last-Modified")
            source_url = str(resp.request.url) if resp.request else download_url
    except Exception as e:  # noqa: BLE001
        logger.warning("SAM extract fetch failed: %s", e)
        return False

    # The response is the ZIP itself; extract the one CSV member it holds.
    try:
        import zipfile
        with zipfile.ZipFile(_io.BytesIO(raw_bytes)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                logger.warning("SAM extract refused: ZIP contains no .csv member")
                return False
            csv_bytes = zf.read(csv_names[0])
    except Exception as e:  # noqa: BLE001
        logger.warning("SAM extract refused: not a readable ZIP (%s)", e)
        return False

    sha256 = hashlib.sha256(csv_bytes).hexdigest()
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = _csv.DictReader(_io.StringIO(text))
    problem = sam_extract_schema_problem(reader.fieldnames)
    if problem:
        _SAM_CACHE["last_refusal"] = {"at": now, "reason": problem}
        logger.warning("SAM extract refused: %s", problem)
        return False

    by_npi: Dict[str, list] = {}
    by_name: Dict[str, list] = {}
    count = 0
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            problem = (f"malformed row at data line {count + 1}: field count does not "
                       f"match the header (truncated or shifted record)")
            _SAM_CACHE["last_refusal"] = {"at": now, "reason": problem}
            logger.warning("SAM extract refused: %s", problem)
            return False
        count += 1
        npi = (row.get("NPI") or "").strip()
        if npi and npi != "0000000000":
            by_npi.setdefault(npi, []).append(row)
        org_name = (row.get("Name") or "").strip()
        if org_name:
            key = normalize_org_name(org_name)
            if key:
                by_name.setdefault(key, []).append(row)
    if count == 0:
        problem = "the extract has a valid header but no rows"
        _SAM_CACHE["last_refusal"] = {"at": now, "reason": problem}
        logger.warning("SAM extract refused: %s", problem)
        return False

    _SAM_CACHE.update(loaded_at=now, by_npi=by_npi, by_name=by_name, row_count=count,
                       schema_version=SAM_EXTRACT_SCHEMA_VERSION, last_refusal=None,
                       edition_date=edition_date, sha256=sha256, source_url=source_url)
    logger.info("SAM extract index loaded: %s rows", count)
    return True


class SAMExtractConnector:
    """Candidate-only screening against the GSA SAM public exclusions extract.

    Every call is a no-op unless `ENABLE_SAM_EXTRACT_SCREENING` is truthy --
    see the module docstring. A hit is `candidate_match`, never `excluded`.
    """

    def _flag_enabled(self) -> bool:
        return os.getenv(ENV_FLAG, "").strip().lower() in ("1", "true", "yes")

    def _edition_evidence(self) -> Dict[str, Any]:
        return {
            "source_dataset": "SAM_Exclusions_Public_Extract_V2",
            "edition_date": _SAM_CACHE.get("edition_date"),
            "extract_sha256": _SAM_CACHE.get("sha256"),
            "source_url": _SAM_CACHE.get("source_url"),
            "list_rows_searched": _SAM_CACHE.get("row_count", 0),
            "list_loaded_at_epoch": _SAM_CACHE.get("loaded_at"),
        }

    def _build(self, matches: list, qp: dict) -> SourceResult:
        data = {
            "candidate_match": len(matches) > 0,
            "candidate_count": len(matches),
            # Deliberately NOT "excluded" -- see module docstring. A match
            # here is never an automatic exclusion finding.
            "candidates": matches[:5],
            **self._edition_evidence(),
        }
        return SourceResult.ok("SAM_EXTRACT", data, qp, API_VERSION,
                                raw_for_hash={"matches": len(matches)})

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not self._flag_enabled():
            return SourceResult.unavailable("SAM_EXTRACT", f"{ENV_FLAG} not enabled", qp, API_VERSION)
        rejection = npi_rejection_reason(npi)
        if rejection:
            return SourceResult.unavailable("SAM_EXTRACT", rejection, qp, API_VERSION)
        if not await _load_sam_extract():
            return SourceResult.unavailable("SAM_EXTRACT", "extract unavailable or refused", qp, API_VERSION)
        matches = _SAM_CACHE["by_npi"].get(npi.strip(), [])
        return self._build(matches, qp)

    async def lookup_by_name(self, org_name: str) -> SourceResult:
        qp = {"org": org_name}
        if not self._flag_enabled():
            return SourceResult.unavailable("SAM_EXTRACT", f"{ENV_FLAG} not enabled", qp, API_VERSION)
        if not await _load_sam_extract():
            return SourceResult.unavailable("SAM_EXTRACT", "extract unavailable or refused", qp, API_VERSION)
        key = normalize_org_name(org_name)
        matches = _SAM_CACHE["by_name"].get(key, []) if key else []
        result = self._build(matches, qp)
        result.data["match_methods"] = ["normalized_business_name"]
        return result

    async def probe(self) -> bool:
        if not self._flag_enabled():
            return False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(_SAM_LISTFILES_URL,
                                         params={"domain": _SAM_DOMAIN, "privacy": "Public"},
                                         headers=HTTP_HEADERS)
            return resp.status_code == 200
        except Exception:
            return False
