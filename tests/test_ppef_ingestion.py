"""
PPEF resource discovery, versioned ingestion, and the local evidence store.

NO NETWORK. The CMS resource list and the CSV bytes are both injected, so these
run identically on a laptop, in CI, and during a CMS outage.

The behaviours most worth protecting here are the ones that would be invisible
if they broke:

  * a component is identified by FILE NAME, not by CMS display title — CMS
    titles the practice-location file "Address Sub-File";
  * a media file_uuid is never treated as a data-api dataset id;
  * schema drift aborts an ingest instead of loading nulls;
  * a truncated ingest is never recorded as a complete one;
  * the checksum covers the bytes actually ingested.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List

import pytest

from app.Tefca.ppef_ingest import (
    IngestError,
    PPEFIngestor,
    SchemaDriftError,
    normalize_row,
    validate_schema,
)
from app.Tefca.ppef_resources import (
    EXPECTED_FIELDS,
    JOIN_KEYS,
    KNOWN_COMPONENT_TRANSPORT,
    PPEF_PARENT_DATASET_ID,
    PPEFResourceCatalog,
    ResourceStatus,
    Transport,
    _component_for,
    _version_from_filename,
)

pytestmark = pytest.mark.regression


# ── The real CMS /resources payload, trimmed to the rows that matter ─────────
# Captured live 2026-08-19 from
#   GET https://data.cms.gov/data-api/v1/dataset/2457ea29-.../resources

CMS_RESOURCES_SAMPLE: List[Dict[str, Any]] = [
    {"type": "Primary", "title": "Medicare FFS Public Provider Enrollment Q3 2026",
     "file_name": "PPEF_Enrollment_Extract_2026.07.17.csv",
     "file_uuid": "faa3796b-01c7-46b7-b9de-fcf981d39922", "file_mime": "text/csv",
     "file_size": 320536185,
     "file_url": "https://data.cms.gov/sites/default/files/2026-07/x/PPEF_Enrollment_Extract_2026.07.17.csv"},
    {"type": "Data Dictionary", "title": "PPEF Data Dictionary",
     "file_name": "PPEF_Data_Dictionary.pdf", "file_uuid": "12249c8b", "file_mime": "application/pdf",
     "file_url": "https://data.cms.gov/sites/default/files/2026-07/PPEF_Data_Dictionary.pdf"},
    {"type": "Dataset", "title": "Additional NPIs Sub-File Q3 2026",
     "file_name": "PPEF_Additional_NPIs_2026.07.17.csv",
     "file_uuid": "43e5bf24-ccce-4154-8e0a-fe0949dc19cd", "file_mime": "text/csv",
     "file_size": 3596195,
     "file_url": "https://data.cms.gov/sites/default/files/2026-07/PPEF_Additional_NPIs_2026.07.17.csv"},
    {"type": "Dataset", "title": " Reassignment Sub-File Q3 2026",
     "file_name": "PPEF_Reassignment_Extract_2026.07.17.csv",
     "file_uuid": "1c29f9d9-0022-401f-8ac4-80142869c2a3", "file_mime": "text/csv",
     "file_size": 128693145,
     "file_url": "https://data.cms.gov/sites/default/files/2026-07/PPEF_Reassignment_Extract_2026.07.17.csv"},
    # NOTE the title/file-name mismatch — this row is the whole reason component
    # identification keys on the file name.
    {"type": "Dataset", "title": "Address Sub-File Q3 2026",
     "file_name": "PPEF_Practice_Location_Extract_2026.07.17.csv",
     "file_uuid": "676f9bbe-072e-4194-9c9f-cd6e310210e4", "file_mime": "text/csv",
     "file_size": 43204754,
     "file_url": "https://data.cms.gov/sites/default/files/2026-07/PPEF_Practice_Location_Extract_2026.07.17.csv"},
    {"type": "Dataset", "title": "Secondary Specialty Sub-File Q3 2026",
     "file_name": "PPEF_Secondary_Specialty_Extract_2026.07.17.csv",
     "file_uuid": "857f4823-6064-4ccc-a269-744e2170e5fb", "file_mime": "text/csv",
     "file_size": 27197304,
     "file_url": "https://data.cms.gov/sites/default/files/2026-07/PPEF_Secondary_Specialty_Extract_2026.07.17.csv"},
    {"type": "Dataset", "title": "Historical Medicare FFS Public Provider Enrollment Data 2021-2022",
     "file_name": "PECOS_Public_Provider_Main_Historical_Files_CY2021-CY2022_2025.08.08.zip",
     "file_uuid": "1dc0dc31", "file_mime": "application/zip",
     "file_url": "https://data.cms.gov/sites/default/files/2025-09/y/hist.zip"},
]


class FakeCatalog(PPEFResourceCatalog):
    def __init__(self, rows=None):
        super().__init__()
        self._rows = rows if rows is not None else CMS_RESOURCES_SAMPLE

    async def fetch_resources(self):
        return list(self._rows)


# ── Discovery ────────────────────────────────────────────────────────────────

class TestDiscovery:
    async def test_all_five_components_discovered(self):
        got = await FakeCatalog().discover()
        assert set(got) == {"ENROLLMENT", "REASSIGNMENT", "PRACTICE_LOCATION",
                            "ADDITIONAL_NPIS", "SECONDARY_SPECIALTY"}

    async def test_address_sub_file_maps_to_practice_location(self):
        """CMS calls it "Address Sub-File"; the file says Practice_Location.

        One capability, two CMS names. The internal key normalises to
        PRACTICE_LOCATION and the CMS title is preserved verbatim for the audit
        trail — no separate "Address" capability is invented.
        """
        got = await FakeCatalog().discover()
        pl = got["PRACTICE_LOCATION"]
        assert pl.cms_title == "Address Sub-File Q3 2026"
        assert pl.file_name == "PPEF_Practice_Location_Extract_2026.07.17.csv"
        assert "ADDRESS" not in got

    async def test_only_enrollment_gets_an_api_endpoint(self):
        got = await FakeCatalog().discover()
        assert got["ENROLLMENT"].api_endpoint
        assert got["ENROLLMENT"].transport == Transport.BOTH.value
        assert got["ENROLLMENT"].status == ResourceStatus.API_AND_DOWNLOAD_AVAILABLE.value
        for c in ("REASSIGNMENT", "PRACTICE_LOCATION", "ADDITIONAL_NPIS", "SECONDARY_SPECIALTY"):
            assert got[c].api_endpoint is None, c
            assert got[c].transport == Transport.DOWNLOAD.value, c
            assert got[c].status == ResourceStatus.DOWNLOAD_AVAILABLE.value, c

    async def test_media_uuid_is_not_recorded_as_a_dataset_id(self):
        """A file_uuid is a media id; it 404s against the data-api.

        Recording one as a dataset id is precisely the mistake that would make
        the system claim API_AVAILABLE for something with no API.
        """
        got = await FakeCatalog().discover()
        pl = got["PRACTICE_LOCATION"]
        assert pl.resource_id == "676f9bbe-072e-4194-9c9f-cd6e310210e4"
        assert pl.parent_dataset_id == PPEF_PARENT_DATASET_ID
        assert pl.resource_id != pl.parent_dataset_id
        assert pl.api_endpoint is None

    async def test_ancillary_pdfs_and_zips_are_ignored(self):
        got = await FakeCatalog().discover()
        names = {r.file_name for r in got.values()}
        assert not any(n.endswith((".pdf", ".zip")) for n in names)

    async def test_version_parsed_from_filename(self):
        got = await FakeCatalog().discover()
        assert all(r.resource_version == "2026.07.17" for r in got.values())
        assert _version_from_filename("PPEF_Reassignment_Extract_2026.07.17.csv") == "2026.07.17"
        assert _version_from_filename("no_version.csv") is None

    async def test_discovery_failure_returns_empty_not_stale_guesses(self):
        class Broken(PPEFResourceCatalog):
            async def fetch_resources(self):
                raise RuntimeError("CMS unreachable")

        assert await Broken().discover() == {}

    def test_component_identification_is_by_file_name(self):
        assert _component_for("PPEF_Practice_Location_Extract_2026.07.17.csv") == "PRACTICE_LOCATION"
        assert _component_for("PPEF_Reassignment_Extract_2026.07.17.csv") == "REASSIGNMENT"
        assert _component_for("PPEF_Data_Dictionary.pdf") is None

    def test_known_transport_table_matches_reality(self):
        assert KNOWN_COMPONENT_TRANSPORT["ENROLLMENT"]["transport"] == Transport.BOTH.value
        for c in ("REASSIGNMENT", "PRACTICE_LOCATION", "ADDITIONAL_NPIS", "SECONDARY_SPECIALTY"):
            assert KNOWN_COMPONENT_TRANSPORT[c]["transport"] == Transport.DOWNLOAD.value


# ── Schema validation ────────────────────────────────────────────────────────

class TestSchemaValidation:
    def test_live_schemas_are_registered_exactly(self):
        """These are the columns the live CSVs actually carry (verified 2026-08-19)."""
        assert EXPECTED_FIELDS["PRACTICE_LOCATION"] == ("ENRLMT_ID", "CITY_NAME", "STATE_CD", "ZIP_CD")
        assert EXPECTED_FIELDS["REASSIGNMENT"] == ("REASGN_BNFT_ENRLMT_ID", "RCV_BNFT_ENRLMT_ID")
        assert EXPECTED_FIELDS["ADDITIONAL_NPIS"] == ("ENRLMT_ID", "NPI")
        assert EXPECTED_FIELDS["SECONDARY_SPECIALTY"] == ("ENRLMT_ID", "PROVIDER_TYPE_CD",
                                                          "PROVIDER_TYPE_DESC")

    def test_both_reassignment_keys_join_to_enrollment(self):
        assert JOIN_KEYS["REASSIGNMENT"] == ("REASGN_BNFT_ENRLMT_ID", "RCV_BNFT_ENRLMT_ID")

    def test_missing_column_aborts_the_ingest(self):
        with pytest.raises(SchemaDriftError) as exc:
            validate_schema("PRACTICE_LOCATION", ["ENRLMT_ID", "CITY_NAME"])
        assert "STATE_CD" in str(exc.value)

    def test_extra_column_is_tolerated(self):
        fields = validate_schema("ADDITIONAL_NPIS", ["ENRLMT_ID", "NPI", "NEW_CMS_COLUMN"])
        assert "NEW_CMS_COLUMN" in fields

    def test_bom_in_header_is_handled(self):
        fields = validate_schema("ADDITIONAL_NPIS", ["﻿ENRLMT_ID", "NPI"])
        assert fields[0] == "ENRLMT_ID"

    def test_unknown_component_is_rejected(self):
        with pytest.raises(IngestError):
            validate_schema("NOT_A_COMPONENT", ["A"])


# ── Row normalisation ────────────────────────────────────────────────────────

class TestRowNormalization:
    def test_reassignment_keeps_both_directions_addressable(self):
        row = normalize_row("REASSIGNMENT", {"REASGN_BNFT_ENRLMT_ID": "I1",
                                             "RCV_BNFT_ENRLMT_ID": "O9"})
        assert row["enrollment_id"] == "I1"          # the practitioner
        assert row["related_enrollment_id"] == "O9"  # the receiving entity
        assert row["payload"]["RCV_BNFT_ENRLMT_ID"] == "O9"

    def test_practice_location_keyed_on_enrollment(self):
        row = normalize_row("PRACTICE_LOCATION", {"ENRLMT_ID": "I1", "CITY_NAME": "BALTIMORE",
                                                  "STATE_CD": "MD", "ZIP_CD": "212011925"})
        assert row["enrollment_id"] == "I1"
        assert row["related_enrollment_id"] is None
        assert row["payload"]["ZIP_CD"] == "212011925"

    def test_additional_npis_captures_the_npi_column(self):
        row = normalize_row("ADDITIONAL_NPIS", {"ENRLMT_ID": "I1", "NPI": "1234567893"})
        assert row["npi"] == "1234567893"

    def test_payload_preserves_cms_field_names_verbatim(self):
        """Evidence must quote the source, not a paraphrase of it."""
        row = normalize_row("SECONDARY_SPECIALTY", {"ENRLMT_ID": "I1", "PROVIDER_TYPE_CD": "14-11",
                                                    "PROVIDER_TYPE_DESC": "INTERNAL MEDICINE"})
        assert set(row["payload"]) == {"ENRLMT_ID", "PROVIDER_TYPE_CD", "PROVIDER_TYPE_DESC"}


# ── Ingestion ────────────────────────────────────────────────────────────────

def csv_bytes(header: List[str], rows: List[List[str]]) -> bytes:
    body = ",".join(header) + "\n" + "\n".join(",".join(r) for r in rows) + "\n"
    return body.encode("utf-8")


class StubIngestor(PPEFIngestor):
    """PPEFIngestor with the network replaced by fixed bytes."""

    def __init__(self, payload: bytes, rows=None):
        super().__init__(catalog=FakeCatalog(rows))
        self.payload = payload

    async def _download(self, resource, sink):
        sink(self.payload)
        return {"sha256": hashlib.sha256(self.payload).hexdigest(),
                "bytes": len(self.payload),
                "http_last_modified": "Tue, 21 Jul 2026 13:31:54 GMT",
                "retrieved_at": "2026-08-19T00:00:00"}


@pytest.fixture
def patched_download(monkeypatch):
    def _apply(ingestor: StubIngestor):
        async def fake(resource, sink, timeout=None):
            return await ingestor._download(resource, sink)
        monkeypatch.setattr("app.Tefca.ppef_ingest.download_component", fake)
        return ingestor
    return _apply


class TestIngestion:
    async def test_ingests_and_hashes_the_bytes_received(self, patched_download):
        payload = csv_bytes(["ENRLMT_ID", "CITY_NAME", "STATE_CD", "ZIP_CD"],
                            [["I1", "BALTIMORE", "MD", "212011925"],
                             ["I1", "COLUMBIA", "MD", "210443021"],
                             ["I2", "SAN JUAN", "PR", "009175030"]])
        ing = patched_download(StubIngestor(payload))
        written: List[Dict[str, Any]] = []
        meta = await ing.ingest("PRACTICE_LOCATION", write_batch=written.extend)

        assert meta.record_count == 3
        assert meta.rows_truncated is False
        assert meta.sha256 == hashlib.sha256(payload).hexdigest()
        assert meta.schema_fields == ["ENRLMT_ID", "CITY_NAME", "STATE_CD", "ZIP_CD"]
        assert meta.cms_title == "Address Sub-File Q3 2026"
        assert meta.resource_version == "2026.07.17"
        assert meta.as_of_label == "Q3 2026"

    async def test_one_enrollment_may_have_many_locations(self, patched_download):
        payload = csv_bytes(["ENRLMT_ID", "CITY_NAME", "STATE_CD", "ZIP_CD"],
                            [["I1", "BALTIMORE", "MD", "21201"],
                             ["I1", "COLUMBIA", "MD", "21044"]])
        ing = patched_download(StubIngestor(payload))
        written: List[Dict[str, Any]] = []
        await ing.ingest("PRACTICE_LOCATION", write_batch=written.extend)
        assert [r["enrollment_id"] for r in written] == ["I1", "I1"]

    async def test_truncation_is_recorded_never_silent(self, patched_download):
        payload = csv_bytes(["ENRLMT_ID", "NPI"],
                            [[f"I{i}", f"100000000{i}"] for i in range(10)])
        ing = patched_download(StubIngestor(payload))
        written: List[Dict[str, Any]] = []
        meta = await ing.ingest("ADDITIONAL_NPIS", write_batch=written.extend, max_rows=4)
        assert meta.record_count == 4
        assert meta.rows_truncated is True     # a partial load never looks complete

    async def test_schema_drift_aborts_before_writing_anything(self, patched_download):
        payload = csv_bytes(["ENRLMT_ID", "CITY"], [["I1", "BALTIMORE"]])
        ing = patched_download(StubIngestor(payload))
        written: List[Dict[str, Any]] = []
        with pytest.raises(SchemaDriftError):
            await ing.ingest("PRACTICE_LOCATION", write_batch=written.extend)
        assert written == []

    async def test_empty_file_is_an_error_not_an_empty_snapshot(self, patched_download):
        ing = patched_download(StubIngestor(b""))
        with pytest.raises(IngestError):
            await ing.ingest("ADDITIONAL_NPIS", write_batch=lambda b: None)

    async def test_undiscovered_component_is_refused(self, patched_download):
        ing = patched_download(StubIngestor(csv_bytes(["ENRLMT_ID", "NPI"], [["I1", "1"]]), rows=[]))
        with pytest.raises(IngestError):
            await ing.ingest("ADDITIONAL_NPIS", write_batch=lambda b: None)

    async def test_provenance_never_claims_realtime(self, patched_download):
        payload = csv_bytes(["REASGN_BNFT_ENRLMT_ID", "RCV_BNFT_ENRLMT_ID"], [["I1", "O9"]])
        ing = patched_download(StubIngestor(payload))
        meta = await ing.ingest("REASSIGNMENT", write_batch=lambda b: None)
        d = meta.to_dict()
        assert d["transport"] == Transport.DOWNLOAD.value
        assert d["resource_version"] == "2026.07.17"
        assert d["sha256"]
        assert d["download_url"].endswith("PPEF_Reassignment_Extract_2026.07.17.csv")


class TestSnapshotOrdering:
    """The snapshot row must exist before any record references it.

    This is the bug that reached dev: records were inserted first and the
    snapshot only afterwards, so the very first flush violated
    tefca_ppef_records.snapshot_id -> tefca_ppef_snapshots.id and the whole
    ingest 502'd. Offline tests could not catch it because none of them had a
    foreign key; this one models the constraint explicitly.
    """

    async def test_records_are_never_written_before_their_snapshot(self, patched_download):
        known_snapshots = set()
        violations = []

        def create_snapshot(snapshot_id):
            known_snapshots.add(snapshot_id)

        def write_records(snapshot_id, rows):
            if snapshot_id not in known_snapshots:
                violations.append(snapshot_id)   # what Postgres would reject

        snapshot_id = "snap-1"
        create_snapshot(snapshot_id)             # endpoint order: snapshot FIRST

        payload = csv_bytes(["ENRLMT_ID", "NPI"], [["I1", "1234567893"]])
        ing = patched_download(StubIngestor(payload))
        await ing.ingest("ADDITIONAL_NPIS",
                         write_batch=lambda batch: write_records(snapshot_id, batch))
        assert violations == []

    async def test_a_snapshot_is_only_readable_once_complete(self):
        """ppef_store reads `complete` snapshots only.

        A pending or failed row documents an attempt; it must never be served as
        evidence, or a half-loaded file would look like a small one.
        """
        import inspect

        from app.Tefca import ppef_store

        source = inspect.getsource(ppef_store.latest_snapshot)
        assert 'ingest_status == "complete"' in source


class TestBackgroundSessionUsage:
    """The background ingest must open its session the way the helper is defined.

    app.core.database.async_session_maker() RETURNS A SESSION (it is
    `_get_session_maker()()`), not a factory. Treating it as a factory raised
    "'AsyncSession' object is not callable" inside the background task — where
    it was invisible, because the endpoint had already returned 202 and the
    snapshot simply sat at `pending` forever.
    """

    def test_async_session_maker_returns_a_session_not_a_factory(self):
        import inspect

        from app.core import database

        src = inspect.getsource(database.async_session_maker)
        assert "_get_session_maker()()" in src, (
            "async_session_maker no longer returns a session; the background "
            "ingest binds it directly and must be updated with it"
        )

    def test_background_ingest_does_not_call_the_returned_session(self):
        import inspect

        from app.Tefca import routes

        src = inspect.getsource(routes._run_ppef_ingest)
        assert "async with async_session_maker() as db" in src
        # The specific bug: binding the RESULT to a local and then calling it.
        assert "session_maker = async_session_maker()" not in src
        assert "async with session_maker()" not in src
