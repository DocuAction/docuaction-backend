"""What the IQVIA importer did with awkward files BEFORE the intake gates
(2026-11-25) - and still does for any snapshot whose rows were staged before
them.

Every value is synthetic. Each test states a behaviour, not an opinion of it.
First written against the unchanged importer (commit "pin what the importer
does today"). The gates then changed several of these for NEW imports only, so
the tests marked `before the gates` now resume a snapshot that already holds a
row and carries no gate marker: the old behaviour must be exactly preserved
there. What a new import does instead is in
tests/test_iqvia_intake_gates_2026_11_25.py.
"""
from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as sm
from iqvia_contract_support import snapshot_staged_before_the_gates as staged_before
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression

HCO_HEADER = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]
HCP_HEADER = ["HCP_HCE_ID", "ADDR_ID", "NPI", "LAST_NM", "ZIP5_CD"]


def write_raw(path, text: str, *, bom: bool = False):
    """The bytes exactly as given (CRLF is the caller's business)."""
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


def csv_text(header, rows) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return out.getvalue()


async def hco_rows(db, snapshot_id):
    rows = (await db.execute(select(sm.IqviaHcoObservation).where(
        sm.IqviaHcoObservation.source_snapshot_id == snapshot_id))).scalars().all()
    return {r.source_record_key: r for r in rows}


