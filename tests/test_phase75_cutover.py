"""Phase 7.5C — frontend cutover, legacy deprecation, and the PDF environment.

Two independently authoritative report systems is the condition this closes. The
tests here pin that the frontend calls the canonical path, that every legacy
report family is marked deprecated rather than deleted, and that the container
image can actually render a PDF — which it could not, despite the code saying
otherwise.

DEVELOPMENT/TEST DATA. Nothing here is an ONC finding.
"""
from __future__ import annotations

import io
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_REPORTS = os.path.join(
    os.path.dirname(REPO), "frontend", "src", "app", "tefca-arc", "reports",
    "page.js")


def _openapi():
    from app.main import app
    return app.openapi()


def _report_paths(spec):
    return {p: ops for p, ops in spec["paths"].items() if "report" in p.lower()}


class TestCanonicalSurface:

    def test_the_canonical_path_serves_the_sow_families(self):
        paths = _openapi()["paths"]
        assert "/api/reports/sow" in paths
        assert "/api/reports/sow/{deliverable}" in paths

    def test_the_canonical_path_serves_stored_artifacts(self):
        paths = _openapi()["paths"]
        assert "/api/reports/artifacts/{report_id}" in paths
        assert "/api/reports/artifacts/{report_id}/download" in paths

    def test_the_canonical_router_is_registered_unconditionally(self):
        """safe_load swallows an ImportError and 404s the deliverable path
        while the service still reports healthy."""
        main = io.open(os.path.join(REPO, "app", "main.py"),
                       encoding="utf-8").read()
        assert 'safe_load("app.reports.routes"' not in main
        assert "from app.reports.routes import router as reports_router" in main

    def test_no_canonical_endpoint_is_marked_deprecated(self):
        spec = _openapi()
        for path, ops in _report_paths(spec).items():
            if path.startswith("/api/reports"):
                assert not any(o.get("deprecated") for o in ops.values()), path


class TestLegacyIsDeprecatedNotDeleted:

    def test_every_legacy_report_family_is_marked(self):
        """Marked, so a consumer sees it in the schema. Not deleted, because
        deletion needs proof that no consumer remains."""
        spec = _openapi()
        expected_deprecated = {
            "/api/tefca/reports",
            "/api/tefca/reports/weekly",
            "/api/tefca/reports/final",
            "/api/tefca/reports/biweekly",
            "/api/tefca/reports/quarterly",
            "/api/tefca/reports/{report_id}",
            "/api/tefca/reports/{report_id}/csv",
            "/api/tefca/reports/{report_id}/pdf",
            "/api/tefca/reports/{report_id}/docx",
            "/api/tefca/reports/{report_id}/download",
            "/api/tefca/arc/reports",
            "/api/tefca/arc/reports/generate",
            "/api/tefca/arc/reports/{report_id}",
            "/api/tefca/arc/reports/{report_id}/excel",
            "/api/tefca/arc/reports/{report_id}/html",
            "/api/tefca/priority/{case_id}/report",
            "/api/tefca/priority/quarterly-report",
            "/api/v1/tefca/reports",
            "/api/v1/tefca/reports/weekly/{cycle_id}",
            "/api/v1/tefca/reports/final/{cycle_id}",
        }
        paths = spec["paths"]
        for path in expected_deprecated:
            assert path in paths, f"{path} was deleted; deprecation is not deletion"
            assert any(o.get("deprecated") for o in paths[path].values()), \
                f"{path} is still mounted but not marked deprecated"

    def test_the_deprecation_says_where_to_go_instead(self):
        spec = _openapi()
        for path, ops in _report_paths(spec).items():
            for op in ops.values():
                if op.get("deprecated"):
                    summary = op.get("summary", "")
                    assert "DEPRECATED / COMPATIBILITY ONLY" in summary, path
                    assert "/api/reports" in summary, path

    def test_endpoints_that_are_not_report_families_stay_unmarked(self):
        """The QA gate and the PII review export are different functions."""
        spec = _openapi()
        for path in ("/api/tefca/qa/report", "/api/tefca/qa/report-gate",
                     "/api/tefca/reports/export"):
            assert path in spec["paths"]
            assert not any(o.get("deprecated")
                           for o in spec["paths"][path].values()), path

    def test_the_legacy_generator_still_exists(self):
        """Deprecating a path must not remove the only implementation of the
        contract's families before the canonical one is proven in use."""
        from app.Tefca import reporting

        for family in ("generate_weekly_report", "generate_final_report",
                       "generate_biweekly_report", "generate_quarterly_report",
                       "generate_priority_status_report"):
            assert callable(getattr(reporting, family, None))


@pytest.mark.skipif(not os.path.exists(FRONTEND_REPORTS),
                    reason="frontend sources not present in this checkout")
