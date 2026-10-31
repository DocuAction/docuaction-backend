"""SAM-extract-only coverage scope and the per-delivery batch lock.

The design under test (2026-10-30):

* `run_coverage_batch(..., scope="sam_extract")` appends ONLY the
  SAM_EXTRACT_SUPPLEMENTARY evidence row to entities that already have
  evidence but no extract row — it never calls `build_evidence`, so no other
  source is re-asked and no quota is re-spent (the forbidden alternative was
  a full-source catch-up over ~24,505 already-covered entities).
* The persisted item comes from `sam_extract_evidence_items`, the SAME code
  the full D3 assembly uses, so the two cannot drift.
* The scope refuses to run while ENABLE_SAM_EXTRACT_SCREENING is off: a
  flag-off mass run would write one UNAVAILABLE row per entity and cover
  nothing.
* Batches for one delivery are serialized by a transaction-level advisory
  lock; a concurrent call is refused (`skipped_lock: true`), never queued —
  overlapping batches were the mechanism behind the ~1,007 duplicated
  evidence generations observed on the 2026-10 delivery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.Tefca.connectors import SourceResult
from app.Tefca.evidence_assembly import sam_extract_evidence_items
from app.Tefca.evidence_dimensions import Dimension, Disposition
from app.tefca_registry import models as reg
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

D3 = Dimension.D3_EXCLUSION_REVOCATION.value


@pytest.fixture(autouse=True)
def _resolve_entities_from_the_registry(monkeypatch):
    """Same dependency statement as test_automated_verification.py: the batch
    resolves references through `resolve_entity`, which honours
    ENTITY_RESOLVER_SOURCE (default "mock")."""
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")


def _screen_result(data) -> SourceResult:
    return SourceResult(source_name="SAM_EXTRACT", success=True, data=data,
                        api_version="GSA-Public-Extract-V2")


# ── pure: the shared item builder ────────────────────────────────────────────

def test_items_flag_off_key_absent_is_empty():
    assert sam_extract_evidence_items(D3, None) == ([], False)


def test_items_clear_result_is_a_single_pass_item():
    items, hit = sam_extract_evidence_items(D3, _screen_result({"candidate_match": False}))
    assert hit is False
    assert [i.disposition for i in items] == [Disposition.PASS.value]
    assert items[0].source == av.EXTRACT_SOURCE
    assert items[0].rule_applied == "SAM_PUBLIC_EXTRACT_SUPPLEMENTARY_SCREEN"


def test_items_candidate_is_review_and_escalates():
    items, hit = sam_extract_evidence_items(D3, _screen_result({"candidate_match": True}))
    assert hit is True
    assert [i.disposition for i in items] == [Disposition.REVIEW.value]
    assert "CANDIDATE only" in items[0].note


def test_items_unavailable_is_disclosed_never_dropped():
    bad = SourceResult(source_name="SAM_EXTRACT", success=False,
                       error="stale_source: extract older than 2 days")
    items, hit = sam_extract_evidence_items(D3, bad)
    assert hit is False
    assert [i.disposition for i in items] == [Disposition.UNAVAILABLE.value]
    assert "stale_source" in items[0].note


def test_candidate_queries_deduplicate_entities_within_a_batch():
    # Several curated records can share one canonical entity; both candidate
    # lists must emit each entity once per batch.
    assert "DISTINCT ON (r.canonical_entity_id)" in av._ELIGIBLE_NOT_COVERED_SQL
    assert "DISTINCT ON (r.canonical_entity_id)" in av._SAM_EXTRACT_NOT_COVERED_SQL


# ── database ─────────────────────────────────────────────────────────────────

CLEAN_EVIDENCE = {
    "dimensions": [
        {"dimension": "D1_IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]},
    ],
}


@pytest.fixture
async def covered_three(rolled_back_db, monkeypatch):
    """Three promoted entities that already carry (mocked) full coverage —
    evidence rows exist, none of them a SAM-extract row. The exact population
    the sam_extract scope exists for."""
    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.61")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
              new=AsyncMock(return_value=CLEAN_EVIDENCE)):
        full = await av.run_coverage_batch(db, intake_id, batch_size=100)
    assert full["remaining"] == 0 and full["processed_this_call"] == 3
    return db, intake_id


async def _extract_rows(db, intake_id):
    return (await db.execute(text(
        "SELECT d.entity_id, d.disposition, d.rule_applied, d.evidence_dimension "
        "FROM tefca_dimension_evidence d "
        "WHERE d.source = 'SAM_EXTRACT_SUPPLEMENTARY' "
        "  AND d.entity_id IN (SELECT CAST(r.canonical_entity_id AS TEXT) "
        "                      FROM rce_curated_records r "
        "                      WHERE r.source_intake_id = CAST(:i AS uuid))"),
        {"i": str(intake_id)})).all()


async def test_sam_extract_scope_appends_only_the_extract_row(covered_three):
    db, intake_id = covered_three

    # Fully covered for the FULL scope, yet all three remain for this scope.
    assert (await av.coverage_progress(db, intake_id))["remaining"] == 0
    before = await av.coverage_progress(db, intake_id, scope=av.SCOPE_SAM_EXTRACT)
    assert before == {"eligible": 3, "remaining": 3, "covered": 0}

    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
              new=AsyncMock(return_value=_screen_result({"candidate_match": False}))):
        result = await av.run_coverage_batch(
            db, intake_id, batch_size=100, scope=av.SCOPE_SAM_EXTRACT)

    assert result["scope"] == av.SCOPE_SAM_EXTRACT
    assert result["processed_this_call"] == 3
    assert result["remaining"] == 0 and result["complete"] is True
    assert result["by_outcome"] == {av.OUTCOME_VERIFIED: 3}

    rows = await _extract_rows(db, intake_id)
    assert len(rows) == 3
    assert {r.disposition for r in rows} == {"PASS"}
    assert {r.rule_applied for r in rows} == {"SAM_PUBLIC_EXTRACT_SUPPLEMENTARY_SCREEN"}
    assert {r.evidence_dimension for r in rows} == {D3}

    # No other source gained a row: the full-coverage mock wrote exactly one
    # NPPES row per entity, and that count is unchanged.
    per_source = dict((await db.execute(text(
        "SELECT d.source, count(*) FROM tefca_dimension_evidence d "
        "WHERE d.entity_id IN (SELECT CAST(r.canonical_entity_id AS TEXT) "
        "                      FROM rce_curated_records r "
        "                      WHERE r.source_intake_id = CAST(:i AS uuid)) "
        "GROUP BY d.source"), {"i": str(intake_id)})).all())
    assert per_source == {"NPPES": 3, "SAM_EXTRACT_SUPPLEMENTARY": 3}

    # Idempotent: a second call finds nothing left and writes nothing.
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
              new=AsyncMock(return_value=_screen_result({"candidate_match": False}))):
        again = await av.run_coverage_batch(
            db, intake_id, batch_size=100, scope=av.SCOPE_SAM_EXTRACT)
    assert again["processed_this_call"] == 0
    assert len(await _extract_rows(db, intake_id)) == 3


async def test_sam_extract_candidate_routes_to_review(covered_three):
    db, intake_id = covered_three
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
              new=AsyncMock(return_value=_screen_result(
                  {"candidate_match": True, "match_methods": ["npi"]}))):
        result = await av.run_coverage_batch(
            db, intake_id, batch_size=100, scope=av.SCOPE_SAM_EXTRACT)

    assert result["by_outcome"] == {av.OUTCOME_REVIEW_REQUIRED: 3}
    rows = await _extract_rows(db, intake_id)
    assert {r.disposition for r in rows} == {"REVIEW"}
    # Candidates are flagged for a human, exactly as the full path flags
    # REVIEW_REQUIRED outcomes — never auto-decided.
    statuses = (await db.execute(text(
        "SELECT e.verification_status FROM tefca_reg_entities e "
        "WHERE CAST(e.id AS TEXT) IN (SELECT CAST(r.canonical_entity_id AS TEXT) "
        "   FROM rce_curated_records r WHERE r.source_intake_id = CAST(:i AS uuid))"),
        {"i": str(intake_id)})).scalars().all()
    assert statuses and set(statuses) == {"in_review"}


async def test_sam_extract_scope_refuses_while_the_flag_is_off(covered_three, monkeypatch):
    db, intake_id = covered_three
    monkeypatch.delenv("ENABLE_SAM_EXTRACT_SCREENING", raising=False)
    with pytest.raises(av.SamExtractScreeningDisabled):
        await av.run_coverage_batch(db, intake_id, scope=av.SCOPE_SAM_EXTRACT)
    assert await _extract_rows(db, intake_id) == []


async def test_unknown_scope_is_refused(covered_three):
    db, intake_id = covered_three
    with pytest.raises(ValueError):
        await av.run_coverage_batch(db, intake_id, scope="everything")


async def test_concurrent_batch_for_the_same_delivery_is_refused_not_duplicated(
        rolled_back_db, monkeypatch):
    """A second session holding this delivery's batch lock makes the call
    return `skipped_lock: true` with nothing processed — the serialization
    that prevents overlapping batches writing duplicate generations.

    Deliberately NOT built on `covered_three`: the advisory xact lock a batch
    takes belongs to the top-level transaction, and under this fixture that is
    the test's own outer transaction — so a delivery the fixture already ran a
    batch for stays locked BY THIS SESSION until teardown, and no second
    session could hold its lock. A fresh, never-batched intake is the honest
    way to stage the conflict."""
    import os
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from app.core.database import _normalize_url

    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.62")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)

    engine = create_async_engine(
        _normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    other = await engine.connect()
    try:
        held = (await other.execute(text(
            "SELECT pg_try_advisory_xact_lock(CAST(:k AS int), "
            "hashtext(CAST(:i AS text)))"),
            {"k": av.BATCH_LOCK_CLASS_KEY, "i": str(intake_id)})).scalar()
        assert held is True  # the other session owns this delivery's lock now
        with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
                  new=AsyncMock(return_value=_screen_result({"candidate_match": False}))):
            result = await av.run_coverage_batch(
                db, intake_id, batch_size=100, scope=av.SCOPE_SAM_EXTRACT)
        assert result["skipped_lock"] is True
        assert result["processed_this_call"] == 0
        assert result["remaining"] == 2  # untouched, honestly reported
        assert await _extract_rows(db, intake_id) == []
    finally:
        await other.rollback()  # releases the xact lock
        await other.close()
        await engine.dispose()
