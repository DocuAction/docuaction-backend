"""Synthetic regression evidence for the remaining delivery verification gaps."""
import json
import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.Tefca import entity_resolution as er
from app.tefca_registry import models as reg
from app.tefca_registry.rce import arc_pipeline as ap, delivery_jobs as jobs
from app.tefca_registry.rce import traceability_models as tm
from rce_traceability_support import rolled_back_db  # noqa: F401
from test_review_lineage_writers_db_2026_11_12 import (
    _two_deliveries_sharing_entities, _patch_bulk_gather,
)

RAW = ("(sqlalchemy.dialects.postgresql.asyncpg.Error) "
       "<class 'asyncpg.exceptions.DataError'>: SYNTHETIC_SECRET "
       "[SQL: INSERT INTO test VALUES ($1)] [parameters: ('SYNTHETIC_SECRET',)]")


@pytest.mark.parametrize("source", [None, "mock", "db_then_mock", "typo"])
def test_fixture_capable_resolver_has_an_explicit_configuration_warning(monkeypatch, source):
    if source is None:
        monkeypatch.delenv(er.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(er.ENV_VAR, source)
    warnings = er.resolver_configuration_warnings()
    assert warnings and warnings[0]["code"] == "REGISTRY_RESOLVER_NOT_EXCLUSIVE"
    assert "ENTITY_RESOLVER_SOURCE=db" in warnings[0]["message"]


def test_db_resolver_has_no_configuration_warning(monkeypatch):
    monkeypatch.setenv(er.ENV_VAR, " db ")
    assert er.resolver_configuration_warnings() == []


def test_stage_serialization_masks_historical_driver_text_without_mutating_evidence():
    now = datetime.utcnow()
    detail = {"error": RAW, "nested": [RAW], "count": 3, "note": "Controlled note"}
    event = tm.RceDeliveryStageEvent(
        id=uuid.uuid4(), job_id=uuid.uuid4(), stage="PROMOTION", attempt=1,
        status="FAILED", started_at=now, completed_at=now,
        failure_class="DataError", failure_reason=RAW, detail=detail,
        correlation_id="synthetic", worker_id="synthetic", build_sha="test")
    payload = event.to_dict()
    assert "SYNTHETIC_SECRET" not in json.dumps(payload)
    assert "[parameters:" not in json.dumps(payload)
    assert event.failure_reason == RAW and event.detail == detail
    assert payload["detail"]["count"] == 3
    assert payload["detail"]["note"] == "Controlled note"


@pytest.mark.asyncio
async def test_real_bulk_writer_cases_count_only_for_their_anchored_delivery(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    _, a, b, ra, rb, entities = await _two_deliveries_sharing_entities(db, "9.99.777.981")
    _patch_bulk_gather(monkeypatch, {ra[i].source_rce_id: entities[i] for i in (1, 2)})
    monkeypatch.setenv(er.ENV_VAR, "db")
    await ap.verify_and_classify(db, [ra[1].source_rce_id, ra[2].source_rce_id],
                                 intake_id=a, persist_evidence=False)
    rows = (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.source_record_id.in_([ra[1].id, ra[2].id])))).scalars().all()
    # This fixture is created by the REAL writer, not pre-seeded review cases.
    assert len(rows) == 2
    rows[0].reviewer_resolution = "CONFIRM"
    rows[0].reportable_at = datetime.utcnow()
    await db.flush()
    single = await jobs._review_counts(db, a)
    batch = await jobs._review_counts_by_intake(db, [a, b])
    assert single == batch[a]
    assert single["total"] == 2 and single["qa_approved"] == 1 and single["open"] == 1
    assert batch[b]["total"] == 0  # The entities are SHARED with delivery B.
    from app.tefca_registry.rce import delivery_routes
    fallback = await delivery_routes._review_counts(db, a)
    assert fallback["open_work_items"] == 1 and fallback["qa_approved"] == 1
    assert (await delivery_routes._review_counts(db, b))["qa_approved"] == 0


@pytest.mark.asyncio
async def test_record_anchor_takes_precedence_over_a_conflicting_legacy_stamp(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    _, a, b, ra, rb, entities = await _two_deliveries_sharing_entities(db, "9.99.777.982")
    _patch_bulk_gather(monkeypatch, {ra[1].source_rce_id: entities[1]})
    await ap.verify_and_classify(db, [ra[1].source_rce_id], intake_id=a,
                                 persist_evidence=False)
    row = (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.source_record_id == ra[1].id))).scalar_one()
    row.verification_results = {**row.verification_results, "source_intake_id": str(b)}
    await db.flush()
    counts = await jobs._review_counts_by_intake(db, [a, b])
    assert counts[a]["total"] == 1 and counts[b]["total"] == 0
    from app.tefca_registry.rce import delivery_routes
    assert (await delivery_routes._review_counts(db, a))["open_work_items"] == 1
    assert (await delivery_routes._review_counts(db, b))["open_work_items"] == 0


def test_empty_cycle_summary_still_discloses_fixture_configuration(monkeypatch):
    from app.tefca_registry.review_cycle import _empty_verification_summary
    monkeypatch.delenv(er.ENV_VAR, raising=False)
    result = _empty_verification_summary()
    assert result["execution_status"] == "NO_RECORDS_REQUESTED"
    assert result["resolver_source"] == "mock" and result["configuration_warnings"]


