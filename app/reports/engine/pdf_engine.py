"""
PDF generation via WeasyPrint.

ONE ENGINE, DELIBERATELY
────────────────────────
There is no fallback renderer. WeasyPrint is the engine; if it cannot run, PDF
generation raises `PDFEngineUnavailable` and says why.

The tempting alternative — fall back to ReportLab, which is already installed —
is refused on purpose. A different engine produces a structurally different
document: different tag tree, different reading order, different pagination,
different accessibility characteristics. The accessibility checks would then be
validating whichever engine happened to be available, and a PDF that passed in
CI could differ from the one a reviewer receives. A missing dependency that
announces itself is far safer than a silent substitution that produces a
plausible-looking, differently-structured file.

WHERE THIS RUNS
WeasyPrint needs the native Pango/Cairo/GObject stack. The deployment target is
the project's `python:3.12-slim` container, where `pip install weasyprint` pulls
what it needs and PDF generation works. On a bare Windows developer machine
those libraries are absent unless the GTK runtime has been installed separately,
so `pdf_available()` returns False there and the PDF tests skip with a named
reason rather than reporting a false pass.

PDF/UA IS A TARGET, NOT A CERTIFICATE
`pdf_variant="pdf/ua-1"` asks WeasyPrint to emit a tagged structure tree. It does
NOT make the document Section 508 conformant, and nothing here says it does —
see `accessibility.conformance_claim()`.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Requested PDF variant. A tagged structure tree is a precondition for an
#: accessible PDF; it is not the whole of one.
PDF_VARIANT = "pdf/ua-1"


class PDFEngineUnavailable(RuntimeError):
    """WeasyPrint's native dependencies are not installed on this host."""


@functools.lru_cache(maxsize=1)
def _probe() -> tuple:
    """(available, reason). Cached — the answer cannot change within a process."""
    try:
        import weasyprint  # noqa: F401
    except ImportError as exc:
        return False, (
            f"WeasyPrint is not installed ({exc}). Install it with "
            f"`pip install weasyprint`; it is already listed in requirements.txt."
        )
    except OSError as exc:
        # This message used to say the libraries "are present in the project's
        # Linux container image". They were not — the Dockerfile installed only
        # ffmpeg, so PDF generation would have failed in the container exactly
        # as it fails here. Phase 7.5 added the stack to the image and a build
        # step that fails the build if the engine cannot start. Saying where
        # something IS supposed to work has to stay true, or it stops anyone
        # from looking.
        return False, (
            f"WeasyPrint is installed but its native libraries are missing: {exc}. "
            f"WeasyPrint requires the Pango/Cairo/GObject stack. The project "
            f"Dockerfile installs it (libpango, libcairo, libgdk-pixbuf) and "
            f"verifies the engine at build time; on Windows it requires the GTK3 "
            f"runtime to be installed separately."
        )
    return True, "WeasyPrint and its native dependencies are available."


def pdf_available() -> bool:
    return _probe()[0]


def unavailable_reason() -> str:
    return _probe()[1]


def render_pdf(html: str, *, title: Optional[str] = None,
               variant: str = PDF_VARIANT) -> bytes:
    """Render a self-contained HTML document to a tagged PDF.

    `base_url` is deliberately NOT set. The HTML is already self-contained —
    fonts and chart images are inlined as data URIs — and leaving base_url unset
    means a stray relative URL cannot cause a filesystem read during rendering.
    """
    available, reason = _probe()
    if not available:
        raise PDFEngineUnavailable(reason)

    from weasyprint import HTML

    document = HTML(string=html)
    try:
        # pdf_tags: the structure tree assistive technology reads. PDF/UA
        # implies it, but stating it keeps the intent visible.
        return document.write_pdf(pdf_variant=variant, pdf_tags=True)
    except (TypeError, ValueError) as exc:
        # An older WeasyPrint may not know this variant name. Falling back to an
        # untagged PDF is acceptable ONLY because it is reported loudly — an
        # untagged PDF must never be presented as an accessible one.
        logger.warning(
            "WeasyPrint rejected pdf_variant=%r (%s). Emitting an UNTAGGED PDF. "
            "This document must NOT be described as accessible until it is "
            "regenerated with a version that supports the variant.", variant, exc)
        return document.write_pdf()


#: Opt-in ONLY. Set by an operator/test, never implied by absence of
#: WeasyPrint — see this module's own "ONE ENGINE, DELIBERATELY" docstring
#: above, which this constant and `render_pdf_dev_chromium_UNTAGGED` are
#: designed to respect, not quietly override. Checking this is the CALLER's
#: responsibility (`app/reports/routes.py::_pdf_response`); this module does
#: not auto-select an engine based on availability.
PDF_ENGINE_DEV_OVERRIDE_ENV = "PDF_ENGINE_DEV_OVERRIDE"
PDF_ENGINE_DEV_OVERRIDE_VALUE = "playwright_chromium_untagged_dev_only"


class PlaywrightEngineUnavailable(RuntimeError):
    """Playwright/Chromium is not installed (`pip install playwright` and
    `python -m playwright install chromium`)."""


@functools.lru_cache(maxsize=1)
def _probe_playwright() -> tuple:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError as exc:
        return False, f"playwright is not installed ({exc})."
    try:
        with playwright.sync_api.sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            browser.close()
    except Exception as exc:  # noqa: BLE001 — e.g. chromium binary not installed
        return False, (f"Playwright is installed but Chromium could not launch "
                       f"({type(exc).__name__}: {exc}). Run "
                       f"`python -m playwright install chromium`.")
    return True, "Playwright/Chromium is available."


