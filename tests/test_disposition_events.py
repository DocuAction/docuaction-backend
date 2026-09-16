"""Dispositions: seven categories, append-only, current view, the equation.

    Received = Created + Updated + Matched/Unchanged + Held + Rejected
             + Missing Key + Excluded
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import dispositions as disp
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import traceability_models as tm
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    QHIN_OID, SYN, curated_by_oid, curated_direct, make_rows, rolled_back_db,
    seed_entity, seed_intake, source_by_oid,
)


@pytest.fixture
async def seven(rolled_back_db, monkeypatch):
    """One row per disposition category, promoted once. Returns (db, intake, rows, job)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "RCE_EXCLUDE_TEST_RECORDS", True, raising=False)
    db = rolled_back_db
    rows = make_rows(7)
    # 2: matched, name differs -> UPDATED.   3: matched identical -> UNCHANGED.
    await seed_entity(db, oid=rows[1]["id"], name="OLD SYNTHETIC NAME",
                      tefcaid=rows[1]["TEFCAID"])
    await seed_entity(db, oid=rows[2]["id"], name=rows[2]["name"],
                      tefcaid=rows[2]["TEFCAID"])
    # 5: unparseable line -> REJECTED.
    intake_id, job = await seed_intake(db, rows, parse_status={5: "field_count_mismatch"})
    await curated_direct(
        db, intake_id, rows,
        statuses={4: "HELD", 5: "REJECTED"},
        overrides={4: {"status_reason": "synthetic quality hold", "issue_count": 1},
                   5: {"status_reason": "unparseable"},
                   6: {"name": None},                    # MISSING_KEY
                   7: {"is_test_record": True}})         # EXCLUDED
    result = await promote_delivery(db, intake_id, actor=SYN)
    return db, intake_id, rows, job, result


async def test_every_category_is_written_exactly_once(seven):
    db, intake_id, rows, job, result = seven
    counts = await disp.counts_for_intake(db, intake_id)
    assert counts == {"CREATED": 1, "UPDATED": 1, "MATCHED_UNCHANGED": 1, "HELD": 1,
                      "REJECTED": 1, "MISSING_KEY": 1, "EXCLUDED": 1, "total": 7}
    assert await disp.records_without_disposition(db, intake_id) == 0
    assert result["entities_created"] == 1
    assert result["entities_updated"] == 1
    assert result["entities_unchanged"] == 1
    assert result["not_promoted_by_status"] == {"HELD": 1, "REJECTED": 1,
                                                "MISSING_KEY": 1, "EXCLUDED": 1}
    assert result["dispositions_written_this_run"] == {
        "CREATED": 1, "UPDATED": 1, "MATCHED_UNCHANGED": 1, "HELD": 1,
        "REJECTED": 1, "MISSING_KEY": 1, "EXCLUDED": 1}


async def test_reason_codes_follow_the_contract(seven):
    db, intake_id, rows, job, _ = seven
    current = {c["source_rce_id"]: c for c in await disp.current_for_intake(db, intake_id)}
    expect = {
        rows[0]["id"]: ("CREATED", disp.REASON_CREATED),
        rows[1]["id"]: ("UPDATED", disp.REASON_UPDATED),
        rows[2]["id"]: ("MATCHED_UNCHANGED", disp.REASON_UNCHANGED),
        rows[3]["id"]: ("HELD", disp.REASON_HELD_QUALITY),
        rows[4]["id"]: ("REJECTED", disp.REASON_REJECTED_PARSE),
        rows[5]["id"]: ("MISSING_KEY", disp.REASON_MISSING_KEY),
        rows[6]["id"]: ("EXCLUDED", disp.REASON_EXCLUDED_TEST),
    }
    for oid, (disposition, reason_code) in expect.items():
        row = current[oid]
        assert (row["disposition"], row["reason_code"]) == (disposition, reason_code), oid
        assert row["sequence"] == 1
        assert row["actor_type"] == "SYSTEM"
        assert str(row["job_id"]) == str(job.id)
    assert current[rows[1]["id"]]["changed_fields"] == ["name"]
    assert current[rows[1]["id"]]["entity_id"] is not None
    assert current[rows[3]["id"]]["entity_id"] is None


async def test_the_equation_holds_against_received(seven):
    db, intake_id, rows, job, _ = seven
    eq = disp.equation(await disp.counts_for_intake(db, intake_id), received=7)
    assert eq["holds"] is True and eq["difference"] == 0 and eq["accounted"] == 7
    short = disp.equation(await disp.counts_for_intake(db, intake_id), received=8)
    assert short["holds"] is False and short["difference"] == 1


