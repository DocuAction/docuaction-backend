"""The quality engine's new-entrant baseline must be a COMPARABLE delivery.

October 2026 (intake e8eb0a91): `previous_delivery` picked a 3-record
smoke-test intake as the baseline because it merely arrived last, and 21
records present (and held) in the real September delivery were flagged
ACT-001 as history-less inactive new entrants. `_build_dataset_context`
now uses `delivery_delta.comparable_previous_delivery`, which skips a
predecessor failing `assess_comparability` (one-sided synthetic, different
declared source, >=10x size gap, different schema fingerprint).

PostgreSQL (`rolled_back_db`); every row is synthetic (ARC 9.99.777.9x).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401 (fixture import)
    ARC, make_rows, rolled_back_db, seed_intake,
)

pytestmark = pytest.mark.asyncio


async def _set_received(db, intake_id, when):
    intake = await db.get(m.RceSourceIntake, intake_id)
    intake.received_at = when
    await db.commit()


async def test_smoke_test_between_two_directories_is_not_the_baseline(rolled_back_db):
    db = rolled_back_db
    # Far past, like test_september_snapshot: the shared test database may hold
    # committed intakes from other suites; nothing earlier than 2001 exists.
    now = datetime(2001, 11, 5, 12, 0, 0)

    # September-like directory: 20 rows, ids .1 — .20
    sept_id, _ = await seed_intake(db, make_rows(20), with_job=False)
    await _set_received(db, sept_id, now - timedelta(days=55))

    # Smoke test in between: 2 rows (>=10x smaller => NOT_COMPARABLE), newer.
    smoke_id, _ = await seed_intake(db, make_rows(2, arc=f"{ARC}.88"), with_job=False)
    await _set_received(db, smoke_id, now - timedelta(days=21))

    # October-like directory: the same 20 ids plus one genuinely new id.
    rows = make_rows(20) + make_rows(1, arc=f"{ARC}.77")
    oct_id, _ = await seed_intake(db, rows, with_job=False)
    await _set_received(db, oct_id, now)

    from app.tefca_registry.rce.delivery_delta import (
        comparable_previous_delivery, previous_delivery,
    )
    from app.tefca_registry.rce.quality_engine import _build_dataset_context

    oct_intake = await db.get(m.RceSourceIntake, oct_id)
    raw = await previous_delivery(db, oct_intake)
    assert str(raw.id) == str(smoke_id)          # what arrived last…
    comparable = await comparable_previous_delivery(db, oct_intake)
    assert str(comparable.id) == str(sept_id)    # …is not what this succeeds

    ctx = await _build_dataset_context(db, oct_id)
    assert ctx["previous_intake_id"] == str(sept_id)
    # The 20 continuing ids are NOT new entrants (with the smoke-test baseline
    # every one of them was); only the genuinely new id is.
    assert ctx["new_entrant_ids"] == {f"{ARC}.77.1"}


async def test_no_comparable_predecessor_means_no_new_entrants(rolled_back_db):
    """A true baseline (only a non-comparable intake exists before it) flags
    nothing: fail-safe, matching the no-predecessor behaviour."""
    db = rolled_back_db
    now = datetime(2001, 11, 5, 12, 0, 0)

    smoke_id, _ = await seed_intake(db, make_rows(2, arc=f"{ARC}.88"), with_job=False)
    await _set_received(db, smoke_id, now - timedelta(days=21))

    oct_id, _ = await seed_intake(db, make_rows(20), with_job=False)
    await _set_received(db, oct_id, now)

    from app.tefca_registry.rce.quality_engine import _build_dataset_context
    ctx = await _build_dataset_context(db, oct_id)
    assert ctx["previous_intake_id"] is None
    assert ctx["new_entrant_ids"] == set()
