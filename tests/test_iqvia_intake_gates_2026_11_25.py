"""The intake gates for NEW imports (2026-11-25), and proof that a snapshot
imported before them is left alone. Database-backed, synthetic throughout.

  C  a file whose header cannot key rows is stopped before any row, once
  D  an unregistered contract stages its rows and cannot reconcile until a
     reviewed contract is registered
  E  a record of another width is quarantined, never staged under names
  F  an identifier that does not fit its lifted column leaves it NULL
  H  a key conflict is classified: same row again, or a different row
  K  no value in any stored reason, metadata or log line
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

import iqvia_contract_support as support
from app.tefca_registry.rce import iqvia_contract_registry as cr
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import iqvia_upload_models as um
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce.source_matching import register_snapshot
from iqvia_contract_support import AFFIL, EDITION_2, HCO, HCP, committed_db  # noqa: F401
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression

SENTINEL = "SENTINEL-VALUE-7Q4Z"
BEFORE = "imported before contract validation"


@pytest.fixture
def contracts(tmp_path, monkeypatch):
    directory = support.build_synthetic_contracts(tmp_path / "contracts")
    support.use_contracts(monkeypatch, directory)
    return directory


async def run_import(db, source, path, **kwargs):
    return await getattr(ii, support.IMPORTERS[source])(
        db, file_path=path, label=f"{SYN}-{path.stem}", created_by=SYN, **kwargs)


async def stored_rows(db, source, snapshot_id):
    model = support.MODELS[source]
    return (await db.execute(select(model).where(model.source_snapshot_id == snapshot_id))).scalars().all()


async def snapshot_by_label(db, label):
    return (await db.execute(select(sm.SourceSnapshot).where(
        sm.SourceSnapshot.snapshot_label == label))).scalar_one()


async def metadata_of(db, snapshot_id):
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    await db.refresh(snapshot)
    return snapshot.metadata_


def hco_rows(n, **overrides):
    contract = support.contract_for(HCO, EDITION_2)
    return contract, [support.contract_row(contract, i, overrides=overrides) for i in range(1, n + 1)]


# ── C: structural stops ──────────────────────────────────────────────────────

class TestStructuralStops:
    @pytest.mark.parametrize("case,code", [
        ("duplicate", cr.STOP_DUPLICATE_NAME), ("blank", cr.STOP_BLANK_NAME),
        ("missing_key", cr.STOP_REQUIRED_KEY_MISSING), ("empty", cr.STOP_NO_HEADER),
        ("not_utf8", cr.STOP_HEADER_UNREADABLE)])
    async def test_the_file_is_stopped_before_any_row_and_says_why(
            self, rolled_back_db, contracts, tmp_path, case, code):
        db = rolled_back_db
        contract, rows = hco_rows(3)
        header = list(contract.names)
        if case == "duplicate":
            header.append("BUS_NM")
        elif case == "blank":
            header[5] = "  "
        elif case == "missing_key":
            header[0] = "hco_hce_id"                       # present only in another case
        path = tmp_path / f"stop-{case}.csv"
        if case == "empty":
            path.write_bytes(b"")
        elif case == "not_utf8":
            path.write_bytes(b"HCO_HCE_ID,ORG_\xff\xfeNPI\r\n1,2\r\n")
        else:
            support.write_cells(path, header, [[r.get(n, "x") for n in header] for r in rows])
        with pytest.raises(ii.IntakeStructuralStop) as caught:
            await run_import(db, HCO, path)
        assert isinstance(caught.value, rp.ReferencePreflightBlocked) and caught.value.non_retryable
        assert "No row was staged" in str(caught.value)
        snapshot = await snapshot_by_label(db, f"{SYN}-stop-{case}")
        assert await stored_rows(db, HCO, snapshot.id) == []
        stop = snapshot.metadata_["intake_stop"]
        assert [s["code"] for s in stop["stops"]] == [code]
        assert stop["retryable"] is False and stop["rows_staged"] == 0
        assert "contract_binding" in snapshot.metadata_ and "reference_preflight" in snapshot.metadata_
        assert "import_validation" not in snapshot.metadata_
        if case == "duplicate":
            assert stop["stops"][0]["columns"] == [{"name": "BUS_NM", "positions": [4, len(header)]}]
        if case == "missing_key":
            assert stop["stops"][0]["columns"] == ["HCO_HCE_ID"]

    async def test_a_file_with_no_header_row_is_stopped_without_repeating_its_first_record(
            self, rolled_back_db, contracts, tmp_path, caplog):
        db = rolled_back_db
        path = support.write_cells(tmp_path / "headerless.csv",
                                   ["90000001", SENTINEL, SENTINEL, "NAME " + SENTINEL, "02101"],
                                   [["90000002", SENTINEL, "", SENTINEL, SENTINEL]])
        caplog.set_level(logging.DEBUG)
        with pytest.raises(ii.IntakeStructuralStop) as caught:
            await run_import(db, HCO, path)
        snapshot = await snapshot_by_label(db, f"{SYN}-headerless")
        codes = {s["code"] for s in snapshot.metadata_["intake_stop"]["stops"]}
        assert codes == {cr.STOP_REQUIRED_KEY_MISSING, cr.STOP_DUPLICATE_NAME}
        everything = support.all_strings(snapshot.metadata_) + [str(caught.value), caplog.text]
        assert all("SENTINEL" not in text and "90000001" not in text for text in everything)

    async def test_the_job_fails_once_and_is_never_requeued(
            self, committed_db, contracts, tmp_path, monkeypatch, caplog):
        db = committed_db
        contract, rows = hco_rows(3, BUS_NM=SENTINEL, SYN_NOTE=SENTINEL[:8])
        header = list(contract.names) + ["BUS_NM"]
        path = support.write_cells(tmp_path / f"job-stop-{uuid.uuid4().hex[:6]}.csv", header,
                                   [[r[n] for n in header] for r in rows])
        hashes = {"n": 0}
        real_hash = ii.file_sha256

        def counting_hash(p, **kw):
            hashes["n"] += 1
            return real_hash(p, **kw)

        staged = await routes.stage_source(
            "hco", routes.StageRequest(file_path=str(path), label=f"{SYN}-{path.stem}"),
            db, user=support.StageUser())
        snapshot_id = uuid.UUID(staged["snapshot_id"])
        support.created_snapshot_ids.append(snapshot_id)
        job = await jobs.get_import_job(db, uuid.UUID(staged["job_id"]))
        job.state, job.attempt_count = um.IqviaImportJob.STATE_RUNNING, 1     # as claim_next_queued does
        await db.commit()
        monkeypatch.setattr(ii, "file_sha256", counting_hash)
        caplog.set_level(logging.DEBUG)
        await routes.run_import_job(str(job.id))
        await db.refresh(job)
        assert job.state == um.IqviaImportJob.STATE_FAILED and job.active_marker is None
        assert job.attempt_count == 1 < um.IqviaImportJob.MAX_ATTEMPTS
        assert job.phase == "Failed (not retryable)" and hashes["n"] == 1
        assert job.error_reason.startswith("IntakeStructuralStop:") and "BUS_NM" in job.error_reason
        meta = await metadata_of(db, snapshot_id)
        assert meta["import_status"] == "failed" and meta["import_error"] == job.error_reason
        assert await stored_rows(db, HCO, snapshot_id) == []
        everything = support.all_strings(meta) + [job.error_reason, caplog.text]
        assert all("SENTINEL" not in text for text in everything)
        # Nothing is left to claim for this snapshot.
        assert await jobs.active_job_for_snapshot(db, snapshot_id) is None


# ── D: unregistered contract ─────────────────────────────────────────────────

class TestUnregisteredContract:
    async def test_rows_are_staged_and_reconciliation_waits_for_a_registered_contract(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        future = list(support.SYNTHETIC_LAYOUTS[HCO]) + list(support.SYNTHETIC_ADDED_IN_EDITION_2[HCO]) \
            + [("SYN_FUTURE_COLUMN", "Text", 6)]
        header = [name for name, _t, _l in future]
        rows = [support.contract_row(contract, n, overrides={"SYN_FUTURE_COLUMN": "kept"}) for n in (1, 2)]
        f = support.write_contract_file(tmp_path / "future.csv", contract, rows, header=header)
        done = await run_import(db, HCO, f)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (2, 2, 0)
        before = {o.source_record_key: (o.id, o.record_sha256, o.payload)
                  for o in await stored_rows(db, HCO, done.snapshot_id)}
        assert all(p["SYN_FUTURE_COLUMN"] == "kept" for _i, _s, p in before.values())

        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        assert verdict["staged_row_count"] == verdict["expected_staged_count"] == 2
        assert verdict["reconciles"] is False and len(verdict["refusal_reasons"]) == 1
        reason = verdict["refusal_reasons"][0]
        assert reason.startswith("Unregistered contract:") and "IQVIA_CONTRACTS_DIR" in reason
        assert "1 added, 0 missing, 0 out of position" in reason and "No re-upload is needed" in reason
        view = verdict["contract_validation"]
        assert view["status"] == "unregistered" and view["label"] == "unregistered contract"
        assert view["diff"]["added"]["names"] == ["SYN_FUTURE_COLUMN"] and view["validated"] is False

        # A reviewed contract for that layout is registered; nothing is re-imported.
        layout = support.write_layout_csv(tmp_path / "future_layout.csv", future)
        support._generator().register(layout, HCO, "2031-05-15", contracts)
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        assert verdict["reconciles"] is True and verdict["refusal_reasons"] == []
        assert verdict["contract_validation"]["status"] == "exact"
        assert verdict["contract_validation"]["bound_at_import"] == "unregistered"
        after = {o.source_record_key: (o.id, o.record_sha256, o.payload)
                 for o in await stored_rows(db, HCO, done.snapshot_id)}
        assert after == before

    async def test_with_no_contract_directory_every_new_import_is_unregistered(
            self, rolled_back_db, tmp_path, monkeypatch):
        db = rolled_back_db
        support.use_contracts(monkeypatch, None)
        f = support.write_cells(tmp_path / "nodir.csv", ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM"],
                                [["90000001", "", "", "NAME"]])
        done = await run_import(db, HCO, f)
        assert done.rows_staged == 1
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        assert verdict["reconciles"] is False
        assert "IQVIA_CONTRACTS_DIR is not set" in verdict["refusal_reasons"][0]

    async def test_a_registered_layout_reconciles(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract, rows = hco_rows(4)
        done = await run_import(db, HCO, support.write_contract_file(tmp_path / "ok.csv", contract, rows))
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        assert verdict["reconciles"] is True and verdict["contract_validation"]["validated"] is True
        assert verdict["contract_validation"]["label"] == "bound to a registered contract"


# ── E: width anomalies ───────────────────────────────────────────────────────

class TestQuarantine:
    async def test_short_and_long_records_are_quarantined_and_their_neighbours_staged(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract, rows = hco_rows(6)
        cells = [[r[n] for n in contract.names] for r in rows]
        cells[1] = cells[1][:-2]                               # record 2: short
        cells[4] = cells[4] + [SENTINEL, SENTINEL]             # record 5: long
        f = support.write_cells(tmp_path / "width.csv", list(contract.names), cells)
        done = await run_import(db, HCO, f)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (6, 4, 2)
        stored = {o.source_record_key: o for o in await stored_rows(db, HCO, done.snapshot_id)}
        assert set(stored) == {rows[i]["HCO_HCE_ID"] for i in (0, 2, 3, 5)}
        assert all(set(o.payload) == set(contract.names) for o in stored.values())
        meta = await metadata_of(db, done.snapshot_id)
        width = meta["import_validation"]["record_width"]
        assert (width["short_records"], width["short_record_numbers"]) == (1, [2])
        assert (width["long_records"], width["long_record_numbers"]) == (1, [5])
        assert (width["quarantined_records"], width["quarantined_record_numbers"]) == (2, [2, 5])
        assert meta["import_validation"]["rows"]["rejected"] == 2
        assert all("SENTINEL" not in text for text in support.all_strings(meta))
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        # The count equation holds (read - rejected == staged); the refusal is explicit.
        assert verdict["staged_row_count"] == verdict["expected_staged_count"] == 4
        assert verdict["reconciles"] is False
        assert any("2 record(s) were quarantined" in r and "ambiguous row structure" in r
                   for r in verdict["refusal_reasons"])


# ── F: lifted identifiers ────────────────────────────────────────────────────

class TestLiftedIdentifiers:
    async def test_text_that_does_not_fit_leaves_the_lifted_column_null_and_stays_in_the_payload(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        fits = support.contract_row(contract, 1, overrides={"ORG_NPI": " 1234567893 ", "ORG_CCN_ID": "012345"})
        long_npi = support.contract_row(contract, 2, overrides={"ORG_NPI": "123456789012"})     # 12 > 10
        long_ccn = support.contract_row(contract, 3, overrides={"ORG_CCN_ID": "C" * 21})        # 21 > 20
        f = support.write_contract_file(tmp_path / "lift.csv", contract, [fits, long_npi, long_ccn])
        done = await run_import(db, HCO, f)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (3, 3, 0)
        stored = {o.source_record_key: o for o in await stored_rows(db, HCO, done.snapshot_id)}
        a, b, c = (stored[r["HCO_HCE_ID"]] for r in (fits, long_npi, long_ccn))
        assert (a.npi, a.ccn) == ("1234567893", "012345")              # exactly as before
        assert b.npi is None and b.payload["ORG_NPI"] == "123456789012" and b.ccn == long_npi["ORG_CCN_ID"]
        assert c.ccn is None and c.payload["ORG_CCN_ID"] == "C" * 21 and c.npi == long_ccn["ORG_NPI"]
        lifted = (await metadata_of(db, done.snapshot_id))["import_validation"]["lifted_identifiers"]
        assert (lifted["npi"]["lifted"], lifted["npi"]["longer_than_column"],
                lifted["npi"]["record_numbers"]) == (2, 1, [2])
        assert (lifted["ccn"]["lifted"], lifted["ccn"]["longer_than_column"],
                lifted["ccn"]["record_numbers"]) == (2, 1, [3])

    async def test_a_practitioner_npi_longer_than_ten_is_not_truncated(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCP, EDITION_2)
        row = support.contract_row(contract, 1, overrides={"NPI": "12345678901"})
        done = await run_import(db, HCP, support.write_contract_file(tmp_path / "hcpnpi.csv", contract, [row]))
        obs = (await stored_rows(db, HCP, done.snapshot_id))[0]
        assert obs.npi is None and obs.payload["NPI"] == "12345678901"


# ── H: key conflicts ─────────────────────────────────────────────────────────

class TestKeyConflicts:
    async def test_a_different_row_under_the_same_key_is_a_collision_and_blocks_reconciliation(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract, rows = hco_rows(4)
        rows.append(dict(rows[1], BUS_NM="OTHER NAME", ZIP5_CD="09999"))       # record 5 collides with 2
        f = support.write_contract_file(tmp_path / "collide.csv", contract, rows)
        done = await run_import(db, HCO, f)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged) == (5, 4, 1)
        kept = {o.source_record_key: o for o in await stored_rows(db, HCO, done.snapshot_id)}
        assert kept[rows[1]["HCO_HCE_ID"]].payload == rows[1]                   # the first row, untouched
        conflicts = (await metadata_of(db, done.snapshot_id))["import_validation"]["key_conflicts"]
        assert conflicts["key_collisions_different_content"] == 1
        assert conflicts["key_collision_record_numbers"] == [5]
        assert conflicts["exact_duplicate_rows_in_file"] == 0
        assert conflicts["collision_columns_that_differ"] == [
            {"column": "BUS_NM", "collisions": 1}, {"column": "ZIP5_CD", "collisions": 1}]
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        assert verdict["reconciles"] is False
        reason = next(r for r in verdict["refusal_reasons"] if "table unique key" in r)
        assert "the stored row grain cannot hold both rows" in reason
        assert "BUS_NM, ZIP5_CD" in reason and "owner decision" in reason
        assert all("OTHER NAME" not in text and "09999" not in text
                   for text in support.all_strings(verdict) + support.all_strings(conflicts))

    async def test_exact_duplicate_rows_are_counted_by_multiplicity_and_are_not_collisions(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract, rows = hco_rows(3)
        f = support.write_contract_file(tmp_path / "dupes.csv", contract,
                                        rows + [rows[0], rows[0], rows[2]])
        done = await run_import(db, HCO, f, chunk_size=2)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged) == (6, 3, 3)
        conflicts = (await metadata_of(db, done.snapshot_id))["import_validation"]["key_conflicts"]
        assert conflicts["exact_duplicate_rows_in_file"] == 3
        assert conflicts["exact_duplicate_record_numbers"] == [4, 5, 6]
        assert conflicts["key_collisions_different_content"] == 0
        assert conflicts["identical_row_already_present"] == 0
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        # Unchanged rule: read - rejected must equal staged. Now explained by the count above.
        assert verdict["refusal_reasons"] == ["staged row count does not equal read-minus-rejected"]
        assert verdict["contract_validation"]["exact_duplicate_rows_in_file"] == 3

    async def test_a_full_resume_reads_every_row_as_already_present(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract, rows = hco_rows(7)
        f = support.write_contract_file(tmp_path / "again.csv", contract, rows)
        first = await run_import(db, HCO, f, chunk_size=3)
        before = {o.source_record_key: (o.id, o.record_sha256) for o in await stored_rows(db, HCO, first.snapshot_id)}
        again = await run_import(db, HCO, f, chunk_size=3, snapshot_id=first.snapshot_id)
        assert (again.rows_staged, again.rows_already_staged) == (0, 7)
        conflicts = (await metadata_of(db, first.snapshot_id))["import_validation"]["key_conflicts"]
        assert conflicts["snapshot_had_rows_at_start"] is True
        assert conflicts["identical_row_already_present"] == 7
        assert conflicts["key_collisions_different_content"] == conflicts["exact_duplicate_rows_in_file"] == 0
        assert {o.source_record_key: (o.id, o.record_sha256)
                for o in await stored_rows(db, HCO, first.snapshot_id)} == before
        assert (await ii.verify_snapshot_staged_completely(db, first.snapshot_id))["reconciles"] is True

    async def test_one_affiliation_key_with_two_different_rows_is_never_dropped_silently(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(AFFIL, EDITION_2)
        key = {"HCP_HCE_ID": "90000001", "HCO_HCE_ID": "80000001", "AFFL_TYP_ID": "11", "TITL_TYP_ID": ""}
        first = support.contract_row(contract, 1, overrides=key)
        other_address = dict(first, ADDR_ID="77777777", ZIP5_CD="09999")           # stored columns differ
        other_unstored = dict(first, SYN_UNSTORED_NOTE="changed")                   # only an unstored one
        f = support.write_contract_file(tmp_path / "affkey.csv", contract, [first, other_address, other_unstored])
        done = await run_import(db, AFFIL, f)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged) == (3, 1, 2)
        conflicts = (await metadata_of(db, done.snapshot_id))["import_validation"]["key_conflicts"]
        assert conflicts["table_unique_key"] == ["source_snapshot_id", "hcp_record_key", "hco_record_key",
                                                 "affiliation_type"]
        assert conflicts["key_collisions_different_content"] == 2
        assert conflicts["collision_columns_that_differ"] == [
            {"column": "ADDR_ID", "collisions": 1}, {"column": "ZIP5_CD", "collisions": 1}]
        assert conflicts["collisions_differing_only_in_columns_not_stored"] == 1
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        reason = next(r for r in verdict["refusal_reasons"] if "table unique key" in r)
        assert "cannot hold both rows" in reason and "ADDR_ID, ZIP5_CD" in reason
        assert "differ only in columns that are not stored" in reason

    async def test_nothing_is_classified_when_nothing_conflicts(self, rolled_back_db, contracts, tmp_path, monkeypatch):
        db = rolled_back_db
        called = []
        real = ii._KeyConflicts.classify

        async def spy(self, db_, rows, record_numbers):
            called.append(len(rows))
            return await real(self, db_, rows, record_numbers)

        monkeypatch.setattr(ii._KeyConflicts, "classify", spy)
        contract, rows = hco_rows(5)
        await run_import(db, HCO, support.write_contract_file(tmp_path / "clean.csv", contract, rows), chunk_size=2)
        assert called == []


# ── a snapshot imported before the gates ─────────────────────────────────────

async def legacy_snapshot(db, path, *, staged_rows):
    """A snapshot as the importer left it BEFORE this change: rows staged, no
    marker, no binding."""
    snapshot = await register_snapshot(
        db, source_system=HCO, label=f"{SYN}-{path.stem}", sha256=ii.file_sha256(path), record_count=0,
        received_at=datetime.now(timezone.utc), created_by=SYN,
        metadata={"original_path": str(path), "reference_reader": ii.REFERENCE_READER})
    rows = [row for _line, row in ii.iter_reference_rows(path)][:staged_rows]
    await ii._stage_rows(db, snapshot_id=snapshot.id, model=sm.IqviaHcoObservation, rows=[
        {"id": uuid.uuid4(), "source_snapshot_id": snapshot.id, "record_sha256": ii._row_sha256(r),
         "observed_at": datetime.now(timezone.utc), "correlation_id": "legacy", "payload": r,
         **ii._hco_payload_and_keys(r)} for r in rows])
    await db.commit()
    return snapshot


class TestSnapshotsImportedBeforeAreLeftAlone:
    HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]

    def _file(self, tmp_path, name, extra=()):
        records = [[f"{SYN}-LEG-{name}-{n}", "", "", "NAME", "02101"] for n in range(4)] + list(extra)
        return support.write_cells(tmp_path / f"{name}.csv", self.HEADER, records)

    async def test_a_completed_legacy_snapshot_is_labelled_and_refused_nothing(
            self, rolled_back_db, tmp_path, monkeypatch):
        db = rolled_back_db
        support.use_contracts(monkeypatch, None)             # no contract anywhere: still not refused
        f = self._file(tmp_path, "done")
        snapshot = await legacy_snapshot(db, f, staged_rows=4)
        snapshot.record_count = 4
        snapshot.metadata_ = {**snapshot.metadata_, "rows_rejected": 0, "import_status": "completed"}
        await db.commit()
        verdict = await ii.verify_snapshot_staged_completely(db, snapshot.id)
        assert verdict["reconciles"] is True and verdict["refusal_reasons"] == []
        view = verdict["contract_validation"]
        assert view["label"] == BEFORE and view["status"] == "not_evaluated" and view["validated"] is False
        from app.core.config import settings
        monkeypatch.setattr(settings, "ENABLE_IQVIA_SOURCES", True, raising=False)
        status = await routes.snapshot_status(snapshot.id, db, user=support.StageUser())
        assert status["contract_validation"]["label"] == BEFORE
        assert status["reconciliation"]["contract_validation_label"] == BEFORE
        assert status["import_validation"] is None and status["intake_stop"] is None

    async def test_a_legacy_resume_keeps_the_legacy_behaviour_and_gets_no_marker(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        f = self._file(tmp_path, "resume", extra=[[f"{SYN}-LEG-short", "", "", "NAME"],          # short
                                                   [f"{SYN}-LEG-resume-0", "", "", "OTHER", "0"]])  # same key
        snapshot = await legacy_snapshot(db, f, staged_rows=2)
        done = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=snapshot.id)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged, done.rows_rejected) == (6, 3, 3, 0)
        meta = await metadata_of(db, snapshot.id)
        assert "intake_gates" not in meta and "contract_binding" not in meta and "intake_stop" not in meta
        assert meta["import_validation"]["intake_gates_applied"] is False
        assert "key_conflicts" not in meta["import_validation"]
        assert meta["import_validation"]["record_width"]["quarantined_records"] == 0
        stored = {o.source_record_key: o for o in await stored_rows(db, HCO, snapshot.id)}
        assert stored[f"{SYN}-LEG-short"].payload["ZIP5_CD"] is None        # legacy shape: staged with null
        verdict = await ii.verify_snapshot_staged_completely(db, snapshot.id)
        assert verdict["contract_validation"]["label"] == BEFORE
        assert verdict["refusal_reasons"] == [
            "staged row count does not equal read-minus-rejected",
            "reference preflight gate is BLOCKED for this snapshot"]        # exactly what it said before

    async def test_a_legacy_resume_still_fails_on_a_long_row_and_an_over_long_identifier(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        long_row = self._file(tmp_path, "longrow", extra=[[f"{SYN}-LEG-long", "", "", "N", "02101", "EXTRA"]])
        snapshot = await legacy_snapshot(db, long_row, staged_rows=1)
        with pytest.raises(TypeError):
            await ii.import_hco_csv(db, file_path=long_row, label="unused", created_by=SYN, snapshot_id=snapshot.id)
        await db.rollback()
        wide = self._file(tmp_path, "widenpi", extra=[[f"{SYN}-LEG-wide", "12345678901", "", "N", "02101"]])
        snapshot = await legacy_snapshot(db, wide, staged_rows=1)
        with pytest.raises(DBAPIError):
            await ii.import_hco_csv(db, file_path=wide, label="unused", created_by=SYN, snapshot_id=snapshot.id)
        await db.rollback()

    async def test_a_legacy_record_hash_is_what_it_always_was(self):
        row = {"HCO_HCE_ID": "1", "ORG_NPI": "", "BUS_NM": "Né", "ZIP5_CD": None}
        import hashlib
        import json
        assert ii._row_sha256(row) == hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()


# ── K: no value in a reason, a log line or metadata ──────────────────────────

class TestNoValueInFailureReasons:
    def test_only_this_packages_own_refusals_keep_their_text(self):
        leaky = DBAPIError("INSERT INTO t VALUES ($1)", {"p": SENTINEL}, Exception(f"Key (x)=({SENTINEL})"))
        assert SENTINEL in str(leaky)
        reason = ii.safe_failure_reason(leaky)
        assert "SENTINEL" not in reason and reason.startswith("DBAPIError:Exception")
        assert "SENTINEL" not in ii.safe_failure_reason(ValueError(SENTINEL))
        assert "SENTINEL" not in ii.safe_failure_reason(UnicodeDecodeError("utf-8", SENTINEL.encode(), 0, 1, "x"))
        assert ii.safe_failure_reason(ii.ImportRefused("snapshot abc is APPROVED, not PENDING")) == \
            "ImportRefused: snapshot abc is APPROVED, not PENDING"
        assert ii.safe_failure_reason(rp.ReferencePreflightBlocked("REF-SCH-001")).endswith("REF-SCH-001")

    async def test_a_database_error_reaches_neither_the_job_the_snapshot_nor_the_log(
            self, committed_db, contracts, tmp_path, monkeypatch, caplog):
        db = committed_db
        contract, rows = hco_rows(2, BUS_NM=SENTINEL)
        path = support.write_contract_file(tmp_path / f"leak-{uuid.uuid4().hex[:6]}.csv", contract, rows)
        staged = await routes.stage_source(
            "hco", routes.StageRequest(file_path=str(path), label=f"{SYN}-{path.stem}"),
            db, user=support.StageUser())
        snapshot_id = uuid.UUID(staged["snapshot_id"])
        support.created_snapshot_ids.append(snapshot_id)
        job = await jobs.get_import_job(db, uuid.UUID(staged["job_id"]))
        job.state, job.attempt_count = um.IqviaImportJob.STATE_RUNNING, 1
        await db.commit()

        async def database_refuses(db_, *, snapshot_id, model, rows):
            # A real statement failure, with the row values among its parameters.
            from sqlalchemy import text
            await db_.execute(text("SELECT CAST(:v AS integer)"), {"v": rows[0]["payload"]["BUS_NM"]})

        monkeypatch.setattr(ii, "_stage_rows", database_refuses)
        caplog.set_level(logging.DEBUG)
        await routes.run_import_job(str(job.id))
        await db.refresh(job)
        # Retryable: back in the queue, and the bookkeeping survived the failed transaction.
        assert job.state == um.IqviaImportJob.STATE_QUEUED and job.attempt_count == 1
        assert job.error_reason.startswith("DBAPIError:") and "error text withheld" in job.error_reason
        meta = await metadata_of(db, snapshot_id)
        assert meta["import_status"] == "staging"
        everything = support.all_strings(meta) + [job.error_reason, caplog.text]
        assert all("SENTINEL" not in text for text in everything)
        assert "iqvia_routes.py" in caplog.text                # the stack frames are still logged