class TestHeaderAndRowShapeToday:
    async def test_a_duplicate_header_name_collapses_and_the_last_cell_wins(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_raw(tmp_path / "dup.csv", csv_text(
            ["HCO_HCE_ID", "ORG_NPI", "BUS_NM", "BUS_NM"],
            [[f"{SYN}-D0", "", "A", "B"], [f"{SYN}-D1", "", "FIRST-CELL", "SECOND-CELL"]]))
        before = await staged_before(db, sm.SOURCE_IQVIA_HCO, f)          # before the gates
        done = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                       snapshot_id=before.id)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged, done.rows_rejected) == (2, 1, 1, 0)
        row = (await hco_rows(db, done.snapshot_id))[f"{SYN}-D1"]
        # Three keys for a four-column record: the first BUS_NM cell is in no stored column.
        assert row.payload == {"HCO_HCE_ID": f"{SYN}-D1", "ORG_NPI": "", "BUS_NM": "SECOND-CELL"}
        # The hash is of the collapsed row, so it does not cover the lost cell either.
        assert row.record_sha256 == ii._row_sha256(dict(row.payload))
        snap = await db.get(sm.SourceSnapshot, done.snapshot_id)
        codes = {x["code"] for x in snap.metadata_["reference_preflight"]["findings"]}
        assert "REF-SCH-003" in codes and snap.metadata_["reference_preflight"]["gate"] == "BLOCKED"

    async def test_a_short_row_stores_json_null_for_the_missing_cells(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_raw(tmp_path / "short.csv",
                      ",".join(HCO_HEADER) + "\r\n"
                      + f"{SYN}-S0,,,NAME-ZERO,02101\r\n"
                      + f"{SYN}-S1,,,NAME-ONE\r\n"          # 4 cells: ZIP5_CD missing
                      + f"{SYN}-S2,,,NAME-TWO,\r\n")        # 5 cells: ZIP5_CD present and empty
        before = await staged_before(db, sm.SOURCE_IQVIA_HCO, f)          # before the gates
        done = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN,
                                       snapshot_id=before.id)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (3, 2, 0)
        rows = await hco_rows(db, done.snapshot_id)
        assert "ZIP5_CD" in rows[f"{SYN}-S1"].payload and rows[f"{SYN}-S1"].payload["ZIP5_CD"] is None
        assert rows[f"{SYN}-S2"].payload["ZIP5_CD"] == ""

    async def test_a_long_row_fails_the_import_with_a_type_error(self, rolled_back_db, tmp_path):
        """DictReader puts the extra cells in a list under the key None; the row
        hash sorts the keys and cannot compare None with a string."""
        db = rolled_back_db
        f = write_raw(tmp_path / "long.csv",
                      ",".join(HCO_HEADER) + "\r\n"
                      + f"{SYN}-L1,,,NAME-ONE,02101\r\n"
                      + f"{SYN}-L2,,,NAME-TWO,02101,EXTRA-CELL\r\n")
        before = await staged_before(db, sm.SOURCE_IQVIA_HCO, f)          # before the gates
        with pytest.raises(TypeError):
            await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=before.id)

    async def test_blank_lines_are_skipped_and_reported_lines_are_record_ordinals(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_raw(tmp_path / "blank.csv",
                      ",".join(HCO_HEADER) + "\r\n"
                      + f"{SYN}-B1,,,NAME-ONE,02101\r\n"
                      + "\r\n"
                      + "\r\n"
                      + ",,,NO-KEY,02101\r\n"               # physical line 5
                      + f"{SYN}-B2,,,NAME-TWO,02101\r\n")
        done = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-blank", created_by=SYN)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (3, 2, 1)
        # The rejected record is on physical line 5; it is reported as 3
        # (second data record + 1): blank lines are not counted.
        assert done.rejected_sample_lines == [3]

    async def test_a_byte_order_mark_is_not_part_of_the_first_column_name(self, rolled_back_db, tmp_path):
        """On main (before PR #216) the mark stayed in the name and every row was
        rejected; this branch is built on #216, which reads it away."""
        db = rolled_back_db
        f = write_raw(tmp_path / "bom.csv", csv_text(HCO_HEADER, [[f"{SYN}-M1", "", "", "N", "02101"]]),
                      bom=True)
        done = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-bom", created_by=SYN)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (1, 1, 0)
        row = (await hco_rows(db, done.snapshot_id))[f"{SYN}-M1"]
        assert set(row.payload) == set(HCO_HEADER)


class TestLiftedIdentifiersToday:
    @pytest.mark.parametrize("npi,ccn", [("12345678901", ""),            # 11 > varchar(10)
                                         ("", "C" * 21)])                # 21 > varchar(20)
    async def test_an_over_long_hco_identifier_makes_postgresql_refuse_the_statement(
            self, rolled_back_db, tmp_path, npi, ccn):
        db = rolled_back_db
        f = write_raw(tmp_path / "wide.csv", csv_text(HCO_HEADER, [
            [f"{SYN}-W1", "", "", "FITS", "02101"],
            [f"{SYN}-W2", npi, ccn, "TOO-WIDE", "02101"]]))
        before = await staged_before(db, sm.SOURCE_IQVIA_HCO, f)          # before the gates
        with pytest.raises(DBAPIError) as caught:
            await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=before.id)
        assert "value too long" in str(caught.value.orig)
        await db.rollback()

    async def test_an_over_long_hcp_npi_makes_postgresql_refuse_the_statement(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_raw(tmp_path / "hcp.csv", csv_text(HCP_HEADER, [
            [f"{SYN}-P0", "A0", "", "NAME", "02101"],
            [f"{SYN}-P1", "A1", "12345678901", "NAME", "02101"]]))
        before = await staged_before(db, sm.SOURCE_IQVIA_HCP, f)          # before the gates
        with pytest.raises(DBAPIError):
            await ii.import_hcp_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=before.id)
        await db.rollback()

    async def test_the_database_error_text_carries_the_row_values(self, rolled_back_db, tmp_path):
        """The exception's own text includes the statement parameters, i.e. field
        values. Anything built from str(exc) (a job failure reason, a log line
        with the traceback) can therefore carry a value."""
        db = rolled_back_db
        sentinel = "SENTINEL-VALUE-7Q4Z"
        f = write_raw(tmp_path / "leak.csv", csv_text(HCO_HEADER, [
            [f"{SYN}-K0", "", "", "FITS", "02101"],
            [f"{SYN}-K1", "12345678901", "", sentinel, "02101"]]))
        before = await staged_before(db, sm.SOURCE_IQVIA_HCO, f)          # before the gates
        with pytest.raises(DBAPIError) as caught:
            await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=before.id)
        assert sentinel in str(caught.value)
        await db.rollback()

    async def test_surrounding_spaces_are_stripped_for_the_lifted_column_only(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_raw(tmp_path / "pad.csv", csv_text(HCO_HEADER, [
            [f"{SYN}-T1", " 1234567893 ", " 012345 ", "NAME", "02101"]]))
        done = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-pad", created_by=SYN)
        row = (await hco_rows(db, done.snapshot_id))[f"{SYN}-T1"]
        assert (row.npi, row.ccn) == ("1234567893", "012345")
        assert (row.payload["ORG_NPI"], row.payload["ORG_CCN_ID"]) == (" 1234567893 ", " 012345 ")


class TestKeyConflictsToday:
    async def test_two_different_rows_sharing_a_key_in_one_file_read_as_already_staged(
            self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_raw(tmp_path / "collide.csv", csv_text(HCO_HEADER, [
            [f"{SYN}-C1", "", "", "FIRST-CONTENT", "02101"],
            [f"{SYN}-C1", "", "", "OTHER-CONTENT", "90210"]]))
        done = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-collide", created_by=SYN)
        # A fresh import, yet one row is reported as "already staged".
        assert (done.rows_read, done.rows_staged, done.rows_already_staged, done.rows_rejected) == (2, 1, 1, 0)
        rows = await hco_rows(db, done.snapshot_id)
        assert rows[f"{SYN}-C1"].payload["BUS_NM"] == "FIRST-CONTENT"   # the second row is in no table
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        assert verdict["reconciles"] is False
        # Before the gates this was the ONLY reason, and it did not say why. A new import now
        # also names the collision (tests/test_iqvia_intake_gates_2026_11_25.py).
        assert verdict["refusal_reasons"][0] == "staged row count does not equal read-minus-rejected"
        assert any("cannot hold both rows" in r for r in verdict["refusal_reasons"][1:])

    async def test_an_exact_duplicate_row_blocks_reconciliation_the_same_way(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        same = [f"{SYN}-E1", "", "", "SAME", "02101"]
        f = write_raw(tmp_path / "dupe.csv", csv_text(HCO_HEADER, [same, same]))
        done = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-dupe", created_by=SYN)
        assert (done.rows_staged, done.rows_already_staged) == (1, 1)
        verdict = await ii.verify_snapshot_staged_completely(db, done.snapshot_id)
        assert verdict["reconciles"] is False
