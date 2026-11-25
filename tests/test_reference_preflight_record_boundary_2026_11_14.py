"""The final record of a reference extract is judged from an ESTABLISHED record
boundary (first review of #216, 2026-11-14). Four findings, each reproduced with
a synthetic file before it was fixed:

  1. a valid quoted field of more than 40 physical lines at the start of the
     64 KiB tail was BLOCKED as truncated (the alignment search gave up);
  2. a file whose records end in a lone CR was BLOCKED as truncated (only LF
     was recognised as a record boundary);
  3. MAJOR: a file cut INSIDE a quoted field whose opening quote lies before the
     tail was CLEAR - a later line of the unterminated field was read as a
     complete final record;
  4. a header, 1,000 blank lines and one valid record was BLOCKED as empty.

Pure: files in tmp_path, no database, no network. Synthetic values only.
"""
from __future__ import annotations

import pytest

from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm

HCO = sm.SOURCE_IQVIA_HCO
HEADER = "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID,ORG_NAME,ADDR_LINE"
TAIL = 64 * 1024


def _row(i: int, last: str = "1 Synthetic Way") -> str:
    return f"S{i:07d},1234567893,123456,SYNTHETIC {i},{last}\n"


def _prefix(n: int = 4000) -> str:
    """A valid header and body well over the tail window (~300 KiB)."""
    return HEADER + "\n" + "".join(_row(i, "x" * 40) for i in range(n))


def _run(tmp_path, name: str, text: str, source: str = HCO):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return rp.preflight_reference_file(path, source)


def _codes(result):
    return [f["code"] for f in result["findings"]]


# -- 1. a long quoted field at the start of the tail --------------------------

@pytest.mark.parametrize("lines", [41, 60, 400])
def test_a_quoted_field_of_many_lines_at_the_start_of_the_tail_is_not_truncation(tmp_path, lines):
    # The multi-line record begins BEFORE the tail window (its field opens with
    # 100,000 characters of padding) and its many short lines open the tail.
    inner = '""\n' * (lines - 1) + '""' + "x" * 1290      # the reviewer's shape: the last
    multi = ('S8888888,1234567893,123456,SYNTHETIC,"' + "p" * 100_000 + inner   # line is "" then text
             + '"' + chr(10))
    text = _prefix() + multi + "".join(_row(i) for i in range(9000, 9020))
    after_padding = len((inner + '"' + chr(10)).encode()) + 20 * len(_row(9000).encode())
    assert after_padding < TAIL < after_padding + 100_000, "the tail starts inside the padded field"
    result = _run(tmp_path, "long_quoted.csv", text)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert result["tail_checked"] is True


# -- 2. CR, LF and CRLF are all record boundaries ------------------------------

@pytest.mark.parametrize("ending", ["\r", "\n", "\r\n"], ids=["cr", "lf", "crlf"])
def test_a_large_file_is_read_with_any_record_ending(tmp_path, ending):
    text = "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID" + ending + ("A,1,2" + ending) * 20_000
    result = _run(tmp_path, "endings.csv", text)
    assert "REF-SCH-008" not in _codes(result) and "REF-SCH-007" not in _codes(result), result["findings"]
    assert result["gate"] == pm.GATE_CLEAR and result["rows_sampled"] == rp.SAMPLE_ROWS


@pytest.mark.parametrize("ending", ["\r", "\n", "\r\n"], ids=["cr", "lf", "crlf"])
def test_a_wide_row_file_with_any_record_ending_has_no_false_field_count_finding(tmp_path, ending):
    # Rows wide enough that the 64 KiB head ends inside a sampled record.
    text = HEADER + ending + "".join(_row(i, "X" * 180).rstrip("\n") + ending for i in range(3000))
    result = _run(tmp_path, "wide_endings.csv", text)
    assert result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert 0 < result["rows_sampled"] < rp.SAMPLE_ROWS


@pytest.mark.parametrize("ending", ["\r", "\r\n"], ids=["cr", "crlf"])
def test_a_truncated_final_record_is_still_blocked_with_cr_endings(tmp_path, ending):
    text = "HCO_HCE_ID,ORG_NPI,ORG_CCN_ID" + ending + ("A,1,2" + ending) * 20_000 + "A,1"
    result = _run(tmp_path, "cr_cut.csv", text)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result)


# -- 3. the record boundary is established before a final record is believed --

@pytest.mark.parametrize("inside", [
    "x" * 70_000 + "\na,b,c,d,e",                       # the reviewer's case: no closing quote
    "x" * 70_000 + "\na,b,c,d,e\n",                     # ... with a final newline
    "line\n" * 20_000 + "S9,1234567893,123456,SYNTHETIC,ok\n",   # many 'records' inside the field
], ids=["no-final-newline", "final-newline", "many-lines-inside"])
def test_a_file_cut_inside_a_quoted_field_opened_before_the_tail_is_blocked(tmp_path, inside):
    text = _prefix() + 'S7777777,1234567893,123456,SYNTHETIC,"' + inside
    assert len(inside.encode()) > TAIL, "the opening quote lies before the tail window"
    result = _run(tmp_path, "open_quote.csv", text)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result), result["findings"]
    finding = next(f for f in result["findings"] if f["code"] == "REF-SCH-008")
    assert finding["disposition"] == pm.DISP_BLOCKED


