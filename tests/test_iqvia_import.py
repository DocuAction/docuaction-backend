"""IQVIA Release-1 local import/matching -- entirely synthetic data.

No IQVIA content exists in this repository or in this test file: every
value below is fabricated, matching only the CONFIRMED STRUCTURE of the
real delivered layouts (column names, the HCP_ADDR (practitioner, address)
row grain, the null/leading-zero conventions) -- never a real row, name,
address, or identifier. The real files stay at
`C:\\ONS HHS\\ONC CSV file`, outside git entirely.
"""
from __future__ import annotations

import csv
import uuid

import pytest
from sqlalchemy import select

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_match as im
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import source_matching as sx
from rce_traceability_support import NPI_REGISTERED, SYN, rolled_back_db, seed_entity  # noqa: F401

pytestmark = pytest.mark.regression


class _User:
    def __init__(self, role, email):
        self.role, self.email, self.id = role, email, None


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


HCO_HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]
HCP_HEADER = ["HCP_HCE_ID", "ADDR_ID", "NPI", "FRST_NM", "LAST_NM", "ZIP5_CD"]
AFFIL_HEADER = ["HCP_HCE_ID", "HCO_HCE_ID", "AFFIL_TYPE_CD"]


# ── import: HCO (DEMOGRAPHIC) ────────────────────────────────────────────────

