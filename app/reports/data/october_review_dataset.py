"""One record-level reporting dataset; Excel, CSV and PDF are renderings of it.

WHAT THIS IS
    A pure builder: normalised per-record FACTS in, one dataset dict out. No
    database access and no writes. Loading facts from the application's tables
    is a separate step (not part of this module).

RULES IT ENFORCES (and the records that carry them)
    * HELD is counted ONCE, inside B4 (OCTOBER-OWNER-APPROVAL-2026-11-10-11,
      reporting methodology). The record keeps Status = HELD and its exact
      blocking reason; nothing is released or relabelled. The deployed
      classifier still excludes HELD upstream - this is REPORTING only.
    * Totals are RECOUNTED from the rows and never forced to match any
      spreadsheet or expected figure; the caller compares them to its own
      expectation (the internal sum check is only a guard).
    * Duplicate AGT_Record_IDs are refused, not silently merged.
    * Field findings (e.g. 1,336) are counted separately and never added to a
      bucket total.
    * Entity_Type is shown only when an original-submitted-NPI identity link is
      ESTABLISHED (entity_type_basis == SUBMITTED_NPI_MATCH, a non-blank
      submitted NPI, and the NPI the source returned equals it); otherwise
      "Not established". A blank submitted NPI can never establish it.
    * USPS reads "Not checked—unavailable" unless a real USPS result is supplied.
    * A supplemental NPI is shown in its own column and never fills the
      submitted NPI.
    * The two QA-comment columns are carried verbatim and kept separate. The
      second column is named for its author and is populated ONLY with an
      attributable source (file/sheet/column + hash) - comment text without one
      is refused, never attached. Where no comment store exists they stay empty
      (storage is an open owner decision). A comment is never a determination.
    * A non-HELD record with no usable persisted classification is NEVER given
      a bucket: it is shown as NOT CLASSIFIED, the dataset is flagged
      INCOMPLETE, and the affected AGT_Record_IDs and count are listed. Excel
      analysis is not a persisted classification and is not substituted.
    * Persisted classifier decisions and approved reporting annotations stay
      distinguishable: "Bucket Basis" begins "Persisted classification" or
      "Reporting annotation" and never mixes the two.
    * B2 stays labelled provisional when the fact says so.
Nothing here is QA approval. Every output is labelled DRAFT.
"""
from __future__ import annotations

import csv
import html
import io
from typing import Any, Dict, Iterable, List, Optional

DATASET_VERSION = "1.0.0"
BUCKETS = ("B1", "B2", "B3", "B4")
STATUS_HELD = "HELD"
USPS_UNAVAILABLE = "Not checked—unavailable"
ENTITY_TYPE_NOT_ESTABLISHED = "Not established"
ENTITY_TYPE_BASIS_OK = "SUBMITTED_NPI_MATCH"
NOT_CLASSIFIED = "NOT CLASSIFIED"
NOT_AT_RECORD_GRAIN = ("Not established at record grain; see the separate "
                       "field-findings report")
NOT_ESTABLISHED = "Not established from retained evidence."
FINDINGS_NOT_INCLUDED = "Not included in this export (separate report)"
IZAAN_COMMENTS_COLUMN = "QA Comments - Izaan (verbatim)"
ORIGINAL_COMMENTS_COLUMN = "QA Comments (verbatim)"
DRAFT_BANNER = ("CURRENT REVIEW / DRAFT / NOT FINAL - INDEPENDENT QA PENDING. "
                "Automated checks are not QA approval.")
#: Wording removed by the 2026-11-11 report corrections; refused in PECOS cells.
PROHIBITED_PECOS_PHRASES = ("lost-identifier/outage family",)

