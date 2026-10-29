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
every lookup returns UNAVAILABLE immediately, no fetch attempted. It IS wired
into the pipeline, but only behind that same flag:
`evidence_service.gather_sources` calls `screen()` (both identifiers, flag
checked before the call too, so a flag-off deployment makes exactly the same
calls it made before this existed) and publishes the result under the
`sam_extract_candidate` key, which `evidence_assembly._dimension_exclusion`
renders as a separate, additive `SAM_EXTRACT_SUPPLEMENTARY` D3 item. Setting
the flag's deployed value remains a separate, explicit decision (and the
D4-SRC required-source question remains open regardless).

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
import datetime as _dt
import hashlib
import io as _io
import logging
import os
import re as _re
import time as _time
from datetime import timezone
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
#: The source refreshes daily; call a loaded extract stale after two missed
#: cycles rather than one, to tolerate a single late/delayed publish without
#: manufacturing an outage out of normal source timing noise.
_SAM_STALE_THRESHOLD_SECONDS = 2 * 86400

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
                               "row_count": 0, "filename": None,
                               "publication_date": None, "date_modified": None,
                               "http_last_modified": None, "sha256": None,
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


def _extract_staleness_reason(now: float) -> Optional[str]:
    """None when the loaded extract's own edition date is recent enough to
    trust for screening; otherwise the reason it is not.

    `_sam_cache_fresh` only says the in-memory CACHE was refreshed recently --
    it says nothing about whether the SOURCE itself stopped updating. A
    extract that successfully re-downloads every day but is secretly serving
    the same stale edition over and over would pass the cache-freshness check
    forever. A stale edition must fail closed and be disclosed as a distinct
    reason, never silently screened as if current -- that would look
    identical to a clean, up-to-date screen.
    """
    # The authoritative edition date is the SOURCE's own publication date,
    # decoded from the file's julian-coded name (see
    # publication_date_from_filename). The HTTP Last-Modified header is kept
    # separately as http_last_modified and is deliberately NOT what staleness
    # keys on -- it says when the file object was written, not which edition
    # GSA says it is.
    pub = _SAM_CACHE.get("publication_date")
    if pub is None:
        return ("extract loaded with no decodable publication date in its "
                "filename; edition freshness cannot be confirmed")
    age_seconds = now - _dt.datetime(pub.year, pub.month, pub.day,
                                      tzinfo=timezone.utc).timestamp()
    if age_seconds > _SAM_STALE_THRESHOLD_SECONDS:
        return (f"extract edition {pub.isoformat()} (from the filename's julian "
                f"date code) is {age_seconds / 86400:.1f} day(s) old; the source "
                f"refreshes daily and this is treated as stale past "
                f"{_SAM_STALE_THRESHOLD_SECONDS / 86400:.0f} day(s)")
    return None


#: `SAM_Exclusions_Public_Extract_V2_26301.ZIP` -> ("26301", date(2026, 10, 28)).
#: GSA's own listing description: "The links include the three-digit Julian
#: date for easier identification of the most recent file" -- YYDDD, where
#: DDD is the day-of-year.
_FILENAME_DATE_RE = _re.compile(
    r"SAM_Exclusions_Public_Extract_V2_(\d{2})(\d{3})\.ZIP$", _re.IGNORECASE)


def publication_date_from_filename(filename: str) -> Optional[_dt.date]:
    """The extract's own publication date, decoded from its julian-coded name.

    This is SOURCE metadata (what day GSA says this edition is), kept distinct
    from the HTTP Last-Modified header (when the file object was written),
    which is retained separately as `http_last_modified`.
    """
    m = _FILENAME_DATE_RE.search(filename or "")
    if not m:
        return None
    yy, ddd = int(m.group(1)), int(m.group(2))
    try:
        return _dt.date(2000 + yy, 1, 1) + _dt.timedelta(days=ddd - 1)
    except ValueError:
        return None


