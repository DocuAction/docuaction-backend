"""The HTML `/api/reports/{id}/html` serves is the HTML the artifact registry
hashed, and the delivery_processing document carries its print-layout rules.

WHY (DEV, 2026-09-24/25, DA-ARC-2026-028)
------------------------------------------
The body served from `review_reports.report_html` (29,174,189 bytes) did not
hash to `report_artifacts.rendered_sha256`, which was registered over
29,173,539 bytes: a 650-byte difference for what should be one rendering.

In the code path there is exactly one rendering: `generate_report` renders
`html` once after the snapshot exists, `store_report` writes that string to
`review_reports.report_html`, `finalize_artifact` hashes `html.encode("utf-8")`
and stores the same bytes, and the /html route returns the column with UTF-8
encoding; nothing writes the column afterwards (the release route touches only
`report_data`). The first test proves that equality end to end through the
route function. If the DEV discrepancy recurs, the difference is therefore
outside this path (transport, or a row that was not produced by this code),
and this SQL tells which::

    -- If the column hashes to the registered digest, the served body was
    -- altered in transit; if not, the row and the registry hold different
    -- renderings.
    SELECT r.report_id,
           octet_length(r.report_html)                              AS column_bytes,
           encode(sha256(convert_to(r.report_html, 'UTF8')), 'hex') AS column_sha256,
           a.size_bytes, a.rendered_sha256, a.artifact_version
      FROM review_reports r
      JOIN report_artifacts a ON a.report_id = r.report_id
                             AND a.content_type = 'text/html'
     WHERE r.report_id = 'DA-ARC-2026-028';

Database-backed tests use the same rolled-back session and synthetic delivery
as test_delivery_processing_report.py; the fixtures are imported from there.
"""
from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    artifact_root, rolled_back_db, seed_delivery)

USER = SimpleNamespace(email="qa@synthetic.invalid", id=None)

# Page one is the approved executive layout (cards, navy tables, boxed panels), portrait; every
# detailed table lives in ONE landscape appendix after it.
MAIN_SECTIONS = ("Status &mdash; four separate questions", "Reconciliation &mdash; every record accounted for",
                 "Exceptions (findings) by rule", "Screening coverage &mdash; what was actually attempted",
                 "Delivery at a glance", "Reconciliation &amp; source integrity",
                 "Source readiness (preflight) &amp; rechecks", "Evidence limitations", "Required next actions")
APPENDIX_SECTIONS = ("Appendix A. Delivery Identity and provenance", "Appendix B. Processing timeline",
                     "Appendix C. Reconciliation detail", "Appendix D. Dispositions", "Appendix E. Findings",
                     "Appendix F. Identifier conflicts and decisions", "Appendix G. Verification coverage detail",
                     "Appendix H. Analyst actions", "Appendix I. Lineage", "Appendix J. Audit note")


async def _persisted_delivery_report(db):
    from app.reports.generator import generate_report

    ids = await seed_delivery(db)
    result = await generate_report(db, report_type="delivery_processing", persist=True,
                                   query_parameters={"job_id": str(ids["job_id"])},
                                   generated_by=USER.email)
    assert result["stored_id"], "the report must be stored for /html to serve it"
    return result


@pytest.mark.asyncio
async def test_html_route_body_hashes_to_the_registered_artifact(rolled_back_db, artifact_root):
    from app.reports import routes
    from app.reports.data.artifact_registry import retrieve_artifact

    db = rolled_back_db
    result = await _persisted_delivery_report(db)
    artifact = result["artifact"]
    assert artifact and artifact.get("registered") is not False, (
        "no HTML artifact was registered; the equality cannot be checked")
    assert artifact["content_type"] == "text/html"

    response = await routes.get_report_html(result["report_id"], job_id=None,
                                            db=db, user=USER)
    body = response.body

    assert hashlib.sha256(body).hexdigest() == artifact["rendered_sha256"]
    assert len(body) == artifact["size_bytes"]
    # The same bytes three ways: the generator's string, the stored column as
    # served, and what the registry hands back after re-hashing.
    assert body == result["html"].encode("utf-8")
    stored = await retrieve_artifact(db, result["report_id"], content_type="text/html")
    assert stored["verified"] and stored["content"] == body


@pytest.mark.asyncio
async def test_delivery_processing_html_carries_the_landscape_layout(rolled_back_db, artifact_root):
    """The stored document (the one the PDF is rendered from) contains the
    named landscape page and the wide-table wrapper, so a PDF rendered from it
    later inherits the layout without any re-render."""
    db = rolled_back_db
    result = await _persisted_delivery_report(db)
    html = result["html"]

    assert "@page dp-landscape" in html
    assert "size: letter landscape" in html
    assert html.count('<div class="dp-wide">') == 1
    assert '<div class="dp-wide">\n<h2>Appendix A. Delivery Identity and provenance</h2>' in html
    for heading in APPENDIX_SECTIONS:
        assert f">{heading}</h2>" in html, heading
    for heading in MAIN_SECTIONS:
        assert f'<h2 class="agt-h2">{heading}</h2>' in html, heading
    # Page one stays portrait: every executive section comes BEFORE the one landscape appendix.
    wide_at = html.index('<div class="dp-wide">')
    for heading in MAIN_SECTIONS:
        assert html.index(f'<h2 class="agt-h2">{heading}</h2>') < wide_at, heading
    for heading in APPENDIX_SECTIONS:
        assert html.index(f">{heading}</h2>") > wide_at, heading
    # the approved executive grammar is present on page one
    for needle in ('class="agt-head"', 'class="agt-kpis"', 'class="agt-strip"', 'class="agt-cols"',
                   'class="agt-pkg"', 'class="agt-foot"', "table class=\"agt\""):
        assert needle in html, needle
    assert html.count('class="agt-kpi"') == 5
    # The DEV/TEST running notice is on the landscape page as well as the
    # portrait one (two @top-center boxes in the conditional style block).
    assert html.count('NOT FOR GOVERNMENT DELIVERY";') == 2
    assert result["accessibility"]["automated_checks_passed"], \
        result["accessibility"]["errors"]


