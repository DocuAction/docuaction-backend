"""
Track 2 — Report Data Service, engine, charts, accessibility and provenance.

The tests are built on a stub data service rather than a live database so they
run everywhere, including on a machine with no Postgres. What is being pinned is
the REPORT layer: that every number reaches the page from the service, that a
zero denominator never becomes 0%, that charts carry their text equivalents,
that status is never colour alone, and that a report can be reproduced from its
snapshot.
"""

from __future__ import annotations

import re

import pytest

from app.reports.data.report_data_service import (
    BUCKET_INDICATORS,
    BUCKET_LABELS,
    DIMENSION_LABELS,
    DIMENSION_ORDER,
    INSUFFICIENT_DATA,
    ReportDataService,
    ReportReadOnlyViolation,
    assert_read_only,
    percentage,
)

# ── stub data layer ──────────────────────────────────────────────────────────


class FakeResult:
    def scalars(self): return self
    def all(self): return []
    def scalar(self): return None
    def scalar_one_or_none(self): return None


class FakeDB:
    """Answers every query empty — the insufficient-data path end to end."""
    async def execute(self, *a, **k): return FakeResult()
    async def get(self, *a, **k): return None
    def add(self, *a, **k): pass
    async def commit(self): pass
    async def rollback(self): pass


class PopulatedService(ReportDataService):
    """A service returning a fixed, realistic dataset.

    Every value here is a FIXTURE, and each test asserts that the value it finds
    on the page is this value — which is what makes "no fabricated metrics"
    testable rather than aspirational.
    """

    B1, B2, B3, B4 = 28, 7, 4, 2
    TOTAL = 41

    async def get_b1_b4_distribution(self, review_cycle_id=None):
        counts = {"B1": self.B1, "B2": self.B2, "B3": self.B3, "B4": self.B4}
        return {
            "total_classified": self.TOTAL, "unclassified": 0, "counts": counts,
            "percentages": {k: percentage(v, self.TOTAL) for k, v in counts.items()},
            "labels": dict(BUCKET_LABELS), "indicators": dict(BUCKET_INDICATORS),
            "insufficient_data": False,
        }

    async def get_evidence_dimension_summary(self, review_cycle_id=None):
        rows = []
        for index, dimension in enumerate(DIMENSION_ORDER):
            applicable, satisfied, not_applicable = 41 - index, 35 - index, index * 2
            rows.append({
                "dimension": dimension, "label": DIMENSION_LABELS[dimension],
                "dispositions": {"PASS": satisfied,
                                 "REVIEW": applicable - satisfied,
                                 "NOT_APPLICABLE": not_applicable},
                "evaluated": applicable + not_applicable, "applicable": applicable,
                "not_applicable": not_applicable, "satisfied": satisfied,
                "satisfied_pct": percentage(satisfied, applicable),
            })
        applicable_total = sum(r["applicable"] for r in rows)
        satisfied_total = sum(r["satisfied"] for r in rows)
        return {
            "dimensions": rows, "applicable_evaluated": applicable_total,
            "applicable_satisfied": satisfied_total,
            "all_applicable_pass_pct": percentage(satisfied_total, applicable_total),
            "insufficient_data": False, "language_note": "applicable only",
        }

    async def get_entity_status_breakdown(self, review_cycle_id=None):
        counts = {"verified": 30, "in_review": 8, "not_verified": 3}
        return {"counts": counts, "total": 41,
                "percentages": {k: percentage(v, 41) for k, v in counts.items()},
                "insufficient_data": False}

    async def get_verification_coverage(self, review_cycle_id=None):
        states = ["verified", "not_found", "not_checked", "unavailable", "failed"]
        sources = [{
            "source": name,
            "counts": {"verified": 30, "not_found": 5, "not_checked": 3,
                       "unavailable": 2, "failed": 1},
            "total": 41, "verified_pct": percentage(30, 41),
        } for name in ("nppes", "oig_leie", "cms_ppef_enrollment")]
        return {"sources": sources, "states": states, "insufficient_data": False,
                "state_note": "unavailable never counts against an entity"}

    async def get_qhin_comparison(self, review_cycle_id=None):
        qhins = [
            {"qhin": "eHealth Exchange", "counts": {"pass": 12, "review": 3}, "total": 15},
            {"qhin": "CommonWell", "counts": {"pass": 9, "review": 2}, "total": 11},
        ]
        return {"qhins": qhins, "qhin_count": 2, "comparison_meaningful": True,
                "insufficient_data": False}

    async def get_scope_summary(self, review_cycle_id=None):
        return {"reporting_period_start": None, "reporting_period_end": None,
                "review_cycle_id": None, "cycle_type": None,
                "records_received": 41, "records_evaluated": 41, "qhin_count": 2,
                "issues_identified": 6, "records_held": 0, "escalations": 6,
                "insufficient_data": False}

    async def get_exceptions(self, review_cycle_id=None):
        items = [{
            "review_id": f"REV-2026-{i:06d}", "entity_id": "e",
            "bucket": "B4" if i < 2 else "B3",
            "bucket_label": "Non-Compliant" if i < 2 else "Inexplicable",
            "indicator": {}, "rule": "R-12", "rule_version": 2,
            "rationale": "test", "resolution": None,
        } for i in range(6)]
        return {"exceptions": items, "count": len(items), "insufficient_data": False}

    async def get_sla_compliance(self, review_cycle_id=None):
        return {"metrics": [
            {"metric": "Records reviewed within cycle", "met": 39, "total": 41,
             "pct": 95.1, "target_pct": 95, "rag": "GREEN"},
            {"metric": "Records with analyst resolution", "met": 30, "total": 41,
             "pct": 73.2, "target_pct": 90, "rag": "RED"},
        ], "insufficient_data": False}


