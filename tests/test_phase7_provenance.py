"""Phase 7A — report provenance: the source hash, the cycle, the classification.

Every report generated before this module stamped `rce_source_file_sha256 =
"cafe"`, because the old lookup read a legacy import-batch table whose newest
row is a unit-test fixture. The tests here exist so that specific failure — a
provenance field that is populated, plausible-looking to a machine, and wrong —
cannot come back.

DEVELOPMENT/TEST DATA. Nothing asserted here is an ONC finding.
"""
from __future__ import annotations

import pytest

from app.reports.data.source_provenance import (
    CLASSIFICATION_DEVELOPMENT, CLASSIFICATION_GOVERNMENT, DEV_CYCLE_PREFIX,
    REASON_NO_INTAKE, REASON_UNUSABLE, SourceProvenance,
    authoritative_source_provenance, development_cycle_id, is_real_sha256,
    resolve_cycle_id)

REAL = "689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d"


class TestNoPlaceholderHashes:
    """The specific values this codebase has actually shipped in a hash field."""

    @pytest.mark.parametrize("junk", [
        "cafe",       # tefca_import_batches, newest row, stamped on all 5 reports
        "deadbeef",   # same table, fhir_bundle fixtures
        "x",          # same table, dbg.json
        "", "   ", None, 0, False,
    ])
    def test_observed_placeholders_are_rejected(self, junk):
        assert is_real_sha256(junk) is False

    @pytest.mark.parametrize("nearly", [
        REAL[:63],            # one short
        REAL + "a",           # one long
        REAL[:-1] + "g",      # not hex
    ])
    def test_near_misses_are_rejected(self, nearly):
        assert is_real_sha256(nearly) is False

    def test_case_is_normalised_not_rejected(self):
        """An uppercase digest is a real digest; only the casing differs."""
        assert is_real_sha256(REAL.upper()) is True

    def test_the_real_area1_digest_is_accepted(self):
        assert is_real_sha256(REAL) is True

    def test_a_provenance_carrying_junk_reports_no_hash(self):
        """Construction cannot launder a placeholder into `has_authoritative_hash`."""
        p = SourceProvenance(sha256="cafe")
        assert p.has_authoritative_hash is False


class TestClassificationTravelsWithTheHash:

    def test_development_is_the_default(self):
        assert SourceProvenance().data_classification == CLASSIFICATION_DEVELOPMENT

    def test_development_provenance_is_not_government_data(self):
        p = SourceProvenance(sha256=REAL)
        assert p.is_government_data is False

    def test_the_flag_is_present_in_the_serialised_form(self):
        """A consumer reading only the dict must still see the classification."""
        d = SourceProvenance(sha256=REAL).to_dict()
        assert d["data_classification"] == CLASSIFICATION_DEVELOPMENT
        assert d["is_government_data"] is False
        assert d["has_authoritative_hash"] is True

    def test_government_classification_is_expressible(self):
        """Not reachable today, but the transition must not need a code change."""
        p = SourceProvenance(sha256=REAL,
                             data_classification=CLASSIFICATION_GOVERNMENT)
        assert p.is_government_data is True


class TestUnavailableIsDistinctFromWrong:
    """"We do not know" and "here is a wrong answer" must not look alike."""

    def test_no_delivery_says_so(self):
        p = SourceProvenance(unavailable_reason=REASON_NO_INTAKE)
        assert p.sha256 is None
        assert p.unavailable_reason == REASON_NO_INTAKE

    def test_an_unusable_checksum_keeps_the_rest_of_the_delivery_record(self):
        """The delivery still happened; only its checksum is unusable."""
        p = SourceProvenance(original_filename="onc-snapshot-20260720.csv",
                             record_count=23566,
                             unavailable_reason=REASON_UNUSABLE)
        assert p.sha256 is None
        assert p.original_filename == "onc-snapshot-20260720.csv"
        assert p.record_count == 23566


