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
    """A quote inside an UNQUOTED field is data to the CSV reader. It makes the
    quote count odd, so the fast placement of the final record disagrees with
    the tail; the file is then read as records, end to end, and is valid."""
    text = HEADER + "\n" + _row(1) + stray + "".join(_row(i, "x" * 40) for i in range(2, 4000))
    result = _run(tmp_path, "stray_quote.csv", text)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert "read as records to the end" in result["bound"]


def test_a_valid_final_record_larger_than_the_tail_window_is_read_not_guessed(tmp_path):
    """No record starts inside the last 64 KiB, so the tail cannot place the
    final record; the file is read as records instead of being called truncated."""
    text = _prefix() + 'S7777777,1234567893,123456,SYNTHETIC,"' + "line\n" * 20_000 + '"\n'
    result = _run(tmp_path, "huge_final.csv", text)
    assert "REF-SCH-008" not in _codes(result) and result["gate"] == pm.GATE_CLEAR, result["findings"]
    assert "read as records to the end" in result["bound"]


def test_a_cut_file_whose_tail_alone_looks_complete_is_blocked_whichever_ending(tmp_path):
    for ending in ("\r", "\r\n"):
        text = (_prefix().replace("\n", ending) + 'S7777777,1234567893,123456,SYNTHETIC,"'
                + "x" * 70_000 + ending + "a,b,c,d,e" + ending)
        result = _run(tmp_path, "open_quote_cr.csv", text)
        assert result["gate"] == pm.GATE_BLOCKED and "REF-SCH-008" in _codes(result), ending


def test_the_bound_states_how_the_final_record_was_placed(tmp_path):
    result = _run(tmp_path, "plain_large.csv", _prefix())
    assert result["gate"] == pm.GATE_CLEAR
    assert "quote characters counted over the whole file" in result["bound"]
    assert "read as records to the end" not in result["bound"]
    small = _run(tmp_path, "plain_small.csv", HEADER + "\n" + _row(1))
    assert "quote characters counted" not in small["bound"]      # the whole file was parsed


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