RECORD_COLUMNS = [
    "Source_Line", "AGT_Record_ID", "QHIN Number", "HCID", "Tefca Id",
    "Organization", "Participation Level",
    "Entity_Type (NPPES Entity Type Code)", "Activity Status", "Purpose_of_Use",
    "Submitted NPI (original, verbatim)",
    "Supplemental NPI (manual research - NOT an accepted correction)",
    "Bucket", "Status", "Bucket Basis", "Flag",
    "NPPES", "PECOS/CMS Enrollment", "OIG LEIE", "SAM.gov Exclusions",
    "USPS Address Check",
    "Submitted Address", "Proposed Corrected Address (AI-Assisted)",
    "Reasons", "Issue Type", "Issue Severity", "Recommendation",
    ORIGINAL_COMMENTS_COLUMN, IZAAN_COMMENTS_COLUMN,
    "Report Clarifications", "Evidence Reference",
]
TEXT_COLUMNS = list(range(len(RECORD_COLUMNS)))   # identifiers stay text
#: Compact subset for the PDF; the full column set is in the Excel/CSV.
PDF_COLUMNS = ["Source_Line", "AGT_Record_ID", "QHIN Number", "Organization",
               "Bucket", "Status", "NPPES", "PECOS/CMS Enrollment", "OIG LEIE",
               "SAM.gov Exclusions", "USPS Address Check", "Reasons"]


class DatasetRefused(ValueError):
    """The dataset could not be built honestly, and the reason is stated."""


def reporting_bucket(bucket: Optional[str], status: Optional[str]) -> str:
    """HELD -> B4 (once). Otherwise the record's own classified bucket."""
    if (status or "").strip().upper() == STATUS_HELD:
        return "B4"
    if bucket not in BUCKETS:
        # No usable persisted classification: never guess one. The dataset
        # reports itself INCOMPLETE and lists the record (see build_dataset).
        return NOT_CLASSIFIED
    return bucket


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _basis(fact: Dict[str, Any], status: str) -> str:
    """Persisted decision and reporting annotation are never blended."""
    if status.upper() == STATUS_HELD:
        reason = _s(fact.get("held_reason")) or "reason not recorded"
        return ("Reporting annotation: HELD counted once in B4; no persisted "
                f"classification (blocked assessment: {reason})")
    base = _s(fact.get("bucket_basis"))
    if fact.get("bucket") not in BUCKETS:
        return ("No usable persisted classification" +
                (f" ({base})" if base else ""))
    note = _s(fact.get("bucket_annotation"))
    if fact.get("provisional") and not note:
        note = "provisional"
    out = base or "Persisted classification"
    return out + (f"; Reporting annotation: {note}" if note else "")


def _entity_type(fact: Dict[str, Any]) -> str:
    """Shown only with an ESTABLISHED original-submitted-NPI identity link."""
    submitted = _s(fact.get("submitted_npi")).strip()
    if (fact.get("entity_type") and submitted
            and fact.get("entity_type_basis") == ENTITY_TYPE_BASIS_OK):
        return _s(fact.get("entity_type"))
    return ENTITY_TYPE_NOT_ESTABLISHED


def _comment_source_label(src: Any) -> str:
    """A non-blank attributable source for comment text, or ''."""
    if isinstance(src, dict):
        parts = [f"{k}={src[k]}" for k in ("file", "sha256", "sheet", "column")
                 if src.get(k)]
        return "; ".join(parts) if len(parts) >= 2 else ""
    return _s(src).strip()


