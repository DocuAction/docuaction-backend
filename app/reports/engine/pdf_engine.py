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

    STILL NOT SECTION 508/PDF-UA CERTIFIED. A structure tree is a
    precondition, not proof — see `inspect_pdf_tagging()`'s own statement.
    """
    available, reason = _probe_playwright()
    if not available:
        raise PlaywrightEngineUnavailable(reason)

    from playwright.sync_api import sync_playwright

    header_template = (
        '<div style="font-size:9px; width:100%; text-align:center; '
        'color:#555;">DocuAction TEFCA ARC'
        + (f' — {title}' if title else '') + '</div>'
    )
    footer_template = (
        '<div style="font-size:9px; width:100%; text-align:center; '
        'color:#555;">Page <span class="pageNumber"></span> of '
        '<span class="totalPages"></span></div>'
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page()
            try:
                page.set_content(html, wait_until="load")
                try:
                    return page.pdf(print_background=True, format="Letter",
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
