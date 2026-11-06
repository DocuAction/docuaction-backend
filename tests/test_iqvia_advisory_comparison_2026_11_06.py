"""Advisory IQVIA comparison consumers (AFFIL presence + org address; HCP_ADDR
address): pure decision tables, then the batch over STAGED synthetic rows.

Invariants under test: read-only (no EntitySourceMatch row is ever written),
advisory determinations only, PENDING snapshots refused without the labelled
diagnostic switch, presence and address reported as two separate facts.
"""
from __future__ import annotations

import csv
import uuid

import pytest
from sqlalchemy import func, select

from app.tefca_registry.rce import iqvia_advisory_comparison as advc
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as smod
from app.tefca_registry.rce.iqvia_affiliation_consumer import SnapshotNotEligible
from rce_traceability_support import (  # noqa: F401
    NPI_REGISTERED, NPI_VALID_OTHER, SYN, rolled_back_db, seed_entity)

pytestmark = pytest.mark.regression

NPI_THIRD = "1043566623"   # Luhn-valid synthetic (same constant the org-lookup suite uses)


# ── pure: normalization ───────────────────────────────────────────────────────

def test_zip_normalization_restores_lost_leading_zeros_and_trims_plus4():
    assert advc.normalize_zip5("02101-1234") == "02101"
    assert advc.normalize_zip5("2101") == "02101"       # spreadsheet-trimmed zero
    assert advc.normalize_zip5("021011234") == "02101"  # ZIP+4 unhyphenated
    assert advc.normalize_zip5("") is None
    assert advc.normalize_zip5(None) is None
    assert advc.normalize_zip5("ab") is None
    assert advc.normalize_zip5("12") is None            # too short to pad honestly


def test_state_and_city_normalization():
    assert advc.normalize_state(" ma ") == "MA"
    assert advc.normalize_state("Mass") is None
    assert advc.normalize_state("1A") is None
    assert advc.normalize_city("  Boston,  MA.  ") == "boston ma"
    assert advc.normalize_city("") is None


# ── pure: address decision table ──────────────────────────────────────────────

ENT = {"address": "1 Synthetic Way", "city": "Testville", "state": "MA", "zip": "02101"}


def test_address_no_iqvia_rows_is_not_compared():
    out = advc.address_outcome(ENT, [])
    assert out["outcome"] == advc.NOT_COMPARED and out["reason"] == "NO_IQVIA_ROWS"


def test_address_rows_with_blank_fields_is_no_address_data():
    out = advc.address_outcome(ENT, [{"city": "", "state": None, "zip": ""}])
    assert out["outcome"] == advc.NO_ADDRESS_DATA


def test_address_unusable_delivered_address_is_not_compared_not_differs():
    out = advc.address_outcome({"city": None, "state": None, "zip": None},
                               [{"city": "Testville", "state": "MA", "zip": "02101"}])
    assert out["outcome"] == advc.NOT_COMPARED
    assert out["reason"] == "DELIVERED_ADDRESS_UNUSABLE"


def test_address_zip_match_is_consistent_with_counts():
    rows = [{"city": "Elsewhere", "state": "TX", "zip": "02101"},
            {"city": "Nowhere", "state": "AK", "zip": "99999"}]
    out = advc.address_outcome(ENT, rows)
    assert out["outcome"] == advc.ADDRESS_CONSISTENT
    assert out["agreeing"] == 1 and out["iqvia_addresses"] == 2


def test_address_city_state_match_without_zip_is_consistent():
    out = advc.address_outcome(ENT, [{"city": "TESTVILLE", "state": "ma", "zip": "77777"}])
    assert out["outcome"] == advc.ADDRESS_CONSISTENT


def test_address_rows_none_agreeing_is_differs():
    out = advc.address_outcome(ENT, [{"city": "Elsewhere", "state": "TX", "zip": "99999"}])
    assert out["outcome"] == advc.ADDRESS_DIFFERS and out["agreeing"] == 0


# ── pure: presence decision table ─────────────────────────────────────────────

def test_presence_outcomes():
    assert advc.presence_outcome([], [])["outcome"] == advc.NO_IQVIA_DATA
    assert advc.presence_outcome(["K1"], [])["outcome"] == advc.SUPPORTS
    conflict = advc.presence_outcome(["K1"], ["K2"])
    assert conflict["outcome"] == advc.DIFFERS
    assert "ORG_NPI_AND_ORG_CCN_IDENTIFY_DIFFERENT_ORGANISATIONS" in conflict["notes"]
    shared = advc.presence_outcome(["K1", "K2"], [])
    assert shared["outcome"] == advc.SUPPORTS   # duplication is ambiguity, not contradiction
    assert "ORG_NPI_SHARED_BY_MULTIPLE_HCO_KEYS" in shared["notes"]
    agree = advc.presence_outcome(["K1"], ["K1"])
    assert agree["outcome"] == advc.SUPPORTS and agree["notes"] == []


