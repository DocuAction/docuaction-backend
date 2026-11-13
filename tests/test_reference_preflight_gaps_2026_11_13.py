"""Three gaps in the reference-snapshot preflight, each reproduced with a synthetic
file before it was fixed (reference_preflight 1.1.0):

  * a header-only extract was CLEAR - the approvable EMPTY snapshot the preflight
    exists to prevent;
  * a VALID extract whose final record has a quoted line break was BLOCKED as
    truncated;
  * `bound` claimed "first 1000 records" when only the first 64 KiB is read.

Pure: files in tmp_path, no database, no network. Synthetic values only. IQVIA is
advisory; nothing here assigns a bucket or touches an imported snapshot.
"""
from __future__ import annotations

import pytest

from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm

HEADERS = {
    sm.SOURCE_IQVIA_HCO: "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID,ORG_NAME,ADDR_LINE",
    sm.SOURCE_IQVIA_HCP: "HCP_HCE_ID,ADDR_ID,NPI,LAST_NAME,ADDR_LINE",
    sm.SOURCE_IQVIA_AFFILIATION: "HCP_HCE_ID,HCO_HCE_ID,AFFL_TYP_ID,TITL_TYP_ID,NOTE",
}


def _row(i: int, last: str = "1 Synthetic Way") -> str:
    return f"S{i:07d},1234567893,123456,SYNTHETIC {i},{last}\n"


def _write(path, text: str):
    path.write_bytes(text.encode("utf-8"))
    return path


def _codes(result):
    return [f["code"] for f in result["findings"]]


@pytest.mark.parametrize("source", sorted(HEADERS))
@pytest.mark.parametrize("trailer", ["", "\n", "\n\n\r\n"])
def test_a_header_with_no_data_record_is_blocked_for_every_supported_file(tmp_path, source, trailer):
    result = rp.preflight_reference_file(_write(tmp_path / "empty.csv", HEADERS[source] + trailer), source)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-012" in _codes(result)
    finding = next(f for f in result["findings"] if f["code"] == "REF-SCH-012")
    assert finding["disposition"] == pm.DISP_BLOCKED and finding["evidence"]["data_records"] == 0
    assert "no data record" in finding["description"]            # says what is wrong, in words
    assert result["rows_sampled"] == 0 and result["originals_modified"] is False


@pytest.mark.parametrize("source", sorted(HEADERS))
def test_one_data_record_is_enough_to_be_clear(tmp_path, source):
    result = rp.preflight_reference_file(_write(tmp_path / "one.csv", HEADERS[source] + "\n" + _row(1)), source)
    assert result["gate"] == pm.GATE_CLEAR and "REF-SCH-012" not in _codes(result)
    assert result["rows_sampled"] == 1


@pytest.mark.parametrize("final", [
    'S0000009,1234567893,123456,SYNTHETIC 9,"Suite 4\nBuilding C"\n',          # LF inside the field
    'S0000009,1234567893,123456,SYNTHETIC 9,"Suite 4\r\nBuilding C"\r\n',      # CRLF inside the field
    'S0000009,1234567893,123456,SYNTHETIC 9,"Suite 4\nBuilding C\nFloor 2"',   # two breaks, no final newline
])
def test_a_valid_final_record_with_a_quoted_line_break_is_not_called_truncated(tmp_path, final):
    text = HEADERS[sm.SOURCE_IQVIA_HCO] + "\n" + _row(1) + _row(2) + final
    result = rp.preflight_reference_file(_write(tmp_path / "multiline.csv", text), sm.SOURCE_IQVIA_HCO)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert result["tail_checked"] is True


def test_the_same_holds_when_the_final_record_is_read_from_the_tail_of_a_large_file(tmp_path):
    body = "".join(_row(i, 'x' * 40) for i in range(4000))                      # ~300 KiB: head and tail differ
    ok = HEADERS[sm.SOURCE_IQVIA_HCO] + "\n" + body + 'S9999999,1234567893,123456,SYNTHETIC,"line one\nline two"\n'
    result = rp.preflight_reference_file(_write(tmp_path / "large_ok.csv", ok), sm.SOURCE_IQVIA_HCO)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR
    # multi-line records THROUGHOUT the tail, so the tail's first bytes fall inside one
    many = HEADERS[sm.SOURCE_IQVIA_HCO] + "\n" + "".join(
        f'S{i:07d},1234567893,123456,SYNTHETIC {i},"line one {i}\nline two"\n' for i in range(6000))
    result = rp.preflight_reference_file(_write(tmp_path / "large_multiline.csv", many), sm.SOURCE_IQVIA_HCO)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR


@pytest.mark.parametrize("final", [
    "S0000009,12345",                                             # cut mid-record
    'S0000009,1234567893,123456,SYNTHETIC 9,"Suite 4\nBuild',     # cut inside a quoted field
    "S0000009,1234567893,123456\n",                               # short record, newline present
])
def test_a_really_truncated_final_record_is_still_blocked_small_and_large(tmp_path, final):
    small = HEADERS[sm.SOURCE_IQVIA_HCO] + "\n" + _row(1) + _row(2) + final
    result = rp.preflight_reference_file(_write(tmp_path / "cut_small.csv", small), sm.SOURCE_IQVIA_HCO)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result)
    large = HEADERS[sm.SOURCE_IQVIA_HCO] + "\n" + "".join(_row(i, 'x' * 40) for i in range(4000)) + final
    result = rp.preflight_reference_file(_write(tmp_path / "cut_large.csv", large), sm.SOURCE_IQVIA_HCO)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result)


def test_the_stated_bound_is_what_was_actually_read(tmp_path):
    wide = HEADERS[sm.SOURCE_IQVIA_HCO] + "\n" + "".join(_row(i, "X" * 180) for i in range(3000))
    result = rp.preflight_reference_file(_write(tmp_path / "wide.csv", wide), sm.SOURCE_IQVIA_HCO)
    sampled = result["rows_sampled"]
    assert 0 < sampled < 1000, sampled                            # 64 KiB of ~230-byte rows
    assert f"the first {sampled} record(s)" in result["bound"] and "64 KiB" in result["bound"]
    assert "first 1000 records" not in result["bound"] and "not a full-file scan" in result["bound"]
    narrow = HEADERS[sm.SOURCE_IQVIA_HCO] + "\n" + "".join(f"S{i:07d},1,2,3,4\n" for i in range(5000))
    result = rp.preflight_reference_file(_write(tmp_path / "narrow.csv", narrow), sm.SOURCE_IQVIA_HCO)
    assert result["rows_sampled"] == rp.SAMPLE_ROWS and "the first 1000 record(s)" in result["bound"]


def test_the_three_supported_files_and_only_those_have_a_contract_and_the_version_moved():
    assert set(rp.REFERENCE_SCHEMAS) == {sm.SOURCE_IQVIA_HCO, sm.SOURCE_IQVIA_HCP, sm.SOURCE_IQVIA_AFFILIATION}
    assert rp.REFERENCE_SCHEMAS[sm.SOURCE_IQVIA_HCP]["required"] == ("HCP_HCE_ID", "ADDR_ID")   # the ADDR file
    assert rp.REFERENCE_PREFLIGHT_VERSION == "1.1.0"
    with pytest.raises(ValueError):
        rp.preflight_reference_file(__file__, "IQVIA_UNKNOWN")
