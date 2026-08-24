"""Phase 7 — report data validation. Every metric must be derivable.

Two defects motivated this file, both silent, both in the one function every
report reads its population through:

  1. Only the current evidence version may reach a report, and unversioned rows
     must not. 716 unversioned rows carry an automatic PASS — a disposition the
     Phase 6 architecture forbids because no source may assert a pass without a
     human — and none of them can be attributed to a rule generation.

  2. The de-dup keyed on (entity, dimension) and discarded 70,698 of 188,528
     observations. Every entity has an ADDRESS observation from NPPES *and* one
     from PPEF, and three EXCLUSION_REVOCATION observations from three sources.
     Those are not duplicates — the disagreement between them is the finding.
     And because `generation_timestamp` is NULL on every population row, the
     tie-break compared "" to "" and the survivor was whichever row the database
     returned first, so the number moved between runs with no visible cause.

The figures asserted below are DERIVED, never hard-coded into production logic.
The constants here are expectations of a test, which is where an expected value
belongs; production code that hard-coded 8,584 would be asserting a development
result as a fact about the world.

DEVELOPMENT/TEST DATA. Nothing here is an ONC finding.
"""
from __future__ import annotations

from collections import Counter

import pytest

from app.Tefca.evidence_version import current_rule_version, historical_rule_versions

# Expected development-data figures, independently reconciled in
# docs/phase6_evidence_correction.md. Not ONC findings.
POPULATION = 23_566
OBSERVATIONS = 188_528
NPPES_CONFLICTS = 8_584
PPEF_CONFLICTS = 1_842
CONFLICTING_ENTITIES = 9_032
BOTH_SOURCES = 1_394


class TestTheSelectorItself:
    """Version scoping, asserted without touching a database."""

    def test_the_current_version_is_the_one_reports_read(self):
        assert current_rule_version() == "phase6-bulk-1.1.0"

    def test_the_defective_generation_is_superseded_not_deleted(self):
        """1.0.0 stays readable as history; it just may not reach a report."""
        assert "phase6-bulk-1.0.0" in historical_rule_versions()

    def test_current_and_superseded_never_overlap(self):
        assert current_rule_version() not in historical_rule_versions()


