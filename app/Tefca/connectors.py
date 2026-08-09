"""
DocuAction TEFCA — Authoritative Data Source Connectors
Six-source validation pipeline for QHIN Participant & Subparticipant review.

ONC TEFCA Review Protocol — Alliance Global Tech, Inc. (AGT)
Contract No. 7571MN26F80064 (HHS/ONC)

DESIGN PRINCIPLE — FAIL CLOSED, NEVER FAIL OPEN
------------------------------------------------
Every connector distinguishes two outcomes that must never be conflated:

  1. VERIFIED RESULT  — the source returned HTTP 200 and we read a real answer
                        (including a verified "no record found"). success=True.
  2. UNAVAILABLE      — timeout, non-200, transport error, or any exception.
                        success=False, data=None, error="<source> unavailable: ...".

A source being UNAVAILABLE must NEVER produce a clean/compliant finding. The
validation engine treats any success=False required source as INDETERMINATE and
routes the entity to the Tier-2 analyst queue — it is never auto-classified B1.

Retry policy (per source): 3 attempts, exponential backoff 1s / 2s / 4s, with a
hard 30-second ceiling on total elapsed time. Retries fire only on transient
failures (timeouts, transport errors, HTTP 429/5xx) — never on 4xx, which will
not be fixed by retrying.
"""

import os
import json
import hashlib
import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

import httpx
from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger("docuaction.tefca.connectors")

# Per-source ceilings. The 30s total is enforced both by httpx (connect/read)
# and by tenacity stop_after_delay so a slow source can never wedge a batch.
SOURCE_TIMEOUT_SECONDS = 30.0
HEALTH_TIMEOUT_SECONDS = 5.0
RETRY_ATTEMPTS = 3


# ─── Data-provenance honesty ─────────────────────────────────────────────────
# TEFCA entity population data is PROVIDED BY ONC per contract direction. AGT
# does not source it independently and does not query any external directory
# system for it.
#
# Until that dataset is loaded, this module serves a bundled development
# dataset, which means the ENTIRE review pipeline is running on demonstration
# data. These helpers stamp a single, honest provenance label on every
# API/report payload so demonstration output can never be mistaken for a
# production review.
#
# TEFCA_ENTITY_DATA_KEY is the flag that says the ONC-provided dataset is in
# place. The legacy name RCE_DIRECTORY_API_KEY is still read so an environment
# set before the rename keeps working.
def is_running_mock() -> bool:
    """True if the module is serving MOCK entity data (no ONC dataset loaded)."""
    return not bool((os.getenv("TEFCA_ENTITY_DATA_KEY")
                     or os.getenv("RCE_DIRECTORY_API_KEY", "")).strip())


def data_source_labels() -> Dict[str, Any]:
    """Provenance fields to stamp on every dashboard/report/status response.
    Single source of truth — imported by reporting.py and routes.py."""
    if is_running_mock():
        return {
            "data_source": "MOCK — demonstration data only",
            "mock_data_warning": ("This report uses synthetic demonstration data. "
                                  "Do not use for operational decisions."),
        }
    return {"data_source": "PRODUCTION", "mock_data_warning": None}

HTTP_HEADERS = {
    "User-Agent": (
        "DocuAction-TEFCA/6.0 "
        "(Alliance Global Tech; ONC Contract 7571MN26F80064)"
    )
}

# Exceptions worth retrying. A non-200 is surfaced as RetryableHTTPError only
# for 429/5xx; 4xx is raised as a terminal error and not retried.
_RETRYABLE_EXC = (httpx.TimeoutException, httpx.TransportError)


class RetryableHTTPError(Exception):
    """Raised for HTTP 429/5xx so tenacity retries; 4xx is terminal."""


# ─── Verification helpers (NPI / name / address cross-reference) ──────────────
# Small, dependency-free comparators used by the enhanced NPPES and SAM checks
# to cross-reference a submitted entity against its authoritative registration.

def _name_similarity(name1: str, name2: str) -> float:
    words1 = set(name1.lower().split()) - {"inc","llc","ltd","corp","co","the","of","and","&","dba","pllc","pc","pa","md","do"}
    words2 = set(name2.lower().split()) - {"inc","llc","ltd","corp","co","the","of","and","&","dba","pllc","pc","pa","md","do"}
    if not words1 or not words2:
        return 0.0
    return len(words1 & words2) / max(len(words1), len(words2))


def _compare_addresses(submitted: dict, registered: dict) -> dict:
    result = {"match": False, "level": None, "details": []}
    sub_state = (submitted.get("state") or "").upper().strip()
    reg_state = (registered.get("state") or "").upper().strip()
    sub_city = (submitted.get("city") or "").lower().strip()
    reg_city = (registered.get("city") or "").lower().strip()
    sub_zip = (submitted.get("postal_code") or submitted.get("zip") or "")[:5]
    reg_zip = (registered.get("postal_code") or "")[:5]
    if sub_state and reg_state and sub_state != reg_state:
        result["level"] = "major"
        result["details"].append(f"STATE MISMATCH: submitted '{sub_state}' vs registered '{reg_state}'")
        return result
    if sub_city and reg_city and sub_city != reg_city:
        result["level"] = "major"
        result["details"].append(f"City mismatch: '{sub_city}' vs '{reg_city}'")
        return result
    if sub_zip and reg_zip and sub_zip != reg_zip:
        result["level"] = "minor"
        result["details"].append(f"ZIP mismatch: '{sub_zip}' vs '{reg_zip}'")
    if not result["level"]:
        result["match"] = True
        result["details"].append("Address verified")
    return result


# ─── Source Result ───────────────────────────────────────────────────────────

