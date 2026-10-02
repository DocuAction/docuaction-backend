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


def inspect_pdf_tagging(pdf_bytes: bytes) -> Dict[str, Any]:
    """Ground truth, not an assumption: does this PDF have a structure tree?

    Checks the document catalog for `/MarkInfo` and `/StructTreeRoot` — the
    two things a real screen reader / PDF-UA validator looks for. Returns
    both flags plus an explicit, unambiguous statement of what this does and
    does not prove.
    """
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    catalog = reader.trailer["/Root"]
    has_mark_info = "/MarkInfo" in catalog
    marked = bool(catalog.get("/MarkInfo", {}).get("/Marked", False)) if has_mark_info else False
    has_struct_tree = "/StructTreeRoot" in catalog
    return {
        "has_mark_info": has_mark_info,
        "marked_true": marked,
        "has_struct_tree_root": has_struct_tree,
        "is_tagged_pdf": has_mark_info and marked and has_struct_tree,
        "statement": (
            "This checks ONLY for the presence of a structure tree (PDF/UA's "
            "precondition). It does NOT check tag correctness, reading-order "
            "fidelity, alt-text on images, color contrast, or any other "
            "Section 508/PDF-UA requirement. A PDF with is_tagged_pdf=True is "
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