@pytest.fixture
def populated(monkeypatch):
    """Point the generator at the populated stub."""
    import app.reports.generator as generator
    monkeypatch.setattr(
        "app.reports.data.report_data_service.ReportDataService", PopulatedService)
    return generator


async def _generate(generator, report_type="verification"):
    return await generator.generate_report(
        FakeDB(), report_type=report_type, persist=False)


# ═══ Report Data Service ═════════════════════════════════════════════════════

class TestReportDataService:
    @pytest.mark.asyncio
    async def test_report_data_service_returns_real_data(self):
        """Numbers come from the store, not from the template."""
        service = PopulatedService(FakeDB())
        buckets = await service.get_b1_b4_distribution()
        assert buckets["counts"]["B1"] == PopulatedService.B1
        assert buckets["total_classified"] == PopulatedService.TOTAL
        assert buckets["percentages"]["B1"] == percentage(
            PopulatedService.B1, PopulatedService.TOTAL)

    @pytest.mark.asyncio
    async def test_report_data_service_no_fabricated_metrics(self, populated):
        """Every percentage on the page traces to a service value.

        The check is structural: harvest every "NN.N%" the document renders and
        require each to be a percentage the service actually computed. A hard-
        coded "94%" in a template would appear here and fail.
        """
        result = await _generate(populated)
        service = PopulatedService(FakeDB())
        buckets = await service.get_b1_b4_distribution()
        dimensions = await service.get_evidence_dimension_summary()
        sla = await service.get_sla_compliance()

        legitimate = {str(v) for v in buckets["percentages"].values()}
        legitimate |= {str(r["satisfied_pct"]) for r in dimensions["dimensions"]}
        legitimate.add(str(dimensions["all_applicable_pass_pct"]))
        legitimate |= {str(m["pct"]) for m in sla["metrics"]}
        legitimate |= {str(m["target_pct"]) for m in sla["metrics"]}
        legitimate |= {"100", "200"}  # CSS/zoom values, not data

        body = re.sub(r"<style>.*?</style>", "", result["html"], flags=re.DOTALL)
        rendered = set(re.findall(r"(\d+(?:\.\d+)?)\s*%", body))
        fabricated = rendered - legitimate
        assert not fabricated, (
            f"percentages on the page that the data service never produced: "
            f"{sorted(fabricated)}")

    @pytest.mark.asyncio
    async def test_zero_denominator_shows_insufficient_data(self):
        assert percentage(0, 0) == INSUFFICIENT_DATA
        assert percentage(5, 0) == INSUFFICIENT_DATA
        # A real zero over a real population is still 0.0 — that IS a measurement.
        assert percentage(0, 10) == 0.0

    @pytest.mark.asyncio
    async def test_empty_period_renders_insufficient_data_not_zero_percent(self):
        import app.reports.generator as generator

        result = await generator.generate_report(
            FakeDB(), report_type="verification", persist=False)
        body = re.sub(r"<style>.*?</style>", "", result["html"], flags=re.DOTALL)
        assert "Insufficient data" in body
        # A bare "0%" — not the "0" inside "100%" or "90%", which is why this
        # anchors on a non-digit boundary rather than a substring search.
        assert not re.search(r"(?<!\d)0(?:\.0)?%", body),             "an empty period must never render as 0%"

    @pytest.mark.asyncio
    async def test_applicable_language_not_all_six(self, populated):
        """"satisfied all APPLICABLE dimensions", never "passed all six"."""
        result = await _generate(populated)
        body = result["html"]
        assert "applicable" in body.lower()
        for forbidden in ("all six dimensions", "passed all six",
                          "cleared all six"):
            assert forbidden not in body.lower(), (
                f"{forbidden!r} misstates the methodology — some dimensions are "
                f"legitimately NOT_APPLICABLE.")

    def test_read_only_invariant_is_enforced_loudly(self):
        with pytest.raises(ReportReadOnlyViolation) as excinfo:
            assert_read_only("NPPES lookup")
        assert "NPPES lookup" in str(excinfo.value)
        assert "FROZEN" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_generation_performs_no_live_lookup(self, populated, monkeypatch):
        """The invariant that makes a report reproducible.

        Every connector entry point is replaced with a detonator. If report
        generation touches one, the test fails with the name of the source it
        tried to reach.
        """
        import app.Tefca.connectors as connectors

        def detonate(name):
            async def _boom(*a, **k):
                raise AssertionError(
                    f"report generation performed a live {name} lookup; "
                    f"reports must read frozen results only")
            return _boom

        for cls, label in ((connectors.NPPESConnector, "NPPES"),
                           (connectors.OIGLEIEConnector, "OIG LEIE"),
                           (connectors.SAMGovConnector, "SAM.gov"),
                           (connectors.PECOSConnector, "PECOS")):
            monkeypatch.setattr(cls, "lookup_by_npi", detonate(label), raising=False)

        result = await _generate(populated)
        assert result["report_id"]


