"""
Chart rendering — matplotlib, coloured exclusively from the USWDS tokens.

THE COLOURS ARE PARSED OUT OF THE STYLESHEET, NOT RETYPED HERE
──────────────────────────────────────────────────────────────
`uswds_report.css` is the single source of truth for every colour in a report,
including chart series. This module reads the `--report-*` variables out of that
file at import time rather than declaring its own copies.

A second hard-coded list of hex values is the obvious shortcut and it is exactly
the failure this avoids: the day someone remediates a contrast problem in the
CSS, the charts would keep rendering the old value and the report would be
half-fixed, with the mismatch visible only to whoever compared a bar to its
legend swatch.

If a token the charts need is missing from the CSS, this module raises at import
rather than substituting a default. A silently substituted colour is an
unreviewed design decision.

NO PIE CHARTS. The renderer implements vertical bar, horizontal bar, stacked
bar and line. `render()` raises on any other kind, so a pie cannot be introduced
by passing a string.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from typing import Dict, List

import matplotlib

# Non-interactive backend. Report generation runs in a web worker with no
# display; the default backend would try to find one and fail.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.font_manager import FontProperties, fontManager  # noqa: E402

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(_PACKAGE_ROOT, "styles", "uswds_report.css")
FONTS_DIR = os.path.join(_PACKAGE_ROOT, "fonts")

#: Tokens the chart layer needs. Absent from the CSS => ImportError, not a guess.
REQUIRED_TOKENS = (
    "--report-primary", "--report-primary-v", "--report-success",
    "--report-warning", "--report-error", "--report-text",
    "--report-muted", "--report-bg", "--report-bg-alt", "--report-border",
)

_TOKEN_RE = re.compile(r"(--report-[a-z-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;")

SUPPORTED_KINDS = ("bar_vertical", "bar_horizontal", "bar_stacked", "line")

MAX_SERIES = 5


def _load_tokens() -> Dict[str, str]:
    with open(CSS_PATH, "r", encoding="utf-8") as handle:
        css = handle.read()
    tokens = {name: value for name, value in _TOKEN_RE.findall(css)}
    missing = [t for t in REQUIRED_TOKENS if t not in tokens]
    if missing:
        raise ImportError(
            f"uswds_report.css is missing required colour tokens: {missing}. "
            f"Charts resolve every colour through the stylesheet; substituting a "
            f"default here would put an unreviewed colour into a federal report."
        )
    return tokens


TOKENS: Dict[str, str] = _load_tokens()


def token(name: str) -> str:
    """Hex for a `--report-*` token. Unknown names raise rather than default."""
    if name not in TOKENS:
        raise KeyError(
            f"{name!r} is not defined in uswds_report.css. Add it to the "
            f"stylesheet — chart colours are never declared in Python.")
    return TOKENS[name]


def _register_bundled_font() -> str:
    """Register bundled Public Sans with matplotlib and return its family name.

    Falls back to matplotlib's default family if the bundled file is absent,
    with a warning. A missing font is a cosmetic defect; refusing to render the
    chart over it would turn a cosmetic defect into a missing figure.
    """
    regular = os.path.join(FONTS_DIR, "PublicSans-Regular.woff2")
    for candidate in ("PublicSans-Regular.ttf", "PublicSans-Regular.otf"):
        path = os.path.join(FONTS_DIR, candidate)
        if os.path.exists(path):
            fontManager.addfont(path)
            return FontProperties(fname=path).get_name()
    if os.path.exists(regular):
        # matplotlib cannot consume woff2 directly. The HTML/PDF path uses the
        # woff2 through CSS; charts fall back to a metrically similar sans so the
        # two do not look jarringly different side by side.
        logger.info("Public Sans is bundled as woff2, which matplotlib cannot "
                    "load; charts use the DejaVu Sans fallback.")
    return "DejaVu Sans"


FONT_FAMILY = _register_bundled_font()


def _base_style() -> None:
    plt.rcParams.update({
        "font.family": FONT_FAMILY,
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "axes.edgecolor": token("--report-border"),
        "axes.labelcolor": token("--report-text"),
        "text.color": token("--report-text"),
        "xtick.color": token("--report-text"),
        "ytick.color": token("--report-text"),
        "figure.facecolor": token("--report-bg"),
        "axes.facecolor": token("--report-bg"),
        "savefig.facecolor": token("--report-bg"),
        "axes.grid": True,
        "grid.color": token("--report-border"),
        "grid.linewidth": 0.6,
        "axes.axisbelow": True,
    })


def render(chart) -> str:
    """A ChartData as a base64 PNG data URI.

    Embedded rather than written to a file so the HTML is self-contained: a
    report emailed as one file must not have figures that resolve to a path on
    the machine that produced it.
    """
    if chart.kind not in SUPPORTED_KINDS:
        raise ValueError(
            f"Unsupported chart kind {chart.kind!r}. Supported: {SUPPORTED_KINDS}. "
            f"Pie charts are deliberately absent — quantity encoded as angle is "
            f"the least accurately read encoding and does not survive greyscale.")
    if len(chart.series) > MAX_SERIES:
        raise ValueError(f"{chart.chart_id}: {len(chart.series)} series exceeds "
                         f"the maximum of {MAX_SERIES}.")
    if not (chart.alt_text or "").strip():
        raise ValueError(
            f"{chart.chart_id} has no alt text. A chart without a text "
            f"equivalent is unreadable to a screen-reader user and cannot be "
            f"rendered.")

    _base_style()
    figure, axes = plt.subplots(
        figsize=(7.0, 3.6 if chart.kind != "bar_horizontal"
                 else max(2.6, 0.52 * len(chart.categories) + 1.2)),
        dpi=160,
    )

    try:
        if chart.kind == "bar_vertical":
            _draw_vertical(axes, chart)
        elif chart.kind == "bar_horizontal":
            _draw_horizontal(axes, chart)
        elif chart.kind == "bar_stacked":
            _draw_stacked(axes, chart)
        else:
            _draw_line(axes, chart)

        axes.spines["top"].set_visible(False)
        axes.spines["right"].set_visible(False)
        if len(chart.series) > 1:
            axes.legend(frameon=False, fontsize=8, loc="best")

        buffer = io.BytesIO()
        figure.tight_layout()
        figure.savefig(buffer, format="png", bbox_inches="tight")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    finally:
        plt.close(figure)


def _draw_vertical(axes, chart) -> None:
    count = len(chart.categories)
    positions = range(count)
    width = 0.8 / max(len(chart.series), 1)
    for index, series in enumerate(chart.series):
        offsets = [p + index * width - 0.4 + width / 2 for p in positions]
        bars = axes.bar(offsets, series.values, width * 0.92,
                        label=series.label, color=token(series.token))
        # Value labels on the bars. A reader should not have to measure a bar
        # against a gridline to recover the number the chart is about.
        if len(chart.series) == 1:
            axes.bar_label(bars, fontsize=8, padding=2)
    axes.set_xticks(list(positions))
    axes.set_xticklabels(chart.categories, fontsize=8)
    axes.set_ylabel(chart.y_label)
    axes.grid(axis="x", visible=False)


def _draw_horizontal(axes, chart) -> None:
    count = len(chart.categories)
    positions = list(range(count))
    left = [0.0] * count
    for series in chart.series:
        bars = axes.barh(positions, series.values, 0.62, left=left,
                         label=series.label, color=token(series.token))
        if len(chart.series) == 1:
            axes.bar_label(bars, fontsize=8, padding=2)
        left = [l + v for l, v in zip(left, series.values)]
    axes.set_yticks(positions)
    axes.set_yticklabels(chart.categories, fontsize=8)
    axes.invert_yaxis()
    axes.set_xlabel(chart.y_label)
    axes.grid(axis="y", visible=False)


def _draw_stacked(axes, chart) -> None:
    count = len(chart.categories)
    positions = list(range(count))
    bottom = [0.0] * count
    for series in chart.series:
        axes.bar(positions, series.values, 0.62, bottom=bottom,
                 label=series.label, color=token(series.token))
        bottom = [b + v for b, v in zip(bottom, series.values)]
    axes.set_xticks(positions)
    axes.set_xticklabels(chart.categories, fontsize=8, rotation=0)
    axes.set_ylabel(chart.y_label)
    axes.grid(axis="x", visible=False)


def _draw_line(axes, chart) -> None:
    for series in chart.series:
        axes.plot(chart.categories, series.values, marker="o", linewidth=2,
                  label=series.label, color=token(series.token))
    axes.set_ylabel(chart.y_label)


def render_all(charts: List) -> Dict[str, str]:
    """{chart_id: data URI}. A chart with no data renders no image — the
    template shows an explicit 'Insufficient data' panel instead, which says
    something a blank axis does not."""
    rendered: Dict[str, str] = {}
    for chart in charts:
        if chart.insufficient_data:
            continue
        rendered[chart.chart_id] = render(chart)
    return rendered
