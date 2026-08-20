"""
CMS Medicare FFS Public Provider Enrollment (PPEF) + Revoked Providers.

WHAT CMS ACTUALLY PUBLISHES, VERIFIED AGAINST THE LIVE API (2026-08-19)
──────────────────────────────────────────────────────────────────────
Everything in this module was probed against data.cms.gov before it was
written. The field names below are the field names CMS returns — not names
copied out of a specification and hoped for.

  PPEF ENROLLMENT   dataset 2457ea29-fc82-48b0-86ec-3b0755de7515
                    2,978,925 rows. Fields (11, verified):
                      NPI, MULTIPLE_NPI_FLAG, PECOS_ASCT_CNTL_ID, ENRLMT_ID,
                      PROVIDER_TYPE_CD, PROVIDER_TYPE_DESC, STATE_CD,
                      FIRST_NAME, MDL_NAME, LAST_NAME, ORG_NAME

  CMS REVOCATION    dataset a6496a7d-4e19-479a-a9ad-d4c0a49e07c3
                    Fields (12, verified):
                      ENRLMT_ID, NPI, FIRST_NAME, MDL_NAME, LAST_NAME,
                      ORG_NAME, MULTIPLE_NPI_FLAG, STATE_CD,
                      PROVIDER_TYPE_DESC, REVOCATION_RSN,
                      REVOCATION_EFCTV_DT, REENROLLMENT_BAR_EXPRTN_DT

THE FOUR RELATIONAL SUB-FILES ARE PUBLISHED — AS DOWNLOADS, NOT AS APIs
───────────────────────────────────────────────────────────────────────
CORRECTION. An earlier version of this module stated that CMS does not publish
the four sub-files. That was wrong, and the error is recorded here rather than
quietly deleted, because the reasoning behind it was sound and still needs
answering:

  * The DCAT catalogue lists ONE PPEF dataset. All 159 dataset titles were read
    — no Reassignment, Practice Location, Address, Additional NPIs or Secondary
    Specialty dataset exists there under any name.
  * Its distributions are only Enrollment, in three API and two CSV versions.
  * A catalogue-wide distribution search matched only the Revalidation
    products, which the specification forbids substituting.

All true. The catalogue is simply not where the sub-files live. They are
ancillary RESOURCES of the parent dataset, listed by an endpoint the catalogue
never mentions — GET /data-api/v1/dataset/{parent}/resources — and discovered
by app.Tefca.ppef_resources. Verified live 2026-08-19:

  Reassignment Sub-File Q3 2026        PPEF_Reassignment_Extract_2026.07.17.csv       128.7 MB
  Address Sub-File Q3 2026             PPEF_Practice_Location_Extract_2026.07.17.csv   43.2 MB
  Secondary Specialty Sub-File Q3 2026 PPEF_Secondary_Specialty_Extract_2026.07.17.csv 27.2 MB
  Additional NPIs Sub-File Q3 2026     PPEF_Additional_NPIs_2026.07.17.csv              3.6 MB

Their file_uuids are MEDIA ids, not dataset ids: all four return 404 against
/data-api/v1/dataset/{uuid}/data. So the transport is download-and-ingest, and
the honest classification is DOWNLOAD_AVAILABLE — never API_AVAILABLE, because
no API request for them has ever succeeded.

Note the naming: CMS titles the practice-location resource "Address Sub-File"
while naming the file PPEF_Practice_Location_Extract. One capability, two
names. It is normalised internally to PRACTICE_LOCATION with the CMS title
preserved in provenance, rather than split into two capabilities.

HOW A COMPONENT IS SERVED
─────────────────────────
  1. If CMS ever exposes a data-api dataset id for it, use the API.
  2. Otherwise serve it from an ingested quarterly snapshot (ppef_ingest).
  3. If neither exists, UNAVAILABLE with a reason — never a fabricated match.

An absent snapshot is an operational fact ("nothing ingested yet"), not a
finding against an entity, and it never becomes a verification failure.

POINT-IN-TIME (Amendment 6)
───────────────────────────
Every lookup records query_timestamp AND the dataset identity. The CMS dataset
UUID is itself the version anchor: CMS mints a new UUID per quarterly release,
so the UUID pins the exact publication a determination was made against. The
HTTP Last-Modified header is captured separately and labelled transport
metadata — it is a CDN artefact, not the CMS as-of date, and the two must not
be confused in an audit trail. This data is quarterly. It is never described as
real-time.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from app.Tefca.connectors import (
    HTTP_HEADERS,
    SourceResult,
    _get_with_retry,
    HEALTH_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


CMS_DATA_API_ROOT = "https://data.cms.gov/data-api/v1/dataset"

PPEF_ENROLLMENT_DATASET_ID = "2457ea29-fc82-48b0-86ec-3b0755de7515"
CMS_REVOCATION_DATASET_ID = "a6496a7d-4e19-479a-a9ad-d4c0a49e07c3"

#: Hard ceiling on rows pulled for one lookup. One-to-many is expected
#: (Amendment 4) so we never take "the first result", but an unbounded pull on a
#: 3M-row dataset is a denial of service against ourselves.
MAX_RECORDS_PER_LOOKUP = 200
PAGE_SIZE = 100


class PPEFComponent(str, Enum):
    """The five relational components CMS documents for PPEF."""

    ENROLLMENT = "ENROLLMENT"
    PRACTICE_LOCATION = "PRACTICE_LOCATION"
    REASSIGNMENT = "REASSIGNMENT"
    ADDITIONAL_NPIS = "ADDITIONAL_NPIS"
    SECONDARY_SPECIALTY = "SECONDARY_SPECIALTY"


#: Which components are reachable through the CMS data API, and the dataset that
#: serves them. `None` means CMS does not publish that component here — see the
#: module docstring for how that was established.
PPEF_COMPONENT_DATASETS: Dict[PPEFComponent, Optional[str]] = {
    PPEFComponent.ENROLLMENT: PPEF_ENROLLMENT_DATASET_ID,
    PPEFComponent.PRACTICE_LOCATION: None,
    PPEFComponent.REASSIGNMENT: None,
    PPEFComponent.ADDITIONAL_NPIS: None,
    PPEFComponent.SECONDARY_SPECIALTY: None,
}

COMPONENT_UNPUBLISHED_REASON = (
    "ppef_component_not_available_via_cms_data_api: CMS publishes this component "
    "as a downloadable quarterly sub-file of the parent PPEF dataset, not as a "
    "data-api dataset — its file_uuid is a media id and returns 404 against "
    "/data-api/v1/dataset/{uuid}/data. Serve it from an ingested snapshot "
    "instead. Substituting the Revalidation Reassignment List is prohibited — "
    "it is a different dataset."
)

#: Returned when the component has no API and nothing has been ingested yet.
#: Distinct from the message above because "CMS offers no API for this" and
#: "we have not loaded it yet" are different facts with different remedies.
COMPONENT_NO_SNAPSHOT_REASON = (
    "ppef_component_no_snapshot_ingested: this component is download-only and no "
    "quarterly snapshot has been ingested into the local evidence store. Run the "
    "PPEF ingestion for it. This is an operational state, not a finding about "
    "any entity."
)

# Field names, exactly as CMS returns them. Referenced symbolically so a CMS
# rename shows up as one edit here rather than as silent None across the module.
F_NPI = "NPI"
F_ENRLMT_ID = "ENRLMT_ID"
F_PAC_ID = "PECOS_ASCT_CNTL_ID"
F_MULTIPLE_NPI_FLAG = "MULTIPLE_NPI_FLAG"
F_PROVIDER_TYPE_CD = "PROVIDER_TYPE_CD"
F_PROVIDER_TYPE_DESC = "PROVIDER_TYPE_DESC"
F_STATE_CD = "STATE_CD"
F_FIRST_NAME = "FIRST_NAME"
F_MDL_NAME = "MDL_NAME"
F_LAST_NAME = "LAST_NAME"
F_ORG_NAME = "ORG_NAME"
F_REVOCATION_RSN = "REVOCATION_RSN"
F_REVOCATION_EFCTV_DT = "REVOCATION_EFCTV_DT"
F_REENROLLMENT_BAR_EXPRTN_DT = "REENROLLMENT_BAR_EXPRTN_DT"

#: Amendment 1. A negative revocation lookup means exactly one thing, and the
#: string says which thing, so no downstream reader can widen it into "provider
#: is enrolled" or "provider is in good standing".
NO_ACTIVE_REVOCATION_RECORD_FOUND = "NO_ACTIVE_REVOCATION_RECORD_FOUND"


@dataclass
class CMSQuery:
    """Provenance for one CMS API call — the reproducibility unit (Amendment 6)."""

    dataset_id: str
    component: str
    filters: Dict[str, Any]
    query_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    http_last_modified: Optional[str] = None
    row_count: int = 0
    truncated: bool = False

    def as_provenance(self) -> Dict[str, Any]:
        return {
            "source": "CMS_PPEF" if self.dataset_id == PPEF_ENROLLMENT_DATASET_ID else "CMS",
            "source_dataset": self.dataset_id,
            "ppef_component": self.component,
            "query_identifier": f"{self.dataset_id}:{self.component}:"
                                + ",".join(f"{k}={v}" for k, v in sorted(self.filters.items())),
            "query_filters": dict(self.filters),
            "query_timestamp": self.query_timestamp,
            # Labelled for what it is. A CDN header is not a dataset as-of date.
            "http_last_modified": self.http_last_modified,
            "dataset_version_anchor": self.dataset_id,
            "update_cadence": "quarterly",
            "realtime": False,
            "row_count": self.row_count,
            "records_truncated": self.truncated,
        }


class CMSDataAPIClient:
    """Thin, keyless client for data.cms.gov/data-api.

    Verified contract: `filter[FIELD]=value` for exact match, `size` + `offset`
    for paging, and `/data/stats` for row counts. Public domain data, no API
    key, no BAA — nothing here transmits entity PHI to CMS; only an NPI, which
    is a public identifier, is ever sent.
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def fetch_all(
        self,
        dataset_id: str,
        component: str,
        filters: Dict[str, Any],
        max_records: int = MAX_RECORDS_PER_LOOKUP,
    ) -> tuple[List[Dict[str, Any]], CMSQuery]:
        """Page through every matching row, up to `max_records`.

        Amendment 4: a provider may hold several enrolments, locations,
        reassignments and NPIs. Callers get the whole set and reconcile it —
        taking row [0] would silently pick one truth out of several.
        """
        query = CMSQuery(dataset_id=dataset_id, component=component, filters=dict(filters))
        url = f"{CMS_DATA_API_ROOT}/{dataset_id}/data"
        rows: List[Dict[str, Any]] = []
        offset = 0

        while len(rows) < max_records:
            params: Dict[str, Any] = {"size": PAGE_SIZE, "offset": offset}
            for k, v in filters.items():
                params[f"filter[{k}]"] = v
            resp = await _get_with_retry(url, params=params, headers=HTTP_HEADERS, timeout=self.timeout)
            if resp.status_code != 200:
                raise CMSUnavailable(f"HTTP {resp.status_code}")
            query.http_last_modified = query.http_last_modified or resp.headers.get("Last-Modified")
            try:
                page = resp.json()
            except Exception as exc:  # malformed body is an availability problem
                raise CMSUnavailable(f"malformed_response: {exc}") from exc
            if not isinstance(page, list):
                raise CMSUnavailable("malformed_response: expected a JSON array of records")
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        query.truncated = len(rows) >= max_records
        rows = rows[:max_records]
        query.row_count = len(rows)
        return rows, query

    async def probe(self, dataset_id: str) -> bool:
        try:
            resp = await _get_with_retry(
                f"{CMS_DATA_API_ROOT}/{dataset_id}/data",
                params={"size": 1},
                headers=HTTP_HEADERS,
                timeout=HEALTH_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False


class CMSUnavailable(Exception):
    """CMS could not answer. Never a verification failure — an availability fact."""


def _person_name(row: Dict[str, Any]) -> str:
    parts = [row.get(F_FIRST_NAME) or "", row.get(F_MDL_NAME) or "", row.get(F_LAST_NAME) or ""]
    return " ".join(p for p in parts if p).strip()


def _display_name(row: Dict[str, Any]) -> str:
    """ORG_NAME for an organisational enrolment, assembled person name otherwise.

    Both are carried through separately by the callers that need to compare
    organisational identity against individual identity (Type 1 vs Type 2); this
    is only the human-readable label.
    """
    return (row.get(F_ORG_NAME) or "").strip() or _person_name(row)


def _shape_enrollment(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one PPEF Enrollment row, preserving every original value."""
    org = (row.get(F_ORG_NAME) or "").strip()
    return {
        "npi": (row.get(F_NPI) or "").strip() or None,
        "enrollment_id": (row.get(F_ENRLMT_ID) or "").strip() or None,
        "pac_id": (row.get(F_PAC_ID) or "").strip() or None,
        "multiple_npi_flag": (row.get(F_MULTIPLE_NPI_FLAG) or "").strip().upper() or None,
        "provider_type_code": (row.get(F_PROVIDER_TYPE_CD) or "").strip() or None,
        "provider_type_desc": (row.get(F_PROVIDER_TYPE_DESC) or "").strip() or None,
        "state": (row.get(F_STATE_CD) or "").strip() or None,
        "organization_name": org or None,
        "individual_name": _person_name(row) or None,
        # ENRLMT_ID's leading character encodes the enrolment class: I = individual,
        # O = organisational. CMS uses this convention throughout PPEF, and it is
        # the only enrolment-class signal in the published Enrollment extract.
        "enrollment_class": _enrollment_class(row),
        "display_name": _display_name(row),
        "_raw": dict(row),
    }


def _enrollment_class(row: Dict[str, Any]) -> Optional[str]:
    enrlmt = (row.get(F_ENRLMT_ID) or "").strip().upper()
    if (row.get(F_ORG_NAME) or "").strip():
        return "ORGANIZATION"
    if enrlmt.startswith("I"):
        return "INDIVIDUAL"
    if enrlmt.startswith("O"):
        return "ORGANIZATION"
    return None


class PPEFEnrollmentConnector:
    """CMS PECOS enrolment evidence (D2), and identity CORROBORATION only (D1).

    NPPES remains the primary NPI identity authority. Nothing in this class may
    be used to overrule NPPES on identity; it corroborates, and where it
    disagrees the disposition is REVIEW for a human, never an automatic failure.
    """

    SOURCE_NAME = "CMS_PPEF_ENROLLMENT"
    DATASET_ID = PPEF_ENROLLMENT_DATASET_ID

    def __init__(self, client: Optional[CMSDataAPIClient] = None):
        self.client = client or CMSDataAPIClient()

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi, "dataset": self.DATASET_ID}
        if not npi:
            # Not an error and not a failure: there is nothing to look up.
            return SourceResult.ok(
                self.SOURCE_NAME,
                {
                    "found": False,
                    "reason": "no_npi_submitted",
                    "records": [],
                    "provenance": CMSQuery(self.DATASET_ID, PPEFComponent.ENROLLMENT.value, {}).as_provenance(),
                },
                qp,
                self.DATASET_ID,
            )
        try:
            rows, query = await self.client.fetch_all(
                self.DATASET_ID, PPEFComponent.ENROLLMENT.value, {F_NPI: npi}
            )
        except CMSUnavailable as exc:
            return SourceResult.unavailable(self.SOURCE_NAME, str(exc), qp, self.DATASET_ID)
        except Exception as exc:
            logger.warning("PPEF enrollment unavailable for NPI %s: %s", npi, exc)
            return SourceResult.unavailable(self.SOURCE_NAME, str(exc), qp, self.DATASET_ID)

        records = [_shape_enrollment(r) for r in rows]
        data = {
            "found": bool(records),
            "npi": npi,
            "records": records,
            "record_count": len(records),
            "enrollment_ids": [r["enrollment_id"] for r in records if r["enrollment_id"]],
            "pac_ids": sorted({r["pac_id"] for r in records if r["pac_id"]}),
            # Amendment 2: MULTIPLE_NPI_FLAG=Y anywhere in the matched set means
            # a differing NPI cannot be called a conflict without ADDITIONAL_NPIS.
            "multiple_npi_flag": "Y" if any(r["multiple_npi_flag"] == "Y" for r in records) else (
                "N" if records else None
            ),
            "provenance": query.as_provenance(),
        }
        return SourceResult.ok(self.SOURCE_NAME, data, qp, self.DATASET_ID, raw_for_hash=rows)

    async def lookup_by_enrollment_id(self, enrollment_id: str) -> SourceResult:
        """Resolve an ENRLMT_ID back to provider identity.

        This is the hop Amendment 5 needs to turn a reassignment's
        RCV_BNFT_ENRLMT_ID into a receiving organisation. It is implemented and
        tested because the join direction is verified against the live
        Enrollment extract, even though the REASSIGNMENT file that would supply
        the input is not currently published.
        """
        qp = {"enrollment_id": enrollment_id, "dataset": self.DATASET_ID}
        if not enrollment_id:
            return SourceResult.ok(
                self.SOURCE_NAME,
                {"found": False, "reason": "no_enrollment_id", "records": []},
                qp, self.DATASET_ID,
            )
        try:
            rows, query = await self.client.fetch_all(
                self.DATASET_ID, PPEFComponent.ENROLLMENT.value, {F_ENRLMT_ID: enrollment_id}
            )
        except Exception as exc:
            return SourceResult.unavailable(self.SOURCE_NAME, str(exc), qp, self.DATASET_ID)
        records = [_shape_enrollment(r) for r in rows]
        return SourceResult.ok(
            self.SOURCE_NAME,
            {"found": bool(records), "enrollment_id": enrollment_id,
             "records": records, "record_count": len(records),
             "provenance": query.as_provenance()},
            qp, self.DATASET_ID, raw_for_hash=rows,
        )

    async def probe(self) -> bool:
        return await self.client.probe(self.DATASET_ID)


class CMSRevocationConnector:
    """CMS Revoked Medicare Providers and Suppliers (D3), kept separate from D2.

    Amendment 1 governs the negative case. Absence from this dataset proves only
    that no ACTIVE revocation record was found — it is not evidence of
    enrolment, eligibility, or good standing, and this connector never returns a
    value that could be read that way.
    """

    SOURCE_NAME = "CMS_REVOCATION"
    DATASET_ID = CMS_REVOCATION_DATASET_ID

    def __init__(self, client: Optional[CMSDataAPIClient] = None):
        self.client = client or CMSDataAPIClient()

    async def lookup_by_npi(self, npi: str) -> SourceResult:
        qp = {"npi": npi, "dataset": self.DATASET_ID}
        if not npi:
            return SourceResult.ok(
                self.SOURCE_NAME,
                {"checked": False, "reason": "no_npi_submitted", "matches": []},
                qp, self.DATASET_ID,
            )
        try:
            rows, query = await self.client.fetch_all(
                self.DATASET_ID, "REVOCATION", {F_NPI: npi}
            )
        except CMSUnavailable as exc:
            return SourceResult.unavailable(self.SOURCE_NAME, str(exc), qp, self.DATASET_ID)
        except Exception as exc:
            logger.warning("CMS revocation unavailable for NPI %s: %s", npi, exc)
            return SourceResult.unavailable(self.SOURCE_NAME, str(exc), qp, self.DATASET_ID)

        matches = [
            {
                "enrollment_id": (r.get(F_ENRLMT_ID) or "").strip() or None,
                "npi": (r.get(F_NPI) or "").strip() or None,
                "organization_name": (r.get(F_ORG_NAME) or "").strip() or None,
                "individual_name": _person_name(r) or None,
                "state": (r.get(F_STATE_CD) or "").strip() or None,
                "provider_type_desc": (r.get(F_PROVIDER_TYPE_DESC) or "").strip() or None,
                "revocation_reason": (r.get(F_REVOCATION_RSN) or "").strip() or None,
                "revocation_effective_date": (r.get(F_REVOCATION_EFCTV_DT) or "").strip() or None,
                "reenrollment_bar_expiration_date": (
                    r.get(F_REENROLLMENT_BAR_EXPRTN_DT) or "").strip() or None,
                "multiple_npi_flag": (r.get(F_MULTIPLE_NPI_FLAG) or "").strip().upper() or None,
                "_raw": dict(r),
            }
            for r in rows
        ]
        data = {
            "checked": True,
            "npi": npi,
            "matches": matches,
            "match_count": len(matches),
            # The whole point of Amendment 1 lives in this one field.
            "result": NO_ACTIVE_REVOCATION_RECORD_FOUND if not matches else "POTENTIAL_REVOCATION_MATCH",
            "scope_note": (
                "Satisfies the CMS Revocation evidence check only. It is not "
                "evidence of Medicare enrolment, eligibility to enrol, or "
                "overall good standing — use PPEF Enrollment separately for "
                "enrolment evidence."
            ),
            "provenance": query.as_provenance(),
        }
        return SourceResult.ok(self.SOURCE_NAME, data, qp, self.DATASET_ID, raw_for_hash=rows)

    async def probe(self) -> bool:
        return await self.client.probe(self.DATASET_ID)


class PPEFRelationalConnector:
    """The four PPEF relational components, served by whichever transport exists.

    CMS publishes these as downloadable quarterly sub-files rather than as
    data-api datasets (see the module docstring). This class therefore resolves
    them from an ingested snapshot, while keeping the data-api path live for the
    day CMS exposes one — PPEF_COMPONENT_DATASETS is still consulted first, so a
    single mapping entry switches a component over with no other change.

    `local_store` is injected rather than imported so this connector has no
    database dependency of its own: routes supply a snapshot-backed callable,
    and tests supply an in-memory one.
    """

    SOURCE_NAME = "CMS_PPEF"

    def __init__(self, client: Optional[CMSDataAPIClient] = None, local_store=None):
        self.client = client or CMSDataAPIClient()
        # async (component, key_field, enrollment_ids) -> (records, snapshot_provenance) | None
        self.local_store = local_store

    def component_dataset(self, component: PPEFComponent) -> Optional[str]:
        return PPEF_COMPONENT_DATASETS.get(component)

    async def fetch_component(
        self, component: PPEFComponent, filters: Dict[str, Any]
    ) -> SourceResult:
        dataset_id = self.component_dataset(component)
        source = f"{self.SOURCE_NAME}_{component.value}"
        qp = {"component": component.value, "filters": filters, "dataset": dataset_id}
        if not dataset_id:
            return SourceResult.unavailable(source, COMPONENT_UNPUBLISHED_REASON, qp, None)
        try:
            rows, query = await self.client.fetch_all(dataset_id, component.value, filters)
        except Exception as exc:
            return SourceResult.unavailable(source, str(exc), qp, dataset_id)
        return SourceResult.ok(
            source,
            {"found": bool(rows), "records": rows, "record_count": len(rows),
             "provenance": query.as_provenance()},
            qp, dataset_id, raw_for_hash=rows,
        )

    async def practice_locations(self, enrollment_ids: List[str]) -> SourceResult:
        """PRACTICE_LOCATION rows for a set of ENRLMT_IDs (Address corroboration).

        One-to-many by design (Amendment 4): every enrolment id is queried and
        every row kept. CMS documents that some individual enrolments
        legitimately have NO practice location row, so an empty result is
        NO_PRACTICE_LOCATION — a fact for the applicability engine to weigh, not
        a failure.
        """
        return await self._fan_out(PPEFComponent.PRACTICE_LOCATION, F_ENRLMT_ID, enrollment_ids)

    async def reassignments(self, enrollment_ids: List[str]) -> SourceResult:
        """REASSIGNMENT rows keyed on the individual practitioner enrolment.

        CMS documents REASGN_BNFT_ENRLMT_ID (the practitioner) and
        RCV_BNFT_ENRLMT_ID (the entity receiving reassigned benefits), both
        joining back to ENROLLMENT.ENRLMT_ID.
        """
        return await self._fan_out(PPEFComponent.REASSIGNMENT, "REASGN_BNFT_ENRLMT_ID", enrollment_ids)

    async def additional_npis(self, enrollment_ids: List[str]) -> SourceResult:
        return await self._fan_out(PPEFComponent.ADDITIONAL_NPIS, F_ENRLMT_ID, enrollment_ids)

    async def secondary_specialties(self, enrollment_ids: List[str]) -> SourceResult:
        return await self._fan_out(PPEFComponent.SECONDARY_SPECIALTY, F_ENRLMT_ID, enrollment_ids)

    async def _fan_out(
        self, component: PPEFComponent, key_field: str, enrollment_ids: List[str]
    ) -> SourceResult:
        """Resolve a component for a set of enrolment ids.

        Three transports, tried in the order of what CMS actually offers:
          1. data-api, if CMS ever publishes a dataset id for this component;
          2. an ingested quarterly snapshot (the real path today);
          3. neither — UNAVAILABLE with a reason that distinguishes "CMS has no
             API for this" from "nothing has been ingested yet".
        """
        source = f"{self.SOURCE_NAME}_{component.value}"
        ids = [e for e in (enrollment_ids or []) if e]
        if not ids:
            return SourceResult.ok(
                source,
                {"found": False, "reason": "no_enrollment_ids", "records": [], "record_count": 0},
                {"component": component.value}, None,
            )
        if not self.component_dataset(component):
            # No API. Try the local snapshot store before declaring anything
            # unavailable — this is the transport CMS actually supports.
            if self.local_store is not None:
                try:
                    local = await self.local_store(component.value, key_field, ids)
                except Exception as exc:
                    logger.warning("PPEF local store failed for %s: %s", component.value, exc)
                    local = None
                if local is not None:
                    records, snapshot = local
                    # A TRUNCATED snapshot cannot support a negative finding.
                    #
                    # "Not in our snapshot" and "not in CMS data" are different
                    # claims, and only the second one says anything about the
                    # entity. When a capped ingest returns no rows, the honest
                    # answer is that the evidence is inconclusive — otherwise a
                    # partial load would manufacture clean absences, which is
                    # precisely the silent-wrongness this design exists to avoid.
                    truncated = bool((snapshot or {}).get("rows_truncated"))
                    inconclusive = truncated and not records
                    return SourceResult.ok(
                        source,
                        {"found": bool(records), "records": records,
                         "record_count": len(records), "enrollment_ids_queried": ids,
                         "snapshot_truncated": truncated,
                         "inconclusive": inconclusive,
                         "inconclusive_reason": (
                             "snapshot_truncated_no_rows: the ingested snapshot is partial, so "
                             "the absence of rows for this enrolment is not evidence that CMS "
                             "has none. Ingest the complete file before relying on a negative."
                             if inconclusive else None),
                         "provenance": snapshot},
                        {"component": component.value, "enrollment_ids": ids},
                        (snapshot or {}).get("resource_version"),
                        raw_for_hash=records,
                    )
            return SourceResult.unavailable(
                source, COMPONENT_NO_SNAPSHOT_REASON,
                {"component": component.value, "enrollment_ids": ids}, None,
            )
        results = await asyncio.gather(
            *[self.fetch_component(component, {key_field: e}) for e in ids],
            return_exceptions=True,
        )
        records: List[Dict[str, Any]] = []
        for r in results:
            if isinstance(r, SourceResult) and r.success:
                records.extend(r.get("records", []) or [])
        return SourceResult.ok(
            source,
            {"found": bool(records), "records": records, "record_count": len(records),
             "enrollment_ids_queried": ids},
            {"component": component.value, "enrollment_ids": ids}, None,
            raw_for_hash=records,
        )


# ─── Health (spec: AVAILABLE / DEGRADED / UNAVAILABLE) ───────────────────────

CAPABILITY_AVAILABLE = "AVAILABLE"
CAPABILITY_DEGRADED = "DEGRADED"
CAPABILITY_UNAVAILABLE = "UNAVAILABLE"


async def cms_capability_health(
    client: Optional[CMSDataAPIClient] = None,
    snapshot_status: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Per-capability health for the CMS systems, not per-"API".

    Two systems are reported — CMS/PECOS Public Provider Enrollment (with its
    components as capabilities) and CMS Revoked Providers/Suppliers — because
    that is what they are. Reporting four "new APIs" would misrepresent one
    relational dataset as four independent authorities, which is exactly the
    inflation the spec forbids.

    A component CMS does not publish is DEGRADED, not UNAVAILABLE: the system is
    answering, one capability of it simply is not offered. And neither state may
    ever propagate into an entity determination.
    """
    client = client or CMSDataAPIClient()
    enrollment_live, revocation_live = await asyncio.gather(
        client.probe(PPEF_ENROLLMENT_DATASET_ID),
        client.probe(CMS_REVOCATION_DATASET_ID),
        return_exceptions=True,
    )
    enrollment_live = bool(enrollment_live) if not isinstance(enrollment_live, Exception) else False
    revocation_live = bool(revocation_live) if not isinstance(revocation_live, Exception) else False
    checked_at = datetime.utcnow().isoformat()

    from app.Tefca.ppef_resources import (
        KNOWN_COMPONENT_TRANSPORT,
        TRANSPORT_RATIONALE,
        ResourceStatus,
        Transport,
    )

    snapshots = snapshot_status or {}
    capabilities = []
    for component in PPEFComponent:
        dataset = PPEF_COMPONENT_DATASETS[component]
        known = KNOWN_COMPONENT_TRANSPORT.get(component.value, {})
        transport = known.get("transport", Transport.NONE.value)
        resource_status = known.get("status", ResourceStatus.UNAVAILABLE.value)
        snap = snapshots.get(component.value)

        if dataset is not None:
            # Served by the data-api. Live iff the probe answered.
            status = CAPABILITY_AVAILABLE if enrollment_live else CAPABILITY_UNAVAILABLE
            note = ("Reachable via data.cms.gov data-api."
                    if enrollment_live else "CMS data-api did not answer the probe.")
        elif snap:
            # Download-only component with an ingested snapshot: this is the
            # normal healthy state for four of the five components.
            status = CAPABILITY_AVAILABLE
            note = (f"Served from ingested snapshot {snap.get('resource_version')} "
                    f"({snap.get('record_count')} records, sha256 "
                    f"{str(snap.get('sha256'))[:12]}…).")
        else:
            # CMS publishes it, we simply have not loaded it yet. DEGRADED, not
            # UNAVAILABLE: the system is answering and the capability is
            # obtainable — it is an operational gap, not an outage.
            status = CAPABILITY_DEGRADED
            note = COMPONENT_NO_SNAPSHOT_REASON

        capabilities.append({
            "capability": component.value,
            "status": status,
            "dataset_id": dataset,
            "transport": transport,
            "resource_status": resource_status,
            "rationale": TRANSPORT_RATIONALE.get(component.value),
            "snapshot": snap,
            "note": note,
        })

    ppef_status = (
        CAPABILITY_UNAVAILABLE if not enrollment_live
        else CAPABILITY_DEGRADED if any(c["status"] == CAPABILITY_DEGRADED for c in capabilities)
        else CAPABILITY_AVAILABLE
    )
    return {
        "checked_at": checked_at,
        "systems": [
            {
                "system": "CMS_PPEF",
                "label": "CMS / PECOS Public Provider Enrollment",
                "status": ppef_status,
                "dataset_id": PPEF_ENROLLMENT_DATASET_ID,
                "update_cadence": "quarterly",
                "capabilities": capabilities,
                "note": (
                    "One relational dataset (PPEF). Components are capabilities of "
                    "this system, not separate external systems."
                ),
            },
            {
                "system": "CMS_REVOCATION",
                "label": "CMS Revoked Medicare Providers and Suppliers",
                "status": CAPABILITY_AVAILABLE if revocation_live else CAPABILITY_UNAVAILABLE,
                "dataset_id": CMS_REVOCATION_DATASET_ID,
                "update_cadence": "quarterly",
                "capabilities": [{
                    "capability": "REVOCATION",
                    "status": CAPABILITY_AVAILABLE if revocation_live else CAPABILITY_UNAVAILABLE,
                    "dataset_id": CMS_REVOCATION_DATASET_ID,
                    "note": "Separate authority from PPEF enrolment. Negative result is "
                            f"{NO_ACTIVE_REVOCATION_RECORD_FOUND} only.",
                }],
            },
        ],
        "impact_on_determinations": (
            "None. An upstream CMS outage yields UNAVAILABLE evidence and routes "
            "to analyst review; it never becomes an entity verification failure."
        ),
    }
