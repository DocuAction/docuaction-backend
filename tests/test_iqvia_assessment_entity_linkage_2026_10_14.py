"""Entity linkage of an IQVIA advisory assessment, against a REAL database (isolation-postgres job).

The assessment is recorded against one delivery; an analyst may also name the registry entity the lookup concerned.
The server must accept that entity ONLY if it is genuinely present in THAT delivery:

  * an entity present in delivery A is accepted for A and stored (`entity_id`), and A's disclosure is unchanged
    (still delivery-level, identifier-free: no entity id appears in the CSV or HTML);
  * an entity that belongs to delivery B is refused for A (ENTITY_NOT_IN_DELIVERY) and nothing is written;
  * an entity with an ABSENT presence row for A (`present = false`: carried by the previous delivery, not this one)
    is refused too. Before this fix any presence row, present or absent, passed the check.

Synthetic data only. Presence rows come from the real `snapshot_effects.record_presence` path.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    artifact_root, rolled_back_db, seed_delivery)
from test_iqvia_assessment_delivery_linkage_2026_10_14 import (  # noqa: F401
    USER, _app, _post, _report, _row_count, _snapshot, _ui_body, routes, stub_lookup)

pytestmark = pytest.mark.asyncio


async def _two_deliveries_with_presence(db):
    from app.tefca_registry.rce import snapshot_effects

    a, b = await seed_delivery(db, label="SYN-ENT-A"), await seed_delivery(db, label="SYN-ENT-B")
    await snapshot_effects.record_presence(db, a["intake_id"], commit=False)
    await snapshot_effects.record_presence(db, b["intake_id"], commit=False)
    return a, b


async def test_entity_in_the_delivery_is_accepted_stored_and_not_disclosed(routes, rolled_back_db, artifact_root,
                                                                           stub_lookup):
    from app.tefca_registry.rce import iqvia_advisory_assessment as aa

    db = rolled_back_db
    a, b = await _two_deliveries_with_presence(db)
    snap = await _snapshot(db)
    stub_lookup(snap)

    resp = await _post(_app(routes, db, USER), _ui_body(snap, a["intake_id"], entity_id=str(a["created_entity_id"])))
    assert resp.status_code == 201, resp.text

    row = (await db.execute(select(aa.IqviaAdvisoryAssessment))).scalars().one()
    assert row.delivery_intake_id == a["intake_id"] and row.entity_id == a["created_entity_id"]
    assert resp.json()["entity_id"] is None          # reviewer view: the entity id is for qalead and above

    ra, rb = await _report(db, a["intake_id"]), await _report(db, b["intake_id"])
    assert ["State", "IQVIA_ADVISORY_USED"] in list(csv.reader(io.StringIO(ra["csv"])))
    assert ra["dataset"]["iqvia_use"]["assessments"] == 1 and rb["dataset"]["iqvia_use"]["assessments"] == 0
    # The delivery report's record-level sections legitimately list entity ids; the IQVIA disclosure itself must not.
    for out in (ra, rb):
        assert str(a["created_entity_id"]) not in json.dumps(out["dataset"]["iqvia_use"], default=str)


async def test_entity_of_another_delivery_is_refused_and_nothing_is_written(routes, rolled_back_db, artifact_root,
                                                                            stub_lookup):
    db = rolled_back_db
    a, b = await _two_deliveries_with_presence(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    app = _app(routes, db, USER)

    for foreign in (b["created_entity_id"], b["existing_entity_id"]):
        resp = await _post(app, _ui_body(snap, a["intake_id"], entity_id=str(foreign)))
        assert resp.status_code == 422 and resp.json()["detail"]["code"] == "ENTITY_NOT_IN_DELIVERY"
    assert await _row_count(db) == 0


async def test_an_absent_presence_row_does_not_count_as_in_the_delivery(routes, rolled_back_db, artifact_root,
                                                                         stub_lookup):
    from app.tefca_registry.rce import snapshot_models as sm

    db = rolled_back_db
    a, b = await _two_deliveries_with_presence(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    # B's entity is recorded for A as ABSENT (the previous delivery carried it, A does not).
    db.add(sm.RceEntityPresence(
        id=uuid.uuid4(), entity_id=b["created_entity_id"], intake_id=a["intake_id"], rce_org_oid="9.99.ABSENT",
        present=False, snapshot_received_at=datetime.now(timezone.utc), correlation_id="entity-linkage-test"))
    await db.flush()

    resp = await _post(_app(routes, db, USER), _ui_body(snap, a["intake_id"], entity_id=str(b["created_entity_id"])))
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "ENTITY_NOT_IN_DELIVERY"
    assert await _row_count(db) == 0
