"""The record-review PDF as the PRODUCTION engine (WeasyPrint) actually paginates it,
and the dataset's text through every rendering (HTML, CSV, workbook, PDF).

TWO PARTS
    * "Rendered": needs WeasyPrint's native stack. It is absent on the Windows
      development host, where these tests skip. In CI they must RUN: the job sets
      RECORD_REVIEW_REQUIRE_REAL_RENDER=1, which turns the skip into a failure,
      so a green job cannot mean "never rendered".
    * "Pure": no engine, no database; runs everywhere.

Every assertion is on something observable - text read back from the rendered
pages by position, or parsed out of the HTML/CSV/workbook - never on the
presence of a CSS string. See tests/record_review_pdf_support.py for how a PDF
is compared with its dataset and the one normalisation that involves.

Set RECORD_REVIEW_PDF_OUT to also write each rendered PDF and a JSON line of
engine facts there (the workflow uploads them as an artifact).
"""
from __future__ import annotations

import copy
import csv
import hashlib
import html as html_mod
import io
import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

import record_review_pdf_support as s
from app.reports.data import october_review_dataset as d
from app.reports.engine.pdf_engine import pdf_available, unavailable_reason

REQUIRE = os.getenv("RECORD_REVIEW_REQUIRE_REAL_RENDER") == "1"
OUT = os.getenv("RECORD_REVIEW_PDF_OUT")
FIXTURES = {"base14": s.base14_facts, "adversarial": s.adversarial_facts}
MAIN_WIDTHS = [w for _g, cs in d.PDF_COLUMN_GROUPS for _c, w in cs]
MAIN_LABELS = [c for _g, cs in d.PDF_COLUMN_GROUPS for c, _w in cs]
DETAIL_WIDTHS = [w for _c, w in d.PDF_DETAIL_COLUMNS]
DETAIL_LABELS = [c for c, _w in d.PDF_DETAIL_COLUMNS]


# ── rendered by WeasyPrint ────────────────────────────────────────────────────

_RENDERED: dict = {}


def _render(name):
    if not pdf_available():
        if REQUIRE:
            pytest.fail("RECORD_REVIEW_REQUIRE_REAL_RENDER=1 but WeasyPrint cannot render here: "
                        + unavailable_reason())
        pytest.skip("WeasyPrint native libraries are not available here")
    if name not in _RENDERED:
        _RENDERED[name] = _render_once(name)
    return _RENDERED[name]


@pytest.fixture(params=sorted(FIXTURES))
def rendered(request):
    return _render(request.param)


@pytest.fixture
def adversarial():
    return _render("adversarial")


def _render_once(name):
    import weasyprint

    dataset = s.build(FIXTURES[name](), report_id=f"RECORD-REVIEW-SYNTHETIC-{name.upper()}")
    before = copy.deepcopy(dataset)
    started = time.perf_counter()
    pdf = d.to_pdf(dataset)                       # the function the export job calls
    seconds = time.perf_counter() - started
    assert dataset == before, "rendering changed the dataset it was given"
    pages = s.read_pdf(pdf)
    if OUT:
        Path(OUT).mkdir(parents=True, exist_ok=True)
        (Path(OUT) / f"weasyprint-{name}.pdf").write_bytes(pdf)
        (Path(OUT) / f"weasyprint-{name}.json").write_text(json.dumps({
            "fixture": name, "engine": "weasyprint", "weasyprint": weasyprint.__version__,
            "pages": len(pages), "seconds": round(seconds, 2), "bytes": len(pdf),
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "records": dataset["summary"]["unique_records"]}), encoding="utf-8")
    tables = s.table_words(pages)
    return {"name": name, "dataset": dataset, "pdf": pdf, "pages": pages,
            "main": s.reconstruct(pages, tables["main"], MAIN_WIDTHS, 1),
            "detail": s.reconstruct(pages, tables["detail"], DETAIL_WIDTHS, 0)}


_merged = s.merge_continuations


def test_every_page_carries_the_draft_marking_and_its_own_page_number(rendered):
    pages = rendered["pages"]
    assert len(pages) >= 2
    for number, page in enumerate(pages, start=1):
        _top, _body, bottom = s.split_furniture(page)
        footer = " ".join(w["text"] for w in sorted(bottom, key=lambda w: w["x0"]))
        assert s.FOOTER_TEXT in footer, f"page {number} has no draft marking: {footer!r}"
        assert f"Page {number} of {len(pages)}" in footer, f"page {number}: {footer!r}"