def build_record_row(fact: Dict[str, Any]) -> List[str]:
    status = _s(fact.get("status")).strip()
    bucket = reporting_bucket(fact.get("bucket"), status)
    pecos = _s(fact.get("pecos"))
    for phrase in PROHIBITED_PECOS_PHRASES:
        if phrase in pecos.lower():
            raise DatasetRefused(
                f"PECOS text for {fact.get('agt_record_id')} uses retired "
                f"blanket wording {phrase!r}; supply the cause-specific text")
    # The second comment column is populated ONLY from an attributable source.
    izaan = _s(fact.get("qa_comments_izaan"))
    if izaan and not _comment_source_label(fact.get("qa_comments_izaan_source")):
        raise DatasetRefused(
            f"{IZAAN_COMMENTS_COLUMN} text for {fact.get('agt_record_id')} has "
            f"no attributable source (file + sha256/sheet/column); refusing to "
            f"attach unattributed comment text")
    flag = _s(fact.get("flag"))
    if fact.get("provisional") and "PROVISIONAL" not in flag.upper():
        flag = (flag + "; " if flag else "") + "PROVISIONAL B2"
    return [
        _s(fact.get("source_line")), _s(fact.get("agt_record_id")),
        _s(fact.get("qhin_number")), _s(fact.get("hcid")),
        _s(fact.get("tefca_id")), _s(fact.get("organization")),
        _s(fact.get("participation_level")), _entity_type(fact),
        _s(fact.get("activity_status")), _s(fact.get("purpose_of_use")),
        _s(fact.get("submitted_npi")), _s(fact.get("supplemental_npi")),
        bucket, status, _basis(fact, status), flag,
        _s(fact.get("nppes")), pecos, _s(fact.get("leie")),
        _s(fact.get("sam")), _s(fact.get("usps")) or USPS_UNAVAILABLE,
        _s(fact.get("submitted_address")), _s(fact.get("proposed_address")),
        _s(fact.get("reasons")),
        _s(fact.get("issue_type")) or NOT_AT_RECORD_GRAIN,
        _s(fact.get("issue_severity")) or NOT_AT_RECORD_GRAIN,
        _s(fact.get("recommendation")) or NOT_AT_RECORD_GRAIN,
        _s(fact.get("qa_comments")), izaan,
        _s(fact.get("report_clarifications")),
        _s(fact.get("evidence_reference")) or NOT_ESTABLISHED,
    ]


