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
    """Place one seeded intake at a chosen point of the received_at timeline."""
    intake = await db.get(m.RceSourceIntake, intake_id)
    intake.received_at = when
    await db.commit()


async def test_smoke_test_between_two_directories_is_not_the_baseline(rolled_back_db):
    """The October 2026 defect in miniature: the latest earlier intake is a
    tiny smoke test; the baseline must be the older comparable directory."""
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


async def test_empty_and_smoke_labelled_intakes_are_never_the_baseline(rolled_back_db):
    """Two intakes that PASS assess_comparability must still be skipped:
    - a header-only PARSED intake (record_count 0 bypasses the size rule — an
      empty population proves absence of nothing);
    - an explicitly labelled smoke test within 10x of the delivery with the
      same schema (the synthetic rule matches only the word "synthetic", and
      here both sides are synthetic fixtures anyway, so it never fires)."""
    db = rolled_back_db
    now = datetime(2001, 11, 5, 12, 0, 0)

    sept_id, _ = await seed_intake(db, make_rows(20), with_job=False)
    await _set_received(db, sept_id, now - timedelta(days=55))

    # Newer: an 18-row smoke test (comparable size, same schema).
    smoke_id, _ = await seed_intake(db, make_rows(18, arc=f"{ARC}.66"), with_job=False)
    smoke = await db.get(m.RceSourceIntake, smoke_id)
    smoke.delivery_label = "SMOKE-TEST 2001-10-16"
    smoke.received_at = now - timedelta(days=20)
    await db.commit()

    # Newer still: 18 data lines whose ids are ALL blank — record_count is
    # positive, but there is no identity population to be absent from.
    blank_rows = make_rows(18, arc=f"{ARC}.55")
    for r in blank_rows:
        r["id"] = ""
    blank_id, _ = await seed_intake(db, blank_rows, with_job=False)
    await _set_received(db, blank_id, now - timedelta(days=15))

    # Newer still: 19 data lines of which only ONE carries a usable id — a
    # degenerate identity population (1 vs the delivery's 21 is >= the 10x
    # rule), even though the line counts look comparable.
    sparse_rows = make_rows(19, arc=f"{ARC}.44")
    for r in sparse_rows[1:]:
        r["id"] = ""
    sparse_id, _ = await seed_intake(db, sparse_rows, with_job=False)
    await _set_received(db, sparse_id, now - timedelta(days=12))

    # Newer still: 19 data lines that all repeat ONE id — the DISTINCT usable
    # population is 1, however many rows carry it.
    dup_rows = make_rows(19, arc=f"{ARC}.33")
    for r in dup_rows:
        r["id"] = f"{ARC}.33.1"
    dup_id, _ = await seed_intake(db, dup_rows, with_job=False)
    await _set_received(db, dup_id, now - timedelta(days=11))

    # Newest: a header-only intake (zero records, PARSED).
    empty_id, _ = await seed_intake(db, [], with_job=False)
    await _set_received(db, empty_id, now - timedelta(days=10))

    oct_id, _ = await seed_intake(db, make_rows(20) + make_rows(1, arc=f"{ARC}.77"),
                                  with_job=False)
    await _set_received(db, oct_id, now)

    from app.tefca_registry.rce.delivery_delta import comparable_previous_delivery
    from app.tefca_registry.rce.quality_engine import _build_dataset_context

    comparable = await comparable_previous_delivery(db, await db.get(m.RceSourceIntake, oct_id))
    # not the empty one, not the blank-id one, not the sparse-id one,
    # not the duplicate-id one, not the smoke test
    assert str(comparable.id) == str(sept_id)
    ctx = await _build_dataset_context(db, oct_id)
    assert ctx["previous_intake_id"] == str(sept_id)
    assert ctx["new_entrant_ids"] == {f"{ARC}.77.1"}


async def test_baseline_search_pages_past_a_batch_of_ineligible_intakes(
        rolled_back_db, monkeypatch):
    """Intake history is append-only: more skipped newer intakes than one scan
    batch must not hide an older valid directory (the batch is a page size,
    never a search bound). Exercised by shrinking the batch to 2 and seeding
    5 ineligible newer intakes."""
    from app.tefca_registry.rce import delivery_delta as dd

    db = rolled_back_db
    now = datetime(2001, 11, 5, 12, 0, 0)

    sept_id, _ = await seed_intake(db, make_rows(20), with_job=False)
    await _set_received(db, sept_id, now - timedelta(days=55))

    for i in range(5):  # five newer header-only intakes, all ineligible
        empty_id, _ = await seed_intake(db, [], with_job=False)
        await _set_received(db, empty_id, now - timedelta(days=30 - i))

    oct_id, _ = await seed_intake(db, make_rows(20), with_job=False)
    await _set_received(db, oct_id, now)

    monkeypatch.setattr(dd, "_BASELINE_SCAN_BATCH", 2)
    comparable = await dd.comparable_previous_delivery(
        db, await db.get(m.RceSourceIntake, oct_id))
    assert comparable is not None and str(comparable.id) == str(sept_id)


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