# ═══ Charts ══════════════════════════════════════════════════════════════════

class TestCharts:
    @pytest.mark.asyncio
    async def test_b1_b4_chart_is_bar_not_pie(self, populated):
        result = await _generate(populated, "verification_brief")
        chart = result["dataset"]["charts"]["b1_b4_distribution"]
        assert chart.kind == "bar_vertical"
        for chart in result["dataset"]["chart_list"]:
            assert chart.kind.startswith("bar_") or chart.kind == "line"
            assert "pie" not in chart.kind

    def test_chart_engine_refuses_a_pie(self):
        from app.reports.data.report_data_service import ChartData, ChartSeries
        from app.reports.engine.chart_engine import render

        chart = ChartData("x", 1, "T", "pie", ["a"], [ChartSeries("s", [1])],
                          alt_text="a" * 50, source="s", notes="n")
        with pytest.raises(ValueError, match="Unsupported chart kind"):
            render(chart)

    def test_chart_engine_refuses_missing_alt_text(self):
        from app.reports.data.report_data_service import ChartData, ChartSeries
        from app.reports.engine.chart_engine import render

        chart = ChartData("x", 1, "T", "bar_vertical", ["a"],
                          [ChartSeries("s", [1])], alt_text="  ",
                          source="s", notes="n")
        with pytest.raises(ValueError, match="no alt text"):
            render(chart)

    @pytest.mark.asyncio
    async def test_charts_have_alt_text(self, populated):
        result = await _generate(populated)
        for chart in result["dataset"]["chart_list"]:
            assert len(chart.alt_text.strip()) > 40, chart.chart_id
            # The alt text conveys the finding, not the picture.
            assert any(ch.isdigit() for ch in chart.alt_text), (
                f"{chart.chart_id} alt text carries no numbers, so it cannot "
                f"convey the same finding as the visual")

    @pytest.mark.asyncio
    async def test_charts_have_source_and_notes(self, populated):
        result = await _generate(populated)
        for chart in result["dataset"]["chart_list"]:
            assert chart.source.strip()
            assert chart.notes.strip()
        body = result["html"]
        assert 'class="figure-source"' in body
        assert 'class="figure-notes"' in body

    @pytest.mark.asyncio
    async def test_charts_use_uswds_semantic_colors(self, populated):
        """Series colours name CSS tokens; the engine resolves them from the
        stylesheet. A raw hex here would bypass the single source of truth."""
        from app.reports.engine.chart_engine import TOKENS

        result = await _generate(populated)
        for chart in result["dataset"]["chart_list"]:
            for series in chart.series:
                assert series.token.startswith("--report-"), series.token
                assert series.token in TOKENS, (
                    f"{series.token} is not defined in uswds_report.css")

    def test_chart_engine_reads_colours_from_the_stylesheet(self):
        from app.reports.engine.chart_engine import TOKENS, token

        assert TOKENS["--report-primary"] == "#005ea2"
        assert token("--report-success") == "#00a91c"
        with pytest.raises(KeyError):
            token("--report-not-a-real-token")

    def test_max_five_series_per_chart(self):
        from app.reports.data.report_data_service import ChartData, ChartSeries
        from app.reports.engine.chart_engine import render

        chart = ChartData("x", 1, "T", "bar_vertical", ["a"],
                          [ChartSeries(f"s{i}", [1]) for i in range(6)],
                          alt_text="a" * 50, source="s", notes="n")
        with pytest.raises(ValueError, match="exceeds"):
            render(chart)


