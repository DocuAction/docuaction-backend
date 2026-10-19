"""`iqvia_batch_comparison.compare_entities_to_approved_iqvia_hco` -- entirely
synthetic data (same convention as `test_iqvia_import.py`: no real IQVIA
content exists in this repository or test file).

Covers all four outcome buckets the module reasons about: MATCHED,
NO_IDENTIFIER, NOT_FOUND, AMBIGUOUS -- plus the access gate
(`ENABLE_IQVIA_SOURCES` off, or no approved snapshot) raising rather than
silently returning zero. Writes nothing: every test asserts zero
`EntitySourceMatch` rows exist afterward.
"""
from __future__ import annotations

import csv
import uuid

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import iqvia_batch_comparison as bc
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import source_matching as sx
from rce_traceability_support import NPI_REGISTERED, NPI_VALID_OTHER, SYN, rolled_back_db, seed_entity  # noqa: F401

pytestmark = pytest.mark.regression

HCO_HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]


class _User:
    def __init__(self, role, email):
        self.role, self.email, self.id = role, email, None


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(HCO_HEADER)
        for r in rows:
            w.writerow(r)
    return path


async def _approve_hco_snapshot(db, tmp_path, label, rows):
    f = write_csv(tmp_path / f"{label}.csv", rows)
    imported = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-{label}", created_by=SYN)
    qalead = _User("qalead", "qa@example.test")
    return await sx.approve_snapshot(db, imported.snapshot_id, user=qalead, approval_ref=f"{SYN}-{label}")


def _add_identifier(db, entity_id, id_type, value, system_uri=None):
    db.add(reg.TefcaEntityIdentifier(
        id=uuid.uuid4(), entity_id=entity_id, identifier_type=id_type,
        identifier_value=value, system_uri=system_uri,
        is_primary=False, identifier_status="active"))


async def _no_matches_written(db, snapshot_id):
    rows = (await db.execute(select(sm.EntitySourceMatch).where(
        sm.EntitySourceMatch.source_snapshot_id == snapshot_id))).scalars().all()
    assert rows == [], "the comparison must never write EntitySourceMatch rows"


