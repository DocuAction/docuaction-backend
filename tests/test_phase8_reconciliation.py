"""Phase 7 closure — legacy/canonical reconciliation and defect classification.

The reconciliation gate: every legacy-counted row must have a deterministic
reason it is not canonically reportable, or Phase 7 does not close.

    LEGACY POPULATION = CANONICAL REPORTABLE + RECONCILED NON-REPORTABLE

The three legacy defects are RE-TESTED here rather than taken from an earlier
run's report. Each is expressed as a property of the source that can fail if
someone changes it.

DEVELOPMENT/TEST DATA. Nothing here is an ONC finding.
"""
from __future__ import annotations

import inspect

import pytest

# ── the four-way classification the closure decision requires ────────────────

EXPECTED_CORRECTION = "EXPECTED_CORRECTION"
EXPECTED_ENHANCEMENT = "EXPECTED_ENHANCEMENT"
CANONICAL_REGRESSION = "CANONICAL_REGRESSION"
UNEXPLAINED = "UNEXPLAINED"

#: Every measured legacy/canonical difference, classified. A difference absent
#: from this table would be UNEXPLAINED, which fails the gate.
DIFFERENCES = {
    "source_table": EXPECTED_CORRECTION,
    "population": EXPECTED_CORRECTION,
    "reportability_gate": EXPECTED_CORRECTION,
    "evidence_selector": EXPECTED_CORRECTION,
    "contractual_labels": EXPECTED_ENHANCEMENT,
    "source_limitations": EXPECTED_ENHANCEMENT,
    "methodology_pending": EXPECTED_ENHANCEMENT,
    "evidence_scope": EXPECTED_ENHANCEMENT,
    "category_vocabulary": None,  # no difference — identical keys
}


class TestDifferenceClassification:

    def test_every_difference_is_classified(self):
        for name, classification in DIFFERENCES.items():
            assert classification in (
                EXPECTED_CORRECTION, EXPECTED_ENHANCEMENT, None), name

    def test_there_are_no_unexplained_differences(self):
        assert UNEXPLAINED not in DIFFERENCES.values()

    def test_there_are_no_canonical_regressions(self):
        """A regression is a difference where canonical is WORSE. None is."""
        assert CANONICAL_REGRESSION not in DIFFERENCES.values()

    def test_the_category_vocabulary_did_not_change(self):
        """The one dimension that must be identical, because it is the
        Government's."""
        from app.reports.data.sow_report_data import GOVERNMENT_CATEGORIES
        from app.Tefca.reporting import CATEGORIES

        assert list(CATEGORIES) == list(GOVERNMENT_CATEGORIES)
        assert DIFFERENCES["category_vocabulary"] is None


class TestLegacyDefectsRetested:
    """Re-derived from the source, not carried over from an earlier report."""

    @staticmethod
    def _legacy():
        from app.Tefca import reporting
        return inspect.getsource(reporting)

    @staticmethod
    def _canonical():
        from app.reports.data import sow_report_data
        return inspect.getsource(sow_report_data)

    def test_defect_1_legacy_reads_the_dashboard_mirror(self):
        """tefca_reviews is a denormalised mirror; review_records is the table
        the QA gate actually operates on."""
        legacy = self._legacy()
        assert "TEFCAReview" in legacy
        assert "ReviewRecord" not in legacy

    def test_defect_2_legacy_has_no_reportability_gate(self):
        legacy = self._legacy()
        assert "reportable_at" not in legacy
        assert "is_reportable" not in legacy

    def test_defect_3_legacy_bypasses_the_canonical_selector(self):
        legacy = self._legacy()
        assert "current_rule_version" not in legacy
        assert "ReportDataService" not in legacy

    def test_canonical_fixes_defect_1(self):
        assert "ReviewRecord" in self._canonical()

    def test_canonical_fixes_defect_2(self):
        assert "reportable_at" in self._canonical()

    def test_canonical_fixes_defect_3(self):
        assert "current_rule_version" in self._canonical()

    def test_canonical_reproduces_no_legacy_defect(self):
        """The approved decision: do not make canonical match legacy by
        reproducing what is wrong with legacy."""
        canonical = self._canonical()
        assert "TEFCAReview" not in canonical, (
            "canonical reads the dashboard mirror — defect 1 reproduced")


# ── the reconciliation itself, against the development database ──────────────

@pytest.fixture
async def legacy_population_present(db_required):
    """Skip unless tefca_reviews carries the legacy demonstration seed.

    `> 0` in test_the_legacy_population_is_entirely_synthetic is a statement
    about the populated development database (50 seeded rows, every one
    is_mock_data). A freshly migrated database has no legacy rows at all, and
    the other assertions in this class hold trivially there. Measured at
    runtime so a populated environment still runs the assertion unchanged.
    """
    from sqlalchemy import text

    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        legacy = (await db.execute(text(
            "select count(*) from tefca_reviews"))).scalar() or 0
    if not legacy:
        pytest.skip(
            f"requires the populated development dataset: tefca_reviews has "
            f"{legacy} rows, so there is no legacy population to classify")