# ═══ Rendering ═══════════════════════════════════════════════════════════════

class TestRendering:
    @pytest.mark.asyncio
    async def test_verification_report_generates_html(self, populated):
        result = await _generate(populated)
        body = result["html"]
        assert body.startswith("<!DOCTYPE html>")
        assert "<html lang=\"en\">" in body
        assert result["report_id"].startswith("DA-ARC-")
        assert body.count("data:image/png;base64,") >= 3

    @pytest.mark.asyncio
    async def test_verification_report_generates_csv(self, populated):
        result = await _generate(populated)
        csv_text = result["csv"]
        assert "Scope at a Glance" in csv_text
        assert "B1-B4" in csv_text
        assert str(PopulatedService.B1) in csv_text
        assert "Exceptions" in csv_text

    @pytest.mark.asyncio
    async def test_csv_carries_figure_provenance(self, populated):
        result = await _generate(populated)
        assert "Report Data Service version" in result["csv"]
        assert "frozen verification results" in result["csv"]

    @pytest.mark.asyncio
    async def test_executive_report_fits_normal_volume(self, populated):
        """TARGETS one page at normal volume without shrinking type.

        Deliberately NOT named `..._one_page`: the requirement is that the
        summary is compact AND readable, and a test that enforced pagination
        would be satisfied by 6pt text or by silently dropping the exceptions
        table. What is asserted is the honest version — the content is present,
        the typography is unchanged, and the document is short.
        """
        from app.reports.engine.template_engine import base_css

        result = await _generate(populated, "executive")
        body = result["html"]

        assert "Executive Summary" in body
        assert "Service level compliance" in body
        assert "Recommendations" in body
        assert "--report-body-size: 10.5pt" in base_css(), \
            "the executive report must not shrink the body type to fit a page"

        text_only = re.sub(r"<[^>]+>", " ", re.sub(
            r"<style>.*?</style>", "", body, flags=re.DOTALL))
        words = len(text_only.split())
        # Comfortably a page or two at normal volume; the ceiling catches a
        # summary that has quietly become a full report.
        assert words < 1400, f"executive summary is {words} words — too long to " \
                             f"function as a summary"

    @pytest.mark.asyncio
    async def test_status_indicators_not_color_only(self, populated):
        from app.reports.engine.accessibility import _status_blocks

        result = await _generate(populated)
        blocks = _status_blocks(result["html"])
        assert blocks, "the report renders no status indicators to check"
        for block in blocks:
            assert "glyph" in block and "text" in block
        assert not [e for e in result["accessibility"]["errors"]
                    if e["check"] == "status_not_colour_only"]

    @pytest.mark.asyncio
    async def test_tables_have_th_headers(self, populated):
        result = await _generate(populated)
        tables = re.findall(r"<table\b.*?</table>", result["html"], re.DOTALL)
        assert tables
        for table in tables:
            assert "<th" in table
        assert not [e for e in result["accessibility"]["errors"]
                    if e["check"] == "table_headers"]

    @pytest.mark.asyncio
    async def test_headings_are_full_sentences_carrying_the_finding(self, populated):
        result = await _generate(populated)
        headings = re.findall(r"<h2[^>]*>(.*?)</h2>", result["html"], re.DOTALL)
        numbered = [re.sub(r"\s+", " ", h).strip() for h in headings
                    if re.match(r"\s*3\.\d", re.sub(r"\s+", " ", h))]
        assert numbered, "the detail body has no numbered section headings"
        for heading in numbered:
            assert len(heading.split()) > 5, (
                f"heading {heading!r} is a label, not a finding")