@dataclass
class SourceResult:
    """
    Canonical result object returned by every connector call.

    Field references consumed downstream (do not remove without updating both
    validation_engine.py and routes.py):
      success, data, error, source_name, query_params,
      query_timestamp, response_hash, api_version
    """
    source_name: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    query_params: Dict[str, Any] = field(default_factory=dict)
    query_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    response_hash: Optional[str] = None
    api_version: Optional[str] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style read of .data so an enriched result can be consumed as a
        mapping (r.get("npi_valid")) without callers reaching into .data. Reads
        from data only — fail-closed unavailable results (data=None) yield the
        default, never a fabricated clean value."""
        return (self.data or {}).get(key, default)

    @staticmethod
    def hash_payload(payload: Any) -> str:
        """Stable SHA-256 of a response payload — for audit reproducibility."""
        try:
            serialized = json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            serialized = str(payload)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def ok(
        cls,
        source_name: str,
        data: Dict[str, Any],
        query_params: Dict[str, Any],
        api_version: Optional[str] = None,
        raw_for_hash: Any = None,
    ) -> "SourceResult":
        return cls(
            source_name=source_name,
            success=True,
            data=data,
            query_params=query_params,
            response_hash=cls.hash_payload(raw_for_hash if raw_for_hash is not None else data),
            api_version=api_version,
        )

    @classmethod
    def unavailable(
        cls,
        source_name: str,
        reason: str,
        query_params: Dict[str, Any],
        api_version: Optional[str] = None,
    ) -> "SourceResult":
        """Fail-closed result. data is None so no caller can read a clean value."""
        return cls(
            source_name=source_name,
            success=False,
            data=None,
            error=f"{source_name} unavailable: {reason}",
            query_params=query_params,
            api_version=api_version,
        )


# ─── Shared fetch with retry/backoff ─────────────────────────────────────────

async def _get_with_retry(
    url: str,
    params: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float = SOURCE_TIMEOUT_SECONDS,
) -> httpx.Response:
    """
    GET with tenacity retry. Retries transient transport/timeout errors and
    HTTP 429/5xx (1s/2s/4s backoff, 30s ceiling). Raises on terminal 4xx.
    """
    async for attempt in AsyncRetrying(
        stop=(stop_after_attempt(RETRY_ATTEMPTS) | stop_after_delay(SOURCE_TIMEOUT_SECONDS)),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(_RETRYABLE_EXC + (RetryableHTTPError,)),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RetryableHTTPError(
                        f"HTTP {resp.status_code} from {url}"
                    )
                return resp
    # AsyncRetrying with reraise=True never falls through, but satisfy type checkers.
    raise RuntimeError("unreachable")


# ─── NPPES NPI Registry (CMS/HHS) ────────────────────────────────────────────

class NPPESConnector:
    BASE_URL = "https://npiregistry.cms.hhs.gov/api"
    API_VERSION = "2.1"

    @staticmethod
    def _shape(r: dict, npi_fallback: str) -> Dict[str, Any]:
        """Normalize one NPPES result row into our provider data dict. Shared by
        lookup_by_npi and lookup_by_name so the enriched fields (taxonomy,
        enumeration_date) are extracted in exactly one place."""
        basic = r.get("basic", {})
        org_name = basic.get("organization_name") or (
            f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
        )
        taxonomies = r.get("taxonomies", []) or []
        primary_tax = next((t for t in taxonomies if t.get("primary")), (taxonomies[0] if taxonomies else {}))
        return {
            "found": True,
            "npi": str(r.get("number", npi_fallback)),
            "enumeration_type": r.get("enumeration_type"),  # NPI-1 / NPI-2
            "status": (basic.get("status") or "ACTIVE"),
            "legal_name": org_name,
            "organization_name": basic.get("organization_name"),
            "credential": basic.get("credential"),
            "addresses": r.get("addresses", []),
            "deactivation_date": basic.get("deactivation_date"),
            "reactivation_date": basic.get("reactivation_date"),
            # Enriched fields (additive — consumed by the enhanced check_nppes):
            "enumeration_date": basic.get("enumeration_date"),
            "taxonomy": primary_tax.get("desc"),
            "taxonomy_code": primary_tax.get("code"),
            "taxonomies": taxonomies,
        }

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not npi:
            # A missing NPI is a real, verified condition — not an outage.
            return SourceResult.ok(
                "NPPES", {"found": False, "npi": None, "reason": "no_npi_submitted"},
                qp, self.API_VERSION,
            )
        try:
            resp = await _get_with_retry(
                f"{self.BASE_URL}/",
                params={"number": npi, "version": self.API_VERSION, "limit": 1},
                headers=HTTP_HEADERS,
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("NPPES", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            results = payload.get("results", [])
            if not results:
                # Verified: NPI does not exist in NPPES.
                return SourceResult.ok(
                    "NPPES", {"found": False, "npi": npi}, qp, self.API_VERSION, raw_for_hash=payload,
                )
            return SourceResult.ok("NPPES", self._shape(results[0], npi), qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"NPPES unavailable for NPI {npi}: {e}")
            return SourceResult.unavailable("NPPES", str(e), qp, self.API_VERSION)

    async def lookup_by_name(self, organization_name: str) -> SourceResult:
        """Resolve a provider by organization name (used when no NPI is on the
        entity). Same fail-closed contract as lookup_by_npi."""
        qp = {"organization_name": organization_name}
        if not organization_name:
            return SourceResult.ok(
                "NPPES", {"found": False, "reason": "no_name_submitted"}, qp, self.API_VERSION,
            )
        try:
            resp = await _get_with_retry(
                f"{self.BASE_URL}/",
                params={"organization_name": organization_name, "version": self.API_VERSION, "limit": 1},
                headers=HTTP_HEADERS,
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("NPPES", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            results = payload.get("results", [])
            if not results:
                return SourceResult.ok(
                    "NPPES", {"found": False, "organization_name": organization_name},
                    qp, self.API_VERSION, raw_for_hash=payload,
                )
            return SourceResult.ok("NPPES", self._shape(results[0], ""), qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"NPPES unavailable for name '{organization_name}': {e}")
            return SourceResult.unavailable("NPPES", str(e), qp, self.API_VERSION)

    async def probe(self) -> bool:
        try:
            resp = await _get_with_retry(
                f"{self.BASE_URL}/",
                params={"number": "1234567893", "version": self.API_VERSION, "limit": 1},
                headers=HTTP_HEADERS,
                timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─── OIG LEIE Exclusion Database (OIG/HHS) ───────────────────────────────────

# Module-level LEIE index. The OIG publishes the full exclusions list as a free
# CSV (no key, no JSON API). We download it once, index it by NPI and by name,
# and refresh daily. The JSON "search API" only 302-redirects into ASP.NET cookie
# detection, so the CSV is the reliable source of truth.
_LEIE_CSV_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
_LEIE_TTL_SECONDS = 86400
_LEIE_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "by_npi": {}, "by_name": {}, "row_count": 0}


async def _ensure_leie_loaded() -> bool:
    """Download + index the LEIE CSV if the cache is empty or stale. Returns
    False (fail-closed) if the CSV cannot be retrieved."""
    import time as _time
    import csv as _csv
    import io as _io
    now = _time.time()
    if _LEIE_CACHE["row_count"] > 0 and (now - _LEIE_CACHE["loaded_at"]) < _LEIE_TTL_SECONDS:
        return True
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            resp = await client.get(_LEIE_CSV_URL, headers=HTTP_HEADERS)
        if resp.status_code != 200:
            return False
        by_npi: Dict[str, list] = {}
        by_name: Dict[tuple, list] = {}
        count = 0
        for row in _csv.DictReader(_io.StringIO(resp.text)):
            count += 1
            rec = {
                "lastname": row.get("LASTNAME", ""), "firstname": row.get("FIRSTNAME", ""),
                "busname": row.get("BUSNAME", ""), "exclusion_type": row.get("EXCLTYPE", ""),
                "exclusion_date": row.get("EXCLDATE", ""), "reinstatement_date": row.get("REINDATE", ""),
                "state": row.get("STATE", ""), "npi": (row.get("NPI") or "").strip(),
            }
            npi = rec["npi"]
            if npi and npi != "0000000000":
                by_npi.setdefault(npi, []).append(rec)
            last = (row.get("LASTNAME") or "").strip().upper()
            first = (row.get("FIRSTNAME") or "").strip().upper()
            if last:
                by_name.setdefault((last, first), []).append(rec)
            bus = (row.get("BUSNAME") or "").strip().upper()
            if bus:
                by_name.setdefault((bus, ""), []).append(rec)
        _LEIE_CACHE.update(loaded_at=now, by_npi=by_npi, by_name=by_name, row_count=count)
        logger.info(f"LEIE index loaded: {count} exclusion rows")
        return True
    except Exception as e:
        logger.warning(f"LEIE CSV load failed: {e}")
        return False


class OIGLEIEConnector:
    """OIG LEIE exclusion screening via the free public CSV (key-less)."""
    API_VERSION = "CSV-UPDATED"

    @staticmethod
    def _reinstated(rec: dict) -> bool:
        rd = (rec.get("reinstatement_date") or "").strip()
        return rd not in ("", "00000000", "0")

    def _build(self, matches: list, qp: dict) -> SourceResult:
        active = [m for m in matches if not self._reinstated(m)]
        historical = [m for m in matches if self._reinstated(m)]
        data = {
            "excluded": len(active) > 0,
            "exclusion_found": len(matches) > 0,
            "active_exclusions": active[:5],
            "historical_exclusions": historical[:5],
            "exclusion_count": len(matches),
            # First active exclusion's headline fields, for evidence records.
            "exclusion_date": (active[0]["exclusion_date"] if active else None),
            "exclusion_type": (active[0]["exclusion_type"] if active else None),
            "reinstatement_date": (historical[0]["reinstatement_date"] if historical else None),
        }
        return SourceResult.ok("OIG_LEIE", data, qp, self.API_VERSION, raw_for_hash={"matches": len(matches)})

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not npi:
            # No NPI to match on — fall back to "no NPI-based exclusion found",
            # which is a verified negative for the NPI dimension (name screening
            # is a separate call). Not an outage.
            if not await _ensure_leie_loaded():
                return SourceResult.unavailable("OIG_LEIE", "exclusions CSV unavailable", qp, self.API_VERSION)
            return self._build([], qp)
        if not await _ensure_leie_loaded():
            return SourceResult.unavailable("OIG_LEIE", "exclusions CSV unavailable", qp, self.API_VERSION)
        matches = _LEIE_CACHE["by_npi"].get(npi.strip(), [])
        return self._build(matches, qp)

    async def lookup_by_name(self, last: str, first: str = "", org: str = "") -> SourceResult:
        qp = {"last": last, "first": first, "org": org}
        if not await _ensure_leie_loaded():
            return SourceResult.unavailable("OIG_LEIE", "exclusions CSV unavailable", qp, self.API_VERSION)
        if org:
            matches = _LEIE_CACHE["by_name"].get((org.strip().upper(), ""), [])
        else:
            matches = _LEIE_CACHE["by_name"].get((last.strip().upper(), first.strip().upper()), [])
        return self._build(matches, qp)

    async def probe(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(HEALTH_TIMEOUT_SECONDS)) as client:
                resp = await client.head(_LEIE_CSV_URL, headers=HTTP_HEADERS)
            return resp.status_code == 200
        except Exception:
            return False


# ─── SAM.gov Federal Registration & Exclusions (GSA) ─────────────────────────

def _sam_failure_reason(resp) -> str:
    """Turn a non-200 SAM response into something diagnosable.

    `HTTP 404` on its own sent an investigation down the wrong path once already
    (see the note on SAMGovConnector.__init__), because it is the status SAM
    returns both when an entity is absent and when the API is not serving at all.
    These strings are read by an operator deciding whether to go get a key, so
    they say which of those happened.
    """
    status = getattr(resp, "status_code", 0)
    try:
        body = (resp.text or "").strip()
    except Exception:  # noqa: BLE001 — diagnostics must not raise
        body = ""

    if status == 404 and not body:
        # Not a key problem, and provably so. The same key returns HTTP 200 with
        # X-Ratelimit-Limit: 36000 against api.data.gov (DEMO_KEY gets 10, an
        # invalid key gets 401), so it is valid and fully provisioned — which
        # also rules out the "public key, needs FOUO" theory. Meanwhile every
        # api.sam.gov path answers an empty 404 from `server: istio-envoy` with
        # no api.data.gov gateway headers, including requests with no key, with
        # an invalid key, to the bare root, and to paths that do not exist.
        # Requests are being refused at SAM's ingress before authentication ever
        # runs. No credential change can affect that.
        return ("HTTP 404 with an empty body — api.sam.gov served no route. "
                "Upstream routing at SAM, NOT a missing, invalid or "
                "insufficiently privileged API key: the same key is valid with a "
                "36,000/hr quota on api.data.gov, and requests carrying no key "
                "get this identical empty 404.")
    if status in (401, 403):
        return f"HTTP {status} — SAM.gov rejected the API key: {body[:160]}"
    if status == 429:
        return "HTTP 429 — SAM.gov rate limit reached (daily quota is per key)"
    return f"HTTP {status}{(' — ' + body[:160]) if body else ''}"


class SAMGovConnector:
    """SAM.gov federal registration + exclusion (debarment) checks.

    TWO endpoints are needed and they are NOT interchangeable:

      Entity Management (v3)  -> is the entity registered, and is that
                                 registration currently Active?
      Exclusions       (v4)   -> is the entity debarred/excluded?

    The v3 record carries an `exclusionStatusFlag`, but that flag is a summary
    maintained on the registration. An entity with no SAM registration at all
    can still appear on the exclusions list, and in that case v3 returns nothing
    while v4 returns a hit. Trusting v3 alone would report "not found, therefore
    fine" about a debarred party, so the exclusions endpoint is queried
    independently rather than inferred.

    SAM is keyed on UEI/CAGE, never NPI. Search strategy, in order:
      1. UEI present  -> exact match, authoritative.
      2. No UEI       -> legal-business-name search, which is fuzzy.
      3. Name search returning MORE THAN ONE entity -> ambiguous; flagged for
         manual review rather than resolved by guessing. Picking the first hit
         would attach a federal registration (or a debarment) to an entity on
         the strength of a name collision.
    """

    BASE_URL = "https://api.sam.gov/entity-information/v3/entities"
    EXCLUSIONS_URL = "https://api.sam.gov/entity-information/v4/exclusions"
    API_VERSION = "v3+v4"

    def __init__(self):
        # Read the key at INSTANTIATION, not at class-definition/import time,
        # so a key loaded after import (e.g. via .env) is honored.
        #
        # CORRECTION 2026-08-07. This used to say the 404s were caused by using
        # the public DEMO_KEY and that a registered key would fix them. That was
        # wrong, and it cost an investigation. Tested with a real registered
        # 40-character key:
        #
        #   - every path on api.sam.gov returns 404 with an EMPTY body —
        #     entity-information v1/v2/v3/v4, opportunities v1/v2,
        #     data-services, and the bare host root
        #   - a deliberately invalid key returns the identical empty 404, so the
        #     key is never evaluated
        #   - NO key at all also returns the empty 404, where a live route is
        #     documented to return 403
        #   - a path that certainly does not exist (/zzz-does-not-exist) is
        #     indistinguishable from a documented one
        #   - reproduced from three independent networks (workstation, Azure
        #     Central US via the dev container, and a third-party fetcher) and on
        #     the separate api-alpha.sam.gov environment
        #   - sam.gov itself serves 200, so this is api.sam.gov specifically
        #
        # api.sam.gov is therefore not routing its API. Getting another key will
        # not change that. Re-test with:
        #   curl -s -o /dev/null -w '%{http_code}' https://api.sam.gov/zzz
        # and treat anything other than 404 as a sign the platform is back.
        self.api_key = os.getenv("SAM_GOV_API_KEY", "")

    async def lookup_by_uei(self, uei: str) -> SourceResult:
        """SAM.gov is keyed on UEI/CAGE, not NPI. Verify registration + exclusion
        (debarment) status by UEI."""
        qp = {"uei": uei}
        if not self.api_key:
            return SourceResult.unavailable(
                "SAM_GOV", "SAM_GOV_API_KEY not set (register a free key at sam.gov)", qp, self.API_VERSION
            )
        if not uei:
            return SourceResult.unavailable(
                "SAM_GOV", "no UEI on entity (SAM.gov cannot be queried by NPI)", qp, self.API_VERSION
            )
        try:
            resp = await _get_with_retry(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "ueiSAM": uei,
                    "includeSections": "entityRegistration,coreData",
                },
                headers=HTTP_HEADERS,
            )
            if resp.status_code != 200:
                return SourceResult.unavailable(
                    "SAM_GOV", _sam_failure_reason(resp), qp, self.API_VERSION)
            payload = resp.json()
            entities = payload.get("entityData", [])
            if not entities:
                return SourceResult.ok(
                    "SAM_GOV", {"found": False, "uei": uei, "registration_current": None, "excluded": False},
                    qp, self.API_VERSION, raw_for_hash=payload,
                )
            reg = entities[0].get("entityRegistration", {})
            status = (reg.get("registrationStatus") or "").upper()
            # coreData.physicalAddress is requested via includeSections; normalize
            # it to our {city, state, postal_code} shape for address cross-reference.
            phys = (entities[0].get("coreData", {}) or {}).get("physicalAddress", {}) or {}
            registered_address = {
                "address_1": phys.get("addressLine1"),
                "city": phys.get("city"),
                "state": phys.get("stateOrProvinceCode"),
                "postal_code": phys.get("zipCode"),
            }
            data = {
                "found": True,
                "uei": reg.get("ueiSAM"),
                "legal_name": reg.get("legalBusinessName"),
                "registration_status": reg.get("registrationStatus"),
                "registration_current": status == "ACTIVE",
                "registration_expiry": reg.get("registrationExpirationDate"),
                "excluded": reg.get("exclusionStatusFlag") == "Y",
                "registered_address": registered_address,
            }
            return SourceResult.ok("SAM_GOV", data, qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"SAM.gov unavailable for UEI {uei}: {e}")
            return SourceResult.unavailable("SAM_GOV", str(e), qp, self.API_VERSION)

    async def lookup_by_name(self, legal_name: str) -> SourceResult:
        """Fuzzy fallback when the registry holds no UEI.

        Returns `ambiguous: True` when SAM matches more than one entity. That is
        a real answer, not a failure: the caller must not treat it as verified.
        """
        qp = {"legalBusinessName": legal_name}
        if not self.api_key:
            return SourceResult.unavailable(
                "SAM_GOV", "SAM_GOV_API_KEY not set (register a free key at sam.gov)",
                qp, self.API_VERSION)
        if not legal_name:
            return SourceResult.unavailable(
                "SAM_GOV", "no legal name on entity", qp, self.API_VERSION)
        try:
            resp = await _get_with_retry(
                self.BASE_URL,
                params={"api_key": self.api_key, "legalBusinessName": legal_name,
                        "includeSections": "entityRegistration,coreData",
                        "page": 0, "size": 10},
                headers=HTTP_HEADERS,
            )
            if resp.status_code != 200:
                return SourceResult.unavailable(
                    "SAM_GOV", _sam_failure_reason(resp), qp, self.API_VERSION)
            payload = resp.json()
            entities = payload.get("entityData", []) or []
            if not entities:
                return SourceResult.ok(
                    "SAM_GOV", {"found": False, "matched_by": "name",
                                "ambiguous": False, "excluded": False},
                    qp, self.API_VERSION, raw_for_hash=payload)
            if len(entities) > 1:
                return SourceResult.ok(
                    "SAM_GOV",
                    {"found": True, "matched_by": "name", "ambiguous": True,
                     "match_count": len(entities),
                     "candidates": [
                         {"uei": (e.get("entityRegistration") or {}).get("ueiSAM"),
                          "legal_name": (e.get("entityRegistration") or {})
                          .get("legalBusinessName")} for e in entities[:10]],
                     "registration_current": None, "excluded": False,
                     "note": "Multiple SAM entities match this legal name; "
                             "manual review required to pick the right one."},
                    qp, self.API_VERSION, raw_for_hash=payload)
            reg = entities[0].get("entityRegistration", {}) or {}
            status = (reg.get("registrationStatus") or "").upper()
            return SourceResult.ok(
                "SAM_GOV",
                {"found": True, "matched_by": "name", "ambiguous": False,
                 "uei": reg.get("ueiSAM"),
                 "legal_name": reg.get("legalBusinessName"),
                 "registration_status": reg.get("registrationStatus"),
                 "registration_current": status == "ACTIVE",
                 "registration_expiry": reg.get("registrationExpirationDate"),
                 "excluded": reg.get("exclusionStatusFlag") == "Y"},
                qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"SAM.gov name lookup failed for {legal_name!r}: {e}")
            return SourceResult.unavailable("SAM_GOV", str(e), qp, self.API_VERSION)

    async def check_exclusions(self, uei: str = "", legal_name: str = "") -> SourceResult:
        """Exclusions (debarment) check against the v4 endpoint.

        Semantics, stated because they invert the usual reading: results FOUND
        means the entity IS excluded (bad). No results means NOT excluded
        (good). A transport failure is neither — it is `unavailable`, and must
        never be collapsed into "clear".
        """
        qp = {k: v for k, v in (("ueiSAM", uei), ("q", legal_name)) if v}
        if not self.api_key:
            return SourceResult.unavailable(
                "SAM_GOV_EXCLUSIONS",
                "SAM_GOV_API_KEY not set (register a free key at sam.gov)",
                qp, "v4")
        if not (uei or legal_name):
            return SourceResult.unavailable(
                "SAM_GOV_EXCLUSIONS", "no UEI or legal name to query", qp, "v4")
        params = {"api_key": self.api_key, "page": 0, "size": 10}
        if uei:
            params["ueiSAM"] = uei
        else:
            params["q"] = legal_name
        try:
            resp = await _get_with_retry(self.EXCLUSIONS_URL, params=params,
                                         headers=HTTP_HEADERS)
            if resp.status_code != 200:
                return SourceResult.unavailable(
                    "SAM_GOV_EXCLUSIONS", _sam_failure_reason(resp), qp, "v4")
            payload = resp.json()
            records = (payload.get("excludedEntity")
                       or payload.get("excludedEntities") or []) or []
            total = payload.get("totalRecords")
            if total is None:
                total = len(records)
            return SourceResult.ok(
                "SAM_GOV_EXCLUSIONS",
                {"excluded": bool(total),
                 "match_count": total,
                 "matched_by": "uei" if uei else "name",
                 "exclusions": [
                     {"name": (r.get("exclusionIdentification") or {})
                      .get("exclusionName"),
                      "type": (r.get("exclusionTypes") or {}).get("exclusionType"),
                      "agency": (r.get("exclusionProgram") or {})
                      .get("excludingAgencyName"),
                      "active_date": (r.get("exclusionActions") or {})
                      .get("listOfActions")} for r in records[:10]]},
                qp, "v4", raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"SAM.gov exclusions lookup failed: {e}")
            return SourceResult.unavailable("SAM_GOV_EXCLUSIONS", str(e), qp, "v4")

    async def verify(self, uei: str = "", legal_name: str = "") -> SourceResult:
        """Combined registration + exclusion check — the entry point callers want.

        Registration comes from v3 (by UEI when available, else by name);
        exclusion comes independently from v4. If EITHER leg is unavailable the
        combined result reports what is known and flags the gap, rather than
        presenting a half-answer as complete.
        """
        qp = {k: v for k, v in (("uei", uei), ("legal_name", legal_name)) if v}
        reg = (await self.lookup_by_uei(uei)) if uei else \
              (await self.lookup_by_name(legal_name))
        exc = await self.check_exclusions(uei=uei, legal_name=legal_name)

        if not reg.success and not exc.success:
            return SourceResult.unavailable(
                "SAM_GOV", reg.error or exc.error or "both SAM legs unavailable",
                qp, self.API_VERSION)

        data = dict(reg.data or {})
        data["registration_available"] = reg.success
        data["exclusions_available"] = exc.success
        if exc.success:
            # v4 is authoritative for exclusion; it overrides the v3 summary flag.
            data["excluded"] = bool(exc.get("excluded"))
            data["exclusion_match_count"] = exc.get("match_count")
            data["exclusions"] = exc.get("exclusions")
        else:
            data["exclusion_check_error"] = exc.error
            data.setdefault("excluded", False)
            # Do not let a missing exclusions check read as a clean bill.
            data["excluded_known"] = False
        if exc.success:
            data["excluded_known"] = True
        return SourceResult.ok("SAM_GOV", data, qp, self.API_VERSION)

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        # The SAM.gov entity API has no NPI index. Callers should pass UEI via
        # lookup_by_uei; querying by NPI cannot verify SAM and fails closed.
        return SourceResult.unavailable(
            "SAM_GOV", "SAM.gov has no NPI lookup; provide entity UEI + SAM_GOV_API_KEY",
            {"npi": npi}, self.API_VERSION,
        )

    async def probe(self) -> bool:
        if not self.api_key:
            return False
        try:
            resp = await _get_with_retry(
                self.BASE_URL,
                params={"api_key": self.api_key, "registrationStatus": "A", "page": 0, "size": 1},
                headers=HTTP_HEADERS, timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─── PECOS Provider Enrollment (CMS) ─────────────────────────────────────────

class PECOSConnector:
    """
    Provider enrollment verification via the free, key-less CMS NPPES NPI
    Registry. Confirms enrollment/identity (provider name, taxonomy/provider
    type, address, enumeration date, status). The PECOS payment-suspension feed
    requires COR provisioning, so payment_suspension is reported as None
    ("not provided by this free source") — never a fabricated clean value.
    """
    BASE_URL = "https://npiregistry.cms.hhs.gov/api/"
    API_VERSION = "2.1"

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not npi:
            return SourceResult.ok(
                "PECOS",
                {"found": False, "payment_suspension": None, "reason": "no_npi_submitted"},
                qp, self.API_VERSION,
            )
        try:
            resp = await _get_with_retry(
                self.BASE_URL,
                params={"version": self.API_VERSION, "number": npi},
                headers=HTTP_HEADERS,
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("PECOS", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            results = payload.get("results", [])
            if not results:
                return SourceResult.ok(
                    "PECOS",
                    {"found": False, "npi": npi, "payment_suspension": None,
                     "note": "NPI not enrolled / not found in NPPES (PECOS source)."},
                    qp, self.API_VERSION, raw_for_hash=payload,
                )
            r = results[0]
            basic = r.get("basic", {})
            taxonomies = r.get("taxonomies", []) or []
            primary_tax = next((t for t in taxonomies if t.get("primary")), (taxonomies[0] if taxonomies else {}))
            loc = next((a for a in r.get("addresses", []) if a.get("address_purpose") == "LOCATION"), {})
            provider_name = basic.get("organization_name") or (
                f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
            )
            data = {
                "found": True,
                "npi": str(r.get("number", npi)),
                "enumeration_type": r.get("enumeration_type"),
                "provider_name": provider_name,
                "status": basic.get("status"),
                "enumeration_date": basic.get("enumeration_date"),
                "taxonomy": primary_tax.get("desc"),
                "taxonomy_code": primary_tax.get("code"),
                "provider_type": primary_tax.get("desc"),
                "city": loc.get("city"),
                "state": loc.get("state"),
                "payment_suspension": None,
                "note": "Enrollment verified via NPPES. Payment-suspension flag requires COR-provisioned feed.",
            }
            return SourceResult.ok("PECOS", data, qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"PECOS/NPPES unavailable for NPI {npi}: {e}")
            return SourceResult.unavailable("PECOS", str(e), qp, self.API_VERSION)

    async def probe(self) -> bool:
        try:
            resp = await _get_with_retry(
                self.BASE_URL, params={"version": self.API_VERSION, "number": "1234567893"},
                headers=HTTP_HEADERS, timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─── TEFCA entity data (provided by ONC) — FHIR R4 ───────────────────────────────

class RCEDirectoryConnector:
    """TEFCA entity population data — PROVIDED BY ONC per contract direction.

    AGT does not source entity population data independently and does not query
    any external directory system for it. This class is the loader for the
    ONC-provided dataset, kept under its original name because the identifier
    appears in database rows, source keys and API payloads; renaming the class
    would be a data migration, not an edit.

    Until the ONC-provided dataset is in place, get_all_organizations and
    get_organization_by_id serve a bundled development dataset, clearly flagged
    data_source="MOCK". The validation engine refuses to auto-classify
    MOCK-sourced entities as Bucket 1, so demonstration data can never
    masquerade as a finalized clean finding.
    """
    BASE_URL = "urn:docuaction:tefca/fhir/r4"
    API_VERSION = "R4"

    def __init__(self):
        self.api_key = os.getenv("RCE_DIRECTORY_API_KEY", "")

    def _is_live(self) -> bool:
        return bool(self.api_key)

    def _auth_headers(self) -> Dict[str, str]:
        return {
            **HTTP_HEADERS,
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/fhir+json",
        }

    async def get_all_organizations(
        self,
        entity_type: Optional[str] = None,
        qhin_name: Optional[str] = None,
        limit: int = 500,
    ) -> SourceResult:
        qp = {"entity_type": entity_type, "qhin_name": qhin_name, "limit": limit}
        if not self._is_live():
            from .mock_data import ALL_MOCK_ENTITIES
            orgs = list(ALL_MOCK_ENTITIES)
            if qhin_name:
                orgs = [o for o in orgs if o.get("_qhin") == qhin_name]
            if entity_type:
                orgs = [o for o in orgs if _entity_type_of(o) == entity_type]
            orgs = orgs[:limit]
            return SourceResult.ok(
                "RCE_DIRECTORY",
                {"organizations": orgs, "count": len(orgs), "data_source": "MOCK"},
                qp, self.API_VERSION,
            )
        try:
            params: Dict[str, Any] = {"_count": limit}
            resp = await _get_with_retry(
                f"{self.BASE_URL}/Organization",
                params=params, headers=self._auth_headers(),
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("RCE_DIRECTORY", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            orgs = [e.get("resource", {}) for e in payload.get("entry", [])]
            return SourceResult.ok(
                "RCE_DIRECTORY",
                {"organizations": orgs, "count": len(orgs), "data_source": "LIVE"},
                qp, self.API_VERSION, raw_for_hash=payload,
            )
        except Exception as e:
            logger.warning(f"TEFCA entity data unavailable: {e}")
            return SourceResult.unavailable("RCE_DIRECTORY", str(e), qp, self.API_VERSION)

    async def get_organization_by_id(self, rce_id: str) -> SourceResult:
        qp = {"rce_id": rce_id}
        if not self._is_live():
            from .mock_data import ALL_MOCK_ENTITIES
            match = next((o for o in ALL_MOCK_ENTITIES if o.get("id") == rce_id), None)
            if match is None:
                return SourceResult.unavailable(
                    "RCE_DIRECTORY", f"organization {rce_id} not found in dataset", qp, self.API_VERSION
                )
            org = dict(match)
            org["data_source"] = "MOCK"
            return SourceResult.ok("RCE_DIRECTORY", org, qp, self.API_VERSION)
        try:
            resp = await _get_with_retry(
                f"{self.BASE_URL}/Organization/{rce_id}",
                params={}, headers=self._auth_headers(),
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("RCE_DIRECTORY", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            org = resp.json()
            org["data_source"] = "LIVE"
            return SourceResult.ok("RCE_DIRECTORY", org, qp, self.API_VERSION, raw_for_hash=org)
        except Exception as e:
            logger.warning(f"TEFCA entity data unavailable for {rce_id}: {e}")
            return SourceResult.unavailable("RCE_DIRECTORY", str(e), qp, self.API_VERSION)

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not self._is_live():
            return SourceResult.unavailable(
                "RCE_DIRECTORY", "entity population data is provided by ONC", qp, self.API_VERSION
            )
        try:
            resp = await _get_with_retry(
                f"{self.BASE_URL}/Organization",
                params={"identifier": f"http://hl7.org/fhir/sid/us-npi|{npi}"},
                headers=self._auth_headers(),
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("RCE_DIRECTORY", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            entries = payload.get("entry", [])
            return SourceResult.ok(
                "RCE_DIRECTORY",
                {"found": len(entries) > 0, "entry_count": len(entries), "entries": entries[:1]},
                qp, self.API_VERSION, raw_for_hash=payload,
            )
        except Exception as e:
            return SourceResult.unavailable("RCE_DIRECTORY", str(e), qp, self.API_VERSION)

    async def probe(self) -> bool:
        if not self._is_live():
            return False
        try:
            resp = await _get_with_retry(
                f"{self.BASE_URL}/metadata", params={},
                headers=self._auth_headers(), timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─── IQVIA OneKey (hierarchy) ────────────────────────────────────────────────

class IQVIAOneKeyConnector:
    """Healthcare provider hierarchy database. Pending federal contract ODC."""
    BASE_URL = "https://api.iqvia.com/onekey/v1/practitioners"
    API_VERSION = "v1"

    def __init__(self):
        self.api_key = os.getenv("IQVIA_ONEKEY_API_KEY", "")

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not self.api_key:
            return SourceResult.unavailable(
                "IQVIA_ONEKEY", "IQVIA_ONEKEY_API_KEY not configured (pending ODC)", qp, self.API_VERSION
            )
        try:
            resp = await _get_with_retry(
                self.BASE_URL, params={"npi": npi},
                headers={**HTTP_HEADERS, "X-API-Key": self.api_key},
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("IQVIA_ONEKEY", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            results = payload.get("results", [])
            return SourceResult.ok(
                "IQVIA_ONEKEY",
                {"found": len(results) > 0, "provider": results[0] if results else None},
                qp, self.API_VERSION, raw_for_hash=payload,
            )
        except Exception as e:
            return SourceResult.unavailable("IQVIA_ONEKEY", str(e), qp, self.API_VERSION)

    async def probe(self) -> bool:
        return False if not self.api_key else False  # no public health endpoint


# ─── helpers ─────────────────────────────────────────────────────────────────

def _entity_type_of(entity: dict) -> str:
    for t in entity.get("type", []) or []:
        for coding in t.get("coding", []) or []:
            return coding.get("code", "")
    return ""


def _extract_npi(entity: dict) -> str:
    for ident in entity.get("identifier", []) or []:
        if ident.get("system") == "http://hl7.org/fhir/sid/us-npi":
            return ident.get("value", "")
    return ""


def _extract_uei(entity: dict) -> str:
    """SAM.gov UEI from a FHIR-ish entity. UEI has no standard FHIR system, so we
    accept any identifier whose system mentions uei/sam.gov, plus common keys."""
    for ident in entity.get("identifier", []) or []:
        sysid = (ident.get("system") or "").lower()
        if "uei" in sysid or "sam.gov" in sysid or "sam-uei" in sysid:
            return ident.get("value", "")
    return entity.get("uei") or entity.get("uei_submitted") or entity.get("_uei") or ""


# ─── Source Connector Manager ────────────────────────────────────────────────

class SourceConnectorManager:
    """Coordinates all six authoritative data source connectors."""

    # Sources required for a defensible classification. If any of these is
    # unavailable for an entity, the validation engine marks it INDETERMINATE
    # and routes to Tier-2 (never auto B1).
    REQUIRED_SOURCES = ("nppes", "leie_npi", "sam_entity", "sam_exclusion", "pecos")

    def __init__(self):
        self.nppes = NPPESConnector()
        self.leie = OIGLEIEConnector()
        self.sam = SAMGovConnector()
        self.pecos = PECOSConnector()
        self.rce_directory = RCEDirectoryConnector()  # attribute name used by routes.py
        self.iqvia = IQVIAOneKeyConnector()

    async def query_all_sources(self, entity: dict) -> Dict[str, SourceResult]:
        """
        Query every authoritative source for one entity, concurrently.
        Returns a dict keyed exactly as the validation engine expects:
          nppes, leie_npi, sam_entity, sam_exclusion, pecos
        SAM is queried once; its single probe backs both the registration check
        (sam_entity) and the debarment check (sam_exclusion).
        """
        npi = _extract_npi(entity)
        uei = _extract_uei(entity)
        nppes_r, leie_r, sam_r, pecos_r = await asyncio.gather(
            self.nppes.lookup_by_npi(npi),
            self.leie.lookup_by_npi(npi),
            self.sam.lookup_by_uei(uei),   # SAM.gov is keyed on UEI, not NPI
            self.pecos.lookup_by_npi(npi),
            return_exceptions=False,
        )
        return {
            "nppes": nppes_r,
            "leie_npi": leie_r,
            "sam_entity": sam_r,
            "sam_exclusion": sam_r,
            "pecos": pecos_r,
        }

    async def validate_entity(self, entity_id: str, entity_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Resolve an entity from the TEFCA entity data by id, then query all sources.
        Returns {"entity": <org dict>, "sources": {<key>: SourceResult}} or an
        error envelope if the entity cannot be resolved.
        """
        org_result = await self.rce_directory.get_organization_by_id(entity_id)
        if not org_result.success or not org_result.data:
            return {"entity": None, "sources": {}, "error": org_result.error}
        entity = org_result.data
        if entity_type and _entity_type_of(entity) != entity_type:
            logger.info(f"entity {entity_id} type {_entity_type_of(entity)} != requested {entity_type}")
        sources = await self.query_all_sources(entity)
        return {"entity": entity, "sources": sources}

    async def health_check(self) -> Dict[str, Dict[str, Any]]:
        """
        Actively probe every source. Returns a dict keyed by source name with
        {"live": bool, "status": str, "note": str}. Never reports a hardcoded
        live status — each entry reflects a real probe.
        """
        checked_at = datetime.utcnow().isoformat()
        probes = await asyncio.gather(
            self.nppes.probe(), self.leie.probe(), self.sam.probe(),
            self.pecos.probe(), self.rce_directory.probe(),
            return_exceptions=True,
        )
        # IQVIA_ONEKEY is deliberately NOT probed here. It has no key and is
        # pending federal ODC, so it reported UNAVAILABLE on every health call --
        # which reads as "a source is down" when nothing is wrong. A connector
        # that was never provisioned is not a health finding. The class remains
        # for when the key arrives.
        names = ["NPPES", "OIG_LEIE", "SAM_GOV", "PECOS", "RCE_DIRECTORY"]
        # SAM's note is derived, not fixed. It read "(requires SAM_GOV_API_KEY)"
        # on every failure, including on environments where the key WAS set —
        # which points at the wrong fix, and did. Say which of the two states
        # this actually is.
        sam_note = "Federal Registration — GSA"
        if not getattr(self.sam, "api_key", ""):
            sam_note += " (requires SAM_GOV_API_KEY)"
        else:
            sam_note += (" (key configured; if UNAVAILABLE, api.sam.gov is not "
                         "routing — upstream, not a key problem)")
        notes = {
            "NPPES": "NPI Registry — CMS/HHS",
            "OIG_LEIE": "Exclusion List — OIG/HHS",
            "SAM_GOV": sam_note,
            "PECOS": "Provider Enrollment — CMS",
            "RCE_DIRECTORY": "TEFCA entity data — provided by ONC per contract direction",
        }
        status: Dict[str, Dict[str, Any]] = {}
        for name, probe in zip(names, probes):
            live = bool(probe) if not isinstance(probe, Exception) else False
            status[name] = {
                "live": live,
                "status": "OK" if live else "UNAVAILABLE",
                "note": notes[name],
                "checked_at": checked_at,
            }
        return status

    async def get_status(self) -> List[Dict[str, Any]]:
        """List form of health_check() — one probed entry per source."""
        hc = await self.health_check()
        return [{"name": name, **info} for name, info in hc.items()]


