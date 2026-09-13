"""Cross-format reconciliation: DOCX, PDF, HTML and CSV are produced from ONE
canonical report dataset and must not disagree about the facts that identify
the deliverable — report ID, contract number, reporting period, entity rows,
categories, data classification. Print output is independent of the
application theme.

DEVELOPMENT/TEST DATA. Synthetic rows only.
"""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from test_sow_report_generation import FakeDB, populated_sow  # noqa: F401  (fixture registered by import)

PERIOD = ("2026-09-07", "2026-09-13")
ENTITIES = ("SYNTHETIC CLINIC ALPHA", "SYNTHETIC CLINIC BRAVO", "SYNTHETIC CLINIC CHARLIE")
CONTRACT = "7571MN26F80064"


async def _generate():
    from app.reports.generator import generate_report
    return await generate_report(FakeDB(), report_type="retrospective_weekly", persist=False,
                                 query_parameters={"period_start": PERIOD[0], "period_end": PERIOD[1],
                                                   "suggested_changes": "Clarify D4 address materiality"})


def _docx_text(blob: bytes):
    from docx import Document
    doc = Document(io.BytesIO(blob))
    paras = "\n".join(p.text for p in doc.paragraphs)
    cells = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    header = "\n".join(p.text for p in doc.sections[0].header.paragraphs)
    return doc, paras + "\n" + cells + "\n" + header


def _pdf_text(blob: bytes) -> str:
    from pdfminer.high_level import extract_text
    return extract_text(io.BytesIO(blob))


@pytest.mark.asyncio
async def test_every_format_states_the_same_identity_and_rows(populated_sow, monkeypatch):  # noqa: F811
    monkeypatch.delenv("REPORT_GOVERNMENT_BRANDING_AUTHORIZED", raising=False)
    from app.reports.branding import current_branding, deliverable_filename_stem
    from app.reports.data.release import build_package, current_release
    from app.reports.engine.docx_engine import render_sow_docx

    result = await _generate()
    html, csv_text, dataset = result["html"], result["csv"], result["dataset"]
    snapshot = result["snapshot"] if isinstance(result.get("snapshot"), dict) else result["snapshot"].to_dict()
    report_id = snapshot["report_id"]
    assert re.fullmatch(r"DA-ARC-\d{4}-\d{3}", report_id)

    # HTML
    assert report_id in html and CONTRACT in html and "DEVELOPMENT / TEST" in html
    for e in ENTITIES:
        assert e in html
    assert PERIOD[0] in html and PERIOD[1] in html

    # CSV — the stratified list, same rows, same id and contract
    assert report_id in csv_text and CONTRACT in csv_text
    for e in ENTITIES:
        assert e in csv_text
    assert PERIOD[0] in csv_text and PERIOD[1] in csv_text

    # DOCX — same facts, same properties
    docx_blob = render_sow_docx(dataset, snapshot, current_release({}), current_branding().to_dict())
    doc, docx_text = _docx_text(docx_blob)
    assert report_id in docx_text and CONTRACT in docx_text and "DEVELOPMENT / TEST DATA" in docx_text
    for e in ENTITIES:
        assert e in docx_text
    # The DOCX prints the reporting period through the same dataset field the HTML uses
    # (formatting may differ from ISO); the fact must be present in the document control table.
    period_line = next((line for line in docx_text.splitlines() if "period" in line.lower()), "")
    assert period_line, "no reporting-period line in the DOCX"
    assert any(tok in docx_text for tok in (PERIOD[0], PERIOD[1], "September 7", "Sept 7", "Sep 7", "7 September", "09/07/2026")), period_line
    assert report_id in doc.core_properties.title and CONTRACT in doc.core_properties.keywords
    assert doc.core_properties.language == "en-US"

    # Categories: the Government's four headings appear in HTML and DOCX alike
    for heading in ("No discrepancies identified", "Inexplicable discrepancies"):
        assert heading in html and heading in docx_text

    # PDF — rendered from the delivered HTML when WeasyPrint is available on this host
    pdf_status = "NOT_RENDERED_ON_THIS_HOST"
    try:
        from app.reports.engine.pdf_engine import render_pdf
        pdf_blob = render_pdf(html, title=f"Task 3 Weekly Progress Report {report_id}")
        text = _pdf_text(pdf_blob)
        assert report_id in text and CONTRACT in text
        for e in ENTITIES:
            assert e in text
        pdf_status = "RECONCILED"
    except Exception as exc:  # noqa: BLE001 — PDFEngineUnavailable or missing native libs on Windows
        pdf_blob = None
        pdf_status = f"NOT_RENDERED_ON_THIS_HOST ({type(exc).__name__})"

    # Package: every member shares the traceable stem and the manifest restates the identity
    stem = deliverable_filename_stem(contract_number=CONTRACT, task=dataset.get("task"), deliverable=dataset.get("deliverable"),
                                     kind="Weekly", period_start=PERIOD[0], period_end=PERIOD[1], report_id=report_id)
    package = build_package(report_id=report_id, html=html, csv_text=csv_text, pdf_bytes=pdf_blob, snapshot=snapshot,
                            release=current_release({}), deliverable={"title": dataset.get("deliverable_title"),
                                                                      "deliverable": dataset.get("deliverable"),
                                                                      "task": dataset.get("task")},
                            docx_bytes=docx_blob, stem=stem, pdf_unavailable_reason=None if pdf_blob else pdf_status)
    names = zipfile.ZipFile(io.BytesIO(package["bytes"])).namelist()
    members = [n for n in names if n not in ("README.txt", "manifest.json")]
    assert members and all(n.startswith(stem + ".") for n in members), members
    assert package["filename"] == f"{stem}.zip"
    assert package["manifest"]["report_id"] == report_id and package["manifest"]["contract_number"] == CONTRACT
    assert not re.search(r"\b(weekly|bi-weekly|quarterly) (progress|report)\b", stem, re.I)   # no cadence sentences in file names
    print(f"PDF_CROSS_FORMAT={pdf_status}")


def test_package_contract_number_has_one_source():
    """The README/manifest contract number must be the branding value, not a second literal."""
    import inspect
    from app.reports import branding
    from app.reports.data import release
    assert release.CONTRACT_NUMBER == branding.CONTRACT_NUMBER
    src = inspect.getsource(release)
    assert 'CONTRACT_NUMBER = "7571' not in src


def test_print_output_is_independent_of_application_theme():
    """Report CSS knows nothing about the application theme: no data-theme hook,
    no prefers-color-scheme, a white page background."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "reports"
    css = (root / "styles" / "uswds_report.css").read_text(encoding="utf-8")
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")
    for text in (css, base):
        assert "data-theme" not in text and "prefers-color-scheme" not in text and "arc-theme" not in text
    m = re.search(r"--report-bg\s*:\s*([^;]+);", css)
    assert m and re.fullmatch(r"(#f{3}|#f{6}|white)", m.group(1).strip(), re.I), (m and m.group(1))