async def test_updated_writes_a_version_row_and_unchanged_touches_nothing(seven):
    db, intake_id, rows, job, _ = seven
    updated = await curated_by_oid(db, intake_id, rows[1]["id"])
    unchanged = await curated_by_oid(db, intake_id, rows[2]["id"])

    versions_updated = (await db.execute(
        select(reg.TefcaEntityVersion).where(
            reg.TefcaEntityVersion.entity_id == updated.canonical_entity_id)
        .order_by(reg.TefcaEntityVersion.version_number))).scalars().all()
    assert [v.version_number for v in versions_updated] == [1, 2]
    snap = versions_updated[1].snapshot_data
    assert snap["changed_fields"] == ["name"]
    assert snap["before"]["name"] == "OLD SYNTHETIC NAME"
    assert snap["after"]["name"] == rows[1]["name"]
    entity = await db.get(reg.TefcaRegEntity, updated.canonical_entity_id)
    assert entity.name == rows[1]["name"] and entity.current_version == 2
    audit = (await db.execute(
        select(reg.TefcaRegAuditLog).where(
            reg.TefcaRegAuditLog.entity_id == updated.canonical_entity_id,
            reg.TefcaRegAuditLog.action == "entity_updated"))).scalars().all()
    assert len(audit) == 1 and audit[0].metadata_["changed_fields"] == ["name"]

    versions_unchanged = (await db.execute(
        select(func.count()).select_from(reg.TefcaEntityVersion).where(
            reg.TefcaEntityVersion.entity_id == unchanged.canonical_entity_id))).scalar()
    assert versions_unchanged == 1, "MATCHED_UNCHANGED writes no version row"
    entity2 = await db.get(reg.TefcaRegEntity, unchanged.canonical_entity_id)
    assert entity2.current_version == 1
    audit2 = (await db.execute(
        select(func.count()).select_from(reg.TefcaRegAuditLog).where(
            reg.TefcaRegAuditLog.entity_id == unchanged.canonical_entity_id,
            reg.TefcaRegAuditLog.action == "entity_updated"))).scalar()
    assert audit2 == 0


async def test_area1_promotion_status_mirrors_the_disposition(seven):
    db, intake_id, rows, job, _ = seven
    expect = {rows[0]["id"]: "promoted", rows[1]["id"]: "promoted",
              rows[2]["id"]: "promoted", rows[3]["id"]: "held",
              rows[4]["id"]: "excluded", rows[5]["id"]: "excluded",
              rows[6]["id"]: "excluded"}
    for oid, status in expect.items():
        src = await source_by_oid(db, intake_id, oid)
        await db.refresh(src)
        assert src.promotion_status == status, oid


async def test_a_rerun_appends_nothing(seven):
    db, intake_id, rows, job, _ = seven
    before = (await db.execute(
        select(func.count()).select_from(tm.RceDispositionEvent).where(
            tm.RceDispositionEvent.intake_id == intake_id))).scalar()
    again = await promote_delivery(db, intake_id, actor=SYN)
    after = (await db.execute(
        select(func.count()).select_from(tm.RceDispositionEvent).where(
            tm.RceDispositionEvent.intake_id == intake_id))).scalar()
    assert before == after == 7
    assert again["dispositions_written_this_run"] == {}
    assert again["entities_created"] == 0 and again["entities_updated"] == 0


async def test_history_is_append_only_and_current_is_the_highest_sequence(seven):
    db, intake_id, rows, job, _ = seven
    held = await curated_by_oid(db, intake_id, rows[3]["id"])
    second = await disp.record(
        db, intake_id=intake_id, source_record_id=held.source_record_id,
        disposition="CREATED", reason_code=disp.REASON_ANALYST,
        reason="synthetic analyst release", curated_record_id=held.id,
        actor="analyst@example.test", actor_type="HUMAN", commit=True)
    assert second.sequence == 2
    history = await disp.history_for_record(db, held.source_record_id)
    assert [h["sequence"] for h in history] == [1, 2]
    assert [h["disposition"] for h in history] == ["HELD", "CREATED"]
    current = {c["source_rce_id"]: c for c in await disp.current_for_intake(db, intake_id)}
    assert current[rows[3]["id"]]["disposition"] == "CREATED"
    assert current[rows[3]["id"]]["actor_type"] == "HUMAN"
    counts = await disp.counts_for_intake(db, intake_id)
    assert counts["HELD"] == 0 and counts["CREATED"] == 2 and counts["total"] == 7


async def test_a_bad_disposition_or_actor_type_is_refused(seven):
    db, intake_id, rows, job, _ = seven
    held = await curated_by_oid(db, intake_id, rows[3]["id"])
    with pytest.raises(ValueError):
        await disp.record(db, intake_id=intake_id, source_record_id=held.source_record_id,
                          disposition="RELEASED", reason_code="X")
    with pytest.raises(ValueError):
        await disp.record(db, intake_id=intake_id, source_record_id=held.source_record_id,
                          disposition="HELD", reason_code="X", actor_type="ROBOT")


async def test_material_changes_ignore_whitespace_and_treat_empty_as_none():
    assert disp.material_changes({"name": " A "}, {"name": "A"}, ["name"]) == []
    assert disp.material_changes({"name": None}, {"name": ""}, ["name"]) == []
    assert disp.material_changes({"name": "A"}, {"name": "B"}, ["name"]) == ["name"]
    assert disp.material_changes({"is_active": True}, {"is_active": False},
                                 ["is_active"]) == ["is_active"]