# ─── check_* connector wrappers (TEFCA Task 2) ───────────────────────────────
# Thin functions with a 10s per-source timeout, error handling, and per-call
# logging to tefca_connector_logs. Each logs via its OWN short-lived DB session
# so concurrent calls (check_all_connectors) never share a session.

_CHECK_TIMEOUT_SECONDS = 10.0
_check_manager_singleton = None


def _check_manager() -> "SourceConnectorManager":
    global _check_manager_singleton
    if _check_manager_singleton is None:
        _check_manager_singleton = SourceConnectorManager()
    return _check_manager_singleton


async def _log_connector_check(connector_name: str, status: str, response_ms: int) -> None:
    """Best-effort insert into tefca_connector_logs (own session, never raises)."""
    try:
        from app.core.database import async_session_maker
        from sqlalchemy import text as _sql_text
        async with async_session_maker() as s:
            await s.execute(_sql_text(
                "INSERT INTO tefca_connector_logs (id, connector_name, status, response_time_ms, checked_at) "
                "VALUES (gen_random_uuid(), :n, :st, :ms, now())"
            ), {"n": connector_name, "st": status, "ms": response_ms})
            await s.commit()
    except Exception as e:
        logger.warning(f"tefca_connector_logs insert failed for {connector_name}: {e}")