# ── staged-data batches (real database, synthetic rows, rolled back) ──────────

AFFIL_HEADER = ["HCP_HCE_ID", "HCO_HCE_ID", "AFFL_TYP_ID", "TITL_TYP_ID",
                "ORG_NPI", "ORG_CCN_ID", "NPI",
                "ADDR_LN_1_TXT", "CITY_NM", "ST_CD", "ZIP5_CD"]
HCP_HEADER = ["HCP_HCE_ID", "ADDR_ID", "NPI", "FRST_NM", "LAST_NM",
              "ADDR_LN_1_TXT", "CITY_NM", "ST_CD", "ZIP5_CD"]


def _write(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)
    return path


async def _stage_affil(db, tmp_path):
    rows = [
        # O1: NPI_REGISTERED, address agrees with seed_entity's defaults (zip 02101)
        [f"{SYN}-P1", f"{SYN}-O1", "A1", "", NPI_REGISTERED, "", "", "1 Any St", "Testville", "MA", "02101"],
        [f"{SYN}-P2", f"{SYN}-O1", "A2", "", NPI_REGISTERED, "", "", "1 Any St", "Testville", "MA", "02101"],
        # O2: NPI_VALID_OTHER, address DIFFERS everywhere
        [f"{SYN}-P3", f"{SYN}-O2", "A1", "", NPI_VALID_OTHER, "", "", "9 Far Rd", "Elsewhere", "TX", "99999"],
    ]
    f = _write(tmp_path / "affil_cmp.csv", AFFIL_HEADER, rows)
    summary = await ii.import_affiliation_csv(db, file_path=f,
                                              label=f"{SYN}-advisory", created_by=SYN)
    assert summary.rows_rejected == 0 and summary.completed
    return summary.snapshot_id


async def _no_match_rows(db) -> int:
    return (await db.execute(
        select(func.count()).select_from(smod.EntitySourceMatch))).scalar_one()


