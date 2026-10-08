"""Organisation-first lookup over STAGED affiliation rows (synthetic data only, real database).

The snapshot stays PENDING; the diagnostic switch is the only way to read it, and it labels the result.
"""
from __future__ import annotations

import csv

import pytest

from app.tefca_registry.rce import iqvia_affiliation_consumer as ac
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import NPI_REGISTERED, NPI_VALID_OTHER, SYN, rolled_back_db, seed_entity  # noqa: F401

pytestmark = pytest.mark.regression

HEADER = ["HCP_HCE_ID", "HCO_HCE_ID", "AFFL_TYP_ID", "TITL_TYP_ID", "ORG_NPI", "ORG_CCN_ID", "NPI"]
NPI_SHARED = "1043566623"      # Luhn-valid synthetic
NPI_ABSENT = "1003000126"      # Luhn-valid synthetic, in no staged row and no registry entity


def write(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(HEADER)
        w.writerows(rows)
    return path


async def stage(db, tmp_path):
    rows = []
    # O1: registered NPI + CCN; 5 relationships (4 provider, 1 contact) from distinct HCPs
    for i in range(4):
        rows.append([f"{SYN}-P{i}", f"{SYN}-O1", f"A{i % 2}", "", NPI_REGISTERED, "010001", ""])
    rows.append([f"{SYN}-P9", f"{SYN}-O1", "", "T9", NPI_REGISTERED, "010001", ""])
    # same HCP-HCO pair under a second affiliation type: two relationships, one organisation
    rows.append([f"{SYN}-P0", f"{SYN}-O1", "A9", "", NPI_REGISTERED, "010001", ""])
    # O2: CCN only, leading zero lost in the source (5 digits)
    rows.append([f"{SYN}-P5", f"{SYN}-O2", "A1", "", "", "20001", ""])
    # O3: one HCO key carrying TWO different ORG_NPI values
    rows.append([f"{SYN}-P6", f"{SYN}-O3", "A1", "", NPI_VALID_OTHER, "", ""])
    rows.append([f"{SYN}-P7", f"{SYN}-O3", "A1", "", NPI_SHARED, "", ""])
    # O4 and O5 share one ORG_NPI (two HCO keys)
    rows.append([f"{SYN}-P8", f"{SYN}-O4", "A1", "", NPI_SHARED, "", ""])
    rows.append([f"{SYN}-P8", f"{SYN}-O5", "A1", "", NPI_SHARED, "", ""])
    f = write(tmp_path / "affil_org.csv", rows)
    summary = await ii.import_affiliation_csv(db, file_path=f, label=f"{SYN}-org-first", created_by=SYN)
    assert summary.rows_rejected == 0 and summary.completed
    return summary.snapshot_id


class TestOrganisationFirst:
    async def test_pending_snapshot_is_refused_without_the_diagnostic_switch(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        with pytest.raises(ac.SnapshotNotEligible):
            await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_npi=NPI_REGISTERED)

    async def test_positive_npi_lookup_preserves_every_relationship_and_labels_diagnostic(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        await seed_entity(db, oid=f"{SYN}-E1", name=f"{SYN} Registered Org", npi=NPI_REGISTERED)
        sid = await stage(db, tmp_path)
        out = await ac.organisation_relationships(db, snapshot_id=sid, org_npi=NPI_REGISTERED,
                                                  diagnostic_allow_pending=True)
        assert out["found_in_extract"] is True
        assert [o["hco_record_key"] for o in out["organisations"]] == [f"{SYN}-O1"]
        assert out["total_relationships"] == 6 and out["returned"] == 6 and out["truncated"] is False
        kinds = sorted(r["kind"] for r in out["relationships"])
        assert kinds == ["CONTACT", "PROVIDER", "PROVIDER", "PROVIDER", "PROVIDER", "PROVIDER"]
        # one HCP appears twice (two affiliation types): rows are relationships, not identities
        assert out["organisations"][0]["distinct_hcp_keys"] == 5 and out["organisations"][0]["relationship_rows"] == 6
        assert out["conflicts"] == []
        assert out["registry_candidate"]["outcome"] == ac.ORG_CANDIDATE
        assert out["registry_candidate"]["determination"] == "AUTOMATED_CANDIDATE_NOT_CONFIRMED"
        assert out["requires_analyst_review"] is True and out["affects_verification"] is False
        assert out["provenance"]["snapshot_id"] == str(sid) and out["provenance"]["status"] == "PENDING"
        assert out["diagnostic_only"].startswith("NOT_ELIGIBLE")

    async def test_ccn_lookup_restores_the_lost_leading_zero(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        out = await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_ccn="20001",
                                                  diagnostic_allow_pending=True)
        # normalized to 020001, which is NOT what the source holds ("20001"): the lookup tries both spellings
        assert [o["hco_record_key"] for o in out["organisations"]] == [f"{SYN}-O2"]
        assert "CCN_LEADING_ZERO_RESTORED" in out["registry_candidate"]["notes"]

    async def test_absent_identifier_is_not_found_in_extract_not_clear(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        out = await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_npi=NPI_ABSENT,
                                                  diagnostic_allow_pending=True)
        assert out["found_in_extract"] is False and out["relationships"] == [] and out["total_relationships"] == 0
        assert out["registry_candidate"]["outcome"] == ac.ORG_NOT_IN_REGISTRY

    async def test_no_usable_identifier_is_reported_not_guessed(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        out = await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_npi="1234567890",
                                                  diagnostic_allow_pending=True)
        assert out["found_in_extract"] is False and "NO_USABLE_IDENTIFIER" in out["notes"]
        assert "NPI_FAILED_VALIDATION" in out["notes"]

    async def test_conflict_npi_and_ccn_identify_different_organisations(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        out = await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_npi=NPI_REGISTERED,
                                                  org_ccn="20001", diagnostic_allow_pending=True)
        assert any(x["code"] == ac.CONFLICT_NPI_CCN_DIFFERENT_ORGS for x in out["conflicts"])
        assert sorted(o["hco_record_key"] for o in out["organisations"]) == [f"{SYN}-O1", f"{SYN}-O2"]

    async def test_one_hco_key_with_two_org_npis_and_a_shared_npi_are_both_flagged(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        out = await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_npi=NPI_SHARED,
                                                  diagnostic_allow_pending=True)
        codes = [x["code"] for x in out["conflicts"]]
        assert ac.CONFLICT_IDENTIFIER_SHARED in codes          # O3, O4, O5 all carry it
        assert ac.CONFLICT_HCO_MULTIPLE_NPI in codes           # O3 also carries a second ORG_NPI
        assert {o["hco_record_key"] for o in out["organisations"]} == {f"{SYN}-O3", f"{SYN}-O4", f"{SYN}-O5"}

    async def test_pagination_is_explicit_complete_and_duplicate_free(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        seen, cursor, pages = [], None, 0
        while True:
            out = await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_npi=NPI_REGISTERED,
                                                      limit=4, cursor=cursor, diagnostic_allow_pending=True)
            pages += 1
            seen += [(r["hco_record_key"], r["hcp_record_key"], r["affiliation_type"]) for r in out["relationships"]]
            assert out["total_relationships"] == 6 and out["page_limit"] == 4
            if not out["truncated"]:
                assert out["next_cursor"] is None
                break
            assert out["returned"] == 4 and out["next_cursor"] is not None
            cursor = out["next_cursor"]
        assert pages == 2 and len(seen) == 6 and len(set(seen)) == 6

    async def test_page_size_is_capped_and_says_so(self, rolled_back_db, tmp_path):
        sid = await stage(rolled_back_db, tmp_path)
        out = await ac.organisation_relationships(rolled_back_db, snapshot_id=sid, org_npi=NPI_REGISTERED,
                                                  limit=10_000, diagnostic_allow_pending=True)
        assert out["page_limit"] == ac.PAGE_MAX and f"LIMIT_CAPPED_AT_{ac.PAGE_MAX}" in out["notes"]

    async def test_snapshot_status_is_unchanged_and_nothing_is_written(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        sid = await stage(db, tmp_path)
        await ac.organisation_relationships(db, snapshot_id=sid, org_npi=NPI_REGISTERED, diagnostic_allow_pending=True)
        snap = await db.get(sm.SourceSnapshot, sid)
        assert snap.status == sm.SNAPSHOT_PENDING
