"""
CMS / PECOS evidence architecture — dimension, applicability and provenance tests.

NO NETWORK. Every CMS interaction goes through an injected fake client, so the
suite is deterministic and fast, and a CMS outage can never turn into a red
build. The fake speaks the exact contract verified against the live API:
`fetch_all(dataset_id, component, filters, max_records) -> (rows, CMSQuery)`,
with CMS field names as CMS returns them.

The tests are organised by the behaviour they protect, and several of them
exist specifically to prove a NEGATIVE — that an unavailable source, a missing
optional record, or an unreachable website does NOT produce a finding against
an entity. Those are the assertions most likely to be quietly broken later.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.Tefca import cms_ppef
from app.Tefca.address_evidence import AddressComparison, build_address_rows, reconcile
from app.Tefca.applicability import EntityCategory, build_profile
from app.Tefca.cms_ppef import (
    CMSQuery,
    CMSRevocationConnector,
    CMSUnavailable,
    NO_ACTIVE_REVOCATION_RECORD_FOUND,
    PPEFComponent,
    PPEFEnrollmentConnector,
    PPEFRelationalConnector,
    cms_capability_health,
)
from app.Tefca.evidence_assembly import (
    assemble_dimensions,
    relationship_conflict_review,
)
from app.Tefca.evidence_dimensions import (
    CORE_DISPOSITIONS,
    Applicability,
    Dimension,
    Disposition,
    sufficiency_summary,
)
from app.Tefca.evidence_service import (
    EvidenceService,
    WEBSITE_NOT_FOUND,
    WEBSITE_UNAVAILABLE,
    evidence_rows_for_persistence,
    website_corroboration,
)

pytestmark = pytest.mark.regression


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeCMSClient:
    """Stands in for CMSDataAPIClient with canned rows and injectable failures."""

    def __init__(self, rows_by_dataset: Optional[Dict[str, List[dict]]] = None,
                 raise_for: Optional[Dict[str, Exception]] = None,
                 probe_ok: bool = True):
        self.rows_by_dataset = rows_by_dataset or {}
        self.raise_for = raise_for or {}
        self.probe_ok = probe_ok
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []

    async def fetch_all(self, dataset_id: str, component: str, filters: Dict[str, Any],
                        max_records: int = 200) -> Tuple[List[dict], CMSQuery]:
        self.calls.append((dataset_id, component, dict(filters)))
        if dataset_id in self.raise_for:
            raise self.raise_for[dataset_id]
        rows = list(self.rows_by_dataset.get(dataset_id, []))
        for key, value in filters.items():
            rows = [r for r in rows if str(r.get(key, "")) == str(value)]
        query = CMSQuery(dataset_id=dataset_id, component=component, filters=dict(filters),
                         http_last_modified="Fri, 14 Aug 2026 01:08:49 GMT")
        query.row_count = len(rows)
        return rows, query

    async def probe(self, dataset_id: str) -> bool:
        return self.probe_ok


def enrollment_row(**over) -> dict:
    row = {
        "NPI": "1003879883", "MULTIPLE_NPI_FLAG": "N", "PECOS_ASCT_CNTL_ID": "8022920719",
        "ENRLMT_ID": "I20031103000001", "PROVIDER_TYPE_CD": "14-16",
        "PROVIDER_TYPE_DESC": "PRACTITIONER - OBSTETRICS/GYNECOLOGY", "STATE_CD": "MD",
        "FIRST_NAME": "ANTONIO", "MDL_NAME": "", "LAST_NAME": "ALVAREZ", "ORG_NAME": "",
    }
    row.update(over)
    return row


def revocation_row(**over) -> dict:
    row = {
        "ENRLMT_ID": "I20031105000097", "NPI": "1801839063", "FIRST_NAME": "MARGARET",
        "MDL_NAME": "L", "LAST_NAME": "KEITH", "ORG_NAME": "", "MULTIPLE_NPI_FLAG": "N",
        "STATE_CD": "FL", "PROVIDER_TYPE_DESC": "PRACTITIONER - INTERNAL MEDICINE",
        "REVOCATION_RSN": "424.535(A)(2) Provider Or Supplier Conduct (Exclusion)",
        "REVOCATION_EFCTV_DT": "2025-05-20", "REENROLLMENT_BAR_EXPRTN_DT": "2030-07-31",
    }
    row.update(over)
    return row


def onc_entity(**over) -> dict:
    entity = {
        "resourceType": "Organization",
        "id": "rce-org-test-001",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1003879883"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-001"},
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type",
                              "code": "PARTICIPANT"}]}],
        "name": "Riverside Community Health Network",
        "telecom": [{"system": "phone", "value": "410-555-0101"}],
        "address": [{"use": "work", "line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201", "country": "US"}],
        "partOf": {"reference": "Organization/rce-qhin-ehealthexchange"},
        "_qhin": "eHealth Exchange",
    }
    entity.update(over)
    return entity


class FakeSourceResult:
    """Minimal stand-in for connectors.SourceResult."""

    def __init__(self, source_name: str, success: bool = True, data: Optional[dict] = None,
                 error: Optional[str] = None):
        self.source_name = source_name
        self.success = success
        self.data = data
        self.error = error
        self.query_timestamp = "2026-08-19T00:00:00"
        self.api_version = "2.1"
        self.query_params: Dict[str, Any] = {}
        self.response_hash = None

    def get(self, key, default=None):
        return (self.data or {}).get(key, default)


def nppes_result(**over) -> FakeSourceResult:
    data = {
        "found": True, "npi": "1003879883", "enumeration_type": "NPI-2",
        "legal_name": "Riverside Community Health Network",
        "organization_name": "Riverside Community Health Network",
        "taxonomy": "General Acute Care Hospital", "taxonomy_code": "282N00000X",
        "status": "A",
        "addresses": [{"address_purpose": "LOCATION", "address_1": "1200 Health Center Drive",
                       "city": "Baltimore", "state": "MD", "postal_code": "21201"}],
    }
    data.update(over)
    return FakeSourceResult("NPPES", True, data)


def clean_sources(**over) -> Dict[str, Any]:
    sources = {
        "nppes": nppes_result(),
        "leie_npi": FakeSourceResult("OIG_LEIE", True, {"excluded": False}),
        "sam_entity": FakeSourceResult("SAM_GOV", True, {"debarred": False}),
        "sam_exclusion": FakeSourceResult("SAM_GOV", True, {"debarred": False}),
    }
    sources.update(over)
    return sources


async def enrollment_source(client: FakeCMSClient, npi: str = "1003879883"):
    return await PPEFEnrollmentConnector(client).lookup_by_npi(npi)


async def revocation_source(client: FakeCMSClient, npi: str = "1003879883"):
    return await CMSRevocationConnector(client).lookup_by_npi(npi)


def dim(results, dimension: Dimension):
    return next(r for r in results if r.dimension == dimension.value)


# ── PECOS enrolment ──────────────────────────────────────────────────────────

class TestPECOSEnrollment:
    async def test_enrollment_match_is_pass(self):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})
        result = await enrollment_source(client)
        assert result.success and result.get("found") is True
        assert result.get("enrollment_ids") == ["I20031103000001"]
        assert result.get("pac_ids") == ["8022920719"]

    async def test_enrollment_non_match_is_not_a_failure(self):
        """A PECOS non-match must never become FAIL — the core D2 rule."""
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: []})
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(client),
            cms_revocation=await revocation_source(FakeCMSClient()),
        )
        entity = onc_entity()
        profile = build_profile(entity, nppes_data=nppes_result().data, pecos_found=False)
        results = assemble_dimensions(entity, profile, sources)
        d2 = dim(results, Dimension.D2_MEDICARE_ENROLLMENT)
        assert d2.disposition != Disposition.FAIL.value
        assert d2.disposition in (Disposition.REVIEW.value, Disposition.NOT_APPLICABLE.value)

    async def test_multiple_enrollments_are_all_collected(self):
        """One-to-many: never take the first row (Amendment 4)."""
        rows = [enrollment_row(ENRLMT_ID="I1"), enrollment_row(ENRLMT_ID="I2"),
                enrollment_row(ENRLMT_ID="O3", ORG_NAME="RIVERSIDE HEALTH")]
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: rows})
        result = await enrollment_source(client)
        assert result.get("record_count") == 3
        assert set(result.get("enrollment_ids")) == {"I1", "I2", "O3"}

    async def test_no_npi_is_not_an_outage(self):
        result = await enrollment_source(FakeCMSClient(), npi="")
        assert result.success is True
        assert result.get("reason") == "no_npi_submitted"


class TestCMSFailureModes:
    """CMS problems are availability facts, never entity findings."""

    async def test_unavailable(self):
        client = FakeCMSClient(raise_for={cms_ppef.PPEF_ENROLLMENT_DATASET_ID:
                                          CMSUnavailable("HTTP 503")})
        result = await enrollment_source(client)
        assert result.success is False and "503" in (result.error or "")

    async def test_timeout(self):
        client = FakeCMSClient(raise_for={cms_ppef.PPEF_ENROLLMENT_DATASET_ID:
                                          asyncio.TimeoutError()})
        result = await enrollment_source(client)
        assert result.success is False
        assert result.data is None  # fail-closed: no caller can read a clean value

    async def test_malformed_response(self):
        client = FakeCMSClient(raise_for={cms_ppef.PPEF_ENROLLMENT_DATASET_ID:
                                          CMSUnavailable("malformed_response: expected array")})
        result = await enrollment_source(client)
        assert result.success is False and "malformed_response" in result.error

    async def test_rate_limited(self):
        client = FakeCMSClient(raise_for={cms_ppef.PPEF_ENROLLMENT_DATASET_ID:
                                          CMSUnavailable("HTTP 429")})
        result = await enrollment_source(client)
        assert result.success is False and "429" in result.error

    async def test_cms_outage_yields_unavailable_dimension_not_fail(self):
        client = FakeCMSClient(raise_for={
            cms_ppef.PPEF_ENROLLMENT_DATASET_ID: CMSUnavailable("HTTP 503"),
            cms_ppef.CMS_REVOCATION_DATASET_ID: CMSUnavailable("HTTP 503"),
        })
        entity = onc_entity()
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(client),
            cms_revocation=await revocation_source(client),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data, pecos_found=None)
        results = assemble_dimensions(entity, profile, sources)
        for r in results:
            assert r.disposition != Disposition.FAIL.value
        assert dim(results, Dimension.D2_MEDICARE_ENROLLMENT).disposition == \
            Disposition.UNAVAILABLE.value


# ── Revocation (Amendment 1) ─────────────────────────────────────────────────

class TestRevocation:
    async def test_negative_lookup_uses_the_narrow_semantics(self):
        result = await revocation_source(FakeCMSClient({cms_ppef.CMS_REVOCATION_DATASET_ID: []}))
        assert result.get("result") == NO_ACTIVE_REVOCATION_RECORD_FOUND
        # And it must say what it does NOT mean.
        assert "not evidence of Medicare enrolment" in result.get("scope_note")

    async def test_match_is_review_never_automatic_rejection(self):
        client = FakeCMSClient({cms_ppef.CMS_REVOCATION_DATASET_ID:
                                [revocation_row(NPI="1003879883")]})
        entity = onc_entity()
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(
                FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})),
            cms_revocation=await revocation_source(client),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data, pecos_found=True)
        d3 = dim(assemble_dimensions(entity, profile, sources), Dimension.D3_EXCLUSION_REVOCATION)
        assert d3.disposition == Disposition.REVIEW.value
        assert d3.disposition != Disposition.FAIL.value
        assert d3.requires_analyst is True

    async def test_captures_every_field_the_amendment_requires(self):
        client = FakeCMSClient({cms_ppef.CMS_REVOCATION_DATASET_ID:
                                [revocation_row(NPI="1003879883")]})
        match = (await revocation_source(client)).get("matches")[0]
        for key in ("enrollment_id", "npi", "state", "provider_type_desc", "revocation_reason",
                    "revocation_effective_date", "reenrollment_bar_expiration_date"):
            assert match[key] is not None, key

    async def test_multiple_matches_all_presented(self):
        """False positives are resolved by an analyst, not by picking one row."""
        rows = [revocation_row(NPI="1003879883", ENRLMT_ID="I1"),
                revocation_row(NPI="1003879883", ENRLMT_ID="I2", STATE_CD="TX")]
        client = FakeCMSClient({cms_ppef.CMS_REVOCATION_DATASET_ID: rows})
        result = await revocation_source(client)
        assert result.get("match_count") == 2

    async def test_three_controls_stay_separately_identifiable(self):
        entity = onc_entity()
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(FakeCMSClient()),
            cms_revocation=await revocation_source(FakeCMSClient()),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data)
        d3 = dim(assemble_dimensions(entity, profile, sources), Dimension.D3_EXCLUSION_REVOCATION)
        assert {i.source for i in d3.items} == {"OIG_LEIE", "SAM_GOV", "CMS_REVOCATION"}


# ── PPEF relational components ───────────────────────────────────────────────

class TestRelationalComponents:
    async def test_download_only_component_without_snapshot_is_unavailable_not_fabricated(self):
        """No API and nothing ingested -> UNAVAILABLE, never an invented match.

        CORRECTED. This test previously asserted the reason string
        "not_published_via_cms_data_api", encoding a claim that CMS does not
        publish these components at all. That claim was disproven: CMS publishes
        all four as quarterly CSV sub-files of the parent dataset (see
        ppef_resources). The BEHAVIOUR under test is unchanged and still
        asserted — no data-api and no snapshot means UNAVAILABLE with data=None
        — only the reason string now states the true fact.
        """
        result = await PPEFRelationalConnector(FakeCMSClient()).practice_locations(["I1"])
        assert result.success is False
        assert "no_snapshot_ingested" in (result.error or "")
        assert result.data is None          # fail-closed: nothing readable as clean

    async def test_api_unavailable_reason_states_the_transport_fact(self):
        """The other reason string must stay accurate too: CMS offers no API here."""
        from app.Tefca.cms_ppef import COMPONENT_UNPUBLISHED_REASON
        assert "not_available_via_cms_data_api" in COMPONENT_UNPUBLISHED_REASON
        assert "download" in COMPONENT_UNPUBLISHED_REASON.lower()

    async def test_snapshot_backed_component_resolves_without_any_api(self):
        """With a snapshot present, a download-only component answers normally."""
        async def store(component, key_field, ids):
            return (
                [{"ENRLMT_ID": "I1", "CITY_NAME": "BALTIMORE", "STATE_CD": "MD",
                  "ZIP_CD": "212011925"}],
                {"resource_version": "2026.07.17", "sha256": "abc123",
                 "cms_resource_title": "Address Sub-File Q3 2026", "realtime": False},
            )

        conn = PPEFRelationalConnector(FakeCMSClient(), local_store=store)
        result = await conn.practice_locations(["I1"])
        assert result.success is True
        assert result.get("record_count") == 1
        assert result.get("records")[0]["CITY_NAME"] == "BALTIMORE"
        assert result.get("provenance")["cms_resource_title"] == "Address Sub-File Q3 2026"

    async def test_snapshot_with_no_rows_is_not_the_same_as_no_snapshot(self):
        """"Searched, genuinely none" and "never loaded" must not collapse.

        CMS documents that some individual enrolments legitimately have no
        practice-location row, so an empty result from a real snapshot is a
        finding about the enrolment, while a missing snapshot is a finding about
        us.
        """
        async def empty_store(component, key_field, ids):
            return ([], {"resource_version": "2026.07.17", "sha256": "abc123"})

        conn = PPEFRelationalConnector(FakeCMSClient(), local_store=empty_store)
        result = await conn.practice_locations(["I1"])
        assert result.success is True          # the snapshot answered
        assert result.get("found") is False    # and the answer is "no rows"
        assert result.get("record_count") == 0

    async def test_local_store_failure_degrades_to_unavailable_not_to_a_clean_result(self):
        async def broken_store(component, key_field, ids):
            raise RuntimeError("database unreachable")

        conn = PPEFRelationalConnector(FakeCMSClient(), local_store=broken_store)
        result = await conn.practice_locations(["I1"])
        assert result.success is False
        assert result.data is None

    async def test_practice_location_linkage_when_published(self, monkeypatch):
        """The ENRLMT_ID join is implemented and works the moment CMS serves it."""
        monkeypatch.setitem(cms_ppef.PPEF_COMPONENT_DATASETS,
                            PPEFComponent.PRACTICE_LOCATION, "ds-loc")
        client = FakeCMSClient({"ds-loc": [
            {"ENRLMT_ID": "I1", "ADR_LN_1": "1200 Health Center Drive", "ADR_LN_2": "",
             "CITY_NAME": "Baltimore", "STATE_CD": "MD", "ZIP_CD": "21201"},
        ]})
        result = await PPEFRelationalConnector(client).practice_locations(["I1"])
        assert result.success and result.get("record_count") == 1
        assert client.calls[0][2] == {"ENRLMT_ID": "I1"}

    async def test_multiple_practice_locations_are_all_kept(self, monkeypatch):
        monkeypatch.setitem(cms_ppef.PPEF_COMPONENT_DATASETS,
                            PPEFComponent.PRACTICE_LOCATION, "ds-loc")
        client = FakeCMSClient({"ds-loc": [
            {"ENRLMT_ID": "I1", "CITY_NAME": "Baltimore", "STATE_CD": "MD", "ZIP_CD": "21201"},
            {"ENRLMT_ID": "I1", "CITY_NAME": "Columbia", "STATE_CD": "MD", "ZIP_CD": "21044"},
        ]})
        result = await PPEFRelationalConnector(client).practice_locations(["I1"])
        assert result.get("record_count") == 2

    async def test_missing_practice_location_is_not_a_failure(self, monkeypatch):
        monkeypatch.setitem(cms_ppef.PPEF_COMPONENT_DATASETS,
                            PPEFComponent.PRACTICE_LOCATION, "ds-loc")
        client = FakeCMSClient({"ds-loc": []})
        entity = onc_entity()
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(
                FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})),
            cms_revocation=await revocation_source(FakeCMSClient()),
            cms_ppef_practice_location=await PPEFRelationalConnector(client).practice_locations(["I1"]),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data, pecos_found=True)
        d4 = dim(assemble_dimensions(entity, profile, sources), Dimension.D4_ADDRESS)
        assert d4.disposition != Disposition.FAIL.value
        notes = " ".join(i.note or "" for i in d4.items)
        assert "NO_PRACTICE_LOCATION" in notes

    async def test_reassignment_uses_the_documented_key(self, monkeypatch):
        monkeypatch.setitem(cms_ppef.PPEF_COMPONENT_DATASETS,
                            PPEFComponent.REASSIGNMENT, "ds-reasgn")
        client = FakeCMSClient({"ds-reasgn": [
            {"REASGN_BNFT_ENRLMT_ID": "I1", "RCV_BNFT_ENRLMT_ID": "O9"},
        ]})
        result = await PPEFRelationalConnector(client).reassignments(["I1"])
        assert result.get("record_count") == 1
        assert client.calls[0][2] == {"REASGN_BNFT_ENRLMT_ID": "I1"}

    async def test_multiple_reassignments_all_returned(self, monkeypatch):
        monkeypatch.setitem(cms_ppef.PPEF_COMPONENT_DATASETS,
                            PPEFComponent.REASSIGNMENT, "ds-reasgn")
        client = FakeCMSClient({"ds-reasgn": [
            {"REASGN_BNFT_ENRLMT_ID": "I1", "RCV_BNFT_ENRLMT_ID": "O9"},
            {"REASGN_BNFT_ENRLMT_ID": "I1", "RCV_BNFT_ENRLMT_ID": "O10"},
        ]})
        result = await PPEFRelationalConnector(client).reassignments(["I1"])
        assert result.get("record_count") == 2

    async def test_enrollment_id_resolves_back_to_identity(self):
        """The Amendment 5 hop: receiving ENRLMT_ID -> receiving organisation."""
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [
            enrollment_row(ENRLMT_ID="O9", ORG_NAME="RIVERSIDE HEALTH SYSTEM", NPI="1999000001"),
        ]})
        result = await PPEFEnrollmentConnector(client).lookup_by_enrollment_id("O9")
        assert result.get("found") is True
        assert result.get("records")[0]["organization_name"] == "RIVERSIDE HEALTH SYSTEM"
        assert result.get("records")[0]["enrollment_class"] == "ORGANIZATION"


# ── Applicability ────────────────────────────────────────────────────────────

class TestApplicability:
    def test_hospital_does_not_get_blanket_mandatory_rules(self):
        """'Hospital => every source mandatory' is exactly what the spec forbids."""
        from app.Tefca.applicability import pecos_reassignment_applicability

        profile = build_profile(onc_entity(), nppes_data=nppes_result().data)
        assert profile.entity_category == EntityCategory.PROVIDER_ORGANIZATION
        # Medicare enrolment IS required for a Medicare-relevant provider org...
        assert profile.applicability_of(Dimension.D2_MEDICARE_ENROLLMENT) == \
            Applicability.REQUIRED.value
        # ...but reassignment corroboration is NOT thereby mandatory. Not every
        # hospital has every kind of PECOS relationship.
        assert pecos_reassignment_applicability(profile) == Applicability.CORROBORATIVE.value

    def test_payer_pecos_is_not_applicable(self):
        nppes = nppes_result(taxonomy_code="302R00000X", taxonomy="Health Maintenance Organization")
        profile = build_profile(onc_entity(), nppes_data=nppes.data)
        assert profile.entity_category == EntityCategory.PAYER
        assert profile.applicability_of(Dimension.D2_MEDICARE_ENROLLMENT) == \
            Applicability.NOT_APPLICABLE.value

    def test_public_health_agency_pecos_not_applicable(self):
        nppes = nppes_result(taxonomy_code="251K00000X", taxonomy="Public Health or Welfare Agency")
        profile = build_profile(onc_entity(), nppes_data=nppes.data)
        assert profile.entity_category == EntityCategory.PUBLIC_HEALTH_AGENCY
        assert profile.applicability_of(Dimension.D2_MEDICARE_ENROLLMENT) == \
            Applicability.NOT_APPLICABLE.value

    def test_qhin_is_network_operator_and_pecos_not_applicable(self):
        entity = onc_entity(type=[{"coding": [{"code": "QHIN"}]}])
        profile = build_profile(entity, nppes_data=nppes_result().data)
        assert profile.entity_category == EntityCategory.HIE_HIN_QHIN
        assert profile.applicability_of(Dimension.D2_MEDICARE_ENROLLMENT) == \
            Applicability.NOT_APPLICABLE.value

    def test_individual_provider_from_nppes_type_1(self):
        nppes = nppes_result(enumeration_type="NPI-1", taxonomy_code="207V00000X")
        profile = build_profile(onc_entity(), nppes_data=nppes.data)
        assert profile.entity_category == EntityCategory.INDIVIDUAL_PROVIDER
        assert profile.medicare_relevance == "LIKELY"

    def test_unknown_taxonomy_stays_corroborative_not_required(self):
        """Undetermined Medicare relevance must never harden into an obligation."""
        nppes = nppes_result(enumeration_type="NPI-2", taxonomy_code="ZZZ9999999")
        profile = build_profile(onc_entity(), nppes_data=nppes.data)
        assert profile.medicare_relevance == "UNDETERMINED"
        assert profile.applicability_of(Dimension.D2_MEDICARE_ENROLLMENT) == \
            Applicability.CORROBORATIVE.value

    def test_evidence_beats_assumption_for_medicare_relevance(self):
        nppes = nppes_result(taxonomy_code="302R00000X")  # payer by taxonomy
        profile = build_profile(onc_entity(), nppes_data=nppes.data, pecos_found=True)
        assert profile.medicare_relevance == "LIKELY"

    def test_methodology_override_is_honoured(self):
        profile = build_profile(
            onc_entity(), nppes_data=nppes_result().data,
            methodology_requires={Dimension.D2_MEDICARE_ENROLLMENT.value:
                                  Applicability.NOT_APPLICABLE.value},
        )
        assert profile.applicability_of(Dimension.D2_MEDICARE_ENROLLMENT) == \
            Applicability.NOT_APPLICABLE.value


# ── NPI type alignment / Amendment 2 ─────────────────────────────────────────

class TestNPIAlignment:
    async def _identity(self, entity, enrollment_rows, nppes=None):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: enrollment_rows})
        nppes = nppes or nppes_result()
        sources = clean_sources(
            nppes=nppes,
            cms_ppef_enrollment=await enrollment_source(client, entity["identifier"][0]["value"]),
            cms_revocation=await revocation_source(FakeCMSClient()),
        )
        profile = build_profile(entity, nppes_data=nppes.data, pecos_found=bool(enrollment_rows))
        return dim(assemble_dimensions(entity, profile, sources), Dimension.D1_IDENTITY)

    async def test_type_2_org_npi_aligns_with_organizational_enrollment(self):
        d1 = await self._identity(
            onc_entity(),
            [enrollment_row(ENRLMT_ID="O1", ORG_NAME="RIVERSIDE COMMUNITY HEALTH NETWORK")],
        )
        pecos_item = next(i for i in d1.items if i.source == "CMS_PPEF_ENROLLMENT")
        assert pecos_item.normalized_values["type_alignment"]["result"] == "ALIGNED"

    async def test_type_divergence_is_review_not_fail(self):
        """NPPES says organisation, PECOS shows an individual enrolment."""
        d1 = await self._identity(onc_entity(), [enrollment_row(ENRLMT_ID="I1", ORG_NAME="")])
        assert d1.disposition == Disposition.REVIEW.value
        assert d1.disposition != Disposition.FAIL.value

    async def test_multiple_npi_flag_y_blocks_a_conflict_finding(self):
        """Amendment 2: differing NPI + MULTIPLE_NPI_FLAG=Y is UNRESOLVED, not CONFLICT."""
        entity = onc_entity()
        rows = [enrollment_row(NPI="1003879883", MULTIPLE_NPI_FLAG="Y")]
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: rows})
        enrollment = await enrollment_source(client, "1003879883")
        # Simulate the enrolment carrying a different primary NPI than the RCE one.
        enrollment.data["records"][0]["npi"] = "1999999999"
        sources = clean_sources(cms_ppef_enrollment=enrollment,
                                cms_revocation=await revocation_source(FakeCMSClient()))
        profile = build_profile(entity, nppes_data=nppes_result().data, pecos_found=True)
        d1 = dim(assemble_dimensions(entity, profile, sources), Dimension.D1_IDENTITY)
        pecos_item = next(i for i in d1.items if i.source == "CMS_PPEF_ENROLLMENT")
        unresolved = pecos_item.normalized_values["unresolved"]
        assert unresolved[0]["result"] == "UNRESOLVED_MULTIPLE_NPI"
        assert unresolved[0]["rule_applied"] == "AMENDMENT_2_MULTIPLE_NPI_FLAG"
        assert not pecos_item.field_conflicts  # PECOS never contributes an identity conflict
        assert d1.disposition != Disposition.FAIL.value

    async def test_pecos_never_overrules_nppes_on_identity(self):
        nppes = nppes_result(npi="1003879883")
        d1 = await self._identity(onc_entity(), [enrollment_row()], nppes=nppes)
        nppes_item = next(i for i in d1.items if i.source == "NPPES")
        pecos_item = next(i for i in d1.items if i.source == "CMS_PPEF_ENROLLMENT")
        assert nppes_item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"
        assert pecos_item.rule_applied == "PECOS_CORROBORATES_IDENTITY_NEVER_REPLACES_NPPES"


# ── Relationship (D6) ────────────────────────────────────────────────────────

class TestRelationship:
    def test_rce_and_pecos_agreement_is_corroboration(self):
        out = relationship_conflict_review("Riverside Health System", ["RIVERSIDE HEALTH SYSTEM"])
        assert out["result"] == "CORROBORATED"

    def test_different_organizations_is_review_with_all_shown(self):
        out = relationship_conflict_review("Riverside Health System",
                                           ["MERCY MEDICAL", "JOHNS HOPKINS"])
        assert out["result"] == "REVIEW"
        assert len(out["organizations"]) == 2

    async def test_non_provider_entity_reassignment_not_applicable(self):
        entity = onc_entity(type=[{"coding": [{"code": "QHIN"}]}])
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(FakeCMSClient()),
            cms_revocation=await revocation_source(FakeCMSClient()),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data)
        d6 = dim(assemble_dimensions(entity, profile, sources),
                 Dimension.D6_PROVIDER_ORG_RELATIONSHIP)
        reassign = next(i for i in d6.items if i.source == "CMS_PPEF_REASSIGNMENT")
        assert reassign.disposition == Disposition.NOT_APPLICABLE.value

    async def test_rce_relationship_present_without_reassignment_never_fails(self, monkeypatch):
        monkeypatch.setitem(cms_ppef.PPEF_COMPONENT_DATASETS,
                            PPEFComponent.REASSIGNMENT, "ds-reasgn")
        entity = onc_entity()
        nppes = nppes_result(enumeration_type="NPI-1", taxonomy_code="207V00000X")
        sources = clean_sources(
            nppes=nppes,
            cms_ppef_enrollment=await enrollment_source(FakeCMSClient()),
            cms_revocation=await revocation_source(FakeCMSClient()),
            cms_ppef_reassignment=await PPEFRelationalConnector(
                FakeCMSClient({"ds-reasgn": []})).reassignments(["I1"]),
        )
        profile = build_profile(entity, nppes_data=nppes.data, pecos_found=False)
        d6 = dim(assemble_dimensions(entity, profile, sources),
                 Dimension.D6_PROVIDER_ORG_RELATIONSHIP)
        assert d6.disposition in (Disposition.REVIEW.value, Disposition.PASS.value)
        assert d6.disposition != Disposition.FAIL.value

    async def test_rce_relationship_is_labelled_primary(self):
        entity = onc_entity()
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(FakeCMSClient()),
            cms_revocation=await revocation_source(FakeCMSClient()),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data)
        d6 = dim(assemble_dimensions(entity, profile, sources),
                 Dimension.D6_PROVIDER_ORG_RELATIONSHIP)
        rce = next(i for i in d6.items if i.source == "ONC_RCE_DIRECTORY")
        assert rce.rule_applied == "RCE_RELATIONSHIP_IS_PRIMARY_TEFCA_EVIDENCE"


# ── Address ──────────────────────────────────────────────────────────────────

class TestAddress:
    def test_normalization_absorbs_formatting_differences(self):
        submitted = {"line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201"}
        candidate = {"line": ["1200 HEALTH CENTER DR."], "city": "BALTIMORE",
                     "state": "MD", "postalCode": "21201-0000"}
        rows = build_address_rows(submitted, [{"source": "NPPES", "address": candidate}])
        assert rows[1].comparison == AddressComparison.MATCH

    def test_different_city_is_a_conflict_for_review(self):
        submitted = {"line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201"}
        candidate = {"line": ["50 Main Street"], "city": "Austin",
                     "state": "TX", "postalCode": "78701"}
        rows = build_address_rows(submitted, [{"source": "NPPES", "address": candidate}])
        assert rows[1].comparison == AddressComparison.CONFLICT
        assert reconcile(rows)["result"] == AddressComparison.CONFLICT

    def test_same_city_different_street_is_partial(self):
        submitted = {"line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201"}
        candidate = {"line": ["77 Hospital Way"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201"}
        rows = build_address_rows(submitted, [{"source": "NPPES", "address": candidate}])
        assert rows[1].comparison == AddressComparison.PARTIAL_MATCH

    def test_submitted_address_is_never_replaced(self):
        submitted = {"line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201"}
        rows = build_address_rows(submitted, [
            {"source": "NPPES", "address": {"line": ["50 Main Street"], "city": "Austin",
                                            "state": "TX", "postalCode": "78701"}}])
        onc_row = rows[0]
        assert "1200 Health Center Drive" in onc_row.original_value
        assert onc_row.normalized_value  # normalised form stored ALONGSIDE, not instead

    def test_every_row_carries_source_original_normalized_and_time(self):
        rows = build_address_rows({"line": ["1 A St"], "city": "X", "state": "MD",
                                   "postalCode": "21201"},
                                  [{"source": "NPPES", "address": {"line": ["1 A St"],
                                                                   "city": "X", "state": "MD",
                                                                   "postalCode": "21201"}}])
        for row in rows:
            d = row.to_dict()
            for key in ("source", "original_value", "normalized_value", "comparison",
                        "retrieved_at"):
                assert key in d

    def test_website_row_cannot_move_the_dimension(self):
        submitted = {"line": ["1200 Health Center Drive"], "city": "Baltimore",
                     "state": "MD", "postalCode": "21201"}
        rows = build_address_rows(submitted, [
            {"source": "NPPES", "address": submitted},
            {"source": "ENTRANT_WEBSITE", "address": {"line": ["999 Elsewhere"],
                                                      "city": "Austin", "state": "TX",
                                                      "postalCode": "78701"}},
        ])
        assert reconcile(rows)["result"] == AddressComparison.MATCH


# ── Website corroboration ────────────────────────────────────────────────────

class TestWebsite:
    async def test_no_website_supplied_is_not_held_against_entity(self):
        out = await website_corroboration(onc_entity())
        assert out["result"] == WEBSITE_NOT_FOUND
        assert out["affects_determination"] is False

    async def test_unreachable_site_is_unavailable_not_a_finding(self, monkeypatch):
        entity = onc_entity(telecom=[{"system": "url", "value": "https://nonexistent.invalid"}])

        class Boom:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): raise OSError("dns failure")

        monkeypatch.setattr("app.Tefca.evidence_service.httpx.AsyncClient", lambda **k: Boom())
        out = await website_corroboration(entity)
        assert out["result"] == WEBSITE_UNAVAILABLE
        assert out["affects_determination"] is False

    @pytest.mark.parametrize("status", [403, 429, 500, 503])
    async def test_blocked_or_erroring_site_is_unavailable(self, monkeypatch, status):
        entity = onc_entity(telecom=[{"system": "url", "value": "https://example.test"}])

        class Resp:
            status_code = status
            text = ""
            headers: Dict[str, str] = {}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return Resp()

        monkeypatch.setattr("app.Tefca.evidence_service.httpx.AsyncClient", lambda **k: Client())
        out = await website_corroboration(entity)
        assert out["result"] == WEBSITE_UNAVAILABLE
        assert out["affects_determination"] is False

    async def test_website_result_is_never_pass_or_fail(self, monkeypatch):
        entity = onc_entity(telecom=[{"system": "url", "value": "https://example.test"}])

        class Resp:
            status_code = 200
            text = "Riverside Community Health Network"
            headers: Dict[str, str] = {}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return Resp()

        monkeypatch.setattr("app.Tefca.evidence_service.httpx.AsyncClient", lambda **k: Client())
        out = await website_corroboration(entity)
        assert out["result"] not in ("PASS", "FAIL")


# ── Provenance, persistence, audit ───────────────────────────────────────────

class TestProvenanceAndAudit:
    async def test_every_cms_item_carries_point_in_time_provenance(self):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})
        entity = onc_entity()
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(client),
            cms_revocation=await revocation_source(
                FakeCMSClient({cms_ppef.CMS_REVOCATION_DATASET_ID: []})),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data, pecos_found=True)
        results = assemble_dimensions(entity, profile, sources)
        cms_items = [i for r in results for i in r.items if i.source.startswith("CMS_")
                     and i.disposition != Disposition.UNAVAILABLE.value]
        assert cms_items
        for item in cms_items:
            assert item.query_timestamp, item.source
            assert item.dataset_version_anchor, item.source
            assert item.realtime is False   # quarterly data is never labelled real-time
            assert item.update_cadence == "quarterly"

    async def test_persistence_rows_are_flat_and_complete(self):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})
        service = EvidenceService(manager=_StubManager(), cms_client=client)
        evidence = await service.build_evidence(onc_entity())
        rows = evidence_rows_for_persistence("rce-org-test-001", "review-1", evidence)
        assert rows
        for row in rows:
            assert row["entity_id"] == "rce-org-test-001"
            assert row["review_id"] == "review-1"
            assert row["evidence_dimension"]
            assert row["disposition"]
            assert row["generation_timestamp"]

    async def test_reruns_produce_distinct_generations(self):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})
        service = EvidenceService(manager=_StubManager(), cms_client=client)
        first = await service.build_evidence(onc_entity())
        await asyncio.sleep(0.01)
        second = await service.build_evidence(onc_entity())
        assert first["generated_at"] != second["generated_at"]
        rows_a = evidence_rows_for_persistence("e", None, first)
        rows_b = evidence_rows_for_persistence("e", None, second)
        # Distinct generation stamps are what make history preservable: the second
        # run adds rows, it does not address the same rows as the first.
        assert rows_a[0]["generation_timestamp"] != rows_b[0]["generation_timestamp"]


class _StubManager:
    """SourceConnectorManager stand-in returning the existing five source keys."""

    async def query_all_sources(self, entity):
        return clean_sources()


# ── Structural invariants ────────────────────────────────────────────────────

class TestNoAPICounting:
    async def test_no_score_or_percentage_is_produced(self):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})
        service = EvidenceService(manager=_StubManager(), cms_client=client)
        evidence = await service.build_evidence(onc_entity())
        blob = str(evidence).lower()
        for banned in ("confidence_score", "percent_verified", "sources_passed", "api_count"):
            assert banned not in blob
        assert "score" not in evidence["sufficiency"]

    async def test_sufficiency_is_per_dimension_not_a_tally(self):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})
        service = EvidenceService(manager=_StubManager(), cms_client=client)
        evidence = await service.build_evidence(onc_entity())
        summary = evidence["sufficiency"]
        assert set(summary) >= {"required_dimensions", "dimensions_awaiting_analyst",
                                "dimensions_unavailable", "all_required_dimensions_settled"}
        assert all(isinstance(v, (list, bool, str)) for v in summary.values())

    async def test_correlated_cms_components_are_one_body_of_evidence(self):
        """PPEF components must not appear as separate systems in health output."""
        health = await cms_capability_health(FakeCMSClient())
        systems = {s["system"] for s in health["systems"]}
        assert systems == {"CMS_PPEF", "CMS_REVOCATION"}
        ppef = next(s for s in health["systems"] if s["system"] == "CMS_PPEF")
        assert {c["capability"] for c in ppef["capabilities"]} == {
            "ENROLLMENT", "PRACTICE_LOCATION", "REASSIGNMENT",
            "ADDITIONAL_NPIS", "SECONDARY_SPECIALTY"}

    async def test_all_six_dimensions_are_always_present_and_ordered(self):
        client = FakeCMSClient({cms_ppef.PPEF_ENROLLMENT_DATASET_ID: [enrollment_row()]})
        service = EvidenceService(manager=_StubManager(), cms_client=client)
        evidence = await service.build_evidence(onc_entity())
        assert [d["dimension"] for d in evidence["dimensions"]] == [
            "IDENTITY", "MEDICARE_ENROLLMENT", "EXCLUSION_REVOCATION",
            "ADDRESS", "TEFCA_ALIGNMENT", "PROVIDER_ORG_RELATIONSHIP"]

    async def test_no_dimension_is_ever_auto_failed(self):
        """The single most important invariant in this feature."""
        client = FakeCMSClient(raise_for={
            cms_ppef.PPEF_ENROLLMENT_DATASET_ID: CMSUnavailable("HTTP 500"),
            cms_ppef.CMS_REVOCATION_DATASET_ID: CMSUnavailable("HTTP 500"),
        })
        service = EvidenceService(manager=_StubManager(), cms_client=client)
        for entity in (onc_entity(), onc_entity(address=[], partOf={}),
                       onc_entity(type=[{"coding": [{"code": "QHIN"}]}])):
            evidence = await service.build_evidence(entity)
            for d in evidence["dimensions"]:
                assert d["disposition"] != "FAIL", (entity["id"], d["dimension"])

    def test_disposition_vocabulary_matches_the_specification(self):
        assert {d.value for d in CORE_DISPOSITIONS} == {
            "PASS", "FAIL", "REVIEW", "NOT_APPLICABLE", "UNAVAILABLE"}


class TestTEFCAAlignment:
    def test_fields_onc_does_not_supply_are_reported_not_inferred(self):
        entity = onc_entity()
        sources = clean_sources()
        profile = build_profile(entity, nppes_data=nppes_result().data)
        d5 = dim(assemble_dimensions(entity, profile, sources), Dimension.D5_TEFCA_ALIGNMENT)
        not_supplied = d5.items[0].normalized_values["fields_not_supplied_by_onc"]
        assert not_supplied["hcid"] == "NOT_SUPPLIED_BY_ONC"
        assert not_supplied["exchange_purpose"] == "NOT_SUPPLIED_BY_ONC"

    def test_subparticipant_without_parent_is_review(self):
        entity = onc_entity(type=[{"coding": [{"code": "SUBPARTICIPANT"}]}], partOf={})
        profile = build_profile(entity, nppes_data=nppes_result().data)
        d5 = dim(assemble_dimensions(entity, profile, clean_sources()),
                 Dimension.D5_TEFCA_ALIGNMENT)
        assert d5.disposition == Disposition.REVIEW.value


class TestSufficiency:
    def test_unavailable_required_dimension_blocks_settled(self):
        from app.Tefca.evidence_dimensions import DimensionResult
        results = [
            DimensionResult(Dimension.D1_IDENTITY.value, Disposition.PASS.value,
                            Applicability.REQUIRED.value, ""),
            DimensionResult(Dimension.D2_MEDICARE_ENROLLMENT.value,
                            Disposition.UNAVAILABLE.value, Applicability.REQUIRED.value, ""),
        ]
        summary = sufficiency_summary(results)
        assert summary["all_required_dimensions_settled"] is False
        assert summary["dimensions_unavailable"] == [Dimension.D2_MEDICARE_ENROLLMENT.value]

    def test_not_applicable_dimensions_do_not_block(self):
        from app.Tefca.evidence_dimensions import DimensionResult
        results = [
            DimensionResult(Dimension.D1_IDENTITY.value, Disposition.PASS.value,
                            Applicability.REQUIRED.value, ""),
            DimensionResult(Dimension.D2_MEDICARE_ENROLLMENT.value,
                            Disposition.NOT_APPLICABLE.value,
                            Applicability.NOT_APPLICABLE.value, ""),
        ]
        assert sufficiency_summary(results)["all_required_dimensions_settled"] is True


class TestAuditCallSignature:
    """Every `log_tefca_event(...)` in routes.py must bind to its real signature.

    This exists because it did not. The dimension-evidence endpoints called
    `log_tefca_event(db, user, "event", {...})` — positional — while the function
    takes `db` positionally and everything else keyword-only. Python raises that
    only when the line executes, so it survived import, unit tests, a green
    1067-test suite and a deploy, and first appeared as an HTTP 500 on dev.

    A static bind over every call site catches the whole class at test time
    instead of at request time. It is AST-based rather than a mock, so it needs
    no database and covers call sites nobody wrote a test for.
    """

    def test_every_log_tefca_event_call_binds(self):
        import ast
        import inspect
        from app.services.audit import log_tefca_event

        signature = inspect.signature(log_tefca_event)
        source = open("app/Tefca/routes.py", encoding="utf-8").read()
        tree = ast.parse(source)

        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "log_tefca_event"
        ]
        assert calls, "no log_tefca_event call sites found — has the audit call been renamed?"

        failures = []
        for call in calls:
            positional = [object()] * len(call.args)
            keywords = {kw.arg: object() for kw in call.keywords if kw.arg}
            if any(kw.arg is None for kw in call.keywords):
                continue  # **kwargs splat — cannot be checked statically
            try:
                signature.bind(*positional, **keywords)
            except TypeError as exc:
                failures.append(f"routes.py:{call.lineno}: {exc}")
        assert not failures, "log_tefca_event call sites do not match its signature:\n" + "\n".join(failures)


class TestTruncatedSnapshotCannotProveAbsence:
    """A partial snapshot must never manufacture a clean negative.

    Found during dev verification: a capped ingest returned no rows for an
    enrolment and the address dimension reported NO_PRACTICE_LOCATION — a claim
    about CMS data — when the truth was only that our snapshot was partial.
    "Not in our snapshot" and "not in CMS" are different statements and only the
    second says anything about the entity.
    """

    async def test_truncated_snapshot_with_no_rows_is_inconclusive(self):
        async def truncated_store(component, key_field, ids):
            return ([], {"resource_version": "2026.07.17", "sha256": "abc",
                         "rows_truncated": True})

        conn = PPEFRelationalConnector(FakeCMSClient(), local_store=truncated_store)
        result = await conn.practice_locations(["I1"])
        assert result.success is True
        assert result.get("found") is False
        assert result.get("inconclusive") is True
        assert "snapshot_truncated_no_rows" in result.get("inconclusive_reason")

    async def test_complete_snapshot_with_no_rows_is_a_real_absence(self):
        async def complete_store(component, key_field, ids):
            return ([], {"resource_version": "2026.07.17", "sha256": "abc",
                         "rows_truncated": False})

        conn = PPEFRelationalConnector(FakeCMSClient(), local_store=complete_store)
        result = await conn.practice_locations(["I1"])
        assert result.get("found") is False
        assert result.get("inconclusive") is False   # CMS genuinely has no row

    async def test_address_dimension_does_not_claim_no_practice_location_when_truncated(self):
        async def truncated_store(component, key_field, ids):
            return ([], {"resource_version": "2026.07.17", "rows_truncated": True,
                         "sha256": "abc"})

        entity = onc_entity()
        conn = PPEFRelationalConnector(FakeCMSClient(), local_store=truncated_store)
        sources = clean_sources(
            cms_ppef_enrollment=await enrollment_source(FakeCMSClient()),
            cms_revocation=await revocation_source(FakeCMSClient()),
            cms_ppef_practice_location=await conn.practice_locations(["I1"]),
        )
        profile = build_profile(entity, nppes_data=nppes_result().data)
        d4 = dim(assemble_dimensions(entity, profile, sources), Dimension.D4_ADDRESS)
        notes = " ".join(i.note or "" for i in d4.items)
        assert "NO_PRACTICE_LOCATION" not in notes
        assert "snapshot_truncated_no_rows" in notes

    async def test_reassignment_absence_from_a_partial_snapshot_is_insufficient_evidence(self):
        async def truncated_store(component, key_field, ids):
            return ([], {"resource_version": "2026.07.17", "rows_truncated": True,
                         "sha256": "abc"})

        entity = onc_entity()
        nppes = nppes_result(enumeration_type="NPI-1", taxonomy_code="207V00000X")
        conn = PPEFRelationalConnector(FakeCMSClient(), local_store=truncated_store)
        sources = clean_sources(
            nppes=nppes,
            cms_ppef_enrollment=await enrollment_source(FakeCMSClient()),
            cms_revocation=await revocation_source(FakeCMSClient()),
            cms_ppef_reassignment=await conn.reassignments(["I1"]),
        )
        profile = build_profile(entity, nppes_data=nppes.data, pecos_found=False)
        d6 = dim(assemble_dimensions(entity, profile, sources),
                 Dimension.D6_PROVIDER_ORG_RELATIONSHIP)
        item = next(i for i in d6.items if i.source == "CMS_PPEF_REASSIGNMENT")
        assert item.disposition == Disposition.INSUFFICIENT_EVIDENCE.value
        assert d6.disposition != Disposition.FAIL.value