@pytest.mark.parametrize("reason", [
    'duplicate key value violates constraint DETAIL: Key (email)=(SYNTHETIC_SECRET) already exists',
    'invalid input syntax for type uuid: "SYNTHETIC_SECRET"',
])
@pytest.mark.asyncio
async def test_timeline_masks_bare_legacy_driver_text_without_rewriting_it(rolled_back_db, reason):
    from app.tefca_registry.rce import stage_events
    from rce_traceability_support import seed_intake, make_rows
    db = rolled_back_db
    intake, job = await seed_intake(db, make_rows(1))
    event = await stage_events.record_instant(db, job.id, "PROMOTION", status="FAILED",
                                            intake_id=intake, failure_reason=reason)
    event.failure_class = "UniqueViolationError"
    await db.flush()
    timeline = await stage_events.timeline(db, job.id)
    assert "SYNTHETIC_SECRET" not in json.dumps(timeline, default=str)
    assert event.failure_reason == reason


def test_controlled_domain_failure_remains_readable():
    from app.core.logging_config import sanitize_stored_error
    reason = "CURATION: ValueError: missing documented field"
    assert sanitize_stored_error(reason, failure_class="ValueError") == reason


@pytest.mark.asyncio
async def test_zero_cases_are_explicit_even_with_a_database_resolver(rolled_back_db, monkeypatch):
    monkeypatch.setenv(er.ENV_VAR, "db")
    _patch_bulk_gather(monkeypatch, {})
    result = await ap.verify_and_classify(rolled_back_db, ["SYNTHETIC-NO-MATCH"],
                                         persist_evidence=False)
    assert result["processed"] == 0 and result["unresolved"] == ["SYNTHETIC-NO-MATCH"]
    assert result["execution_status"] == "NO_CASES_CREATED"
    assert result["resolver_source"] == "db"
    assert result["configuration_warnings"] == []


@pytest.mark.asyncio
async def test_empty_request_is_not_misreported_as_failed_verification(rolled_back_db, monkeypatch):
    monkeypatch.setenv(er.ENV_VAR, "db")
    result = await ap.verify_and_classify(rolled_back_db, [], persist_evidence=False)
    assert result["execution_status"] == "NO_RECORDS_REQUESTED"


@pytest.mark.asyncio
async def test_contextless_case_is_not_borrowed_and_legacy_delivery_stamp_still_works(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    _, a, b, ra, rb, entities = await _two_deliveries_sharing_entities(db, "9.99.777.983")
    _patch_bulk_gather(monkeypatch, {ra[1].source_rce_id: entities[1]})
    await ap.verify_and_classify(db, [ra[1].source_rce_id], persist_evidence=False)
    row = (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id == entities[1]))).scalar_one()
    assert row.source_record_id is None
    counts = await jobs._review_counts_by_intake(db, [a, b])
    assert counts[a]["total"] == counts[b]["total"] == 0
    row.verification_results = {**row.verification_results, "source_intake_id": str(a)}
    await db.flush()
    counts = await jobs._review_counts_by_intake(db, [a, b])
    assert counts[a]["total"] == 1 and counts[b]["total"] == 0
    assert await jobs._review_counts(db, a) == counts[a]


@pytest.mark.asyncio
async def test_cycle_lock_connection_survives_commit_and_pool_churn(db_required):
    """Use the actual pool: an ordinary Session would release its lock backend."""
    from app.tefca_registry.review_cycle import _cycle_session

    engine = create_async_engine(os.environ["DATABASE_URL"], pool_size=2, max_overflow=0)
    key = str(uuid.uuid4())
    try:
        async with AsyncSession(engine, expire_on_commit=False) as request_db:
            async with _cycle_session(request_db) as pinned:
                original_pid = (await pinned.execute(text("select pg_backend_pid()"))).scalar_one()
                await pinned.execute(text("select pg_advisory_lock(hashtext(:k))"), {"k": key})
                await pinned.commit()
                # Force a different borrower to hold the first available pooled
                # connection while this operation continues after commit.
                async with engine.connect() as other:
                    other_pid = (await other.execute(text("select pg_backend_pid()"))).scalar_one()
                    assert other_pid != original_pid
                    assert not (await other.execute(text(
                        "select pg_try_advisory_lock(hashtext(:k))"), {"k": key})).scalar_one()
                    assert (await pinned.execute(text("select pg_backend_pid()"))).scalar_one() == original_pid
                    assert (await pinned.execute(text(
                        "select pg_advisory_unlock(hashtext(:k))"), {"k": key})).scalar_one()
                    await pinned.commit()
    finally:
        await engine.dispose()


def test_a_withheld_marker_inside_a_driver_payload_does_not_make_it_safe():
    from app.core.logging_config import sanitize_stored_error
    raw = "SYNTHETIC_SECRET (message withheld from evidence; appended by source)"
    assert "SYNTHETIC_SECRET" not in sanitize_stored_error(raw, failure_class="UniqueViolationError")