def test_the_same_text_with_its_closing_quote_is_clear(tmp_path):
    text = (_prefix() + 'S7777777,1234567893,123456,SYNTHETIC,"' + "line\n" * 20_000 + '"\n'
            + _row(9001))
    result = _run(tmp_path, "closed_quote.csv", text)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]


@pytest.mark.parametrize("stray", [
    'S6666666,1234567893,123456,SYNTHETIC 5" PIPE,1 Synthetic Way\n',     # one literal quote, unquoted field
    'S6666666,1234567893,123456,SYNTHETIC,5" x 3" x 1"\n',                # three of them
], ids=["one-quote", "three-quotes"])
def test_a_literal_quote_in_an_unquoted_field_does_not_block_a_valid_file(tmp_path, stray):
    """A quote inside an UNQUOTED field is data to the CSV reader: the file is
    valid CSV, and it is read as records, end to end."""
    text = HEADER + "\n" + _row(1) + stray + "".join(_row(i, "x" * 40) for i in range(2, 4000))
    result = _run(tmp_path, "stray_quote.csv", text)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert "whole file was read as CSV records" in result["bound"]


def test_a_valid_final_record_larger_than_the_tail_window_is_read_not_guessed(tmp_path):
    """No record starts inside the last 64 KiB, so the tail cannot place the
    final record; the file is read as records instead of being called truncated."""
    text = _prefix() + 'S7777777,1234567893,123456,SYNTHETIC,"' + "line\n" * 20_000 + '"\n'
    result = _run(tmp_path, "huge_final.csv", text)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert "whole file was read as CSV records" in result["bound"]


def test_a_cut_file_whose_tail_alone_looks_complete_is_blocked_whichever_ending(tmp_path):
    for ending in ("\r", "\r\n"):
        text = (_prefix().replace("\n", ending) + 'S7777777,1234567893,123456,SYNTHETIC,"'
                + "x" * 70_000 + ending + "a,b,c,d,e" + ending)
        result = _run(tmp_path, "open_quote_cr.csv", text)
        assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result), ending


# -- 4. existence of a data record is not the sampled count --------------------

def test_blank_lines_before_the_only_record_do_not_make_the_file_empty(tmp_path):
    result = _run(tmp_path, "blanks_then_one.csv", HEADER + "\n" + "\n" * 1000 + _row(1))
    assert "REF-SCH-012" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert result["rows_sampled"] == 0, "the sample window held only blank lines, and says so"


@pytest.mark.parametrize("blank", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_a_large_file_of_a_header_and_blank_lines_is_blocked_as_empty(tmp_path, blank):
    result = _run(tmp_path, "blanks_only.csv", HEADER + "\n" + blank * 70_000)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-012" in _codes(result), result["findings"]


def test_a_record_between_two_large_runs_of_blank_lines_is_found(tmp_path):
    text = HEADER + "\n" + "\n" * 70_000 + _row(1) + "\n" * 70_000
    result = _run(tmp_path, "blanks_record_blanks.csv", text)
    assert "REF-SCH-012" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]


# -- round 2 (review of fc31299, 2026-11-14): quote parity is not CSV field state --

import csv  # noqa: E402
import io  # noqa: E402
import random  # noqa: E402


def _strict_last_record(text: str):
    """What a strict CSV read of the WHOLE text says: its last non-empty record,
    or None when the text is not complete CSV. Written here, apart from the
    module under test, as the reference the gate has to agree with."""
    try:
        rows = [r for r in csv.reader(io.StringIO(text, newline=""), strict=True) if r]
    except csv.Error:
        return None
    return rows[-1] if rows else []


def test_an_even_quote_count_does_not_hide_an_unterminated_final_field(tmp_path):
    """The reviewer's file. A literal quote in an earlier UNQUOTED field plus the
    opening quote of the final, never-closed field make the number of quotes in
    front of the tail EVEN, so parity said the tail starts outside a quoted
    field and `a,b,c,d,e` was accepted as a complete final record."""
    text = ("HCO_HCE_ID,ORG_NPI,ORG_CCN_ID,col4,col5\n"
            + '1,2,3,4,5" PIPE\n'
            + "1,2,3,4,5\n" * 7000
            + '1,2,3,4,"' + "x" * 70_000 + "\na,b,c,d,e\n")
    assert _strict_last_record(text) is None, "a strict parse rejects this file"
    result = _run(tmp_path, "even_quotes_open_field.csv", text)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result), result["findings"]
    finding = next(f for f in result["findings"] if f["code"] == "REF-SCH-008")
    assert "quoted" in finding["description"], finding["description"]
    assert finding["evidence"]["reader_error"] == "ends_inside_quoted_field"


