"""
DocuAction TEFCA Review Protocol
Data Source Connectors — All 6 sources

LIVE (build today):
  - NPPES:    npiregistry.cms.hhs.gov/api  (no auth)
  - OIG LEIE: exclusions.oig.hhs.gov       (no auth)
  - SAM.gov:  api.sam.gov                  (needs SAM_GOV_API_KEY)
  - PECOS:    data.cms.gov                 (public endpoint)

MOCK (replace when access arrives):
  - RCE Directory: techsupport@sequoiaproject.org
  - IQVIA OneKey:  federalsales@iqvia.com  (contract award ODC)
"""

import os
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
import httpx
from .mock_data import ALL_MOCK_ENTITIES, get_mock_entity_by_npi

logger = logging.getLogger(__name__)

# ─── Environment Variables ────────────────────────────────────────────────────
SAM_GOV_API_KEY = os.getenv("SAM_GOV_API_KEY", "")
RCE_DIRECTORY_API_KEY = os.getenv("RCE_DIRECTORY_API_KEY", "")   # pending
RCE_DIRECTORY_BASE_URL = os.getenv(
    "RCE_DIRECTORY_BASE_URL",
    "https://rce.sequoiaproject.org/api"                         # pending
)
ONEKEY_API_KEY = os.getenv("ONEKEY_API_KEY", "")                 # pending contract award

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
HTTP_HEADERS = {"User-Agent": "AGT-DocuAction-TEFCA/1.0 (imran@agtbi.com)"}


# ─── Source Response Container ───────────────────────────────────────────────

class SourceResult:
    def __init__(
        self,
        source_name: str,
        success: bool,
        data: dict | None = None,
        error: str | None = None,
        query_params: dict | None = None,
        api_version: str = "1.0",
    ):
        self.source_name = source_name
        self.success = success
        self.data = data or {}
        self.error = error
        self.query_params = query_params or {}
        self.query_timestamp = datetime.utcnow().isoformat()
        self.response_hash = self._hash()
        self.api_version = api_version

    def _hash(self) -> str:
        content = json.dumps(self.data, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "query_params": self.query_params,
            "query_timestamp": self.query_timestamp,
            "response_hash": self.response_hash,
            "api_version": self.api_version,
        }


# ─── NPPES Connector ─────────────────────────────────────────────────────────

