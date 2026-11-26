"""When may a review cycle link a member to a review that already exists? (2026-11-26)

Owner: the recovery link "links only the same delivered record and entity, respects the owning cycle and assessment
context, and cannot reuse a stale review or link one already owned elsewhere."

The situation: the writer commits its reviews before the cycle commits the links, so a crash between the two leaves
members UNLINKED whose own review (classified on submitted values) already exists. The next call links such a member
to that review - no new review, no source asked - but ONLY when every condition of
`review_cycle._own_existing_review` holds. Each test below breaks exactly one condition after the same synthetic
crash and shows: that member is refused with the reason for that condition (kind `already_reviewed`, the review id a
key of its own, in the response and in the audit row), the other three members are linked, no review is created and
no source is asked. The positive case and the two "no effect" cases follow, then the terminal signal and the lock.

Synthetic data only; sources are recorders; the file runs with outbound network refused.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import assessment_context as ac
from app.tefca_registry.rce import models as m
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401 - fixture
from test_blank_npi_classified_on_submitted_values_2026_11_25 import (  # noqa: F401 - `world` is a fixture
    _PM, _crash_between_reviews_and_links, _give_qhin, _real_cycle, _review_ids, _review_of, world,
)

pytestmark = pytest.mark.asyncio


async def _crashed(w, monkeypatch):
    """The synthetic crash: four reviews committed by the cycle, no member linked. Returns plain values."""
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    intake_b, entities, record_ids = await _crash_between_reviews_and_links(w, monkeypatch)
    sample = (await w.db.execute(select(reg.ReviewSample).where(
        reg.ReviewSample.strata_config["source_intake_id"].astext == str(intake_b)))).scalars().one()
    target = (await w.db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.source_record_id == record_ids[0]))).scalars().one()
    w.sources.npi_calls.clear()
    w.sources.name_calls.clear()
    return SimpleNamespace(intake=intake_b, entities=entities, record_ids=record_ids, sample_id=sample.id,
                           drawn_at=sample.drawn_at, target=target, target_id=target.review_id,
                           target_entity=target.entity_id, existing=await _review_ids(w.db, record_ids))


async def test_the_crash_leaves_reviews_stamped_with_their_cycle_and_invisible_to_sample_scoped_reads(
        world, monkeypatch):
    """The writer stamps the creating sample in the commit that creates the review - and does NOT set
    `ReviewRecord.sample_id`, the column reports and queues scope on, which is still written only when the member
    is linked. A review made by the supplementary verify path carries no stamp."""
    w = world
    c = await _crashed(w, monkeypatch)
    for review_id in c.existing:
        review = await _review_of(w.db, review_id)
        assert review.verification_results[ap.CREATED_FOR_SAMPLE_KEY] == str(c.sample_id)
        assert review.sample_id is None and "queue_source" not in review.verification_results
    outside = await ap.verify_and_classify(w.db, [w.refs[0]], intake_id=c.intake)       # the /verify path
    made_outside = await _review_of(w.db, outside["outcomes"][0]["review_id"])
    assert ap.CREATED_FOR_SAMPLE_KEY not in made_outside.verification_results


async def test_the_positive_case_links_each_member_to_its_own_review(world, monkeypatch):
    w = world
    c = await _crashed(w, monkeypatch)
    w.db.expire_all()                  # the stored classifier input is re-read from the database, not from memory
    result = await rc._create_review_cycle(w.db, c.intake, user=_PM, full_population=True)
    assert result["linked_this_call"] == 4 and result["linked_existing_submitted_values_review"] == 4
    assert sorted(result["linked_existing_submitted_values_review_ids"]) == c.existing
    assert result["remaining"] == 0 and result["complete"] is True and result["needs_person"] is False
    assert result["not_reviewable_for_identity"] == []
    assert await _review_ids(w.db, c.record_ids) == c.existing          # no new review
    assert w.sources.npi_calls == [] and w.sources.name_calls == []     # no source asked
    for review_id in c.existing:
        review = await _review_of(w.db, review_id)
        assert review.sample_id == c.sample_id                          # scoped to the sample only now


async def _break(case, w, c, monkeypatch):
    """Break exactly ONE link condition for the target member; returns the reason the cycle must give."""
    t = c.target
    results = dict(t.verification_results)
    assessment = dict(results["assessment_as_of"])
    if case == "assessment_date_changed":
        t.verification_results = {**results, "assessment_as_of": {**assessment, "assessment_date": "2026-11-30"}}
        return rc.WHY_ASSESSMENT_DATE.format(theirs="2026-11-30", ours=assessment["assessment_date"])
    if case == "assessment_run_changed":
        t.verification_results = {**results, "assessment_as_of": {**assessment, "run_id": str(uuid.uuid4())}}
        return rc.WHY_ASSESSMENT_RUN
    if case == "assessment_is_a_fallback":
        t.verification_results = {**results, "assessment_as_of": {**assessment,
                                                                  "source": ac.SOURCE_FALLBACK_NO_RUN}}
        return rc.WHY_ASSESSMENT_FALLBACK.format(source=ac.SOURCE_FALLBACK_NO_RUN)
    if case == "older_than_the_plan":
        t.created_at = c.drawn_at - timedelta(days=1)
        return rc.WHY_OLDER_THAN_PLAN
    if case == "made_by_the_verify_route":
        t.verification_results = {k: v for k, v in results.items() if k != ap.CREATED_FOR_SAMPLE_KEY}
        return rc.WHY_NOT_THIS_CYCLE.format(created="none recorded")
    if case == "made_by_another_plan":
        other = str(uuid.uuid4())
        t.verification_results = {**results, ap.CREATED_FOR_SAMPLE_KEY: other}
        return rc.WHY_NOT_THIS_CYCLE.format(created=other)
    if case == "two_reviews_on_the_record":
        await w.db.flush()
        second = await ap.verify_and_classify(w.db, [w.refs[0]], intake_id=c.intake, creating_sample_id=c.sample_id)
        w.sources.npi_calls.clear()
        w.sources.name_calls.clear()
        both = sorted([c.target_id, second["outcomes"][0]["review_id"]])
        return rc.WHY_SEVERAL_REVIEWS.format(n=2, reviews=", ".join(both))
    if case == "two_record_entity":
        original = await w.db.get(m.RceSourceRecord, c.record_ids[0])
        extra = m.RceSourceRecord(
            id=uuid.uuid4(), source_intake_id=c.intake, line_number=9_999, raw_line=original.raw_line,
            parsed=dict(original.parsed), record_sha256=uuid.uuid4().hex * 2, source_rce_id=None,
            tefcaid=original.tefcaid, hcid=original.hcid, npi=None, field_count=original.field_count,
            parse_status="ok", promotion_status="pending")
        w.db.add(extra)
        await w.db.flush()
        w.db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=c.intake, source_record_id=extra.id,
                                    record_status="CLEAN", rce_org_oid=None, transformation_version="test",
                                    canonical_entity_id=c.target_entity))
        return rc.WHY_SEVERAL_RECORDS.format(n=2)
    if case == "the_record_is_held":
        row = (await w.db.execute(select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_record_id == c.record_ids[0]))).scalars().one()
        row.record_status = "HELD"
        return rc.WHY_SEVERAL_RECORDS.format(n=0)
    if case == "assigned":
        t.assigned_to_user_id = uuid.uuid4()
        return rc.WHY_TOUCHED.format(what="assigned")
    if case == "resolved_by_a_reviewer":
        t.reviewer_resolution = "confirmed"
        return rc.WHY_TOUCHED.format(what="a reviewer resolution")
    if case == "has_a_decision_event":
        w.db.add(reg.ReviewDecisionEvent(
            id=uuid.uuid4(), review_id=c.target_id, sequence_number=1, event_type="ANALYST_DETERMINATION",
            determination="CONFIRM",
            actor_user_id=uuid.uuid4(), actor_email="analyst@synthetic-test.docuaction.invalid",
            actor_role="reviewer", rationale="synthetic determination rationale"))
        return rc.WHY_TOUCHED.format(what="1 decision event(s)")
    if case == "rules_changed":
        from app.tefca_registry.bucket_classifier import BucketClassifier
        from app.tefca_registry.rule_configuration import compose
        onc = [{**r, "version": 99} for r in compose({"treatment_b4_onc_scope"})]

        async def _now_in_force(self, _db):
            return onc
        monkeypatch.setattr(BucketClassifier, "load_rules", _now_in_force)
        # the target is the sole-purpose active Treatment record: B3 by the default when it was reviewed,
        # B4 by RULE-006 under the rules now in force
        return rc.WHY_RULES_CHANGED.format(now="B4 by RULE-006 v99", then="B3 by DEFAULT-UNMATCHED v0")
    raise AssertionError(case)


REFUSALS = ["assessment_date_changed", "assessment_run_changed", "assessment_is_a_fallback", "older_than_the_plan",
            "made_by_the_verify_route", "made_by_another_plan", "two_reviews_on_the_record", "two_record_entity",
            "the_record_is_held", "assigned", "resolved_by_a_reviewer", "has_a_decision_event", "rules_changed"]


@pytest.mark.parametrize("case", REFUSALS)
async def test_one_broken_condition_refuses_that_member_and_only_that_member(world, monkeypatch, case):
    w = world
    c = await _crashed(w, monkeypatch)
    why = await _break(case, w, c, monkeypatch)
    await w.db.flush()
    reviews_before = await _review_ids(w.db, c.record_ids)
    intake, target_entity, sample_id = c.intake, c.target_entity, c.sample_id

    result = await rc._create_review_cycle(w.db, intake, user=_PM, full_population=True)

    assert result["linked_this_call"] == 3 and result["linked_existing_submitted_values_review"] == 3, result[
        "not_reviewable_for_identity"]
    assert c.target_id not in result["linked_existing_submitted_values_review_ids"]
    assert result["remaining"] == 1 and result["complete"] is False
    (listed,) = result["not_reviewable_for_identity"]
    assert listed["entity_id"] == str(target_entity) and listed["kind"] == rc.KIND_ALREADY_REVIEWED
    assert listed["not_linked_because"] == why, listed["not_linked_because"]
    assert listed["existing_review"] in reviews_before                  # the review id, as a key of its own
    assert listed["reason"] == rc.NOT_REVIEWABLE_ALREADY_REVIEWED.format(review=listed["existing_review"], why=why)
    assert await _review_ids(w.db, c.record_ids) == reviews_before      # nothing new was created
    assert w.sources.npi_calls == [] and w.sources.name_calls == []     # and no source was asked
    member = (await w.db.execute(select(reg.SampleEntity).where(
        reg.SampleEntity.sample_id == sample_id, reg.SampleEntity.entity_id == target_entity))).scalars().one()
    assert member.review_id is None and member.review_status == "pending"
    audit = (await w.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_members_verified"))).scalars().all()
    rows = [n for a in audit for n in (a.metadata_ or {}).get("not_reviewable_members") or []
            if n.get("existing_review") == listed["existing_review"]]
    assert rows and rows[-1]["not_linked_because"] == why               # the audit row names the review and why

    # THE TERMINAL SIGNAL. This call linked three, so it is not terminal. The next links nothing, nothing is
    # transient, and the one member left needs a person: the driver can stop. The sample is NOT complete.
    assert result["needs_person"] is False
    again = await rc._create_review_cycle(w.db, intake, user=_PM, full_population=True)
    assert again["linked_this_call"] == 0 and again["remaining"] == 1 and again["complete"] is False
    assert again["needs_person"] is True and again["needs_person_count"] == 1
    assert again["no_further_reviews_possible"] is False                # that one stays for identity reasons only
    assert "needs_person is" in again["note"]


async def test_a_delivery_assessed_before_the_effective_date_is_unaffected(world, monkeypatch):
    """October: no submitted-values review can exist, nothing is read for one, nothing is linked; the members the
    NPI gate refuses are reported for identity as before - and the driver is told a person is needed."""
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    run = (await w.db.execute(select(m.RceIngestionRun).where(
        m.RceIngestionRun.source_intake_id == w.intake_b))).scalars().first()
    run.assessment_metadata = {"assessment_date": "2026-11-08", "source": "pinned_param"}
    await w.db.flush()
    result = await rc._create_review_cycle(w.db, w.intake_b, user=_PM, full_population=True)
    assert result["linked_this_call"] == 0 and result["linked_existing_submitted_values_review"] == 0
    assert result["not_reviewable_identity_count"] == 4 and result["not_reviewable_already_reviewed_count"] == 0
    assert {n["reason"] for n in result["not_reviewable_for_identity"]} == {ap.REFUSAL_SUBMITTED_NPI_DIFFERS}
    assert result["no_further_reviews_possible"] is True and result["needs_person"] is True
    assert result["complete"] is False


async def test_a_member_with_no_existing_review_is_reviewed_exactly_as_before(world, monkeypatch):
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    result = await rc._create_review_cycle(w.db, w.intake_a, user=_PM, full_population=True)
    assert result["linked_this_call"] == 4 and result["linked_existing_submitted_values_review"] == 0
    assert result["complete"] is True and result["needs_person"] is False
    assert result["verification"]["requested"] == 4 and result["not_reviewable_for_identity"] == []
    members = (await w.db.execute(select(reg.SampleEntity).where(
        reg.SampleEntity.sample_id == uuid.UUID(result["plan"]["sample_id"])))).scalars().all()
    for member in members:
        review = await _review_of(w.db, member.review_id)
        assert review.sample_id == member.sample_id                     # linked the ordinary way
        assert review.verification_results[ap.CREATED_FOR_SAMPLE_KEY] == str(member.sample_id)


async def test_the_delivery_lock_does_not_depend_on_how_the_id_is_spelled(world, monkeypatch):
    """The per-delivery advisory lock was keyed on the caller's raw string, so the same delivery written in upper
    case took a different lock. It is keyed on the parsed id now: every spelling takes the SAME lock."""
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    keys = []
    sync_conn = (await w.db.connection()).sync_connection

    def _capture(_conn, _cursor, statement, parameters, *_rest):
        if "pg_advisory_lock" in statement:
            keys.append(tuple(parameters) if not isinstance(parameters, dict) else tuple(parameters.values()))
    event.listen(sync_conn, "before_cursor_execute", _capture)
    try:
        intake = w.intake_b
        for spelling in (intake, str(intake), str(intake).upper(), "{" + str(intake).upper() + "}"):
            await rc._create_review_cycle(w.db, spelling, user=_PM, full_population=True)
    finally:
        event.remove(sync_conn, "before_cursor_execute", _capture)
    assert len(keys) == 4 and len(set(keys)) == 1, keys
    assert keys[0] == (f"review_cycle:{intake}",)
    with pytest.raises(rc.CycleRefused, match="No delivery"):
        await rc._create_review_cycle(w.db, "not-a-delivery-id", user=_PM)