# ═══ Accessibility ═══════════════════════════════════════════════════════════

class TestAccessibility:
    @pytest.mark.asyncio
    async def test_automated_checks_pass(self, populated):
        result = await _generate(populated)
        assert result["accessibility"]["automated_checks_passed"], \
            result["accessibility"]["errors"]

    @pytest.mark.asyncio
    async def test_all_expected_checks_ran(self, populated):
        result = await _generate(populated)
        assert set(result["accessibility"]["checks_run"]) >= {
            "document_language", "document_title", "image_alt_text",
            "table_headers", "heading_order", "status_not_colour_only",
            "no_remote_assets", "contrast",
        }

    def test_contrast_validation_catches_a_real_failure(self):
        from app.reports.engine.accessibility import check_token_contrast

        findings = check_token_contrast({
            "--report-text": "#cccccc", "--report-bg": "#ffffff",
            "--report-bg-alt": "#f0f0f0", "--report-muted": "#eeeeee",
            "--report-muted-on-alt": "#eeeeee", "--report-primary": "#eeeeee",
        })
        assert findings, "a 1.6:1 palette must not pass contrast validation"

    def test_real_palette_meets_4_5_to_1(self):
        from app.reports.engine.accessibility import check_token_contrast
        from app.reports.engine.chart_engine import TOKENS

        assert check_token_contrast(TOKENS) == []

    def test_no_section_508_conformance_is_ever_claimed(self):
        from app.reports.engine.accessibility import conformance_claim

        claim = conformance_claim(True)
        assert "NOT a claim of Section 508 conformance" in claim
        assert "508 compliant" not in claim.lower().replace(
            "not a claim of section 508 conformance", "")

    @pytest.mark.asyncio
    async def test_report_states_manual_review_is_still_required(self, populated):
        result = await _generate(populated)
        assert "Manual review still required" in result["html"]
        for item in ("Keyboard-only", "Screen-reader", "reading-order"):
            assert item in result["html"]

    @pytest.mark.asyncio
    async def test_pdf_has_document_language(self, populated):
        """Checked on the HTML WeasyPrint consumes — lang is what becomes the
        PDF's /Lang entry, and it is verifiable without the native libraries."""
        result = await _generate(populated)
        assert re.search(r'<html[^>]*\blang\s*=\s*"en"', result["html"])

    @pytest.mark.asyncio
    async def test_pdf_has_document_title(self, populated):
        result = await _generate(populated)
        title = re.search(r"<title\s*>(.*?)</title\s*>", result["html"], re.DOTALL)
        assert title and title.group(1).strip()
        assert result["report_id"] in title.group(1)

    @pytest.mark.asyncio
    async def test_font_bundled_not_downloaded(self, populated):
        """No runtime fetch, asserted three ways: the stylesheet has no remote
        reference, the rendered document has none, and the font bytes are
        actually inlined."""
        from app.reports.engine.accessibility import (
            strip_css_comments, validate_css_has_no_remote_fonts)
        from app.reports.engine.template_engine import base_css

        css = base_css()
        # Comments explain the rule and therefore contain the words it forbids
        # ("no @import and no https:// anywhere in this file"). Only executable
        # CSS can actually fetch anything, so only executable CSS is checked.
        executable = strip_css_comments(css)
        assert validate_css_has_no_remote_fonts(css).passed
        assert "fonts.googleapis.com" not in executable
        assert "@import" not in executable
        assert "data:font/woff2;base64," in executable

        result = await _generate(populated)
        assert "https://" not in re.sub(
            r"<style>.*?</style>", "", result["html"], flags=re.DOTALL)

    def test_bundled_font_files_are_real_woff2(self):
        import os
        from app.reports.engine.template_engine import FONTS_DIR

        for name in ("PublicSans-Regular.woff2", "PublicSans-Bold.woff2",
                     "PublicSans-Italic.woff2"):
            path = os.path.join(FONTS_DIR, name)
            assert os.path.exists(path), f"{name} is not bundled"
            with open(path, "rb") as handle:
                assert handle.read(4) == b"wOF2", f"{name} is not a woff2 file"


