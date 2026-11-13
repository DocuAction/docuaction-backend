"""The record-review PDF source (to_html) after the 2026-11-13 presentation pass.

Pure (no database, no PDF engine): the HTML is the single input to both PDF engines,
so what is asserted here holds for the rendered PDF's content. Layout itself was
inspected by rendering (see qa-evidence/2026-11-13-record-review-pdf).
"""
from __future__ import annotations

import html as html_mod
import re

import pytest

from app.reports.data import october_review_dataset as d

LOADER_NOTE_A = ("Not established by this loader: no evidence row for the linked entity at or "
                 "before the cutoff exists in the evidence store it reads. This does not show "
                 "the source was never checked elsewhere")
LOADER_NOTE_B = ("Not established by this loader: the record has no linked entity, so no "
                 "entity-linked verification evidence is available to it here.")
SRC = {"file": "c.xlsx", "sha256": "ab" * 32, "sheet": "S", "column": "QA Comments"}


def _f(i, bucket="B1", **over):
    f = {"agt_record_id": f"agt-{i}", "source_line": i + 1, "qhin_number": "2.16.840.1.113883.4.391.1000",
         "hcid": f"urn:oid:9.99.{i}", "tefca_id": f"T-{i}", "organization": f"Org {i}",
         "participation_level": "Participant", "activity_status": "Active", "purpose_of_use": "T-PYMNT",
         "submitted_npi": "1234567893", "bucket": bucket, "status": "CLEAN", "bucket_basis": "rule",
         "nppes": LOADER_NOTE_A, "pecos": LOADER_NOTE_A, "leie": LOADER_NOTE_A, "sam": LOADER_NOTE_A,
         "submitted_address": "1 Way, Town, MA, 02101, USA", "reasons": "why"}
    f.update(over)
    return f


def _page(facts, **kw):
    kw.setdefault("report_id", "R-TEST")
    return d.to_html(d.build_dataset(facts, **kw))


def _text(page):
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", page))


def test_grouped_headers_fixed_widths_and_repeating_thead():
    page = _page([_f(1)])
    assert "<thead>" in page and "display: table-header-group" in d.PDF_CSS
    for group, _cols in d.PDF_COLUMN_GROUPS:
        assert f"scope='colgroup'>{html_mod.escape(group)}</th>" in page
    assert abs(sum(w for _g, cs in d.PDF_COLUMN_GROUPS for _c, w in cs) - 100) < 1e-6
    assert abs(sum(w for _c, w in d.PDF_DETAIL_COLUMNS) - 100) < 1e-6
    assert [c for _g, cs in d.PDF_COLUMN_GROUPS for c, _w in cs] == d.PDF_COLUMNS
    assert "counter(page)" in d.PDF_CSS and "counter(pages)" in d.PDF_CSS       # page X of Y
    assert "break-inside: avoid" in d.PDF_CSS                                   # no row split across pages


def test_every_record_value_the_pdf_shows_is_the_datasets_own():
    ds = d.build_dataset([_f(1), _f(2, "B4", status="HELD", held_reason="blocker"),
                          _f(3, None, status="NOT CURATED")], report_id="R")
    page, rows = d.to_html(ds), ds["sheets"]["Records"]["rows"]
    text = _text(page)
    col = {c: i for i, c in enumerate(d.RECORD_COLUMNS)}
    for r in rows:
        for c in ("AGT_Record_ID", "Submitted NPI (original, verbatim)", "Bucket", "Status", "Reasons"):
            assert r[col[c]] in text, (c, r[col[c]])
    assert text.count("agt-2") >= 2            # main table and the detail/unclassified tables
    assert "NOT CLASSIFIED" in text and "HELD" in text


def test_loader_placeholder_sentences_fold_to_one_numbered_note_each_and_real_results_do_not():
    page = _page([_f(1), _f(2, nppes=LOADER_NOTE_B, pecos="Enrolled: ACTIVE (PECOS 2026-11-08)")])
    assert page.count(html_mod.escape(LOADER_NOTE_A)) == 1 and page.count(html_mod.escape(LOADER_NOTE_B)) == 1
    assert "Not established <sup>[1]</sup>" in page and "Not established <sup>[2]</sup>" in page
    assert "Enrolled: ACTIVE (PECOS 2026-11-08)" in page       # a real result is never shortened
    assert "<ol class='notes'>" in page
    # nothing is folded when no placeholder is present
    plain = _page([_f(1, nppes="x", pecos="y", leie="z", sam="w")])
    assert "<ol class='notes'>" not in plain and "<sup>" not in plain


