"""scripts/qa_fixture_pack.py — deterministic synthetic delivery fixtures.

Pure: no database, no network. The "engine" tests run the project's own
quality rule functions (app/tefca_registry/rce/quality_rules.py) over the
generated rows with a dataset context built the way quality_engine builds it,
so every expected rule code in the manifest is checked against the live rule
set rather than trusted. `new_entrant_ids` is set to every delivered id, which
is the "an earlier delivery exists" condition under which ACT-001 fires; the
fixture documents that condition.
"""
from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from app.services.npi_validator import compute_check_digit, validate_npi
from app.tefca_registry.rce import quality_rules as qr
from app.tefca_registry.rce import reader
from app.tefca_registry.rce.field_map import OBSERVED_QHIN_OIDS, RCE_FIELDS

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa_fixture_pack.py"

_spec = importlib.util.spec_from_file_location("qa_fixture_pack", SCRIPT)
fp = importlib.util.module_from_spec(_spec)
sys.modules["qa_fixture_pack"] = fp   # dataclasses resolve postponed annotations via sys.modules
_spec.loader.exec_module(fp)  # type: ignore[union-attr]

SMALL_SCALE = 400
ID_PATTERN = re.compile(r"^9\.99\.777\.QA[A-Z0-9]+\.\d+$")


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pack")
    fp.generate(out, scale=SMALL_SCALE)
    return out


@pytest.fixture(scope="module")
def manifest(pack) -> dict:
    return json.loads((pack / fp.MANIFEST_NAME).read_text("utf-8"))


def _read(pack: Path, filename: str) -> reader.DeliveryRead:
    return reader.read_delivery((pack / filename).read_bytes(), declared_delimiter="|")


def _sha_map(directory: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.iterdir()) if p.is_file()}


# ── determinism ──────────────────────────────────────────────────────────────

def test_two_runs_are_byte_identical(tmp_path):
    fp.generate(tmp_path / "a", scale=SMALL_SCALE)
    fp.generate(tmp_path / "b", scale=SMALL_SCALE)
    a, b = _sha_map(tmp_path / "a"), _sha_map(tmp_path / "b")
    assert a == b
    assert fp.MANIFEST_NAME in a and len(a) == len(fp.SCENARIOS) + 1


def test_manifest_carries_no_timestamp(manifest):
    text = json.dumps(manifest)
    assert not re.search(r"20\d\d-\d\d-\d\dT\d\d:\d\d", text)
    assert "generated_at" not in manifest


# ── shape ────────────────────────────────────────────────────────────────────

def test_header_is_exactly_the_locked_41_columns(pack):
    assert fp.RCE_COLUMNS == RCE_FIELDS
    for scenario in fp.SCENARIOS:
        raw = (pack / scenario.filename).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), "no BOM"
        header, rest = raw.split(b"\r\n", 1)
        assert header.decode("utf-8") == "|".join(RCE_FIELDS)
        assert b"\n" not in rest.replace(b"\r\n", b""), "CRLF only"


def test_every_row_parses_with_41_fields_and_synthetic_identity(pack):
    for scenario in fp.SCENARIOS:
        read = _read(pack, scenario.filename)
        assert read.delimiter == "|" and read.encoding == "utf-8"
        assert read.malformed_count == 0, scenario.name
        prefix = f"9.99.777.QA{scenario.tag}."
        for line in read.lines:
            assert line.field_count == 41
            assert ID_PATTERN.match(line.get("id")), line.get("id")
            assert line.get("id").startswith(prefix), (scenario.name, line.get("id"))
            assert "SYNTHETIC" in line.get("name"), line.get("name")
            assert line.get("orgManagingOrg").startswith(prefix)
            assert line.get("address_line") == "1 Synthetic Way"
            part_of = line.get("partOf")
            assert part_of == "" or part_of.startswith(prefix)