@pytest.mark.usefixtures("db_required")
class TestDerivedFromPersistedEvidence:
    """Everything here goes through the canonical service, not raw SQL.

    Reading the numbers with a hand-written query would test the query, not the
    path a report actually takes. The point is that the *reporting* path returns
    these figures.
    """

    @staticmethod
    async def _rows():
        from app.core.database import async_session_maker
        from app.reports.data.report_data_service import ReportDataService

        async with async_session_maker() as db:
            svc = ReportDataService(db)
            rows = await svc._dimension_rows(None)
            return rows, dict(svc.evidence_scope)

    @pytest.mark.asyncio
    async def test_no_observation_is_silently_dropped(self):
        """The regression: 37.5% of the evidence used to disappear here."""
        rows, scope = await self._rows()
        assert scope["observations_read"] == scope["observations_reported"]
        assert scope["collapsed_duplicates"] == 0
        assert len(rows) == OBSERVATIONS

    @pytest.mark.asyncio
    async def test_the_scope_states_what_it_excluded(self):
        """A report that narrows its own population has to say so."""
        _, scope = await self._rows()
        assert scope["rule_version"] == current_rule_version()
        assert scope["superseded_versions_excluded"] == historical_rule_versions()
        assert "source" in scope["dedup_key"]

    @pytest.mark.asyncio
    async def test_only_the_current_version_reaches_the_report(self):
        rows, _ = await self._rows()
        assert {r.rule_version for r in rows} == {current_rule_version()}

    @pytest.mark.asyncio
    async def test_no_unversioned_row_reaches_the_report(self):
        """716 unversioned rows carry an automatic PASS. None may be reported."""
        rows, _ = await self._rows()
        assert all(r.rule_version is not None for r in rows)

    @pytest.mark.asyncio
    async def test_the_report_asserts_no_automatic_pass_or_fail(self):
        rows, _ = await self._rows()
        assert not [r for r in rows if r.disposition in ("PASS", "FAIL")]

    @pytest.mark.asyncio
    async def test_both_address_sources_survive(self):
        """One of the two used to be discarded, arbitrarily."""
        rows, _ = await self._rows()
        addr = [r for r in rows if "ADDRESS" in (r.evidence_dimension or "")]
        by_source = Counter(r.source for r in addr)
        assert by_source["NPPES"] == POPULATION
        assert by_source["CMS_PPEF_PRACTICE_LOCATION"] == POPULATION

    @pytest.mark.asyncio
    async def test_the_nppes_conflict_figure_derives_to_8584(self):
        """Derived through the reporting path — not read from a constant."""
        rows, _ = await self._rows()
        derived = len([r for r in rows
                       if "ADDRESS" in (r.evidence_dimension or "")
                       and r.source == "NPPES"
                       and r.dimension_disposition == "CONFLICT"])
        assert derived == NPPES_CONFLICTS

    @pytest.mark.asyncio
    async def test_observations_and_entities_are_different_quantities(self):
        """The three address figures were being used interchangeably.

        8,584 NPPES conflict observations; 1,842 PPEF; 10,426 observations in
        total; 9,032 distinct entities, of which 1,394 conflict on both sources.
        The arithmetic has to close, or one of them is wrong.
        """
        rows, _ = await self._rows()
        conflicts = [r for r in rows
                     if "ADDRESS" in (r.evidence_dimension or "")
                     and r.dimension_disposition == "CONFLICT"]
        by_source = Counter(r.source for r in conflicts)
        assert by_source["NPPES"] == NPPES_CONFLICTS
        assert by_source["CMS_PPEF_PRACTICE_LOCATION"] == PPEF_CONFLICTS

        entities = {r.entity_id for r in conflicts}
        assert len(entities) == CONFLICTING_ENTITIES

        per_entity = Counter(r.entity_id for r in conflicts)
        both = len([e for e, c in per_entity.items() if c > 1])
        assert both == BOTH_SOURCES
        assert len(entities) + both == len(conflicts)

    @pytest.mark.asyncio
    async def test_each_source_answers_once_per_entity_and_dimension(self):
        """The de-dup key is only correct if it is actually unique."""
        rows, _ = await self._rows()
        keys = Counter((r.entity_id, r.evidence_dimension, r.source) for r in rows)
        assert not [k for k, c in keys.items() if c > 1]

    @pytest.mark.asyncio
    async def test_every_dimension_reconciles_to_the_population(self):
        """Per source, each dimension answers for every entity exactly once."""
        rows, _ = await self._rows()
        per = Counter((r.evidence_dimension, r.source) for r in rows)
        assert per, "no evidence read"
        for (dimension, source), count in per.items():
            assert count == POPULATION, (
                f"{dimension}/{source} answered for {count} entities, "
                f"expected {POPULATION}")

    @pytest.mark.asyncio
    async def test_the_read_is_reproducible(self):
        """Two reads of frozen, append-only evidence must agree exactly.

        This is the property the NULL-timestamp tie-break destroyed: the same
        query returned a different survivor depending on row order.
        """
        first, _ = await self._rows()
        second, _ = await self._rows()
        key = lambda rs: sorted(  # noqa: E731
            (r.entity_id, r.evidence_dimension, r.source, r.dimension_disposition)
            for r in rs)
        assert key(first) == key(second)


# ═══ The same regression, without a database ═════════════════════════════════
#
# The class above only runs where a populated development database is reachable,
# and the harness points DATABASE_URL at a test instance that does not exist —
# so on most runs it skips. The de-dup defect is pure logic, though, and logic
# can be pinned with a fixture. These run everywhere.

class _Row:
    """Enough of a TEFCADimensionEvidence to exercise the selector."""

    def __init__(self, entity_id, dimension, source, disposition="OBSERVED",
                 rule_version="phase6-bulk-1.1.0", generation_timestamp=None,
                 created_at=None, row_id=""):
        self.entity_id = entity_id
        self.evidence_dimension = dimension
        self.source = source
        self.dimension_disposition = disposition
        self.disposition = None
        self.rule_version = rule_version
        self.generation_timestamp = generation_timestamp
        self.created_at = created_at
        self.id = row_id