def test_three_comment_kinds_stay_separate_verbatim_and_whitespace_preserving():
    orig = "orig  two spaces\nsecond line\t<tab>"
    iz = "izaan & co"
    clar = "[PECOS-221] clarification"
    page = _page([_f(1, qa_comments=orig, qa_comments_source=SRC,
                     qa_comments_izaan=iz, qa_comments_izaan_source=SRC, report_clarifications=clar)])
    detail = page.split("Addresses and commentary (verbatim)")[1]
    cells = re.findall(r"<td class='verbatim'>(.*?)</td>", detail, flags=re.S)
    assert len(cells) == 5                                           # address, proposed, orig, izaan, clar
    assert [html_mod.unescape(c) for c in cells[2:]] == [orig, iz, clar]
    assert "white-space: pre-wrap" in d.PDF_CSS
    assert f"<th scope='col'>{html_mod.escape(d.ORIGINAL_COMMENTS_COLUMN)}</th>" in detail
    assert f"<th scope='col'>{html_mod.escape(d.IZAAN_COMMENTS_COLUMN)}</th>" in detail
    assert "<th scope='col'>Report Clarifications</th>" in detail
    assert "not determinations, resolutions or QA approvals" in detail


def test_detail_table_lists_only_records_with_something_to_show():
    page = _page([_f(1, submitted_address=""), _f(2, submitted_address="", qa_comments="c", qa_comments_source=SRC)])
    detail = page.split("Addresses and commentary (verbatim)")[1]
    assert "agt-2" in detail and "agt-1" not in detail and "(1 of 2 records)" in detail
    none = _page([_f(1, submitted_address="")])
    assert ("No record in this dataset carries a submitted address, a proposed corrected address, "
            "a comment or a clarification") in none


def test_draft_marking_synthetic_marking_and_unclassified_list():
    page = _page([_f(1), _f(2, None, status="NOT CURATED")], classification="DRAFT - SYNTHETIC TEST DATA")
    assert "DRAFT / NOT FINAL / INDEPENDENT QA PENDING" in d.PDF_CSS            # every page footer
    assert "INDEPENDENT QA PENDING" in page and "DATASET INCOMPLETE" in page
    assert "SYNTHETIC TEST DATA - not a delivered Government record" in page
    assert "Unclassified records (1)" in page and "agt-2" in page.split("Unclassified records (1)")[1]
    real = _page([_f(1)], classification="DRAFT")
    assert "SYNTHETIC" not in real and "Unclassified records" not in real
    assert "<title>R-TEST - record review (DRAFT)</title>" in real


def test_held_is_counted_once_inside_b4_and_the_pdf_says_so():
    ds = d.build_dataset([_f(1, "B4", status="HELD", held_reason="x"), _f(2, "B4")], report_id="R")
    page = _text(d.to_html(ds))
    assert ds["summary"]["buckets"]["B4"] == 2 and ds["summary"]["held_inside_b4"] == 1
    assert "of which HELD (counted once, inside B4)" in page
    assert "HELD records are counted once, inside B4, with their status and reasons retained" in page
    assert "Field findings are a separate grain" in page


@pytest.mark.parametrize("value,cls", [("B1", "chip-B1"), ("B4", "chip-B4"), ("HELD", "chip-HELD"),
                                       ("NOT CLASSIFIED", "chip-NOTCLASSIFIED"), ("CLEAN", "chip\"")])
def test_bucket_and_status_chips(value, cls):
    assert cls in d._chip(value)
    assert d._chip("") == ""


def test_html_is_escaped_everywhere_it_shows_data():
    page = _page([_f(1, organization="<b>x</b> & y", reasons="<script>", qa_comments="<i>c</i>",
                     qa_comments_source=SRC, nppes="<u>")])
    assert "<b>x</b>" not in page and "<script>" not in page and "<i>c</i>" not in page and "<u>" not in page
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in page


# ── review findings on #213 (CodeRabbit, 2026-11-13) ─────────────────────────

def _lines_needed(piece: str, per_line: int) -> int:
    """An independent count of the lines `piece` occupies: greedy word wrap, a
    newline ends a line, a word longer than a line is broken across lines.
    Written separately from split_for_rows so the two cannot share a mistake.
    A newline that ENDS the piece does not open another line: nothing follows
    it in that cell."""
    lines, col = 1, 0
    body = piece[:-1] if piece.endswith("\n") else piece
    for token in re.findall(r"\n|[^\S\n]+|\S+", body):
        if token == "\n":
            lines, col = lines + 1, 0
        elif token.isspace():
            col += len(token)
        else:
            if col and col + len(token) > per_line:
                lines, col = lines + 1, 0
            while len(token) > per_line:
                token, lines = token[per_line:], lines + 1
            col += len(token)
    return lines