def test_filenames_follow_the_convention(pack):
    for scenario in fp.SCENARIOS:
        assert scenario.filename == f"SYNTHETIC-QA-{scenario.name}.psv"
        assert (pack / scenario.filename).is_file()


# ── NPIs ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [0, 1, 2, 7, 99, 12345, 24999, 99999999])
def test_synthetic_npis_agree_with_the_project_validator(n):
    valid = fp.synthetic_npi(n)
    assert len(valid) == 10 and valid.startswith("9")
    assert compute_check_digit(valid[:9]) == valid[9]
    assert validate_npi(valid) == (True, "")
    bad = fp.luhn_invalid_npi(n)
    assert len(bad) == 10 and bad[:9] == valid[:9] and bad != valid
    ok, message = validate_npi(bad)
    assert ok is False and "Luhn" in message


def _expected_codes(manifest: dict, scenario) -> dict:
    """Line number -> expected 'RULE issue_type' codes; the scale file keeps
    them in-process rather than in the manifest."""
    entry = manifest["scenarios"][scenario.name]
    if "expected_by_line" in entry:
        return entry["expected_by_line"]
    return {str(i): sorted(f"{r} {t}" for r, t in line.expected)
            for i, line in enumerate(scenario.lines(SMALL_SCALE), start=2)}


def test_valid_npi_scenarios_pass_and_luhn_invalid_rows_fail(pack, manifest):
    for scenario in fp.SCENARIOS:
        read = _read(pack, scenario.filename)
        expected = _expected_codes(manifest, scenario)
        for line in read.lines:
            npi = line.get("NPI")
            codes = expected.get(str(line.line_number), [])
            checksum_expected = any(c.startswith("NPI-003 ") for c in codes)
            if checksum_expected:
                assert validate_npi(npi)[0] is False, (scenario.name, line.line_number)
            elif npi and re.fullmatch(r"[0-9]{10}", npi):
                assert validate_npi(npi) == (True, ""), (scenario.name, line.line_number, npi)
            if npi and re.fullmatch(r"[0-9]{10}", npi):
                assert npi.startswith("9"), "synthetic NPIs never start with 1 or 2"


# ── manifest ─────────────────────────────────────────────────────────────────

def test_manifest_is_complete_and_internally_consistent(pack, manifest):
    assert set(manifest["scenarios"]) == {s.name for s in fp.SCENARIOS}
    assert manifest["header"] == list(RCE_FIELDS)
    assert manifest["scale"] == SMALL_SCALE
    assert manifest["target_rule_set_version"] == qr.RULE_SET_VERSION
    assert manifest["synthetic"] is True
    for scenario in fp.SCENARIOS:
        entry = manifest["scenarios"][scenario.name]
        path = pack / entry["filename"]
        blob = path.read_bytes()
        assert entry["sha256"] == hashlib.sha256(blob).hexdigest()
        assert entry["size_bytes"] == len(blob)
        read = _read(pack, entry["filename"])
        assert entry["row_count"] == read.record_count
        assert entry["oid_prefix"] == f"9.99.777.QA{scenario.tag}"
        assert entry["notes"] and entry["workflow_uses"]
        for code in entry["expected_rule_codes"]:
            assert code in manifest["finding_catalog"], code
        if scenario.name == "SEPTEMBER_SCALE":
            assert "expected_by_line" not in entry
            continue
        by_line = entry["expected_by_line"]
        ids_by_line = entry["ids_by_line"]
        assert set(by_line) == {str(l.line_number) for l in read.lines}
        for line in read.lines:
            assert ids_by_line[str(line.line_number)] == line.get("id")
        counted = collections.Counter(c for codes in by_line.values() for c in codes)
        assert dict(counted) == entry["expected_rule_codes"]
    for state, driver in manifest["workflow_state_drivers"].items():
        assert driver["drive_on"] and driver["how"], state


