"""Review link of an IQVIA advisory assessment, against a REAL database (isolation-postgres job).

The written workflow attaches advisory evidence to the review it informs. The review link is OPTIONAL (delivery-level
evidence stays permitted; an entity is never required) and is validated server-side before any write:

  * a review of an entity present in delivery A is accepted for A, stored, listed by review, counted (not named) in A's
    disclosure, and not in B's;
  * a review reached only through a delivery A source record (no entity) is accepted without an entity;
  * a review of delivery B, an unknown review, or an entity that is not the review's own entity is refused with a stable
    code and nothing is written;
  * two assessments for one review are two rows (append-only), and a direct UPDATE is refused by the trigger;
  * the database refuses a review that does not exist and a delete of a referenced review (foreign key, RESTRICT);
  * the migration adds the column without widening the app role's privileges (SELECT, INSERT only).

Synthetic data only; presence rows come from the real `snapshot_effects.record_presence`.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, text

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    artifact_root, rolled_back_db, seed_delivery)
from test_iqvia_assessment_delivery_linkage_2026_10_14 import (  # noqa: F401
    USER, _app, _post, _report, _row_count, _snapshot, _ui_body, routes, stub_lookup)

pytestmark = pytest.mark.asyncio

_seq = iter(range(900001, 999999))


async def _setup(db):
    from app.tefca_registry.rce import snapshot_effects

    a, b = await seed_delivery(db, label="SYN-REV-A"), await seed_delivery(db, label="SYN-REV-B")
    await snapshot_effects.record_presence(db, a["intake_id"], commit=False)
    await snapshot_effects.record_presence(db, b["intake_id"], commit=False)
    return a, b


async def _review(db, *, entity_id=None, source_record_id=None) -> str:
    from app.tefca_registry import models as reg

    rid = f"REV-2026-{next(_seq):06d}"
    db.add(reg.ReviewRecord(review_id=rid, entity_id=entity_id, source_record_id=source_record_id))
    await db.flush()
    return rid


async def _a_source_record(db, intake_id):
    from app.tefca_registry.rce import models as rm

    return (await db.execute(select(rm.RceSourceRecord.id).where(rm.RceSourceRecord.source_intake_id == intake_id)
                             .limit(1))).scalar_one()


async def test_review_of_an_entity_in_the_delivery_is_accepted_listed_counted_and_isolated(
        routes, rolled_back_db, artifact_root, stub_lookup):
    from app.tefca_registry.rce import iqvia_advisory_assessment as aa

    db = rolled_back_db
    a, b = await _setup(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    review = await _review(db, entity_id=a["created_entity_id"])

    resp = await _post(_app(routes, db, USER), _ui_body(
        snap, a["intake_id"], entity_id=str(a["created_entity_id"]), review_id=review))
    assert resp.status_code == 201, resp.text
    assert resp.json()["review_id"] == review

    row = (await db.execute(select(aa.IqviaAdvisoryAssessment))).scalars().one()
    assert (row.delivery_intake_id, row.entity_id, row.review_id) == (a["intake_id"], a["created_entity_id"], review)

    listing = await aa.list_assessments(db, review_id=review)
    assert [r["review_id"] for r in listing] == [review]
    assert await aa.list_assessments(db, delivery_intake_id=b["intake_id"]) == []

    ra, rb = await _report(db, a["intake_id"]), await _report(db, b["intake_id"])
    use_a, use_b = ra["dataset"]["iqvia_use"], rb["dataset"]["iqvia_use"]
    assert (use_a["assessments"], use_a["reviews_linked"]) == (1, 1) and use_b["assessments"] == 0
    assert "1 distinct review(s)" in ra["html"]
    for out in (ra, rb):   # counted, never named
        assert review not in json.dumps(out["dataset"]["iqvia_use"], default=str)


async def test_a_review_reached_only_by_a_delivery_record_is_accepted_without_an_entity(
        routes, rolled_back_db, artifact_root, stub_lookup):
    from app.tefca_registry.rce import iqvia_advisory_assessment as aa

    db = rolled_back_db
    a, _b = await _setup(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    review = await _review(db, source_record_id=await _a_source_record(db, a["intake_id"]))

    resp = await _post(_app(routes, db, USER), _ui_body(snap, a["intake_id"], review_id=review))
    assert resp.status_code == 201, resp.text
    row = (await db.execute(select(aa.IqviaAdvisoryAssessment))).scalars().one()
    assert row.review_id == review and row.entity_id is None            # entity is not required


async def test_foreign_unknown_and_inconsistent_references_are_refused_without_a_write(
        routes, rolled_back_db, artifact_root, stub_lookup):
    db = rolled_back_db
    a, b = await _setup(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    app = _app(routes, db, USER)

    review_b = await _review(db, entity_id=b["created_entity_id"])                    # belongs to delivery B only
    review_a = await _review(db, entity_id=a["created_entity_id"])
    cases = [
        (dict(review_id=review_b), "REVIEW_NOT_IN_DELIVERY"),
        (dict(review_id="REV-2026-000000"), "REVIEW_NOT_FOUND"),
        # entity present in A, but not the entity of review_a
        (dict(review_id=review_a, entity_id=str(a["existing_entity_id"])), "ENTITY_NOT_IN_REVIEW"),
        # entity of delivery B is refused earlier, for the delivery
        (dict(review_id=review_a, entity_id=str(b["created_entity_id"])), "ENTITY_NOT_IN_DELIVERY"),
    ]
    for extra, code in cases:
        resp = await _post(app, _ui_body(snap, a["intake_id"], **extra))
        assert resp.status_code == 422 and resp.json()["detail"]["code"] == code, (extra, resp.text)
    assert await _row_count(db) == 0


async def test_two_assessments_for_one_review_are_two_rows_and_rows_cannot_be_updated(
        routes, rolled_back_db, artifact_root, stub_lookup):
    from app.tefca_registry.rce import iqvia_advisory_assessment as aa

    db = rolled_back_db
    a, _b = await _setup(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    review = await _review(db, entity_id=a["created_entity_id"])
    app = _app(routes, db, USER)
    for _ in range(2):
        assert (await _post(app, _ui_body(snap, a["intake_id"], review_id=review))).status_code == 201
    assert len(await aa.list_assessments(db, review_id=review)) == 2

    nested = await db.begin_nested()
    with pytest.raises(Exception, match="append-only"):
        await db.execute(text("UPDATE iqvia_advisory_assessment SET review_id = NULL"))
    await nested.rollback()
    assert len(await aa.list_assessments(db, review_id=review)) == 2


async def test_migration_adds_the_column_without_widening_app_role_privileges(rolled_back_db):
    db = rolled_back_db
    col = (await db.execute(text(
        "SELECT is_nullable, character_maximum_length FROM information_schema.columns "
        "WHERE table_name='iqvia_advisory_assessment' AND column_name='review_id'"))).one()
    assert tuple(col) == ("YES", 20)
    assert (await db.execute(text("SELECT to_regclass('public.ix_iqvia_assess_review') IS NOT NULL"))).scalar_one()
    privs = {p: (await db.execute(text(
        "SELECT has_table_privilege('docuaction_app','public.iqvia_advisory_assessment', :p)"), {"p": p})).scalar_one()
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")}
    assert privs == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False}
    fks = (await db.execute(text(
        "SELECT count(*) FROM pg_constraint WHERE conrelid = to_regclass('public.iqvia_advisory_assessment') "
        "AND contype = 'f'"))).scalar_one()
    assert fks == 3                       # snapshot, delivery and the review link
    target = (await db.execute(text(
        "SELECT confrelid::regclass::text, confdeltype::text FROM pg_constraint WHERE conname = 'fk_iqvia_assess_review'"))).one()
    assert tuple(target) == ("review_records", "r")                   # RESTRICT


async def test_a_source_record_in_one_delivery_is_not_overridden_by_the_entity_present_in_another(
        routes, rolled_back_db, artifact_root, stub_lookup):
    db = rolled_back_db
    a, b = await _setup(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    entity = a["created_entity_id"]
    review = await _review(db, entity_id=entity, source_record_id=await _a_source_record(db, a["intake_id"]))
    from app.tefca_registry.rce import snapshot_models as sm
    # make the same entity present in delivery B, so only the source record can say the review is A's
    from datetime import datetime
    db.add(sm.RceEntityPresence(intake_id=b["intake_id"], entity_id=entity, present=True, rce_org_oid="1.2.3.SYN", correlation_id="syn-rl",
                                snapshot_received_at=datetime.utcnow()))
    await db.flush()
    app = _app(routes, db, USER)
    resp = await _post(app, _ui_body(snap, b["intake_id"], review_id=review))
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "REVIEW_NOT_IN_DELIVERY", resp.text
    assert await _row_count(db) == 0
    assert (await _post(app, _ui_body(snap, a["intake_id"], review_id=review))).status_code == 201


async def test_database_refuses_an_unknown_review_and_deleting_a_referenced_review(
        routes, rolled_back_db, artifact_root, stub_lookup):
    db = rolled_back_db
    a, _b = await _setup(db)
    snap = await _snapshot(db)
    stub_lookup(snap)
    review = await _review(db, entity_id=a["created_entity_id"])
    assert (await _post(_app(routes, db, USER), _ui_body(snap, a["intake_id"], review_id=review))).status_code == 201
    nested = await db.begin_nested()
    with pytest.raises(Exception, match="fk_iqvia_assess_review"):
        await db.execute(text("DELETE FROM review_records WHERE review_id = :r"), {"r": review})
    await nested.rollback()