def playwright_available() -> bool:
    return _probe_playwright()[0]


def render_pdf_dev_chromium_UNTAGGED(html: str, *, title: Optional[str] = None) -> bytes:
    """DEV/VERIFICATION ENGINE — NOT THE PRODUCTION PATH, NOT ACCESSIBLE.

    Deliberately named loudly (UNTAGGED in the function name, not just a
    docstring) so a caller cannot use it without seeing what it is. Renders
    via headless Chromium (Playwright), split into contiguous portrait/
    landscape runs and merged with pypdf, because Chromium's print engine
    does not implement CSS Paged Media Level 3 named pages / page-margin-box
    `string()` content — the mechanism `app/reports/styles/uswds_report.css`
    uses (`@page dp-landscape` + `.dp-wide { page: dp-landscape; }`) to put
    this report's wide evidence tables in landscape within one otherwise-
    portrait document under WeasyPrint. Without the split, Chromium silently
    ignores that CSS and crushes every wide table into a portrait page.

    THIS PDF HAS NO TAGGED STRUCTURE TREE. Confirmed, not assumed — see
    `inspect_pdf_tagging()`. It has NEVER been evaluated for Section 508 or
    PDF/UA conformance and must never be presented as accessible. It exists
    so real PDF output can be inspected (page order, text-layer, pagination)
    on hosts where WeasyPrint's native dependencies cannot be installed —
    this host included.
    """
    available, reason = _probe_playwright()
    if not available:
        raise PlaywrightEngineUnavailable(reason)

    import copy
    import io

    from bs4 import BeautifulSoup
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    soup = BeautifulSoup(html, "html.parser")
    main = soup.body.find("main")
    if main is None:
        # No <main> to split (not this report's template shape) — render as
        # one portrait document rather than fail outright.
        runs = [(False, [])]
        single_doc = True
    else:
        children = main.find_all(recursive=False)
        runs = []
        for child in children:
            is_wide = "dp-wide" in (child.get("class") or [])
            if runs and runs[-1][0] == is_wide:
                runs[-1][1].append(child)
            else:
                runs.append((is_wide, [child]))
        single_doc = False

    def _build_doc(elements) -> str:
        if single_doc:
            return html
        doc = copy.copy(soup)
        new_main = doc.new_tag("main")
        for el in elements:
            new_main.append(copy.copy(el))
        old_main = doc.body.find("main")
        old_main.replace_with(new_main)
        return str(doc)

    writer = PdfWriter()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            for is_wide, elements in runs:
                page = browser.new_page()
                try:
                    page.set_content(_build_doc(elements), wait_until="load")
                    part_bytes = page.pdf(print_background=True, format="Letter",
                                          landscape=is_wide)
                finally:
                    page.close()
                reader = PdfReader(io.BytesIO(part_bytes))
                for pg in reader.pages:
                    writer.add_page(pg)
        finally:
            browser.close()

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


#: ISO 32000-1 §14.8.4 standard structure types. A tag outside this set must be
#: mapped to one of these in the structure tree's /RoleMap (PDF/UA-1 §7.1),
#: otherwise a validator — and a screen reader — has no idea what it is.
_STANDARD_STRUCTURE_TYPES = frozenset({
    "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption", "TOC", "TOCI",
    "Index", "NonStruct", "Private", "P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "L",
    "LI", "Lbl", "LBody", "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot", "Span",
    "Quote", "Note", "Reference", "BibEntry", "Code", "Link", "Annot", "Ruby", "RB",
    "RT", "RP", "Warichu", "WT", "WP", "Figure", "Formula", "Form",
})
#: The non-standard tags Chromium's tagged-PDF export emits for HTML phrasing
#: elements (<strong>, <em>, …), and the standard type each maps to. Only tags
#: in this table are mapped automatically; an unexpected non-standard tag is
#: REPORTED (see `inspect_pdf_accessibility`), never guessed into a mapping.
_ROLE_MAP_DEFAULTS = {"Strong": "Span", "Em": "Span", "Strike": "Span",
                      "Underline": "Span", "Mark": "Span", "Sub": "Span", "Sup": "Span"}

_PATH_CONSTRUCT_OPS = frozenset({"m", "l", "c", "v", "y", "h", "re"})
_PATH_PAINT_OPS = frozenset({"f", "F", "f*", "B", "B*", "b", "b*", "S", "s"})
_OTHER_CONTENT_OPS = frozenset({"Do", "sh", "BI", "ID", "EI", "INLINE IMAGE"})
_TEXT_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})


_SUPPRESS_MARGIN_BOXES_CSS = (
    "<style>@page{@top-left{content:none}@top-center{content:none}"
    "@top-right{content:none}@bottom-center{content:none}}"
    "@page dp-landscape{@top-left{content:none}@top-center{content:none}"
    "@top-right{content:none}@bottom-center{content:none}}"
    "@page :first{@top-left{content:none}@top-center{content:none}"
    "@top-right{content:none}@bottom-center{content:none}}</style>")


def _escape_html(text: Optional[str]) -> str:
    import html as html_mod
    return html_mod.escape(text or "", quote=True)


def _css_top_center_marking(html: str) -> str:
    """The literal string the template put in `@page { @top-center { content:
    "…" } }` — base.html's per-page data-classification marking — or "" when
    the document carries none (Government-classified data). Read from the
    document, so the engine can never invent a marking the template did not."""
    import re

    match = re.search(r"@top-center\s*\{\s*content:\s*\"([^\"]+)\"", html)
    return match.group(1).strip() if match else ""


