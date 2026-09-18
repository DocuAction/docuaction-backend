"""Pins the 2026-09-17 report-contamination defect at its actual source:
`ReportDataService.get_scope_summary`'s `records_received` and
`issues_identified` used to run unconditional `COUNT(*)` over the WHOLE
registry (`tefca_reg_entities`, `tefca_entity_findings`) regardless of
`review_cycle_id` — traced live to report DA-ARC-2026-014, generated with
review_cycle_id=null, showing 25,983 registry-wide entities / 1,129
registry-wide open findings under what a user took to be a 50-record
delivery's report.

These tests exercise the REAL `ReportDataService.get_scope_summary` (not a
stub) against a fake session that tracks every statement issued, so the
assertion is structural: when a review cycle DOES resolve, the registry-wide
tables must never be queried at all, and the scoped, cycle-only numbers must
be the ones returned.
"""

from __future__ import annotations

import uuid

import pytest

from app.reports.data.report_data_service import ReportDataService
from app.tefca_registry import models as reg


class _Result:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class ScopedFakeDB:
    """Answers `db.get` and `db.execute` by inspecting WHAT is being asked
    for — the model class for `get`, the queried table's name (compiled from
    the statement) for `execute` — never by call order, so a refactor that
    reorders the real function's internal calls does not spuriously break
    this test.
    """

    REGISTRY_WIDE_ENTITY_COUNT = 25983  # the exact figure DA-ARC-2026-014 showed
    REGISTRY_WIDE_ISSUE_COUNT = 1129    # ditto

    def __init__(self, *, cycle, sample, review_records, scoped_issue_count):
        self._cycle = cycle
        self._sample = sample
        self._review_records = review_records
        self._scoped_issue_count = scoped_issue_count
        self.unscoped_registry_query_issued = False

    async def get(self, model, ident):
        if model is reg.ReviewCycle:
            return self._cycle if str(getattr(self._cycle, "id", None)) == str(ident) else None
        if model is reg.ReviewSample:
            return self._sample if str(getattr(self._sample, "id", None)) == str(ident) else None
        return None

    async def execute(self, stmt, params=None):
        text = str(stmt)
        # `_review_records`: SELECT ... FROM review_records ...
        if "review_records" in text and "SELECT" in text.upper():
            return _Result(rows=self._review_records)
        # The registry-wide, UNSCOPED entity/finding counts this defect wrote.
        # Both are a plain, unfiltered COUNT(*) over the whole table — no
        # WHERE naming a specific entity/sample/cycle appears in the compiled
        # SQL at all when the bug is present.
        if "tefca_reg_entities" in text and "count" in text.lower():
            self.unscoped_registry_query_issued = True
            return _Result(scalar=self.REGISTRY_WIDE_ENTITY_COUNT)
        if ("tefca_entity_findings" in text and "count" in text.lower()
                and "entity_id IN" not in text and "entity_id in" not in text.lower()):
            self.unscoped_registry_query_issued = True
            return _Result(scalar=self.REGISTRY_WIDE_ISSUE_COUNT)
        # The SCOPED finding count this fix adds: same table, but filtered to
        # the cycle's own entity ids.
        if "tefca_entity_findings" in text:
            return _Result(scalar=self._scoped_issue_count)
        return _Result(scalar=0, rows=[])


class _Cycle:
    def __init__(self, id_, sample_id):
        self.id = id_
        self.sample_id = sample_id
        self.cycle_start = None
        self.cycle_end = None
        self.cycle_type = "quarterly"


class _Sample:
    def __init__(self, id_, population_size):
        self.id = id_
        self.population_size = population_size


class _Record:
    def __init__(self, entity_id):
        self.entity_id = entity_id
        self.reclassified_to = None
        self.classification_bucket = "B1"


@pytest.mark.asyncio
async def test_scope_summary_uses_the_cycles_own_population_not_the_registry():
    """The exact defect: `records_received` for a delivery's review cycle must
    be that cycle's OWN drawn population (50, in this fixture) — never the
    unrelated registry-wide entity count (25,983)."""
    cycle_id = str(uuid.uuid4())
    sample_id = uuid.uuid4()
    cycle = _Cycle(cycle_id, sample_id)
    sample = _Sample(sample_id, population_size=50)
    records = [_Record(f"entity-{i}") for i in range(45)]  # the drawn/verified sample
    db = ScopedFakeDB(cycle=cycle, sample=sample, review_records=records,
                      scoped_issue_count=12)

    service = ReportDataService(db)
    scope = await service.get_scope_summary(cycle_id)

    assert scope["records_received"] == 50, (
        f"expected the cycle's own population (50), got {scope['records_received']} "
        f"— this is the registry-wide figure if the fix regressed")
    assert scope["records_received"] != ScopedFakeDB.REGISTRY_WIDE_ENTITY_COUNT
    assert scope["issues_identified"] == 12
    assert scope["issues_identified"] != ScopedFakeDB.REGISTRY_WIDE_ISSUE_COUNT
    assert db.unscoped_registry_query_issued is False, (
        "get_scope_summary queried the whole registry even though a valid "
        "review_cycle_id was supplied — the exact 2026-09-17 defect")


@pytest.mark.asyncio
async def test_scope_summary_with_no_cycle_id_still_uses_the_documented_all_records_default():
    """Omitting review_cycle_id entirely is the SEPARATE, intentional,
    already-tested system-wide report (see GenerateReportRequest's own
    docstring: "Omit for all records") — that default must be unchanged."""
    db = ScopedFakeDB(cycle=None, sample=None, review_records=[], scoped_issue_count=0)
    service = ReportDataService(db)
    scope = await service.get_scope_summary(None)

    assert scope["records_received"] == ScopedFakeDB.REGISTRY_WIDE_ENTITY_COUNT
    assert scope["issues_identified"] == ScopedFakeDB.REGISTRY_WIDE_ISSUE_COUNT
    assert db.unscoped_registry_query_issued is True


@pytest.mark.asyncio
async def test_scope_summary_reports_zero_not_the_registry_when_cycle_has_no_sample():
    """A cycle id that resolves but whose sample is gone/unavailable must read
    as zero, not silently widen to the registry — the scoped branch never
    falls back to the unscoped query."""
    cycle_id = str(uuid.uuid4())
    cycle = _Cycle(cycle_id, sample_id=uuid.uuid4())  # sample_id points nowhere
    db = ScopedFakeDB(cycle=cycle, sample=None, review_records=[], scoped_issue_count=0)
    service = ReportDataService(db)
    scope = await service.get_scope_summary(cycle_id)

    assert scope["records_received"] == 0
    assert scope["records_received"] != ScopedFakeDB.REGISTRY_WIDE_ENTITY_COUNT
    assert db.unscoped_registry_query_issued is False
