"""
DOCX rendering of a contract deliverable — the editable electronic copy.

Section E of the contract requires work products in hard copy and electronic
copy with "all text and diagrammatic files ... editable by the Government", and
ONC directed the D2 resubmission in Microsoft Word. This engine produces that
copy from the SAME frozen dataset the HTML and CSV are rendered from, so the
three cannot disagree.

ACCESSIBILITY FUNDAMENTALS APPLIED (Section 508 / accessible Word practice)
───────────────────────────────────────────────────────────────────────────
  * real styles, never manual formatting: Title, Heading 1/2/3, Normal,
    List Bullet — the heading outline is what a screen reader navigates;
  * every table has a marked header row that repeats across pages, and a
    caption paragraph before it;
  * document title, subject, author, keywords and language are set in core
    properties; the body language is en-US;
  * page numbers in the footer and the contract reference in the header;
  * a table of contents field the Government can update on open (Word asks);
  * no meaning carried by colour alone — categories are numbered and named.

Automated generation cannot certify Section 508 conformance; the document is
built to the checklist and states that a manual check remains due.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

DOCX_CONTENT_TYPE = ("application/vnd.openxmlformats-officedocument"
                     ".wordprocessingml.document")
DOCX_ENGINE_VERSION = "1.0.0"


def docx_available() -> bool:
    try:
        import docx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# ── low-level helpers ────────────────────────────────────────────────────────

def _set_repeat_header(row) -> None:
    """Mark a table row as a header row that repeats on every page."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tr_pr = row._tr.get_or_add_trPr()
    element = OxmlElement("w:tblHeader")
    element.set(qn("w:val"), "true")
    tr_pr.append(element)


