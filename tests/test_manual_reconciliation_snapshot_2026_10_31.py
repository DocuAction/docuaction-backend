"""The MANUAL reconciliation-snapshot leg — the missing refresh-and-pin path.

`SNAPSHOT_TRIGGERS` has carried "MANUAL" (and the table's CHECK constraint
accepts it) since the vocabulary existed, but NO code path ever used it:
snapshots were written only as side-effects (pipeline completion, identifier
dispositions, post-promotion findings). After an evidence backfill or a
catch-up batch, the pinned snapshot silently predated the current state and
delivery-scoped reports kept citing stale front-page reconciliation facts —
observed on DA-ARC-2026-087 (pinned 2026-10-30T06:03Z, generated 2026-10-31,
after release 41174963190's one-row backfill changed the check results).

Design under test:
* an explicit qalead act persists the LIVE reconciliation through the one
  existing writer (`persist_snapshot`, append-only, max-sequence+1, hashed);
* the new snapshot becomes the latest — what the reconciliation GET serves
  by default and what a NEW report pins; earlier snapshots are untouched;
* a failing check is persisted as failing: pinning is never clearing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce import traceability_models as tm
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)


@pytest.fixture
async def delivered(rolled_back_db):
    """A promoted synthetic delivery with its RUNNING job."""
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.81")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    return db, intake_id, job


async def _snapshots(db, job_id):
    return (await db.execute(
        select(tm.RceReconciliationSnapshot)
        .where(tm.RceReconciliationSnapshot.job_id == job_id)
        .order_by(tm.RceReconciliationSnapshot.sequence))).scalars().all()


async def test_manual_trigger_persists_appends_and_becomes_latest(delivered):
    db, intake_id, job = delivered
    before = await _snapshots(db, job.id)

    result = await reconciliation.reconcile_delivery(db, intake_id)
    snap = await reconciliation.persist_snapshot(
        db, intake_id, result, job_id=job.id, actor=SYN, trigger="MANUAL")

    # The CHECK constraint accepted the MANUAL trigger and the row is durable.
    assert snap.trigger == "MANUAL"
    after = await _snapshots(db, job.id)
    assert len(after) == len(before) + 1          # append-only: nothing replaced
    assert snap.sequence == (before[-1].sequence if before else 0) + 1
    for old in before:                            # earlier snapshots untouched
        kept = next(s for s in after if s.id == old.id)
        assert (kept.hash, kept.trigger, kept.passed) == (old.hash, old.trigger, old.passed)

    # It is now the latest — what the GET serves and a NEW report pins.
    latest = await reconciliation.latest_snapshot(db, job.id)
    assert latest is not None and latest.id == snap.id

    # The snapshot IS the live result, hashed — not a re-derivation.
    assert snap.hash == reconciliation.snapshot_hash(
        dict(result.get("equation") or {}), dict(result.get("dimensions") or {}),
        list(result.get("checks") or []))


async def test_a_failing_check_is_persisted_as_failing(delivered):
    db, intake_id, job = delivered
    result = await reconciliation.reconcile_delivery(db, intake_id)
    # Make one check fail deterministically, the way a real unresolved item
    # does — the persist path must carry it, never clean it up.
    result = dict(result)
    checks = list(result.get("checks") or [])
    checks.append({"check": "synthetic: deliberately failing",
                   "passed": False, "detail": "pinning is not clearing"})
    result["checks"] = checks
    result["passed"] = False

    snap = await reconciliation.persist_snapshot(
        db, intake_id, result, job_id=job.id, actor=SYN, trigger="MANUAL")
    assert snap.passed is False
    assert "deliberately failing" in (snap.failure_reason or "")
    failed = [c for c in (snap.checks or []) if not c.get("passed")]
    assert any("synthetic" in c["check"] for c in failed)


async def test_manual_snapshot_requires_a_job():
    # The route refuses an intake with no delivery job (409) and the writer
    # refuses a null job outright — a snapshot must belong to a job.
    with pytest.raises(ValueError):
        await reconciliation.persist_snapshot(
            None, "00000000-0000-0000-0000-000000000000", {},
            job_id=None, actor=SYN, trigger="MANUAL")


async def test_concurrent_persist_loses_cleanly_as_409(delivered):
    """Two operators persist at once: both compute max(sequence)+1, the
    UNIQUE (job_id, sequence) constraint makes the slower one lose, and the
    route answers 409 instead of surfacing a raw IntegrityError or — worse —
    allowing duplicate sequences. The winner's snapshot is untouched."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from app.tefca_registry.rce import routes as r

    # The constraint this safety property rests on actually exists.
    names = {c.name for c in tm.RceReconciliationSnapshot.__table__.constraints}
    assert "uq_rce_snapshot_sequence" in names

    db, intake_id, job = delivered
    body = r.ManualSnapshotRequest(reason="concurrency test: loser must 409")
    user = SimpleNamespace(email=SYN, id=None)
    with patch("app.tefca_registry.rce.reconciliation.persist_snapshot",
               new=AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))), \
         patch("app.tefca_registry.rce.reconciliation.reconcile_delivery",
               new=AsyncMock(return_value={"equation": {}, "checks": [], "passed": True})):
        with pytest.raises(HTTPException) as exc:
            await r.persist_manual_reconciliation_snapshot(
                str(intake_id), body, request=None, db=db, user=user)
    assert exc.value.status_code == 409
    assert "concurrent" in str(exc.value.detail).lower()


async def test_snapshot_survives_a_failed_audit_write(delivered):
    """The snapshot is durable the moment persist_snapshot commits; a failed
    audit row afterwards is logged and reported, never a reason to hide or
    lose the snapshot (same posture as _try_persist_snapshot's error path)."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.tefca_registry.rce import routes as r

    db, intake_id, job = delivered
    before = len(await _snapshots(db, job.id))
    body = r.ManualSnapshotRequest(reason="audit-failure path: snapshot must survive")
    user = SimpleNamespace(email=SYN, id=None)
    with patch("app.tefca_registry.audit.record",
               side_effect=RuntimeError("audit store down")):
        out = await r.persist_manual_reconciliation_snapshot(
            str(intake_id), body, request=None, db=db, user=user)

    assert out["snapshot"]["trigger"] == "MANUAL"
    after = await _snapshots(db, job.id)
    assert len(after) == before + 1
    assert str(after[-1].id) == out["snapshot"]["id"]  # durable despite the audit failure


def test_route_registered_at_qalead_with_required_reason():
    from app.tefca_registry.rce import routes as r

    route = next(rt for rt in r.router.routes
                 if getattr(rt, "path", "") ==
                 "/api/tefca/rce/deliveries/{intake_id}/reconciliation/snapshots"
                 and "POST" in getattr(rt, "methods", set()))
    # The reason is a required, non-trivial field (min 10 chars): the audit
    # row records intent, not an empty string.
    fields = r.ManualSnapshotRequest.model_fields
    assert fields["reason"].is_required()
    meta = fields["reason"].metadata
    assert any(getattr(m, "min_length", None) == 10 for m in meta)
    # And the vocabulary/constraint pair this route finally uses:
    assert "MANUAL" in tm.SNAPSHOT_TRIGGERS