class NPPESConnector:
    """
    National Plan & Provider Enumeration System
    Free public API — no authentication required.
    https://npiregistry.cms.hhs.gov/api/?version=2.1
    """
    BASE_URL = "https://npiregistry.cms.hhs.gov/api/"
    VERSION = "2.1"

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        """Look up a provider/organization by NPI number."""
        params = {"number": npi, "version": self.VERSION}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=HTTP_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return SourceResult("NPPES", True, {"found": False, "npi": npi}, query_params=params)
                entity = results[0]
                return SourceResult("NPPES", True, {
                    "found": True,
                    "npi": entity.get("number"),
                    "enumeration_type": entity.get("enumeration_type"),
                    "status": entity.get("basic", {}).get("status", "").upper(),
                    "legal_name": self._extract_name(entity),
                    "addresses": entity.get("addresses", []),
                    "taxonomies": entity.get("taxonomies", []),
                    "endpoints": entity.get("endpoints", []),
                    "last_updated": entity.get("last_updated"),
                    "basic": entity.get("basic", {}),
                }, query_params=params, api_version=self.VERSION)
        except httpx.HTTPStatusError as e:
            logger.error(f"NPPES HTTP error for NPI {npi}: {e}")
            return SourceResult("NPPES", False, error=f"HTTP {e.response.status_code}", query_params=params)
        except Exception as e:
            logger.error(f"NPPES error for NPI {npi}: {e}")
            return SourceResult("NPPES", False, error=str(e), query_params=params)

    async def search_by_name(self, org_name: str, state: str = "") -> SourceResult:
        """Search organizations by name."""
        params = {
            "organization_name": org_name,
            "enumeration_type": "NPI-2",
            "version": self.VERSION,
            "limit": 5,
        }
        if state:
            params["state"] = state
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(self.BASE_URL, params=params, headers=HTTP_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                return SourceResult("NPPES", True, {
                    "results": data.get("results", []),
                    "result_count": data.get("result_count", 0),
                }, query_params=params, api_version=self.VERSION)
        except Exception as e:
            logger.error(f"NPPES name search error: {e}")
            return SourceResult("NPPES", False, error=str(e), query_params=params)

    def _extract_name(self, entity: dict) -> str:
        """Extract the legal name from NPPES entity."""
        basic = entity.get("basic", {})
        enum_type = entity.get("enumeration_type", "")
        if enum_type == "NPI-2":
            return basic.get("name") or basic.get("organization_name", "")
        else:
            first = basic.get("first_name", "")
            last = basic.get("last_name", "")
            cred = basic.get("credential", "")
            return f"{first} {last} {cred}".strip()


# ─── OIG LEIE Connector ──────────────────────────────────────────────────────

class LEIEConnector:
    """
    OIG List of Excluded Individuals/Entities
    Free public API + monthly bulk download.
    https://exclusions.oig.hhs.gov
    """
    SEARCH_URL = "https://exclusions.oig.hhs.gov/api/search.json"
    BULK_URL = "https://oig.hhs.gov/exclusions/exclusions_dlp.asp"

    async def check_exclusion_by_npi(self, npi: str) -> SourceResult:
        """Check if entity is excluded using NPI."""
        params = {"npi": npi}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(self.SEARCH_URL, params=params, headers=HTTP_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                exclusions = data if isinstance(data, list) else data.get("exclusions", [])
                active = [e for e in exclusions if not e.get("reinstatement_date") or
                         self._is_future(e.get("reinstatement_date"))]
                return SourceResult("OIG_LEIE", True, {
                    "npi": npi,
                    "excluded": len(active) > 0,
                    "active_exclusions": active,
                    "historical_exclusions": [e for e in exclusions if e not in active],
                    "exclusion_count": len(exclusions),
                }, query_params=params)
        except Exception as e:
            logger.error(f"LEIE NPI check error for {npi}: {e}")
            return SourceResult("OIG_LEIE", False, error=str(e), query_params=params)

    async def check_exclusion_by_name(self, org_name: str) -> SourceResult:
        """Check exclusion by organization name."""
        params = {"name": org_name}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(self.SEARCH_URL, params=params, headers=HTTP_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                exclusions = data if isinstance(data, list) else data.get("exclusions", [])
                active = [e for e in exclusions if not e.get("reinstatement_date") or
                         self._is_future(e.get("reinstatement_date"))]
                return SourceResult("OIG_LEIE", True, {
                    "org_name": org_name,
                    "excluded": len(active) > 0,
                    "active_exclusions": active,
                    "exclusion_count": len(exclusions),
                }, query_params=params)
        except Exception as e:
            logger.error(f"LEIE name check error: {e}")
            return SourceResult("OIG_LEIE", False, error=str(e), query_params=params)

    def _is_future(self, date_str: str) -> bool:
        if not date_str:
            return False
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d")
            return d > datetime.utcnow()
        except Exception:
            return False


# ─── SAM.gov Connector ────────────────────────────────────────────────────────

class SAMGovConnector:
    """
    System for Award Management
    Register at api.data.gov for API key (10 min).
    SAM_GOV_API_KEY env variable required.
    """
    ENTITY_URL = "https://api.sam.gov/entity-information/v3/entities"
    EXCLUSIONS_URL = "https://api.sam.gov/entity-information/v3/exclusions"

    def _headers(self) -> dict:
        return {**HTTP_HEADERS, "X-Api-Key": SAM_GOV_API_KEY}

    async def check_entity(self, legal_name: str = "", uei: str = "") -> SourceResult:
        """Check SAM.gov entity registration status."""
        if not SAM_GOV_API_KEY:
            return SourceResult("SAM_GOV", False,
                error="SAM_GOV_API_KEY not set. Register at api.data.gov to get key.")

        params = {
            "samRegistered": "Yes",
            "includeSections": "entityRegistration,coreData",
        }
        if uei:
            params["ueiSAM"] = uei
        elif legal_name:
            params["legalBusinessName"] = legal_name

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    self.ENTITY_URL, params=params, headers=self._headers()
                )
                resp.raise_for_status()
                data = resp.json()
                entities = data.get("entityData", [])
                if not entities:
                    return SourceResult("SAM_GOV", True, {
                        "found": False,
                        "legal_name": legal_name,
                        "uei": uei,
                    }, query_params=params)
                entity = entities[0]
                reg = entity.get("entityRegistration", {})
                expiry = reg.get("registrationExpirationDate")
                return SourceResult("SAM_GOV", True, {
                    "found": True,
                    "uei": reg.get("ueiSAM"),
                    "legal_name": reg.get("legalBusinessName"),
                    "dba_name": reg.get("dbaName"),
                    "registration_status": reg.get("registrationStatus"),
                    "registration_expiry": expiry,
                    "active_exclusion": reg.get("activeExclusion", False),
                    "physical_address": entity.get("coreData", {}).get("physicalAddress", {}),
                    "registration_current": self._is_current(expiry),
                }, query_params=params)
        except Exception as e:
            logger.error(f"SAM.gov entity check error: {e}")
            return SourceResult("SAM_GOV", False, error=str(e), query_params=params)

    async def check_exclusion(self, legal_name: str = "", uei: str = "") -> SourceResult:
        """Check SAM.gov exclusions."""
        if not SAM_GOV_API_KEY:
            return SourceResult("SAM_GOV_EXCLUSIONS", False,
                error="SAM_GOV_API_KEY not set. Register at api.data.gov.")

        params: dict = {}
        if uei:
            params["ueiSAM"] = uei
        elif legal_name:
            params["exclusionName"] = legal_name

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    self.EXCLUSIONS_URL, params=params, headers=self._headers()
                )
                resp.raise_for_status()
                data = resp.json()
                exclusions = data.get("exclusionData", [])
                active = [e for e in exclusions
                         if not e.get("terminationDate") or
                         self._is_future(e.get("terminationDate"))]
                return SourceResult("SAM_GOV_EXCLUSIONS", True, {
                    "excluded": len(active) > 0,
                    "active_exclusions": active,
                    "total_exclusions": len(exclusions),
                }, query_params=params)
        except Exception as e:
            logger.error(f"SAM.gov exclusion check error: {e}")
            return SourceResult("SAM_GOV_EXCLUSIONS", False, error=str(e), query_params=params)

    def _is_current(self, expiry_str: str | None) -> bool:
        if not expiry_str:
            return False
        try:
            d = datetime.strptime(expiry_str[:10], "%Y-%m-%d")
            return d > datetime.utcnow()
        except Exception:
            return False

    def _is_future(self, date_str: str) -> bool:
        if not date_str:
            return False
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d")
            return d > datetime.utcnow()
        except Exception:
            return False


# ─── PECOS Connector (Public) ─────────────────────────────────────────────────

class PECOSConnector:
    """
    Provider Enrollment, Chain & Ownership System
    Public data via CMS data.cms.gov (no auth required).
    Enhanced real-time access provided by ONC COR at contract award.
    """
    BASE_URL = "https://data.cms.gov/provider-data/api/1/datastore/query"
    DATASET_ID = "mj5m-pzi6"  # Medicare Physician & Other Practitioners

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        """Look up provider enrollment data from PECOS public dataset."""
        params = {
            "conditions[0][property]": "NPI",
            "conditions[0][value]": npi,
            "conditions[0][operator]": "=",
            "limit": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{self.BASE_URL}/{self.DATASET_ID}"
                resp = await client.get(url, params=params, headers=HTTP_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return SourceResult("PECOS", True, {
                        "found": False, "npi": npi,
                        "note": "Not found in PECOS public dataset (Part B claims data)."
                    }, query_params={"npi": npi})
                r = results[0]
                return SourceResult("PECOS", True, {
                    "found": True,
                    "npi": r.get("NPI"),
                    "provider_last_name": r.get("Lst_Nm"),
                    "provider_first_name": r.get("Frst_Nm"),
                    "provider_type": r.get("Rndrng_Prvdr_Type"),
                    "city": r.get("Rndrng_Prvdr_City"),
                    "state": r.get("Rndrng_Prvdr_State_Abrvtn"),
                    "zip": r.get("Rndrng_Prvdr_Zip5"),
                    "credentials": r.get("Rndrng_Prvdr_Crdntls"),
                    "enrl_id": r.get("Rndrng_Prvdr_Enrlmt_ID"),
                    "payment_suspension": False,  # Full flag requires enhanced PECOS API via COR
                    "note": "Public PECOS data. Enhanced access (payment suspension, ownership chain) via COR at contract award."
                }, query_params={"npi": npi})
        except Exception as e:
            logger.error(f"PECOS lookup error for NPI {npi}: {e}")
            return SourceResult("PECOS", False, error=str(e), query_params={"npi": npi})


# ─── RCE Directory Connector (MOCK until API key arrives) ────────────────────

class RCEDirectoryConnector:
    """
    Sequoia Project RCE Directory Service
    FHIR R4 Organization resources for all TEFCA QHINs, Participants, Subparticipants.

    STATUS: MOCK — API key pending from Sequoia Project.
    Email sent to: techsupport@sequoiaproject.org
    When key arrives: set RCE_DIRECTORY_API_KEY and RCE_DIRECTORY_BASE_URL env vars.
    """

    def _is_live(self) -> bool:
        return bool(RCE_DIRECTORY_API_KEY and RCE_DIRECTORY_BASE_URL)

    def _headers(self) -> dict:
        return {**HTTP_HEADERS, "Authorization": f"Bearer {RCE_DIRECTORY_API_KEY}"}

    async def get_all_organizations(
        self,
        entity_type: str | None = None,
        qhin_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> SourceResult:
        """Fetch all organizations from RCE Directory (or mock)."""
        if self._is_live():
            return await self._live_get_organizations(entity_type, limit, offset)
        return self._mock_get_organizations(entity_type, qhin_name, limit, offset)

    async def get_organization_by_id(self, rce_id: str) -> SourceResult:
        """Get single organization by RCE resource ID."""
        if self._is_live():
            return await self._live_get_by_id(rce_id)
        return self._mock_get_by_id(rce_id)

    async def _live_get_organizations(
        self, entity_type: str | None, limit: int, offset: int
    ) -> SourceResult:
        """LIVE: Query real RCE Directory FHIR endpoint."""
        params: dict = {
            "_format": "json",
            "_count": limit,
            "_offset": offset,
        }
        if entity_type:
            params["type"] = entity_type
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{RCE_DIRECTORY_BASE_URL}/Organization"
                resp = await client.get(url, params=params, headers=self._headers())
                resp.raise_for_status()
                bundle = resp.json()
                entries = bundle.get("entry", [])
                return SourceResult("RCE_DIRECTORY", True, {
                    "total": bundle.get("total", len(entries)),
                    "organizations": [e.get("resource") for e in entries],
                    "live": True,
                }, query_params=params)
        except Exception as e:
            logger.error(f"RCE Directory live error: {e}")
            return SourceResult("RCE_DIRECTORY", False, error=str(e), query_params=params)

    async def _live_get_by_id(self, rce_id: str) -> SourceResult:
        """LIVE: Get single organization by ID."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{RCE_DIRECTORY_BASE_URL}/Organization/{rce_id}"
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                return SourceResult("RCE_DIRECTORY", True, resp.json(), query_params={"id": rce_id})
        except Exception as e:
            logger.error(f"RCE Directory live get error: {e}")
            return SourceResult("RCE_DIRECTORY", False, error=str(e))

    def _mock_get_organizations(
        self, entity_type: str | None, qhin_name: str | None, limit: int, offset: int
    ) -> SourceResult:
        """MOCK: Return sample entities from mock_data.py."""
        entities = ALL_MOCK_ENTITIES
        if entity_type:
            entities = [e for e in entities if
                       any(c.get("code") == entity_type
                           for t in e.get("type", [])
                           for c in t.get("coding", []))]
        if qhin_name:
            entities = [e for e in entities if e.get("_qhin") == qhin_name]
        page = entities[offset: offset + limit]
        return SourceResult("RCE_DIRECTORY", True, {
            "total": len(entities),
            "organizations": page,
            "live": False,
            "mock_note": "MOCK DATA — RCE API key pending from techsupport@sequoiaproject.org",
        })

    def _mock_get_by_id(self, rce_id: str) -> SourceResult:
        """MOCK: Get single organization by ID from mock data."""
        from .mock_data import MOCK_ENTITY_INDEX
        entity = MOCK_ENTITY_INDEX.get(rce_id)
        if entity:
            return SourceResult("RCE_DIRECTORY", True, entity)
        return SourceResult("RCE_DIRECTORY", True, {"found": False, "id": rce_id})


# ─── IQVIA OneKey Connector (MOCK until contract award) ──────────────────────

class OneKeyConnector:
    """
    IQVIA OneKey — Corporate hierarchy, entity type, practitioner affiliations.
    STATUS: MOCK — Commercial license pending contract award.
    Contact: federalsales@iqvia.com (bid as ODC)
    """

    MOCK_DATA = {
        "1003000126": {"orgType": "Health System", "parentOrg": None, "subsidiaryCount": 12},
        "1023011403": {"orgType": "Physician Group", "parentOrg": "Regional Health Network MD", "subsidiaryCount": 0},
        "1194840903": {"orgType": "Physical Therapy Practice", "parentOrg": None, "subsidiaryCount": 0,
                       "_note": "CONFLICT: submitted as Health Partner Network but OneKey shows PT practice"},
        "1275660135": {"orgType": "Management Company", "parentOrg": "Offshore Holdings LLC",
                       "_note": "Non-healthcare parent entity — flag for review"},
        "1285762539": {"orgType": "Consulting Firm", "parentOrg": "Federal Contractor Group",
                       "_note": "SAM.gov debarment confirmed"},
    }

    async def get_organization(self, npi: str, org_name: str = "") -> SourceResult:
        """Get organizational hierarchy data (MOCK)."""
        mock = self.MOCK_DATA.get(npi, {
            "orgType": "Unknown",
            "parentOrg": None,
            "subsidiaryCount": 0,
        })
        return SourceResult("IQVIA_ONEKEY", True, {
            **mock,
            "npi": npi,
            "live": False,
            "mock_note": "MOCK DATA — IQVIA OneKey license pending contract award (ODC). Contact federalsales@iqvia.com",
        })


# ─── Source Connector Manager ─────────────────────────────────────────────────

class SourceConnectorManager:
    """
    Orchestrates all data source queries in parallel.
    Returns combined results for validation engine.
    """

    def __init__(self):
        self.nppes = NPPESConnector()
        self.leie = LEIEConnector()
        self.sam_gov = SAMGovConnector()
        self.pecos = PECOSConnector()
        self.rce_directory = RCEDirectoryConnector()
        self.onekey = OneKeyConnector()

    async def query_all_sources(self, entity: dict) -> dict[str, SourceResult]:
        """
        Run all authoritative source queries in parallel for an entity.
        Returns dict of source_name -> SourceResult.
        """
        npi = self._extract_npi(entity)
        legal_name = entity.get("name", "")
        uei = self._extract_uei(entity)

        tasks = {
            "nppes": self.nppes.lookup_by_npi(npi) if npi else self._no_npi_result("NPPES"),
            "leie_npi": self.leie.check_exclusion_by_npi(npi) if npi else self._no_npi_result("OIG_LEIE"),
            "leie_name": self.leie.check_exclusion_by_name(legal_name) if legal_name else self._empty_result("OIG_LEIE"),
            "sam_entity": self.sam_gov.check_entity(legal_name=legal_name, uei=uei),
            "sam_exclusion": self.sam_gov.check_exclusion(legal_name=legal_name, uei=uei),
            "pecos": self.pecos.lookup_by_npi(npi) if npi else self._no_npi_result("PECOS"),
            "onekey": self.onekey.get_organization(npi, legal_name) if npi else self._no_npi_result("IQVIA_ONEKEY"),
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        output: dict[str, SourceResult] = {}
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                output[key] = SourceResult(key.upper(), False, error=str(result))
            else:
                output[key] = result
        return output

    async def health_check(self) -> dict[str, dict]:
        """Check connectivity to all data sources."""
        results = {}

        # NPPES
        try:
            r = await self.nppes.lookup_by_npi("1003000126")
            results["NPPES"] = {"status": "OK" if r.success else "ERROR",
                               "live": True, "requires_key": False}
        except Exception as e:
            results["NPPES"] = {"status": "ERROR", "error": str(e)}

        # OIG LEIE
        try:
            r = await self.leie.check_exclusion_by_npi("1003000126")
            results["OIG_LEIE"] = {"status": "OK" if r.success else "ERROR",
                                   "live": True, "requires_key": False}
        except Exception as e:
            results["OIG_LEIE"] = {"status": "ERROR", "error": str(e)}

        # SAM.gov
        results["SAM_GOV"] = {
            "status": "OK" if SAM_GOV_API_KEY else "NO_KEY",
            "live": bool(SAM_GOV_API_KEY),
            "requires_key": True,
            "note": "" if SAM_GOV_API_KEY else "Register at api.data.gov — 10 min process",
        }

        # PECOS
        try:
            r = await self.pecos.lookup_by_npi("1003000126")
            results["PECOS"] = {"status": "OK" if r.success else "ERROR",
                               "live": True, "requires_key": False}
        except Exception as e:
            results["PECOS"] = {"status": "ERROR", "error": str(e)}

        # RCE Directory
        results["RCE_DIRECTORY"] = {
            "status": "MOCK" if not RCE_DIRECTORY_API_KEY else "OK",
            "live": bool(RCE_DIRECTORY_API_KEY),
            "requires_key": True,
            "note": "Email techsupport@sequoiaproject.org for API key",
        }

        # IQVIA OneKey
        results["IQVIA_ONEKEY"] = {
            "status": "MOCK",
            "live": False,
            "requires_key": True,
            "note": "Pending contract award ODC — federalsales@iqvia.com",
        }

        return results

    def _extract_npi(self, entity: dict) -> str:
        for ident in entity.get("identifier", []):
            if ident.get("system") == "http://hl7.org/fhir/sid/us-npi":
                return ident.get("value", "")
        return ""

    def _extract_uei(self, entity: dict) -> str:
        for ident in entity.get("identifier", []):
            if "sam" in ident.get("system", "").lower():
                return ident.get("value", "")
        return ""

    async def _no_npi_result(self, source: str) -> SourceResult:
        return SourceResult(source, True, {"found": False, "reason": "No NPI in submission"})

    async def _empty_result(self, source: str) -> SourceResult:
        return SourceResult(source, True, {})