class TestLayoutRulesWithoutADatabase:
    """The rules live in the one stylesheet every report inlines."""

    def test_stylesheet_defines_the_landscape_page_and_table_rules(self):
        from app.reports.engine.template_engine import base_css

        css = base_css()
        assert "@page dp-landscape" in css and "size: letter landscape" in css
        assert ".dp-wide {" in css and "page: dp-landscape" in css
        assert "table-layout: fixed" in css
        assert "thead { display: table-header-group; }" in css
        assert "tfoot { display: table-footer-group; }" in css
        assert "overflow-wrap: anywhere" in css and "hyphens: auto" in css
        # the landscape page carries the same running header/footer boxes
        landscape = css[css.index("@page dp-landscape"):]
        for box in ("@top-left", "@top-right", "@bottom-center"):
            assert box in landscape, box

    def test_template_wraps_exactly_the_wide_sections(self):
        from app.reports.engine.template_engine import TEMPLATES_DIR

        path = os.path.join(TEMPLATES_DIR, "delivery_processing.html")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        assert source.count('<div class="dp-wide">') == 1
        # Overall div balance (a basic HTML-sanity check, not pinned to any one
        # section's count) -- the template also carries a cover/KPI band
        # (dp-band/agt-*) above the wide sections, with its own divs.
        import re
        assert len(re.findall(r"<div\b", source)) == source.count("</div>")
        # The appendix block is self-contained (no nested <div>), so the non-greedy match below
        # finds exactly its own close.
        assert len(re.findall(r'<div class="dp-wide">.*?</div>', source, re.S)) == 1
        wide = source.index('<div class="dp-wide">')
        assert wide < source.index("<h2>Appendix D. Dispositions</h2>")
        # every Program Manager section sits before the appendix, so it stays portrait
        for heading in ("Evidence limitations", "Required next actions", "Screening coverage"):
            assert source.index(f'<h2 class="agt-h2">{heading}') < wide, heading


@pytest.mark.asyncio
async def test_html_route_serves_the_registered_bytes_even_when_the_column_drifts(
        rolled_back_db, artifact_root):
    """QA108-20260927-013 — the canonical-surface rule, pinned.

    The REGISTERED artifact is the deliverable: it was hashed at finalisation
    and is re-verified on read. If `review_reports.report_html` later drifts
    (whatever wrote it), the route must keep serving the bytes the recipient
    can verify against the registry — never the drifted column."""
    from sqlalchemy import select

    from app.reports import routes
    from app.tefca_registry import models as reg

    db = rolled_back_db
    result = await _persisted_delivery_report(db)
    artifact = result["artifact"]

    row = (await db.execute(select(reg.ReviewReport).where(
        reg.ReviewReport.report_id == result["report_id"]))).scalar_one()
    row.report_html = row.report_html + "<!-- synthetic drift -->"
    await db.flush()

    response = await routes.get_report_html(result["report_id"], job_id=None,
                                            db=db, user=USER)
    assert hashlib.sha256(response.body).hexdigest() == artifact["rendered_sha256"], (
        "/html served the drifted column instead of the registered artifact")
    assert response.headers.get("X-Report-Source") == "artifact-registry"


@pytest.mark.asyncio
async def test_html_route_still_serves_a_legacy_report_with_no_artifact(
        rolled_back_db, artifact_root):
    """A report from before artifact registration has only the column; the
    route serves it exactly as before, and says so."""
    import uuid as _uuid

    from app.reports import routes
    from app.tefca_registry import models as reg

    db = rolled_back_db
    legacy = reg.ReviewReport(
        id=_uuid.uuid4(), report_id="DA-SYN-LEGACY-001", report_type="weekly",
        report_data={"dataset": {}}, report_html="<html><body>legacy</body></html>")
    db.add(legacy)
    await db.flush()

    response = await routes.get_report_html("DA-SYN-LEGACY-001", job_id=None,
                                            db=db, user=USER)
    assert response.body == b"<html><body>legacy</body></html>"
    assert response.headers.get("X-Report-Source") == "stored-column"


@pytest.mark.asyncio
async def test_pdf_route_serves_the_registered_pdf_without_rendering(
        rolled_back_db, artifact_root, monkeypatch):
    """QA108-20260927-004 — a registered PDF is SERVED, never re-rendered.

    The render engine is nailed shut for the duration: if the route still
    tried to render, it would blow up instead of answering."""
    from app.reports import routes
    from app.reports.data.artifact_registry import finalize_artifact

    db = rolled_back_db
    result = await _persisted_delivery_report(db)

    pdf_bytes = b"%PDF-1.4 synthetic registered rendering"
    registered = await finalize_artifact(
        db, report_id=result["report_id"], report_type="delivery_processing",
        content=pdf_bytes, content_type="application/pdf",
        review_cycle_id="SYNTHETIC-CYCLE", generated_by=USER.email)
    assert registered["rendered_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()

    async def _explode(*a, **k):  # pragma: no cover - reached only on regression
        raise AssertionError("the PDF route rendered despite a registered artifact")

    monkeypatch.setattr(routes, "_pdf_response", _explode)

    response = await routes.get_report_pdf(result["report_id"], job_id=None,
                                           db=db, user=USER)
    assert response.body == pdf_bytes
    assert response.media_type == "application/pdf"
    assert response.headers.get("X-Report-Source") == "artifact-registry"
