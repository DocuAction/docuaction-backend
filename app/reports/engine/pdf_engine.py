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
        return document.write_pdf(pdf_variant=variant)
    except (TypeError, ValueError) as exc:
        # An older WeasyPrint may not know this variant name. Falling back to an
        # untagged PDF is acceptable ONLY because it is reported loudly — an
        # untagged PDF must never be presented as an accessible one.
        logger.warning(
            "WeasyPrint rejected pdf_variant=%r (%s). Emitting an UNTAGGED PDF. "
            "This document must NOT be described as accessible until it is "
            "regenerated with a version that supports the variant.", variant, exc)
        return document.write_pdf()


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