def _html_document_metadata(html: str) -> Dict[str, str]:
    """The document properties the report itself declares — `<title>`,
    `<meta name="author">`, `<meta name="keywords">`,
    `<meta name="dcterms.created">`, `<html lang>` — exactly the fields
    base.html emits so that WeasyPrint writes them into the PDF. The dev
    Chromium engine reads the same fields from the same place, so the two
    engines describe the document identically; nothing is hard-coded here."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    def meta(name: str) -> str:
        tag = soup.find("meta", attrs={"name": name})
        return (tag.get("content") or "").strip() if tag else ""

    html_tag = soup.find("html")
    return {
        "title": soup.title.get_text(" ", strip=True) if soup.title else "",
        "author": meta("author"),
        "keywords": meta("keywords"),
        "created": meta("dcterms.created"),
        "lang": ((html_tag.get("lang") if html_tag else "") or "").strip(),
    }


def _mat_mul(a: tuple, b: tuple) -> tuple:
    """PDF affine matrices as 6-tuples (a b c d e f): returns a × b."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0 * b0 + a1 * b2, a0 * b1 + a1 * b3,
            a2 * b0 + a3 * b2, a2 * b1 + a3 * b3,
            a4 * b0 + a5 * b2 + b4, a4 * b1 + a5 * b3 + b5)