def test_scale_respects_the_flag(tmp_path):
    fp.generate(tmp_path, scale=1234, only=["SEPTEMBER_SCALE"])
    manifest = json.loads((tmp_path / fp.MANIFEST_NAME).read_text("utf-8"))
    assert list(manifest["scenarios"]) == ["SEPTEMBER_SCALE"]
    entry = manifest["scenarios"]["SEPTEMBER_SCALE"]
    assert entry["row_count"] == 1234
    read = _read(tmp_path, entry["filename"])
    assert read.record_count == 1234
    ids = [l.get("id") for l in read.lines]
    assert len(set(ids)) == 1234, "no duplicate ids in the scale file"
    defective = sum(1 for l in read.lines if l.line_number - 1 >= 2 and (l.line_number - 1) % 5 == 0)
    assert abs(defective / 1234 - 0.2) < 0.01
    kinds = entry["rows_by_kind"]
    assert kinds["qhin"] == 1
    assert kinds["participant"] + kinds["subparticipant"] == 1234 - 1 - defective


def test_only_rejects_unknown_scenarios(tmp_path):
    with pytest.raises(ValueError, match="unknown scenario"):
        fp.generate(tmp_path, scale=10, only=["NOPE"])


def test_cli_list_and_generate(tmp_path, capsys):
    assert fp.main(["--list"]) == 0
    out = capsys.readouterr().out
    for scenario in fp.SCENARIOS:
        assert scenario.name in out
    assert fp.main(["--out", str(tmp_path), "--scale", "50", "--only", "CLEAN_B1"]) == 0
    assert (tmp_path / "SYNTHETIC-QA-CLEAN_B1.psv").is_file()
    assert (tmp_path / fp.MANIFEST_NAME).is_file()


