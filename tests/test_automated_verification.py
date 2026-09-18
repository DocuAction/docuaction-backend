"""Automated verification coverage: every eligible entity of a delivery gets
looked up, in bounded batches, idempotently, WITHOUT creating a ReviewRecord
or a review cycle (see automated_verification.py's module docstring for why
that separation from the analyst-review sample is deliberate, not accidental).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_VALID_OTHER, SYN, make_rows, rolled_back_db, run_quality_and_curation,
    seed_intake,
)


# ── pure: outcome classification (no DB, no network) ────────────────────────

def _evidence(dispositions, applicabilities=None):
    applicabilities = applicabilities or ["APPLICABLE"] * len(dispositions)
    return {"dimensions": [
        {"dimension": f"D{i}", "disposition": d, "applicability": a}
        for i, (d, a) in enumerate(zip(dispositions, applicabilities))
    ]}


def test_classify_all_pass_is_verified():
    assert av._classify_entity_outcome(_evidence(["PASS", "CORROBORATED"])) == av.OUTCOME_VERIFIED


def test_classify_any_conflict_is_verified_with_differences():
    assert av._classify_entity_outcome(_evidence(["PASS", "CONFLICT"])) == av.OUTCOME_VERIFIED_WITH_DIFFERENCES
    assert av._classify_entity_outcome(_evidence(["PASS", "FAIL"])) == av.OUTCOME_VERIFIED_WITH_DIFFERENCES


def test_classify_all_not_found_is_not_found():
    assert av._classify_entity_outcome(_evidence(["NOT_FOUND", "NOT_FOUND"])) == av.OUTCOME_NOT_FOUND


def test_classify_mixed_found_and_not_found_is_verified_with_differences():
    assert av._classify_entity_outcome(_evidence(["PASS", "NOT_FOUND"])) == av.OUTCOME_VERIFIED_WITH_DIFFERENCES


def test_classify_all_unavailable_is_unavailable():
    assert av._classify_entity_outcome(_evidence(["UNAVAILABLE", "UNAVAILABLE"])) == av.OUTCOME_UNAVAILABLE


def test_classify_no_applicable_dimensions_is_not_eligible():
    assert av._classify_entity_outcome(
        _evidence(["PASS"], applicabilities=["NOT_APPLICABLE"])) == av.OUTCOME_NOT_ELIGIBLE
    assert av._classify_entity_outcome({"dimensions": []}) == av.OUTCOME_NOT_ELIGIBLE


def test_every_outcome_constant_is_a_string_never_none():
    for name in ("OUTCOME_VERIFIED", "OUTCOME_VERIFIED_WITH_DIFFERENCES", "OUTCOME_NOT_FOUND",
                 "OUTCOME_REVIEW_REQUIRED", "OUTCOME_RETRY_PENDING", "OUTCOME_FAILED",
                 "OUTCOME_UNAVAILABLE", "OUTCOME_NOT_ELIGIBLE"):
        value = getattr(av, name)
        assert isinstance(value, str) and value


def test_automated_coverage_enabled_reads_env(monkeypatch):
    monkeypatch.delenv(av.ENV_FLAG, raising=False)
    assert av.automated_coverage_enabled() is False
    monkeypatch.setenv(av.ENV_FLAG, "true")
    assert av.automated_coverage_enabled() is True
    monkeypatch.setenv(av.ENV_FLAG, "0")
    assert av.automated_coverage_enabled() is False


# ── database: real 5-entity delivery, evidence-gathering mocked (no network) ─

CLEAN_EVIDENCE = {
    "dimensions": [
        {"dimension": "D1_IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]},
    ],
}


@pytest.fixture
async def promoted_five(rolled_back_db):
    """Five promoted, canonical entities from one synthetic intake — no
    network calls yet; evidence-gathering is mocked per test."""
    db = rolled_back_db
    rows = make_rows(5, arc="9.99.777.60")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    return db, intake_id


async def test_coverage_progress_before_any_run(promoted_five):
    db, intake_id = promoted_five
    progress = await av.coverage_progress(db, intake_id)
    assert progress == {"eligible": 5, "remaining": 5, "covered": 0}


async def test_run_coverage_batch_covers_every_eligible_entity_and_creates_no_review_record(
        promoted_five):
    db, intake_id = promoted_five
    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=CLEAN_EVIDENCE)):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100)

    assert result["eligible"] == 5
    assert result["covered"] == 5
    assert result["remaining"] == 0
    assert result["complete"] is True
    assert result["processed_this_call"] == 5
    assert result["by_outcome"] == {av.OUTCOME_VERIFIED: 5}

    # THE INVARIANT THIS MODULE EXISTS TO PRESERVE: no ReviewRecord, ever.
    review_records = (await db.execute(
        select(reg.ReviewRecord.id))).scalars().all()
    assert review_records == []


async def test_run_coverage_batch_is_idempotent_on_a_second_call(promoted_five):
    db, intake_id = promoted_five
    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=CLEAN_EVIDENCE)) as mocked:
        first = await av.run_coverage_batch(db, intake_id, batch_size=100)
        second = await av.run_coverage_batch(db, intake_id, batch_size=100)

    assert first["processed_this_call"] == 5
    # Nothing left not-covered, so the second call finds zero work and never
    # calls the (expensive, quota-spending) evidence gatherer again for these
    # entities.
    assert second["processed_this_call"] == 0
    assert second["remaining"] == 0
    assert mocked.await_count == 5  # exactly once per entity, total, across both calls


async def test_batch_size_bounds_one_call_and_leaves_the_rest_for_the_next(promoted_five):
    db, intake_id = promoted_five
    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=CLEAN_EVIDENCE)):
        first = await av.run_coverage_batch(db, intake_id, batch_size=2)
        assert first["processed_this_call"] == 2
        assert first["remaining"] == 3
        assert first["complete"] is False

        second = await av.run_coverage_batch(db, intake_id, batch_size=2)
        assert second["processed_this_call"] == 2
        assert second["remaining"] == 1

        third = await av.run_coverage_batch(db, intake_id, batch_size=2)
        assert third["processed_this_call"] == 1
        assert third["remaining"] == 0
        assert third["complete"] is True


async def test_one_entity_failure_does_not_stop_the_rest_of_the_batch(promoted_five):
    db, intake_id = promoted_five
    calls = {"n": 0}

    async def flaky_build_evidence(self, entity):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("simulated connector timeout")
        return CLEAN_EVIDENCE

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=flaky_build_evidence):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100)

    assert result["processed_this_call"] == 5
    assert result["by_outcome"].get(av.OUTCOME_RETRY_PENDING) == 1
    assert result["by_outcome"].get(av.OUTCOME_VERIFIED) == 4
    # The failed entity was NOT marked covered - it has no evidence row, so it
    # is still eligible for retry on the next call.
    assert result["remaining"] == 1
    assert result["covered"] == 4


async def test_a_second_run_after_a_failure_retries_only_the_failed_entity(promoted_five):
    db, intake_id = promoted_five
    calls = {"n": 0}

    async def fail_first_entity_only(self, entity):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("simulated connector timeout")
        return CLEAN_EVIDENCE

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=fail_first_entity_only):
        first = await av.run_coverage_batch(db, intake_id, batch_size=100)
    assert first["remaining"] == 1

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=CLEAN_EVIDENCE)) as mocked:
        second = await av.run_coverage_batch(db, intake_id, batch_size=100)

    assert second["processed_this_call"] == 1  # only the one that failed before
    assert second["remaining"] == 0
    assert mocked.await_count == 1