async def _timed_connector_check(coro, connector_name: str) -> SourceResult:
    import time as _time
    t0 = _time.monotonic()
    try:
        result = await asyncio.wait_for(coro, timeout=_CHECK_TIMEOUT_SECONDS)
        ms = int((_time.monotonic() - t0) * 1000)
        await _log_connector_check(connector_name, "available" if result.success else "unavailable", ms)
        return result
    except Exception as e:
        ms = int((_time.monotonic() - t0) * 1000)
        await _log_connector_check(connector_name, "unavailable", ms)
        return SourceResult.unavailable(connector_name, f"{type(e).__name__}: {e}", {}, None)


# ─── Discrepancy severity (shared with review_engine.generate_verification_summary) ─
# Ascending severity — mirrors the review_engine 4-bucket taxonomy status names.
DISCREPANCY_LEVELS = ("no_discrepancy", "minor_administrative", "inexplicable", "non_compliant")


def max_discrepancy_level(*levels: Optional[str]) -> str:
    """Return the most severe of the given discrepancy levels (ignoring None/
    unknown values). Empty/all-unknown input yields 'no_discrepancy'."""
    worst = 0
    for lv in levels:
        if lv in DISCREPANCY_LEVELS:
            worst = max(worst, DISCREPANCY_LEVELS.index(lv))
    return DISCREPANCY_LEVELS[worst]


