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
            r = results[0]
            basic = r.get("basic", {})
            org_name = basic.get("organization_name") or (
                f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
            )
            data = {
                "found": True,
                "npi": str(r.get("number", npi)),
                "enumeration_type": r.get("enumeration_type"),  # NPI-1 / NPI-2
                "status": (basic.get("status") or "ACTIVE"),
                "legal_name": org_name,
                "organization_name": basic.get("organization_name"),
                "credential": basic.get("credential"),
                "addresses": r.get("addresses", []),
                "deactivation_date": basic.get("deactivation_date"),
                "reactivation_date": basic.get("reactivation_date"),
            }
            return SourceResult.ok("NPPES", data, qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"NPPES unavailable for NPI {npi}: {e}")
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

class OIGLEIEConnector:
    BASE_URL = "https://exclusions.oig.hhs.gov/api/1.0/exclusions/"
    API_VERSION = "1.0"

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not npi:
            return SourceResult.ok(
                "OIG_LEIE",
                {"excluded": False, "active_exclusions": [], "historical_exclusions": [],
                 "reason": "no_npi_submitted"},
                qp, self.API_VERSION,
            )
        try:
            resp = await _get_with_retry(
                self.BASE_URL,
                params={"npi": npi, "format": "json"},
                headers=HTTP_HEADERS,
            )
            if resp.status_code != 200:
                # Outage must NOT read as "not excluded".
                return SourceResult.unavailable("OIG_LEIE", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            exclusions = payload.get("results", payload if isinstance(payload, list) else [])
            active = [e for e in exclusions if not e.get("reinstatement_date")]
            historical = [e for e in exclusions if e.get("reinstatement_date")]
            data = {
                "excluded": len(active) > 0,
                "active_exclusions": active[:5],
                "historical_exclusions": historical[:5],
                "exclusion_count": len(exclusions),
            }
            return SourceResult.ok("OIG_LEIE", data, qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"OIG LEIE unavailable for NPI {npi}: {e}")
            return SourceResult.unavailable("OIG_LEIE", str(e), qp, self.API_VERSION)

    async def probe(self) -> bool:
        try:
            resp = await _get_with_retry(
                self.BASE_URL, params={"npi": "1234567893", "format": "json"},
                headers=HTTP_HEADERS, timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─── SAM.gov Federal Registration & Exclusions (GSA) ─────────────────────────

class SAMGovConnector:
    BASE_URL = "https://api.sam.gov/entity-information/v3/entities"
    API_VERSION = "v3"

    def __init__(self):
        # Read the key at INSTANTIATION, not at class-definition/import time,
        # so a key loaded after import (e.g. via .env) is honored.
        self.api_key = os.getenv("SAM_GOV_API_KEY", "")

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not self.api_key:
            # No key = we genuinely cannot verify. Fail closed (not "clean").
            return SourceResult.unavailable(
                "SAM_GOV", "SAM_GOV_API_KEY not configured", qp, self.API_VERSION
            )
        if not npi:
            return SourceResult.ok(
                "SAM_GOV",
                {"found": False, "registration_current": None, "excluded": False,
                 "reason": "no_npi_submitted"},
                qp, self.API_VERSION,
            )
        try:
            resp = await _get_with_retry(
                self.BASE_URL,
                params={
                    "npi": npi,
                    "includeSections": "entityRegistration,coreData",
                    "api_key": self.api_key,
                },
                headers=HTTP_HEADERS,
            )
            if resp.status_code != 200:
                return SourceResult.unavailable("SAM_GOV", f"HTTP {resp.status_code}", qp, self.API_VERSION)
            payload = resp.json()
            entities = payload.get("entityData", [])
            if not entities:
                return SourceResult.ok(
                    "SAM_GOV", {"found": False, "registration_current": None, "excluded": False},
                    qp, self.API_VERSION, raw_for_hash=payload,
                )
            reg = entities[0].get("entityRegistration", {})
            status = (reg.get("registrationStatus") or "").upper()
            data = {
                "found": True,
                "uei": reg.get("ueiSAM"),
                "legal_name": reg.get("legalBusinessName"),
                "registration_status": reg.get("registrationStatus"),
                "registration_current": status == "ACTIVE",
                "registration_expiry": reg.get("registrationExpirationDate"),
                "excluded": reg.get("exclusionStatusFlag") == "Y",
            }
            return SourceResult.ok("SAM_GOV", data, qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"SAM.gov unavailable for NPI {npi}: {e}")
            return SourceResult.unavailable("SAM_GOV", str(e), qp, self.API_VERSION)

    async def probe(self) -> bool:
        if not self.api_key:
            return False
        try:
            resp = await _get_with_retry(
                self.BASE_URL,
                params={"includeSections": "entityRegistration", "api_key": self.api_key,
                        "samRegistrationStatus": "A"},
                headers=HTTP_HEADERS, timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─── PECOS Provider Enrollment (CMS) ─────────────────────────────────────────

class PECOSConnector:
    """
    Public CMS provider enrollment data. Enhanced real-time PECOS access
    (payment suspension flags) is provided by the ONC COR at contract award.
    The public dataset does not expose suspension flags, so payment_suspension
    is reported only when the enhanced feed is available; absence is recorded
    honestly as "not available from this source", never as a verified clean.
    """
    DATASET_URL = "https://data.cms.gov/provider-data/api/1/datastore/query/mj5m-pzi6"
    API_VERSION = "1.0"

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
                self.DATASET_URL,
                params={
                    "conditions[0][property]": "NPI",
                    "conditions[0][value]": npi,
                    "conditions[0][operator]": "=",
                    "limit": 1,
                },
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
                     "note": "NPI not present in public PECOS enrollment dataset."},
                    qp, self.API_VERSION, raw_for_hash=payload,
                )
            r = results[0]
            data = {
                "found": True,
                "npi": r.get("NPI"),
                "provider_last_name": r.get("Lst_Nm"),
                "provider_first_name": r.get("Frst_Nm"),
                "provider_type": r.get("Rndrng_Prvdr_Type"),
                "state": r.get("Rndrng_Prvdr_State_Abrvtn"),
                # Public dataset has no suspension flag — None = "not provided by
                # this source", distinct from False ("verified not suspended").
                "payment_suspension": None,
                "note": "Public PECOS data. Suspension flags require COR-provisioned feed.",
            }
            return SourceResult.ok("PECOS", data, qp, self.API_VERSION, raw_for_hash=payload)
        except Exception as e:
            logger.warning(f"PECOS unavailable for NPI {npi}: {e}")
            return SourceResult.unavailable("PECOS", str(e), qp, self.API_VERSION)

    async def probe(self) -> bool:
        try:
            resp = await _get_with_retry(
                self.DATASET_URL, params={"limit": 1},
                headers=HTTP_HEADERS, timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ─── RCE Directory (Sequoia Project) — FHIR R4 ───────────────────────────────

class RCEDirectoryConnector:
    """
    FHIR R4 Organization endpoint. Live API key pending — Case #00055525 with
    the Sequoia Project (techsupport@sequoiaproject.org).

    When RCE_DIRECTORY_API_KEY is set, organizations are pulled live. When it is
    not, get_all_organizations / get_organization_by_id serve the bundled
    development dataset, clearly flagged data_source="MOCK". The validation
    engine refuses to auto-classify MOCK-sourced entities as Bucket 1, so mock
    development data can never masquerade as a finalized clean finding.
    """
    BASE_URL = "https://rce.sequoiaproject.org/fhir/r4"
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
            logger.warning(f"RCE Directory unavailable: {e}")
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
            logger.warning(f"RCE Directory unavailable for {rce_id}: {e}")
            return SourceResult.unavailable("RCE_DIRECTORY", str(e), qp, self.API_VERSION)

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi}
        if not self._is_live():
            return SourceResult.unavailable(
                "RCE_DIRECTORY", "live API key pending (Sequoia Case #00055525)", qp, self.API_VERSION
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
        nppes_r, leie_r, sam_r, pecos_r = await asyncio.gather(
            self.nppes.lookup_by_npi(npi),
            self.leie.lookup_by_npi(npi),
            self.sam.lookup_by_npi(npi),
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
        Resolve an entity from the RCE Directory by id, then query all sources.
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
            self.pecos.probe(), self.rce_directory.probe(), self.iqvia.probe(),
            return_exceptions=True,
        )
        names = ["NPPES", "OIG_LEIE", "SAM_GOV", "PECOS", "RCE_DIRECTORY", "IQVIA_ONEKEY"]
        notes = {
            "NPPES": "NPI Registry — CMS/HHS",
            "OIG_LEIE": "Exclusion List — OIG/HHS",
            "SAM_GOV": "Federal Registration — GSA (requires SAM_GOV_API_KEY)",
            "PECOS": "Provider Enrollment — CMS",
            "RCE_DIRECTORY": "FHIR R4 — Sequoia Project (key pending Case #00055525)",
            "IQVIA_ONEKEY": "Provider hierarchy — pending federal ODC",
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