class TestReportCycle:

    def test_a_cycle_is_never_null(self):
        """Every stored report before Phase 7 had review_cycle_id = None."""
        assert resolve_cycle_id(None, evidence_version=None,
                                source_sha256=None)

    def test_an_explicit_cycle_wins(self):
        got = resolve_cycle_id("CYCLE-7", evidence_version="phase6-bulk-1.1.0",
                               source_sha256=REAL)
        assert got == "CYCLE-7"

    def test_a_derived_cycle_is_deterministic(self):
        a = development_cycle_id("phase6-bulk-1.1.0", REAL)
        b = development_cycle_id("phase6-bulk-1.1.0", REAL)
        assert a == b

    def test_a_different_evidence_version_is_a_different_cycle(self):
        a = development_cycle_id("phase6-bulk-1.0.0", REAL)
        b = development_cycle_id("phase6-bulk-1.1.0", REAL)
        assert a != b

    def test_a_different_source_is_a_different_cycle(self):
        a = development_cycle_id("phase6-bulk-1.1.0", REAL)
        b = development_cycle_id("phase6-bulk-1.1.0", "f" * 64)
        assert a != b

    def test_a_derived_cycle_cannot_be_mistaken_for_a_contract_cycle(self):
        cid = development_cycle_id("phase6-bulk-1.1.0", REAL)
        assert cid.startswith(DEV_CYCLE_PREFIX)

    def test_a_placeholder_source_does_not_reach_the_cycle_id(self):
        cid = development_cycle_id("phase6-bulk-1.1.0", "cafe")
        assert "cafe" not in cid
        assert "nosource" in cid


class TestAuthoritativeLookup:
    """The lookup reads Area 1, and refuses what Area 1 cannot support."""

    @pytest.mark.asyncio
    async def test_no_intake_yields_a_reason_not_a_guess(self):
        class _Empty:
            async def execute(self, *_a, **_k):
                class R:
                    def scalars(self):
                        class S:
                            def first(self_inner): return None
                        return S()
                return R()

        p = await authoritative_source_provenance(_Empty())
        assert p.sha256 is None
        assert p.unavailable_reason == REASON_NO_INTAKE

    @pytest.mark.asyncio
    async def test_a_database_failure_never_invents_a_hash(self):
        class _Broken:
            async def execute(self, *_a, **_k):
                raise RuntimeError("connection reset")

        p = await authoritative_source_provenance(_Broken())
        assert p.sha256 is None
        assert p.unavailable_reason == REASON_NO_INTAKE

    @pytest.mark.asyncio
    async def test_an_intake_with_a_placeholder_checksum_is_refused(self):
        """The exact regression: a stored checksum of "cafe" must not propagate."""
        class _Row:
            id = "11111111-1111-1111-1111-111111111111"
            sha256 = "cafe"
            original_filename = "test.csv"
            record_count = 3
            schema_fingerprint = "abc"
            received_at = None
            status = "PARSED"

        class _Junk:
            async def execute(self, *_a, **_k):
                class R:
                    def scalars(self):
                        class S:
                            def first(self_inner): return _Row()
                        return S()
                return R()

        p = await authoritative_source_provenance(_Junk())
        assert p.sha256 is None
        assert p.unavailable_reason == REASON_UNUSABLE
        # but the delivery it did find is still described
        assert p.original_filename == "test.csv"

    @pytest.mark.asyncio
    async def test_a_real_intake_is_returned_with_its_delivery_facts(self):
        class _Row:
            id = "22222222-2222-2222-2222-222222222222"
            sha256 = REAL
            original_filename = "onc-snapshot-20260720.csv"
            record_count = 23566
            schema_fingerprint = "1cd655e9120dc9d0d6a52697ea470519"
            received_at = None
            status = "PARSED"

        class _Good:
            async def execute(self, *_a, **_k):
                class R:
                    def scalars(self):
                        class S:
                            def first(self_inner): return _Row()
                        return S()
                return R()

        p = await authoritative_source_provenance(_Good())
        assert p.sha256 == REAL
        assert p.record_count == 23566
        assert p.has_authoritative_hash is True
        # development until the Government delivery is what Area 1 holds
        assert p.data_classification == CLASSIFICATION_DEVELOPMENT