def _extract_address(entity: dict) -> dict:
    """Normalize a submitted entity's primary address to {address_1, city, state,
    postal_code}. Handles the FHIR `address` list (mock/RCE org shape) and a flat
    dict. Returns {} when no usable address is present."""
    addr = entity.get("address")
    if isinstance(addr, list) and addr:
        addr = addr[0]
    if not isinstance(addr, dict):
        addr = {}
    line = addr.get("line")
    if isinstance(line, list):
        line = line[0] if line else None
    out = {
        "address_1": line or addr.get("address_1"),
        "city": addr.get("city"),
        "state": addr.get("state"),
        "postal_code": addr.get("postalCode") or addr.get("postal_code") or addr.get("zip"),
    }
    return out if _has_address(out) else {}


def _has_address(a: dict) -> bool:
    """True only if the address carries a matchable field (state/city/ZIP), so an
    all-empty dict never scores a spurious 'Address verified'."""
    return bool(a) and any(a.get(k) for k in ("state", "city", "postal_code"))


def _enrich_nppes(result: SourceResult, submitted_name: str, submitted_address: Optional[dict]) -> SourceResult:
    """Cross-reference a submitted name/address against an NPPES SourceResult and
    fold the verification fields into result.data. Fail-closed: an unavailable
    result (data=None) is returned untouched — never enriched into a clean value."""
    if not result.success or not result.data:
        return result
    d = result.data
    details: List[str] = []

    found = bool(d.get("found"))
    status = (d.get("status") or "ACTIVE").upper()
    enum_type = d.get("enumeration_type") or ""
    deactivated = bool(d.get("deactivation_date")) and not d.get("reactivation_date")

    npi_valid = found
    npi_active = found and not deactivated and status in ("ACTIVE", "A", "")

    if enum_type == "NPI-1":
        npi_type = "Type 1 (Individual)"
    elif enum_type == "NPI-2":
        npi_type = "Type 2 (Organization)"
    else:
        npi_type = enum_type or None

    # Name/address cross-reference only makes sense against a real found record —
    # a not-found result echoes the *submitted* name back, which must never be
    # scored as a registered-name match.
    reg_addr: Dict[str, Any] = {}
    registered_name = ""
    name_match_score: Optional[float] = None
    addr_cmp: Optional[dict] = None
    if found:
        addrs = d.get("addresses", []) or []
        reg_addr = next((a for a in addrs if a.get("address_purpose") == "LOCATION"), (addrs[0] if addrs else {}))
        registered_name = d.get("legal_name") or d.get("organization_name") or ""
        if submitted_name and registered_name:
            name_match_score = _name_similarity(submitted_name, registered_name)
        if _has_address(submitted_address or {}):
            addr_cmp = _compare_addresses(submitted_address, reg_addr)
    name_match = name_match_score is not None and name_match_score >= 0.5

    # Overall discrepancy level for this source.
    if not npi_valid:
        level = "non_compliant"
        details.append("NPI not found / not valid in NPPES")
    elif not npi_active:
        level = "non_compliant"
        details.append(f"NPI not active (status={status or 'UNKNOWN'}, deactivation_date={d.get('deactivation_date')})")
    else:
        level = "no_discrepancy"
        if name_match_score is not None:
            if name_match_score >= 0.85:
                details.append(f"Name verified ({name_match_score:.0%}): '{registered_name}'")
            elif name_match_score >= 0.5:
                level = max_discrepancy_level(level, "minor_administrative")
                details.append(f"Partial name match ({name_match_score:.0%}): '{submitted_name}' vs '{registered_name}'")
            else:
                level = max_discrepancy_level(level, "inexplicable")
                details.append(f"NAME MISMATCH ({name_match_score:.0%}): '{submitted_name}' vs '{registered_name}'")
        if addr_cmp:
            details.extend(addr_cmp["details"])
            if addr_cmp["level"] == "major":
                level = max_discrepancy_level(level, "inexplicable")
            elif addr_cmp["level"] == "minor":
                level = max_discrepancy_level(level, "minor_administrative")

    d.update({
        "npi_valid": npi_valid,
        "npi_active": npi_active,
        "npi_type": npi_type,
        "name_match": name_match,
        "name_match_score": (round(name_match_score, 3) if name_match_score is not None else None),
        "address_match": (addr_cmp["match"] if addr_cmp else None),
        "address_discrepancy": (addr_cmp["level"] if addr_cmp else None),
        "taxonomy": d.get("taxonomy"),
        "taxonomy_code": d.get("taxonomy_code"),
        "enumeration_date": d.get("enumeration_date"),
        "registered_address": reg_addr,
        "discrepancy_level": level,
        "details": details,
    })
    return result