def build_dataset(facts: Iterable[Dict[str, Any]], *, report_id: str,
                  findings_count: Optional[int] = 0, assessment_date: str = "",
                  classification: str = "DRAFT",
                  load_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # None = the separate field-findings report is NOT part of this export; it
    # is then stated as such, never shown as a misleading 0.
    if findings_count is not None and int(findings_count) < 0:
        raise DatasetRefused("findings_count cannot be negative")
    facts = list(facts)
    ids = [_s(f.get("agt_record_id")) for f in facts]
    if any(not i for i in ids):
        raise DatasetRefused("a record has no AGT_Record_ID")
    if len(set(ids)) != len(ids):
        raise DatasetRefused("duplicate AGT_Record_ID in input; refusing to merge")
    facts.sort(key=lambda f: (int(f["source_line"]) if str(f.get("source_line", "")).isdigit() else 0, f["agt_record_id"]))
    rows = [build_record_row(f) for f in facts]
    b_idx, s_idx = RECORD_COLUMNS.index("Bucket"), RECORD_COLUMNS.index("Status")
    id_idx, ln_idx = RECORD_COLUMNS.index("AGT_Record_ID"), RECORD_COLUMNS.index("Source_Line")
    counts = {b: 0 for b in BUCKETS}
    held = 0
    held_no_reason = 0
    unclassified: List[List[str]] = []
    for r in rows:
        if r[b_idx] in counts:
            counts[r[b_idx]] += 1
        else:                               # NOT CLASSIFIED: never given a bucket
            unclassified.append([r[id_idx], r[ln_idx], r[s_idx]])
        if r[s_idx].upper() == STATUS_HELD:
            held += 1
            if "reason not recorded" in r[b_idx + 2]:
                held_no_reason += 1
    if sum(counts.values()) + len(unclassified) != len(rows):   # guard
        raise DatasetRefused("bucket counts do not sum to the record count")
    incomplete = bool(unclassified)
    banner = DRAFT_BANNER
    if incomplete:
        banner += (f" DATASET INCOMPLETE: {len(unclassified)} record(s) have no "
                   "usable persisted classification; no bucket was assigned to "
                   "them (listed on the Unclassified sheet).")
    comment_sources: Dict[str, List[str]] = {"original": [], "izaan": []}
    for f in facts:
        for kind, key in (("original", "qa_comments_source"),
                          ("izaan", "qa_comments_izaan_source")):
            lab = _comment_source_label(f.get(key))
            if lab and lab not in comment_sources[kind]:
                comment_sources[kind].append(lab)
    summary = {
        "unique_records": len(rows), "buckets": counts,
        "held_inside_b4": held, "held_without_recorded_reason": held_no_reason,
        "field_findings": None if findings_count is None else int(findings_count),
        "findings_counted_in_buckets": False,
        "assessment_date": assessment_date,
        "complete": not incomplete,
        "unclassified_non_held_count": len(unclassified),
        "unclassified_non_held_ids": [u[0] for u in unclassified],
        "comment_sources": comment_sources,
    }
    summary_rows = ([[b, str(counts[b])] for b in BUCKETS]
                    + [["Total (unique records)", str(len(rows))],
                       ["of which HELD (counted once, inside B4)", str(held)],
                       ["Not classified (no usable persisted classification; "
                        "no bucket assigned)", str(len(unclassified))],
                       ["Dataset complete", "No" if incomplete else "Yes"],
                       ["Field findings (separate; not in bucket totals)",
                        FINDINGS_NOT_INCLUDED if findings_count is None
                        else str(int(findings_count))],
                       ["Assessment date (D-12)", assessment_date]])
    sheets: Dict[str, Any] = {
        "Summary": {"columns": ["Measure", "Value"], "rows": summary_rows,
                    "note": banner},
        "Records": {"columns": list(RECORD_COLUMNS), "rows": rows,
                    "text_columns": TEXT_COLUMNS, "note": banner},
    }
    order = ["Summary", "Records"]
    if incomplete:
        sheets["Unclassified"] = {
            "columns": ["AGT_Record_ID", "Source_Line", "Status"],
            "rows": unclassified, "text_columns": [0, 1, 2], "note": banner}
        order.append("Unclassified")
    return {
        "report_id": report_id, "workbook_version": DATASET_VERSION,
        "classification": classification, "banner": banner,
        "summary": summary, "sheet_order": order, "sheets": sheets,
        "load_meta": load_meta or {},
    }


# -- renderings of the SAME dataset ----------------------------------------

def to_xlsx(dataset: Dict[str, Any]) -> bytes:
    from app.reports.engine.xlsx_engine import render_workbook
    return render_workbook(dataset)


def to_csv(dataset: Dict[str, Any]) -> bytes:
    from app.reports.engine.csv_engine import neutralise_row, to_bytes
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([dataset["banner"] + " Buckets: " + ", ".join(
        f"{b} {dataset['summary']['buckets'][b]}" for b in BUCKETS)
        + f" = {dataset['summary']['unique_records']} "
        f"({dataset['summary']['held_inside_b4']} HELD inside B4). "
        "Field findings counted separately: "
        + (FINDINGS_NOT_INCLUDED.lower() if dataset['summary']['field_findings'] is None
           else str(dataset['summary']['field_findings'])) + ". "
        "Skip this first line on import."])
    rec = dataset["sheets"]["Records"]
    w.writerow(rec["columns"])
    for r in rec["rows"]:
        w.writerow(neutralise_row(r))
    return to_bytes(buf.getvalue())


def to_html(dataset: Dict[str, Any]) -> str:
    e = html.escape
    cols = PDF_COLUMNS
    rec = dataset["sheets"]["Records"]
    idx = [rec["columns"].index(c) for c in cols]
    srows = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td></tr>"
                    for a, b in dataset["sheets"]["Summary"]["rows"])
    head = "".join(f"<th>{e(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{e(r[i])}</td>" for i in idx) + "</tr>"
                   for r in rec["rows"])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>{e(dataset['report_id'])} - record review (DRAFT)</title>"
            f"<style>@page{{size:landscape}}body{{font:9pt sans-serif}}"
            f"td,th{{border:1px solid #ccc;padding:2px;text-align:left}}"
            f"</style></head><body><h1>Record review - DRAFT</h1>"
            f"<p>{e(dataset['banner'])}</p><h2>Summary</h2><table><tr><th>Measure</th>"
            f"<th>Value</th></tr>{srows}</table><h2>Records</h2>"
            f"<p>Compact view; the full column set is in the Excel/CSV from the "
            f"same dataset.</p><table><tr>{head}</tr>{body}</table></body></html>")


def to_pdf(dataset: Dict[str, Any]) -> bytes:
    from app.reports.engine.pdf_engine import render_pdf
    return render_pdf(to_html(dataset), title=f"{dataset['report_id']} DRAFT")