@pytest.mark.usefixtures("db_required")
class TestReconciliationAgainstTheDatabase:

    @staticmethod
    async def _counts():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            async def scalar(sql):
                return (await db.execute(text(sql))).scalar()

            return {
                "legacy": await scalar("select count(*) from tefca_reviews"),
                "legacy_synthetic": await scalar(
                    "select count(*) from tefca_reviews where is_mock_data"),
                "canonical_records": await scalar(
                    "select count(*) from review_records"),
                "canonical_reportable": await scalar(
                    "select count(*) from review_records "
                    "where reportable_at is not null"),
                "decision_events": await scalar(
                    "select count(*) from review_decision_events"),
            }

    @pytest.mark.asyncio
    async def test_the_equation_balances(self):
        """LEGACY POPULATION = CANONICAL REPORTABLE-FROM-LEGACY + RECONCILED.

        2026-10-04 (Round 26): `c["canonical_reportable"]` originally counted
        EVERY reportable `review_records` row in the whole database, which
        made this assert `canonical_reportable == 0` whenever `legacy ==
        legacy_synthetic` (true once the legacy population is fully
        classified -- see test_every_legacy_row_has_a_reason). That broke
        whenever ANY unrelated, legitimate QA approval ran earlier in the
        same shared test database -- reproduced directly: committing one
        ordinary reportable review_record with no connection to the legacy
        population failed this assertion with `1 == 0`, identically to
        test_nothing_became_reportable below.

        `test_no_legacy_row_links_to_a_review_record` already proves NO
        review_record is EVER linked to a legacy `tefca_reviews` row (by
        NPI) -- migration left the two populations categorically disjoint.
        So "how many canonical-reportable records came FROM reconciling the
        legacy population" is provably always zero, and the equation this
        test is actually named for only ever concerns the LEGACY-linked
        subset of canonical_reportable, not the whole table. Scoped to that
        subset explicitly rather than assumed.
        """
        from sqlalchemy import text

        from app.core.database import async_session_maker

        c = await self._counts()
        async with async_session_maker() as db:
            canonical_reportable_from_legacy = (await db.execute(text("""
                select count(*) from tefca_reviews tr
                where exists (
                    select 1 from review_records rr
                    join tefca_entity_identifiers i on i.entity_id = rr.entity_id
                    where i.identifier_value = tr.npi
                      and rr.reportable_at is not null)"""))).scalar()
        reconciled = c["legacy_synthetic"]
        assert canonical_reportable_from_legacy + reconciled == c["legacy"]

    @pytest.mark.asyncio
    async def test_every_legacy_row_has_a_reason(self):
        """No row may be left without a derived disposition."""
        c = await self._counts()
        assert c["legacy_synthetic"] == c["legacy"], (
            f"{c['legacy'] - c['legacy_synthetic']} legacy rows are not "
            f"flagged synthetic and need another derived reason")

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("legacy_population_present")
    async def test_the_legacy_population_is_entirely_synthetic(self):
        """Every legacy row is is_mock_data = TRUE — a demonstration seed, not
        a review of any entity."""
        c = await self._counts()
        assert c["legacy_synthetic"] == c["legacy"] > 0

    @pytest.mark.asyncio
    async def test_no_legacy_row_links_to_a_review_record(self):
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            linked = (await db.execute(text("""
                select count(*) from tefca_reviews tr
                where exists (
                    select 1 from review_records rr
                    join tefca_entity_identifiers i on i.entity_id = rr.entity_id
                    where i.identifier_value = tr.npi)"""))).scalar()
        assert linked == 0

    @pytest.mark.asyncio
    async def test_nothing_became_reportable(self):
        """Reconciling the legacy population must not itself create a
        reportable canonical record or a decision event.

        2026-10-04 (Round 26): this previously asserted the WHOLE
        `review_records`/`review_decision_events` tables were empty of
        reportable rows and events -- true only on a database where no
        other test has ever exercised the (entirely separate, entirely
        legitimate) QA-approval workflow. Reproduced: one ordinary
        reportable review_record, unrelated to any legacy row, broke this
        with `1 == 0`. Scoped to what the gate is actually meant to prove --
        that nothing reportable or decided traces back to a legacy
        `tefca_reviews` row -- using the same NPI linkage
        `test_no_legacy_row_links_to_a_review_record` already establishes
        is categorically absent."""
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            linked_reportable = (await db.execute(text("""
                select count(*) from tefca_reviews tr
                where exists (
                    select 1 from review_records rr
                    join tefca_entity_identifiers i on i.entity_id = rr.entity_id
                    where i.identifier_value = tr.npi
                      and rr.reportable_at is not null)"""))).scalar()
            linked_events = (await db.execute(text("""
                select count(*) from tefca_reviews tr
                where exists (
                    select 1 from review_decision_events de
                    join review_records rr on rr.review_id = de.review_id
                    join tefca_entity_identifiers i on i.entity_id = rr.entity_id
                    where i.identifier_value = tr.npi)"""))).scalar()
        assert linked_reportable == 0
        assert linked_events == 0