def test_no_page_is_blank_and_no_text_is_clipped_stacked_or_unreadably_small(rendered):
    for number, page in enumerate(rendered["pages"], start=1):
        _top, body, _bottom = s.split_furniture(page)
        assert body, f"page {number} carries only its header and footer (a blank overflow page)"
        off = [w["text"] for w in page["words"]
               if w["x0"] < 0 or w["x1"] > page["w"] or w["top"] < 0 or w["bottom"] > page["h"]]
        assert not off, f"page {number}: text outside the page box: {off[:5]}"
        in_margin = [w["text"] for w in body if w["x0"] < s.MARGIN_SIDE_PT - 1 or
                     w["x1"] > page["w"] - s.MARGIN_SIDE_PT + 1]
        assert not in_margin, f"page {number}: text runs into the side margin: {in_margin[:5]}"
        assert not s.stacked_words(page["words"]), (number, s.stacked_words(page["words"])[:3])
        small = [(w["text"], round(w["size"], 1)) for w in page["words"] if w["size"] < s.MIN_READABLE_PT]
        assert not small, f"page {number}: text below {s.MIN_READABLE_PT} pt: {small[:5]}"


def test_no_cell_text_leaves_its_own_column(rendered):
    for which in ("main", "detail"):
        assert not rendered[which]["outside"], (which, rendered[which]["outside"][:8])
        assert rendered[which]["orphan"] is None, f"{which}: table text before any record row"


def test_column_headings_repeat_above_the_rows_on_every_page_of_both_tables(rendered):
    for which, labels in (("main", MAIN_LABELS), ("detail", DETAIL_LABELS)):
        table = rendered[which]
        assert table["headers"], f"{which} table not found in the PDF"
        for page, cells in table["headers"]:
            for label, got in zip(labels, cells):
                # the main table's group-heading row sits above; its words share the band
                assert got.endswith(s.squash(label)), (
                    f"{which} table, page {page}: column heading {label!r} is not repeated (found {got!r})")
        assert not table["header_below_data"], (
            f"{which} table: data above the column headings on pages {table['header_below_data']}")


def test_classification_cells_are_set_like_the_other_data_cells(rendered):
    """The Flag text ("PROVISIONAL B2") is data, not a heading: same weight and no
    larger than the cells beside it."""
    flagged = [w for page in rendered["pages"] for w in page["words"] if w["text"] == "PROVISIONAL"]
    assert flagged, "fixture has provisional B2 records; their flag must be in the PDF"
    for w in flagged:
        assert w["size"] <= 8.05 and "bold" not in w["fontname"].lower(), (w["size"], w["fontname"])


def test_main_table_shows_every_record_and_field_of_the_dataset(rendered):
    got = _merged(rendered["main"]["rows"], 1)
    want = s.expected_main(rendered["dataset"])
    assert [r[1] for r in got] == [r[1] for r in want], "records missing, repeated or out of order"
    for g, w in zip(got, want):
        for label, gc, wc in zip(MAIN_LABELS, g, w):
            if "[" in wc and gc != wc:          # a superscript note number may read before its line
                assert sorted(gc) == sorted(wc), (w[1], label, gc, wc)
                continue
            assert gc == wc, f"{w[1]} / {label}: {s.first_difference(gc, wc)}"


def test_detail_table_keeps_each_address_and_comment_with_its_record_and_column(rendered):
    got = _merged(rendered["detail"]["rows"], 0)
    want = s.expected_detail(rendered["dataset"])
    assert len(got) == len(want), f"detail rows: got {len(got)}, want {len(want)}"
    for g, w in zip(got, want):
        assert g[0].startswith(w[0]), (g[0][:40], w[0])
        for label, gc, wc in zip(DETAIL_LABELS[1:], g[1:], w[1:]):
            assert gc == wc, f"{w[0]} / {label}: {s.first_difference(gc, wc)}"


def test_every_marker_of_the_long_comments_is_in_its_own_column_once_and_in_order(adversarial):
    rows = {r[0][:len(s.ID_LONG)]: r for r in _merged(adversarial["detail"]["rows"], 0)}
    row = rows[s.ID_LONG]
    for label, markers in ((d.ORIGINAL_COMMENTS_COLUMN, s.LONG_ORIGINAL_MARKERS),
                           (d.IZAAN_COMMENTS_COLUMN, s.LONG_IZAAN_MARKERS),
                           ("Report Clarifications", s.LONG_CLARIFICATION_MARKERS)):
        cell = row[DETAIL_LABELS.index(label)]
        position = -1
        for marker in markers:
            assert cell.count(marker) == 1, f"{label}: marker {marker} appears {cell.count(marker)} times"
            assert cell.index(marker) > position, f"{label}: marker {marker} is out of order"
            position = cell.index(marker)
        for other_label, other in zip(DETAIL_LABELS, row):
            if other_label != label:
                assert markers[0][:-5] not in other, f"{label} text found in the {other_label} column"
    assert len(s.LONG_ORIGINAL) >= 20_000 and len(s.LONG_ORIGINAL_MARKERS) > 100


