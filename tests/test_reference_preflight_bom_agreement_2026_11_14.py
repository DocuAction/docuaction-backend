"""The reference preflight and the importer read a UTF-8 byte-order mark the
same way (fifth pass on #216, 2026-11-14; owner instruction: "Fix #216's BOM
disagreement so preflight and importer handle the same file consistently").

Before: the gate removed a leading mark before matching the first column
(REF-SCH-010, informational, decided 2026-10-04) and answered CLEAR, while the
importer opened the same file as `utf-8`, kept the mark in the first column's
name, found no identity column in any row and rejected every one.

After: the 2026-10-04 decision stands and the IMPORTER agrees with it. Both
sides decode with the one encoding `reference_preflight.REFERENCE_FILE_ENCODING`
(`utf-8-sig`): a mark at the very start of the file is not part of the first
column's name; a U+FEFF anywhere else is data and is kept; only the UTF-8 mark
is tolerated. Nothing computed on the raw bytes changes.

Pure: files in tmp_path, no database, no network. Synthetic values only. The
database side (a real import and a resumed import of a marked file) is in
tests/test_iqvia_import_durability.py.
"""
from __future__ import annotations

import codecs
import csv
import hashlib

import pytest

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm

BOM = codecs.BOM_UTF8
MARK = "﻿"