def test_refuses_production(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(SystemExit) as exc:
        fp.main(["--out", str(tmp_path), "--scale", "10"])
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


# ── paired scenarios ─────────────────────────────────────────────────────────

def _rows(pack: Path, name: str) -> dict:
    return {l.get("id"): l.parsed for l in _read(pack, fp.SCENARIO_BY_NAME[name].filename).lines}


def test_relationship_b_moves_exactly_one_edge(pack):
    a, b = _rows(pack, "RELATIONSHIP_A"), _rows(pack, "RELATIONSHIP_B")
    assert set(a) == set(b)
    diffs = {oid: [c for c in RCE_FIELDS if a[oid][c] != b[oid][c]] for oid in a}
    changed = {oid: cols for oid, cols in diffs.items() if cols}
    assert changed == {"9.99.777.QAREL.4": ["partOf"]}
    assert a["9.99.777.QAREL.4"]["partOf"] == "9.99.777.QAREL.2"
    assert b["9.99.777.QAREL.4"]["partOf"] == "9.99.777.QAREL.3"


def test_conflict_b_contradicts_a_only_on_npis(pack):
    a, b = _rows(pack, "CONFLICTING_EVIDENCE_A"), _rows(pack, "CONFLICTING_EVIDENCE_B")
    assert set(a) < set(b) and len(set(b) - set(a)) == 1
    changed = {oid: [c for c in RCE_FIELDS if a[oid][c] != b[oid][c]] for oid in a}
    assert {oid: cols for oid, cols in changed.items() if cols} == {
        "9.99.777.QACONFLICT.2": ["NPI"], "9.99.777.QACONFLICT.4": ["NPI"]}
    assert b["9.99.777.QACONFLICT.4"]["NPI"] == a["9.99.777.QACONFLICT.3"]["NPI"]
    (new_id,) = set(b) - set(a)
    assert b[new_id]["NPI"] == a["9.99.777.QACONFLICT.5"]["NPI"]


# ── the engine agrees with the manifest ──────────────────────────────────────

def _dataset(read: reader.DeliveryRead) -> dict:
    """The cross-record facts quality_engine._build_dataset_context computes,
    for an environment that already holds an earlier delivery and no registry
    entity under the synthetic arc."""
    ids = collections.Counter(l.get("id") for l in read.lines if l.get("id"))
    tefcaids = collections.Counter(l.get("TEFCAID") for l in read.lines if l.get("TEFCAID"))
    hcids = collections.Counter(l.get("HCID") for l in read.lines if l.get("HCID"))
    return {
        "expected_field_count": len(RCE_FIELDS),
        "known_source_ids": set(ids),
        "qhin_oids": set(OBSERVED_QHIN_OIDS),
        "registry_oids": set(),
        "previous_source_ids": set(),
        "new_entrant_ids": set(ids),
        "source_id_duplicates": {v: n for v, n in ids.items() if n > 1},
        "tefcaid_duplicates": {v: n for v, n in tefcaids.items() if n > 1},
        "hcid_duplicates": {v: n for v, n in hcids.items() if n > 1},
    }


def _engine_findings(read: reader.DeliveryRead) -> dict:
    dataset = _dataset(read)
    out = {}
    for line in read.lines:
        ctx = qr.RecordContext(line_number=line.line_number, parse_status=line.parse_status,
                               field_count=line.field_count, values=dict(line.parsed),
                               dataset=dataset)
        findings = []
        for rule in qr.RULES:
            findings.extend(rule.evaluate(ctx))
        out[line.line_number] = findings
    return out


@pytest.mark.parametrize("scenario", [s for s in fp.SCENARIOS if s.name != "SEPTEMBER_SCALE"],
                         ids=lambda s: s.name)
def test_engine_raises_exactly_the_expected_codes(pack, manifest, scenario):
    read = _read(pack, scenario.filename)
    expected = manifest["scenarios"][scenario.name]["expected_by_line"]
    for line_number, findings in _engine_findings(read).items():
        got = sorted(f"{f.rule_id} {f.issue_type}" for f in findings)
        assert got == expected[str(line_number)], (scenario.name, line_number)


def test_engine_agrees_on_the_scale_file(pack):
    scenario = fp.SCENARIO_BY_NAME["SEPTEMBER_SCALE"]
    read = _read(pack, scenario.filename)
    lines = scenario.lines(SMALL_SCALE)
    assert [l.id for l in lines] == [l.get("id") for l in read.lines]
    engine = _engine_findings(read)
    for line, delivered in zip(lines, read.lines):
        got = sorted(f"{f.rule_id} {f.issue_type}" for f in engine[delivered.line_number])
        assert got == sorted(f"{r} {i}" for r, i in line.expected), (line.kind, delivered.line_number)
    kinds = collections.Counter(l.kind for l in lines)
    assert set(kinds) >= {name for name, _ in fp.SCALE_DEFECTS} | {"participant", "subparticipant", "qhin"}


def test_finding_catalog_matches_the_live_rule_set(pack):
    """Every catalogued (rule, issue_type) is observed at least once across the
    pack, and its severity / authority match what the rule actually emits."""
    seen = {}
    for scenario in fp.SCENARIOS:
        for findings in _engine_findings(_read(pack, scenario.filename)).values():
            for f in findings:
                seen.setdefault((f.rule_id, f.issue_type), (f.severity, f.correction_authority))
    for key, expected in fp.FINDING_CATALOG.items():
        assert key in seen, f"{key} never observed in the pack"
        assert seen[key] == expected, key
    assert set(seen) == set(fp.FINDING_CATALOG), "the pack raises an uncatalogued finding"


def test_clean_b1_member_rows_carry_no_human_required_finding(pack):
    read = _read(pack, fp.SCENARIO_BY_NAME["CLEAN_B1"].filename)
    engine = _engine_findings(read)
    qhin = engine[2]
    assert {(f.rule_id, f.correction_authority) for f in qhin} >= {
        ("REQ-001", "HUMAN_REQUIRED"), ("INT-002", "NO_CORRECTION")}
    for line_number in range(3, 7):
        assert all(f.correction_authority == "NO_CORRECTION" and f.severity == "INFORMATIONAL"
                   for f in engine[line_number]), line_number