def test_short_identifiers_and_status_words_are_not_broken_across_lines(rendered):
    """A 10-digit NPI, PROVISIONAL, NOT CLASSIFIED, "established" and "unavailable"
    each fit their column: read back, each is ONE word, once per row that has it.
    (Text longer than its column - an OID, a UUID - still wraps; that is not this.)"""
    rows = rendered["dataset"]["sheets"]["Records"]["rows"]
    col = {c: i for i, c in enumerate(d.RECORD_COLUMNS)}
    tables = s.table_words(rendered["pages"])
    words = [w["text"] for page in tables["main"] for w in page]

    def count(predicate):
        return sum(1 for w in words if predicate(w))

    npi = sum(1 for r in rows if r[col["Submitted NPI (original, verbatim)"]] == "1234567893")
    assert count(lambda w: w == "1234567893") == npi, "a 10-digit NPI is broken across lines"
    flagged = sum(1 for r in rows if "PROVISIONAL" in r[col["Flag"]])
    assert count(lambda w: w == "PROVISIONAL") == flagged, "PROVISIONAL is broken across lines"
    unclassified = sum(1 for r in rows if r[col["Bucket"]] == d.NOT_CLASSIFIED)
    assert count(lambda w: w == "CLASSIFIED") == unclassified, "NOT CLASSIFIED is broken inside the word"
    usps = sum(1 for r in rows if r[col["USPS Address Check"]] == d.USPS_UNAVAILABLE)
    assert count(lambda w: w.endswith("unavailable")) == usps, "unavailable is broken across lines"
    folded = sum(1 for r in rows for c in ("NPPES", "PECOS/CMS Enrollment", "OIG LEIE", "SAM.gov Exclusions")
                 if r[col[c]].startswith(d.PDF_NOTE_PREFIX))
    assert count(lambda w: w == "established") == folded, "established is broken across lines"


def test_a_row_longer_than_a_page_names_its_record_on_every_page_it_reaches(rendered):
    """Wherever a table's rows are on a page, the identity column on THAT page names
    the record each belongs to. A page of comment text with no AGT_Record_ID on it
    cannot be attributed once printed."""
    for which in ("main", "detail"):
        pages = [p for p, _owner in rendered[which]["continued"]]
        assert not pages, f"{which} table: text continues onto pages {pages} with no record identifier"


def test_detail_section_lists_exactly_the_records_the_inclusion_rule_selects(rendered):
    dataset = rendered["dataset"]
    want = s.detail_ids(dataset)
    got = [r[0] for r in _merged(rendered["detail"]["rows"], 0)]
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert g.startswith(s.squash(w))
    text = s.squash(" ".join(w["text"] for p in rendered["pages"] for w in p["words"]))
    total = dataset["summary"]["unique_records"]
    assert f"Recorddetail({len(want)}of{total}records)" in text
    if rendered["name"] == "adversarial":
        assert s.ID_NO_DETAIL not in want and set(s.ADDRESS_ONLY_IDS) <= set(want)


def test_summary_counts_in_the_pdf_are_the_datasets_own(rendered):
    summary = rendered["dataset"]["summary"]
    first = s.squash(" ".join(w["text"] for line in s._lines(rendered["pages"][0]["words"]) for w in line))
    for bucket in d.BUCKETS:
        assert f"{bucket}{summary['buckets'][bucket]}" in first
    assert f"Total(uniquerecords){summary['unique_records']}" in first
    assert f"ofwhichHELD(countedonce,insideB4){summary['held_inside_b4']}" in first
    assert sum(summary["buckets"].values()) + summary["unclassified_non_held_count"] == summary["unique_records"]


# ── pure: the dataset through HTML, CSV and the workbook, exactly ─────────────

