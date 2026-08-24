"""
Jinja2 + USWDS HTML rendering.

The CSS is INLINED into every rendered document rather than linked. A report is
frequently emailed, archived, or attached to a deliverable; a linked stylesheet
would resolve to a path on the machine that produced it and the document would
arrive unstyled — which for a status-indicator-bearing compliance report means
arriving unreadable.

Fonts are inlined the same way, as base64 data URIs read from the bundled woff2
files. That is what makes "no runtime download" literally true rather than
aspirational: the rendered HTML contains the font bytes and reaches for nothing.

AUTOESCAPE IS ON. Entity names come from a delivered file and land in HTML; the
RCE delivery is already known to contain test artefacts and encoding damage, and
an organisation name is exactly the kind of field that eventually contains a
bracket.
"""

from __future__ import annotations

import base64
import functools
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(_PACKAGE_ROOT, "templates")
STYLES_DIR = os.path.join(_PACKAGE_ROOT, "styles")
FONTS_DIR = os.path.join(_PACKAGE_ROOT, "fonts")

CSS_FILE = os.path.join(STYLES_DIR, "uswds_report.css")

#: Bumped when a template's structure changes. Recorded on every snapshot, so a
#: number can be traced to the layout that presented it.
TEMPLATE_VERSION = "1.0.0"

_FONT_FILES = {
    ("Public Sans", "normal", 400): "PublicSans-Regular.woff2",
    ("Public Sans", "normal", 700): "PublicSans-Bold.woff2",
    ("Public Sans", "italic", 400): "PublicSans-Italic.woff2",
}


@functools.lru_cache(maxsize=1)
def inlined_font_face_css() -> str:
    """@font-face rules with the woff2 bytes embedded as data URIs.

    Read from disk once and cached. A missing font file is logged and skipped
    rather than raised: the fallback stack still renders a legible document, and
    failing a compliance report over a typeface would be the wrong trade.
    """
    blocks: List[str] = []
    for (family, style, weight), filename in _FONT_FILES.items():
        path = os.path.join(FONTS_DIR, filename)
        if not os.path.exists(path):
            logger.warning("bundled font missing: %s — falling back to the "
                           "system sans stack for that weight.", filename)
            continue
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        blocks.append(
            f'@font-face{{font-family:"{family}";font-style:{style};'
            f'font-weight:{weight};'
            f'src:url("data:font/woff2;base64,{encoded}") format("woff2");}}'
        )
    return "\n".join(blocks)


@functools.lru_cache(maxsize=1)
def base_css() -> str:
    """The stylesheet with its @font-face block replaced by inlined fonts.

    The file's own @font-face rules use relative URLs, which are correct for a
    developer opening the CSS directly and useless in a self-contained document.
    They are stripped and replaced rather than left in place, so the rendered
    HTML contains exactly one definition per face.
    """
    with open(CSS_FILE, "r", encoding="utf-8") as handle:
        css = handle.read()
    start = css.find("@font-face")
    end = css.rfind("}", start, css.find("/* ── Semantic tokens"))
    if start != -1 and end != -1:
        css = css[:start] + css[end + 1:]
    return inlined_font_face_css() + "\n" + css


def _format_date(value: Any) -> str:
    if value is None:
        return "Not specified"
    if isinstance(value, str):
        return value
    try:
        return value.strftime("%d %B %Y")
    except (AttributeError, ValueError):
        return str(value)


def _format_number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


def _format_pct(value: Any) -> str:
    """A percentage, or the insufficient-data sentence.

    NEVER renders 0% for an empty population. "0% of applicable dimensions were
    satisfied" and "there were no applicable dimensions to evaluate" are
    different statements, and only one of them is true of an empty cycle.
    """
    from app.reports.data.report_data_service import INSUFFICIENT_DATA

    if value == INSUFFICIENT_DATA or value is None:
        return "Insufficient data"
    return f"{value}%"


@functools.lru_cache(maxsize=1)
def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]),
        # StrictUndefined: a template referencing a value the data service does
        # not provide is a BUG, and rendering it as an empty string would put a
        # silently blank figure in a federal report. Fail at render time.
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["date"] = _format_date
    env.filters["number"] = _format_number
    env.filters["pct"] = _format_pct
    return env


def render_html(template_name: str, context: Dict[str, Any]) -> str:
    """Render one report template to a self-contained HTML document."""
    from app.reports.data.report_data_service import INSUFFICIENT_DATA

    env = environment()
    template = env.get_template(template_name)
    full_context = {
        "css": base_css(),
        "generated_at": datetime.now(timezone.utc),
        "generated_at_display": datetime.now(timezone.utc).strftime(
            "%d %B %Y at %H:%M UTC"),
        "template_version": TEMPLATE_VERSION,
        "INSUFFICIENT_DATA": INSUFFICIENT_DATA,
        **context,
    }
    return template.render(**full_context)