def parse_listfiles_payload(payload) -> list[dict]:
    """The VERIFIED shape of the listfiles response (checked against the real
    endpoint, 2026-10-29): `{"_embedded": {"customS3ObjectSummaryList": [
    {"displayKey": "<filename>.ZIP", "dateModified": "Oct 28,2026", ...}]}}`.

    Returns `[{"filename", "date_modified", "publication_date"}, ...]` for
    every entry whose filename carries a decodable julian date code, or []
    when the payload does not match the verified shape -- an unexpected shape
    REFUSES (empty list -> caller reports unavailable), it never guesses.
    """
    if not isinstance(payload, dict):
        return []
    entries = (payload.get("_embedded") or {}).get("customS3ObjectSummaryList")
    if not isinstance(entries, list):
        return []
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        filename = e.get("displayKey")
        pub = publication_date_from_filename(filename or "")
        if filename and pub:
            out.append({"filename": filename,
                        "date_modified": e.get("dateModified"),
                        "publication_date": pub})
    return out


async def _find_current_extract_file(client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """Ask the listfiles API which file is current. Returns the newest entry
    (by the filename's own julian date code, not lexicographic guesswork), or
    None if the listing failed or did not match the verified shape."""
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
    entries = parse_listfiles_payload(payload)
    if not entries:
        logger.warning("SAM extract listfiles answered, but not in the verified "
                       "shape (_embedded.customS3ObjectSummaryList); refusing "
                       "rather than guessing")
        return None
    return max(entries, key=lambda e: e["publication_date"])


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
            entry = await _find_current_extract_file(client)
            if not entry:
                logger.warning("SAM extract: could not determine current file from listfiles")
                return False
            filename = entry["filename"]
            download_url = f"{_SAM_DOWNLOAD_ROOT}/{quote(_SAM_DOMAIN)}/{quote(filename)}"
            # Verified live (2026-10-29): this endpoint answers 303 to a signed
            # S3 URL; the bare S3 href in the listing's _links 403s without the
            # signature, so follow_redirects is required, not optional.
            resp = await client.get(download_url, params={"privacy": "Public"}, headers=HTTP_HEADERS)
            if resp.status_code != 200:
                logger.warning("SAM extract download failed: HTTP %s", resp.status_code)
                return False
            raw_bytes = resp.content
            http_last_modified = resp.headers.get("Last-Modified")
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
                       filename=filename,
                       publication_date=publication_date_from_filename(filename),
                       date_modified=entry.get("date_modified"),
                       http_last_modified=http_last_modified,
                       sha256=sha256, source_url=source_url)
    logger.info("SAM extract index loaded: %s rows (edition %s)", count,
                _SAM_CACHE["publication_date"])
    return True


