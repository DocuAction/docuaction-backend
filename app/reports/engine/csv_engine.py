"""
Paired CSV export — the numbers behind every figure, as data.

WHY EVERY FIGURE GETS A CSV
A chart is a picture of numbers. A reader who needs to check a figure, re-analyse
it, or read it with assistive technology needs the numbers themselves, and
retyping them off a bar chart is neither reasonable nor reliable. The CSV is
generated from the SAME ChartData object the image is rendered from, so the two
cannot drift: there is no second query and no second rounding step.

Rendered with CRLF line endings and a UTF-8 BOM. Excel is where these files are
actually opened, and without the BOM it reads UTF-8 as the local ANSI codepage —
which would turn the very mojibake this system detects into mojibake of its own
making.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List


def chart_to_rows(chart) -> List[List[Any]]:
    """One chart as a header row plus one row per category."""
    header = ["Category"] + [s.label for s in chart.series]
    rows = [header]
    for index, category in enumerate(chart.categories):
        row: List[Any] = [category]
        for series in chart.series:
            row.append(series.values[index] if index < len(series.values) else "")
        rows.append(row)
    return rows


def chart_to_csv(chart) -> str:
    """A single figure's data, with its provenance in the preamble.

    The title, source and notes ride along as comment lines. A CSV that escapes
    into a shared drive without them is a column of numbers nobody can date or
    attribute, which is how a figure ends up quoted out of context.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([f"# {chart.numbered_title}"])
    writer.writerow([f"# {chart.source}"])
    writer.writerow([f"# Notes: {chart.notes}"])
    if chart.insufficient_data:
        writer.writerow(["# Insufficient data for this reporting period."])
        return buffer.getvalue()
    for row in chart_to_rows(chart):
        writer.writerow(row)
    return buffer.getvalue()


def report_to_csv(dataset: Dict[str, Any], report_id: str,
                  generated_at: str) -> str:
    """Every figure in one report, as one CSV with a provenance header.

    A single file rather than a zip of many: a reviewer asking "where did this
    number come from" should open one thing.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")

    writer.writerow([f"# DocuAction TEFCA ARC report {report_id}"])
    writer.writerow([f"# Generated (UTC): {generated_at}"])
    writer.writerow([f"# Report Data Service version: {dataset.get('service_version')}"])
    writer.writerow([f"# Review cycle: {dataset.get('review_cycle_id') or 'All records'}"])
    writer.writerow(["# Every value below is read from frozen verification "
                     "results. No live lookup runs during report generation."])
    writer.writerow([])

    scope = dataset.get("scope") or {}
    writer.writerow(["## Scope at a Glance"])
    writer.writerow(["Measure", "Value"])
    for label, key in (
        ("Records received", "records_received"),
        ("Records evaluated", "records_evaluated"),
        ("QHINs", "qhin_count"),
        ("Issues identified", "issues_identified"),
        ("Records held", "records_held"),
        ("B3/B4 escalations", "escalations"),
    ):
        writer.writerow([label, scope.get(key, "")])
    writer.writerow([])

    for chart in dataset.get("chart_list") or []:
        writer.writerow([f"## {chart.numbered_title}"])
        writer.writerow([f"# {chart.source}"])
        writer.writerow([f"# Notes: {chart.notes}"])
        if chart.insufficient_data:
            writer.writerow(["Insufficient data for this reporting period"])
        else:
            for row in chart_to_rows(chart):
                writer.writerow(row)
        writer.writerow([])

    exceptions = (dataset.get("exceptions") or {}).get("exceptions") or []
    writer.writerow(["## Exceptions (B3 / B4)"])
    writer.writerow(["Review ID", "Bucket", "Classification", "Rule",
                     "Rule version", "Analyst resolution"])
    if not exceptions:
        writer.writerow(["No B3 or B4 exceptions in this reporting period"])
    for item in exceptions:
        writer.writerow([
            item.get("review_id", ""), item.get("bucket", ""),
            item.get("bucket_label", ""), item.get("rule", ""),
            item.get("rule_version", ""), item.get("resolution", "") or "Unresolved",
        ])
    return buffer.getvalue()


def to_bytes(csv_text: str) -> bytes:
    """UTF-8 with a BOM — see the module docstring on Excel."""
    return csv_text.encode("utf-8-sig")
