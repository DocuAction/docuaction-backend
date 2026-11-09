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
    """ZIP normalization restores lost leading zeros and trims ZIP+4 to five
    digits."""
    assert advc.normalize_zip5("02101-1234") == "02101"
    assert advc.normalize_zip5("2101") == "02101"       # spreadsheet-trimmed zero
    assert advc.normalize_zip5("021011234") == "02101"  # ZIP+4 unhyphenated
    assert advc.normalize_zip5("") is None
    assert advc.normalize_zip5(None) is None
    assert advc.normalize_zip5("ab") is None
    assert advc.normalize_zip5("12") is None            # too short to pad honestly


def test_state_and_city_normalization():
    """State and city normalization used by the address comparison."""
    assert advc.normalize_state(" ma ") == "MA"
    assert advc.normalize_state("Mass") is None
    assert advc.normalize_state("1A") is None
    assert advc.normalize_city("  Boston,  MA.  ") == "boston ma"
    assert advc.normalize_city("") is None


# ── pure: address decision table ──────────────────────────────────────────────

ENT = {"address": "1 Synthetic Way", "city": "Testville", "state": "MA", "zip": "02101"}


def test_address_no_iqvia_rows_is_not_compared():
    """No IQVIA rows for the entity -> NOT_COMPARED, never DIFFERS."""
    out = advc.address_outcome(ENT, [])
    assert out["outcome"] == advc.NOT_COMPARED and out["reason"] == "NO_IQVIA_ROWS"


def test_address_rows_with_blank_fields_is_no_address_data():
    """Rows present but every address field blank -> NO_ADDRESS_DATA."""
    out = advc.address_outcome(ENT, [{"city": "", "state": None, "zip": ""}])
    assert out["outcome"] == advc.NO_ADDRESS_DATA


def test_address_unusable_delivered_address_is_not_compared_not_differs():
    """An unusable delivered address -> NOT_COMPARED, never DIFFERS."""
    out = advc.address_outcome({"city": None, "state": None, "zip": None},
                               [{"city": "Testville", "state": "MA", "zip": "02101"}])
    assert out["outcome"] == advc.NOT_COMPARED
    assert out["reason"] == "DELIVERED_ADDRESS_UNUSABLE"


def test_address_zip_match_is_consistent_with_counts():
    """A ZIP agreement outcome is consistent with the agreeing/total counts."""
    rows = [{"city": "Elsewhere", "state": "TX", "zip": "02101"},
            {"city": "Nowhere", "state": "AK", "zip": "99999"}]
    out = advc.address_outcome(ENT, rows)
    assert out["outcome"] == advc.ADDRESS_CONSISTENT
    assert out["agreeing"] == 1 and out["iqvia_addresses"] == 2


def test_address_city_state_match_without_zip_is_consistent():
    """City/state agreement without a usable ZIP still counts as agreement."""
    out = advc.address_outcome(ENT, [{"city": "TESTVILLE", "state": "ma", "zip": "77777"}])
    assert out["outcome"] == advc.ADDRESS_CONSISTENT


def test_address_rows_none_agreeing_is_differs():
    """Comparable rows with none agreeing -> DIFFERS."""
    out = advc.address_outcome(ENT, [{"city": "Elsewhere", "state": "TX", "zip": "99999"}])
    assert out["outcome"] == advc.ADDRESS_DIFFERS and out["agreeing"] == 0


# ── pure: presence decision table ─────────────────────────────────────────────

