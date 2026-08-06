"""Reviewed-workbook upload (Task 3.1).

The file arriving here is the output of an hour of human editing. Every test
below exists because of a way that hour could be silently lost.
"""
import io

import pytest

from app.bulletin_intelligence.reviewed_upload import (
    EXPECTED_HEADERS, UploadError, diff_against_articles,
    parse_reviewed_workbook)

openpyxl = pytest.importorskip("openpyxl")


def _wb_bytes(header=None, rows=(), preamble=0, sheet="FCC Bulletin"):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for _ in range(preamble):
        ws.append(["FCC Daily News Summary"])
    ws.append(list(header if header is not None else EXPECTED_HEADERS))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _row(n=1, url="https://ex.test/a", title="Title", summary=None,
         category="Broadband"):
    if summary is None:
        summary = " ".join(["word"] * 70)          # inside the 60-100 target
    return [n, category, "August 5, 2026", "Direct", title, summary,
            "Reuters", "No", "High", url, "perigon"]


class Art:
    def __init__(self, url, title="Old title", summary="Old summary"):
        self.url = url
        self.title = title
        self.summary = summary


# ── Structure ─────────────────────────────────────────────────────────────────

def test_parses_a_well_formed_workbook():
    out = parse_reviewed_workbook(_wb_bytes(rows=[_row(1), _row(2, "https://ex.test/b")]))
    assert out["count"] == 2
    assert out["rows"][0]["url"] == "https://ex.test/a"
    assert out["rows"][0]["category"] == "Broadband"


def test_header_is_found_below_a_title_block():
    """Exports carry a title block, and reviewers add notes above the table."""
    out = parse_reviewed_workbook(_wb_bytes(rows=[_row()], preamble=3))
    assert out["header_row"] == 4
    assert out["count"] == 1


def test_wrong_columns_are_rejected():
    bad = ["#", "Category", "Date", "Title", "Summary"]
    with pytest.raises(UploadError, match="A-K"):
        parse_reviewed_workbook(_wb_bytes(header=bad, rows=[_row()]))


def test_reordered_columns_are_rejected_not_guessed():
    """Same names in a different order silently remaps every value."""
    swapped = list(EXPECTED_HEADERS)
    swapped[4], swapped[5] = swapped[5], swapped[4]     # Title <-> Summary
    with pytest.raises(UploadError, match="out of order"):
        parse_reviewed_workbook(_wb_bytes(header=swapped, rows=[_row()]))


def test_a_non_xlsx_file_gets_a_clear_message():
    with pytest.raises(UploadError, match="Not an .xlsx"):
        parse_reviewed_workbook(b"url,title\nhttps://x,Y\n")
    with pytest.raises(UploadError, match="Empty"):
        parse_reviewed_workbook(b"")


def test_headers_but_no_data_rows_is_an_error():
    with pytest.raises(UploadError, match="no usable data rows"):
        parse_reviewed_workbook(_wb_bytes(rows=[]))


def test_parser_columns_match_the_exporter():
    """One contract read from both ends — drift would land edits in wrong fields."""
    from app.bulletin_intelligence.excel_export import HEADERS
    assert list(HEADERS) == EXPECTED_HEADERS


# ── Row-level handling ────────────────────────────────────────────────────────

def test_a_row_without_a_url_is_reported_not_silently_dropped():
    data = _wb_bytes(rows=[_row(), _row(2, url="")])
    out = parse_reviewed_workbook(data)
    assert out["count"] == 1
    assert any("no URL" in w for w in out["warnings"])


def test_blank_spacer_rows_are_skipped_without_complaint():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(EXPECTED_HEADERS)
    ws.append(_row(1))
    ws.append([])
    ws.append(_row(2, "https://ex.test/b"))
    buf = io.BytesIO(); wb.save(buf)
    out = parse_reviewed_workbook(buf.getvalue())
    assert out["count"] == 2
    assert not [w for w in out["warnings"] if "blank" in w.lower()]


def test_duplicate_urls_collapse_with_the_later_row_winning():
    out = parse_reviewed_workbook(_wb_bytes(rows=[
        _row(1, title="First"), _row(2, title="Second")]))
    assert out["count"] == 1
    assert out["duplicate_rows"] == 1
    assert out["rows"][0]["title"] == "Second"
    assert any("duplicate" in w for w in out["warnings"])


def test_off_target_summary_warns_but_does_not_reject():
    """60-100 words is the house target, not a rule a reviewer cannot override."""
    out = parse_reviewed_workbook(_wb_bytes(rows=[_row(summary="Too short.")]))
    assert out["count"] == 1
    assert any("target 60-100" in w for w in out["warnings"])


# ── Diff ──────────────────────────────────────────────────────────────────────

def test_diff_reports_edits_additions_and_removals_separately():
    parsed = parse_reviewed_workbook(_wb_bytes(rows=[
        _row(1, "https://ex.test/a", title="Edited title"),
        _row(2, "https://ex.test/new"),
    ]))
    diff = diff_against_articles(parsed["rows"], [
        Art("https://ex.test/a"), Art("https://ex.test/gone")])

    assert diff["edited_count"] == 1
    assert diff["edited"][0]["changes"]["title"]["to"] == "Edited title"
    assert diff["added"] == ["https://ex.test/new"]
    assert diff["removed"] == ["https://ex.test/gone"]


def test_diff_changes_nothing():
    """A dry run that mutated the briefing would be worse than no dry run."""
    art = Art("https://ex.test/a", title="Original")
    parsed = parse_reviewed_workbook(_wb_bytes(rows=[_row(title="Changed")]))
    diff_against_articles(parsed["rows"], [art])
    assert art.title == "Original"


def test_an_empty_cell_is_not_treated_as_an_edit_to_empty():
    """A reviewer who clears a cell is far likelier to have not filled it in
    than to want the title deleted."""
    parsed = parse_reviewed_workbook(_wb_bytes(rows=[_row(title="", summary="")]))
    diff = diff_against_articles(parsed["rows"], [Art("https://ex.test/a")])
    assert diff["edited_count"] == 0

    # ...but a cell the reviewer actually filled in IS an edit, so the guard
    # above cannot be "ignore this row".
    parsed = parse_reviewed_workbook(_wb_bytes(rows=[_row(title="", summary="New text")]))
    diff = diff_against_articles(parsed["rows"], [Art("https://ex.test/a")])
    assert diff["edited_count"] == 1
    assert "title" not in diff["edited"][0]["changes"]
    assert "summary" in diff["edited"][0]["changes"]
