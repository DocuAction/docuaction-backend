"""Layout acceptance for the delivery processing PDF, rendered by the PRODUCTION engine (WeasyPrint).

Found on DEV 2026-10-06 (reports DA-ARC-2026-060 / 061), all presentation defects:
  R-01 the severity word INFORMATIONAL overran its column ("INFORMATIONALpartOf")
  R-02 page 2 was almost blank: a line or a whole box spilled off the one-page cover
  R-03 one or two rows of a table stranded alone on a page
  R-04 headings ("Appendix E", "Report Provenance") stranded at the foot of a page, away from their table
  R-05 raw Python `True` printed in the integrity-check result column

The two fixtures are the stored datasets of those DEV reports (synthetic development data); the variants stress
the same template with more findings rules, more next actions, a longer timeline and a failed reconciliation.

Skipped where WeasyPrint's native libraries are absent (the Windows development host); it runs on the Linux PDF
job. Set LAYOUT_OUT_DIR to also write each rendered PDF there (the workflow uploads them as an artifact).
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

import pytest

from app.reports.branding import current_branding
from app.reports.engine.pdf_engine import pdf_available, render_pdf
from app.reports.engine.template_engine import render_html
from app.reports.generator import _marking_context

needs_weasyprint = pytest.mark.skipif(not pdf_available(), reason="WeasyPrint native libraries are not available here")

DATA = Path(__file__).parent / "data" / "delivery_report"
OUT = os.getenv("LAYOUT_OUT_DIR")


def _fixture(name: str):
    return json.loads((DATA / f"ds_{name}.json").read_text(encoding="utf-8"))


def _many_rules(d):
    f = d["dataset"]["findings"]
    base = f["by_code"][0]
    f["by_code"] = [{**base, "rule_id": f"SYN-{i:03d}", "issue_type": f"SYNTHETIC_ISSUE_TYPE_NUMBER_{i}",
                     "severity": ("HIGH" if i % 3 == 0 else "INFORMATIONAL"), "count": i + 1} for i in range(14)]
    f["total"] = sum(r["count"] for r in f["by_code"])
    rows = []
    for i in range(45):
        r = dict(f["rows"][i % len(f["rows"])])
        r["issue_code"] = f"DQ-SYN-{i:06d}"
        r["line_number"] = i + 2
        r["severity"] = "INFORMATIONAL" if i % 2 else "HIGH"
        rows.append(r)
    f["rows"], f["rows_shown"], f["rows_total"] = rows, len(rows), len(rows)
    return d


def _busy_cover(d):
    ds = d["dataset"]
    ds["analyst"]["counts"].update({"open": 3, "claimed": 2, "qa_pending": 1})
    ds["findings"]["open_high"] = 4
    ds["reconciliation"]["passed"] = False
    ds["reconciliation"]["failure_reason"] = "synthetic: received 3 does not equal accounted 2"
    ds["readiness"]["available"] = False
    return d


def _long_timeline(d):
    ev = d["dataset"]["timeline"]["events"]
    d["dataset"]["timeline"]["events"] = [dict(ev[i % len(ev)], id=f"syn-{i}", attempt=1 + i // len(ev))
                                          for i in range(30)]
    return d


SCENARIOS = {
    "clean_060": lambda: _fixture("060"),
    "exceptions_061": lambda: _fixture("061"),
    "many_rules": lambda: _many_rules(_fixture("061")),
    "busy_cover": lambda: _busy_cover(_fixture("061")),
    "worst_case": lambda: _long_timeline(_busy_cover(_many_rules(_fixture("061")))),
}


def _render(name: str):
    d = copy.deepcopy(SCENARIOS[name]())
    ds, snap = d["dataset"], d["snapshot"]
    ctx = {k: v for k, v in ds.items() if k not in ("chart_list", "service_version", "review_cycle_id")}
    brand = current_branding()
    ctx.update(chart_images={}, branding={**brand.to_dict(), "agt_logo": brand.agt_logo,
                                          "government_logo": brand.government_logo},
               pdf_author=brand.prepared_by, pdf_keywords="layout-test",
               document_status="Draft — awaiting PM review", reviewed_by=None, progress=None, annex=None)
    html = render_html("delivery_processing.html", {**ctx, "snapshot": snap, **_marking_context(snap)})
    pdf = render_pdf(html, title=f"layout {name}")
    if OUT:
        Path(OUT).mkdir(parents=True, exist_ok=True)
        (Path(OUT) / f"{name}.pdf").write_bytes(pdf)
    return pdf


@pytest.fixture(scope="module")
def pdfs():
    if not pdf_available():
        pytest.skip("WeasyPrint native libraries are not available here")
    import io

    import pdfplumber

    out = {}
    for name in SCENARIOS:
        pdf = _render(name)
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            pages = []
            for page in doc.pages:
                words = page.extract_words(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"])
                pages.append({"w": float(page.width), "h": float(page.height), "words": words,
                              "text": page.extract_text() or ""})
        out[name] = pages
    return out


ALL = list(SCENARIOS)
HEADING = re.compile(r"^(Appendix [A-J]\.|Report Provenance$|Accessibility$)")


@needs_weasyprint
@pytest.mark.parametrize("name", ALL)
def test_the_cover_is_exactly_one_page_and_no_page_is_nearly_blank(pdfs, name):
    pages = pdfs[name]
    assert "REQUIRED NEXT ACTIONS" in pages[0]["text"].upper(), "the next-actions box must sit on page one"
    assert "Program Manager release decision" in pages[0]["text"], "last next action must be on page one"
    # page two is the first appendix page: landscape, not a spill-over of the portrait cover
    assert pages[1]["w"] > pages[1]["h"], f"{name}: page 2 is portrait, so the cover spilled over: {pages[1]['text'][:120]!r}"
    for i, p in enumerate(pages[:-1], start=1):
        assert len(p["text"]) >= 300, f"{name}: page {i} is nearly blank ({len(p['text'])} chars): {p['text'][:80]!r}"


GLUED_SEVERITY = re.compile(r"\b(INFORMATIONAL|CRITICAL|MEDIUM|HIGH|LOW)(?=[A-Za-z_])")


def stacked_words(words, tol=0.6, min_area=2.0):
    """Pairs of words whose boxes intersect by a meaningful area. Catches text drawn on top of other text (for
    example a visually hidden element whose glyphs stay in the text layer). Boxes are shrunk by `tol` so words
    that merely touch are not reported."""
    ws = sorted(words, key=lambda w: w["x0"])
    out = []
    for i, a in enumerate(ws):
        for b in ws[i + 1:]:
            if b["x0"] >= a["x1"] - tol:
                break
            ix = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]) - tol
            iy = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]) - tol
            if ix > 0 and iy > 0 and ix * iy >= min_area:
                out.append((a["text"], b["text"]))
    return out


def glued_severity(text):
    """A severity word immediately followed by letters: a cell overrunning into its neighbour. The extractor
    reads an overrun as ONE token (the glyphs abut), which is why a box-intersection test cannot see it."""
    return [m.group(0) + text[m.end():m.end() + 12].split()[0] for m in GLUED_SEVERITY.finditer(text)]


class TestTheDetectorsThemselves:
    """Control tests: they run without WeasyPrint so the detectors are proven on every platform."""

    def test_glued_severity_catches_the_original_defect_and_ignores_clean_cells(self):
        assert glued_severity("INFORMATIONALpartOf") and glued_severity("INFORMATIONALaddress_text x")
        assert not glued_severity("INFORMATIONAL partOf") and not glued_severity("HIGH TEFCAID MEDIUM partOf")

    def test_stacked_words_catches_text_on_text_and_ignores_neighbours(self):
        def w(t, x0, x1, top=10.0, bottom=18.0):
            return {"text": t, "x0": x0, "x1": x1, "top": top, "bottom": bottom}
        assert stacked_words([w("Accounting", 40, 100), w("Received", 60, 110)])
        assert not stacked_words([w("one", 40, 60), w("two", 60.2, 80)])          # touching only
        assert not stacked_words([w("a", 40, 100, 10, 18), w("b", 60, 110, 30, 38)])  # different lines


@needs_weasyprint
@pytest.mark.parametrize("name", ALL)
def test_no_text_is_drawn_on_top_of_other_text(pdfs, name):
    problems = [(pi, stacked_words(p["words"])[:3]) for pi, p in enumerate(pdfs[name], start=1)
                if stacked_words(p["words"])]
    assert not problems, f"{name}: {problems[:4]}"


@needs_weasyprint
@pytest.mark.parametrize("name", ALL)
def test_no_cell_overruns_into_its_neighbour(pdfs, name):
    problems = [(pi, glued_severity(p["text"])[:3]) for pi, p in enumerate(pdfs[name], start=1)
                if glued_severity(p["text"])]
    assert not problems, f"{name}: severity word glued to the next cell: {problems[:4]}"


@needs_weasyprint
@pytest.mark.parametrize("name", ALL)
def test_no_heading_is_stranded_at_the_foot_of_a_page(pdfs, name):
    for pi, p in enumerate(pdfs[name], start=1):
        body = [w for w in p["words"] if w["top"] < p["h"] - 48]          # ignore the running footer band
        if not body:
            continue
        last_top = max(w["top"] for w in body)
        last_line = " ".join(w["text"] for w in sorted(body, key=lambda w: w["x0"]) if abs(w["top"] - last_top) < 2.0)
        assert not HEADING.match(last_line), f"{name}: page {pi} ends with a heading: {last_line!r}"


@needs_weasyprint
@pytest.mark.parametrize("name", ALL)
def test_no_raw_python_booleans_are_printed(pdfs, name):
    for pi, p in enumerate(pdfs[name], start=1):
        assert not re.search(r"\b(True|False)\b", p["text"]), f"{name}: raw boolean on page {pi}"


@needs_weasyprint
@pytest.mark.parametrize("name", ALL)
def test_every_table_continues_with_more_than_one_row_on_a_new_page(pdfs, name):
    """R-03: a page that carries only one or two table rows after a break. Landscape appendix pages only; a page
    is suspect when it is mostly empty AND is not the last landscape page before the portrait tail."""
    pages = pdfs[name]
    landscape = [i for i, p in enumerate(pages) if p["w"] > p["h"]]
    for i in landscape[:-1]:
        p = pages[i]
        body = [w for w in p["words"] if 40 < w["top"] < p["h"] - 48]
        used = (max(w["bottom"] for w in body) - min(w["top"] for w in body)) if body else 0
        assert used > 0.12 * p["h"], f"{name}: landscape page {i + 1} uses only {used:.0f}pt of {p['h']:.0f}pt"