class _FakeDB:
    """Returns a fixed row set and ignores the filter, so the test measures the
    de-dup rather than the WHERE clause."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_a, **_k):
        rows = self._rows

        class _Result:
            def scalars(self_inner):
                class _S:
                    def all(self_s): return rows
                return _S()
        return _Result()


def _service(rows):
    from app.reports.data.report_data_service import ReportDataService
    return ReportDataService(_FakeDB(rows))


class TestDedupLogic:

    @pytest.mark.asyncio
    async def test_two_sources_on_one_dimension_both_survive(self):
        """The exact shape that lost 70,698 rows: NPPES and PPEF both answer
        the ADDRESS question for the same entity, and disagree."""
        rows = [
            _Row("E1", "D4_ADDRESS", "NPPES", "CONFLICT"),
            _Row("E1", "D4_ADDRESS", "CMS_PPEF_PRACTICE_LOCATION", "NORMALIZED_MATCH"),
        ]
        kept = await _service(rows)._dimension_rows(None)
        assert len(kept) == 2
        assert {r.source for r in kept} == {"NPPES", "CMS_PPEF_PRACTICE_LOCATION"}

    @pytest.mark.asyncio
    async def test_three_sources_on_one_dimension_all_survive(self):
        """EXCLUSION_REVOCATION has three sources per entity — 70,698 rows over
        23,566 entities. Two of the three used to be discarded."""
        rows = [_Row("E1", "D5_EXCLUSION_REVOCATION", s)
                for s in ("OIG_LEIE", "SAM_GOV", "CMS_REVOCATION")]
        kept = await _service(rows)._dimension_rows(None)
        assert len(kept) == 3

    @pytest.mark.asyncio
    async def test_a_genuine_duplicate_is_still_collapsed(self):
        """Same entity, same dimension, same source, twice. That IS a duplicate."""
        rows = [
            _Row("E1", "D4_ADDRESS", "NPPES", "CONFLICT",
                 generation_timestamp="2026-01-01", row_id="a"),
            _Row("E1", "D4_ADDRESS", "NPPES", "EXACT_MATCH",
                 generation_timestamp="2026-06-01", row_id="b"),
        ]
        kept = await _service(rows)._dimension_rows(None)
        assert len(kept) == 1
        # the newer generation wins
        assert kept[0].dimension_disposition == "EXACT_MATCH"

    @pytest.mark.asyncio
    async def test_a_tie_with_null_timestamps_resolves_deterministically(self):
        """The population rows all have generation_timestamp = NULL. The old
        tie-break compared "" to "" and let row order decide, so the same query
        returned different answers on different runs."""
        a = _Row("E1", "D4_ADDRESS", "NPPES", "CONFLICT", row_id="aaa")
        b = _Row("E1", "D4_ADDRESS", "NPPES", "EXACT_MATCH", row_id="bbb")
        forward = await _service([a, b])._dimension_rows(None)
        reverse = await _service([b, a])._dimension_rows(None)
        assert len(forward) == len(reverse) == 1
        assert forward[0].dimension_disposition == reverse[0].dimension_disposition

    @pytest.mark.asyncio
    async def test_the_scope_reports_what_it_collapsed(self):
        rows = [
            _Row("E1", "D4_ADDRESS", "NPPES", row_id="a"),
            _Row("E1", "D4_ADDRESS", "NPPES", row_id="b"),
            _Row("E1", "D4_ADDRESS", "CMS_PPEF_PRACTICE_LOCATION", row_id="c"),
        ]
        svc = _service(rows)
        await svc._dimension_rows(None)
        assert svc.evidence_scope["observations_read"] == 3
        assert svc.evidence_scope["observations_reported"] == 2
        assert svc.evidence_scope["collapsed_duplicates"] == 1

    @pytest.mark.asyncio
    async def test_scope_is_populated_even_when_evidence_is_unavailable(self):
        """A failed read must still say what it was scoped to, or a zero looks
        like a real count of zero."""
        class _Broken:
            async def execute(self, *_a, **_k):
                raise RuntimeError("connection reset")

        from app.reports.data.report_data_service import ReportDataService
        svc = ReportDataService(_Broken())
        assert await svc._dimension_rows(None) == []
        assert svc.evidence_scope["unavailable"] is True
        assert svc.evidence_scope["rule_version"] == current_rule_version()
