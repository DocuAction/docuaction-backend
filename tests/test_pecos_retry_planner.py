"""Bounded PECOS-only dry-run retry planner (Fix 6) — required test matrix.

Items 12-15 of the six-fix remediation, plus the follow-up corrections:
constant (non-N+1) query count, and sanitized/opaque candidate identifiers.

NO real database and NO upstream calls: `plan_pecos_retry` is exercised
against a FakeDB that records every write attempt (there must be none) and
counts every `execute()` call (there must be exactly two, regardless of how
many rows are scanned or how many candidates qualify).
"""
from __future__ import annotations

import hashlib

import pytest

from app.Tefca.pecos_retry_planner import (
    MAX_CANDIDATES,
    SCAN_WINDOW,
    TARGET_SOURCES,
    _opaque_ref,
    plan_pecos_retry,
)
from app.services.npi_validator import make_valid_npi


class _Result:
    """Stands in for the SQLAlchemy Result object used by both queries the
    planner issues: a plain row-tuple `.all()`."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeDB:
    """Fails loudly on any write attempt — a dry-run planner must never call
    these. `execute()` returns the NPPES scan on the first call and the bulk
    existing-sources result on the second — and must NEVER be called a third
    time, which is exactly the N+1 regression this fake is built to catch."""

    def __init__(self, nppes_rows, existing_rows):
        self._nppes_rows = list(nppes_rows)
        self._existing_rows = list(existing_rows)
        self.call_count = 0

    async def execute(self, _stmt):
        self.call_count += 1
        if self.call_count == 1:
            return _Result(list(self._nppes_rows))
        if self.call_count == 2:
            return _Result(list(self._existing_rows))
        raise AssertionError(
            f"plan_pecos_retry issued a {self.call_count}th database query — "
            "this is the N+1 regression; it must issue exactly two, ever."
        )

    def add(self, *a, **k):
        raise AssertionError("dry-run planner must never write")

    async def commit(self):
        raise AssertionError("dry-run planner must never commit")

    async def flush(self):
        raise AssertionError("dry-run planner must never flush")


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


def _existing_rows_bulk():
    # ONE flat result set covering every entity in _rows(), as the single
    # bulk query now returns — not a per-entity queue.
    return [
        ("entity-covered", "CMS_PPEF_ENROLLMENT"),
        ("entity-covered", "CMS_REVOCATION"),
        # entity-missing: no rows at all.
    ]


class TestConstantQueryCount:
    """Item 1 of the follow-up: prove the N+1 pattern is gone."""

    async def test_exactly_two_queries_for_a_typical_run(self):
        db = FakeDB(_rows(), _existing_rows_bulk())
        await plan_pecos_retry(db, limit=25)
        assert db.call_count == 2

    async def test_query_count_does_not_grow_with_candidate_count(self):
        """The regression this guards against: query count scaling with the
        number of scanned/candidate rows. 200 distinct, all-missing entities
        must cost exactly the same two queries as three."""
        rows = [(f"entity-{i}", {"npi": make_valid_npi(str(300000000 + i))}) for i in range(200)]
        db = FakeDB(rows, existing_rows=[])  # nothing pre-covered
        report = await plan_pecos_retry(db, limit=25)
        assert db.call_count == 2
        assert report["database_queries_made"] == 2
        assert report["candidate_count"] == MAX_CANDIDATES  # bounded output…
        assert report["entities_scanned"] == 200             # …not bounded scanning

    async def test_single_query_when_every_npi_is_invalid(self):
        """No valid entities survive filtering -> the second (bulk-evidence)
        query has nothing to ask and is skipped entirely, not issued empty."""
        rows = [("entity-1", {"npi": "not-an-npi"}), ("entity-2", {"npi": ""})]
        db = FakeDB(rows, existing_rows=[])
        report = await plan_pecos_retry(db, limit=25)
        assert db.call_count == 1
        assert report["database_queries_made"] == 1
        assert report["candidate_count"] == 0

    async def test_never_loads_the_full_24589_record_population(self):
        """The initial scan is bounded by SCAN_WINDOW, which is far smaller
        than a real delivery — the planner must ask for at most SCAN_WINDOW
        rows no matter how large `limit` is."""
        captured_limits = []

        class LimitCapturingResult(_Result):
            pass

        class CapturingDB(FakeDB):
            async def execute(self, stmt):
                # SQLAlchemy Select objects expose their LIMIT clause via
                # ._limit_clause / .get_final_froms() in different versions;
                # the portable check is simply that compiling the statement
                # contains "LIMIT" bounded well under a full-population size.
                compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                captured_limits.append(compiled)
                return await super().execute(stmt)

        db = CapturingDB(_rows(), _existing_rows_bulk())
        await plan_pecos_retry(db, limit=25)
        first_query_sql = captured_limits[0]
        assert f"LIMIT {SCAN_WINDOW}" in first_query_sql
        assert SCAN_WINDOW < 24589


class TestBoundedAndScoped:
    async def test_never_targets_the_legacy_proxy(self):
        assert "pecos" not in [s.lower() for s in TARGET_SOURCES]
        assert set(TARGET_SOURCES) == {"CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"}

    async def test_returns_no_more_than_25_candidates(self):
        rows = [(f"entity-{i}", {"npi": make_valid_npi(str(200000000 + i))}) for i in range(40)]
        db = FakeDB(rows, existing_rows=[])
        report = await plan_pecos_retry(db, limit=100)  # caller asks for more than the ceiling
        assert report["candidate_count"] <= MAX_CANDIDATES
        assert report["max_candidates"] <= MAX_CANDIDATES

    async def test_excludes_records_already_carrying_full_target_coverage(self):
        db = FakeDB(_rows(), _existing_rows_bulk())
        report = await plan_pecos_retry(db, limit=25)
        refs = [c["candidate_ref"] for c in report["candidates"]]
        assert _opaque_ref("entity-covered") not in refs
        assert _opaque_ref("entity-missing") in refs
        assert report["excluded_already_has_evidence"] == 1

    async def test_excludes_invalid_npi_candidates_and_states_why(self):
        db = FakeDB(_rows(), _existing_rows_bulk())
        report = await plan_pecos_retry(db, limit=25)
        refs = [c["candidate_ref"] for c in report["candidates"]]
        assert _opaque_ref("entity-badnpi") not in refs
        assert report["excluded_invalid_npi"] == 1

    async def test_each_candidate_states_which_sources_are_missing_and_why(self):
        db = FakeDB(_rows(), _existing_rows_bulk())
        report = await plan_pecos_retry(db, limit=25)
        cand = next(c for c in report["candidates"] if c["candidate_ref"] == _opaque_ref("entity-missing"))
        assert set(cand["missing_sources"]) == set(TARGET_SOURCES)
        assert cand["reason"]


class TestSanitizedOutput:
    """Item 3 of the follow-up: no full NPI, name, address, or raw entity id."""

    async def test_candidates_carry_no_name_address_full_npi_or_raw_entity_id(self):
        db = FakeDB(_rows(), _existing_rows_bulk())
        report = await plan_pecos_retry(db, limit=25)
        blob = str(report["candidates"])
        assert NPI_B not in blob                 # full NPI never present
        assert "entity-missing" not in blob       # raw entity id never present
        assert "entity-covered" not in blob
        assert "entity-badnpi" not in blob
        cand = next(c for c in report["candidates"] if c["candidate_ref"] == _opaque_ref("entity-missing"))
        assert cand["npi_masked"].startswith("...")
        assert len(cand["npi_masked"]) <= 8
        for forbidden_key in ("entity_id", "name", "organization_name", "address", "legal_name", "npi"):
            assert forbidden_key not in cand

    async def test_candidate_ref_is_opaque_deterministic_and_non_reversible(self):
        ref = _opaque_ref("entity-missing")
        assert ref.startswith("cand-")
        assert "entity-missing" not in ref
        # Deterministic: same input, same output — required for idempotency.
        assert ref == _opaque_ref("entity-missing")
        # Non-reversible by construction: it is a truncated SHA-256 digest,
        # not an encoding of the id (spot-check against a hand-computed hash).
        expected = "cand-" + hashlib.sha256(b"entity-missing").hexdigest()[:12]
        assert ref == expected

    async def test_report_states_the_identifier_policy(self):
        db = FakeDB(_rows(), _existing_rows_bulk())
        report = await plan_pecos_retry(db, limit=25)
        assert "candidate_ref" in report["identifier_note"]
        assert "no name" in report["identifier_note"].lower() or "no  name" in report["identifier_note"].lower()


class TestDryRunGuarantees:
    async def test_makes_zero_database_writes(self):
        db = FakeDB(_rows(), _existing_rows_bulk())
        report = await plan_pecos_retry(db, limit=25)
        assert report["dry_run"] is True
        assert report["executed_retry"] is False
        assert report["database_writes_made"] == 0
        assert report["upstream_calls_made"] == 0
        # FakeDB.add/commit/flush all raise — reaching the assertions above
        # without an exception is itself the proof no write path fired.

    async def test_repeated_dry_run_is_idempotent_same_candidates_zero_writes(self):
        report_1 = await plan_pecos_retry(FakeDB(_rows(), _existing_rows_bulk()), limit=25)
        report_2 = await plan_pecos_retry(FakeDB(_rows(), _existing_rows_bulk()), limit=25)
        assert report_1["candidates"] == report_2["candidates"]
        assert report_1["candidate_count"] == report_2["candidate_count"]
        assert report_2["database_writes_made"] == 0

    async def test_does_not_change_any_delivery_or_disposition_state(self):
        """The planner reads tefca_dimension_evidence only, through a session
        whose add/commit/flush all raise if called; a clean return proves no
        delivery, reconciliation or disposition table was touched."""
        db = FakeDB(_rows(), _existing_rows_bulk())
        report = await plan_pecos_retry(db, limit=25)
        assert report["database_writes_made"] == 0
        assert db.call_count == 2  # reads did happen — this is not a no-op stub
