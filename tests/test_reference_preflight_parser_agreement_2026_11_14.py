"""The reference preflight and the importer's reader, case by case (third pass
on #216, 2026-11-14). Five CSV-parser items were admitted after the second
review; each is fixed here or pinned as it stands:

  a. FIXED  - a field beyond the csv reader's limit inside the SAMPLE made the
              preflight raise instead of answering BLOCKED.
  b. FIXED  - a record with the wrong number of fields in the MIDDLE of a large
              file was not reported: only the sampled records and the final one
              were counted, although every record is read.
  c. FIXED  - invalid UTF-8 beyond the first 64 KiB passed the preflight (it
              decoded with replacement characters) and failed in the importer,
              which decodes strictly.
  d. PINNED - text after a closing quote (`"ab"c`): the preflight's strict
              reader blocks it; the importer's reader accepts it. Not loosened.
  e. PINNED - a field beyond the reader's limit: blocked here, and the
              importer's reader fails on the same file.

The importer's reader is the importer's own (`iqvia_import.iter_reference_rows`:
text mode, the preflight's REFERENCE_FILE_ENCODING, newline="", csv.DictReader),
called directly since the fifth pass, so the two cannot drift apart unnoticed.

Pure: files in tmp_path, no database, no network. Synthetic values only.
"""
from __future__ import annotations

import csv
import inspect

import pytest

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm

HCO = sm.SOURCE_IQVIA_HCO
HEADER = "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID,ORG_NAME,ADDR_LINE"
TAIL = 64 * 1024


def _row(i: int, last: str = "1 Synthetic Way") -> str:
    return f"S{i:07d},1234567893,123456,SYNTHETIC {i},{last}\n"


def _body(n: int = 4000) -> str:
    """A valid header and records well over 64 KiB (~300 KiB)."""
    return HEADER + "\n" + "".join(_row(i, "x" * 40) for i in range(n))


def _write(tmp_path, name: str, data) -> object:
    path = tmp_path / name
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _findings(result, code):
    return [f for f in result["findings"] if f["code"] == code]


def _importer_reads(path) -> int:
    """The number of records the importer's OWN reader yields for `path`
    (fifth pass, 2026-11-14: `iqvia_import.iter_reference_rows`, no longer a
    restatement of how the importer opens the file)."""
    return sum(1 for _ in ii.iter_reference_rows(path))


def test_the_importers_reader_is_what_these_tests_call():
    """Updated in the fifth pass: the importer now decodes with the preflight's
    REFERENCE_FILE_ENCODING (utf-8-sig) instead of utf-8, so a leading
    byte-order mark is read the same way by both; everything else about the
    reader - text mode, newline="", csv.DictReader, the process's own field
    limit - is as before."""
    source = inspect.getsource(ii)
    assert ('open_no_follow(path, "r", encoding=REFERENCE_FILE_ENCODING, newline="")'
            in source)
    assert ii.REFERENCE_FILE_ENCODING == rp.REFERENCE_FILE_ENCODING == "utf-8-sig"
    assert "csv.DictReader(fh)" in source
    assert "field_size_limit" not in source, "the importer does not change the reader's limit"


@pytest.fixture
def small_field_limit():
    """A reduced csv field limit, restored whatever happens."""
    previous = csv.field_size_limit()
    csv.field_size_limit(1024)
    try:
        yield 1024
    finally:
        csv.field_size_limit(previous)


# -- a. a field beyond the reader's limit inside the sample ---------------------

@pytest.mark.parametrize("where", ["first_record", "header", "later_record"])
def test_a_a_field_beyond_the_limit_in_the_sample_is_blocked_not_raised(
        tmp_path, small_field_limit, where):
    long_field = "y" * (small_field_limit + 50)
    if where == "header":
        text = HEADER + "," + long_field + "\n" + _row(1)
    elif where == "first_record":
        text = HEADER + "\n" + f"S0000001,1234567893,123456,SYNTHETIC,{long_field}\n" + _row(2)
    else:
        text = (HEADER + "\n" + "".join(_row(i) for i in range(20))
                + f"S0000099,1234567893,123456,SYNTHETIC,{long_field}\n" + _row(100))
    path = _write(tmp_path, f"limit_{where}.csv", text)
    result = rp.preflight_reference_file(path, HCO)          # must not raise
    assert result["gate"] == pm.GATE_BLOCKED
    blocked = _findings(result, "REF-SCH-008")
    assert len(blocked) == 1, result["findings"]
    assert blocked[0]["evidence"]["reader_error"] == rp.READER_FIELD_TOO_LONG
    assert blocked[0]["evidence"]["reader_field_limit"] == small_field_limit
    assert "longer than the CSV reader's limit" in blocked[0]["description"]
    assert long_field[:40] not in str(result), "no field content in the result"
    with pytest.raises(csv.Error):                            # e. the importer agrees
        _importer_reads(path)


