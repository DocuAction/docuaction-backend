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

WIDE_SECTIONS = ("4. Dispositions", "5. Findings", "6. Identifier conflicts and decisions",
                 "7. Verification coverage", "8. Analyst actions", "9. Lineage")


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
    assert html.count('<div class="dp-wide">') == 6
    for heading in WIDE_SECTIONS:
        assert f'<div class="dp-wide">\n<h2>{heading}</h2>' in html, heading
    # Narrative sections stay portrait: the wrapper closes before section 10.
    assert "</div>\n<h2>10. Evidence limitations</h2>" in html
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
        assert source.count('<div class="dp-wide">') == 6
        assert source.count("</div>") == 6
        assert source.index('<div class="dp-wide">') < source.index("<h2>4. Dispositions</h2>")
        assert source.rindex("</div>") < source.index("<h2>10. Evidence limitations</h2>")
        assert source.index("<h2>3. Reconciliation</h2>") < source.index('<div class="dp-wide">')