# ═══ Development watermark ═══════════════════════════════════════════════════

class TestDevelopmentWatermark:
    """The banner is a contract requirement of this phase, not decoration.

    A development report that reaches the wrong inbox has to announce itself
    before anyone reads a number off it. These tests render real documents
    through the real template stack rather than asserting on the template
    source, because what matters is what the recipient's screen shows.
    """

    @staticmethod
    async def _html(monkeypatch, classification="DEVELOPMENT_TEST"):
        from test_reports import FakeDB, PopulatedService
        import app.reports.generator as generator

        monkeypatch.setattr(
            "app.reports.data.report_data_service.ReportDataService",
            PopulatedService)
        monkeypatch.setattr(
            "app.reports.data.source_provenance._classification",
            lambda: classification)
        result = await generator.generate_report(
            FakeDB(), report_type="verification", persist=False)
        return result["html"]

    @pytest.mark.asyncio
    async def test_the_three_required_phrases_are_present(self, monkeypatch):
        html = await self._html(monkeypatch)
        assert "DEVELOPMENT / TEST DATA" in html
        assert "NOT FOR GOVERNMENT DELIVERY" in html
        assert "NOT ONC FINDINGS" in html

    @pytest.mark.asyncio
    async def test_the_banner_is_visible_not_only_metadata(self, monkeypatch):
        """It must be in the body, not hidden in a meta tag or a comment."""
        html = await self._html(monkeypatch)
        body = html.split("<body>", 1)[1]
        assert 'class="dev-banner"' in body
        # and not screen-reader-only, which would hide it from a sighted reader
        assert 'dev-banner sr-only' not in body

    @pytest.mark.asyncio
    async def test_the_banner_is_announced_to_assistive_technology(self, monkeypatch):
        html = await self._html(monkeypatch)
        assert 'role="note"' in html
        assert 'aria-label="Development data notice"' in html

    @pytest.mark.asyncio
    async def test_it_states_the_government_file_is_not_imported(self, monkeypatch):
        html = await self._html(monkeypatch)
        assert "Government source file has not yet been imported" in html

    @pytest.mark.asyncio
    async def test_every_page_of_a_pdf_carries_the_notice(self, monkeypatch):
        """A running page element, so pagination cannot strip it after page 1."""
        html = await self._html(monkeypatch)
        assert "@top-center" in html
        assert html.count("NOT FOR GOVERNMENT DELIVERY") >= 2

    @pytest.mark.asyncio
    async def test_the_provenance_table_states_the_classification(self, monkeypatch):
        html = await self._html(monkeypatch)
        assert "Data classification" in html
        assert "not for Government delivery" in html

    @pytest.mark.asyncio
    async def test_government_data_would_not_carry_the_development_banner(
            self, monkeypatch):
        """The banner is conditional, not unconditional — otherwise it would be
        ignored as boilerplate by the time it mattered."""
        html = await self._html(monkeypatch, classification="GOVERNMENT")
        assert 'class="dev-banner"' not in html
        assert "NOT FOR GOVERNMENT DELIVERY" not in html


class TestSnapshotCarriesProvenance:

    @pytest.mark.asyncio
    async def test_a_generated_snapshot_has_a_cycle_and_a_classification(
            self, monkeypatch):
        from test_reports import FakeDB, PopulatedService
        import app.reports.generator as generator

        monkeypatch.setattr(
            "app.reports.data.report_data_service.ReportDataService",
            PopulatedService)
        result = await generator.generate_report(
            FakeDB(), report_type="verification", persist=False)
        snap = result["snapshot"]

        # never null — every report stored before Phase 7 had a null cycle
        assert snap.review_cycle_id
        assert snap.data_classification == CLASSIFICATION_DEVELOPMENT
        # and never a placeholder hash
        assert snap.rce_source_file_sha256 != "cafe"
        if snap.rce_source_file_sha256 is not None:
            assert is_real_sha256(snap.rce_source_file_sha256)
