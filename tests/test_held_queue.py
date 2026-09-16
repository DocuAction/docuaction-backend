"""Held records enter the review work queue once; a second bridge run adds none.

Two ways a record is held: a quality finding (NPI-002 length) and a promotion-
time identifier conflict (NPI-008). Both are HUMAN_REQUIRED, both become one
case each, and the conflict case carries the submitted and existing values.
"""

from __future__ import annotations

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import dq_review_bridge as bridge
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_REGISTERED, NPI_VALID_OTHER, SYN, curated_by_oid, make_rows,
    rolled_back_db, run_quality_and_curation, seed_entity, seed_intake,
)


async def _cases(db, intake_id):
    return (await db.execute(
        select(reg.ReviewRecord).where(
            reg.ReviewRecord.verification_results["queue_source"].astext
            == bridge.QUEUE_SOURCE,
            reg.ReviewRecord.verification_results["source_intake_id"].astext
            == str(intake_id)).order_by(reg.ReviewRecord.review_id))).scalars().all()


async def test_held_records_enter_the_queue_exactly_once(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.31")
    rows[0]["NPI"] = "12345"                # NPI-002 LENGTH -> HELD
    rows[1]["NPI"] = NPI_VALID_OTHER        # conflict with the registered value
    await seed_entity(db, oid=rows[1]["id"], name=rows[1]["name"], npi=NPI_REGISTERED)
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)

    # After curation: the quality hold is a case; the conflict does not exist yet.
    first = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    assert first["cases_created"] == 1
    assert first["pre_promotion_cases"] == 1
    cases = await _cases(db, intake_id)
    assert len(cases) == 1
    assert cases[0].verification_results["rule_ids"] == ["NPI-002"]
    assert cases[0].verification_results["issue_types"] == ["NPI_LENGTH_INVALID"]
    assert cases[0].verification_results["submitted_value"] == "12345"
    assert cases[0].entity_id is None and cases[0].source_record_id is not None
    assert cases[0].classification_bucket is None
    assert cases[0].reviewer_resolution is None and cases[0].reportable_at is None

    # After promotion: the identifier conflict is the one new case.
    result = await promote_delivery(db, intake_id, actor=SYN)
    assert result["conflicts_raised"] == 1
    second = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    assert second["cases_created"] == 1
    assert second["cases_already_present"] == 1
    cases = {c.verification_results["rule_ids"][0]: c for c in await _cases(db, intake_id)}
    assert set(cases) == {"NPI-002", "NPI-008"}
    conflict_case = cases["NPI-008"]
    assert conflict_case.verification_results["issue_types"] == ["NPI_EXISTING_VALUE_CONFLICT"]
    assert conflict_case.verification_results["submitted_value"] == NPI_VALID_OTHER
    assert conflict_case.verification_results["existing_value"] == NPI_REGISTERED
    assert conflict_case.verification_results["case_classification"] == "IDENTITY"
    assert conflict_case.verification_results["severity"] == "HIGH"
    values = conflict_case.verification_results["values"]
    assert values[0]["field_name"] == "NPI"
    assert values[0]["existing_value"] == NPI_REGISTERED

    # A third pass adds nothing.
    third = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    assert third["cases_created"] == 0 and third["cases_already_present"] == 2
    assert len(await _cases(db, intake_id)) == 2

    # The clean record raised no case at all.
    clean = await curated_by_oid(db, intake_id, rows[2]["id"])
    assert clean.canonical_entity_id is not None
    assert not [c for c in await _cases(db, intake_id)
                if str(c.source_record_id) == str(clean.source_record_id)]


async def test_plan_includes_the_conflict_issue_as_human_required(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.32")
    rows[0]["NPI"] = NPI_VALID_OTHER
    await seed_entity(db, oid=rows[0]["id"], name=rows[0]["name"], npi=NPI_REGISTERED)
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    plan = await bridge.plan_cases(db, intake_id)
    assert plan["planned_cases"] == 1
    case = plan["cases"][0]
    assert case["rule_ids"] == ["NPI-008"]
    assert case["submitted_value"] == NPI_VALID_OTHER
    assert case["existing_value"] == NPI_REGISTERED
    assert case["record_status"] == "HELD"
    assert bridge.classification_for("NPI-008") == "IDENTITY"
    for rid in ("NPI-004", "NPI-005", "NPI-006", "NPI-009"):
        assert bridge.classification_for(rid) == "IDENTITY"


async def test_open_cases_lists_the_held_work_for_the_delivery(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.33")
    rows[0]["NPI"] = "ABCDEFGHIJ"           # NPI-004 FORMAT -> HELD
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    open_cases = await bridge.open_cases(db, intake_id)
    assert len(open_cases) == 1
    assert open_cases[0]["rule_ids"] == ["NPI-004"]
    assert open_cases[0]["issue_types"] == ["NPI_FORMAT_INVALID"]
    assert open_cases[0]["reportable"] is False
