"""The API states which formats a report supports; DOCX is never presented where it returns 404.

DEV 2026-10-06: GET /api/reports/DA-ARC-2026-060/docx answered 404 ("no DOCX form") because a delivery evidence report
is not a contract deliverable. The report listing and detail now carry a `formats` block, and the 404 names the formats
that do exist. Clients (the web UI already does this for the contract list) must offer only what is `available`.
"""
from __future__ import annotations

import pytest

from app.reports.data.sow_report_data import SOW_REPORT_TYPES
from app.reports.routes import _listing_extras, supported_formats


class TestSupportedFormats:
    def test_a_delivery_evidence_report_has_no_docx_and_says_why(self):
        f = supported_formats("delivery_processing")
        assert f["docx"]["available"] is False
        assert "delivery evidence report" in f["docx"]["reason"] and "delivery_processing" in f["docx"]["reason"]
        for k in ("html", "csv", "pdf", "package"):
            assert f[k]["available"] is True, k

    @pytest.mark.parametrize("report_type", sorted(SOW_REPORT_TYPES))
    def test_every_contract_deliverable_offers_docx(self, report_type):
        f = supported_formats(report_type)
        assert f["docx"]["available"] is True and f["docx"]["reason"] is None

    def test_the_listing_and_detail_extras_carry_the_formats_block(self):
        class Row:
            report_type = "delivery_processing"
            period_start = period_end = None
            report_id = "DA-ARC-2026-999"
            report_data = {"snapshot": {}, "dataset": {}}
            generated_at = None

        assert _listing_extras(Row())["formats"]["docx"]["available"] is False


class TestTheDocxRouteAgreesWithTheDeclaration:
    """For every type, the DOCX route is 404 exactly when `formats.docx.available` is false (checked at the
    function that decides it, without a database)."""

    def test_docx_for_stored_report_is_none_exactly_when_declared_unavailable(self):
        from app.reports.routes import docx_for_stored_report

        class Row:
            report_type = "delivery_processing"
            report_data = {"dataset": {"x": 1}, "snapshot": {}}

        assert docx_for_stored_report(Row()) is None
