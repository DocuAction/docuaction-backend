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


#: Characters a spreadsheet may treat as the start of a formula (kept in step
#: with xlsx_engine.FORMULA_LEADERS). A delivered organisation name or a
#: reviewer's free-text reason that begins with one of these must not execute
#: when the CSV is opened in Excel (review findings F4/L1, 2026-09-16).
FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def neutralise_cell(value: Any) -> Any:
    """Prefix a formula-looking string with an apostrophe; leave other values."""
    if isinstance(value, str) and value.startswith(FORMULA_LEADERS):
        return "'" + value
    return value


def neutralise_row(row) -> List[Any]:
    return [neutralise_cell(v) for v in row]


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


def sow_report_to_csv(dataset: Dict[str, Any], report_id: str,
                      generated_at: str) -> str:
    """The stratified Participant/Subparticipant list of a SOW report, as data.

    One row per review record, with the Government category as text. The
    category number and wording are the contract's; the rule column is
    provenance. Pending records follow under their own header so the file can
    never be read as "everything below is a finding".
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([f"# DocuAction TEFCA ARC {dataset.get('deliverable_title', 'report')} {report_id}"])
    writer.writerow([f"# Contract number: {dataset.get('contract_number', '')}"])
    writer.writerow([f"# Deliverable: {dataset.get('deliverable', '')}"])
    writer.writerow([f"# Generated (UTC): {generated_at}"])
    writer.writerow([f"# Reporting period: {dataset.get('reporting_period_start') or '-'} "
                     f"to {dataset.get('reporting_period_end') or '-'}"])
    writer.writerow([f"# SOW data service version: {dataset.get('service_version')}"])
    writer.writerow(["# A row is listed under a category only on a standing "
                     "independent QA approval. Pending rows are not findings."])
    writer.writerow([])

    columns = dataset.get("entity_columns") or []
    keys = [c["key"] for c in columns]
    labels = [c["label"] for c in columns]
    writer.writerow(["## Stratified list of Participants and Subparticipants"])
    writer.writerow(["Category no.", *labels])
    listed = 0
    for category in dataset.get("categories") or []:
        number = (dataset.get("category_numbers") or {}).get(category, "")
        for row in (dataset.get("entity_lists") or {}).get(category, []):
            writer.writerow(neutralise_row([number, *[row.get(k, "") or "" for k in keys]]))
            listed += 1
    if not listed:
        writer.writerow(["No Participant or Subparticipant is listed for this reporting period"])
    writer.writerow([])

    writer.writerow(["## Pending independent QA (not findings)"])
    writer.writerow(["Case", "Participant / Subparticipant", "QHIN",
                     "System recommendation", "Rule", "Why pending"])
    pending = dataset.get("pending_qa") or []
    if not pending:
        writer.writerow(["No review record is pending QA"])
    for row in pending:
        writer.writerow(neutralise_row([row.get("review_id", ""), row.get("entity_name", ""),
                         row.get("qhin", ""), row.get("category_label", ""),
                         row.get("rule", ""), row.get("pending_reason", "")]))
    writer.writerow([])

    changes = dataset.get("methodology_changes") or {}
    writer.writerow(["## Suggested changes to the methodology / control framework"])
    for item in changes.get("suggested") or ["None recorded for this reporting period"]:
        writer.writerow([item])
    if changes.get("includes_implemented"):
        writer.writerow(["## Implemented changes to the methodology / control framework"])
        for item in changes.get("implemented") or ["None recorded for this reporting period"]:
            writer.writerow([item])
    return buffer.getvalue()


#: Record-level disposition columns, in order. The header row of the CSV and
#: the record table in the HTML are both driven from the dataset rows, so a
#: column cannot exist in one and not the other.
DELIVERY_DISPOSITION_COLUMNS = (
    ("line_number", "Line"),
    ("source_record_id", "Source record id"),
    ("source_rce_id", "Source id (RCE OID)"),
    ("name", "Name"),
    ("submitted_npi", "Submitted NPI"),
    ("curated_npi", "Curated NPI"),
    ("disposition", "Disposition"),
    ("reason_code", "Reason code"),
    ("entity_id", "Entity id"),
    ("finding_count", "Findings"),
    ("warning_count", "Warnings"),
    ("sequence", "Sequence"),
    ("actor_type", "Actor type"),
    ("actor", "Actor"),
    ("decided_at", "Decided (UTC)"),
    ("reconstructed", "Reconstructed"),
)

#: The accounting categories, in the order the equation states them.
DELIVERY_DISPOSITION_ORDER = ("CREATED", "UPDATED", "MATCHED_UNCHANGED", "HELD",
                              "REJECTED", "MISSING_KEY", "EXCLUDED")


def delivery_processing_to_csv(dataset: Dict[str, Any], report_id: str,
                               generated_at: str) -> str:
    """The Delivery Processing Report as data: EVERY record-level disposition.

    Not paged, not sampled. The preamble pins the same identity the HTML
    prints on page one (job, intake, snapshot id + hash, build, template), the
    totals block restates the equation, and then one row per received record
    follows. The totals and the rows come from the SAME dataset dict the HTML
    was rendered from, so the three cannot disagree.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    delivery = dataset.get("delivery") or {}
    build = dataset.get("build") or {}
    disp = dataset.get("dispositions") or {}
    recon = dataset.get("reconciliation") or {}

    writer.writerow([f"# DocuAction TEFCA ARC Delivery Processing Report {report_id}"])
    writer.writerow([f"# Generated (UTC): {generated_at}"])
    writer.writerow([f"# Job id: {delivery.get('job_id') or '-'}"])
    writer.writerow([f"# Intake id: {delivery.get('intake_id') or '-'}"])
    writer.writerow([f"# Delivery: {delivery.get('delivery_label') or '-'} / "
                     f"{delivery.get('filename') or '-'}"])
    writer.writerow([f"# Source file SHA-256: {delivery.get('sha256') or '-'}"])
    writer.writerow([f"# Reconciliation snapshot id: {dataset.get('snapshot_id') or 'none'}"])
    writer.writerow([f"# Reconciliation snapshot hash: {dataset.get('snapshot_hash') or '-'}"])
    writer.writerow([f"# Reconciliation snapshot created (UTC): "
                     f"{dataset.get('snapshot_created_at') or '-'}"])
    writer.writerow([f"# Build SHA: {build.get('git_sha') or 'unknown'}"])
    writer.writerow([f"# Migration revision: {build.get('migration_revision') or 'unknown'}"])
    writer.writerow([f"# Template version: {dataset.get('template_version')}"])
    writer.writerow([f"# Report Data Service version: {dataset.get('service_version')}"])
    writer.writerow(["# Every value below is read from persisted evidence. No rule is "
                     "re-run and no reconciliation is recomputed while this file is produced."])
    writer.writerow([])

    eq = (recon.get("equation") or {}) if recon.get("available") else {}
    writer.writerow(["## Reconciliation equation (pinned snapshot)"])
    writer.writerow(["Measure", "Value"])
    if eq:
        writer.writerow(["Received", eq.get("received", "")])
        for key in DELIVERY_DISPOSITION_ORDER:
            writer.writerow([key, eq.get(key.lower(), "")])
        writer.writerow(["Accounted", eq.get("accounted", "")])
        writer.writerow(["Equation holds", eq.get("holds", "")])
        writer.writerow(["Snapshot passed", recon.get("passed", "")])
    else:
        writer.writerow(["No reconciliation snapshot is persisted for this delivery", ""])
    writer.writerow([])

    counts = disp.get("counts") or {}
    writer.writerow(["## Disposition totals (current table)"])
    writer.writerow(["Disposition", "Records"])
    for key in DELIVERY_DISPOSITION_ORDER:
        writer.writerow([key, counts.get(key, 0)])
    writer.writerow(["Total", counts.get("total", 0)])
    writer.writerow(["Records received", disp.get("records_received", "")])
    writer.writerow(["Records without a disposition", disp.get("records_without_disposition", "")])
    writer.writerow([])

    writer.writerow(["## Record-level dispositions (all rows)"])
    writer.writerow([label for _, label in DELIVERY_DISPOSITION_COLUMNS])
    rows = disp.get("rows") or []
    if not rows:
        writer.writerow(["No disposition events are persisted for this delivery"])
    for row in rows:
        writer.writerow(neutralise_row(["" if row.get(key) is None else row.get(key)
                                        for key, _ in DELIVERY_DISPOSITION_COLUMNS]))
    writer.writerow([])

    writer.writerow(["## Evidence limitations"])
    for item in dataset.get("limitations") or ["None recorded"]:
        writer.writerow([item])
    return buffer.getvalue()


def to_bytes(csv_text: str) -> bytes:
    """UTF-8 with a BOM — see the module docstring on Excel."""
    return csv_text.encode("utf-8-sig")