class TestAccessGate:
    async def test_raises_when_the_licensed_flag_is_off(self, rolled_back_db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "false")
        with pytest.raises(bc.IqviaSourceNotAvailable, match="ENABLE_IQVIA_SOURCES"):
            await bc.compare_entities_to_approved_iqvia_hco(rolled_back_db, entity_ids=[uuid.uuid4()])

    async def test_raises_when_no_approved_snapshot_exists(self, rolled_back_db, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        with pytest.raises(bc.IqviaSourceNotAvailable, match="no APPROVED"):
            await bc.compare_entities_to_approved_iqvia_hco(rolled_back_db, entity_ids=[uuid.uuid4()])

    async def test_empty_population_is_a_no_op_not_an_error(self, rolled_back_db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        approved = await _approve_hco_snapshot(db, tmp_path, "empty", [
            [f"{SYN}-HCO-X", NPI_REGISTERED, "", f"{SYN} X", "02101"]])
        summary = await bc.compare_entities_to_approved_iqvia_hco(db, entity_ids=[])
        assert summary.considered == 0
        assert summary.as_dict()["snapshot_id"] == str(approved.id)
        await _no_matches_written(db, approved.id)


class TestFourBuckets:
    async def test_clean_npi_hit_is_matched(self, rolled_back_db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        oid = f"{SYN}.BATCH.MATCHED"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} Matched Target", npi=NPI_REGISTERED)
        approved = await _approve_hco_snapshot(db, tmp_path, "matched", [
            [f"{SYN}-HCO-MATCHED", NPI_REGISTERED, "", f"{SYN} Matched Target", "02101"]])

        summary = await bc.compare_entities_to_approved_iqvia_hco(db, entity_ids=[entity_id])

        assert summary.considered == 1
        assert summary.matched == 1
        assert summary.no_identifier == summary.not_found == summary.ambiguous == 0
        assert summary.details[0]["bucket"] == bc.BUCKET_MATCHED
        await _no_matches_written(db, approved.id)

    async def test_entity_with_no_identifier_is_no_identifier(self, rolled_back_db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        oid = f"{SYN}.BATCH.BARE"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} Bare Entity")  # no npi=
        approved = await _approve_hco_snapshot(db, tmp_path, "bare", [
            [f"{SYN}-HCO-BARE", NPI_REGISTERED, "", f"{SYN} Unrelated", "02101"]])

        summary = await bc.compare_entities_to_approved_iqvia_hco(db, entity_ids=[entity_id])

        assert summary.considered == 1
        assert summary.no_identifier == 1
        assert summary.matched == summary.not_found == summary.ambiguous == 0
        assert summary.details[0]["bucket"] == bc.BUCKET_NO_IDENTIFIER
        await _no_matches_written(db, approved.id)

    async def test_npi_present_but_absent_from_extract_is_not_found(self, rolled_back_db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        oid = f"{SYN}.BATCH.NOTFOUND"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} Not In Extract", npi=NPI_VALID_OTHER)
        # approved extract carries a different NPI entirely
        approved = await _approve_hco_snapshot(db, tmp_path, "notfound", [
            [f"{SYN}-HCO-OTHER", NPI_REGISTERED, "", f"{SYN} Someone Else", "02101"]])

        summary = await bc.compare_entities_to_approved_iqvia_hco(db, entity_ids=[entity_id])

        assert summary.considered == 1
        assert summary.not_found == 1
        assert summary.matched == summary.no_identifier == summary.ambiguous == 0
        assert summary.details[0]["bucket"] == bc.BUCKET_NOT_FOUND
        await _no_matches_written(db, approved.id)

    async def test_ccn_fallback_with_multiple_candidates_is_ambiguous(self, rolled_back_db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        oid = f"{SYN}.BATCH.AMBIG"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} CCN Ambiguous")
        _add_identifier(db, entity_id, "ccn", "029999",
                        system_uri="urn:oid:2.16.840.1.113883.4.336")
        await db.flush()
        # two distinct IQVIA_HCO rows carry the same CCN -> not unique in the extract
        approved = await _approve_hco_snapshot(db, tmp_path, "ambig", [
            [f"{SYN}-HCO-AMBIG-1", "", "029999", f"{SYN} Facility A", "02101"],
            [f"{SYN}-HCO-AMBIG-2", "", "029999", f"{SYN} Facility B", "02101"],
        ])

        summary = await bc.compare_entities_to_approved_iqvia_hco(db, entity_ids=[entity_id])

        assert summary.considered == 1
        assert summary.ambiguous == 1
        assert summary.matched == summary.no_identifier == summary.not_found == 0
        assert summary.details[0]["bucket"] == bc.BUCKET_AMBIGUOUS
        await _no_matches_written(db, approved.id)

    async def test_single_clean_ccn_hit_is_still_ambiguous_never_auto_matched(
            self, rolled_back_db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        oid = f"{SYN}.BATCH.CCNONE"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} CCN Only")
        _add_identifier(db, entity_id, "ccn", "039999",
                        system_uri="urn:oid:2.16.840.1.113883.4.336")
        await db.flush()
        approved = await _approve_hco_snapshot(db, tmp_path, "ccnone", [
            [f"{SYN}-HCO-CCNONE", "", "039999", f"{SYN} CCN Only", "02101"]])

        summary = await bc.compare_entities_to_approved_iqvia_hco(db, entity_ids=[entity_id])

        # CCN is CANDIDATE-only by this codebase's existing rule, even with
        # exactly one clean hit -- never reported as an automatic MATCHED.
        assert summary.ambiguous == 1
        assert summary.matched == 0
        await _no_matches_written(db, approved.id)

    async def test_entity_with_two_npis_is_ambiguous_on_our_own_side(
            self, rolled_back_db, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        oid = f"{SYN}.BATCH.DUALNPI"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} Dual NPI", npi=NPI_REGISTERED)
        _add_identifier(db, entity_id, "npi", NPI_VALID_OTHER)
        await db.flush()
        approved = await _approve_hco_snapshot(db, tmp_path, "dualnpi", [
            [f"{SYN}-HCO-DUAL", NPI_REGISTERED, "", f"{SYN} Dual NPI", "02101"]])

        summary = await bc.compare_entities_to_approved_iqvia_hco(db, entity_ids=[entity_id])

        assert summary.ambiguous == 1
        assert summary.details[0]["reason"] == "entity carries more than one identifier of the same type"
        await _no_matches_written(db, approved.id)


class TestBatchedNotOneAtATime:
    async def test_a_mixed_population_is_bucketed_correctly_in_one_pass(
            self, rolled_back_db, tmp_path, monkeypatch):
        """24,589 real records is out of scope for a unit test; this proves
        the SAME code path handles a mixed, multi-entity population
        correctly with a small `batch_size`, which is what the real batching
        behavior (chunked queries, not one row at a time) actually depends
        on -- not the absolute count."""
        monkeypatch.setenv("ENABLE_IQVIA_SOURCES", "true")
        db = rolled_back_db
        matched_id = await seed_entity(db, oid=f"{SYN}.MIX.1", name=f"{SYN} Mix Matched", npi=NPI_REGISTERED)
        bare_id = await seed_entity(db, oid=f"{SYN}.MIX.2", name=f"{SYN} Mix Bare")
        notfound_id = await seed_entity(db, oid=f"{SYN}.MIX.3", name=f"{SYN} Mix NotFound", npi=NPI_VALID_OTHER)
        approved = await _approve_hco_snapshot(db, tmp_path, "mixed", [
            [f"{SYN}-HCO-MIX", NPI_REGISTERED, "", f"{SYN} Mix Matched", "02101"]])

        summary = await bc.compare_entities_to_approved_iqvia_hco(
            db, entity_ids=[matched_id, bare_id, notfound_id], batch_size=1)

        assert summary.considered == 3
        assert summary.matched == 1
        assert summary.no_identifier == 1
        assert summary.not_found == 1
        assert summary.ambiguous == 0
        await _no_matches_written(db, approved.id)
