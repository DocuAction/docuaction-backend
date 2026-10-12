"""`plan_completion` reads every member's review events in ONE batched read.

Found by profiling the DEV Supervisor Operations dashboard (2026-10-10): `sampling_overview` took 12.2 s of the 16.8 s call because
`plan_completion` issued one query per review-backed member (201 queries for one 1,365-member plan, 471 for the whole dashboard).
The counts must not change; only the number of round trips.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, select

from app.tefca_registry import models as reg
from app.tefca_registry import qhin_sampling as qs

from test_qhin_sampling_operational import (ANALYST, QA, SYN, _build, _case_for_member,  # noqa: F401
                                            rolled_back_db)

pytestmark = pytest.mark.asyncio


async def _reference_plan_completion(db, sample_id):
    """The implementation before this change, kept verbatim as the reference (one query per member)."""
    from app.tefca_registry.qa_gate import _events, is_reportable

    members = (await db.execute(select(reg.SampleEntity).where(reg.SampleEntity.sample_id == sample_id))).scalars().all()
    counts = {"selected": len(members), "no_review_case": 0, "review_pending": 0, "submitted_for_qa": 0, "qa_returned": 0,
              "qa_escalated": 0, "qa_approved": 0}
    for member in members:
        if not member.review_id:
            counts["no_review_case"] += 1
            continue
        events = await _events(db, member.review_id)
        if not events:
            counts["review_pending"] += 1
            continue
        if is_reportable(events):
            counts["qa_approved"] += 1
            continue
        qa = [e for e in events if e.event_type == "QA_REVIEW"]
        if qa and qa[-1].qa_action == "RETURN":
            counts["qa_returned"] += 1
        elif qa and qa[-1].qa_action == "ESCALATE":
            counts["qa_escalated"] += 1
        else:
            counts["submitted_for_qa"] += 1
    return counts


async def _count_queries(db, coro_factory):
    sync_engine = db.bind.engine.sync_engine
    seen = []

    def _hook(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _hook)
    try:
        result = await coro_factory()
    finally:
        event.remove(sync_engine, "before_cursor_execute", _hook)
    return result, len(seen)


async def test_batched_counts_equal_the_per_member_reference_and_use_constant_queries(rolled_back_db):
    from app.tefca_registry.qa_gate import record_analyst_determination, submit_qa_review

    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 12})
    plan = await qs.finalize_plan(db, intake_id, seed=51)
    await db.commit()
    sample_id = uuid.UUID(plan["sample_id"])
    members = (await db.execute(select(reg.SampleEntity).where(reg.SampleEntity.sample_id == sample_id))).scalars().all()
    assert len(members) >= 8

    # Six members get a review case in six different states; the rest have none.
    reviews = [await _case_for_member(db, members[i], intake_id) for i in range(6)]
    await db.commit()
    # 0: no events (pending) · 1: determined only (submitted for QA) · 2: approved · 3: returned · 4: escalated · 5: returned
    await record_analyst_determination(db, reviews[1], user=ANALYST, determination="CONFIRM", rationale="Synthetic determination one.")
    await record_analyst_determination(db, reviews[2], user=ANALYST, determination="CONFIRM", rationale="Synthetic determination two.")
    await submit_qa_review(db, reviews[2], user=QA, qa_action="APPROVE", qa_reason="Synthetic QA approval.")
    await record_analyst_determination(db, reviews[3], user=ANALYST, determination="CONFIRM", rationale="Synthetic determination three.")
    await submit_qa_review(db, reviews[3], user=QA, qa_action="RETURN", qa_reason="Synthetic QA return.")
    await record_analyst_determination(db, reviews[4], user=ANALYST, determination="CONFIRM", rationale="Synthetic determination four.")
    await submit_qa_review(db, reviews[4], user=QA, qa_action="ESCALATE", qa_reason="Synthetic QA escalation.",
                           escalated_to_user_id=QA.id, escalation_reason="Synthetic escalation.")
    await record_analyst_determination(db, reviews[5], user=ANALYST, determination="CONFIRM", rationale="Synthetic determination five.")
    await submit_qa_review(db, reviews[5], user=QA, qa_action="RETURN", qa_reason="Synthetic QA return five.")
    await db.commit()

    expected, ref_queries = await _count_queries(db, lambda: _reference_plan_completion(db, sample_id))
    got, new_queries = await _count_queries(db, lambda: qs.plan_completion(db, sample_id))
    assert got["counts"] == expected
    assert expected["qa_approved"] == 1 and expected["qa_returned"] == 2 and expected["qa_escalated"] == 1
    assert expected["submitted_for_qa"] == 1 and expected["review_pending"] == 1 and expected["no_review_case"] == len(members) - 6
    # the old code issued 2 + one query per review-backed member (6); the batched code issues a constant 3-4 however many members there are
    assert ref_queries >= 2 + 6
    assert new_queries <= 4, f"{new_queries} queries"
    assert new_queries < ref_queries


async def test_a_plan_with_no_review_cases_needs_no_event_read(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 8}, label=f"{SYN}-NOEV")
    plan = await qs.finalize_plan(db, intake_id, seed=52)
    await db.commit()
    got, queries = await _count_queries(db, lambda: qs.plan_completion(db, uuid.UUID(plan["sample_id"])))
    assert got["counts"]["no_review_case"] == got["counts"]["selected"] and got["complete"] is False
    assert queries <= 3          # the sample row, its members and (at most) one flush; no events to read
