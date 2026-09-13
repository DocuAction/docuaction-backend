"""A generated deliverable carries exactly one document-level <h1> (AUD-20260913-14).

The cover page owns the h1; the running header repeats the title as a paragraph. Reports
rendered without a cover keep their h1 in the header (base.html fallback branch; exercised by
the template's own `{% else %}` path, not by a separate test here).
"""
import pathlib
import re

import pytest

from test_sow_report_generation import FakeDB, populated_sow  # noqa: F401  (synthetic rows, no database)

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "reports" / "templates"


@pytest.mark.asyncio
async def test_sow_report_has_exactly_one_h1(populated_sow):  # noqa: F811
    from app.reports.generator import generate_report

    r = await generate_report(FakeDB(), report_type="retrospective_weekly", persist=False,
                              query_parameters={"period_start": "2026-09-07", "period_end": "2026-09-13"})
    html = r["html"]
    h1s = re.findall(r"<h1\b[^>]*>(.*?)</h1>", html, flags=re.S)
    assert len(h1s) == 1, h1s
    assert 'id="cover-heading"' in html
    assert re.search(r'<p class="report-title"[^>]*>', html), "running header must still repeat the title"