def test_a_run_of_newlines_cannot_put_more_lines_in_a_slice_than_the_budget():
    """Before: 100 consecutive newlines stayed in ONE slice, and the detail cell
    (white-space: pre-wrap) rendered all of them in one row."""
    text = "start" + "\n" * 100 + "end"
    slices = d.split_for_rows(text, 31, 24)
    assert "".join(slices) == text
    assert max(piece.count("\n") for piece in slices) <= 24
    assert all(_lines_needed(piece, 31) <= 24 for piece in slices)
    assert len(slices) == 5                       # 24 + 24 + 24 + 24 newlines, then 4 and "end"


def test_text_that_ends_with_the_newline_that_fills_a_slice_adds_no_empty_slice():
    text = "a\n" * 24
    slices = d.split_for_rows(text, 31, 24)
    assert "".join(slices) == text and all(slices) and len(slices) == 1


def test_a_run_of_an_exact_multiple_of_the_line_width_leaves_its_last_line_full():
    """Before: after a 20-character run at 10 per line the column was reset to 0,
    so the next word was counted on the same line and the slice ran to 3 lines
    against a budget of 2."""
    slices = d.split_for_rows("B" * 20 + " a", 10, 2)
    assert "".join(slices) == "B" * 20 + " a"
    assert slices == ["B" * 20 + " ", "a"]
    assert all(_lines_needed(piece, 10) <= 2 for piece in slices)
    # 30 characters at 10 per line fill exactly 3 lines; the next word starts a 4th.
    assert d.split_for_rows("C" * 30 + " d", 10, 4) == ["C" * 30 + " d"]
    assert d.split_for_rows("C" * 30 + " d", 10, 3) == ["C" * 30 + " ", "d"]


def test_no_slice_needs_more_lines_than_the_budget_by_an_independent_count():
    import random

    rng = random.Random(2026111301)
    alphabet = "ab c\n  XYZ0123456789-_/"
    for _trial in range(3000):
        per_line, budget = rng.randint(4, 40), rng.randint(1, 30)
        words = []
        for _ in range(rng.randint(0, 120)):
            kind = rng.random()
            if kind < 0.15:
                words.append("W" * (per_line * rng.randint(1, 3)))     # exact multiples
            elif kind < 0.25:
                words.append("\n" * rng.randint(1, 40))                # newline runs
            else:
                words.append("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12))))
        text = " ".join(words)
        slices = d.split_for_rows(text, per_line, budget)
        assert "".join(slices) == text
        for piece in slices:
            assert _lines_needed(piece, per_line) <= budget, (per_line, budget, piece[:80])


def test_a_continuation_row_is_merged_only_into_the_record_it_names():
    from record_review_pdf_support import merge_continuations, note_number_displaced

    rows = [["1", "A", "a1"], ["", "A(continued,part2of3)", "a2"],
            ["", "A(continued,part3of3)", "a3"], ["2", "B", "b1"]]
    assert merge_continuations(rows, 1) == [["1", "A", "a1a2a3"], ["2", "B", "b1"]]
    # detail table: the record's own identity cell carries more than the id
    assert merge_continuations([["Aline2·B1/CLEAN·part1of2", "x"], ["A(continued,part2of2)", "y"]], 0) \
        == [["Aline2·B1/CLEAN·part1of2", "xy"]]

    with pytest.raises(AssertionError, match="names 'B'"):       # mislabelled: B's row under A
        merge_continuations([["1", "A", "a1"], ["", "B(continued,part2of2)", "b2"]], 1)
    with pytest.raises(AssertionError, match="no record before it"):
        merge_continuations([["", "A(continued,part2of2)", "a2"]], 1)
    with pytest.raises(AssertionError, match="expected part 2"):  # a part is missing
        merge_continuations([["1", "A", "a1"], ["", "A(continued,part3of3)", "a3"]], 1)
    with pytest.raises(AssertionError, match="expected part 3"):  # a part is repeated
        merge_continuations([["1", "A", "a1"], ["", "A(continued,part2of3)", "a2"],
                             ["", "A(continued,part2of3)", "a2"]], 1)

    # Only a placeholder cell's own note number may be read out of place.
    assert note_number_displaced("[2]Notestablished", "Notestablished[2]")
    assert note_number_displaced("Not[2]established", "Notestablished[2]")
    assert not note_number_displaced("[3]Notestablished", "Notestablished[2]")       # another note
    assert not note_number_displaced("Notestablished", "Notestablished[2]")          # number lost
    assert not note_number_displaced("[2][2]Notestablished", "Notestablished[2]")
    assert not note_number_displaced("establishedNot[2]", "Notestablished[2]")       # words reordered
    # Free text with brackets is never excused: reordered markers are a difference.
    assert not note_number_displaced("[WHY-2]b[WHY-1]a", "[WHY-1]a[WHY-2]b")
    assert not note_number_displaced("Notestablished[2]extra", "Notestablished[2]extra")