class TestAffiliationBatch:
    async def test_pending_snapshot_refused_without_diagnostic_switch(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        eid = await seed_entity(db, oid=f"{SYN}-E1", name=f"{SYN} Org", npi=NPI_REGISTERED)
        sid = await _stage_affil(db, tmp_path)
        with pytest.raises(SnapshotNotEligible):
            await advc.advisory_comparison_batch(db, snapshot_id=sid, entity_ids=[eid])

    async def test_batch_outcomes_are_per_entity_and_read_only(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        e_match = await seed_entity(db, oid=f"{SYN}-E1", name=f"{SYN} Match", npi=NPI_REGISTERED)
        e_diff = await seed_entity(db, oid=f"{SYN}-E2", name=f"{SYN} Diff", npi=NPI_VALID_OTHER)
        e_absent = await seed_entity(db, oid=f"{SYN}-E3", name=f"{SYN} Absent", npi=NPI_THIRD)
        sid = await _stage_affil(db, tmp_path)
        before = await _no_match_rows(db)
        out = await advc.advisory_comparison_batch(
            db, snapshot_id=sid, entity_ids=[e_match, e_diff, e_absent],
            diagnostic_allow_pending=True)
        by_id = {r["entity_id"]: r for r in out["results"]}
        m, d, a = by_id[str(e_match)], by_id[str(e_diff)], by_id[str(e_absent)]
        assert m["presence"]["outcome"] == advc.SUPPORTS
        assert m["address"]["outcome"] == advc.ADDRESS_CONSISTENT
        assert d["presence"]["outcome"] == advc.SUPPORTS
        assert d["address"]["outcome"] == advc.ADDRESS_DIFFERS
        assert a["presence"]["outcome"] == advc.NO_IQVIA_DATA
        assert a["address"]["outcome"] == advc.NOT_COMPARED
        # two separate facts, advisory invariants, counts, diagnostic label
        assert out["summary"]["presence"][advc.SUPPORTS] == 2
        assert out["summary"]["address"][advc.ADDRESS_DIFFERS] == 1
        assert out["determination"] == "AUTOMATED_CANDIDATE_NOT_CONFIRMED"
        assert out["requires_analyst_review"] is True
        assert out["affects_verification"] is False
        assert out["diagnostic_only"].startswith("NOT_ELIGIBLE")
        # identifiers are masked, never raw
        assert all("***" in v for v in m["identifiers"]["npi"])
        # READ-ONLY: no EntitySourceMatch row appeared
        assert await _no_match_rows(db) == before

    async def test_unknown_entity_is_reported_not_skipped(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        sid = await _stage_affil(db, tmp_path)
        ghost = uuid.uuid4()
        out = await advc.advisory_comparison_batch(
            db, snapshot_id=sid, entity_ids=[ghost], diagnostic_allow_pending=True)
        assert out["results"][0] == {"entity_id": str(ghost), "error": "NO_SUCH_ENTITY"}
        assert out["summary"]["presence"]["NO_SUCH_ENTITY"] == 1

    async def test_batch_cap_and_empty_input_are_refused(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        sid = await _stage_affil(db, tmp_path)
        with pytest.raises(ValueError):
            await advc.advisory_comparison_batch(db, snapshot_id=sid, entity_ids=[])
        with pytest.raises(ValueError):
            await advc.advisory_comparison_batch(
                db, snapshot_id=sid,
                entity_ids=[uuid.uuid4() for _ in range(advc.MAX_BATCH + 1)])

    async def test_hco_snapshot_is_not_served_by_this_consumer(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = _write(tmp_path / "hco.csv",
                   ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"],
                   [[f"{SYN}-H1", NPI_REGISTERED, "", f"{SYN} HCO", "02101"]])
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-hco", created_by=SYN)
        with pytest.raises(SnapshotNotEligible):
            await advc.advisory_comparison_batch(
                db, snapshot_id=summary.snapshot_id, entity_ids=[uuid.uuid4()],
                diagnostic_allow_pending=True)


class TestHcpAddressBatch:
    async def test_practitioner_address_outcomes(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        e_match = await seed_entity(db, oid=f"{SYN}-E1", name=f"{SYN} P-Match", npi=NPI_REGISTERED)
        e_absent = await seed_entity(db, oid=f"{SYN}-E2", name=f"{SYN} P-Absent", npi=NPI_THIRD)
        rows = [
            [f"{SYN}-HCP1", "A1", NPI_REGISTERED, "Jo", "Syn", "1 Any St", "Testville", "MA", "02101"],
            [f"{SYN}-HCP1", "A2", NPI_REGISTERED, "Jo", "Syn", "9 Far Rd", "Elsewhere", "TX", "99999"],
        ]
        f = _write(tmp_path / "hcp_cmp.csv", HCP_HEADER, rows)
        summary = await ii.import_hcp_csv(db, file_path=f, label=f"{SYN}-hcp", created_by=SYN)
        assert summary.rows_rejected == 0
        out = await advc.advisory_comparison_batch(
            db, snapshot_id=summary.snapshot_id, entity_ids=[e_match, e_absent],
            diagnostic_allow_pending=True)
        by_id = {r["entity_id"]: r for r in out["results"]}
        m, a = by_id[str(e_match)], by_id[str(e_absent)]
        # one of the practitioner's two addresses agrees: consistent, with counts
        assert m["presence"]["outcome"] == advc.SUPPORTS
        assert m["address"]["outcome"] == advc.ADDRESS_CONSISTENT
        assert m["address"]["agreeing"] == 1 and m["address"]["iqvia_addresses"] == 2
        # an NPI absent from the extract is a fact about the extract, not the record
        assert a["presence"]["outcome"] == advc.NO_IQVIA_DATA
        assert a["address"]["outcome"] == advc.NOT_COMPARED
        assert out["source_system"] == "IQVIA_HCP"


# ── capability wiring (pure; no HTTP) ─────────────────────────────────────────

def test_matching_capability_reports_advisory_sub_capability():
    from types import SimpleNamespace

    from app.tefca_registry.rce import snapshot_models as sm
    from app.tefca_registry.rce.iqvia_routes import _matching_capability

    pending = SimpleNamespace(status=sm.SNAPSHOT_PENDING, record_count=5)
    cap = _matching_capability(sm.SOURCE_IQVIA_AFFILIATION, pending)
    assert cap["supported"] is False                      # /match stays HCO-only
    adv = cap["advisory_comparison"]
    assert adv["supported"] is True and adv["snapshot_approved"] is False
    assert adv["executable_now"] is False
    approved = SimpleNamespace(status=sm.SNAPSHOT_APPROVED, record_count=5)
    adv2 = _matching_capability(sm.SOURCE_IQVIA_HCP, approved)["advisory_comparison"]
    assert adv2["snapshot_approved"] is True
    assert adv2["flag"] == advc.ADDR_FLAG
    # flags default off, so still not executable — consumption is a switch, not a deploy
    assert adv2["executable_now"] is False
    hco = _matching_capability(sm.SOURCE_IQVIA_HCO, approved)
    assert hco["supported"] is True
    assert hco["advisory_comparison"]["supported"] is False
    assert hco["advisory_comparison"]["code"] == "USE_MATCH_ROUTE"