def test_presence_outcomes():
    """Presence-axis outcomes per identifier evidence (SUPPORTS / DIFFERS /
    NOT_COMPARED)."""
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
    """Write a CSV with CRLF line endings, as the real import expects."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)
    return path


async def _stage_affil(db, tmp_path):
    """Import a tiny synthetic affiliation CSV; returns its snapshot id."""
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
    """Count persisted EntitySourceMatch rows (advisory comparison must write
    none)."""
    return (await db.execute(
        select(func.count()).select_from(smod.EntitySourceMatch))).scalar_one()


class TestAffiliationBatch:
    async def test_pending_snapshot_refused_without_diagnostic_switch(self, rolled_back_db, tmp_path):
        """A PENDING snapshot is refused unless the diagnostic switch is set."""
        db = rolled_back_db
        eid = await seed_entity(db, oid=f"{SYN}-E1", name=f"{SYN} Org", npi=NPI_REGISTERED)
        sid = await _stage_affil(db, tmp_path)
        with pytest.raises(SnapshotNotEligible):
            await advc.advisory_comparison_batch(db, snapshot_id=sid, entity_ids=[eid])

    async def test_batch_outcomes_are_per_entity_and_read_only(self, rolled_back_db, tmp_path):
        """Batch comparison returns per-entity outcomes and persists nothing."""
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
        """An unknown entity id is reported in the results, never silently
        dropped."""
        db = rolled_back_db
        sid = await _stage_affil(db, tmp_path)
        ghost = uuid.uuid4()
        out = await advc.advisory_comparison_batch(
            db, snapshot_id=sid, entity_ids=[ghost], diagnostic_allow_pending=True)
        assert out["results"][0] == {"entity_id": str(ghost), "error": "NO_SUCH_ENTITY"}
        assert out["summary"]["presence"]["NO_SUCH_ENTITY"] == 1

    async def test_batch_cap_and_empty_input_are_refused(self, rolled_back_db, tmp_path):
        """Empty input and over-cap batches are refused."""
        db = rolled_back_db
        sid = await _stage_affil(db, tmp_path)
        with pytest.raises(ValueError):
            await advc.advisory_comparison_batch(db, snapshot_id=sid, entity_ids=[])
        with pytest.raises(ValueError):
            await advc.advisory_comparison_batch(
                db, snapshot_id=sid,
                entity_ids=[uuid.uuid4() for _ in range(advc.MAX_BATCH + 1)])

    async def test_hco_snapshot_is_not_served_by_this_consumer(self, rolled_back_db, tmp_path):
        """HCO snapshots belong to the /match route, not the advisory
        consumer."""
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
        """Practitioner (HCP/ADDR) address-comparison outcomes."""
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
    """The capability dict reports the advisory_comparison sub-capability."""
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


@pytest.mark.parametrize("source", ["affiliation", "hcp"])
async def test_all_distinct_addresses_are_compared_beyond_fifty(
        rolled_back_db, tmp_path, source):
    """A later matching address must not disappear behind a fifty-row cap."""
    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-all-addresses",
                            name=f"{SYN} Address Coverage", npi=NPI_REGISTERED)
    rows = []
    for i in range(51):
        city, state, zipcode = (
            ("Testville", "MA", "02101") if i == 50
            else (f"Elsewhere {i}", "TX", f"{75000 + i:05d}"))
        if source == "affiliation":
            rows.append([f"{SYN}-P{i}", f"{SYN}-all-O", "A1", "",
                         NPI_REGISTERED, "", "", "1 Synthetic Way",
                         city, state, zipcode])
        else:
            rows.append([f"{SYN}-all-P", f"A{i}", NPI_REGISTERED,
                         "Jo", "Synthetic", "1 Synthetic Way",
                         city, state, zipcode])
    header = AFFIL_HEADER if source == "affiliation" else HCP_HEADER
    file_path = _write(tmp_path / f"{source}_51.csv", header, rows)
    importer = (ii.import_affiliation_csv if source == "affiliation"
                else ii.import_hcp_csv)
    summary = await importer(db, file_path=file_path,
                             label=f"{SYN}-all-{source}", created_by=SYN)
    assert summary.completed and summary.rows_rejected == 0
    before = await _no_match_rows(db)
    out = await advc.advisory_comparison_batch(
        db, snapshot_id=summary.snapshot_id, entity_ids=[eid],
        diagnostic_allow_pending=True)
    address = out["results"][0]["address"]
    assert address["outcome"] == advc.ADDRESS_CONSISTENT
    assert address["agreeing"] == 1
    assert address["iqvia_addresses"] == 51
    assert await _no_match_rows(db) == before


async def test_retired_identifiers_are_ignored_and_null_status_remains_current(
        rolled_back_db, tmp_path):
    """Old identifiers cannot create false affiliations or CCN conflicts."""
    from sqlalchemy import update
    from app.tefca_registry import models as reg

    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-identifier-status",
                            name=f"{SYN} Current Identity", npi=NPI_REGISTERED)
    file_path = _write(tmp_path / "identifier_status.csv", AFFIL_HEADER, [
        [f"{SYN}-current-P", f"{SYN}-current-O", "A1", "",
         NPI_REGISTERED, "111111", "", "1 Synthetic Way", "Testville", "MA", "02101"],
        [f"{SYN}-retired-P", f"{SYN}-retired-O", "A1", "",
         NPI_VALID_OTHER, "222222", "", "9 Far Rd", "Elsewhere", "TX", "99999"],
    ])
    staged = await ii.import_affiliation_csv(
        db, file_path=file_path, label=f"{SYN}-identifier-status", created_by=SYN)
    db.add(reg.TefcaEntityIdentifier(
        entity_id=eid, identifier_type="npi", identifier_value=NPI_VALID_OTHER,
        identifier_status="superseded"))
    old_ccn = reg.TefcaEntityIdentifier(
        entity_id=eid, identifier_type="ccn", identifier_value="222222",
        identifier_status="retired")
    db.add(old_ccn)
    await db.flush()
    await db.execute(update(reg.TefcaEntityIdentifier).where(
        reg.TefcaEntityIdentifier.entity_id == eid,
        reg.TefcaEntityIdentifier.identifier_value == NPI_REGISTERED
    ).values(identifier_status=None))

    async def compare():
        """Run the advisory batch for the one seeded entity."""
        return await advc.advisory_comparison_batch(
            db, snapshot_id=staged.snapshot_id, entity_ids=[eid],
            diagnostic_allow_pending=True)

    current = (await compare())["results"][0]["presence"]
    assert current["outcome"] == advc.SUPPORTS
    assert current["hco_keys"] == [f"{SYN}-current-O"]
    assert current["notes"] == []
    old_ccn.identifier_status = "active"
    await db.flush()
    conflict = (await compare())["results"][0]["presence"]
    assert conflict["outcome"] == advc.DIFFERS
    assert "ORG_NPI_AND_ORG_CCN_IDENTIFY_DIFFERENT_ORGANISATIONS" in conflict["notes"]


async def test_identifier_batch_reads_once_and_keeps_empty_entities(
        rolled_back_db):
    """The loader handles a multi-entity page using one database execute."""
    db = rolled_back_db
    eid = await seed_entity(db, oid=f"{SYN}-bulk-identifiers",
                            name=f"{SYN} Bulk Identity", npi=NPI_REGISTERED)
    ghost = uuid.uuid4()

    class CountedDB:
        calls = 0

        async def execute(self, statement):
            """Count executes while delegating to the real session."""
            self.calls += 1
            return await db.execute(statement)

    counted = CountedDB()
    result = await advc._entity_identifiers_batch(counted, [eid, ghost])
    assert counted.calls == 1
    assert result[eid]["npi"] == [NPI_REGISTERED]
    assert result[ghost] == {"npi": [], "ccn": []}


def test_address_usable_but_not_comparable_rows_are_not_compared():
    """Review finding 2026-11-08: a row with address data on a DIFFERENT axis
    than the delivered record (city/state only vs delivered ZIP-only, or
    ZIP-only vs delivered city/state-only) proves nothing either way — it is
    NOT_COMPARED/NO_COMPARABLE_FIELDS, never ADDRESS_DIFFERS."""
    # Delivered has only a ZIP; IQVIA rows carry only city/state.
    out = advc.address_outcome(
        {"zip": "02114", "city": None, "state": None},
        [{"city": "Testville", "state": "MA", "zip": ""}])
    assert out["outcome"] == advc.NOT_COMPARED
    assert out["reason"] == "NO_COMPARABLE_FIELDS"
    # Mixed: one non-comparable row plus one comparable disagreeing row —
    # only the comparable row counts, and the existing differs semantics hold.
    out = advc.address_outcome(
        {"zip": "02114", "city": None, "state": None},
        [{"city": "Testville", "state": "MA", "zip": ""},
         {"city": "", "state": "", "zip": "99999"}])
    assert out["outcome"] == advc.ADDRESS_DIFFERS
    assert out["iqvia_addresses"] == 1 and out["agreeing"] == 0
    # And a comparable agreeing row still wins exactly as before.
    out = advc.address_outcome(
        {"zip": "02114", "city": None, "state": None},
        [{"city": "Testville", "state": "MA", "zip": ""},
         {"city": "", "state": "", "zip": "02114"}])
    assert out["outcome"] == advc.ADDRESS_CONSISTENT
    assert out["iqvia_addresses"] == 1 and out["agreeing"] == 1