@pytest.mark.parametrize("stray_quotes", [1, 2, 3, 4])
@pytest.mark.parametrize("closed", [True, False], ids=["closed", "cut"])
def test_any_number_of_stray_quotes_before_an_open_or_closed_final_field(tmp_path, stray_quotes, closed):
    stray = "1,2,3,4," + 'q" ' * stray_quotes + "end\n"
    text = ("HCO_HCE_ID,ORG_NPI,ORG_CCN_ID,col4,col5\n" + stray + "1,2,3,4,5\n" * 7000
            + '1,2,3,4,"' + "x" * 70_000 + "\na,b,c,d,e\n" + ('"\n' if closed else ""))
    result = _run(tmp_path, "stray_then_field.csv", text)
    expected_clear = _strict_last_record(text) is not None
    assert expected_clear is closed
    assert ("REF-SCH-008" not in _codes(result)) is expected_clear, (stray_quotes, closed, result["findings"])


def test_the_gate_agrees_with_a_strict_parse_on_random_files(tmp_path):
    """Differential: for random files over the tail window, built from quoted and
    unquoted fields, doubled quotes, literal quotes in unquoted fields, embedded
    line breaks and all three record endings, then cut at a random point or left
    whole, the final-record verdict is exactly a strict whole-file parse's."""
    rng = random.Random(20261114)
    pieces = ['plain', 'two words', '"quoted"', '"with, comma"', '"doubled "" quote"',
              '"line\nbreak"', '"cr\rbreak"', '"crlf\r\nbreak"', 'stray" quote', '5" x 3"',
              '', '""', '"' + "x" * 300 + '"']
    disagreements = []
    for case in range(60):
        ending = rng.choice(["\n", "\r\n", "\r"])
        body = ["A,B,C,D,E"]
        size = 0
        while size < 90_000:
            row = ",".join(rng.choice(pieces) for _ in range(5))
            body.append(row)
            size += len(row) + 2
        text = ending.join(body) + ending
        if rng.random() < 0.6:                       # cut somewhere in the last part
            text = text[:len(text) - rng.randint(1, 400)]
        if rng.random() < 0.3:                       # or end on an opened, never closed field
            text += 'a,b,c,d,"open' + ending + "a,b,c,d,e" + ending
        path = tmp_path / f"diff_{case}.csv"
        path.write_bytes(text.encode("utf-8"))
        result = rp.preflight_reference_file(path, HCO)
        last = _strict_last_record(text)
        want_block = last is None or len(last) != 5
        got_block = "REF-SCH-008" in _codes(result)
        if want_block != got_block:
            disagreements.append((case, repr(ending), want_block, got_block))
    assert not disagreements, disagreements


def test_a_field_beyond_the_readers_limit_is_blocked_for_what_it_is(tmp_path):
    """The importer reads with the csv module's field limit as it stands
    (csv.field_size_limit(), 131,072 by default) and fails on a longer field. The
    gate reads with the same limit, blocks the same file, and says a field is too
    long - not that the file is truncated."""
    limit = csv.field_size_limit()
    text = _prefix() + 'S7777777,1234567893,123456,SYNTHETIC,"' + "x" * (limit + 10) + '"\n' + _row(9001)
    with pytest.raises(csv.Error):                   # what the importer's reader does with it
        list(csv.DictReader(io.StringIO(text, newline="")))
    result = _run(tmp_path, "long_field.csv", text)
    assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result)
    finding = next(f for f in result["findings"] if f["code"] == "REF-SCH-008")
    assert finding["evidence"]["reader_error"] == "field_longer_than_reader_limit"
    assert finding["evidence"]["reader_field_limit"] == limit
    assert "longer than" in finding["description"] and "truncated" not in finding["description"]
    assert csv.field_size_limit() == limit, "the process-wide limit is not changed"


def test_a_field_just_inside_the_readers_limit_is_read(tmp_path):
    limit = csv.field_size_limit()
    text = _prefix() + 'S7777777,1234567893,123456,SYNTHETIC,"' + "x" * (limit - 10) + '"\n' + _row(9001)
    result = _run(tmp_path, "long_ok_field.csv", text)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]


def test_a_large_files_bound_says_the_whole_file_was_read_as_records(tmp_path):
    result = _run(tmp_path, "plain_large.csv", _prefix())
    assert result["gate"] == pm.GATE_CLEAR
    assert "whole file was read as CSV records" in result["bound"]
    assert "not a full-file scan" not in result["bound"]
    assert "quote characters counted" not in result["bound"]
    assert result["records_read"] == 4000 + 1          # header + 4,000 records
    small = _run(tmp_path, "plain_small.csv", HEADER + "\n" + _row(1))
    assert "whole file was read as CSV records" in small["bound"]
    assert small["records_read"] == 2


def test_no_quote_parity_shortcut_remains():
    for name in ("_quotes_before", "_record_starts", "_final_record_from_tail"):
        assert not hasattr(rp, name), name