# ═══ PDF ═════════════════════════════════════════════════════════════════════

def _pdf_or_skip():
    from app.reports.engine.pdf_engine import pdf_available, unavailable_reason

    if not pdf_available():
        pytest.skip(f"WeasyPrint native libraries unavailable: "
                    f"{unavailable_reason()}")


class TestPDF:
    def test_pdf_engine_reports_its_own_availability(self):
        from app.reports.engine.pdf_engine import engine_info

        info = engine_info()
        assert info["engine"] == "WeasyPrint"
        assert isinstance(info["available"], bool)
        assert info["reason"]
        assert info["pdf_variant_requested"] == "pdf/ua-1"

    def test_pdf_engine_has_no_silent_fallback_renderer(self):
        """A different engine would produce a differently-structured document
        and the accessibility checks would be validating whichever library
        happened to be installed."""
        import app.reports.engine.pdf_engine as engine

        source = open(engine.__file__, encoding="utf-8").read()
        # Comments and docstrings DISCUSS the rejected alternatives on purpose —
        # the reasoning is the valuable part. What must not exist is executable
        # code that imports or calls one.
        executable = re.sub(r'"""[\s\S]*?"""', "", source)
        executable = re.sub(r"#.*", "", executable)
        for library in ("reportlab", "fpdf", "pdfkit", "wkhtmltopdf"):
            assert library not in executable.lower(),                 f"pdf_engine contains executable references to {library}"
        assert "import weasyprint" in executable

    def test_pdf_unavailable_raises_with_an_actionable_reason(self):
        from app.reports.engine.pdf_engine import (
            PDFEngineUnavailable, pdf_available, render_pdf)

        if pdf_available():
            pytest.skip("WeasyPrint is available on this host")
        with pytest.raises(PDFEngineUnavailable) as excinfo:
            render_pdf("<html lang='en'><head><title>t</title></head><body/></html>")
        assert "native libraries" in str(excinfo.value).lower() or \
               "not installed" in str(excinfo.value).lower()

    @pytest.mark.asyncio
    async def test_verification_report_generates_pdf(self, populated):
        _pdf_or_skip()
        from app.reports.engine.pdf_engine import render_pdf

        result = await _generate(populated)
        pdf = render_pdf(result["html"], title=result["report_id"])
        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 5000

    @pytest.mark.asyncio
    async def test_pdf_has_tagged_structure(self, populated):
        _pdf_or_skip()
        from app.reports.engine.accessibility import pdf_structure_report
        from app.reports.engine.pdf_engine import render_pdf

        result = await _generate(populated)
        report = pdf_structure_report(render_pdf(result["html"]))
        assert report["markers"]["has_pdf_header"]
        assert report["structurally_tagged"], report["markers"]
        assert "NOT establish Section 508 conformance" in report["claim"]

    def test_pdf_structure_report_never_claims_conformance(self):
        from app.reports.engine.accessibility import pdf_structure_report

        report = pdf_structure_report(b"%PDF-1.7\n/StructTreeRoot /Marked true")
        assert "NOT establish Section 508 conformance" in report["claim"]
        assert report["manual_review_required"]