def test_a_the_reduced_limit_does_not_leak():
    assert csv.field_size_limit() == 131072


# -- b. a wrong field count anywhere in the file --------------------------------

@pytest.mark.parametrize("extra", [",EXTRA", None])
def test_b_a_wrong_field_count_in_the_middle_of_a_large_file_is_reported(tmp_path, extra):
    rows = [_row(i, "x" * 40) for i in range(4000)]
    for index in (2500, 2600, 3900):
        rows[index] = (rows[index].rstrip("\n") + extra + "\n" if extra
                       else rows[index].rsplit(",", 1)[0] + "\n")      # one field short
    path = _write(tmp_path, "mid.csv", HEADER + "\n" + "".join(rows))
    result = rp.preflight_reference_file(path, HCO)
    assert result["rows_sampled"] < 2500, "the faults lie beyond the sampled records"
    assert result["gate"] == pm.GATE_BLOCKED
    found = _findings(result, "REF-SCH-007")
    assert len(found) == 1, "ONE technical finding, not one per record"
    evidence = found[0]["evidence"]
    assert evidence["records_with_wrong_field_count"] == 3
    assert evidence["first_lines"] == [2502, 2602, 3902]      # physical lines; header is line 1
    assert evidence["expected"] == 5 and evidence["fields"] == (6 if extra else 4)
    assert evidence["line"] == 2502
    assert "SYNTHETIC" not in str(evidence) and "EXTRA" not in str(found[0]), "no row content"
    assert not _findings(result, "REF-SCH-008"), "the final record is complete"


def test_b_many_wrong_records_are_counted_and_only_the_first_few_lines_listed(tmp_path):
    rows = [_row(i, "x" * 40) for i in range(4000)]
    for index in range(2000, 2400):
        rows[index] = rows[index].rstrip("\n") + ",EXTRA\n"
    path = _write(tmp_path, "many.csv", HEADER + "\n" + "".join(rows))
    evidence = _findings(rp.preflight_reference_file(path, HCO), "REF-SCH-007")[0]["evidence"]
    assert evidence["records_with_wrong_field_count"] == 400
    assert evidence["first_lines"] == [2002, 2003, 2004, 2005, 2006]


def test_b_blank_lines_and_a_valid_large_file_have_no_field_count_finding(tmp_path):
    rows = [_row(i, "x" * 40) for i in range(4000)]
    rows[3000:3000] = ["\n"] * 25                              # blank lines mid-file: tolerated
    result = rp.preflight_reference_file(
        _write(tmp_path, "ok.csv", HEADER + "\n" + "".join(rows)), HCO)
    assert not _findings(result, "REF-SCH-007") and not _findings(result, "REF-SCH-008")
    assert result["records_read"] == 4001
    assert "every record's field count" in result["bound"]
    assert f"the first {result['rows_sampled']} record(s)" in result["bound"]


def test_b_a_short_final_record_is_one_finding_as_before(tmp_path):
    """A truncated final record of a large file stays REF-SCH-008 alone; it is
    not ALSO counted as a wrong field count."""
    path = _write(tmp_path, "cut.csv", _body() + "S9999999,12")
    result = rp.preflight_reference_file(path, HCO)
    assert len(_findings(result, "REF-SCH-008")) == 1 and not _findings(result, "REF-SCH-007")


def test_b_a_sampled_wrong_record_is_reported_as_before(tmp_path):
    text = HEADER + "\n" + _row(1).rstrip("\n") + ",EXTRA\n" + _row(2)
    found = _findings(rp.preflight_reference_file(_write(tmp_path, "s.csv", text), HCO),
                      "REF-SCH-007")
    assert len(found) == 1
    assert found[0]["evidence"]["line"] == 2 and found[0]["evidence"]["fields"] == 6


