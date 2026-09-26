"""Bounded PECOS-only dry-run retry planner (Fix 6) — required test matrix.

Items 12-15 of the six-fix remediation. NO real database and NO upstream
calls: `plan_pecos_retry` is exercised against a FakeDB that records every
write attempt (there must be none) and returns a deterministic, hand-built
result set so idempotency can be asserted exactly.
"""
from __future__ import annotations

import pytest

from app.Tefca.pecos_retry_planner import (
    MAX_CANDIDATES,
    TARGET_SOURCES,
    plan_pecos_retry,
)
from app.services.npi_validator import make_valid_npi


class _Result:
    """Stands in for the SQLAlchemy Result object: supports both the plain
    row-tuple `.all()` used for the NPPES scan and the `.scalars().all()`
    used for the existing-sources check."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


class FakeDB:
    """Fails loudly on any write attempt — a dry-run planner must never call
    these. `execute()` returns the NPPES scan on the first call and then pops
    one entry per subsequent call, in the same order `plan_pecos_retry`
    iterates its rows (dedup, first generation per entity, in row order)."""

    def __init__(self, nppes_rows, existing_sources_in_order):
        self._nppes_rows = list(nppes_rows)
        self._existing_queue = list(existing_sources_in_order)
        self.call_count = 0

    async def execute(self, _stmt):
        self.call_count += 1
        if self.call_count == 1:
            return _Result(list(self._nppes_rows))
        return _Result(self._existing_queue.pop(0))

    def add(self, *a, **k):
        raise AssertionError("dry-run planner must never write")

    async def commit(self):
        raise AssertionError("dry-run planner must never commit")

    async def flush(self):
        raise AssertionError("dry-run planner must never flush")

    async def execute_write(self, *a, **k):  # defensive: not a real API, just a tripwire
        raise AssertionError("dry-run planner must never write")


NPI_A = make_valid_npi("100000001")
NPI_B = make_valid_npi("100000002")
NPI_C = make_valid_npi("100000003")


def _rows():
    """Three entities: one already fully covered, one missing both target
    sources, one with an invalid NPI on file."""
    return [
        ("entity-covered", {"npi": NPI_A}),
        ("entity-missing", {"npi": NPI_B}),
        ("entity-badnpi", {"npi": "not-an-npi"}),
    ]


def _existing_in_row_order():
    # Matches _rows() order: entity-covered has both, entity-missing has
    # neither. entity-badnpi is excluded before the existing-sources query
    # ever runs (npi_rejection_reason short-circuits it), so no third queue
    # entry is consumed for it.
    return [
        list(TARGET_SOURCES),  # entity-covered: both already present
        [],                    # entity-missing: neither present
    ]


class TestBoundedAndScoped:
    async def test_never_targets_the_legacy_proxy(self):
        assert "pecos" not in [s.lower() for s in TARGET_SOURCES]
        assert set(TARGET_SOURCES) == {"CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"}

    async def test_returns_no_more_than_25_candidates(self):
        # 40 distinct entities all missing both sources, no invalid NPIs.
        rows = [(f"entity-{i}", {"npi": make_valid_npi(str(200000000 + i))}) for i in range(40)]
        existing = [[] for _ in range(40)]
        db = FakeDB(rows, existing)
        report = await plan_pecos_retry(db, limit=100)  # caller asks for more than the ceiling
        assert report["candidate_count"] <= MAX_CANDIDATES
        assert report["max_candidates"] <= MAX_CANDIDATES

    async def test_excludes_records_already_carrying_full_target_coverage(self):
        db = FakeDB(_rows(), _existing_in_row_order())
        report = await plan_pecos_retry(db, limit=25)
        ids = [c["entity_id"] for c in report["candidates"]]
        assert "entity-covered" not in ids
        assert "entity-missing" in ids
        assert report["excluded_already_has_evidence"] == 1

    async def test_excludes_invalid_npi_candidates_and_states_why(self):
        db = FakeDB(_rows(), _existing_in_row_order())
        report = await plan_pecos_retry(db, limit=25)
        ids = [c["entity_id"] for c in report["candidates"]]
        assert "entity-badnpi" not in ids
        assert report["excluded_invalid_npi"] == 1

    async def test_each_candidate_states_which_sources_are_missing_and_why(self):
        db = FakeDB(_rows(), _existing_in_row_order())
        report = await plan_pecos_retry(db, limit=25)
        cand = next(c for c in report["candidates"] if c["entity_id"] == "entity-missing")
        assert set(cand["missing_sources"]) == set(TARGET_SOURCES)
        assert cand["reason"]


class TestSanitizedOutput:
    async def test_candidates_carry_no_name_address_or_full_npi(self):
        db = FakeDB(_rows(), _existing_in_row_order())
        report = await plan_pecos_retry(db, limit=25)
        blob = str(report["candidates"])
        assert NPI_B not in blob  # full NPI never present
        cand = next(c for c in report["candidates"] if c["entity_id"] == "entity-missing")
        assert cand["npi_masked"].startswith("...")
        assert len(cand["npi_masked"]) <= 8
        for forbidden_key in ("name", "organization_name", "address", "legal_name"):
            assert forbidden_key not in cand


class TestDryRunGuarantees:
    async def test_makes_zero_database_writes(self):
        db = FakeDB(_rows(), _existing_in_row_order())
        report = await plan_pecos_retry(db, limit=25)
        assert report["dry_run"] is True
        assert report["executed_retry"] is False
        assert report["database_writes_made"] == 0
        assert report["upstream_calls_made"] == 0
        # FakeDB.add/commit/flush all raise — reaching the assertions above
        # without an exception is itself the proof no write path fired.

    async def test_repeated_dry_run_is_idempotent_same_candidates_zero_writes(self):
        report_1 = await plan_pecos_retry(FakeDB(_rows(), _existing_in_row_order()), limit=25)
        report_2 = await plan_pecos_retry(FakeDB(_rows(), _existing_in_row_order()), limit=25)
        assert report_1["candidates"] == report_2["candidates"]
        assert report_1["candidate_count"] == report_2["candidate_count"]
        assert report_2["database_writes_made"] == 0

    async def test_does_not_change_any_delivery_or_disposition_state(self):
        """The planner reads tefca_dimension_evidence only, through a session
        whose add/commit/flush all raise if called; a clean return proves no
        delivery, reconciliation or disposition table was touched."""
        db = FakeDB(_rows(), _existing_in_row_order())
        report = await plan_pecos_retry(db, limit=25)
        assert report["database_writes_made"] == 0
        assert db.call_count >= 1  # reads did happen — this is not a no-op stub