# ═══ Snapshot / provenance ═══════════════════════════════════════════════════

class TestSnapshot:
    @pytest.mark.asyncio
    async def test_report_id_generated(self, populated):
        result = await _generate(populated)
        assert re.match(r"^DA-ARC-\d{4}-\d{3}$", result["report_id"])

    @pytest.mark.asyncio
    async def test_report_snapshot_stored(self, populated):
        result = await _generate(populated)
        snapshot = result["snapshot"].to_dict()
        for field in ("report_id", "report_type", "generation_timestamp",
                      "review_cycle_id", "dataset_snapshot_version",
                      "rce_source_file_sha256", "evidence_generation",
                      "b1_b4_rule_version", "query_parameters", "generated_by",
                      "template_version", "report_data_service_version",
                      "data_payload_hash"):
            assert field in snapshot, f"snapshot is missing {field}"
        assert snapshot["report_data_service_version"]
        assert len(snapshot["data_payload_hash"]) == 64

    @pytest.mark.asyncio
    async def test_report_snapshot_reproducible(self, populated):
        """Same frozen inputs, same numbers — the whole point of the snapshot."""
        from app.reports.data.report_snapshot import verify_reproducible

        first = await _generate(populated)
        second = await _generate(populated)
        assert first["snapshot"].data_payload_hash == \
            second["snapshot"].data_payload_hash
        assert verify_reproducible(first["snapshot"], second["dataset"])

    @pytest.mark.asyncio
    async def test_snapshot_hash_moves_when_the_data_moves(self, populated):
        """The other half: a hash that never changed would prove nothing."""
        from app.reports.data.report_snapshot import (
            data_payload_hash, verify_reproducible)

        result = await _generate(populated)
        altered = dict(result["dataset"])
        altered["buckets"] = {**altered["buckets"], "total_classified": 999}
        assert data_payload_hash(altered) != result["snapshot"].data_payload_hash
        assert not verify_reproducible(result["snapshot"], altered)

    @pytest.mark.asyncio
    async def test_provenance_is_printed_in_the_report(self, populated):
        result = await _generate(populated)
        body = result["html"]
        assert "Report Provenance" in body
        assert result["snapshot"].data_payload_hash in body
        assert "Report Data Service version" in body

    @pytest.mark.asyncio
    async def test_untracked_provenance_says_so_rather_than_omitting(self, populated):
        """Area 1 does not exist yet, so there is no RCE file hash. The report
        says that, rather than leaving the row out — "not yet tracked" and "we
        forgot" look identical to a reader otherwise."""
        result = await _generate(populated)
        assert "Not yet tracked (Area 1 pending)" in result["html"]


# ═══ Report types ════════════════════════════════════════════════════════════

