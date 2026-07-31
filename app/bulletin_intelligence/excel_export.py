"""FCC bulletin -> client-facing Excel workbook.

This is the sheet an FCC contact downloads from the email link, so it carries
only what a reader needs: the story, where it came from, and how to reach it.
It deliberately does NOT include relevance scores, story-group ids or the
subscription flag — those are internal QA signals and this workbook is served
from a PUBLIC endpoint. The QA spreadsheet still exists, guarded, and is built
by `_render_excel_workbook` in bulletin_download_routes.py.

Why articles are passed in rather than read off the briefing: the Briefing
dataclass stores `html_content`, `article_count` and `topic_counts` — it has no
article list. The caller rehydrates the stories by matching the URLs in the
stored HTML back to the archive, which is what keeps this workbook and the HTML
preview showing the same set rather than two independently re-derived ones.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAVY = "003087"
BLUE = "0078D4"
BAND = "F5F8FD"
GRID = "D0D0D0"

HEADERS = ["#", "Topic", "Source", "Headline", "Summary", "URL"]
WIDTHS = [5, 25, 22, 60, 80, 50]

_thin = Side(style="thin", color=GRID)
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def _clean(value: Any) -> str:
    """Excel rejects control characters in cell text; strip them rather than
    letting openpyxl raise on one bad scraped summary."""
    s = "" if value is None else str(value)
    return "".join(ch for ch in s if ch == "\n" or ch == "\t" or ord(ch) >= 32).strip()


def _sheet_title(prefix: str, date_str: str) -> str:
    """Excel sheet names cap at 31 chars and forbid []:*?/\\ ."""
    raw = f"{prefix} {date_str}".strip()
    for bad in "[]:*?/\\":
        raw = raw.replace(bad, "")
    return raw[:31] or "Bulletin"


def _source_of(article: Any) -> str:
    """Prefer the human outlet name; fall back through the collector fields."""
    for attr in ("source_name", "outlet", "source"):
        v = getattr(article, attr, "") or ""
        if v:
            return str(v)
    return ""


def create_bulletin_excel(
    briefing: Dict[str, Any],
    articles: List[Any],
    section_of: Optional[Callable[[Any], str]] = None,
) -> Workbook:
    """Build the two-sheet client workbook.

    `section_of` maps an article to its display section (the engine's
    `_section_of`). When absent we fall back to the article's own `section` or
    `topic`, so this module never hard-depends on the engine.
    """
    date_str = _clean(briefing.get("briefing_date") or "")
    agency = str(briefing.get("agency_id") or "fcc").upper()

    def topic_of(a: Any) -> str:
        if section_of is not None:
            try:
                return _clean(section_of(a)) or "General"
            except Exception:
                pass
        return _clean(getattr(a, "section", "") or getattr(a, "topic", "") or "General")

    # Sort by topic, then source within topic — the order the spec asks for and
    # the order that makes the sheet skimmable by section.
    rows = sorted(articles, key=lambda a: (topic_of(a).lower(), _source_of(a).lower()))

    wb = Workbook()

    # ── Sheet 1: the stories ──────────────────────────────────────────────────
    ws = wb.active
    ws.title = _sheet_title(f"{agency} Bulletin", date_str)

    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=NAVY)
    for col, name in enumerate(HEADERS, start=1):
        c = ws.cell(row=1, column=col, value=name)
        c.font = header_font
        c.fill = header_fill
        c.border = BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(col)].width = WIDTHS[col - 1]

    body = Font(name="Arial", size=10)
    topic_font = Font(name="Arial", size=10, bold=True, color=NAVY)
    url_font = Font(name="Arial", size=10, color=BLUE, underline="single")
    band_fill = PatternFill("solid", fgColor=BAND)
    wrap = Alignment(wrap_text=True, vertical="top")
    top = Alignment(vertical="top")

    for i, a in enumerate(rows, start=1):
        r = i + 1
        url = _clean(getattr(a, "url", ""))
        values = [
            i,
            topic_of(a),
            _source_of(a),
            _clean(getattr(a, "title", "")),
            _clean(getattr(a, "summary", "")),
            url,
        ]
        for col, v in enumerate(values, start=1):
            c = ws.cell(row=r, column=col, value=v)
            c.font = body
            c.border = BORDER
            c.alignment = wrap if col in (4, 5) else top
            if i % 2 == 0:
                c.fill = band_fill
        ws.cell(row=r, column=2).font = topic_font
        u = ws.cell(row=r, column=6)
        u.font = url_font
        if url.startswith(("http://", "https://")):
            # Guard the hyperlink: Excel refuses very long targets, and a broken
            # link should not cost us the whole workbook.
            if len(url) <= 2000:
                u.hyperlink = url

    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(rows) + 1}"

    # ── Sheet 2: summary ──────────────────────────────────────────────────────
    s = wb.create_sheet("Summary")
    s.column_dimensions["A"].width = 42
    s.column_dimensions["B"].width = 12

    s["A1"] = f"{agency} Daily News Summary"
    s["A1"].font = Font(name="Arial", size=14, bold=True, color=NAVY)
    s["A2"] = date_str
    s["A2"].font = Font(name="Arial", size=12, color=NAVY)
    s["A3"] = "Prepared by Alliance Global Tech, Inc. (AGT)"
    s["A3"].font = Font(name="Arial", size=10)

    for col, name in enumerate(("Topic", "Count"), start=1):
        c = s.cell(row=5, column=col, value=name)
        c.font = header_font
        c.fill = header_fill
        c.border = BORDER
        c.alignment = Alignment(horizontal="center")

    # Counts come from the rows actually written, so the two sheets can never
    # disagree — deriving them from briefing["topic_counts"] would reintroduce
    # the count-vs-render split this workbook exists to avoid.
    counts: Dict[str, int] = {}
    for a in rows:
        t = topic_of(a)
        counts[t] = counts.get(t, 0) + 1

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    r = 6
    for topic, n in ordered:
        tc = s.cell(row=r, column=1, value=topic)
        tc.font = Font(name="Arial", size=10, bold=True, color=NAVY)
        tc.border = BORDER
        nc = s.cell(row=r, column=2, value=n)
        nc.font = body
        nc.border = BORDER
        nc.alignment = Alignment(horizontal="center")
        r += 1

    tc = s.cell(row=r, column=1, value="TOTAL")
    tc.font = Font(name="Arial", size=11, bold=True, color=NAVY)
    tc.border = BORDER
    nc = s.cell(row=r, column=2)
    # A live formula, not a baked number, so the sheet stays correct if a reader
    # deletes a topic row.
    nc.value = f"=SUM(B6:B{r - 1})" if ordered else 0
    nc.font = Font(name="Arial", size=11, bold=True, color=NAVY)
    nc.border = BORDER
    nc.alignment = Alignment(horizontal="center")

    return wb