#: (source, header, one data row builder) - the identity column is FIRST in
#: each, so the mark lands on the column each importer keys on.
SOURCES = [
    (sm.SOURCE_IQVIA_HCO, ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID"], "HCO_HCE_ID"),
    (sm.SOURCE_IQVIA_HCP, ["HCP_HCE_ID", "ADDR_ID", "NPI"], "HCP_HCE_ID"),
    (sm.SOURCE_IQVIA_AFFILIATION, ["HCP_HCE_ID", "HCO_HCE_ID", "AFFL_TYP_ID", "TITL_TYP_ID"],
     "HCP_HCE_ID"),
]


def _body(header, n=5, ending="\r\n"):
    lines = [",".join(header)] + [
        ",".join(f"SYNTHETIC-{c}-{i}" for c in range(len(header))) for i in range(n)]
    return (ending.join(lines) + ending).encode("utf-8")


def _write(tmp_path, name, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _rows(path):
    return [row for _line, row in ii.iter_reference_rows(path)]


def _blocked(result):
    return sorted(f["code"] for f in result["findings"] if f["disposition"] == pm.DISP_BLOCKED)


def test_both_sides_name_one_encoding():
    assert rp.REFERENCE_FILE_ENCODING == "utf-8-sig"
    assert ii.REFERENCE_FILE_ENCODING is rp.REFERENCE_FILE_ENCODING


@pytest.mark.parametrize("source,header,identity", SOURCES)
@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_a_marked_file_is_clear_and_the_importer_reads_the_same_records(
        tmp_path, source, header, identity, ending):
    plain = _write(tmp_path, "plain.csv", _body(header, ending=ending))
    marked = _write(tmp_path, "marked.csv", BOM + _body(header, ending=ending))

    result = rp.preflight_reference_file(marked, source)
    assert result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert [f["disposition"] for f in result["findings"] if f["code"] == "REF-SCH-010"] == [
        pm.DISP_INFORMATIONAL]
    assert result["header"] == header

    rows = _rows(marked)
    assert list(rows[0].keys()) == header, "the mark is not part of the first column's name"
    assert all(row[identity] == f"SYNTHETIC-0-{i}" for i, row in enumerate(rows))
    assert len(rows) == result["records_read"] - 1, "the same records, the header apart"

    # Nothing else changes: the rows, and the per-row hash stored with each
    # observation, are those of the same file delivered without the mark.
    assert rows == _rows(plain)
    assert [ii._row_sha256(r) for r in rows] == [ii._row_sha256(r) for r in _rows(plain)]
    assert rp.preflight_reference_file(plain, source)["records_read"] == result["records_read"]


def test_line_numbers_are_the_same_with_and_without_the_mark(tmp_path):
    header = SOURCES[0][1]
    plain = _write(tmp_path, "plain.csv", _body(header))
    marked = _write(tmp_path, "marked.csv", BOM + _body(header))
    assert [n for n, _ in ii.iter_reference_rows(marked)] == [2, 3, 4, 5, 6]
    assert [n for n, _ in ii.iter_reference_rows(marked)] == [
        n for n, _ in ii.iter_reference_rows(plain)]


def test_what_is_computed_on_the_raw_bytes_does_not_change(tmp_path):
    """The file hash is the snapshot's identity and the duplicate key, and the
    preserved original is these bytes. Reading the file decodes a copy; the
    bytes, and so the hash, are the delivered ones - mark included."""
    data = BOM + _body(SOURCES[0][1])
    marked = _write(tmp_path, "marked.csv", data)
    before = hashlib.sha256(data).hexdigest()
    assert ii.file_sha256(marked) == before
    _rows(marked)
    rp.preflight_reference_file(marked, sm.SOURCE_IQVIA_HCO)
    assert marked.read_bytes() == data and ii.file_sha256(marked) == before
    # A marked and an unmarked delivery are different bytes: two identities, as before.
    plain = _write(tmp_path, "plain.csv", _body(SOURCES[0][1]))
    assert ii.file_sha256(plain) != before


@pytest.mark.parametrize("where,data,check", [
    ("inside a later field",
     b"HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\nB,x\xef\xbb\xbfy,2\n",
     lambda rows: rows[1]["ORG_NPI"] == f"x{MARK}y"),
    ("at the start of a later line",
     b"HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n\xef\xbb\xbfB,1,2\n",
     lambda rows: rows[1]["HCO_HCE_ID"] == f"{MARK}B"),
    ("at the end of the file's last field",
     b"HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\xef\xbb\xbf\n",
     lambda rows: rows[0]["ORG_CCN_ID"] == f"2{MARK}"),
    ("after a leading mark, inside the data",
     BOM + b"HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\n\xef\xbb\xbfA,1,2\n",
     lambda rows: rows[0]["HCO_HCE_ID"] == f"{MARK}A"),
])
def test_a_mark_anywhere_else_is_data_and_is_kept_by_both_sides(tmp_path, where, data, check):
    path = _write(tmp_path, "data-mark.csv", data)
    rows = _rows(path)
    assert check(rows), (where, rows)
    assert list(rows[0].keys())[0] == "HCO_HCE_ID"
    result = rp.preflight_reference_file(path, sm.SOURCE_IQVIA_HCO)
    assert result["gate"] == pm.GATE_CLEAR, (where, result["findings"])
    assert len(rows) == result["records_read"] - 1
    assert path.read_bytes() == data


def test_two_marks_at_the_start_leave_one_in_the_name_and_both_sides_say_so(tmp_path):
    """Only ONE leading mark is a byte-order mark. A second is a character of
    the first column's name: the gate does not find the identity column and the
    importer does not either. They agree; nothing is guessed."""
    path = _write(tmp_path, "two.csv", BOM + BOM + b"HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n")
    result = rp.preflight_reference_file(path, sm.SOURCE_IQVIA_HCO)
    assert result["gate"] == pm.GATE_BLOCKED
    rows = _rows(path)
    assert list(rows[0].keys())[0] == f"{MARK}HCO_HCE_ID" and rows[0].get("HCO_HCE_ID") is None


@pytest.mark.parametrize("source,header,identity", SOURCES)
def test_a_marked_file_without_its_identity_column_is_still_blocked(
        tmp_path, source, header, identity):
    without = ["SOME_OTHER_ID"] + [c for c in header[1:] if c != identity]
    path = _write(tmp_path, "missing.csv", BOM + _body(without))
    result = rp.preflight_reference_file(path, source)
    assert result["gate"] == pm.GATE_BLOCKED, result["findings"]
    assert all(row.get(identity) is None for row in _rows(path))


@pytest.mark.parametrize("name,encoded", [
    ("utf-16-le with its mark", codecs.BOM_UTF16_LE + "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n"
     .encode("utf-16-le")),
    ("utf-16-be with its mark", codecs.BOM_UTF16_BE + "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n"
     .encode("utf-16-be")),
    ("utf-32-le with its mark", codecs.BOM_UTF32_LE + "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n"
     .encode("utf-32-le")),
    ("utf-32-be with its mark", codecs.BOM_UTF32_BE + "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID\nA,1,2\n"
     .encode("utf-32-be")),
])
def test_only_the_utf8_mark_is_tolerated(tmp_path, name, encoded):
    """Not loosened: another encoding's mark is not UTF-8 text. The gate blocks
    the file and the importer's reader cannot use it either."""
    path = _write(tmp_path, "other.csv", encoded)
    result = rp.preflight_reference_file(path, sm.SOURCE_IQVIA_HCO)
    assert result["gate"] == pm.GATE_BLOCKED, (name, result["findings"])
    assert not [f for f in result["findings"] if f["code"] == "REF-SCH-010"], name
    try:
        rows = _rows(path)
    except (UnicodeDecodeError, csv.Error):
        return
    assert all(row.get("HCO_HCE_ID") is None for row in rows), (name, rows)


def test_an_unmarked_file_is_read_exactly_as_before(tmp_path):
    header = SOURCES[0][1]
    path = _write(tmp_path, "plain.csv", _body(header))
    with open(path, "r", encoding="utf-8", newline="") as fh:
        as_before = list(csv.DictReader(fh))
    assert _rows(path) == as_before
    result = rp.preflight_reference_file(path, sm.SOURCE_IQVIA_HCO)
    assert result["gate"] == pm.GATE_CLEAR
    assert not [f for f in result["findings"] if f["code"] == "REF-SCH-010"]


def test_the_importer_has_one_reader_of_the_staged_file_and_it_uses_that_encoding():
    """Source check: the import engine reads rows only through
    `iter_reference_rows`, and no text-mode open of the staged file in this
    module names another encoding. A reader added later with its own
    `encoding="utf-8"` is caught here."""
    import inspect
    import re

    source = inspect.getsource(ii)
    assert "for line_no, row in iter_reference_rows(path)" in inspect.getsource(ii._import_csv)
    text_opens = re.findall(r'open_no_follow\([^)]*encoding=([^,)]+)', source)
    assert text_opens == ["REFERENCE_FILE_ENCODING"], text_opens
    assert "csv.DictReader" not in inspect.getsource(ii._import_csv)