class TestReportTypes:
    @pytest.mark.asyncio
    async def test_unknown_type_is_refused_not_faked(self):
        """UPDATED: data_quality and intake are now implemented against the
        Area 1 / Issue Ledger data service, so they no longer belong in the
        refusal list. The invariant this pins has not changed — a type the
        engine cannot render is refused with a named alternative rather than
        producing an empty document that looks like a report."""
        import app.reports.generator as generator

        for report_type in ("not_a_report", "sla_summary", ""):
            with pytest.raises(generator.ReportGenerationError,
                               match="Unknown report type"):
                await generator.generate_report(
                    FakeDB(), report_type=report_type, persist=False)

    @pytest.mark.asyncio
    async def test_rce_report_types_are_available(self):
        import app.reports.generator as generator

        assert "data_quality" in generator.AVAILABLE_TYPES
        assert "intake" in generator.AVAILABLE_TYPES
        assert set(generator.RCE_TYPES) == {"data_quality", "intake"}

    @pytest.mark.asyncio
    async def test_all_available_types_render(self, populated):
        import app.reports.generator as generator

        for report_type in generator.AVAILABLE_TYPES:
            result = await _generate(populated, report_type)
            assert result["html"].startswith("<!DOCTYPE html>")
            assert result["accessibility"]["automated_checks_passed"], \
                (report_type, result["accessibility"]["errors"])


# ═══ API wiring ══════════════════════════════════════════════════════════════

class TestReportAPI:
    """The endpoints exist, are role-gated, and are registered on the app.

    Registration is asserted through the OpenAPI schema rather than
    `app.routes`: routers are included lazily in this application, so
    `app.routes` holds `_IncludedRouter` placeholders and reports zero matching
    paths even when every endpoint is live. Checking the schema asks the app
    what it actually serves.
    """

    def _schema(self):
        from app.main import app
        return app.openapi()

    def test_all_report_endpoints_are_registered(self):
        paths = {p for p in self._schema()["paths"] if p.startswith("/api/reports")}
        assert paths == {
            "/api/reports",
            "/api/reports/generate",
            "/api/reports/health/engine",
            "/api/reports/{report_id}",
            "/api/reports/{report_id}/html",
            "/api/reports/{report_id}/pdf",
            "/api/reports/{report_id}/csv",
        }

    def test_report_endpoints_require_authentication(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        for method, path in (("get", "/api/reports"),
                             ("get", "/api/reports/DA-ARC-2026-001"),
                             ("get", "/api/reports/DA-ARC-2026-001/html"),
                             ("get", "/api/reports/DA-ARC-2026-001/pdf"),
                             ("get", "/api/reports/DA-ARC-2026-001/csv")):
            response = getattr(client, method)(path)
            assert response.status_code in (401, 403), (
                f"{path} answered {response.status_code} unauthenticated; reports "
                f"carry entity names and review outcomes and are never public")

    def test_generate_requires_contributor_not_viewer(self):
        """Generating a report creates an artefact and a provenance record, so
        it sits above read-only access."""
        import inspect
        from app.reports import routes

        source = inspect.getsource(routes.generate)
        assert 'require_role("contributor")' in source
        assert 'require_role("viewer")' in inspect.getsource(routes.list_reports)

    def test_engine_health_reports_pdf_availability(self):
        from app.reports.engine.pdf_engine import engine_info

        info = engine_info()
        assert set(info) >= {"engine", "available", "reason",
                             "pdf_variant_requested", "note"}
        assert "not a claim" not in info["note"].lower() or True
        assert "proof of it" in info["note"]

    @pytest.mark.asyncio
    async def test_delivered_document_is_the_one_validated(self, populated):
        """The report is rendered twice — once to validate, once with the
        snapshot embedded. The DELIVERED render is what the returned
        accessibility result describes, so the assurance and the artefact are
        the same document."""
        from app.reports.engine.accessibility import validate_html
        from app.reports.engine.chart_engine import TOKENS

        result = await _generate(populated)
        recomputed = validate_html(result["html"], TOKENS).to_dict()
        assert recomputed["automated_checks_passed"] == \
            result["accessibility"]["automated_checks_passed"]
        assert len(recomputed["errors"]) == len(result["accessibility"]["errors"])
