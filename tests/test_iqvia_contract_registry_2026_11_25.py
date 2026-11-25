"""The intake-contract registry, header binding and the pure validation
counters (2026-11-25). No database. Every contract here is synthetic: invented
columns registered in a temporary directory by the real generator script.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import shutil
import types

import pytest

import iqvia_contract_support as support
from app.tefca_registry.rce import iqvia_contract_registry as cr
from app.tefca_registry.rce import iqvia_intake_validation as intake
from app.tefca_registry.rce import iqvia_reconciliation as rec
from iqvia_contract_support import AFFIL, EDITION_1, EDITION_2, HCO, HCP

pytestmark = pytest.mark.regression

SENTINEL = "SENTINEL-VALUE-7Q4Z"


@pytest.fixture
def contracts(tmp_path, monkeypatch):
    directory = support.build_synthetic_contracts(tmp_path / "contracts")
    support.use_contracts(monkeypatch, directory)
    return directory


def names(source, edition=EDITION_2):
    return list(support.contract_for(source, edition).names)


class TestWhereContractsComeFrom:
    def test_nothing_is_registered_when_the_setting_is_not_set(self, monkeypatch):
        support.use_contracts(monkeypatch, None)
        registry = cr.get_registry()
        assert registry.state == cr.STATE_NOT_CONFIGURED and registry.contracts() == ()
        binding = cr.bind_header(registry, HCO, ["HCO_HCE_ID", "ORG_NPI"])
        assert binding["outcome"] == cr.OUTCOME_UNREGISTERED and binding["diff"] is None
        assert "IQVIA_CONTRACTS_DIR is not set" in binding["operator_message"]
        assert "No re-upload is needed" in binding["operator_message"]

    def test_an_empty_or_missing_directory_registers_nothing_and_says_which(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        support.use_contracts(monkeypatch, empty)
        assert cr.get_registry().state == cr.STATE_EMPTY
        support.use_contracts(monkeypatch, tmp_path / "absent")
        registry = cr.get_registry()
        assert registry.state == cr.STATE_ERROR and "does not exist" in registry.error

    def test_the_generated_contracts_load_and_verify(self, contracts):
        registry = cr.get_registry()
        assert registry.state == cr.STATE_LOADED
        # HCP is one layout delivered in two editions: one identity, one contract.
        assert {s: len(registry.contracts(s)) for s in support.SOURCES} == {HCO: 2, HCP: 1, AFFIL: 2}
        assert registry.newest(HCP).editions == (EDITION_1, EDITION_2)
        assert len({(c.source_system, c.layout_sha256) for c in registry.contracts()}) == 5
        for contract in registry.contracts():
            assert len(contract.layout_sha256) == 64
            assert [f.ordinal for f in contract.fields] == list(range(1, len(contract.fields) + 1))

    def test_a_numeric_column_carries_no_length_and_nothing_else_is_stated(self, contracts):
        doc = json.loads((contracts / f"{HCO.lower()}_{EDITION_2}_v1.json").read_text(encoding="utf-8"))
        numeric = [f for f in doc["fields"] if f["vendor_type"] == "Numeric"]
        assert numeric and all(f["vendor_length"] is None for f in numeric)
        assert set(doc["fields"][0]) == {"ordinal", "name", "attribute_of", "vendor_type", "vendor_length"}
        assert "numeric range" in doc["not_established_by_vendor"]

    def test_registering_again_changes_no_contract_file(self, contracts, tmp_path):
        before = {p.name: p.read_bytes() for p in contracts.glob("*.json") if p.name != "manifest.json"}
        support.build_synthetic_contracts(contracts)
        after = {p.name: p.read_bytes() for p in contracts.glob("*.json") if p.name != "manifest.json"}
        assert before == after

    def test_a_different_layout_under_an_existing_file_name_is_refused(self, contracts, tmp_path):
        generator = support._generator()
        layout = support.write_layout_csv(tmp_path / "other_layout.csv",
                                          support.SYNTHETIC_LAYOUTS[HCO] + [("SYN_OTHER", "Text", 4)])
        target = contracts / f"{HCO.lower()}_{EDITION_1}_v1.json"
        before = target.read_bytes()
        with pytest.raises(SystemExit):
            generator.register(layout, HCO, EDITION_1, contracts)
        assert target.read_bytes() == before

    def test_identity_is_the_content_not_the_line_endings(self, contracts, tmp_path, monkeypatch):
        copy = tmp_path / "crlf"
        shutil.copytree(contracts, copy)
        digests = {}
        for path in copy.glob("*.json"):
            digests[path.name] = cr.canonical_sha256(json.loads(path.read_text(encoding="utf-8")))
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        support.use_contracts(monkeypatch, copy)
        assert cr.get_registry().state == cr.STATE_LOADED
        for path in copy.glob("*.json"):
            assert cr.canonical_sha256(json.loads(path.read_text(encoding="utf-8"))) == digests[path.name]

    @pytest.mark.parametrize("damage", ["rename_field", "drop_field", "stray_file", "manifest_sha"])
    def test_a_contract_set_that_fails_its_own_checks_registers_nothing(
            self, contracts, tmp_path, monkeypatch, damage):
        copy = tmp_path / "damaged"
        shutil.copytree(contracts, copy)
        target = copy / f"{HCO.lower()}_{EDITION_2}_v1.json"
        doc = json.loads(target.read_text(encoding="utf-8"))
        if damage == "rename_field":
            doc["fields"][3]["name"] = "SYN_EDITED_IN_PLACE"
            target.write_text(json.dumps(doc), encoding="utf-8")
        elif damage == "drop_field":
            doc["fields"].pop()
            target.write_text(json.dumps(doc), encoding="utf-8")
        elif damage == "stray_file":
            (copy / "unlisted_v1.json").write_text(json.dumps(doc), encoding="utf-8")
        else:
            manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
            manifest["contracts"][0]["layout_sha256"] = "0" * 64
            (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        support.use_contracts(monkeypatch, copy)
        registry = cr.get_registry()
        assert registry.state == cr.STATE_ERROR and registry.contracts() == ()
        binding = cr.bind_header(registry, HCO, ["HCO_HCE_ID"])
        assert binding["outcome"] == cr.OUTCOME_UNREGISTERED
        assert "could not be verified" in binding["operator_message"]


class TestBindingByName:
    def test_the_exact_header_binds_to_its_own_edition(self, contracts):
        registry = cr.get_registry()
        for edition in (EDITION_1, EDITION_2):
            binding = cr.bind_header(registry, HCO, names(HCO, edition))
            assert binding["outcome"] == cr.OUTCOME_EXACT and binding["diff"] is None
            assert binding["contract"]["editions"] == [edition]
            assert binding["contract"]["layout_sha256"] == support.contract_for(HCO, edition).layout_sha256

    def test_the_same_columns_in_another_order_bind_as_reordered(self, contracts):
        header = names(HCO)
        header[1], header[4] = header[4], header[1]
        binding = cr.bind_header(cr.get_registry(), HCO, header)
        assert binding["outcome"] == cr.OUTCOME_REORDERED
        assert binding["contract"]["editions"] == [EDITION_2]
        diff = binding["diff"]
        assert diff["added"]["count"] == 0 and diff["missing"]["count"] == 0
        assert diff["reordered"]["is_reordered"] and set(diff["reordered"]["first"]) >= {"ORG_NPI", "ZIP5_CD"}

    def test_a_future_layout_is_unregistered_with_a_diff_against_the_newest_contract(self, contracts):
        header = names(HCO) + ["SYN_FUTURE_COLUMN"]
        header.remove("SYN_CODE")
        binding = cr.bind_header(cr.get_registry(), HCO, header)
        assert binding["outcome"] == cr.OUTCOME_UNREGISTERED and binding["contract"] is None
        diff = binding["diff"]
        assert diff["against"]["editions"] == [EDITION_2]
        assert diff["added"]["names"] == ["SYN_FUTURE_COLUMN"] and diff["missing"]["names"] == ["SYN_CODE"]
        assert diff["reordered"]["is_reordered"] is False
        assert "IQVIA_CONTRACTS_DIR" in binding["operator_message"]

    def test_a_duplicated_column_never_binds(self, contracts):
        header = names(HCO) + ["BUS_NM"]
        binding = cr.bind_header(cr.get_registry(), HCO, header)
        assert binding["outcome"] == cr.OUTCOME_UNREGISTERED
        assert binding["diff"]["duplicate_names"]["names"] == ["BUS_NM"]

    def test_a_name_differing_only_in_case_or_spacing_is_not_the_column(self, contracts):
        header = names(HCO)
        header[3] = " bus_nm "
        binding = cr.bind_header(cr.get_registry(), HCO, header)
        assert binding["outcome"] == cr.OUTCOME_UNREGISTERED
        assert binding["diff"]["missing"]["names"] == ["BUS_NM"] and binding["diff"]["added"]["count"] == 1

    def test_the_difference_between_two_registered_editions(self, contracts):
        registry = cr.get_registry()
        for source, added in ((HCO, ["SYN_ADDED_ONE", "SYN_ADDED_TWO"]), (AFFIL, ["SYN_UNSTORED_ADDED"])):
            diff = cr.compatibility_diff(names(source, EDITION_2), support.contract_for(source, EDITION_1))
            assert diff["added"]["names"] == added and diff["missing"]["count"] == 0
            assert diff["reordered"]["is_reordered"] is False
        assert len(registry.contracts(HCP)) == 1          # identical: nothing to diff

    def test_binding_is_a_pure_function_of_the_stored_header(self, contracts):
        registry = cr.get_registry()
        stored = cr.describe_header(registry, HCO, names(HCO))
        assert stored["names_verbatim"] and stored["names_sha256"] == cr.names_sha256(names(HCO))
        assert stored["names_sha256"] == json.loads(
            (contracts / f"{HCO.lower()}_{EDITION_2}_v1.json").read_text(encoding="utf-8"))["ordered_names_sha256"]
        assert cr.bind_header(registry, HCO, stored["names"])["outcome"] == cr.OUTCOME_EXACT


class TestNoValueIsWrittenDown:
    def test_a_file_without_a_header_row_has_its_first_record_withheld(self, contracts):
        registry = cr.get_registry()
        first_record = ["90000001", SENTINEL, "012345", "SENTINEL NAME", "02101"]
        stored = cr.describe_header(registry, HCO, first_record)
        binding = cr.bind_header(registry, HCO, first_record)
        assert stored["recognised_as_header"] is False and stored["names_verbatim"] is False
        assert stored["names"] == [None] * 5 and stored["names_sha256"] is None
        assert stored["names_withheld"][1] == {"position": 2, "characters": len(SENTINEL)}
        assert binding["diff"]["added"] == {"count": 5, "names": [], "names_truncated": False,
                                            "names_withheld": 5}
        for text in support.all_strings([stored, binding]):
            assert "SENTINEL" not in text and "012345" not in text and "90000001" not in text

    def test_new_columns_of_a_real_header_are_named(self, contracts):
        header = names(HCO) + ["SYN_FUTURE_COLUMN"]
        stored = cr.describe_header(cr.get_registry(), HCO, header)
        assert stored["recognised_as_header"] and stored["names"] == header and stored["names_verbatim"]

    def test_a_registry_error_names_a_file_and_a_reason_only(self, contracts, tmp_path, monkeypatch):
        copy = tmp_path / "bad"
        shutil.copytree(contracts, copy)
        (copy / f"{HCP.lower()}_{EDITION_1}_v1.json").write_text("{ not json", encoding="utf-8")
        support.use_contracts(monkeypatch, copy)
        error = cr.get_registry().error
        assert error.startswith(f"{HCP.lower()}_{EDITION_1}_v1.json") and str(copy) not in error


class TestTheStagingTokenIsUntouched:
    """`compute_staging_token` decides whether a stored reconciliation result is
    still current. Its output for a fixed input is pinned byte for byte, and no
    metadata key added by the intake-contract work changes it."""

    def _snapshot(self, **extra):
        return types.SimpleNamespace(
            sha256="ab" * 32, status="PENDING", record_count=12345,
            metadata_={"rows_rejected": 7, "import_status": "completed",
                       "import_summary": {"rows_read": 12345, "rows_staged": 12000,
                                          "rows_already_staged": 338, "rows_rejected": 7,
                                          "rejected_sample_lines": [3, 9]},
                       "reference_preflight": {"gate": "CLEAR", "findings": []}, **extra})

    def _job(self):
        return types.SimpleNamespace(id="11111111-2222-3333-4444-555555555555", attempt_count=2,
                                     state="SUCCEEDED", completed_at=dt.datetime(2026, 11, 25, 10, 4, 0))

    def test_golden_values(self):
        assert rec.compute_staging_token(self._snapshot(), self._job(), False) == \
            "2c5c8843c5540448566a6ea00b1a092bff02e09f170c80a7d31def333911f31f"
        assert rec.compute_staging_token(self._snapshot(), None, True) == \
            "4b67c47a173dde53ac6cb09cf43fe937f12d6ea78c679782726c7aa5ad8c59ec"

    def test_the_new_metadata_keys_are_not_part_of_it(self):
        extra = {"source_header": {"names": ["A"]}, "contract_binding": {"outcome": "unregistered"},
                 "import_validation": {"rows": {"read": 1}}, "intake_gates": {"version": "1"},
                 "intake_stop": {"code": "X"}}
        assert rec.compute_staging_token(self._snapshot(**extra), self._job(), False) == \
            rec.compute_staging_token(self._snapshot(), self._job(), False)


LEGACY_SHAPES = [
    "A,B,C\r\n1,2,3\r\n",
    "A,B,B\r\n1,2,3\r\n",                              # duplicate name: last cell wins
    "A,B,C\r\n1,2\r\n1\r\n",                           # short records
    "A,B,C\r\n1,2,3,4,5\r\n",                          # long record
    "A,B,C\r\n\r\n1,2,3\r\n\r\n\r\n4,5,6\r\n",         # blank lines
    'A,B,C\r\n"x,y","line\r\nbreak","q""uote"\r\n',    # quoted delimiter, newline, quote
    "\r\n1,2,3\r\n",                                   # blank first line: an empty header
    "A,B,C\r\n",                                       # header only
    "",                                                # nothing
    "A,B,C\n1,2,3\n4,5,6",                             # LF, no final newline
    "A,,C\r\n1,2,3\r\n",                               # a blank header name
]


class TestTheRecordReaderIsTheOldReader:
    @pytest.mark.parametrize("text", LEGACY_SHAPES)
    def test_legacy_row_builds_exactly_what_dictreader_builds(self, text):
        expected = list(csv.DictReader(io.StringIO(text, newline="")))
        reader = csv.reader(io.StringIO(text, newline=""))
        header = next(reader, None)
        got = [] if header is None else [intake.legacy_row(header, cells) for cells in reader if cells]
        assert got == expected

    @pytest.mark.parametrize("text", LEGACY_SHAPES)
    def test_the_importer_reads_the_records_iter_reference_rows_reads(self, text, tmp_path):
        from app.tefca_registry.rce import iqvia_import as ii
        path = tmp_path / "f.csv"
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
        header, error = intake.read_reference_header(path)
        assert error is None
        validation = intake.FullFileValidation(header=header, contract=None, basis="none",
                                               lifted_limits={}, lifted_sources={})
        mine = [(n + 1, intake.legacy_row(validation.header, cells))
                for n, cells in ii._iter_reference_records(path, validation)]
        assert mine == list(ii.iter_reference_rows(path))


class TestTheCounters:
    def _validation(self, source=HCO, header=None):
        contract = support.contract_for(source, EDITION_2)
        return contract, intake.FullFileValidation(
            header=header or list(contract.names), contract=contract, basis="bound contract",
            lifted_limits={"npi": 10, "ccn": 20}, lifted_sources={"npi": "ORG_NPI", "ccn": "ORG_CCN_ID"})

    @pytest.mark.parametrize("cell,plain", [
        ("0", True), ("12345", True), ("-7", True), ("3.25", True), ("0012", True),
        ("9" * 40, True),                       # far beyond 2**63: still a plain decimal lexeme
        ("", True), ("   ", True),              # blank is not a finding
        (" 12", False), ("12 ", False), ("1,234", False), ("1e5", False), ("+5", False),
        ("12.", False), (".5", False), ("0x1F", False), ("NaN", False), ("TWELVE", False),
        ("١٢", False),                # non-ASCII digits
    ])
    def test_what_counts_as_a_plain_decimal_lexeme(self, contracts, cell, plain):
        contract, validation = self._validation()
        row = support.contract_row(contract, 1, overrides={"SYN_COUNT": cell})
        validation.measure(1, [row[n] for n in contract.names])
        finding = validation.as_metadata(rows={})["numeric_not_plain_decimal"]
        assert finding["cells"] == (0 if plain else 1)
        if not plain:
            assert finding["by_column"] == [{"column": "SYN_COUNT", "position": 8, "count": 1,
                                             "record_numbers": [1]}]

    def test_a_value_at_the_vendor_length_passes_and_one_over_is_counted(self, contracts):
        contract, validation = self._validation()
        at_limit = support.contract_row(contract, 1, fill=True)
        assert len(at_limit["SYN_LONG_LIST"]) == 5000 and len(at_limit["SYN_NOTE"]) == 8
        validation.measure(1, [at_limit[n] for n in contract.names])
        over = dict(at_limit, SYN_NOTE=at_limit["SYN_NOTE"] + "!", SYN_LONG_LIST="9" * 5001)
        validation.measure(2, [over[n] for n in contract.names])
        report = validation.as_metadata(rows={})["over_vendor_length"]
        assert report["cells"] == 2 and report["columns_affected"] == 2
        assert {(c["column"], c["vendor_length"], c["longest_characters"], tuple(c["record_numbers"]))
                for c in report["by_column"]} == {("SYN_NOTE", 8, 9, (2,)), ("SYN_LONG_LIST", 5000, 5001, (2,))}

    def test_length_is_counted_in_characters(self, contracts):
        contract, validation = self._validation()
        row = support.contract_row(contract, 1, overrides={"SYN_NOTE": "é" * 8})   # 8 chars, 16 bytes
        validation.measure(1, [row[n] for n in contract.names])
        assert validation.as_metadata(rows={})["over_vendor_length"]["cells"] == 0

    def test_only_columns_the_contract_knows_are_measured(self, contracts):
        contract = support.contract_for(HCO, EDITION_2)
        header = list(contract.names) + ["SYN_FUTURE_COLUMN"]
        validation = intake.FullFileValidation(header=header, contract=contract, basis="newest",
                                               lifted_limits={}, lifted_sources={})
        row = support.contract_row(contract, 1)
        validation.measure(1, [row[n] for n in contract.names] + ["x" * 9000])
        report = validation.as_metadata(rows={})
        assert report["measured_against"]["columns_without_contract_field"] == 1
        assert report["over_vendor_length"]["cells"] == 0

    def test_the_summary_is_bounded_and_holds_no_value(self, contracts):
        contract, validation = self._validation()
        for n in range(1, 5001):
            row = support.contract_row(contract, n, overrides={
                "SYN_NOTE": SENTINEL, "SYN_COUNT": SENTINEL, "SYN_BIG_ID": SENTINEL,
                "SYN_CODE": SENTINEL, "BUS_NM": SENTINEL * 5, "ZIP5_CD": SENTINEL})
            cells = [row[c] for c in contract.names]
            validation.measure(n, cells)
            validation.width_differs(n, cells[:-1])
            validation.width_differs(n, cells + [SENTINEL])
            validation.lifted(n, {"npi": SENTINEL, "ccn": SENTINEL * 3})
        report = validation.as_metadata(rows={"read": 5000})
        assert all(len(c["record_numbers"]) == intake.MAX_RECORD_NUMBERS
                   for c in report["over_vendor_length"]["by_column"])
        assert report["record_width"]["short_records"] == 5000
        assert len(report["record_width"]["long_record_numbers"]) == intake.MAX_RECORD_NUMBERS
        assert report["lifted_identifiers"]["npi"]["longer_than_column"] == 5000
        assert report["lifted_identifiers"]["ccn"]["longer_than_column"] == 5000
        assert report["values_recorded"] is False
        assert len(json.dumps(report)) < 4000
        assert all("SENTINEL" not in text for text in support.all_strings(report))

    def test_lifted_identifier_outcomes(self, contracts):
        _contract, validation = self._validation()
        assert validation.lifted(1, {"npi": support.synthetic_npi(1), "ccn": "012345"}) == []
        assert validation.lifted(2, {"npi": None, "ccn": None}) == []
        assert validation.lifted(3, {"npi": "1234567890", "ccn": "C" * 21}) == ["ccn"]   # bad check digit
        assert validation.lifted(4, {"npi": "12345678901"}) == ["npi"]
        lifted = validation.as_metadata(rows={})["lifted_identifiers"]
        assert lifted["npi"] == {"source_column": "ORG_NPI", "column_characters": 10, "lifted": 2,
                                 "blank": 1, "longer_than_column": 1, "record_numbers": [4],
                                 "not_a_valid_npi": 2}
        assert lifted["ccn"] == {"source_column": "ORG_CCN_ID", "column_characters": 20, "lifted": 1,
                                 "blank": 1, "longer_than_column": 1, "record_numbers": [3]}