class TestHcoImport:
    async def test_stages_one_row_per_entity_and_preserves_leading_zeros(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "demo.csv", HCO_HEADER, [
            [f"{SYN}-HCO-1", NPI_REGISTERED, "012345", f"{SYN} Clinic One", "02101"],
            [f"{SYN}-HCO-2", "", "", f"{SYN} Clinic Two", "02101"],
        ])
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-hco", created_by=SYN)
        assert summary.rows_read == 2
        assert summary.rows_staged == 2
        assert summary.rows_rejected == 0
        assert summary.completed

        rows = (await db.execute(select(sm.IqviaHcoObservation).where(
            sm.IqviaHcoObservation.source_snapshot_id == summary.snapshot_id))).scalars().all()
        by_key = {r.source_record_key: r for r in rows}
        assert by_key[f"{SYN}-HCO-1"].ccn == "012345"  # leading zero intact, string column
        assert by_key[f"{SYN}-HCO-1"].payload["ORG_CCN_ID"] == "012345"  # and in the verbatim payload
        assert by_key[f"{SYN}-HCO-2"].npi is None  # blank -> None, not ""
        assert by_key[f"{SYN}-HCO-2"].ccn is None

    async def test_rejects_rows_with_no_usable_key(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "demo_bad.csv", HCO_HEADER, [
            ["", NPI_REGISTERED, "", f"{SYN} No Key", "02101"],  # missing HCO_HCE_ID
            [f"{SYN}-HCO-OK", "", "", f"{SYN} OK", "02101"],
        ])
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-hco-bad", created_by=SYN)
        assert summary.rows_read == 2
        assert summary.rows_staged == 1
        assert summary.rows_rejected == 1
        assert summary.rejected_sample_lines == [2]  # header is line 1

    async def test_idempotent_rerun_against_the_same_snapshot_stages_nothing_new(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "demo_retry.csv", HCO_HEADER, [
            [f"{SYN}-HCO-R1", "", "", f"{SYN} Retry One", "02101"],
            [f"{SYN}-HCO-R2", "", "", f"{SYN} Retry Two", "02101"],
        ])
        first = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-retry", created_by=SYN)
        assert first.rows_staged == 2

        # Simulated interrupted-then-resumed run: same file, same snapshot id.
        second = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-retry",
                                         created_by=SYN, snapshot_id=first.snapshot_id)
        assert second.rows_staged == 0
        assert second.rows_already_staged == 2

        rows = (await db.execute(select(sm.IqviaHcoObservation).where(
            sm.IqviaHcoObservation.source_snapshot_id == first.snapshot_id))).scalars().all()
        assert len(rows) == 2  # never duplicated

    async def test_resume_refuses_a_different_file_under_the_same_snapshot(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f1 = write_csv(tmp_path / "a.csv", HCO_HEADER, [[f"{SYN}-A", "", "", "A", "02101"]])
        f2 = write_csv(tmp_path / "b.csv", HCO_HEADER, [[f"{SYN}-B", "", "", "B", "02101"]])
        first = await ii.import_hco_csv(db, file_path=f1, label=f"{SYN}-swap", created_by=SYN)
        with pytest.raises(ValueError, match="does not match"):
            await ii.import_hco_csv(db, file_path=f2, label=f"{SYN}-swap",
                                    created_by=SYN, snapshot_id=first.snapshot_id)

    async def test_chunking_does_not_change_the_result(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        rows = [[f"{SYN}-HCO-C{i}", "", "", f"{SYN} Chunk {i}", "02101"] for i in range(25)]
        f = write_csv(tmp_path / "chunked.csv", HCO_HEADER, rows)
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-chunked",
                                          created_by=SYN, chunk_size=7)
        assert summary.rows_read == 25
        assert summary.rows_staged == 25


# ── import: HCP_ADDR ─────────────────────────────────────────────────────────

class TestHcpImport:
    async def test_one_key_per_practitioner_address_pair_no_fanout_collision(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        # Same practitioner, two addresses -- the real file's actual grain.
        f = write_csv(tmp_path / "addr.csv", HCP_HEADER, [
            [f"{SYN}-HCP-1", "1", NPI_REGISTERED, "Jo", f"{SYN} One", "02101"],
            [f"{SYN}-HCP-1", "2", NPI_REGISTERED, "Jo", f"{SYN} One", "02102"],
        ])
        summary = await ii.import_hcp_csv(db, file_path=f, label=f"{SYN}-addr", created_by=SYN)
        assert summary.rows_staged == 2
        rows = (await db.execute(select(sm.IqviaHcpObservation).where(
            sm.IqviaHcpObservation.source_snapshot_id == summary.snapshot_id))).scalars().all()
        keys = {r.source_record_key for r in rows}
        assert keys == {f"{SYN}-HCP-1:1", f"{SYN}-HCP-1:2"}  # distinct, never collapsed

    async def test_missing_addr_id_is_rejected_not_silently_collapsed(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "addr_bad.csv", HCP_HEADER, [
            [f"{SYN}-HCP-2", "", NPI_REGISTERED, "Jo", f"{SYN} Two", "02101"],
        ])
        summary = await ii.import_hcp_csv(db, file_path=f, label=f"{SYN}-addr-bad", created_by=SYN)
        assert summary.rows_staged == 0
        assert summary.rows_rejected == 1


# ── import: affiliation (no real data exists; synthetic-only path) ──────────

class TestAffiliationImport:
    async def test_stages_rows_against_a_synthetic_file(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "affil.csv", AFFIL_HEADER, [
            [f"{SYN}-HCP-1", f"{SYN}-HCO-1", "ADMITTING"],
        ])
        summary = await ii.import_affiliation_csv(db, file_path=f, label=f"{SYN}-affil",
                                                   created_by=SYN)
        assert summary.rows_staged == 1
        row = (await db.execute(select(sm.IqviaAffiliationObservation).where(
            sm.IqviaAffiliationObservation.source_snapshot_id == summary.snapshot_id)
            )).scalars().one()
        assert row.hcp_record_key == f"{SYN}-HCP-1"
        assert row.hco_record_key == f"{SYN}-HCO-1"

    async def test_hcp_to_org_matching_is_explicitly_not_implemented(self):
        with pytest.raises(NotImplementedError, match="no honest basis"):
            await im.match_hcp_snapshot(None, snapshot_id=uuid.uuid4(), actor=SYN)


# ── reconciliation / atomic publication ─────────────────────────────────────

class TestReconciliation:
    async def test_fully_staged_snapshot_reconciles(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "recon_ok.csv", HCO_HEADER, [
            [f"{SYN}-HCO-REC1", "", "", "A", "02101"],
            [f"{SYN}-HCO-REC2", "", "", "B", "02101"],
        ])
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-recon", created_by=SYN)
        status = await ii.verify_snapshot_staged_completely(db, summary.snapshot_id)
        assert status["reconciles"] is True
        assert status["staged_row_count"] == 2
        assert status["declared_record_count"] == 2

    async def test_partially_staged_snapshot_does_not_reconcile(self, rolled_back_db, tmp_path):
        """Simulates an interrupted import: the snapshot's declared
        record_count reflects the full file, but only some rows actually
        made it into the observation table."""
        db = rolled_back_db
        snapshot = await sx.register_snapshot(
            db, source_system=sm.SOURCE_IQVIA_HCO, label=f"{SYN}-partial",
            sha256="0" * 64, record_count=10, received_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc), created_by=SYN)
        db.add(sm.IqviaHcoObservation(
            id=uuid.uuid4(), source_snapshot_id=snapshot.id,
            source_record_key=f"{SYN}-HCO-ONLYONE", record_sha256="1" * 64,
            payload={"HCO_HCE_ID": f"{SYN}-HCO-ONLYONE"}, correlation_id=SYN))
        await db.commit()
        status = await ii.verify_snapshot_staged_completely(db, snapshot.id)
        assert status["reconciles"] is False
        assert status["staged_row_count"] == 1
        assert status["declared_record_count"] == 10

    async def test_approval_is_a_separate_step_never_done_by_the_importer(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "approve.csv", HCO_HEADER, [[f"{SYN}-HCO-AP", "", "", "A", "02101"]])
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-approve", created_by=SYN)
        snapshot = await db.get(sm.SourceSnapshot, summary.snapshot_id)
        assert snapshot.status == sm.SNAPSHOT_PENDING  # importer never approves

        qalead = _User("qalead", "qa@example.test")
        approved = await sx.approve_snapshot(db, summary.snapshot_id, user=qalead,
                                             approval_ref=f"{SYN}-approval-ref")
        assert approved.status == sm.SNAPSHOT_APPROVED
        assert approved.id != summary.snapshot_id  # append-only successor row


# ── matching ──────────────────────────────────────────────────────────────────

class TestHcoMatching:
    async def test_auto_approves_exact_unique_type2_npi(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        oid = f"{SYN}.MATCH.1"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} Match Target", npi=NPI_REGISTERED)
        f = write_csv(tmp_path / "match.csv", HCO_HEADER, [
            [f"{SYN}-HCO-MATCH", NPI_REGISTERED, "", f"{SYN} Match Target", "02101"],
        ])
        imported = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-match", created_by=SYN)
        qalead = _User("qalead", "qa@example.test")
        approved = await sx.approve_snapshot(db, imported.snapshot_id, user=qalead,
                                             approval_ref=f"{SYN}-ref")

        async def nppes_lookup(npi):
            return {"enumeration_type": "NPI-2"}

        summary = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                              nppes_type_lookup=nppes_lookup)
        assert summary.auto_approved == 1
        match = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().one()
        assert match.match_status == sm.MATCH_AUTO_APPROVED
        assert str(match.entity_id) == str(entity_id)

    async def test_no_nppes_evidence_is_never_fabricated_into_an_auto_approval(
            self, rolled_back_db, tmp_path):
        """The safety property this module exists to prove: with no NPPES
        type lookup injected, a match that WOULD auto-approve if Type-2 were
        known instead stays CANDIDATE -- never fabricated as confirmed.
        `evaluate_npi_match`'s own existing "entity type not evidenced"
        branch (unchanged, pre-existing `source_matching.py` logic) returns
        CANDIDATE with no candidate entities listed at all -- it does not
        even suggest which registry entities might match until NPPES type
        is known. With no entity to attach it to, this module counts it as
        `zero_candidate_unwritten`, not a written CANDIDATE row -- the
        observation stays visible in `iqvia_hco_observation` for an analyst
        to find directly, rather than disappearing."""
        db = rolled_back_db
        oid = f"{SYN}.MATCH.2"
        await seed_entity(db, oid=oid, name=f"{SYN} No Evidence", npi=NPI_REGISTERED)
        f = write_csv(tmp_path / "match_noevidence.csv", HCO_HEADER, [
            [f"{SYN}-HCO-NOEV", NPI_REGISTERED, "", f"{SYN} No Evidence", "02101"],
        ])
        imported = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-noev", created_by=SYN)
        qalead = _User("qalead", "qa@example.test")
        approved = await sx.approve_snapshot(db, imported.snapshot_id, user=qalead,
                                             approval_ref=f"{SYN}-ref")
        summary = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN)  # no lookup
        assert summary.auto_approved == 0
        assert summary.candidates_written == 0
        assert summary.zero_candidate_unwritten == 1
        no_rows = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().all()
        assert no_rows == []  # nothing fabricated; nothing written either

    async def test_ccn_only_is_always_a_candidate(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        from app.tefca_registry import models as reg
        oid = f"{SYN}.MATCH.3"
        entity_id = await seed_entity(db, oid=oid, name=f"{SYN} CCN Target")
        db.add(reg.TefcaEntityIdentifier(
            id=uuid.uuid4(), entity_id=entity_id, identifier_type="ccn",
            identifier_value="019999", system_uri="urn:oid:2.16.840.1.113883.4.336",
            is_primary=False, identifier_status="active"))
        await db.commit()
        f = write_csv(tmp_path / "match_ccn.csv", HCO_HEADER, [
            [f"{SYN}-HCO-CCN", "", "019999", f"{SYN} CCN Target", "02101"],
        ])
        imported = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-ccn", created_by=SYN)
        qalead = _User("qalead", "qa@example.test")
        approved = await sx.approve_snapshot(db, imported.snapshot_id, user=qalead,
                                             approval_ref=f"{SYN}-ref")
        summary = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN)
        assert summary.candidates_written == 1
        assert summary.auto_approved == 0
        match = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().one()
        assert match.match_method == sm.METHOD_CCN_CANDIDATE
        assert match.match_status == sm.MATCH_CANDIDATE

    async def test_no_identifier_at_all_is_counted_not_matched(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "match_none.csv", HCO_HEADER, [
            [f"{SYN}-HCO-NONE", "", "", f"{SYN} Nothing", "02101"],
        ])
        imported = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-none", created_by=SYN)
        qalead = _User("qalead", "qa@example.test")
        approved = await sx.approve_snapshot(db, imported.snapshot_id, user=qalead,
                                             approval_ref=f"{SYN}-ref")
        summary = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN)
        assert summary.unmatchable_no_identifier == 1
        no_rows = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().all()
        assert no_rows == []

    async def test_rematching_the_same_snapshot_skips_already_matched_rows(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        oid = f"{SYN}.MATCH.4"
        await seed_entity(db, oid=oid, name=f"{SYN} Rerun Target", npi=NPI_REGISTERED)
        f = write_csv(tmp_path / "match_rerun.csv", HCO_HEADER, [
            [f"{SYN}-HCO-RERUN", NPI_REGISTERED, "", f"{SYN} Rerun Target", "02101"],
        ])
        imported = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-rerun", created_by=SYN)
        qalead = _User("qalead", "qa@example.test")
        approved = await sx.approve_snapshot(db, imported.snapshot_id, user=qalead,
                                             approval_ref=f"{SYN}-ref")

        async def nppes_lookup(npi):
            return {"enumeration_type": "NPI-2"}

        first = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                            nppes_type_lookup=nppes_lookup)
        second = await im.match_hco_snapshot(db, snapshot_id=approved.id, actor=SYN,
                                             nppes_type_lookup=nppes_lookup)
        assert first.auto_approved == 1
        assert second.auto_approved == 0
        assert second.already_matched_skipped == 1
        rows = (await db.execute(select(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id == approved.id))).scalars().all()
        assert len(rows) == 1  # never duplicated by the rerun