async def check_nppes(name: str = "", npi: str = "", address: Optional[dict] = None) -> SourceResult:
    """Enhanced NPI verification.

    Looks the provider up in NPPES — by NPI, or by organization name when no NPI
    is available — then cross-references the submitted name and address against
    the registered record. Returns a SourceResult whose .data carries the
    verification fields (npi_valid, npi_active, npi_type, name_match[_score],
    address_match, address_discrepancy, taxonomy, enumeration_date,
    registered_address, discrepancy_level, details[]). SourceResult.get() exposes
    them dict-style (r.get("npi_valid")).

    Back-compat: a lone positional 10-digit value — check_nppes("1234567893"),
    as the QA health probe and legacy callers invoke it — is treated as the NPI.
    """
    if not npi and name and name.isdigit() and len(name) == 10:
        npi, name = name, ""
    mgr = _check_manager()
    coro = mgr.nppes.lookup_by_npi(npi) if npi else mgr.nppes.lookup_by_name(name)
    result = await _timed_connector_check(coro, "NPPES")
    return _enrich_nppes(result, name, address)


async def check_pecos(npi: str) -> SourceResult:
    return await _timed_connector_check(_check_manager().pecos.lookup_by_npi(npi), "PECOS")


async def check_sam(uei: str = "", address: Optional[dict] = None) -> SourceResult:
    """SAM.gov registration/exclusion by UEI. When `address` is supplied and the
    entity is found, cross-reference it against SAM's registered physical address
    (additive fields: address_match, address_discrepancy, details)."""
    result = await _timed_connector_check(_check_manager().sam.lookup_by_uei(uei), "SAM_GOV")
    if _has_address(address or {}) and result.success and result.data and result.data.get("found"):
        cmp = _compare_addresses(address, result.data.get("registered_address") or {})
        result.data["address_match"] = cmp["match"]
        result.data["address_discrepancy"] = cmp["level"]
        result.data.setdefault("details", []).extend(cmp["details"])
    return result


async def check_leie(name: str = "", npi: str = "") -> SourceResult:
    mgr = _check_manager()
    coro = mgr.leie.lookup_by_npi(npi) if npi else mgr.leie.lookup_by_name(last=name)
    return await _timed_connector_check(coro, "OIG_LEIE")


async def check_all_connectors(entity: dict, db=None) -> dict:
    """Run NPPES/PECOS/SAM/LEIE concurrently for one entity. `db` is accepted for
    API symmetry; logging uses each check's own session (concurrency-safe).

    Submitted name + address are threaded into the NPPES and SAM checks so their
    results carry the name/address cross-reference used by the verification
    summary."""
    npi = _extract_npi(entity)
    uei = _extract_uei(entity)
    name = entity.get("name", "")
    address = _extract_address(entity)
    nppes_r, pecos_r, sam_r, leie_r = await asyncio.gather(
        check_nppes(name, npi, address), check_pecos(npi), check_sam(uei, address), check_leie(name, npi),
    )
    return {"nppes": nppes_r, "pecos": pecos_r, "sam": sam_r, "leie": leie_r}
