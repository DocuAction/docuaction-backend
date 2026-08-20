"""
PPEF resource discovery — where the sub-files actually live.

A CORRECTION, RECORDED DELIBERATELY
───────────────────────────────────
An earlier pass concluded that CMS does not publish the four PPEF relational
sub-files. That conclusion was wrong, and the way it was reached is worth
keeping so it is not repeated: it rested on the DCAT catalogue
(https://data.cms.gov/data.json) and on the data-api, and BOTH of those are
genuinely silent about the sub-files.

  * The DCAT catalogue carries ONE PPEF dataset entry. All 159 dataset titles
    were listed and read: there is no Reassignment, Practice Location, Address,
    Additional NPIs or Secondary Specialty dataset under any name.
  * Its five distributions are three API versions and two CSV versions of the
    same Enrollment extract.
  * A catalogue-wide search of every distribution across all 159 datasets
    matched only the Revalidation products, which are a different dataset and
    which the specification forbids substituting.

All of that was true and none of it was the whole picture. The sub-files are
published as ANCILLARY RESOURCES of the parent PPEF dataset, and they are
listed by an endpoint the catalogue does not mention:

    GET https://data.cms.gov/data-api/v1/dataset/{parent_uuid}/resources

That returns 11 resources for PPEF, four of which are the sub-files. Their
file_uuids are media identifiers, NOT data-api dataset ids — all four return
404 against /data-api/v1/dataset/{uuid}/data, which is exactly why they must be
classified DOWNLOAD_AVAILABLE and not API_AVAILABLE. Verified live 2026-08-19.

CMS NAMING (why file_name and not title decides the mapping)
────────────────────────────────────────────────────────────
CMS titles the practice-location resource "Address Sub-File Q3 2026" while
naming the file PPEF_Practice_Location_Extract_2026.07.17.csv. Same capability,
two names, and the specification calls this out: normalise internally to
PPEF_PRACTICE_LOCATION, keep the exact CMS title in provenance, and do NOT
invent a separate "Address" capability alongside "Practice Location".

Component identification therefore keys on the FILE NAME, which carries the
CMS-internal structural name, and the display title is carried along untouched
for the audit trail.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.Tefca.connectors import HTTP_HEADERS, _get_with_retry

logger = logging.getLogger(__name__)

CMS_DATA_API_ROOT = "https://data.cms.gov/data-api/v1/dataset"

PPEF_PARENT_DATASET_ID = "2457ea29-fc82-48b0-86ec-3b0755de7515"
CMS_REVOCATION_DATASET_ID = "a6496a7d-4e19-479a-a9ad-d4c0a49e07c3"


class Transport(str, Enum):
    """How a component is actually obtainable."""

    DATA_API = "DATA_API"
    DOWNLOAD = "DOWNLOAD"
    BOTH = "BOTH"
    NONE = "NONE"


class ResourceStatus(str, Enum):
    API_AVAILABLE = "API_AVAILABLE"
    DOWNLOAD_AVAILABLE = "DOWNLOAD_AVAILABLE"
    API_AND_DOWNLOAD_AVAILABLE = "API_AND_DOWNLOAD_AVAILABLE"
    METADATA_ONLY = "METADATA_ONLY"
    UNAVAILABLE = "UNAVAILABLE"


#: file_name patterns → internal component key. Keyed on the file name because
#: that carries the CMS structural name; the display title varies (see docstring).
_COMPONENT_FILE_PATTERNS = [
    (re.compile(r"PPEF_Enrollment_Extract", re.I), "ENROLLMENT"),
    (re.compile(r"PPEF_Reassignment_Extract", re.I), "REASSIGNMENT"),
    (re.compile(r"PPEF_Practice_Location_Extract", re.I), "PRACTICE_LOCATION"),
    (re.compile(r"PPEF_Additional_NPIs", re.I), "ADDITIONAL_NPIS"),
    (re.compile(r"PPEF_Secondary_Specialty_Extract", re.I), "SECONDARY_SPECIALTY"),
]

#: Schema CMS actually returns, verified by reading each live CSV header
#: (2026-07-17 / Q3 2026 extract). Used to validate an ingest rather than to
#: assume: if CMS changes a column, ingestion must fail loudly, not silently
#: load nulls into an evidence store.
EXPECTED_FIELDS: Dict[str, tuple] = {
    "ENROLLMENT": ("NPI", "MULTIPLE_NPI_FLAG", "PECOS_ASCT_CNTL_ID", "ENRLMT_ID",
                   "PROVIDER_TYPE_CD", "PROVIDER_TYPE_DESC", "STATE_CD",
                   "FIRST_NAME", "MDL_NAME", "LAST_NAME", "ORG_NAME"),
    "REASSIGNMENT": ("REASGN_BNFT_ENRLMT_ID", "RCV_BNFT_ENRLMT_ID"),
    "PRACTICE_LOCATION": ("ENRLMT_ID", "CITY_NAME", "STATE_CD", "ZIP_CD"),
    "ADDITIONAL_NPIS": ("ENRLMT_ID", "NPI"),
    "SECONDARY_SPECIALTY": ("ENRLMT_ID", "PROVIDER_TYPE_CD", "PROVIDER_TYPE_DESC"),
}

#: The join key each component uses back to ENROLLMENT.ENRLMT_ID.
JOIN_KEYS: Dict[str, tuple] = {
    "ENROLLMENT": ("ENRLMT_ID",),
    "REASSIGNMENT": ("REASGN_BNFT_ENRLMT_ID", "RCV_BNFT_ENRLMT_ID"),
    "PRACTICE_LOCATION": ("ENRLMT_ID",),
    "ADDITIONAL_NPIS": ("ENRLMT_ID",),
    "SECONDARY_SPECIALTY": ("ENRLMT_ID",),
}


@dataclass
class PPEFResource:
    """One discovered CMS resource, with the provenance an audit needs."""

    component: str
    cms_title: str                 # exact CMS title, e.g. "Address Sub-File Q3 2026"
    file_name: Optional[str] = None
    resource_id: Optional[str] = None      # CMS file_uuid (media id, NOT a dataset id)
    download_url: Optional[str] = None
    api_endpoint: Optional[str] = None
    parent_dataset_id: str = PPEF_PARENT_DATASET_ID
    file_size: Optional[int] = None
    file_mime: Optional[str] = None
    resource_version: Optional[str] = None   # e.g. "2026.07.17" parsed from the file name
    transport: str = Transport.NONE.value
    status: str = ResourceStatus.UNAVAILABLE.value
    discovered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component,
            "cms_title": self.cms_title,
            "file_name": self.file_name,
            "resource_id": self.resource_id,
            "parent_dataset_id": self.parent_dataset_id,
            "download_url": self.download_url,
            "api_endpoint": self.api_endpoint,
            "file_size": self.file_size,
            "file_mime": self.file_mime,
            "resource_version": self.resource_version,
            "transport": self.transport,
            "status": self.status,
            "expected_fields": list(EXPECTED_FIELDS.get(self.component, ())),
            "join_keys": list(JOIN_KEYS.get(self.component, ())),
            "discovered_at": self.discovered_at,
        }


_VERSION_RE = re.compile(r"(\d{4}\.\d{2}\.\d{2})")


def _version_from_filename(file_name: Optional[str]) -> Optional[str]:
    if not file_name:
        return None
    m = _VERSION_RE.search(file_name)
    return m.group(1) if m else None


def _component_for(file_name: Optional[str]) -> Optional[str]:
    for pattern, component in _COMPONENT_FILE_PATTERNS:
        if file_name and pattern.search(file_name):
            return component
    return None


class PPEFResourceCatalog:
    """Reads the CMS resource list for the PPEF parent dataset.

    Discovery is live rather than hard-coded because CMS re-publishes quarterly
    with new file names, new media uuids and new titles. Hard-coding this
    quarter's identifiers would silently serve stale evidence next quarter — and
    the specification is explicit: do not hard-code guessed identifiers.
    """

    def __init__(self, parent_dataset_id: str = PPEF_PARENT_DATASET_ID, timeout: float = 60.0):
        self.parent_dataset_id = parent_dataset_id
        self.timeout = timeout

    async def fetch_resources(self) -> List[Dict[str, Any]]:
        url = f"{CMS_DATA_API_ROOT}/{self.parent_dataset_id}/resources"
        resp = await _get_with_retry(url, params={}, headers=HTTP_HEADERS, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"CMS resources endpoint returned HTTP {resp.status_code}")
        payload = resp.json()
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("CMS resources endpoint returned an unexpected shape")
        return rows

    async def discover(self) -> Dict[str, PPEFResource]:
        """Map every PPEF component to its current CMS resource.

        ENROLLMENT is the only component with a data-api endpoint, so it is the
        only one that can be BOTH. The rest are download-only until CMS says
        otherwise — and "says otherwise" means a successful API request, not a
        resource merely existing.
        """
        found: Dict[str, PPEFResource] = {}
        try:
            rows = await self.fetch_resources()
        except Exception as exc:
            logger.warning("PPEF resource discovery failed: %s", exc)
            return found

        for row in rows:
            file_name = row.get("file_name")
            component = _component_for(file_name)
            if not component:
                continue
            # Prefer the primary Enrollment resource over historical archives.
            if component in found and row.get("type") != "Primary":
                continue
            resource = PPEFResource(
                component=component,
                cms_title=(row.get("title") or "").strip(),
                file_name=file_name,
                resource_id=row.get("file_uuid"),
                download_url=row.get("file_url"),
                file_size=row.get("file_size"),
                file_mime=row.get("file_mime"),
                resource_version=_version_from_filename(file_name),
                transport=Transport.DOWNLOAD.value,
                status=ResourceStatus.DOWNLOAD_AVAILABLE.value,
            )
            if component == "ENROLLMENT":
                resource.api_endpoint = f"{CMS_DATA_API_ROOT}/{self.parent_dataset_id}/data"
                resource.transport = Transport.BOTH.value
                resource.status = ResourceStatus.API_AND_DOWNLOAD_AVAILABLE.value
            found[component] = resource
        return found


#: Static fallback describing what discovery found on 2026-08-19, used ONLY to
#: describe capability when discovery itself cannot run (e.g. CMS unreachable
#: during a health probe). It never supplies a URL to fetch from: a stale
#: download URL would be worse than no download URL, because it would quietly
#: ingest last quarter's data as if it were current.
KNOWN_COMPONENT_TRANSPORT: Dict[str, Dict[str, str]] = {
    "ENROLLMENT": {"transport": Transport.BOTH.value,
                   "status": ResourceStatus.API_AND_DOWNLOAD_AVAILABLE.value},
    "REASSIGNMENT": {"transport": Transport.DOWNLOAD.value,
                     "status": ResourceStatus.DOWNLOAD_AVAILABLE.value},
    "PRACTICE_LOCATION": {"transport": Transport.DOWNLOAD.value,
                          "status": ResourceStatus.DOWNLOAD_AVAILABLE.value},
    "ADDITIONAL_NPIS": {"transport": Transport.DOWNLOAD.value,
                        "status": ResourceStatus.DOWNLOAD_AVAILABLE.value},
    "SECONDARY_SPECIALTY": {"transport": Transport.DOWNLOAD.value,
                            "status": ResourceStatus.DOWNLOAD_AVAILABLE.value},
}

#: Why each component is transported the way it is. Surfaced in the API and the
#: report so the decision is reviewable rather than implicit.
TRANSPORT_RATIONALE: Dict[str, str] = {
    "ENROLLMENT": (
        "BOTH. The data-api supports exact filter[NPI] lookup against 2,978,925 rows, "
        "which is the efficient path for verifying one entity. The quarterly CSV is "
        "retained for snapshot ingestion so a determination can cite the exact extract "
        "it was made against."
    ),
    "REASSIGNMENT": (
        "DOWNLOAD. No data-api endpoint exists (file_uuid 404s against /data). It is "
        "also the component that most needs bulk treatment: 128 MB, relational, and "
        "joined on ENRLMT_ID in both directions, so per-entity API calls would not "
        "serve it even if they existed."
    ),
    "PRACTICE_LOCATION": (
        "DOWNLOAD. No data-api endpoint. One enrolment may have many locations, and "
        "address reconciliation needs all of them together rather than a first row."
    ),
    "ADDITIONAL_NPIS": (
        "DOWNLOAD. No data-api endpoint. Small (3.6 MB) and consulted only when "
        "MULTIPLE_NPI_FLAG=Y, but it must come from the SAME snapshot as the "
        "enrolment it resolves, or it could contradict the record it is meant to explain."
    ),
    "SECONDARY_SPECIALTY": (
        "DOWNLOAD. No data-api endpoint. Corroborates provider type within D1 Identity "
        "only — it is never a separate dimension and never an independent vote."
    ),
}
