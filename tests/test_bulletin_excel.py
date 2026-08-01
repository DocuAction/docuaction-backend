"""The client Excel workbook: shape, branding, and the two-sheet agreement.

The workbook is a deliverable — an FCC contact opens it in Excel — so these
assert the things that make it readable rather than merely well-formed: the
header identifies columns, the topic/count table adds up, and the file survives
a round trip through a real reader.
"""
import io

import pytest
from openpyxl import load_workbook

from app.bulletin_intelligence.excel_export import create_bulletin_excel

BRIEFING = {
    "briefing_id": "fcc_20260731_120000",
    "agency_id": "fcc",
    "briefing_date": "July 31, 2026",
}


class _Art:
    def __init__(self, source, title, summary, url, section):
        self.source_name = self.outlet = self.source = source
        self.title, self.summary, self.url = title, summary, url
        self.section, self.topic = section, "x"


def _articles():
    return [
        _Art("Radio World", "NAB meets FCC", "One.", "https://e.test/a", "Media & Broadcasting"),
        _Art("Reuters", "Spectrum auction", "Two.", "https://e.test/b", "Wireless & Spectrum"),
        _Art("WaPo", "Ownership rules", "Three.", "https://e.test/c", "Media & Broadcasting"),
        _Art("FCC", "Commission notice", "Four.", "https://e.test/d", "General"),
    ]


def _roundtrip(wb):
    """Save and reopen — proves a real reader can parse what we produced, which
    a purely in-memory assertion does not."""
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return load_workbook(buf), buf.getvalue()


def test_workbook_has_stories_and_summary_sheets():
    wb, data = _roundtrip(create_bulletin_excel(BRIEFING, _articles(), lambda a: a.section))
    assert len(wb.sheetnames) == 2
    assert wb.sheetnames[1] == "Summary"
    assert "July 31, 2026" in wb.sheetnames[0]
    assert data[:2] == b"PK", "not a valid xlsx container"


def test_column_headers_and_row_count():
    wb, _ = _roundtrip(create_bulletin_excel(BRIEFING, _articles(), lambda a: a.section))
    ws = wb[wb.sheetnames[0]]
    assert [ws.cell(1, c).value for c in range(1, 7)] == [
        "#", "Topic", "Source", "Headline", "Summary", "URL"]
    assert ws.max_row == 1 + len(_articles())


def test_rows_are_ordered_by_topic_then_source():
    wb, _ = _roundtrip(create_bulletin_excel(BRIEFING, _articles(), lambda a: a.section))
    ws = wb[wb.sheetnames[0]]
    got = [(ws.cell(r, 2).value, ws.cell(r, 3).value) for r in range(2, ws.max_row + 1)]
    assert got == sorted(got, key=lambda t: (t[0].lower(), t[1].lower()))


def test_reader_affordances_present():
    """Frozen header, auto-filter and clickable URLs are what make 180 rows
    usable; without them the sheet is technically correct and unreadable."""
    wb, _ = _roundtrip(create_bulletin_excel(BRIEFING, _articles(), lambda a: a.section))
    ws = wb[wb.sheetnames[0]]
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref.startswith("A1:F")
    assert ws.cell(2, 6).hyperlink is not None
    assert ws.cell(2, 4).alignment.wrap_text and ws.cell(2, 5).alignment.wrap_text


def test_summary_counts_match_the_rows_written():
    """The summary is derived from the rows actually emitted, not from the
    briefing's stored topic_counts — those come from a different pipeline and
    have disagreed with the rendered story count before."""
    wb, _ = _roundtrip(create_bulletin_excel(BRIEFING, _articles(), lambda a: a.section))
    s = wb["Summary"]
    counts = {}
    r = 6
    while s.cell(r, 1).value not in (None, "TOTAL"):
        counts[s.cell(r, 1).value] = s.cell(r, 2).value
        r += 1
    assert counts == {"Media & Broadcasting": 2, "General": 1, "Wireless & Spectrum": 1}
    assert list(counts.values()) == sorted(counts.values(), reverse=True)
    assert s.cell(r, 1).value == "TOTAL"
    assert str(s.cell(r, 2).value).startswith("=SUM(")


def test_control_characters_do_not_break_the_workbook():
    """Summaries are scraped from third-party feeds; one stray control byte must
    not cost the whole download."""
    arts = _articles()
    arts[0].summary = "Bad\x07byte\x0bhere"
    wb, _ = _roundtrip(create_bulletin_excel(BRIEFING, arts, lambda a: a.section))
    ws = wb[wb.sheetnames[0]]
    joined = "".join(str(ws.cell(r, 5).value or "") for r in range(2, ws.max_row + 1))
    assert "\x07" not in joined and "\x0b" not in joined


def test_empty_briefing_still_produces_a_valid_file():
    wb, data = _roundtrip(create_bulletin_excel(BRIEFING, [], lambda a: "General"))
    assert data[:2] == b"PK"
    ws = wb[wb.sheetnames[0]]
    assert ws.max_row == 1  # header only
