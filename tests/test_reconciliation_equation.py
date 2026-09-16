"""Reconciliation: the disposition equation, and the persisted snapshot."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core import request_context
from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce import traceability_models as tm
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)


def _check(result, prefix):
    return next(c for c in result["checks"] if c["check"].startswith(prefix))


async def test_reconciliation_fails_while_a_record_lacks_a_disposition(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.41")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)

    before = await reconciliation.reconcile_delivery(db, intake_id)
    assert before["passed"] is False
    assert before["records_without_disposition"] == 3
    assert _check(before, "Every source record has a current disposition")["passed"] is False
    assert _check(before, "Disposition equation")["passed"] is False
    assert before["equation"]["received"] == 3 and before["equation"]["accounted"] == 0

    await promote_delivery(db, intake_id, actor=SYN)
    after = await reconciliation.reconcile_delivery(db, intake_id)
    assert after["passed"] is True, after["failed_checks"]
    assert after["records_without_disposition"] == 0
    assert after["equation"]["holds"] is True
    assert after["equation"]["created"] == 3
    assert after["dispositions"]["CREATED"] == 3
    assert _check(after, "Zero orphan report links")["passed"] is True
    assert _check(after, "Unresolved identifier conflicts are all HELD")["passed"] is True
    # Every pre-existing check is still there.
    names = [c["check"] for c in after["checks"]]
    for expected in ("A: every delivered line stored", "D == A: every source record curated",
                     "C = A − B: eligible population is exact",
                     "E == C: every eligible record promoted",
                     "E: Area 1 promotion markers agree with Area 2",
                     "E: one registry entity per promoted record",
                     "F ⊆ E: every verification traces to a promoted entity",
                     "Zero orphan Area 2 records", "Zero orphan promotions",
                     "Zero orphan issues", "Zero orphan corrections",
                     "Every correction is explained", "Every rule evaluated every record",
                     "No rule execution failed", "Every determination traces to evidence",
                     "Every determination cites a rule",
                     "Area 1 raw lines still hash to their intake values"):
        assert expected in names, expected


async def test_snapshot_is_persisted_with_hash_build_and_migration_revision(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.42")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    result = await reconciliation.reconcile_delivery(db, intake_id)

    with request_context.bind(job_id=job.id):
        snapshot = await reconciliation.persist_snapshot(
            db, intake_id, result, job_id=job.id, actor=SYN, trigger="PIPELINE")

    assert snapshot.sequence == 1
    assert snapshot.passed is True
    assert snapshot.received == 2 and snapshot.created == 2 and snapshot.accounted == 2
    assert re.fullmatch(r"[0-9a-f]{64}", snapshot.hash)
    assert snapshot.hash == reconciliation.snapshot_hash(
        result["equation"], result["dimensions"], result["checks"])
    assert snapshot.build_sha == request_context.build_sha()
    revision = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert snapshot.migration_revision == revision
    assert snapshot.correlation_id == str(job.id)
    assert snapshot.trigger == "PIPELINE" and snapshot.actor == SYN
    assert snapshot.source_evidence["populations"]["A_source_records_received"] == 2
    assert snapshot.source_evidence["intake_id"] == str(intake_id)
    assert snapshot.dimensions["findings"]["total"] >= 0
    assert snapshot.checks == result["checks"]
    assert snapshot.to_dict()["equation"]["holds"] is True

    second = await reconciliation.persist_snapshot(
        db, intake_id, result, job_id=job.id, actor=SYN, trigger="MANUAL")
    assert second.sequence == 2
    latest = await reconciliation.latest_snapshot(db, job.id)
    assert latest.id == second.id
    assert await reconciliation.snapshot_history_count(db, job.id) == 2
    assert latest.hash == snapshot.hash, "same evidence, same hash"


async def test_a_failing_result_persists_as_failed_with_the_reason(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.43")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    result = await reconciliation.reconcile_delivery(db, intake_id)   # pre-promotion
    snapshot = await reconciliation.persist_snapshot(
        db, intake_id, result, job_id=job.id, actor=SYN, trigger="PIPELINE")
    assert snapshot.passed is False
    assert "Every source record has a current disposition" in snapshot.failure_reason
    assert snapshot.received == 2 and snapshot.accounted == 0


async def test_the_check_constraint_rejects_a_passing_snapshot_whose_counts_do_not_sum(
        rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.44")
    intake_id, job = await seed_intake(db, rows)
    db.add(tm.RceReconciliationSnapshot(
        job_id=job.id, intake_id=intake_id, sequence=1, passed=True,
        received=5, created=1, updated=0, matched_unchanged=0, held=0,
        rejected=0, missing_key=0, excluded=0, dimensions={}, checks=[],
        source_evidence={}, actor=SYN, trigger="MANUAL", hash="0" * 64,
        build_sha="test", migration_revision="test", correlation_id="test"))
    with pytest.raises(IntegrityError) as exc:
        await db.flush()
    assert "ck_rce_snapshot_equation" in str(exc.value)
    await db.rollback()


async def test_snapshot_requires_a_job_and_a_known_trigger(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.45")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    result = await reconciliation.reconcile_delivery(db, intake_id)
    with pytest.raises(ValueError):
        await reconciliation.persist_snapshot(db, intake_id, result, job_id=None,
                                              actor=SYN, trigger="PIPELINE")
    with pytest.raises(ValueError):
        await reconciliation.persist_snapshot(db, intake_id, result, job_id=job.id,
                                              actor=SYN, trigger="WHENEVER")
    assert await reconciliation.latest_snapshot(db, None) is None