class _Tables(HTMLParser):
    """Every <table> as rows of (row class, [cell text]); <br> reads as a newline."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._row, self._cell, self._cls = [], None, None, ""

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row, self._cls = [], dict(attrs).get("class") or ""
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append((self._cls, self._row))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _html_tables(page):
    parser = _Tables()
    parser.feed(page)
    return parser.tables


def _html_detail_records(page):
    """Detail table as {id: [text per detail column after the id]}, continuation
    rows concatenated onto their record exactly (no separator added)."""
    table = next(t for t in _html_tables(page) if t and t[0][1][:1] == ["AGT_Record_ID"])
    out, order = {}, []
    for cls, cells in table[1:]:
        if "continuation" in cls.split():
            for i, text in enumerate(cells[1:]):
                out[order[-1]][i] += text
            continue
        rid = cells[0].split("\n")[0]
        order.append(rid)
        out[rid] = list(cells[1:])
    return out, order


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_html_carries_every_detail_field_exactly_including_whitespace(name):
    dataset = s.build(FIXTURES[name]())
    before = copy.deepcopy(dataset)
    page = d.to_html(dataset)
    assert dataset == before
    rec = dataset["sheets"]["Records"]
    col = {c: i for i, c in enumerate(rec["columns"])}
    got, order = _html_detail_records(page)
    assert order == s.detail_ids(dataset)
    for row in rec["rows"]:
        rid = row[col["AGT_Record_ID"]]
        if rid not in got:
            continue
        for label, text in zip(DETAIL_LABELS[1:], got[rid]):
            assert text == row[col[label]], f"{rid} / {label}: {s.first_difference(text, row[col[label]])}"


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_html_main_table_carries_every_shown_field_exactly(name):
    dataset = s.build(FIXTURES[name]())
    rec = dataset["sheets"]["Records"]
    col = {c: i for i, c in enumerate(rec["columns"])}
    table = next(t for t in _html_tables(d.to_html(dataset))
                 if len(t) > 1 and t[1][1][:2] == ["Source_Line", "AGT_Record_ID"])
    rows = []
    for cls, cells in table[2:]:
        if "continuation" in cls.split():
            for i, text in enumerate(cells):
                if i != 1:
                    rows[-1][i] += text
        else:
            rows.append(list(cells))
    assert len(rows) == len(rec["rows"])
    for got, row in zip(rows, rec["rows"]):
        for i, label in enumerate(MAIN_LABELS):
            want = row[col[label]]
            if want.startswith(d.PDF_NOTE_PREFIX):
                assert re.fullmatch(r"Not established \[\d+\]", got[i]), (label, got[i])
            else:
                assert got[i] == want, f"{row[col['AGT_Record_ID']]} / {label}: {s.first_difference(got[i], want)}"


def test_html_like_text_is_shown_as_text_and_never_becomes_markup():
    page = d.to_html(s.build(s.adversarial_facts()))
    assert "<script>" not in page and "<b>not bold</b>" not in page and "<!-- comment -->" not in page
    got, _order = _html_detail_records(page)
    assert got[s.ID_WHITESPACE][DETAIL_LABELS[1:].index(d.IZAAN_COMMENTS_COLUMN)] == s.HTML_LIKE
    assert got[s.ID_WHITESPACE][DETAIL_LABELS[1:].index(d.ORIGINAL_COMMENTS_COLUMN)] == s.WHITESPACE_UNICODE


def test_csv_and_workbook_carry_every_field_of_every_record_exactly():
    import openpyxl

    dataset = s.build(s.adversarial_facts())
    before = copy.deepcopy(dataset)
    rows = dataset["sheets"]["Records"]["rows"]
    csv_rows = list(csv.reader(io.StringIO(d.to_csv(dataset).decode("utf-8-sig"), newline="")))
    assert csv_rows[1] == d.RECORD_COLUMNS
    assert csv_rows[2:] == rows                      # no fixture cell starts with a formula leader
    ws = openpyxl.load_workbook(io.BytesIO(d.to_xlsx(dataset)))["Records"]
    header = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1) if r and r[0] == "Source_Line")
    xl_rows = [["" if v is None else str(v) for v in r] for r in ws.iter_rows(min_row=header + 1, values_only=True)]
    assert xl_rows == rows
    assert dataset == before


def test_detail_section_says_which_records_it_lists_and_the_rule_matches_what_is_listed():
    """A record with ONLY a submitted address is listed, so the explanation has to
    say so; a record with none of the five fields is not listed."""
    dataset = s.build([s._fact(1), s._fact(2, submitted_address=""),
                       s._fact(3, submitted_address="", qa_comments="c", qa_comments_source=s.SRC)])
    page = d.to_html(dataset)
    _got, order = _html_detail_records(page)
    assert order == [f"{s.ID_PREFIX}0001", f"{s.ID_PREFIX}0003"] == s.detail_ids(dataset)
    note = html_mod.unescape(re.sub(r"<[^>]+>", " ", page.split("Addresses and commentary (verbatim)")[1]))
    note = note.split("Record detail (")[0]
    assert "submitted address" in note.lower()
    assert "Only records with an address correction or any comment" not in note
    empty = d.to_html(s.build([s._fact(2, submitted_address="")]))
    assert "carries a submitted address" in empty.split("Addresses and commentary (verbatim)")[1]


def test_long_text_is_cut_into_exact_consecutive_slices_and_nothing_else():
    import random

    rng = random.Random(20261113)
    alphabet = "ab c\n\t  XYZ0123456789-_/&<>\u00e9"
    for trial in range(1500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 700)))
        if trial % 7 == 0:
            text += "Q" * rng.randint(1, 2500)                   # one run longer than a whole slice
        per_line, lines = rng.randint(4, 40), rng.randint(1, 30)
        slices = d.split_for_rows(text, per_line, lines)
        assert "".join(slices) == text
        assert all(slices) or text == ""
    assert d.split_for_rows("", 30, 24) == [""]
    assert d.split_for_rows("short text", 30, 24) == ["short text"]
    whole = d.split_for_rows(s.LONG_ORIGINAL, 31, 24)
    assert len(whole) > 20 and "".join(whole) == s.LONG_ORIGINAL
    assert all(not piece[0].isspace() for piece in whole)        # a slice starts on a word, never mid-gap


def test_a_long_record_continues_in_rows_that_each_name_the_record_and_part():
    page = d.to_html(s.build(s.adversarial_facts()))
    detail = next(t for t in _html_tables(page) if t and t[0][1][:1] == ["AGT_Record_ID"])
    mine = [(cls, cells) for cls, cells in detail[1:] if cells[0].startswith(s.ID_LONG)]
    parts = len(mine)
    assert parts > 20 and mine[0][0] == "" and f"part 1 of {parts}" in mine[0][1][0]
    for k, (cls, cells) in enumerate(mine[1:], start=2):
        assert cls == "continuation"
        assert cells[0] == f"{s.ID_LONG}\n(continued, part {k} of {parts})"
    original = DETAIL_LABELS.index(d.ORIGINAL_COMMENTS_COLUMN)
    assert "".join(cells[original] for _cls, cells in mine) == s.LONG_ORIGINAL
    # an ordinary record is one row and says nothing about parts
    short = [(cls, cells) for cls, cells in detail[1:] if cells[0].startswith(f"{s.ID_PREFIX}0001")]
    assert len(short) == 1 and "part" not in short[0][1][0]
    main = next(t for t in _html_tables(page) if len(t) > 1 and t[1][1][:2] == ["Source_Line", "AGT_Record_ID"])
    reasons = [(cls, cells) for cls, cells in main[2:] if cells[1].startswith(s.ID_LONG_REASONS)]
    assert len(reasons) > 1 and all(cls == "continuation" for cls, _c in reasons[1:])
    assert "".join(cells[-1] for _cls, cells in reasons) == s.LONG_REASONS


def test_held_is_counted_once_inside_b4_and_findings_stay_a_separate_grain():
    dataset = s.build(s.adversarial_facts())
    summary = dataset["summary"]
    rows = dataset["sheets"]["Records"]["rows"]
    b, st = d.RECORD_COLUMNS.index("Bucket"), d.RECORD_COLUMNS.index("Status")
    held = [r for r in rows if r[st] == "HELD"]
    assert held and all(r[b] == "B4" for r in held)
    assert summary["held_inside_b4"] == len(held)
    assert summary["buckets"]["B4"] == sum(1 for r in rows if r[b] == "B4")      # each record once
    assert sum(summary["buckets"].values()) + summary["unclassified_non_held_count"] == len(rows) == 64
    assert summary["field_findings"] is None and summary["findings_counted_in_buckets"] is False
    assert summary["complete"] is False and summary["unclassified_non_held_count"] == 4


def test_the_checker_reads_a_missing_word_a_moved_comment_and_whitespace_as_what_they_are():
    """Control: the one normalisation hides whitespace only."""
    assert s.squash("a  b\n\tc") == "abc" == s.squash("abc")
    assert s.squash("alpha beta gamma") != s.squash("alpha gamma")
    assert s.squash("café") == s.squash("café") != s.squash("cafe")
    text, markers = s.marked_text("X", 500)
    assert len(set(markers)) == len(markers) and all(text.count(m) == 1 for m in markers)
    assert text.startswith(markers[0]) and text.endswith(markers[-1])