def _add_field(paragraph, instruction: str, placeholder: str = "") -> None:
    """A Word field (PAGE, NUMPAGES, TOC ...) that Word evaluates on open."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar"); separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t"); text.text = placeholder
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    for element in (begin, instr, separate, text, end):
        run._r.append(element)


def _set_document_language(document, lang: str = "en-US") -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    styles_element = document.styles.element
    rpr_default = styles_element.find(qn("w:docDefaults"))
    if rpr_default is None:
        return
    rpr = rpr_default.find(qn("w:rPrDefault"))
    if rpr is None:
        return
    inner = rpr.find(qn("w:rPr"))
    if inner is None:
        inner = OxmlElement("w:rPr")
        rpr.append(inner)
    lang_el = inner.find(qn("w:lang"))
    if lang_el is None:
        lang_el = OxmlElement("w:lang")
        inner.append(lang_el)
    lang_el.set(qn("w:val"), lang)


def _table(document, caption: str, headers: List[str], rows: Iterable[List[Any]],
           *, empty_text: str) -> None:
    """An accessible table: caption paragraph, header row that repeats."""
    cap = document.add_paragraph(caption)
    cap.style = document.styles["Caption"] if "Caption" in [s.name for s in document.styles] else cap.style
    rows = list(rows)
    if not rows:
        document.add_paragraph(empty_text)
        return
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    header_cells = table.rows[0].cells
    for i, text in enumerate(headers):
        header_cells[i].text = ""
        run = header_cells[i].paragraphs[0].add_run(str(text))
        run.bold = True
    _set_repeat_header(table.rows[0])
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = "" if value is None else str(value)
    document.add_paragraph()


def _fmt_date(value: Any) -> str:
    if not value:
        return "Not specified"
    text = str(value)[:10]
    try:
        return datetime.fromisoformat(text).strftime("%B %d, %Y")
    except ValueError:
        return text


# ── the document ─────────────────────────────────────────────────────────────

def render_sow_docx(dataset: Dict[str, Any], snapshot: Dict[str, Any],
                    release: Dict[str, Any], branding: Dict[str, Any]) -> bytes:
    """Build the editable deliverable from the frozen dataset."""
    from docx import Document
    from docx.enum.section import WD_ORIENT  # noqa: F401  (documenting intent)
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.shared import Pt

    document = Document()
    _set_document_language(document, "en-US")

    report_id = snapshot.get("report_id") or "report"
    deliverable = dataset.get("deliverable") or ""
    title = dataset.get("deliverable_title") or "Contract Report"
    task = dataset.get("task") or ""
    contract = branding.get("contract_number") or dataset.get("contract_number") or ""
    classification = snapshot.get("data_classification") or "DEVELOPMENT_TEST"
    period_start = dataset.get("reporting_period_start")
    period_end = dataset.get("reporting_period_end")
    period_text = (f"{_fmt_date(period_start)} – {_fmt_date(period_end)}"
                   if (period_start or period_end) else "Not specified")
    status_label = release.get("label") or release.get("status") or "Draft"
    generated = snapshot.get("generation_timestamp") or datetime.now(timezone.utc).isoformat()

    # Core properties — what a reader's assistive technology announces first.
    core = document.core_properties
    core.title = f"{title} {report_id} — Contract {contract}"
    core.subject = f"{deliverable} {title}"
    core.author = branding.get("prepared_by") or "Alliance Global Tech Inc."
    core.keywords = f"{contract}; {task}; {deliverable}; TEFCA ARC; {report_id}"
    core.language = "en-US"
    core.comments = ("Generated by DocuAction from recorded, QA-approved review "
                     "data. Editable electronic copy of the deliverable.")

    # Header / footer: contract reference and page numbers on every page.
    section = document.sections[0]
    header = section.header.paragraphs[0]
    header.text = f"{branding.get('product_name', 'DocuAction')} · {title} · Contract {contract}"
    header.alignment = WD_ALIGN_PARAGRAPH.LEFT
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(f"{report_id} · Page ")
    _add_field(footer, "PAGE", "1")
    footer.add_run(" of ")
    _add_field(footer, "NUMPAGES", "1")

    body_style = document.styles["Normal"]
    body_style.font.name = "Calibri"
    body_style.font.size = Pt(11)

    # ── Cover ────────────────────────────────────────────────────────────────
    if classification != "GOVERNMENT":
        notice = document.add_paragraph()
        run = notice.add_run("DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY. "
                             "NOT ONC FINDINGS. Every figure in this document is a "
                             "validation result computed over development data.")
        run.bold = True
    document.add_paragraph(branding.get("product_name", "DocuAction").upper())
    document.add_paragraph(branding.get("program_name", "").upper())
    document.add_paragraph(task.upper())
    t = document.add_paragraph(title, style="Title")
    t.alignment = WD_ALIGN_PARAGRAPH.LEFT
    document.add_paragraph(f"Deliverable {deliverable}")
    document.add_paragraph(f"Reporting Period: {period_text}")
    document.add_paragraph(f"Contract: {contract}")
    document.add_paragraph("Prepared for:")
    for line in branding.get("prepared_for") or []:
        document.add_paragraph(line)
    if branding.get("government_branding_authorized"):
        document.add_paragraph("[Authorized Government mark placement — per the approval on file]")
    document.add_paragraph(f"Prepared by: {branding.get('prepared_by', 'Alliance Global Tech Inc.')}")
    document.add_paragraph(f"Version: {snapshot.get('template_version') or '1.0'}")
    document.add_paragraph(f"Date: {_fmt_date(generated)}")
    document.add_paragraph(f"Document Status: {status_label}")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ── Document control ─────────────────────────────────────────────────────
    document.add_heading("Document Control", level=1)
    _table(document, "Document control record",
           ["Item", "Value"], [
               ["Contract number", contract],
               ["Program", branding.get("program_name", "")],
               ["Task", task],
               ["Deliverable", f"{deliverable} — {title}"],
               ["Reporting period", period_text],
               ["Report ID", report_id],
               ["Version", snapshot.get("template_version") or "1.0"],
               ["Prepared by", snapshot.get("generated_by") or branding.get("prepared_by", "")],
               ["Reviewed by (PM)", release.get("updated_by") or "Awaiting PM review"],
               ["QA status of listed reviews", "Independent QA approved (only approved reviews are listed)"],
               ["PM release status", status_label],
               ["Generated (UTC)", generated],
               ["Source delivery reference",
                (snapshot.get("source_provenance") or {}).get("original_filename") or "Not recorded"],
               ["Methodology version", dataset.get("evidence_rule_version") or "Not recorded"],
               ["Rule set version", snapshot.get("b1_b4_rule_version") or "Not recorded"],
               ["Data classification", classification],
           ], empty_text="")

    document.add_heading("Contents", level=1)
    toc = document.add_paragraph()
    _add_field(toc, 'TOC \\o "1-3" \\h \\z \\u',
               "Right-click and choose Update Field to build the table of contents.")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ── Body ─────────────────────────────────────────────────────────────────
    labels = dataset.get("government_labels") or {}
    numbers = dataset.get("category_numbers") or {}
    categories = dataset.get("categories") or []
    lists = dataset.get("entity_lists") or {}
    counts = dataset.get("list_counts") or {}
    strat = dataset.get("stratification") or {}
    pending = dataset.get("pending_qa") or []
    columns = dataset.get("entity_columns") or []
    keys = [c["key"] for c in columns]
    heads = [c["label"] for c in columns]

    document.add_heading("1. Executive Summary", level=1)
    document.add_paragraph(
        f"This {title.lower()} covers {period_text} under contract {contract}. "
        f"{counts.get('listed_total', 0)} Participant/Subparticipant review(s) with a "
        f"standing independent QA approval are listed under the four contractual "
        f"discrepancy categories; {counts.get('pending_qa', 0)} review record(s) remain "
        f"pending QA and are reported separately as work in progress, not as findings.")
    for item in dataset.get("required_content") or []:
        document.add_paragraph(item, style="List Bullet")

    document.add_heading("2. Reporting Period and Scope", level=1)
    document.add_paragraph(f"Reporting period: {period_text}.")
    document.add_paragraph(
        f"Records considered: {strat.get('records_considered', 0)}. Evidence rule "
        f"version: {dataset.get('evidence_rule_version') or 'not recorded'}. "
        f"{strat.get('gate', '')}")

    document.add_heading("3. Review Activity", level=1)
    _table(document, "Review activity in the reporting period",
           ["Measure", "Value"], [
               ["Reviews listed (QA approved)", counts.get("listed_total", 0)],
               ["Reviews pending independent QA", counts.get("pending_qa", 0)],
               ["Records considered", strat.get("records_considered", 0)],
           ], empty_text="No activity recorded.")

    sampling = dataset.get("sampling") or {}
    document.add_heading("4. Population and Sampling Context", level=1)
    document.add_paragraph(f"Confidence: {sampling.get('confidence_floor', 'not recorded')}. "
                           f"Stratification: {sampling.get('stratification_requirement', 'not recorded')}.")
    document.add_paragraph(sampling.get("parameters_status", ""))
    _table(document, "Official sampling plans on record",
           ["Plan", "Type", "Population", "Sample", "Confidence", "Margin", "FPC", "Stratified by", "Drawn (UTC)"],
           [[p.get("name") or p.get("sample_id"), p.get("review_type"), p.get("population_size"),
             p.get("sample_size"), p.get("confidence_level"), p.get("margin_of_error"),
             "Yes" if p.get("use_fpc") else "No", p.get("stratify_by"), p.get("drawn_at")]
            for p in sampling.get("plans") or []],
           empty_text="No official sampling plan has been drawn.")

    document.add_heading("5. QHIN Stratification Summary", level=1)
    _table(document, "Participants and Subparticipants by Government discrepancy category",
           ["No.", "Category", "Listed in this report", "Pending QA (not findings)"],
           [[numbers.get(c), labels.get(c), counts.get(c, 0),
             (strat.get("pending_qa") or {}).get(c, 0)] for c in categories],
           empty_text="No categories recorded.")

    document.add_heading("6. Review Results", level=1)
    document.add_paragraph("Each category below lists the Participants and Subparticipants "
                           "with a standing independent QA approval. A list, not a count.")
    for i, category in enumerate(categories, start=1):
        document.add_heading(f"6.{i} {labels.get(category, category)}", level=2)
        rows = lists.get(category) or []
        _table(document, f"{labels.get(category, category)} — {len(rows)} Participant(s) / Subparticipant(s)",
               heads, [[r.get(k) or "—" for k in keys] for r in rows],
               empty_text="No Participant or Subparticipant is listed under this category for the reporting period.")

    document.add_heading("7. Significant Observations", level=1)
    limits = (dataset.get("source_limitations") or {}).get("sources_unavailable") or {}
    if limits:
        _table(document, "Sources that could not answer during evidence collection",
               ["Source", "Observations affected"], [[k, v] for k, v in limits.items()],
               empty_text="")
    else:
        document.add_paragraph("No source limitation was recorded in the reporting period.")
    document.add_paragraph((dataset.get("source_limitations") or {}).get("note", ""))

    changes = dataset.get("methodology_changes") or {}
    document.add_heading("8. Suggested Methodology / Control Framework Changes", level=1)
    if changes.get("suggested"):
        for item in changes["suggested"]:
            document.add_paragraph(item, style="List Bullet")
    else:
        document.add_paragraph("No suggested change was recorded for this reporting period.")
    if changes.get("includes_implemented"):
        document.add_heading("Implemented changes", level=2)
        if changes.get("implemented"):
            for item in changes["implemented"]:
                document.add_paragraph(item, style="List Bullet")
        else:
            document.add_paragraph("No implemented change was recorded for this reporting period.")
    document.add_paragraph(changes.get("basis", ""))

    document.add_heading("9. Issues, Dependencies and COR Decisions", level=1)
    document.add_paragraph((dataset.get("methodology_pending") or {}).get(
        "note", "Items awaiting a COR methodology decision are counted and disclosed."))
    if pending:
        _table(document, "Reviews pending independent QA (not findings)",
               ["Case", "Participant / Subparticipant", "QHIN", "System recommendation", "Why pending"],
               [[r.get("review_id"), r.get("entity_name"), r.get("qhin"),
                 f"{r.get('category_label')} ({r.get('rule')})", r.get("pending_reason")] for r in pending],
               empty_text="")

    document.add_heading("10. Supporting Traceability", level=1)
    _table(document, "Provenance record",
           ["Attribute", "Value"], [
               ["Report ID", report_id],
               ["Generated (UTC)", generated],
               ["Generated by", snapshot.get("generated_by")],
               ["Data payload hash (SHA-256)", snapshot.get("data_payload_hash")],
               ["Source delivery SHA-256", snapshot.get("rce_source_file_sha256") or "Not available"],
               ["Evidence rule version", dataset.get("evidence_rule_version")],
               ["Rule set version", snapshot.get("b1_b4_rule_version")],
               ["Template version", snapshot.get("template_version")],
               ["DOCX engine version", DOCX_ENGINE_VERSION],
           ], empty_text="")
    document.add_paragraph(
        "Every value in this report is read from frozen review records through the "
        "Report Data Service. No source lookup runs during report generation. "
        "Accessibility: built to the Section 508 authoring checklist (styles, heading "
        "outline, marked header rows, title, language, page numbers); a manual "
        "accessibility check remains due before any conformance claim is made.")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
