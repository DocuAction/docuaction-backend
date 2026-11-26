"""Which evaluation does a reconciliation status come from? (2026-11-26)

The defect: the delivery job stores its reconciliation snapshot before any review exists, and the review cycle
never writes another. `GET .../reconciliation` and the delivery detail went on serving that snapshot's
`passed: true` with nothing to tell a reader it was not "passes now" - while the cycle gate, which evaluates live,
refused the delivery (reproduced in tests/test_review_cycle_batch_gate_characterization_2026_11_26.py).

The fix under test is ADDITIVE and read-only (`reconciliation.status_attribution`):

* every existing response field keeps its value - the stored snapshot's verdict is still reported as stored;
* a new `attribution` block names the evaluation (stored snapshot id / sequence / trigger / time, or a live
  evaluation at request time) and reports what a live read finds now;
* `passed_now` is False when a check re-asked live fails, None when that is NOT ESTABLISHED (the full
  reconciliation was not re-run), and True only for a full live evaluation;
* nothing is written: no snapshot is added, changed or superseded, and no check or gate is altered.

Synthetic data only; sources are recorders; outbound network is refused.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce import routes as rce_routes
from rce_traceability_support import rolled_back_db  # noqa: F401 - fixture
from support_delivery_api import add_stage_events, headers_for, seed_delivery
from test_blank_npi_classified_on_submitted_values_2026_11_25 import _PM
from test_review_cycle_batch_gate_characterization_2026_11_26 import (  # noqa: F401 - `delivery` is a fixture
    ADJUDICATION_CHECK, BATCH, VIEWER, _default_routed, delivery,
)

pytestmark = pytest.mark.usefixtures("no_outbound_network")

QALEAD = SimpleNamespace(id=None, email="qalead@synthetic-test.docuaction.invalid", role="qalead")
#: The response keys of the persisted branch of GET .../reconciliation before this change.
KEYS_BEFORE = {"intake_id", "passed", "populations", "curated_status_counts", "dispositions", "equation",
               "records_without_disposition", "identifier_conflicts", "corrections", "report_links",
               "rule_execution", "dimensions", "checks", "failed_checks", "area1_integrity", "source", "snapshot",
               "note"}


async def _snapshot_facts(d):
    snap = await reconciliation.latest_snapshot(d.db, d.job_id)
    return (str(snap.id), snap.sequence, snap.hash, bool(snap.passed), snap.checks, snap.dimensions,
            await reconciliation.snapshot_history_count(d.db, d.job_id))


@pytest.mark.asyncio
async def test_before_any_review_the_stored_pass_is_attributed_and_nothing_fails_live(delivery):
    d = delivery
    served = await rce_routes.reconciliation(str(d.intake), recompute=False, db=d.db, user=VIEWER)
    assert set(served) == KEYS_BEFORE | {"attribution"}
    assert served["passed"] is True and served["source"] == "persisted_snapshot"
    a = served["attribution"]
    assert a["status_source"] == reconciliation.SOURCE_SNAPSHOT
    assert a["snapshot"]["id"] == d.snapshot_id and a["snapshot"]["sequence"] == d.snapshot_sequence
    assert a["snapshot"]["trigger"] == "PIPELINE" and a["snapshot"]["passed"] is True
    assert a["snapshot"]["created_at"] is not None and a["evaluated_at"]
    assert a["live_checks_evaluated"] == [ADJUDICATION_CHECK] and a["live_failed_checks"] == []
    assert a["passed_now"] is None                       # NOT established: the full reconciliation was not re-run
    assert a["reviews"] == {"at_snapshot": 0, "now": 0}
    assert a["default_routed_unadjudicated"] == {"at_snapshot": 0, "now": 0}
    assert a["snapshot_predates_review_activity"] is False
    assert "not established" in a["statement"]


@pytest.mark.asyncio
async def test_after_a_batch_the_stored_pass_is_still_reported_as_stored_and_the_live_failure_is_stated(delivery):
    d = delivery
    before = await _snapshot_facts(d)
    untouched = await rce_routes.reconciliation(str(d.intake), recompute=False, db=d.db, user=VIEWER)
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    waiting = len(await _default_routed(d.db, d.entities))
    assert waiting >= 1

    served = await rce_routes.reconciliation(str(d.intake), recompute=False, db=d.db, user=VIEWER)
    # every field that existed before this change has the value it had before the batch: the snapshot is served
    for key in KEYS_BEFORE:
        assert served[key] == untouched[key], key
    assert served["passed"] is True and served["failed_checks"] == []
    a = served["attribution"]
    assert a["status_source"] == reconciliation.SOURCE_SNAPSHOT and a["snapshot"]["id"] == d.snapshot_id
    assert a["passed_now"] is False and a["live_failed_checks"] == [ADJUDICATION_CHECK]
    assert a["reviews"] == {"at_snapshot": 0, "now": BATCH}
    assert a["default_routed_unadjudicated"] == {"at_snapshot": 0, "now": waiting}
    assert a["snapshot_predates_review_activity"] is True
    assert f"{waiting} default-routed (DEFAULT-UNMATCHED) review(s) await an analyst resolution" in a["statement"]
    assert "does NOT pass now" in a["statement"] and "Stored snapshot 1 (PIPELINE" in a["statement"]
    assert "does not compare the stored snapshot" in a["current_field_meaning"]

    # the count is the gate's own: the same statement, so it cannot say something the gate does not
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert live["dimensions"]["verification"]["default_unadjudicated"] == waiting
    assert [c["check"] for c in live["failed_checks"]] == a["live_failed_checks"]
    # read-only: the stored snapshot is byte-for-byte what it was, and no snapshot was added
    assert await _snapshot_facts(d) == before


@pytest.mark.asyncio
async def test_a_live_evaluation_is_attributed_as_live_and_agrees_with_its_own_result(delivery):
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    live = await rce_routes.reconciliation(str(d.intake), recompute=True, db=d.db, user=QALEAD)
    assert live["source"] == "live_recompute" and live["passed"] is False
    a = live["attribution"]
    assert a["status_source"] == reconciliation.SOURCE_LIVE and a["snapshot"] is None
    assert a["live_checks_evaluated"] == "all" and a["passed_now"] is False
    assert a["live_failed_checks"] == [c["check"] for c in live["failed_checks"]] == [ADJUDICATION_CHECK]
    assert a["default_routed_unadjudicated"]["now"] == live["dimensions"]["verification"]["default_unadjudicated"]


@pytest.mark.asyncio
async def test_after_adjudication_the_live_failure_clears_but_a_pass_is_not_claimed(delivery):
    """An analyst resolution clears the re-asked check. The attribution then says NOT ESTABLISHED - never
    "passed" - because the snapshot still predates the reviews and the other checks were not re-run."""
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    for review in await _default_routed(d.db, d.entities):
        review.reviewer_resolution = "confirmed"
    await d.db.flush()
    a = await reconciliation.status_attribution(
        d.db, d.intake, snapshot=await reconciliation.latest_snapshot(d.db, d.job_id))
    assert a["live_failed_checks"] == [] and a["passed_now"] is None
    assert a["default_routed_unadjudicated"]["now"] == 0
    assert a["reviews"] == {"at_snapshot": 0, "now": BATCH} and a["snapshot_predates_review_activity"] is True
    assert "predates review activity" in a["statement"] and "not established" in a["statement"]


@pytest.mark.asyncio
async def test_a_delivery_with_no_snapshot_says_so(delivery):
    d = delivery
    a = await reconciliation.status_attribution(d.db, d.intake, snapshot=None)
    assert a["status_source"] == reconciliation.SOURCE_NONE and a["snapshot"] is None
    assert a["passed_now"] is None and a["snapshot_predates_review_activity"] is None
    assert a["statement"].startswith("No reconciliation snapshot is stored for this delivery.")


@pytest.mark.asyncio
async def test_the_adjudication_check_itself_is_unchanged(delivery):
    """The statement was moved into a function, not altered: same name, same detail text, same count."""
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    waiting = len(await _default_routed(d.db, d.entities))
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    check = next(c for c in live["checks"] if c["check"] == "Every default-routed determination is adjudicated")
    assert check == {"check": "Every default-routed determination is adjudicated", "passed": False,
                     "detail": f"{waiting} default-routed (DEFAULT-UNMATCHED) review(s) await an analyst resolution"}
    assert await reconciliation.default_routed_unadjudicated(d.db, d.intake) == waiting
    assert len(live["checks"]) == d.checks_at_job_end                  # no check added or removed


# ── the delivery detail (committed data, real HTTP route) ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def committed_delivery(db_available):
    if not db_available:
        pytest.skip("No database reachable at DATABASE_URL.")
    d = seed_delivery(state="SUCCEEDED")
    add_stage_events(d["job_id"], d["intake_id"])
    return d


def test_the_delivery_detail_carries_the_attribution_beside_the_fields_it_already_had(client, committed_delivery):
    r = client.get(f"/api/tefca/rce/delivery-jobs/{committed_delivery['job_id']}/detail",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    block = r.json()["reconciliation"]
    assert set(block) == {"snapshot", "history_count", "disposition_processing", "current", "attribution"}
    assert block["current"] is True                                    # unchanged meaning, unchanged value
    a = block["attribution"]
    assert a["status_source"] == ("persisted_snapshot" if block["snapshot"] else "no_snapshot")
    assert a["passed_now"] is None and a["live_failed_checks"] == []
    assert a["live_checks_evaluated"] == ["Every default-routed determination is adjudicated"]
    assert a["reviews"]["now"] == 0 and a["default_routed_unadjudicated"]["now"] == 0
    assert "current_field_meaning" in a
