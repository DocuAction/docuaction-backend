"""No verdict of the reference preflight rests on a truncated or non-strict view
of the file (fourth pass on #216, 2026-11-14).

Review finding: when the 64 KiB sample ended INSIDE a quoted multi-line record,
the sample's non-strict reader returned a partial record, the sample's
field-count check reported REF-SCH-007 and a valid file was BLOCKED - although
the strict whole-file read accepted it.

After this pass the sample decides nothing. The header, every field count, the
encoding and the final record all come from ONE strict read of the whole file.
`rows_sampled` is a count of COMPLETE records in the first 64 KiB and is
informational only.

Line numbers: a record's line is the physical line on which the record ENDS
(the csv reader's `line_num`), counting LF, CR and CRLF each as one line end.

Pure: files in tmp_path, no database, no network. Synthetic values only.
"""
from __future__ import annotations

import csv

import pytest

from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm

HCO = sm.SOURCE_IQVIA_HCO
HEADER = b"HCO_HCE_ID,ORG_NPI,ORG_CCN_ID,col4,col5"
SAMPLE = 64 * 1024


def _write(tmp_path, name, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _codes(result):
    return sorted(f["code"] for f in result["findings"] if f["disposition"] == pm.DISP_BLOCKED)


def _strict_rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return [r for r in csv.reader(fh, strict=True) if r]


# ── the finding ──────────────────────────────────────────────────────────────

def test_the_reviewers_file_a_quoted_record_crossing_the_sample_boundary_is_clear(tmp_path):
    data = (HEADER + b"\n"
            + b'1,2,3,"' + b"x" * 100 + b"\n"
            + b"x" * 65536 + b'",z\n')
    path = _write(tmp_path, "crossing.csv", data)
    assert [len(r) for r in _strict_rows(path)] == [5, 5], "the file is valid CSV"
    result = rp.preflight_reference_file(path, HCO)
    assert _codes(result) == [], result["findings"]
    assert result["gate"] == pm.GATE_CLEAR
    assert result["rows_sampled"] == 0, "the only data record is not complete inside the sample"
    assert result["records_read"] == 2


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("complete_before", [0, 3, 400])
def test_a_crossing_record_after_complete_records_is_clear_for_every_line_ending(
        tmp_path, ending, complete_before):
    rows = b"".join(b"A%d,1,2,3,4" % i + ending for i in range(complete_before))
    filler = SAMPLE - len(HEADER) - len(ending) - len(rows) - 50
    crossing = (b'B,1,2,"' + b"y" * filler + ending + b"y" * 5000 + b'",last' + ending)
    after = b"".join(b"C%d,1,2,3,4" % i + ending for i in range(5))
    path = _write(tmp_path, "crossing2.csv", HEADER + ending + rows + crossing + after)
    result = rp.preflight_reference_file(path, HCO)
    assert _codes(result) == [], result["findings"]
    assert result["rows_sampled"] == complete_before, "only COMPLETE sampled records are counted"
    assert result["records_read"] == 1 + complete_before + 1 + 5


# ── the mirror cases ─────────────────────────────────────────────────────────

def test_a_record_that_crosses_the_boundary_and_is_never_closed_is_still_blocked(tmp_path):
    data = (HEADER + b"\n" + b"A,1,2,3,4\n"
            + b'B,1,2,"' + b"y" * (SAMPLE + 5000) + b"\nC,1,2,3,4\n")
    result = rp.preflight_reference_file(_write(tmp_path, "open.csv", data), HCO)
    found = [f for f in result["findings"] if f["code"] == "REF-SCH-008"]
    assert result["gate"] == pm.GATE_BLOCKED and len(found) == 1
    assert found[0]["evidence"]["reader_error"] == rp.READER_ENDS_IN_QUOTES
    assert not [f for f in result["findings"] if f["code"] == "REF-SCH-007"]


def test_a_record_that_crosses_the_boundary_with_the_wrong_field_count_is_blocked(tmp_path):
    data = (HEADER + b"\n" + b"A,1,2,3,4\n"
            + b'B,1,"' + b"y" * 100 + b"\n" + b"y" * SAMPLE + b'",last\n'      # 4 fields
            + b"C,1,2,3,4\n")
    result = rp.preflight_reference_file(_write(tmp_path, "short.csv", data), HCO)
    found = [f for f in result["findings"] if f["code"] == "REF-SCH-007"]
    assert len(found) == 1 and found[0]["evidence"]["fields"] == 4
    assert found[0]["evidence"]["line"] == 4, "the physical line on which that record ends"
    assert found[0]["evidence"]["records_with_wrong_field_count"] == 1


def test_a_sample_that_ends_exactly_on_a_record_boundary(tmp_path):
    row = b"A,1,2,3,4\n"
    n = (SAMPLE - len(HEADER) - 1) // len(row)
    pad = SAMPLE - len(HEADER) - 1 - n * len(row)
    first = b"A,1,2,3," + b"4" * (1 + pad) + b"\n"
    head = HEADER + b"\n" + first + row * (n - 1)
    assert len(head) == SAMPLE
    path = _write(tmp_path, "exact.csv", head + row * 500)
    result = rp.preflight_reference_file(path, HCO)
    assert _codes(result) == []
    assert result["rows_sampled"] == min(n, rp.SAMPLE_ROWS)
    assert result["records_read"] == 1 + n + 500


def test_a_file_smaller_than_the_sample_is_judged_the_same_way(tmp_path):
    ok = _write(tmp_path, "small.csv", HEADER + b"\nA,1,2,3,4\nB,1,2,\"multi\nline\",4\n")
    result = rp.preflight_reference_file(ok, HCO)
    assert _codes(result) == [] and result["rows_sampled"] == 2 and result["records_read"] == 3

    bad = _write(tmp_path, "small-bad.csv", HEADER + b"\nA,1,2,3\nB,1,2,3,4\nC,1,2,3,4\n")
    result = rp.preflight_reference_file(bad, HCO)
    found = [f for f in result["findings"] if f["code"] == "REF-SCH-007"]
    assert len(found) == 1 and found[0]["evidence"]["line"] == 2
    assert found[0]["evidence"]["first_lines"] == [2]


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_a_wrong_field_count_inside_the_sample_region_reports_its_physical_line(tmp_path, ending):
    rows = [b"A%d,1,2,3,4" % i for i in range(8000)]          # well over 64 KiB
    rows[2] = b'M,1,2,"two' + ending + b'lines",4'              # lines 4-5, valid
    rows[10] = b"S,1,2"                                         # 3 fields; ends on line 13
    path = _write(tmp_path, "inside.csv", HEADER + ending + ending.join(rows) + ending)
    result = rp.preflight_reference_file(path, HCO)
    found = [f for f in result["findings"] if f["code"] == "REF-SCH-007"]
    assert len(found) == 1, result["findings"]
    assert found[0]["evidence"]["line"] == 13 and found[0]["evidence"]["fields"] == 3
    assert found[0]["evidence"]["first_lines"] == [13]


# ── nothing else may rest on the truncated view either ───────────────────────

def test_a_multibyte_character_cut_by_the_sample_boundary_is_not_an_encoding_fault(tmp_path):
    """One line longer than the sample, with a two-byte character straddling
    byte 65,536: the sample, cut mid-character, used to fail its own decode."""
    lead = HEADER + b"\n" + b'A,1,2,3,"'
    fill = SAMPLE - len(lead) - 1
    data = lead + b"z" * fill + "é".encode("utf-8") + b"z" * 300 + b'"\nB,1,2,3,4\n'
    assert data[SAMPLE - 1:SAMPLE + 1] == "é".encode("utf-8")
    path = _write(tmp_path, "straddle.csv", data)
    assert len(_strict_rows(path)) == 3
    result = rp.preflight_reference_file(path, HCO)
    assert _codes(result) == [], result["findings"]


def test_the_header_is_the_whole_first_record_even_beyond_the_sample(tmp_path):
    """A header longer than the sample: the required column is its LAST name.
    Read from the sample alone it was 'absent'."""
    extra = b",".join(b"EXTRA_COLUMN_%05d" % i for i in range(5000))        # ~95 KiB
    header = b"ORG_NPI,ORG_CCN_ID," + extra + b",HCO_HCE_ID"
    n = header.count(b",") + 1
    row = b",".join([b"1"] * n) + b"\n"
    path = _write(tmp_path, "wide.csv", header + b"\n" + row * 3)
    result = rp.preflight_reference_file(path, HCO)
    assert _codes(result) == [], [f["code"] for f in result["findings"]]
    assert result["header"][-1] == "HCO_HCE_ID" and len(result["header"]) == n


def test_a_large_file_that_starts_with_blank_lines_is_not_called_empty(tmp_path):
    """More than a sample of blank lines, then a header and records. The sample
    is all white space; the file is not empty. The importer takes its column
    names from the first row, which is blank, so the file is still BLOCKED -
    for its missing identity column, not as an empty file."""
    data = b"\n" * (SAMPLE + 10) + HEADER + b"\nA,1,2,3,4\n"
    result = rp.preflight_reference_file(_write(tmp_path, "blank-lead.csv", data), HCO)
    codes = _codes(result)
    assert "REF-SCH-005" not in codes and "REF-SCH-012" not in codes, codes
    assert "REF-SCH-001" in codes and result["gate"] == pm.GATE_BLOCKED


def test_the_bound_says_what_decided_and_what_the_sample_is(tmp_path):
    data = HEADER + b"\n" + b"A,1,2,3,4\n" * 9000
    result = rp.preflight_reference_file(_write(tmp_path, "bound.csv", data), HCO)
    bound = result["bound"]
    assert "whole file was read as CSV records (strict, decoded as UTF-8)" in bound
    assert "header" in bound and "every record's field count" in bound
    assert f"the first {result['rows_sampled']} record(s)" in bound
    assert "informational" in bound, "the sampled count decides nothing, and the text says so"


def test_no_field_count_verdict_is_taken_from_the_sample():
    import inspect

    source = inspect.getsource(rp.preflight_reference_file)
    assert "rows[1:1 + SAMPLE_ROWS]" not in source
    assert "sample_reader" not in source, "the sample is not parsed for any verdict"


# ── the header as the importer's reader keys it ──────────────────────────────

def _importer_first_key_and_value(path, column):
    """Read `path` exactly as the importer does (utf-8, newline="", DictReader)."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        row = next(reader)
        return reader.fieldnames[0], row.get(column)


@pytest.mark.parametrize("delivered", [b" HCO_HCE_ID", b"HCO_HCE_ID ", b"\tHCO_HCE_ID"])
def test_a_required_column_with_space_around_its_name_is_not_the_column(tmp_path, delivered):
    """It was matched after trimming and the file was CLEAR. The importer keys
    rows by the name as delivered and finds no HCO_HCE_ID in any row."""
    data = delivered + b",ORG_NPI,ORG_CCN_ID\n" + b"A,1,2\nB,1,2\n"
    path = _write(tmp_path, "spaced.csv", data)
    assert _importer_first_key_and_value(path, "HCO_HCE_ID")[1] is None
    result = rp.preflight_reference_file(path, HCO)
    found = [f for f in result["findings"] if f["code"] == "REF-SCH-004"]
    assert result["gate"] == pm.GATE_BLOCKED and len(found) == 1, result["findings"]
    assert found[0]["evidence"] == {"delivered_name": delivered.decode(),
                                    "expected_name": "HCO_HCE_ID"}


def test_an_exactly_named_header_is_still_clear(tmp_path):
    path = _write(tmp_path, "exact-names.csv", b"HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n")
    result = rp.preflight_reference_file(path, HCO)
    assert result["gate"] == pm.GATE_CLEAR and result["header"] == [
        "HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID"]


def test_pinned_a_byte_order_mark_is_clear_here_and_kept_in_the_name_by_the_importer(tmp_path):
    """NOT changed in this pass; pinned so a change is deliberate. The gate
    removes the mark before matching the first column (REF-SCH-010,
    informational, decided 2026-10-04). The importer opens the file as utf-8,
    keeps the mark in the first column's name, and so finds no HCO_HCE_ID in
    any row. The two disagree; which one changes is an owner decision."""
    path = _write(tmp_path, "bom.csv",
                  b"\xef\xbb\xbfHCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n")
    result = rp.preflight_reference_file(path, HCO)
    assert result["gate"] == pm.GATE_CLEAR
    assert [f["disposition"] for f in result["findings"] if f["code"] == "REF-SCH-010"] == [
        pm.DISP_INFORMATIONAL]
    first_key, value = _importer_first_key_and_value(path, "HCO_HCE_ID")
    assert first_key == "\ufeffHCO_HCE_ID" and value is None