class SAMExtractConnector:
    """Candidate-only screening against the GSA SAM public exclusions extract.

    Every call is a no-op unless `ENABLE_SAM_EXTRACT_SCREENING` is truthy --
    see the module docstring. A hit is `candidate_match`, never `excluded`.
    """

    def _flag_enabled(self) -> bool:
        return os.getenv(ENV_FLAG, "").strip().lower() in ("1", "true", "yes")

    def _edition_evidence(self) -> Dict[str, Any]:
        pub = _SAM_CACHE.get("publication_date")
        return {
            "source_dataset": "SAM_Exclusions_Public_Extract_V2",
            "extract_filename": _SAM_CACHE.get("filename"),
            # The edition, as three separate facts, never conflated:
            # the filename's own julian-coded publication date (authoritative,
            # what staleness keys on), the listing's dateModified string, and
            # the HTTP Last-Modified header (object-write time, retained
            # separately as the directive requires).
            "publication_date": pub.isoformat() if pub else None,
            "listing_date_modified": _SAM_CACHE.get("date_modified"),
            "http_last_modified": _SAM_CACHE.get("http_last_modified"),
            "extract_sha256": _SAM_CACHE.get("sha256"),
            "source_url": _SAM_CACHE.get("source_url"),
            "list_rows_searched": _SAM_CACHE.get("row_count", 0),
            "list_loaded_at_epoch": _SAM_CACHE.get("loaded_at"),
            "schema_version": _SAM_CACHE.get("schema_version"),
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
        stale = _extract_staleness_reason(_time.time())
        if stale:
            return SourceResult.unavailable("SAM_EXTRACT", f"stale_source: {stale}", qp, API_VERSION)
        matches = _SAM_CACHE["by_npi"].get(npi.strip(), [])
        return self._build(matches, qp)

    async def lookup_by_name(self, org_name: str) -> SourceResult:
        qp = {"org": org_name}
        if not self._flag_enabled():
            return SourceResult.unavailable("SAM_EXTRACT", f"{ENV_FLAG} not enabled", qp, API_VERSION)
        if not (org_name or "").strip():
            # Same missing-vs-malformed distinction as npi_rejection_reason:
            # no name to look up is a normal, common state, not a data
            # defect. Must not fall through to `normalize_org_name("")`,
            # which would ask the cache for an empty key.
            return SourceResult.unavailable(
                "SAM_EXTRACT",
                "no_org_name_submitted: entity has no organisation name to look up",
                qp, API_VERSION)
        if not await _load_sam_extract():
            return SourceResult.unavailable("SAM_EXTRACT", "extract unavailable or refused", qp, API_VERSION)
        stale = _extract_staleness_reason(_time.time())
        if stale:
            return SourceResult.unavailable("SAM_EXTRACT", f"stale_source: {stale}", qp, API_VERSION)
        key = normalize_org_name(org_name)
        matches = _SAM_CACHE["by_name"].get(key, []) if key else []
        result = self._build(matches, qp)
        result.data["match_methods"] = ["normalized_business_name"]
        return result

    async def screen(self, npi: Optional[str], org_name: Optional[str]) -> SourceResult:
        """Screen by EVERY available identifier, never just the strongest one.

        Many exclusion rows in the extract carry no NPI at all (the NPI column
        is optional in GSA's data), so an entity that HAS an NPI can still
        only be findable by name. Screening NPI-first-else-name would silently
        miss exactly those rows. This unions both lookups: a candidate found
        by either method is a candidate, and `match_methods` records which
        method(s) actually matched.
        """
        qp = {"npi": npi, "org": org_name}
        if not self._flag_enabled():
            return SourceResult.unavailable("SAM_EXTRACT", f"{ENV_FLAG} not enabled", qp, API_VERSION)
        has_usable_npi = bool(npi) and npi_rejection_reason(npi) is None
        has_name = bool((org_name or "").strip())
        if not has_usable_npi and not has_name:
            return SourceResult.unavailable(
                "SAM_EXTRACT",
                "no_usable_identifier: neither a valid NPI nor an organisation "
                "name is available to screen on",
                qp, API_VERSION)
        if not await _load_sam_extract():
            return SourceResult.unavailable("SAM_EXTRACT", "extract unavailable or refused", qp, API_VERSION)
        stale = _extract_staleness_reason(_time.time())
        if stale:
            return SourceResult.unavailable("SAM_EXTRACT", f"stale_source: {stale}", qp, API_VERSION)
        methods_used, methods_matched, matches, seen = [], [], [], set()

        def _add(rows, method):
            matched = False
            for row in rows:
                rid = id(row)
                if rid not in seen:
                    seen.add(rid)
                    matches.append(row)
                matched = True
            if matched:
                methods_matched.append(method)

        if has_usable_npi:
            methods_used.append("npi")
            _add(_SAM_CACHE["by_npi"].get(npi.strip(), []), "npi")
        if has_name:
            methods_used.append("normalized_business_name")
            key = normalize_org_name(org_name)
            if key:
                _add(_SAM_CACHE["by_name"].get(key, []), "normalized_business_name")
        result = self._build(matches, qp)
        result.data["match_methods"] = methods_used
        result.data["match_methods_matched"] = methods_matched
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