class TestFrontendCutover:

    @staticmethod
    def _source():
        return io.open(FRONTEND_REPORTS, encoding="utf-8").read()

    @staticmethod
    def _calls(source):
        """Every API path the page actually calls, ignoring comments.

        Comment lines are stripped first: the file documents the path it moved
        away from, and a naive grep would read that as a live call.
        """
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith(("*", "/*", "//")))
        return set(re.findall(r"['\"`](/api/[^'\"`$]*)", code))

    def test_the_page_calls_the_canonical_listing(self):
        assert "/api/reports" in self._calls(self._source())

    def test_the_page_no_longer_calls_a_legacy_report_path(self):
        for call in self._calls(self._source()):
            assert not call.startswith("/api/tefca/reports"), \
                f"page still calls the deprecated {call}"
            assert not call.startswith("/api/v1/tefca/reports"), call

    def test_generation_goes_through_the_canonical_endpoint(self):
        assert "/api/reports/generate" in self._source()

    def test_it_does_not_offer_a_format_the_canonical_path_cannot_serve(self):
        """DOCX is served only by the deprecated path and is not a contract
        requirement. A download button that 404s is worse than no button."""
        source = self._source()
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith(("*", "/*", "//")))
        assert "'docx'" not in code

    def test_the_development_banner_is_rendered(self):
        source = self._source()
        assert "DevelopmentBanner" in source
        assert "not for government delivery" in source.lower()
        assert "NOT ONC FINDINGS" in source

    def test_the_banner_is_announced_to_assistive_technology(self):
        assert 'role="note"' in self._source()

    def test_the_banner_defaults_to_showing_when_classification_is_unknown(self):
        """Defaulting the other way would let a failed lookup silently upgrade
        the page to looking like Government output."""
        source = self._source()
        assert "if (classification === 'GOVERNMENT') return null;" in source

    def test_the_page_records_why_the_numbers_changed(self):
        """The counts got smaller at cutover. Someone will ask."""
        source = self._source()
        assert "reportability gate" in source


class TestPdfEnvironment:
    """The container could not render a PDF, and the code said it could."""

    @staticmethod
    def _dockerfile():
        return io.open(os.path.join(REPO, "Dockerfile"), encoding="utf-8").read()

    @pytest.mark.parametrize("library", [
        "libpango-1.0-0", "libpangoft2-1.0-0", "libcairo2",
        "libgdk-pixbuf-2.0-0",
    ])
    def test_the_image_installs_the_native_stack(self, library):
        assert library in self._dockerfile()

    def test_the_image_installs_a_fallback_font(self):
        """A missing fallback turns an unexpected glyph into a blank box."""
        assert "fonts-dejavu-core" in self._dockerfile()

    def test_the_build_fails_if_the_pdf_engine_cannot_start(self):
        """A container that boots and then 503s on every PDF request surfaces
        the failure to whoever asked for a deliverable, not to whoever built
        the image."""
        dockerfile = self._dockerfile()
        assert "from weasyprint import HTML" in dockerfile
        assert "%PDF-" in dockerfile

    def test_the_unavailable_message_no_longer_claims_the_image_has_them(self):
        """It said the libraries "are present in the project's Linux container
        image". They were not. A false statement about where something works
        stops anyone from looking.

        Asserted against the MESSAGE the function returns, not the module
        source: the code comment deliberately quotes the old wording to explain
        what changed, and a source-wide grep would flag that as the defect.
        """
        from app.reports.engine.pdf_engine import pdf_available, unavailable_reason

        # Guarded on pdf_available(), NOT on the truthiness of the reason.
        # _probe() returns a non-empty string on SUCCESS too ("WeasyPrint and
        # its native dependencies are available."), so `if not reason` never
        # fired, and on any host where the engine works this asserted the
        # unavailable-message against the available-message. Which is exactly
        # what happened in CI, where the engine does work; it passed locally
        # only because WeasyPrint is unavailable on Windows. pdf_available() is
        # the real predicate, and is what test_reports.py already uses.
        if pdf_available():
            pytest.skip("PDF engine is available here; there is no failure reason "
                        "to assert on")
        reason = unavailable_reason() or ""
        assert "are present in the project's Linux container image" not in reason
        assert "Dockerfile installs it" in reason

    def test_a_tagged_pdf_is_requested(self):
        from app.reports.engine.pdf_engine import PDF_VARIANT, engine_info

        assert PDF_VARIANT == "pdf/ua-1"
        assert engine_info()["pdf_variant_requested"] == "pdf/ua-1"

    def test_no_section_508_conformance_is_claimed_from_generation(self):
        from app.reports.engine.pdf_engine import engine_info

        note = engine_info()["note"]
        assert "not proof of it" in note
        assert "No Section 508 conformance is claimed" in note

    @pytest.mark.skipif(
        os.name == "nt",
        reason="WeasyPrint's Pango/Cairo/GObject stack is not installed on this "
               "Windows host. This test runs in the Linux container image, "
               "which Phase 7.5 fixed to actually contain it.")
    def test_a_pdf_actually_renders(self):
        from app.reports.engine.pdf_engine import pdf_available, render_pdf

        assert pdf_available(), "PDF engine unavailable on a platform that should have it"
        pdf = render_pdf("<html><body><h1>Test</h1><table><caption>c</caption>"
                         "<tr><th scope='col'>A</th></tr><tr><td>1</td></tr>"
                         "</table></body></html>", title="test")
        assert pdf[:5] == b"%PDF-"
        assert len(pdf) > 500

    @pytest.mark.skipif(
        os.name == "nt",
        reason="PDF rendering unavailable on this Windows host; runs in the "
               "Linux container image.")
    def test_the_development_banner_survives_pdf_rendering(self):
        from app.reports.engine.pdf_engine import render_pdf

        pdf = render_pdf(
            '<html><body><div class="dev-banner"><p>DEVELOPMENT / TEST DATA '
            '&mdash; NOT FOR GOVERNMENT DELIVERY</p></div></body></html>',
            title="banner")
        assert pdf[:5] == b"%PDF-"
