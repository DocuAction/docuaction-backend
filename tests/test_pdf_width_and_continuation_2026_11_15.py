"""CodeRabbit on #232 (7534eb6), both reproduced before the fix:
(1) a run of wide glyphs was sliced by the 0.62 em average and could exceed a page; (2) the continuation helper accepted a
continuation named "A" into the record "AB". Pure tests (no PDF engine)."""
import pytest

import record_review_pdf_support as s
from app.reports.data import october_review_dataset as d


def _lines(text, cpl):
    return -(-len(text) // cpl)


def test_a_run_of_wide_glyphs_is_sliced_for_its_real_width():
    cpl, max_lines = 10, 40
    run = "W" * 400
    slices = d.split_for_rows(run, cpl, max_lines)
    assert "".join(slices) == run, "slicing never edits the text"
    worst = max(len(x) for x in slices)
    assert len(slices) >= 2 and worst <= int(cpl / 1.6) * max_lines, [len(x) for x in slices]
    assert len(d.split_for_rows("W" * 400, 10, 40)) > len(d.split_for_rows("w" * 400, 10, 40)), "wide glyphs need more slices than average ones"


@pytest.mark.parametrize("text", ["MMMM @@@@ %%%% WWWW " * 40])
def test_wide_text_never_exceeds_the_line_budget(text):
    cpl, max_lines = 20, 24
    adjusted = d._width_adjusted_chars_per_line(text, cpl)
    assert adjusted < cpl
    assert "".join(d.split_for_rows(text, cpl, max_lines)) == text


def test_ordinary_mixed_text_keeps_the_unchanged_estimate():
    text = "Original QA comment: the submitted NPI did not resolve in NPPES on the assessment date; a manual search found a plausible match. " * 5
    assert d._width_adjusted_chars_per_line(text, 40) == 40


def _row(identity, text=""):
    return [identity, text]


def test_a_continuation_named_for_a_prefix_of_the_record_is_rejected():
    rows = [_row("ABline3.B1/CLEAN", "x"), _row("A" + s.CONTINUED + "2of2)", "y")]
    with pytest.raises(AssertionError):
        s.merge_continuations(rows, 0)
    ok = [_row("AB", "x"), _row("AB" + s.CONTINUED + "2of2)", "y")]
    assert s.merge_continuations(ok, 0) == [["AB", "xy"]]
    detail = [_row("ABline3.B1/CLEAN", "x"), _row("AB" + s.CONTINUED + "2of2)", "y")]
    assert s.merge_continuations(detail, 0)[0][1] == "xy"
