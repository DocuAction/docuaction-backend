"""The record-review PDF carries the analyst/QA disposition and the SAM public-extract result - not only the
Excel/CSV (owner instruction 2026-11-15). Synthetic facts only.

Pure part (runs everywhere): the HTML the PDF is made from. Rendered part: WeasyPrint, which must RUN in CI
(RECORD_REVIEW_REQUIRE_REAL_RENDER=1 turns the usual skip into a failure) - text is read back from the pages.
"""
from __future__ import annotations

import os

import pytest

import record_review_pdf_support as s
from app.reports.data import october_review_dataset as d
from app.reports.engine.pdf_engine import pdf_available, unavailable_reason

REQUIRE = os.getenv("RECORD_REVIEW_REQUIRE_REAL_RENDER") == "1"

APPROVED_NOTE = "analyst determination #1 (CONFIRM B2) at 2026-11-08T11:00:00; QA APPROVE at 2026-11-08T12:00:00"
EXTRACT_CANDIDATE = ("CANDIDATE MATCH (2) - NOT a confirmed exclusion; analyst determination required | edition "
                     "2026-11-05 (SAM_Exclusions_Public_Extract_V2_26309.CSV) | sha256 abababababab | checked "
                     "2026-11-06 | supplementary to, not a substitute for, the live SAM.gov API check")
EXTRACT_UNAVAILABLE = ("Extract screen unavailable - not performed: edition stale | checked 2026-11-06 | "
                       "supplementary to, not a substitute for, the live SAM.gov API check")


def _facts():
    f = s.base14_facts()
    f[1].update(reviewed_disposition="Confirmed B2", reviewed_date="2026-11-08T12:00:00", reviewed_basis=APPROVED_NOTE,
                reviewed_qa_status="Approved by QA", sam_extract=EXTRACT_CANDIDATE)
    f[2].update(reviewed_qa_status="Returned by QA; not approved", reviewed_basis="analyst determination #1 (RECLASSIFY to B3)",
                sam_extract=EXTRACT_UNAVAILABLE)
    f[3].update(sam_extract="No candidate match in the extract | edition 2026-11-05 | checked 2026-11-06")     # clean: not listed
    f[6].update(reviewed_qa_status="Analyst determination recorded; awaiting QA")                            # the HELD one
    return f


def _dataset():
    return s.build(_facts(), report_id="RECORD-REVIEW-SYNTHETIC-REVIEWED")


def test_the_html_lists_records_with_analyst_activity_or_a_notable_extract_result_and_only_those():
    html = d.to_html(_dataset())
    assert d.PDF_REVIEW_HEADING in html
    assert "Reviewed dispositions and extract results (3 of 14 records)" in html
    for needle in ("Confirmed B2", "Approved by QA", "Returned by QA; not approved", "awaiting QA",
                   "CANDIDATE MATCH (2)", "NOT a confirmed exclusion", "SAM_Exclusions_Public_Extract_V2_26309.CSV",
                   "Extract screen unavailable"):
        assert needle in html, needle
    assert "No candidate match in the extract" not in html.split(d.PDF_REVIEW_HEADING, 1)[1], "a clean result is not listed"
    # The automated Bucket and Status stay in the main table; the HELD record is not relabelled.
    held = [r for r in _dataset()["sheets"]["Records"]["rows"] if r[_dataset()["sheets"]["Records"]["columns"].index("Status")] == "HELD"]
    assert held and all("Confirmed" not in x for r in held for x in r)


def test_an_empty_review_table_says_so_instead_of_vanishing():
    html = d.to_html(s.build(s.base14_facts(), report_id="RECORD-REVIEW-SYNTHETIC-NONE"))
    assert d.PDF_REVIEW_HEADING in html and "No record in this dataset has analyst/QA activity" in html


def _render():
    if not pdf_available():
        if REQUIRE:
            pytest.fail("RECORD_REVIEW_REQUIRE_REAL_RENDER=1 but WeasyPrint cannot render here: " + unavailable_reason())
        pytest.skip("WeasyPrint native libraries are not available here")
    pdf = d.to_pdf(_dataset())
    return pdf, s.read_pdf(pdf)


def test_rendered_pdf_shows_the_reviewed_disposition_and_the_extract_evidence():
    """Read back COLUMN BY COLUMN, per record: a phrase that wraps inside its cell is rebuilt in reading order
    (a flat run of the page's words interleaves neighbouring columns and cannot be compared)."""
    pdf, pages = _render()
    assert pdf.startswith(b"%PDF-")
    widths = [w for _c, w in d.PDF_REVIEW_COLUMNS]
    table = s.reconstruct(pages, s.table_words(pages)["review"], widths, 0)
    assert not table["outside"], table["outside"][:6]
    rows = {s.squash(cols[0])[:14]: cols for cols in table["rows"]}
    facts = {f["agt_record_id"]: f for f in _facts()}
    expected = {rid: f for rid, f in facts.items() if f.get("reviewed_qa_status") or f.get("sam_extract")}
    listed = {rid: f for rid, f in expected.items()
              if f.get("reviewed_qa_status", "No analyst determination recorded") != "No analyst determination recorded"
              or (f.get("sam_extract") and not f["sam_extract"].startswith("No candidate"))}
    assert len(table["rows"]) == len(listed) == 3, (len(table["rows"]), sorted(listed))
    for rid, f in listed.items():
        cols = next(c for c in table["rows"] if s.squash(c[0]).startswith(s.squash(rid)))
        want = [f.get("reviewed_qa_status", "No analyst determination recorded"), f.get("reviewed_disposition", ""),
                f.get("reviewed_date", ""), f.get("reviewed_basis", ""), f.get("sam_extract") or ""]
        for got, expect in zip(cols[1:], want):
            if expect:
                assert s.squash(expect) in s.squash(got), (rid, expect[:50], got[:80])
    assert any("NOTaconfirmedexclusion" in s.squash(c[5]) for c in table["rows"])
    assert any("CANDIDATEMATCH(2)" in s.squash(c[5]) and "2026-11-05" in s.squash(c[5]) for c in table["rows"])


def test_rendered_review_table_has_no_clipped_stacked_or_unreadably_small_text_and_repeats_its_headings():
    _pdf, pages = _render()
    for number, page in enumerate(pages, start=1):
        off = [w["text"] for w in page["words"]
               if w["x0"] < 0 or w["x1"] > page["w"] or w["top"] < 0 or w["bottom"] > page["h"]]
        assert not off, f"page {number}: text outside the page box: {off[:5]}"
        assert not s.stacked_words(page["words"]), (number, s.stacked_words(page["words"])[:3])
        small = [(w["text"], round(w["size"], 1)) for w in page["words"] if w["size"] < s.MIN_READABLE_PT]
        assert not small, f"page {number}: text below {s.MIN_READABLE_PT} pt: {small[:5]}"
    heading_page = next(i for i, p in enumerate(pages) if "Revieweddispositionsand" in s.squash(" ".join(w["text"] for w in p["words"])))
    assert "Analyst/QAStatus" in s.squash(" ".join(w["text"] for w in pages[heading_page]["words"]))
