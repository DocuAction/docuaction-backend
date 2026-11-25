"""The REAL intake contracts, exercised field by field - only where they exist.

The vendor layouts are not in this repository, and neither is anything that
names their columns. These tests run when `IQVIA_PRIVATE_CONTRACTS_DIR_FOR_TESTS`
names a private contracts directory; the facts they compare against (field
counts, the difference between two delivery editions, the pinned content digest
of every contract) are read from `private_test_expectations.json` beside that
directory. Without it every test here is skipped, with that reason.

The rows are still synthetic: generated from the contract field list. No
delivered data file is ever opened.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

import iqvia_contract_support as support
from app.tefca_registry.rce import iqvia_contract_registry as cr
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as sm
from iqvia_contract_support import AFFIL, HCO, HCP
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401

PRIVATE = support.private_contracts_dir()
EXPECTATIONS = None if PRIVATE is None else PRIVATE.parent / "private_test_expectations.json"

pytestmark = [
    pytest.mark.regression,
    pytest.mark.skipif(
        PRIVATE is None or not EXPECTATIONS.is_file(),
        reason=f"the private IQVIA contracts are not configured ({support.PRIVATE_DIR_ENV} is unset, or "
               "private_test_expectations.json is missing beside it); the vendor layouts are not in "
               "this repository"),
]


@pytest.fixture
def expected():
    return json.loads(EXPECTATIONS.read_text(encoding="utf-8"))


@pytest.fixture
def private_contracts(monkeypatch):
    support.use_contracts(monkeypatch, PRIVATE)
    registry = cr.get_registry()
    assert registry.state == cr.STATE_LOADED, registry.error
    return registry


def newest_and_previous(registry, source, expected):
    newest = support.contract_for(source, expected["editions"][-1])
    previous = support.contract_for(source, expected["editions"][0])
    return newest, previous


class TestTheRegisteredContracts:
    def test_field_counts_and_identities(self, private_contracts, expected):
        counts = {s: len(private_contracts.newest(s).fields) for s in support.SOURCES}
        assert counts == expected["field_counts"]
        assert sum(counts.values()) == expected["total_source_specific_fields"]
        identities = [(c.source_system, c.layout_sha256) for c in private_contracts.contracts()]
        assert len(set(identities)) == len(identities)

    def test_no_contract_file_has_been_edited_in_place(self, private_contracts, expected):
        """Content digests (canonical JSON, so line endings do not matter)."""
        found = {p.name: cr.canonical_sha256(json.loads(p.read_text(encoding="utf-8")))
                 for p in PRIVATE.glob("*.json") if p.name != cr.MANIFEST_NAME}
        assert found == expected["canonical_sha256"]

    def test_a_numeric_field_never_carries_a_length(self, private_contracts):
        for contract in private_contracts.contracts():
            assert all(f.vendor_length is None for f in contract.fields if f.vendor_type == "Numeric")
            assert all(f.vendor_length is not None for f in contract.fields if f.vendor_type == "Text")

    @pytest.mark.parametrize("source", support.SOURCES)
    def test_the_difference_between_the_two_editions(self, private_contracts, expected, source):
        newest, previous = newest_and_previous(private_contracts, source, expected)
        want = expected["edition_diff"][source]
        if want == "identical":
            assert newest.layout_sha256 == previous.layout_sha256 and len(newest.editions) == 2
            return
        diff = cr.compatibility_diff(list(newest.names), previous)
        assert diff["added"]["names"] == want["added"] and diff["missing"]["names"] == want["missing"]
        assert diff["reordered"]["is_reordered"] is False

    @pytest.mark.parametrize("source", support.SOURCES)
    def test_each_edition_header_binds_to_its_own_contract(self, private_contracts, expected, source):
        for edition in expected["editions"]:
            contract = support.contract_for(source, edition)
            binding = cr.bind_header(private_contracts, source, list(contract.names))
            assert binding["outcome"] == cr.OUTCOME_EXACT
            assert binding["contract"]["layout_sha256"] == contract.layout_sha256


class TestEveryRealFieldIsPreserved:
    @pytest.mark.parametrize("source", [HCO, HCP])
    @pytest.mark.parametrize("fill", [False, True])
    async def test_each_field_round_trips_unchanged_into_the_payload(
            self, rolled_back_db, private_contracts, expected, tmp_path, source, fill):
        db = rolled_back_db
        contract = private_contracts.newest(source)
        rows = [support.contract_row(contract, n, fill=fill) for n in (1, 2)]
        f = support.write_contract_file(tmp_path / f"{source}-{fill}.csv", contract, rows)
        done = await getattr(ii, support.IMPORTERS[source])(db, file_path=f, label=f"{SYN}-p-{source}-{fill}",
                                                           created_by=SYN)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (2, 2, 0)
        model = support.MODELS[source]
        stored = (await db.execute(select(model).where(model.source_snapshot_id == done.snapshot_id))).scalars().all()
        by_first_key = {o.payload[contract.names[0]]: o for o in stored}
        fields_checked = set()
        for row in rows:
            payload = by_first_key[row[contract.names[0]]].payload
            assert payload == row and all(isinstance(v, str) for v in payload.values())
            fields_checked |= set(payload)
        assert len(fields_checked) == expected["field_counts"][source]
        if fill:
            longest = max(len(v) for v in rows[0].values())
            assert longest == max(f_.vendor_length or 0 for f_ in contract.fields)
            for name in expected.get("five_thousand_character_columns", {}).get(source, []):
                assert len(by_first_key[rows[0][contract.names[0]]].payload[name]) == 5000
        snapshot = await db.get(sm.SourceSnapshot, done.snapshot_id)
        await db.refresh(snapshot)
        assert snapshot.metadata_["contract_binding"]["outcome"] == cr.OUTCOME_EXACT
        validation = snapshot.metadata_["import_validation"]
        assert validation["over_vendor_length"]["cells"] == 0
        assert validation["numeric_not_plain_decimal"]["cells"] == 0
        assert (await ii.verify_snapshot_staged_completely(db, done.snapshot_id))["reconciles"] is True

    async def test_affiliation_projection_round_trips_and_every_other_field_is_accounted_for(
            self, rolled_back_db, private_contracts, expected, tmp_path):
        db = rolled_back_db
        contract = private_contracts.newest(AFFIL)
        rows = [support.contract_row(contract, n, fill=True) for n in (1, 2)]
        f = support.write_contract_file(tmp_path / "affil.csv", contract, rows)
        done = await ii.import_affiliation_csv(db, file_path=f, label=f"{SYN}-p-affil", created_by=SYN)
        assert (done.rows_read, done.rows_staged, done.rows_rejected) == (2, 2, 0)
        projection = set(ii.affiliation_payload_columns())
        assert projection <= set(contract.names)
        stored = {o.hcp_record_key: o for o in (await db.execute(select(sm.IqviaAffiliationObservation).where(
            sm.IqviaAffiliationObservation.source_snapshot_id == done.snapshot_id))).scalars().all()}
        in_payload, file_and_hash_only = set(), set()
        for row in rows:
            obs = stored[row["HCP_HCE_ID"]]
            assert obs.payload == {c: row[c] for c in contract.names if c in projection}
            assert obs.record_sha256 == ii._row_sha256(row)
            for column in contract.names:
                if column in projection:
                    in_payload.add(column)
                else:
                    assert column not in obs.payload
                    assert ii._row_sha256({**row, column: row[column] + "~"}) != obs.record_sha256
                    file_and_hash_only.add(column)
        assert len(in_payload) == len(projection) == 25
        assert len(in_payload) + len(file_and_hash_only) == expected["field_counts"][AFFIL]
        assert (await ii.verify_snapshot_staged_completely(db, done.snapshot_id))["reconciles"] is True