# -- c. invalid UTF-8 beyond the first 64 KiB -----------------------------------

@pytest.mark.parametrize("bad", [b"\xff", b"\xc3\x28", b"\xe2\x82"])
def test_c_invalid_utf8_beyond_the_sample_is_blocked_with_its_byte_offset(tmp_path, bad):
    good = _body().encode("utf-8")
    marker = b"S0003000,"
    at = good.index(marker) + len(marker)
    assert at > TAIL
    data = good[:at] + bad + good[at:]
    path = _write(tmp_path, "enc.csv", data)
    with pytest.raises(UnicodeDecodeError):                    # what the importer does
        _importer_reads(path)
    result = rp.preflight_reference_file(path, HCO)
    assert result["gate"] == pm.GATE_BLOCKED
    found = _findings(result, "REF-SCH-006")
    assert len(found) == 1, result["findings"]
    assert found[0]["evidence"] == {"byte_offset": at, "expected": "utf-8"}
    assert found[0]["field_name"] == "__encoding__"
    assert not _findings(result, "REF-SCH-008"), "not reported as truncation"


def test_c_invalid_utf8_as_the_last_bytes_is_blocked(tmp_path):
    good = _body().encode("utf-8")
    path = _write(tmp_path, "enc_end.csv", good[:-1] + b"\xe2\x82")
    with pytest.raises(UnicodeDecodeError):
        _importer_reads(path)
    found = _findings(rp.preflight_reference_file(path, HCO), "REF-SCH-006")
    assert len(found) == 1 and found[0]["evidence"]["byte_offset"] == len(good) - 1


def test_c_valid_multibyte_text_across_the_file_is_clear(tmp_path):
    text = HEADER + "\n" + "".join(_row(i, "Café é中\U0001f600 " * 4) for i in range(3000))
    path = _write(tmp_path, "multi.csv", text)
    assert _importer_reads(path) == 3000
    result = rp.preflight_reference_file(path, HCO)
    assert not _findings(result, "REF-SCH-006") and not _findings(result, "REF-SCH-008")
    assert result["records_read"] == 3001


# -- d. text after a closing quote: pinned, not loosened ------------------------

def test_d_text_after_a_closing_quote_is_blocked_here_and_read_by_the_importer(tmp_path):
    """PINNED DIFFERENCE. The preflight reads strictly; the importer's reader is
    not strict and folds the text into the field. The gate is the stricter of
    the two on purpose and is not loosened here: a file it blocks is one whose
    record boundaries a strict CSV reader cannot vouch for. Changing either
    side must be a deliberate decision that changes this test."""
    text = _body(50) + 'S9000001,1234567893,123456,"ab"c,1 Synthetic Way\n' + _row(9000002)
    path = _write(tmp_path, "after_quote.csv", text)
    assert _importer_reads(path) == 52                          # the importer reads every record
    with open(path, "r", encoding="utf-8", newline="") as fh:
        odd = [r for r in csv.DictReader(fh) if r["HCO_HCE_ID"] == "S9000001"][0]
    assert odd["ORG_NAME"] == "abc"                             # ...and folds the text in
    result = rp.preflight_reference_file(path, HCO)
    assert result["gate"] == pm.GATE_BLOCKED
    found = _findings(result, "REF-SCH-008")
    assert len(found) == 1 and found[0]["evidence"]["reader_error"] == rp.READER_NOT_WELL_FORMED


# -- e. a field beyond the default limit: gate and importer agree ---------------

def test_e_a_field_beyond_the_default_limit_fails_in_both(tmp_path):
    limit = csv.field_size_limit()
    text = _body(50) + f"S9000001,1234567893,123456,SYNTHETIC,{'z' * (limit + 10)}\n" + _row(9000002)
    path = _write(tmp_path, "long.csv", text)
    with pytest.raises(csv.Error):
        _importer_reads(path)
    found = _findings(rp.preflight_reference_file(path, HCO), "REF-SCH-008")
    assert len(found) == 1 and found[0]["evidence"]["reader_error"] == rp.READER_FIELD_TOO_LONG
    assert csv.field_size_limit() == limit
