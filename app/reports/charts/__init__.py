"""
Chart definitions — what each figure shows, and how it describes itself.

Every chart is BAR-STYLE (or a line chart for trends). No pie charts anywhere:
a pie encodes quantity as angle, which is the hardest encoding to read
accurately, cannot be labelled reliably at small slice sizes, and degrades to
unreadable in greyscale. Bars are directly comparable and survive both.

Each chart carries its alt text, source line and notes line as REQUIRED fields
of ChartData. A chart cannot be constructed without them, and the render engine
refuses one that arrives with them empty — which is what stops "we'll add alt
text later" from becoming a shipped accessibility defect.

Alt text is written to convey THE SAME KEY FINDING as the visual, not to
describe the picture. "Bar chart with four bars" tells a screen-reader user
nothing; "B1 no-discrepancy accounts for 28 of 41 entities" tells them what a
sighted reader takes from the figure at a glance.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.reports.data.report_data_service import (
    BUCKET_CODES,
    BUCKET_LABELS,
    ChartData,
    ChartSeries,
    is_insufficient,
)

#: Hard cap. Beyond five series a grouped or stacked bar becomes a colour-matching
#: puzzle rather than a comparison, and no palette stays distinguishable in
#: greyscale past five.
MAX_SERIES = 5

SOURCE_LINE = (
    "Source: DocuAction TEFCA ARC verification pipeline, via the Report Data "
    "Service. Values are read from frozen verification results."
)


def _insufficient(chart_id: str, figure_number: int, title: str, kind: str,
                  y_label: str = "Entities") -> ChartData:
    """A chart with no data to draw.

    Rendered as an explicit "Insufficient data for this reporting period"
    panel — never as an empty axis and never as a row of zeroes, both of which
    read as a measured result of zero rather than as an absence of measurement.
    """
    return ChartData(
        chart_id=chart_id, figure_number=figure_number, title=title, kind=kind,
        categories=[], series=[], y_label=y_label,
        alt_text=(f"{title}: insufficient data for this reporting period. No "
                  f"records were available to chart."),
        source=SOURCE_LINE,
        notes=("No data was available for this reporting period, so no values are "
               "shown. This is an absence of measurement, not a measured zero."),
        insufficient_data=True,
    )


def b1_b4_distribution_chart(buckets: Dict[str, Any], figure_number: int = 1) -> ChartData:
    """Figure 1 — B1-B4 distribution. VERTICAL BAR, never a pie."""
    if buckets.get("insufficient_data"):
        return _insufficient("b1_b4_distribution", figure_number,
                             "B1-B4 Discrepancy Classification", "bar_vertical")

    counts = buckets["counts"]
    total = buckets["total_classified"]
    top = max(BUCKET_CODES, key=lambda c: counts[c])
    return ChartData(
        chart_id="b1_b4_distribution",
        figure_number=figure_number,
        title="B1-B4 Discrepancy Classification",
        kind="bar_vertical",
        categories=[f"{c} {BUCKET_LABELS[c]}" for c in BUCKET_CODES],
        series=[ChartSeries(
            label="Entities",
            values=[counts[c] for c in BUCKET_CODES],
            token="--report-primary",
        )],
        alt_text=(
            "Vertical bar chart of discrepancy classification across "
            f"{total} classified entities. "
            + "; ".join(f"{c} {BUCKET_LABELS[c]}: {counts[c]}" for c in BUCKET_CODES)
            + f". The largest group is {top} {BUCKET_LABELS[top]} with {counts[top]} "
              f"entities."
        ),
        source=SOURCE_LINE,
        notes=("Each entity is counted once, under its final classification — an "
               "analyst reclassification supersedes the automated bucket. "
               "Classification follows the approved B1-B4 methodology and the "
               "rule version recorded on each review."),
    )


def verification_coverage_chart(coverage: Dict[str, Any], figure_number: int = 2) -> ChartData:
    """Figure 2 — coverage by source. STACKED BAR across the five states."""
    if coverage.get("insufficient_data"):
        return _insufficient("verification_coverage", figure_number,
                             "Verification Coverage by Source", "bar_stacked",
                             y_label="Checks")

    sources = coverage["sources"]
    states = coverage["states"][:MAX_SERIES]
    tokens = ["--report-success", "--report-warning", "--report-muted",
              "--report-border", "--report-error"]

    series = [
        ChartSeries(label=state.replace("_", " "),
                    values=[s["counts"].get(state, 0) for s in sources],
                    token=tokens[i % len(tokens)])
        for i, state in enumerate(states)
    ]
    verified_total = sum(s["counts"].get("verified", 0) for s in sources)
    checks_total = sum(s["total"] for s in sources)
    return ChartData(
        chart_id="verification_coverage",
        figure_number=figure_number,
        title="Verification Coverage by Source",
        kind="bar_stacked",
        categories=[s["source"] for s in sources],
        series=series,
        y_label="Checks",
        alt_text=(
            f"Stacked bar chart of {checks_total} verification checks across "
            f"{len(sources)} sources, split by outcome state. "
            + "; ".join(
                f"{s['source']}: {s['counts'].get('verified', 0)} verified of {s['total']}"
                for s in sources)
            + f". Across all sources {verified_total} checks returned verified."
        ),
        source=SOURCE_LINE,
        notes=("Five verification states are shown separately. 'unavailable' means "
               "the source could not be reached and never counts against an entity; "
               "'not found' means the source answered and held no record. Collapsing "
               "the two would report a third party's outage as a finding."),
    )


def evidence_dimensions_chart(dimensions: Dict[str, Any], figure_number: int = 3) -> ChartData:
    """Figure 3 — dimension results. HORIZONTAL BAR (long category labels)."""
    if dimensions.get("insufficient_data"):
        return _insufficient("evidence_dimensions", figure_number,
                             "Evidence Dimension Results", "bar_horizontal")

    rows = dimensions["dimensions"]
    return ChartData(
        chart_id="evidence_dimensions",
        figure_number=figure_number,
        title="Evidence Dimension Results",
        kind="bar_horizontal",
        categories=[r["label"] for r in rows],
        series=[
            ChartSeries(label="Satisfied",
                        values=[r["satisfied"] for r in rows],
                        token="--report-success"),
            ChartSeries(label="Applicable, not satisfied",
                        values=[max(r["applicable"] - r["satisfied"], 0) for r in rows],
                        token="--report-warning"),
            ChartSeries(label="Not applicable",
                        values=[r["not_applicable"] for r in rows],
                        token="--report-muted"),
        ],
        alt_text=(
            "Horizontal bar chart of the six evidence dimensions, each split into "
            "satisfied, applicable-but-not-satisfied, and not-applicable. "
            + "; ".join(
                f"{r['label']}: {r['satisfied']} satisfied of {r['applicable']} applicable"
                for r in rows)
            + "."
        ),
        source=SOURCE_LINE,
        notes=("Not-applicable is shown as its own segment and is excluded from the "
               "satisfied rate. A dimension that does not apply to an entity is not "
               "a dimension that entity failed."),
    )


def entity_status_chart(statuses: Dict[str, Any], figure_number: int = 4) -> ChartData:
    """Figure 4 — entity status. HORIZONTAL BAR."""
    if statuses.get("insufficient_data"):
        return _insufficient("entity_status", figure_number,
                             "Entity Verification Status", "bar_horizontal")

    ordered = sorted(statuses["counts"].items(), key=lambda kv: -kv[1])
    return ChartData(
        chart_id="entity_status",
        figure_number=figure_number,
        title="Entity Verification Status",
        kind="bar_horizontal",
        categories=[k.replace("_", " ") for k, _ in ordered],
        series=[ChartSeries(label="Entities", values=[v for _, v in ordered],
                            token="--report-primary")],
        alt_text=(
            f"Horizontal bar chart of verification status across {statuses['total']} "
            "registry entities. "
            + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in ordered) + "."
        ),
        source=SOURCE_LINE,
        notes=("Status is the entity's current registry state. An entity may be "
               "counted here without having been sampled in this review cycle."),
    )


def qhin_comparison_chart(qhins: Dict[str, Any], figure_number: int = 5) -> ChartData:
    """Figure 5 — QHIN comparison. GROUPED BAR, and only when there is more
    than one QHIN to compare. A comparison chart of one thing is not a
    comparison."""
    if qhins.get("insufficient_data") or not qhins.get("comparison_meaningful"):
        return _insufficient("qhin_comparison", figure_number,
                             "Review Outcomes by QHIN", "bar_vertical")

    rows = qhins["qhins"]
    statuses: List[str] = []
    for row in rows:
        for status in row["counts"]:
            if status not in statuses:
                statuses.append(status)
    statuses = statuses[:MAX_SERIES]
    tokens = ["--report-success", "--report-warning", "--report-error",
              "--report-primary", "--report-muted"]
    return ChartData(
        chart_id="qhin_comparison",
        figure_number=figure_number,
        title="Review Outcomes by QHIN",
        kind="bar_vertical",
        categories=[r["qhin"] for r in rows],
        series=[
            ChartSeries(label=status.replace("_", " "),
                        values=[r["counts"].get(status, 0) for r in rows],
                        token=tokens[i % len(tokens)])
            for i, status in enumerate(statuses)
        ],
        alt_text=(
            f"Grouped vertical bar chart comparing review outcomes across "
            f"{len(rows)} QHINs. "
            + "; ".join(f"{r['qhin']}: {r['total']} reviews" for r in rows) + "."
        ),
        source=SOURCE_LINE,
        notes=("QHIN attribution comes from the review record. Entities with no QHIN "
               "attribution are grouped as Unattributed rather than distributed."),
    )


def build_all_charts(buckets, coverage, dimensions, statuses, qhins) -> List[ChartData]:
    """Every figure for the verification report, numbered in reading order."""
    charts = [
        b1_b4_distribution_chart(buckets, 1),
        verification_coverage_chart(coverage, 2),
        evidence_dimensions_chart(dimensions, 3),
        entity_status_chart(statuses, 4),
    ]
    if qhins.get("comparison_meaningful"):
        charts.append(qhin_comparison_chart(qhins, 5))
    for chart in charts:
        if len(chart.series) > MAX_SERIES:
            raise ValueError(
                f"{chart.chart_id} declares {len(chart.series)} series; the "
                f"maximum is {MAX_SERIES}. Beyond that a reader is matching "
                f"colours rather than comparing values.")
    return charts
