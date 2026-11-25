"""What an import stores, cell for cell, and what it records about the file
(2026-11-25). Database-backed. Synthetic contracts and synthetic rows only.

The rule under test: the importer preserves. No value is trimmed, converted,
split or re-typed on its way into `payload`; identifiers stay text; and what is
recorded about the file is column names, counts and record numbers.
"""
from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import select

import iqvia_contract_support as support
from app.tefca_registry.rce import iqvia_contract_registry as cr
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as sm
from iqvia_contract_support import AFFIL, EDITION_1, EDITION_2, HCO, HCP
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401

pytestmark = pytest.mark.regression

SENTINEL = "SENTINEL-VALUE-7Q4Z"


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


async def metadata_of(db, snapshot_id):
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    await db.refresh(snapshot)
    return snapshot.metadata_


def key_of(source, row):
    if source == HCO:
        return row["HCO_HCE_ID"]
    return f'{row["HCP_HCE_ID"]}:{row["ADDR_ID"]}'


class TestEveryFieldIsPreserved:
    @pytest.mark.parametrize("source", [HCO, HCP])
    @pytest.mark.parametrize("fill", [False, True])
    async def test_each_field_round_trips_unchanged_into_the_payload(
            self, rolled_back_db, contracts, tmp_path, source, fill):
        db = rolled_back_db
        contract = support.contract_for(source, EDITION_2)
        rows = [support.contract_row(contract, n, fill=fill) for n in (1, 2, 3)]
        f = support.write_contract_file(tmp_path / f"{source}-{fill}.csv", contract, rows)
        done = await run_import(db, source, f)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (3, 3, 0)
        stored = {r.source_record_key: r for r in await stored_rows(db, source, done.snapshot_id)}
        checked = set()
        for row in rows:
            payload = stored[key_of(source, row)].payload
            assert payload == row                       # every column, exactly, and nothing else
            assert all(isinstance(v, str) for v in payload.values())
            checked |= set(payload)
        assert checked == set(contract.names)

    async def test_affiliation_stores_the_projection_and_hashes_the_whole_row(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(AFFIL, EDITION_2)
        rows = [support.contract_row(contract, n, fill=True) for n in (1, 2)]
        f = support.write_contract_file(tmp_path / "affil.csv", contract, rows)
        done = await run_import(db, AFFIL, f)
        assert (done.rows_read, done.rows_staged) == (2, 2)
        projection = set(ii.affiliation_payload_columns())
        stored = {r.hcp_record_key: r for r in await stored_rows(db, AFFIL, done.snapshot_id)}
        accounted = set()
        for row in rows:
            obs = stored[row["HCP_HCE_ID"]]
            in_payload = {c for c in contract.names if c in projection}
            assert obs.payload == {c: row[c] for c in in_payload}
            # Every other column is in the original file and the row hash only.
            assert obs.record_sha256 == ii._row_sha256(row)
            for column in set(contract.names) - in_payload:
                assert column not in obs.payload
                assert ii._row_sha256({**row, column: row[column] + "~"}) != obs.record_sha256
            accounted |= in_payload | (set(contract.names) - in_payload)
        assert accounted == set(contract.names)
        assert {"SYN_UNSTORED_NOTE", "SYN_UNSTORED_COUNT", "SYN_UNSTORED_LIST",
                "SYN_UNSTORED_ADDED"} <= set(contract.names) - projection

    async def test_awkward_values_are_stored_exactly_as_delivered(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        list_5000 = ",".join(support.synthetic_npi(n) for n in range(455))[:5000]
        assert len(list_5000) == 5000
        awkward = {
            "ZIP5_CD": "00501", "ORG_CCN_ID": "012345",                 # leading zeros
            "SYN_BIG_ID": "9" * 40,                                     # far beyond 2**63
            "SYN_COUNT": "0012.50",                                     # decimal text, zeros kept
            "SYN_NOTE": "",                                             # empty cell
            "SYN_CODE": " a ",                                          # spaces kept
            "BUS_NM": 'Café "Niño", São\r\nLine two 日本',   # quote, comma, newline, non-ASCII
            "SYN_LONG_LIST": list_5000,
        }
        rows = [support.contract_row(contract, 1, overrides=awkward),
                support.contract_row(contract, 2, overrides={"SYN_COUNT": "TWELVE", "SYN_BIG_ID": "1e99"})]
        f = support.write_contract_file(tmp_path / "awkward.csv", contract, rows)
        done = await run_import(db, HCO, f)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (2, 2, 0)
        stored = {r.source_record_key: r for r in await stored_rows(db, HCO, done.snapshot_id)}
        first = stored[rows[0]["HCO_HCE_ID"]]
        for column, value in awkward.items():
            assert first.payload[column] == value
        assert first.payload["SYN_LONG_LIST"] == list_5000 and isinstance(first.payload["SYN_LONG_LIST"], str)
        assert isinstance(first.payload["SYN_BIG_ID"], str) and first.payload["SYN_NOTE"] == ""
        assert first.ccn == "012345"
        second = stored[rows[1]["HCO_HCE_ID"]]
        assert (second.payload["SYN_COUNT"], second.payload["SYN_BIG_ID"]) == ("TWELVE", "1e99")
        validation = (await metadata_of(db, done.snapshot_id))["import_validation"]
        not_decimal = {c["column"]: (c["count"], c["record_numbers"])
                       for c in validation["numeric_not_plain_decimal"]["by_column"]}
        assert not_decimal == {"SYN_COUNT": (1, [2]), "SYN_BIG_ID": (1, [2])}
        assert validation["over_vendor_length"]["cells"] == 0

    async def test_a_value_one_over_the_vendor_length_is_stored_whole_and_counted(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        row = support.contract_row(contract, 1, fill=True)
        row["SYN_NOTE"] += "!"
        row["SYN_LONG_LIST"] += "9"
        f = support.write_contract_file(tmp_path / "over.csv", contract, [row])
        done = await run_import(db, HCO, f)
        obs = (await stored_rows(db, HCO, done.snapshot_id))[0]
        assert len(obs.payload["SYN_NOTE"]) == 9 and len(obs.payload["SYN_LONG_LIST"]) == 5001
        report = (await metadata_of(db, done.snapshot_id))["import_validation"]["over_vendor_length"]
        assert {(c["column"], c["longest_characters"]) for c in report["by_column"]} == {
            ("SYN_NOTE", 9), ("SYN_LONG_LIST", 5001)}

    async def test_a_byte_order_mark_changes_nothing_stored(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCP, EDITION_2)
        row = support.contract_row(contract, 1)
        f = support.write_contract_file(tmp_path / "bom.csv", contract, [row], bom=True)
        done = await run_import(db, HCP, f)
        assert (await stored_rows(db, HCP, done.snapshot_id))[0].payload == row
        meta = await metadata_of(db, done.snapshot_id)
        assert meta["contract_binding"]["outcome"] == cr.OUTCOME_EXACT
        assert meta["source_header"]["names"][0] == "HCP_HCE_ID"


class TestWhatIsRecordedAboutTheFile:
    async def test_header_binding_and_summary_are_on_the_snapshot(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        rows = [support.contract_row(contract, n) for n in range(1, 8)]
        rows[2]["HCO_HCE_ID"] = ""                                   # rejected: no key
        f = support.write_contract_file(tmp_path / "rec.csv", contract, rows)
        done = await run_import(db, HCO, f)
        meta = await metadata_of(db, done.snapshot_id)
        assert meta["source_header"]["names"] == list(contract.names)
        assert meta["source_header"]["names_sha256"] == cr.names_sha256(contract.names)
        binding = meta["contract_binding"]
        assert binding["outcome"] == cr.OUTCOME_EXACT and binding["bound_by"] == "column name"
        assert binding["contract"]["layout_sha256"] == contract.layout_sha256
        assert meta["reference_preflight"]["gate"] == "CLEAR"
        validation = meta["import_validation"]
        assert validation["rows"] == {"read": 7, "inserted": 6, "already_present": 0, "rejected": 1}
        assert validation["scope"].startswith("full file") and validation["completed"] is True
        assert validation["measured_against"]["contract_id"] == contract.contract_id
        assert validation["record_width"] == {"header_columns": len(contract.names), "short_records": 0,
                                              "short_record_numbers": [], "long_records": 0,
                                              "long_record_numbers": []}
        assert validation["lifted_identifiers"]["npi"]["lifted"] == 6
        # Not inside anything the staging token reads.
        assert "import_validation" not in json.dumps(meta.get("import_summary") or {})
        assert set(meta) >= {"source_header", "contract_binding", "import_validation", "reference_preflight"}

    async def test_the_binding_is_committed_with_the_preflight_before_any_row(
            self, rolled_back_db, contracts, tmp_path, monkeypatch):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        f = support.write_contract_file(tmp_path / "early.csv", contract,
                                        [support.contract_row(contract, n) for n in (1, 2)])
        seen = {}

        async def stop_before_staging(db_, *, snapshot_id, model, rows):
            meta = await metadata_of(db_, snapshot_id)
            seen.update(meta)
            raise RuntimeError("stopped by the test")

        monkeypatch.setattr(ii, "_stage_rows", stop_before_staging)
        with pytest.raises(RuntimeError):
            await run_import(db, HCO, f)
        assert {"reference_preflight", "source_header", "contract_binding"} <= set(seen)
        assert "import_validation" not in seen               # written once, at the end only

    async def test_an_older_edition_binds_to_its_own_contract(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_1)
        f = support.write_contract_file(tmp_path / "old.csv", contract, [support.contract_row(contract, 1)])
        done = await run_import(db, HCO, f)
        binding = (await metadata_of(db, done.snapshot_id))["contract_binding"]
        assert binding["outcome"] == cr.OUTCOME_EXACT and binding["contract"]["editions"] == [EDITION_1]

    async def test_reordered_columns_are_mapped_by_name(self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        header = list(reversed(contract.names))
        row = support.contract_row(contract, 1)
        f = support.write_contract_file(tmp_path / "reordered.csv", contract, [row], header=header)
        done = await run_import(db, HCO, f)
        obs = (await stored_rows(db, HCO, done.snapshot_id))[0]
        assert obs.payload == row and obs.npi == row["ORG_NPI"] and obs.ccn == row["ORG_CCN_ID"]
        # The row hash does not depend on the column order of the file.
        assert obs.record_sha256 == ii._row_sha256(row)
        meta = await metadata_of(db, done.snapshot_id)
        assert meta["contract_binding"]["outcome"] == cr.OUTCOME_REORDERED
        assert meta["source_header"]["names"] == header

    async def test_a_future_column_is_preserved_and_reported_as_a_difference(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        header = [n for n in contract.names if n != "SYN_CODE"] + ["SYN_FUTURE_COLUMN"]
        row = support.contract_row(contract, 1, overrides={"SYN_FUTURE_COLUMN": "kept"})
        f = support.write_contract_file(tmp_path / "future.csv", contract, [row], header=header)
        done = await run_import(db, HCO, f)
        assert (done.rows_read, done.rows_staged) == (1, 1)
        obs = (await stored_rows(db, HCO, done.snapshot_id))[0]
        assert obs.payload == {n: row[n] for n in header}
        binding = (await metadata_of(db, done.snapshot_id))["contract_binding"]
        assert binding["outcome"] == cr.OUTCOME_UNREGISTERED
        assert binding["diff"]["added"]["names"] == ["SYN_FUTURE_COLUMN"]
        assert binding["diff"]["missing"]["names"] == ["SYN_CODE"]
        assert "IQVIA_CONTRACTS_DIR" in binding["operator_message"]
        validation = (await metadata_of(db, done.snapshot_id))["import_validation"]
        assert validation["measured_against"]["columns_without_contract_field"] == 1

    async def test_with_no_contract_directory_rows_are_staged_and_the_file_is_unregistered(
            self, rolled_back_db, tmp_path, monkeypatch):
        db = rolled_back_db
        support.use_contracts(monkeypatch, None)
        f = support.write_cells(tmp_path / "none.csv", ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM"],
                                [["90000001", "", "", "NAME"]])
        done = await run_import(db, HCO, f)
        assert (done.rows_read, done.rows_staged) == (1, 1)
        meta = await metadata_of(db, done.snapshot_id)
        assert meta["contract_binding"]["outcome"] == cr.OUTCOME_UNREGISTERED
        assert meta["contract_binding"]["registry"]["state"] == cr.STATE_NOT_CONFIGURED
        assert meta["import_validation"]["measured_against"]["contract_id"] is None


class TestLegitimateRepeatsAreDistinctRows:
    async def test_one_practitioner_with_several_addresses_stages_every_address(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(HCP, EDITION_2)
        rows = [support.contract_row(contract, n, overrides={"HCP_HCE_ID": "90000001", "NPI": support.synthetic_npi(5)})
                for n in range(1, 6)]
        assert len({r["ADDR_ID"] for r in rows}) == 5
        f = support.write_contract_file(tmp_path / "addr.csv", contract, rows)
        done = await run_import(db, HCP, f)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged, done.rows_rejected) == (5, 5, 0, 0)
        stored = await stored_rows(db, HCP, done.snapshot_id)
        assert {o.source_record_key for o in stored} == {f'90000001:{r["ADDR_ID"]}' for r in rows}
        assert (await ii.verify_snapshot_staged_completely(db, done.snapshot_id))["staged_row_count"] == 5

    async def test_one_practitioner_with_several_affiliations_stages_every_relationship(
            self, rolled_back_db, contracts, tmp_path):
        db = rolled_back_db
        contract = support.contract_for(AFFIL, EDITION_2)
        base = {"HCP_HCE_ID": "90000001"}
        rows = [
            support.contract_row(contract, 1, overrides={**base, "HCO_HCE_ID": "80000001", "AFFL_TYP_ID": "11", "TITL_TYP_ID": ""}),
            support.contract_row(contract, 2, overrides={**base, "HCO_HCE_ID": "80000002", "AFFL_TYP_ID": "11", "TITL_TYP_ID": ""}),
            support.contract_row(contract, 3, overrides={**base, "HCO_HCE_ID": "80000001", "AFFL_TYP_ID": "12", "TITL_TYP_ID": ""}),
            support.contract_row(contract, 4, overrides={**base, "HCO_HCE_ID": "80000001", "AFFL_TYP_ID": "", "TITL_TYP_ID": "7"}),
        ]
        f = support.write_contract_file(tmp_path / "rel.csv", contract, rows)
        done = await run_import(db, AFFIL, f)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged, done.rows_rejected) == (4, 4, 0, 0)
        stored = await stored_rows(db, AFFIL, done.snapshot_id)
        assert {(o.hcp_record_key, o.hco_record_key, o.affiliation_type) for o in stored} == {
            ("90000001", "80000001", "11"), ("90000001", "80000002", "11"),
            ("90000001", "80000001", "12"), ("90000001", "80000001", "CONTACT:7")}


class TestResume:
    async def test_an_interrupted_import_resumes_and_the_summary_covers_the_whole_file(
            self, rolled_back_db, contracts, tmp_path, monkeypatch):
        db = rolled_back_db
        contract = support.contract_for(HCO, EDITION_2)
        rows = [support.contract_row(contract, n) for n in range(1, 11)]
        rows[8]["SYN_COUNT"] = "not-a-number"
        f = support.write_contract_file(tmp_path / "resume.csv", contract, rows)
        real, calls = ii._stage_rows, {"n": 0}

        async def second_chunk_fails(db_, *, snapshot_id, model, rows):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("interrupted by the test")
            return await real(db_, snapshot_id=snapshot_id, model=model, rows=rows)

        monkeypatch.setattr(ii, "_stage_rows", second_chunk_fails)
        snapshot_id = None
        with pytest.raises(RuntimeError):
            await run_import(db, HCO, f, chunk_size=4)
        await db.rollback()
        snapshot_id = (await db.execute(select(sm.SourceSnapshot.id).where(
            sm.SourceSnapshot.snapshot_label == f"{SYN}-resume"))).scalar_one()
        before = {o.source_record_key: (o.id, o.record_sha256, o.payload)
                  for o in await stored_rows(db, HCO, snapshot_id)}
        assert len(before) == 4
        assert "import_validation" not in await metadata_of(db, snapshot_id)

        monkeypatch.setattr(ii, "_stage_rows", real)
        done = await run_import(db, HCO, f, chunk_size=4, snapshot_id=snapshot_id)
        assert (done.rows_read, done.rows_staged, done.rows_already_staged) == (10, 6, 4)
        after = {o.source_record_key: (o.id, o.record_sha256, o.payload)
                 for o in await stored_rows(db, HCO, snapshot_id)}
        assert len(after) == 10 and all(after[k] == v for k, v in before.items())   # nothing rewritten
        validation = (await metadata_of(db, snapshot_id))["import_validation"]
        assert validation["rows"] == {"read": 10, "inserted": 6, "already_present": 4, "rejected": 0}
        assert validation["numeric_not_plain_decimal"]["by_column"][0]["record_numbers"] == [9]
        assert validation["records_measured_per_column"] == 10


class TestNoValueIsRecordedOrLogged:
    @pytest.mark.parametrize("source", [HCO, HCP, AFFIL])
    async def test_a_sentinel_in_every_cell_appears_in_no_log_and_no_metadata(
            self, rolled_back_db, contracts, tmp_path, caplog, source):
        db = rolled_back_db
        contract = support.contract_for(source, EDITION_2)
        keep = {"HCO_HCE_ID", "HCP_HCE_ID", "ADDR_ID"}
        rows = []
        for n in (1, 2, 3):
            row = support.contract_row(contract, n)
            rows.append({c: (v if c in keep else f"{SENTINEL}-{c}-{n}") for c, v in row.items()})
        rows.append({c: "" if c in keep else SENTINEL for c in contract.names})      # rejected row
        f = support.write_contract_file(tmp_path / f"sentinel-{source}.csv", contract, rows)
        caplog.set_level(logging.DEBUG)
        try:
            done = await run_import(db, source, f)
            snapshot_id = done.snapshot_id
        except Exception as exc:          # noqa: BLE001 - an over-long lifted value still fails today
            assert source in (HCO, HCP)
            await db.rollback()
            snapshot_id = (await db.execute(select(sm.SourceSnapshot.id).where(
                sm.SourceSnapshot.snapshot_label == f"{SYN}-sentinel-{source}"))).scalar_one()
            del exc
        meta = await metadata_of(db, snapshot_id)
        assert all("SENTINEL" not in text for text in support.all_strings(meta))
        assert "SENTINEL" not in caplog.text