_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _aria_hidden_strings(html: str) -> set:
    """The text of every element the template itself marked
    `aria-hidden="true"` — the status-indicator glyphs base.html's macros
    emit beside a text label (`<span class="glyph" aria-hidden="true">■</span>`).
    The document declared them decorative; nothing else ever qualifies."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    return {t.get_text(strip=True) for t in soup.select('[aria-hidden="true"]')
            if t.get_text(strip=True)}


def _decorative_glyph_positions(pdf_bytes: bytes, glyphs: set) -> Dict[int, list]:
    """Where, on each page, a text-showing operation decodes to EXACTLY one
    of the document's aria-hidden strings. Decoded with pypdf (which reads the
    fonts' ToUnicode maps); positions are device-space (x, y) of the text
    origin, the same quantity `_mark_page_chrome_as_artifacts` computes from
    the raw operators, so the two passes can be matched by position."""
    import io

    from pypdf import PdfReader

    if not glyphs:
        return {}
    found: Dict[int, list] = {}
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for index, page in enumerate(reader.pages):
        hits: list = []

        def visitor(text, cm, tm, font_dict, font_size):
            if text.strip() in glyphs:
                device = _mat_mul(tuple(float(v) for v in tm), tuple(float(v) for v in cm))
                hits.append((device[4], device[5]))

        page.extract_text(visitor_text=visitor)
        if hits:
            found[index] = hits
    return found


def _mark_page_chrome_as_artifacts(ops: list, page_box: tuple, chrome_band_pt: float,
                                   decorative_positions: Optional[list] = None) -> tuple:
    """Wrap the content Chromium draws OUTSIDE the tagged document body —
    the running header/footer text and the full-page background fill — in
    `/Artifact` marked-content sequences (ISO 32000-1 §14.8.2.2; PDF/UA-1
    §7.1 requires every content item to be either real tagged content or an
    artifact). Returns `(new_ops, stats)`.

    FAIL-SAFE BY CONSTRUCTION. Nothing here can hide meaningful content:

    * Only content at marked-content depth 0 is touched. Everything inside a
      `/P <</MCID n>> BDC … EMC` (every table, heading, paragraph) is at
      depth ≥ 1 and is copied through untouched.
    * A depth-0 `BT…ET` block is wrapped as a Pagination artifact only when
      EVERY text-showing operator in it is positioned inside the page's own
      top or bottom margin band (`chrome_band_pt` from the page edge). That
      is where Chromium draws `header_template`/`footer_template`, and where
      the laid-out document body — which lives inside the margins by
      definition — can never be. The position is computed from the real CTM
      and text matrix, not guessed from operator counts. Any depth-0 text
      outside the bands is left exactly as it was and counted in
      `depth0_text_blocks_left_unwrapped`, because that would mean document
      text escaped tagging and a validator must still see it.
    * A depth-0 text block INSIDE the content area is wrapped as a Layout
      artifact only when every text origin in it coincides (±1.5 pt) with a
      position where pypdf decoded a string the document itself marked
      `aria-hidden="true"` (`decorative_positions`, from
      `_decorative_glyph_positions`): the status-indicator glyphs ■ ● ▲ that
      base.html emits beside a text label. Chromium drops aria-hidden content
      from the tag tree but still paints it; the template declared it
      decorative, so Artifact is the faithful translation. Text the document
      did not declare decorative never qualifies, whatever it looks like.
    * A depth-0 fill is wrapped as a Layout artifact only when it is ONE
      rectangle covering ≥ 90 % of the page (the page background Chromium
      paints for `print_background=True`). Any other depth-0 painting —
      a smaller fill, a stroke, an image XObject, an inline image — is left
      unwrapped and counted in `unclassified_depth0_items`.
    """
    from pikepdf import Dictionary, Name, Operator

    pagination = Dictionary(Type=Name.Pagination)
    layout = Dictionary(Type=Name.Layout)
    x0, y0, x1, y1 = page_box
    page_area = abs((x1 - x0) * (y1 - y0))
    top_band_from = max(y0, y1) - chrome_band_pt
    bottom_band_to = min(y0, y1) + chrome_band_pt
    decorative = list(decorative_positions or [])

    def is_decorative(points: list) -> bool:
        return bool(points) and all(
            any(abs(px - dx) <= 1.5 and abs(py - dy) <= 1.5 for dx, dy in decorative)
            for px, py in points)

    stats = {"text_blocks_wrapped": 0, "background_fills_wrapped": 0,
             "decorative_glyph_blocks_wrapped": 0,
             "unclassified_depth0_items": 0, "depth0_text_blocks_left_unwrapped": 0}

    out: list = []
    run: list = []
    run_kind: Optional[str] = None
    rect_count = 0
    other_construct = 0
    rect_area = 0.0
    depth = 0
    # Graphics state, tracked across the WHOLE stream (q/Q/cm nest around
    # tagged content too), so a depth-0 text position is computed correctly.
    ctm = _IDENTITY
    ctm_stack: list = []
    tlm = _IDENTITY
    tm = _IDENTITY
    leading = 0.0
    text_ys: list = []

    def flush(kind: Optional[str]) -> None:
        nonlocal run, run_kind, rect_count, other_construct, rect_area
        if run:
            if kind == "pagination":
                out.append(([Name.Artifact, pagination], Operator("BDC")))
                out.extend(run)
                out.append(([], Operator("EMC")))
            elif kind == "layout":
                out.append(([Name.Artifact, layout], Operator("BDC")))
                out.extend(run)
                out.append(([], Operator("EMC")))
            else:
                out.extend(run)
        run = []
        run_kind = None
        rect_count = 0
        other_construct = 0
        rect_area = 0.0

    def nums(operands, n):
        try:
            return tuple(float(v) for v in operands[:n])
        except (TypeError, ValueError):
            return None

    for instr in ops:
        op = str(instr.operator)
        operands = instr.operands

        # ── graphics / text state, regardless of depth ─────────────────
        if op == "q":
            ctm_stack.append(ctm)
        elif op == "Q":
            ctm = ctm_stack.pop() if ctm_stack else _IDENTITY
        elif op == "cm":
            m = nums(operands, 6)
            if m:
                ctm = _mat_mul(m, ctm)
        elif op == "BT":
            tlm = tm = _IDENTITY
        elif op == "Tm":
            m = nums(operands, 6)
            if m:
                tlm = tm = m
        elif op in ("Td", "TD"):
            d = nums(operands, 2)
            if d:
                if op == "TD":
                    leading = -d[1]
                tlm = tm = _mat_mul((1, 0, 0, 1, d[0], d[1]), tlm)
        elif op == "TL":
            d = nums(operands, 1)
            if d:
                leading = d[0]
        elif op in ("T*", "'", '"'):
            tlm = tm = _mat_mul((1, 0, 0, 1, 0, -leading), tlm)

        if op in ("BDC", "BMC"):
            flush(None)
            depth += 1
            out.append(instr)
            continue
        if op == "EMC":
            flush(None)
            depth -= 1
            out.append(instr)
            continue
        if depth > 0:
            out.append(instr)
            continue

        # ── depth 0 ──────────────────────────────────────────────────────
        if run_kind == "text":
            run.append(instr)
            if op in _TEXT_SHOW_OPS:
                device = _mat_mul(tm, ctm)
                text_ys.append((device[4], device[5]))
            if op == "ET":
                in_top = all(y >= top_band_from for _, y in text_ys)
                in_bottom = all(y <= bottom_band_to for _, y in text_ys)
                if text_ys and (in_top or in_bottom):
                    stats["text_blocks_wrapped"] += 1
                    flush("pagination")
                elif text_ys and is_decorative(text_ys):
                    stats["decorative_glyph_blocks_wrapped"] += 1
                    flush("layout")
                else:
                    if text_ys:
                        stats["depth0_text_blocks_left_unwrapped"] += 1
                    flush(None)
                text_ys = []
            continue
        if op == "BT":
            flush(None)
            run_kind = "text"
            text_ys = []
            run.append(instr)
            continue
        if op in _TEXT_SHOW_OPS:
            # Text outside BT/ET is malformed; never wrap it, just report it.
            flush(None)
            out.append(instr)
            stats["unclassified_depth0_items"] += 1
            continue
        if op in _PATH_CONSTRUCT_OPS:
            if run_kind != "path":
                flush(None)
                run_kind = "path"
            if op == "re":
                rect_count += 1
                try:
                    w, h = float(instr.operands[2]), float(instr.operands[3])
                    rect_area += abs(w * h)
                except (IndexError, TypeError, ValueError):
                    other_construct += 1
            else:
                other_construct += 1
            run.append(instr)
            continue
        if op in ("W", "W*"):
            if run_kind != "path":
                flush(None)
                run_kind = "path"
            run.append(instr)
            continue
        if op == "n":
            # A path ended without painting (a clip) is not a content item.
            if run_kind != "path":
                flush(None)
                run_kind = "path"
            run.append(instr)
            flush(None)
            continue
        if op in _PATH_PAINT_OPS:
            if run_kind != "path":
                flush(None)
                run_kind = "path"
            run.append(instr)
            is_background = (rect_count == 1 and other_construct == 0
                             and page_area > 0 and rect_area >= 0.9 * page_area)
            if is_background:
                stats["background_fills_wrapped"] += 1
                flush("layout")
            else:
                stats["unclassified_depth0_items"] += 1
                flush(None)
            continue
        if op in _OTHER_CONTENT_OPS:
            flush(None)
            out.append(instr)
            stats["unclassified_depth0_items"] += 1
            continue
        # Graphics/text-state operators (q, Q, cm, gs, g, rg, w, Tf, Td, …):
        # part of whichever run is open, otherwise passed straight through.
        if run_kind is not None:
            run.append(instr)
        else:
            out.append(instr)
    flush(None)
    return out, stats


def _struct_types_present(struct_tree_root) -> set:
    """Every /S tag name used anywhere under a pikepdf /StructTreeRoot."""
    import pikepdf

    seen: set = set()
    types: set = set()

    def walk(node) -> None:
        if not isinstance(node, pikepdf.Dictionary):
            return
        key = node.objgen
        if key != (0, 0):
            if key in seen:
                return
            seen.add(key)
        s = node.get("/S")
        if s is not None:
            types.add(str(s).lstrip("/"))
        kids = node.get("/K")
        if isinstance(kids, pikepdf.Dictionary):
            walk(kids)
        elif isinstance(kids, pikepdf.Array):
            for kid in kids:
                walk(kid)  # ints (MCIDs) are skipped by the isinstance guard

    walk(struct_tree_root)
    return types


#: Chromium places `header_template`/`footer_template` inside the page
#: margins requested at render time. The tagged engine asks for 0.6 in top
#: and bottom; the band is that margin plus a small tolerance for descenders.
_CHROME_MARGIN_PT = 0.6 * 72
_CHROME_BAND_PT = _CHROME_MARGIN_PT + 12.0


def _pdf_ua_postprocess(pdf_bytes: bytes, html: str, *,
                        chrome_band_pt: float = _CHROME_BAND_PT) -> tuple:
    """Close the three PDF/UA-1 gaps veraPDF identified in Chromium's tagged
    output (2026-10-03, `verapdf_report_ua1.xml`, 3 of 106 rules failed):

    1. ISO 14289-1 §7.1 t8 — no XMP metadata stream. Written here from the
       document's own declared properties (`_html_document_metadata`), with
       `pdfuaid:part = 1`, plus the Info-dictionary title, `/Lang` and
       `/ViewerPreferences /DisplayDocTitle true` PDF/UA also expects.
    2. ISO 14289-1 §7.1 t3 — 563 content items neither tagged nor marked
       Artifact: the per-page header/footer text and page-background fill
       Chromium draws outside the tagged body. Marked as Pagination/Layout
       artifacts by `_mark_page_chrome_as_artifacts`, which refuses to touch
       anything that could be document content.
    3. ISO 14289-1 §7.1 t5 — the non-standard `Strong` tag with no RoleMap
       entry. Mapped per `_ROLE_MAP_DEFAULTS`; any other non-standard tag is
       left unmapped and reported.

    Returns `(bytes, stats)`. The structure tree, MCIDs and ParentTree are
    untouched: artifact sequences carry no MCID, so nothing the tree points
    at moves.
    """
    import datetime
    import io

    import pikepdf
    from pikepdf import Dictionary, Name, parse_content_stream, unparse_content_stream

    info = _html_document_metadata(html)
    glyph_positions = _decorative_glyph_positions(pdf_bytes, _aria_hidden_strings(html))
    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    stats: Dict[str, Any] = {
        "title": info["title"], "lang": info["lang"],
        "text_blocks_wrapped": 0, "background_fills_wrapped": 0,
        "decorative_glyph_blocks_wrapped": 0,
        "unclassified_depth0_items": 0, "depth0_text_blocks_left_unwrapped": 0,
        "role_map_added": [], "non_standard_tags_unmapped": [],
    }

    # 1. Metadata.
    created = info["created"] or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=True) as meta:
        meta["dc:title"] = info["title"]
        if info["author"]:
            meta["dc:creator"] = [info["author"]]
        if info["keywords"]:
            meta["pdf:Keywords"] = info["keywords"]
        meta["xmp:CreateDate"] = created
        meta["pdfuaid:part"] = 1
    pdf.docinfo["/Title"] = info["title"]
    if info["author"]:
        pdf.docinfo["/Author"] = info["author"]
    if "/Lang" not in pdf.Root and info["lang"]:
        pdf.Root.Lang = pikepdf.String(info["lang"])
    vp = pdf.Root.get("/ViewerPreferences")
    if vp is None:
        pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    else:
        vp.DisplayDocTitle = True

    # 2. Artifacts.
    for index, page in enumerate(pdf.pages):
        page.contents_coalesce()
        box = tuple(float(x) for x in page.mediabox)
        new_ops, pstats = _mark_page_chrome_as_artifacts(
            parse_content_stream(page), box, chrome_band_pt,
            decorative_positions=glyph_positions.get(index))
        for key in ("text_blocks_wrapped", "background_fills_wrapped",
                    "decorative_glyph_blocks_wrapped",
                    "unclassified_depth0_items", "depth0_text_blocks_left_unwrapped"):
            stats[key] += pstats[key]
        if (pstats["text_blocks_wrapped"] or pstats["background_fills_wrapped"]
                or pstats["decorative_glyph_blocks_wrapped"]):
            page.Contents = pdf.make_stream(unparse_content_stream(new_ops))

    # 3. RoleMap.
    root = pdf.Root.get("/StructTreeRoot")
    if root is not None:
        present = _struct_types_present(root)
        role_map = root.get("/RoleMap")
        if role_map is None:
            role_map = Dictionary()
            root.RoleMap = role_map
        for tag in sorted(present - _STANDARD_STRUCTURE_TYPES):
            if Name("/" + tag) in role_map:
                continue
            target = _ROLE_MAP_DEFAULTS.get(tag)
            if target:
                role_map[Name("/" + tag)] = Name("/" + target)
                stats["role_map_added"].append(f"{tag}->{target}")
            else:
                stats["non_standard_tags_unmapped"].append(tag)

    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue(), stats


def structure_signature(pdf_bytes: bytes) -> Dict[str, Any]:
    """A comparable fingerprint of the structure tree: element count, tag
    histogram, and the ordered headings as (type, page, mcid). Two PDFs with
    equal signatures have the same tree shape and the same heading reading
    order — the check that proves `_pdf_ua_postprocess` never touched the
    tree (adopted from the stopped Lane R post-processor, 2026-10-03)."""
    import collections
    import io

    import pikepdf

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if root is None:
            return {"element_count": 0, "tag_histogram": {}, "headings": []}
        page_index = {pg.objgen: i for i, pg in enumerate(pdf.pages)}
        types: list = []
        headings: list = []
        seen: set = set()

        def walk(node) -> None:
            if not isinstance(node, pikepdf.Dictionary):
                return
            key = node.objgen
            if key != (0, 0):
                if key in seen:
                    return
                seen.add(key)
            s = node.get("/S")
            if s is not None:
                tag = str(s).lstrip("/")
                types.append(tag)
                if tag in ("H", "H1", "H2", "H3", "H4", "H5", "H6"):
                    pg = node.get("/Pg")
                    pgi = page_index.get(pg.objgen, -1) if isinstance(pg, pikepdf.Dictionary) else -1
                    mcid = -1
                    kids = node.get("/K")
                    if isinstance(kids, pikepdf.Array):
                        for kid in kids:
                            if isinstance(kid, pikepdf.Dictionary):
                                if kid.get("/MCID") is not None:
                                    mcid = int(kid["/MCID"])
                                    break
                            else:
                                try:
                                    mcid = int(kid)
                                    break
                                except (TypeError, ValueError):
                                    continue
                    elif kids is not None and not isinstance(kids, pikepdf.Dictionary):
                        try:
                            mcid = int(kids)
                        except (TypeError, ValueError):
                            pass
                    headings.append((tag, pgi, mcid))
            kids = node.get("/K")
            if isinstance(kids, pikepdf.Array):
                for kid in kids:
                    walk(kid)
            elif isinstance(kids, pikepdf.Dictionary):
                walk(kids)

        walk(root)
        return {
            "element_count": len(types),
            "tag_histogram": dict(sorted(collections.Counter(types).items())),
            "headings": headings,
        }


def inspect_pdf_accessibility(pdf_bytes: bytes) -> Dict[str, Any]:
    """The checks a reviewer performs by hand, done programmatically where a
    program can do them, with an explicit statement of what they do NOT
    establish. Complements `inspect_pdf_tagging()` (structure-tree presence).

    Returns, per check, what was found — never a conformance verdict. A
    structure tree with correct heading nesting and real TH cells is a
    precondition for an accessible table; only assistive-technology testing
    by a qualified reviewer establishes that the table IS accessible.
    """
    import io
    import re

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    catalog = reader.trailer["/Root"]

    xmp_bytes = b""
    if "/Metadata" in catalog:
        try:
            xmp_bytes = catalog["/Metadata"].get_object().get_data()
        except Exception:  # noqa: BLE001 — a malformed stream is itself the finding
            xmp_bytes = b""
    pdfua_part = None
    # pikepdf writes `<pdfuaid:part xmlns:pdfuaid="…">1</pdfuaid:part>`; other
    # writers use the attribute form `pdfuaid:part="1"`. Accept both.
    match = re.search(rb"pdfuaid:part(?:=\"(\d+)\"|[^>]*>\s*(\d+))", xmp_bytes)
    if match:
        pdfua_part = int(next(g for g in match.groups() if g))

    viewer_prefs = catalog.get("/ViewerPreferences")
    display_doc_title = bool(viewer_prefs.get_object().get("/DisplayDocTitle", False)) if viewer_prefs else False
    info = reader.metadata or {}

    sequence: list = []
    counts: Dict[str, int] = {}
    figures = links = figures_with_alt = links_with_alt = 0
    role_map: Dict[str, str] = {}
    seen: set = set()

    def walk(node) -> None:
        nonlocal figures, links, figures_with_alt, links_with_alt
        if id(node) in seen:
            return
        seen.add(id(node))
        obj = node.get_object() if hasattr(node, "get_object") else node
        if not isinstance(obj, dict):
            return
        tag = obj.get("/S")
        if tag is not None:
            name = str(tag).lstrip("/")
            sequence.append(name)
            counts[name] = counts.get(name, 0) + 1
            if name == "Figure":
                figures += 1
                figures_with_alt += 1 if "/Alt" in obj else 0
            if name == "Link":
                links += 1
                links_with_alt += 1 if ("/Alt" in obj or "/ActualText" in obj) else 0
        kids = obj.get("/K")
        if isinstance(kids, list):
            for kid in kids:
                walk(kid)
        elif kids is not None:
            walk(kids)

    if "/StructTreeRoot" in catalog:
        root = catalog["/StructTreeRoot"].get_object()
        rm = root.get("/RoleMap")
        if rm is not None:
            role_map = {str(k).lstrip("/"): str(v).lstrip("/") for k, v in rm.get_object().items()}
        kids = root.get("/K")
        if isinstance(kids, list):
            for kid in kids:
                walk(kid)
        elif kids is not None:
            walk(kids)

    headings = [t for t in sequence if re.fullmatch(r"H[1-6]", t)]
    nesting_ok = bool(headings) and headings[0] == "H1"
    prev = 0
    for h in headings:
        level = int(h[1])
        if level > prev + 1:
            nesting_ok = False
        prev = level

    link_annotations = 0
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            try:
                if str(annot.get_object().get("/Subtype")) == "/Link":
                    link_annotations += 1
            except Exception:  # noqa: BLE001
                continue

    present = set(sequence)
    non_standard = sorted(present - _STANDARD_STRUCTURE_TYPES)
    return {
        "lang": str(catalog.get("/Lang")) if "/Lang" in catalog else None,
        "has_xmp_metadata": bool(xmp_bytes),
        "xmp_pdfua_part": pdfua_part,
        "info_title": str(info.get("/Title", "")) if info else "",
        "display_doc_title": display_doc_title,
        "heading_sequence": headings,
        "heading_nesting_ok": nesting_ok,
        "table_count": counts.get("Table", 0),
        "th_count": counts.get("TH", 0),
        "td_count": counts.get("TD", 0),
        "link_struct_elements": links,
        "link_struct_elements_with_alt": links_with_alt,
        "link_annotations": link_annotations,
        "figures": figures,
        "figures_with_alt": figures_with_alt,
        "role_map": role_map,
        "non_standard_tags": non_standard,
        "non_standard_tags_unmapped": [t for t in non_standard if t not in role_map],
        "statement": (
            "Programmatic equivalents of a manual review: language, metadata, "
            "heading order, table header cells, link and figure alternatives. "
            "They do NOT establish Section 508 or PDF/UA conformance; that "
            "requires Acrobat's accessibility checker and assistive-technology "
            "testing by a qualified reviewer, neither of which this performs."
        ),
    }


def render_pdf_dev_chromium_tagged(html: str, *, title: Optional[str] = None) -> bytes:
    """DEV/VERIFICATION ENGINE — tagged, single-document, NOT the production path.

    Supersedes `render_pdf_dev_chromium_UNTAGGED` for accessibility testing.
    That function split the document into portrait/landscape runs (because
    Chromium ignores this template's `@page dp-landscape` CSS Paged Media
    rule) and merged them with pypdf — which works for visual inspection but
    was CONFIRMED (by direct catalog inspection, not assumed) to drop the
    entire structure tree: pypdf's `add_page`/`append`, and pikepdf's
    `pages.extend`, were both tested and both produce a merged document with
    no `/StructTreeRoot` at all, even when every source fragment was itself
    correctly tagged. Page-level PDF merging does not carry structure trees
    across document boundaries in either library.

    Real fix, not a patch: render the WHOLE document in ONE Playwright call
    with `tagged=True` and a single global `landscape=True` page orientation
    — this needs no split and no merge, so there is exactly one structure
    tree, confirmed intact by `inspect_pdf_tagging()` (8,676 structure
    elements across 15 tag types on the reference 60-entity delivery,
    including real `/Table`/`/TR`/`/TD`/`/TH` and `/H1`/`/H2` tags — not a
    stub). The tradeoff: portrait-only sections (identity, reconciliation,
    narrative text) render on a wider page than they need, rather than
    switching per-section orientation — a real, accepted visual cost for a
    document that is otherwise reading-order-correct and has a genuine,
    verifiable structure tree, which split+merge could not offer at all.

    Requires Playwright >=1.63 (the version this project installed — `tagged`
    is not available in materially older releases; check `pip show
    playwright` and Playwright's release notes if this raises TypeError on
    an older install).

    Running headers and global "Page X of Y" numbering — a gap this
    function's split+merge predecessor could not close, since Playwright's
    `header_template`/`footer_template` are per-document and the merge
    produced one document out of three separately-numbered ones — now work
    directly, for free, because there is only ever one document here.

    POST-PROCESSED FOR PDF/UA-1 (2026-10-03). veraPDF's PDF/UA-1 profile
    failed Chromium's raw output on exactly three rules (XMP metadata absent;
    header/footer text and page-background fill neither tagged nor marked
    Artifact; `Strong` unmapped in the RoleMap). `_pdf_ua_postprocess` closes
    those three with pikepdf — see its docstring for the fail-safe rules that
    stop it from ever hiding document content as decoration — and the result
    is re-validated with `scripts/verapdf_validate.py`.

    STILL NOT SECTION 508/PDF-UA CERTIFIED. A structure tree is a
    precondition, not proof — see `inspect_pdf_tagging()`'s own statement, and
    `inspect_pdf_accessibility()` for what the manual-equivalent checks do
    and do not establish.
    """
    available, reason = _probe_playwright()
    if not available:
        raise PlaywrightEngineUnavailable(reason)

    from playwright.sync_api import sync_playwright

    # CSS page-margin boxes (2026-10-03). Chromium renders the template's
    # `@page { @top-center { content: "DEVELOPMENT / TEST DATA — …" } }`
    # marking (base.html) as a real margin box — but UNTAGGED, and on top of
    # the Playwright header, so the marking was drawn twice per page and was
    # 51 of the 563 untagged content items veraPDF reported. In this engine
    # the margin boxes are suppressed and the marking is carried by the
    # header template instead: once per page, still red and bold, and then
    # marked as a Pagination artifact by `_pdf_ua_postprocess` like the rest
    # of the running header. WeasyPrint (the production engine) is untouched
    # and keeps using the CSS boxes. `string()`/`counter()` margin boxes from
    # uswds_report.css are not rendered by Chromium either way.
    marking = _css_top_center_marking(html)
    marking_html = (
        f'<span style="color:#b50909; font-weight:700;">{_escape_html(marking)}</span>'
        '<span style="margin:0 10px;">·</span>' if marking else '')
    header_template = (
        '<div style="font-size:9px; width:100%; text-align:center; '
        'color:#555;">' + marking_html + 'DocuAction TEFCA ARC'
        + (f' — {_escape_html(title)}' if title else '') + '</div>'
    )
    footer_template = (
        '<div style="font-size:9px; width:100%; text-align:center; '
        'color:#555;">Page <span class="pageNumber"></span> of '
        '<span class="totalPages"></span></div>'
    )
    html = html.replace("</head>", _SUPPRESS_MARGIN_BOXES_CSS + "</head>", 1)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page()
            try:
                page.set_content(html, wait_until="load")
                try:
                    raw = page.pdf(print_background=True, format="Letter",
                                   landscape=True, tagged=True,
                                   display_header_footer=True,
                                   header_template=header_template,
                                   footer_template=footer_template,
                                   margin={"top": "0.6in", "bottom": "0.6in"})
                except TypeError as exc:
                    raise PlaywrightEngineUnavailable(
                        f"Installed Playwright does not support tagged PDF "
                        f"export ({exc}). Upgrade with `pip install -U "
                        f"playwright` (tagged PDF export requires Playwright "
                        f">=1.63) and re-run `python -m playwright install "
                        f"chromium`.") from exc
            finally:
                page.close()
        finally:
            browser.close()

    processed, stats = _pdf_ua_postprocess(raw, html)
    logger.info("pdf_ua_postprocess", extra={"pdf_ua": stats})
    return processed


def _walk_struct_tree(node, seen: set) -> tuple:
    """Returns (element_count, {tag types}) for one structure-tree node,
    recursing through `/K`. `seen` guards against the tree's own internal
    object-reference cycles (a real possibility in PDF object graphs)."""
    if id(node) in seen:
        return 0, set()
    seen.add(id(node))
    obj = node.get_object() if hasattr(node, "get_object") else node
    if not isinstance(obj, dict):
        return 0, set()
    count = 1
    types = set()
    tag = obj.get("/S")
    if tag is not None:
        types.add(str(tag))
    kids = obj.get("/K")
    if isinstance(kids, list):
        for kid in kids:
            c, t = _walk_struct_tree(kid, seen)
            count += c
            types |= t
    elif kids is not None:
        c, t = _walk_struct_tree(kids, seen)
        count += c
        types |= t
    return count, types


def inspect_pdf_tagging(pdf_bytes: bytes) -> Dict[str, Any]:
    """Ground truth, not an assumption: does this PDF have a structure tree,
    and is it a real one or an empty stub?

    Checks the document catalog for `/MarkInfo` and `/StructTreeRoot` — the
    two things a real screen reader / PDF-UA validator looks for — and then
    walks the tree itself, counting elements and distinct tag types, so
    "has a structure tree" can be told apart from "has a trivial one-node
    placeholder structure tree". Returns all of this plus an explicit,
    unambiguous statement of what it does and does not prove.
    """
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    catalog = reader.trailer["/Root"]
    has_mark_info = "/MarkInfo" in catalog
    marked = bool(catalog.get("/MarkInfo", {}).get("/Marked", False)) if has_mark_info else False
    has_struct_tree = "/StructTreeRoot" in catalog

    element_count = 0
    tag_types: set = set()
    if has_struct_tree:
        root = catalog["/StructTreeRoot"].get_object()
        kids = root.get("/K")
        seen: set = set()
        if isinstance(kids, list):
            for kid in kids:
                c, t = _walk_struct_tree(kid, seen)
                element_count += c
                tag_types |= t
        elif kids is not None:
            element_count, tag_types = _walk_struct_tree(kids, seen)

    return {
        "has_mark_info": has_mark_info,
        "marked_true": marked,
        "has_struct_tree_root": has_struct_tree,
        "is_tagged_pdf": has_mark_info and marked and has_struct_tree,
        "struct_element_count": element_count,
        "distinct_tag_types": sorted(tag_types),
        "is_trivial_stub": has_struct_tree and element_count <= 1,
        "statement": (
            "This checks for the presence AND actual content of a structure "
            "tree (PDF/UA's precondition). It does NOT check tag CORRECTNESS, "
            "reading-order fidelity, alt-text on images, color contrast, or "
            "any other Section 508/PDF-UA requirement. A PDF with "
            "is_tagged_pdf=True and a non-trivial struct_element_count is "
            "NOT thereby proven 508-conformant; a PDF with is_tagged_pdf=False "
            "is CERTAINLY not."
        ),
    }


def engine_info() -> Dict[str, Any]:
    """Engine status, for the report snapshot and the health endpoint."""
    available, reason = _probe()
    version = None
    if available:
        try:
            import weasyprint
            version = getattr(weasyprint, "__version__", None)
        except Exception:  # noqa: BLE001
            version = None
    return {
        "engine": "WeasyPrint",
        "available": available,
        "version": version,
        "reason": reason,
        "pdf_variant_requested": PDF_VARIANT,
        "note": ("A tagged PDF is a precondition for accessibility, not proof of "
                 "it. No Section 508 conformance is claimed from successful "
                 "generation."),
    }
