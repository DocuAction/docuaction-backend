"""The operational per-QHIN sampling path, end to end.

    delivery -> eligible population -> canonical QHIN resolver
      -> draw_per_stratum -> frozen ReviewSample + SampleEntity
      -> existing review workflow -> analyst -> independent QA

WHAT THIS CLOSES
────────────────
Step #13 proved the statistics and left one blocker: nothing wired
`draw_per_stratum` to an operational plan. `POST /samples` still stratified by
`entity_level`/`state` and still called the proportional `draw_sample`, which on
the delivered population gives the 3-record QHIN ZERO selected records while the
total reads as a 95% sample.

`app/tefca_registry/qhin_sampling.py` is that wiring. It contains no formula —
the statistics stay in `sampling_engine`, and this module decides eligibility,
resolves the QHIN from the canonical `managed_by_qhin` edge, and freezes the
result into the tables that already existed.

Fixtures are synthetic: OIDs under an unassigned `9.99.222` arc, prefixed names,
`@synthetic.test` identities. Every test runs inside a rolled-back transaction
except the concurrency test, which needs two committing sessions and uses a
throwaway schema in a separate database.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import Base, _normalize_url
from app.tefca_registry import models as reg
from app.tefca_registry import qhin_sampling as qs
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint
from app.tefca_registry.sampling_engine import CochranSampler

SYN = "SYNTHETIC-SAMP"


def principal(email, role):
    return SimpleNamespace(id=uuid.uuid4(), email=email, role=role)


ANALYST = principal("analyst@synthetic.test", "reviewer")
QA = principal("qa@synthetic.test", "qalead")


# ── synthetic delivery with QHIN strata ──────────────────────────────────────

async def _build(db, spec, *, held=(), label=None):
    """One synthetic delivery: {qhin_tag: N} -> intake with promoted entities."""
    intake_id = uuid.uuid4()
    blob = b"synthetic"
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=label or f"{SYN}-D",
        original_filename="synthetic.csv", storage_path="(synthetic)",
        sha256=hashlib.sha256(blob + intake_id.bytes).hexdigest(),
        file_size_bytes=len(blob), delimiter="|", encoding="utf-8",
        line_terminator="CRLF", headers=list(RCE_FIELDS),
        schema_fingerprint=schema_fingerprint(list(RCE_FIELDS)),
        record_count=sum(spec.values()), received_at=datetime.utcnow(),
        received_by=SYN, status="PARSED",
        source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()

    qhins = {}
    for tag in spec:
        qhin_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(
            id=qhin_id, name=f"{SYN} {tag}", display_name=f"{SYN} {tag}",
            entity_level="qhin", entity_type="health_information_network",
            operational_status="active", verification_status="not_verified",
            current_version=1, is_active=True))
        qhins[tag] = qhin_id
    await db.flush()

    line = 1
    pending = []          # written after the entities they point at exist
    for tag, count in spec.items():
        for i in range(count):
            line += 1
            oid = f"9.99.222.{tag}.{i}"
            source_id, entity_id = uuid.uuid4(), uuid.uuid4()
            db.add(m.RceSourceRecord(
                id=source_id, source_intake_id=intake_id, line_number=line,
                raw_line=oid, parsed={"id": oid},
                record_sha256=hashlib.sha256(oid.encode()).hexdigest(),
                source_rce_id=oid, field_count=len(RCE_FIELDS),
                parse_status="ok", promotion_status="promoted"))
            db.add(reg.TefcaRegEntity(
                id=entity_id, name=f"{SYN} {tag} ORG {i}",
                display_name=f"{SYN} {tag} ORG {i}", entity_level="participant",
                entity_type="provider", operational_status="active",
                verification_status="not_verified", current_version=1,
                is_active=True, rce_org_oid=oid, source_record_id=source_id))
            pending.append((tag, i, oid, source_id, entity_id))
    await db.flush()

    for tag, i, oid, source_id, entity_id in pending:
        db.add(reg.TefcaEntityRelationship(
            id=uuid.uuid4(), parent_entity_id=qhins[tag],
            child_entity_id=entity_id,
            relationship_type="managed_by_qhin", status="active",
            source="import", effective_date=date(2026, 1, 1)))
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id,
            source_record_id=source_id,
            record_status="HELD" if oid in held else "CLEAN",
            issue_count=0, correction_count=0, rce_org_oid=oid,
            name=f"{SYN} {tag} ORG {i}",
            transformation_version="test-1.0.0",
            canonical_entity_id=entity_id))
    await db.commit()
    return intake_id, qhins


@pytest.fixture
async def rolled_back_db(db_required):
    engine = create_async_engine(
        _normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    connection = await engine.connect()
    outer = await connection.begin()
    session = AsyncSession(bind=connection,
                           join_transaction_mode="create_savepoint",
                           expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()
        await connection.close()
        await engine.dispose()


SPEC = {"A": 60, "B": 400, "C": 3000, "D": 3}
LIGHT = {"A": 30, "B": 40, "C": 25, "D": 3}


def _expected_n(N):
    """Cochran with FPC, written out here so the assertion does not simply ask
    the code under test whether it agrees with itself."""
    z, p, e = 1.96, 0.5, 0.05
    n0 = (z ** 2) * p * (1 - p) / (e ** 2)
    n = n0 / (1 + (n0 - 1) / N)
    return max(1, min(N, math.ceil(n)))


def test_the_expected_sizes_match_the_engine_and_are_not_hardcoded():
    """Both routes to the number agree, so neither is quietly wrong alone."""
    sampler = CochranSampler()
    for N in (3, 44, 60, 88, 400, 3000, 10481):
        assert _expected_n(N) == sampler.calculate_sample_size(N), N
    # The values this gate reasons about, stated once so a silent drift shows.
    assert (_expected_n(60), _expected_n(400), _expected_n(3000),
            _expected_n(3)) == (53, 197, 341, 3)


# ── STEP 2 — the QHIN resolver ───────────────────────────────────────────────

async def test_qhin_comes_from_the_canonical_relationship(rolled_back_db):
    db = rolled_back_db
    intake_id, qhins = await _build(db, {"A": 5, "B": 4})

    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert len(eligible) == 9 and unresolved == []
    assert {u["qhin"] for u in eligible} == {str(qhins["A"]), str(qhins["B"])}


async def test_an_unresolvable_qhin_is_reported_not_reassigned(rolled_back_db):
    """No edge, or two edges, is an ambiguity — never a plausible guess."""
    db = rolled_back_db
    intake_id, qhins = await _build(db, {"A": 4, "B": 1})

    # Remove one record's edge, and give another a second managing QHIN.
    edges = (await db.execute(
        select(reg.TefcaEntityRelationship)
        .where(reg.TefcaEntityRelationship.parent_entity_id == qhins["A"])
    )).scalars().all()
    await db.delete(edges[0])
    db.add(reg.TefcaEntityRelationship(
        id=uuid.uuid4(), parent_entity_id=qhins["B"],
        child_entity_id=edges[1].child_entity_id,
        relationship_type="managed_by_qhin", status="active", source="import",
        effective_date=date(2026, 1, 1)))
    await db.commit()

    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert len(eligible) == 3 and len(unresolved) == 2
    reasons = sorted(u["reason"] for u in unresolved)
    assert "2 managing QHINs" in reasons[0]
    assert "no canonical managed_by_qhin edge" in reasons[1]
    assert all(u["qhin"] == qs.UNRESOLVED_QHIN for u in unresolved), (
        "an ambiguous record must not be filed under a plausible QHIN")


async def test_held_is_excluded_by_default_and_the_choice_is_explicit(
        rolled_back_db):
    """HELD eligibility is an open ONC question; a silent default would answer it."""
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 5}, held={"9.99.222.A.0"})

    default, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert len(default) == 4
    assert any("HELD" in u["reason"] for u in unresolved)

    included, _ = await qs.resolve_qhin_strata(db, intake_id, include_held=True)
    assert len(included) == 5


# ── STEPS 5/6/13/14/15 — per-QHIN sizing through the operational path ────────

async def test_every_qhin_is_sized_against_its_own_population(rolled_back_db):
    db = rolled_back_db
    intake_id, qhins = await _build(db, SPEC)

    preview = await qs.preview_plan(db, intake_id)
    assert preview["qhin_strata"] == 4
    assert preview["eligible_population"] == sum(SPEC.values())
    by_key = {str(qhins[tag]): tag for tag in SPEC}
    for key, info in preview["per_qhin"].items():
        tag = by_key[key]
        assert info["population_size"] == SPEC[tag]
        assert info["sample_size"] == _expected_n(SPEC[tag])
    assert preview["total_sample_size"] == sum(
        _expected_n(N) for N in SPEC.values())


async def test_finalized_membership_matches_the_per_qhin_sizes_exactly(
        rolled_back_db):
    db = rolled_back_db
    intake_id, qhins = await _build(db, SPEC)

    plan = await qs.finalize_plan(db, intake_id, actor=ANALYST.email,
                                  actor_id=ANALYST.id, seed=4242)
    await db.commit()

    by_key = {str(qhins[tag]): tag for tag in SPEC}
    for key, selected in plan["per_qhin_selected"].items():
        assert selected == _expected_n(SPEC[by_key[key]])
    assert plan["sample_size"] == sum(_expected_n(N) for N in SPEC.values())
    assert plan["membership_count"] == plan["sample_size"]
    assert plan["stratify_by"] == "managed_by_qhin"
    assert plan["selection_algorithm"] == qs.SELECTION_ALGORITHM


async def test_a_three_record_qhin_is_a_census_and_never_zero(rolled_back_db):
    """The exact defect Step #13 found, pinned so it cannot come back."""
    db = rolled_back_db
    intake_id, qhins = await _build(db, SPEC)

    plan = await qs.finalize_plan(db, intake_id, seed=7)
    await db.commit()

    d_key = str(qhins["D"])
    assert plan["per_qhin_selected"][d_key] == 3, (
        "a 3-record QHIN must be a census, never 0 — proportional allocation "
        "gave it zero, which is what this gate exists to prevent")
    assert plan["per_qhin_sizing"][d_key]["census"] is True


async def test_no_cross_qhin_leakage_and_no_duplicate_membership(rolled_back_db):
    db = rolled_back_db
    intake_id, qhins = await _build(db, LIGHT)
    plan = await qs.finalize_plan(db, intake_id, seed=11)
    await db.commit()

    members = (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
    )).scalars().all()
    entity_ids = [mem.entity_id for mem in members]
    assert len(entity_ids) == len(set(entity_ids)), "a record was enrolled twice"

    # Every member's stratum matches the QHIN its canonical edge names.
    edges = dict((c, p) for c, p in (await db.execute(
        select(reg.TefcaEntityRelationship.child_entity_id,
               reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id.in_(entity_ids),
               reg.TefcaEntityRelationship.relationship_type
               == "managed_by_qhin"))).all())
    for member in members:
        assert member.stratum == str(edges[member.entity_id])


# ── STEP 7 — the legacy proportional path must not serve this ────────────────

def test_the_official_path_never_uses_the_proportional_draw():
    import inspect

    src = inspect.getsource(qs)
    assert "draw_per_stratum" in src
    assert "draw_sample(" not in src, (
        "the official per-QHIN path must not call the national/proportional "
        "draw — it under-samples small QHINs")
    # And no formula was copied into the orchestration layer.
    for formula in ("math.ceil", "1.96", "z_for", "** 2"):
        assert formula not in src, (
            f"{formula!r} suggests the statistics were reimplemented here "
            f"instead of being called from sampling_engine")


# ── STEPS 10/16 — idempotent finalisation, no redraw ─────────────────────────

async def test_finalizing_twice_returns_the_same_official_sample(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 40, "B": 60})

    first = await qs.finalize_plan(db, intake_id, seed=99)
    await db.commit()
    second = await qs.finalize_plan(db, intake_id, seed=12345)   # different seed
    await db.commit()

    assert second["already_finalized"] is True
    assert second["sample_id"] == first["sample_id"]
    assert second["random_seed"] == first["random_seed"], (
        "a second finalise must not redraw with a new seed")
    assert second["per_qhin_selected"] == first["per_qhin_selected"]

    plans = int((await db.execute(
        select(func.count()).select_from(reg.ReviewSample))).scalar() or 0)
    assert plans == 1


async def test_reading_a_plan_never_redraws_it(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 50})
    plan = await qs.finalize_plan(db, intake_id, seed=2026)
    await db.commit()

    first = await qs.get_plan(db, uuid.UUID(plan["sample_id"]))
    second = await qs.get_plan(db, uuid.UUID(plan["sample_id"]))
    assert first == second
    assert first["random_seed"] == 2026

    members = int((await db.execute(
        select(func.count()).select_from(reg.SampleEntity))).scalar() or 0)
    assert members == plan["membership_count"]


async def test_different_parameters_are_a_different_plan(rolled_back_db):
    """Changing what the sample MEANS is a new plan, not a redraw of the old."""
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 50})

    a = await qs.finalize_plan(db, intake_id, margin=0.05, seed=1)
    await db.commit()
    b = await qs.finalize_plan(db, intake_id, margin=0.10, seed=1)
    await db.commit()

    assert a["sample_id"] != b["sample_id"]
    assert b["already_finalized"] is False
    assert b["sample_size"] < a["sample_size"], "a wider margin needs fewer"


# ── STEP 12 — server-controlled selection ────────────────────────────────────

def test_a_caller_cannot_supply_selected_ids_or_qhin_assignment():
    """Cherry-picking is impossible because there is no parameter for it."""
    import inspect

    params = set(inspect.signature(qs.finalize_plan).parameters)
    for forbidden in ("entity_ids", "selected", "selected_ids", "members",
                      "qhin_assignment", "strata_override", "population"):
        assert forbidden not in params, (
            f"{forbidden!r} would let a caller choose the sample")
    # Only methodology parameters and identity are accepted.
    assert params == {"db", "intake_id", "review_type", "confidence", "margin",
                      "proportion", "use_fpc", "include_held", "seed",
                      "actor", "actor_id", "sample_name"}


async def test_a_post_finalization_seed_cannot_change_the_membership(
        rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 40})
    plan = await qs.finalize_plan(db, intake_id, seed=555)
    await db.commit()
    before = sorted(str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity))).scalars().all())

    await qs.finalize_plan(db, intake_id, seed=999)
    await db.commit()
    after = sorted(str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity))).scalars().all())
    assert before == after


# ── STEPS 18-24 — the review workflow, now reachable ─────────────────────────

async def _case_for_member(db, member, intake_id):
    """Create the review case a sampled record needs, via the existing model."""
    review_id = f"REV-8000-{uuid.uuid4().int % 1000000:06d}"
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id=review_id, entity_id=member.entity_id,
        source_record_id=None,
        verification_results={"queue_source": "TEFCA_ARC_PER_QHIN",
                              "selection_reason": "STATISTICAL_SAMPLE",
                              "sample_id": str(member.sample_id),
                              "qhin": member.stratum,
                              "source_intake_id": str(intake_id),
                              "priority": 50}))
    member.review_id = review_id
    await db.flush()
    return review_id


async def test_a_sampled_record_flows_through_the_existing_review_workflow(
        rolled_back_db):
    """STEP 18/20/21/22: sample -> case -> claim -> determine -> independent QA."""
    from app.tefca_registry import case_assignment as assignment
    from app.tefca_registry.qa_gate import (_events, is_reportable,
                                            record_analyst_determination,
                                            submit_qa_review)

    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 8})
    plan = await qs.finalize_plan(db, intake_id, seed=31)
    await db.commit()

    member = (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
        .limit(1))).scalars().first()
    review_id = await _case_for_member(db, member, intake_id)
    await db.commit()

    # The case carries WHY it exists — a statistical selection, not a DQ finding.
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    payload = record.verification_results
    assert payload["selection_reason"] == "STATISTICAL_SAMPLE"
    assert payload["sample_id"] == plan["sample_id"]
    assert payload["qhin"] == member.stratum

    await assignment.claim(db, review_id, user=ANALYST)
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic determination for a sampled record.")
    await db.commit()
    assert is_reportable(await _events(db, review_id)) is False

    await submit_qa_review(db, review_id, user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic QA approval of a sampled record.")
    await db.commit()
    assert is_reportable(await _events(db, review_id)) is True


async def test_an_analyst_still_cannot_qa_their_own_sampled_case(rolled_back_db):
    from app.tefca_registry.qa_gate import (QaGateRefused,
                                            record_analyst_determination,
                                            submit_qa_review)

    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 8})
    plan = await qs.finalize_plan(db, intake_id, seed=32)
    await db.commit()
    member = (await db.execute(select(reg.SampleEntity).limit(1))).scalars().first()
    review_id = await _case_for_member(db, member, intake_id)
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic determination on a sampled record.")
    await db.commit()

    with pytest.raises(QaGateRefused, match="segregation of duties"):
        await submit_qa_review(db, review_id, user=ANALYST, qa_action="APPROVE",
                               qa_reason="Synthetic self-approval attempt.")
    await db.rollback()


async def test_returned_and_escalated_members_stay_in_the_sample(rolled_back_db):
    """STEP 24: a member is never swapped for an easier record."""
    from app.tefca_registry.qa_gate import (record_analyst_determination,
                                            submit_qa_review)

    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 10})
    plan = await qs.finalize_plan(db, intake_id, seed=33)
    await db.commit()
    members = (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
    )).scalars().all()
    before = sorted(str(mem.entity_id) for mem in members)

    returned = await _case_for_member(db, members[0], intake_id)
    escalated = await _case_for_member(db, members[1], intake_id)
    await db.commit()

    for review_id, action, extra in (
            (returned, "RETURN", {}),
            (escalated, "ESCALATE", {"escalated_to_user_id": QA.id,
                                     "escalation_reason": "Synthetic escalation."})):
        await record_analyst_determination(
            db, review_id, user=ANALYST, determination="CONFIRM",
            rationale="Synthetic determination before QA action.")
        await submit_qa_review(db, review_id, user=QA, qa_action=action,
                               qa_reason=f"Synthetic QA {action}.", **extra)
    await db.commit()

    after = sorted(str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
    )).scalars().all())
    assert after == before, "a returned or escalated member was replaced"


async def test_selection_alone_is_not_completion(rolled_back_db):
    """STEP 23: completion is QA-approved review, read from the review events."""
    from app.tefca_registry.qa_gate import (record_analyst_determination,
                                            submit_qa_review)

    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 8})
    plan = await qs.finalize_plan(db, intake_id, seed=34)
    await db.commit()

    completion = await qs.plan_completion(db, uuid.UUID(plan["sample_id"]))
    assert completion["complete"] is False
    assert completion["counts"]["no_review_case"] == completion["counts"]["selected"]

    member = (await db.execute(select(reg.SampleEntity).limit(1))).scalars().first()
    review_id = await _case_for_member(db, member, intake_id)
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic determination for completion accounting.")
    await db.commit()
    mid = await qs.plan_completion(db, uuid.UUID(plan["sample_id"]))
    assert mid["counts"]["submitted_for_qa"] == 1
    assert mid["complete"] is False

    await submit_qa_review(db, review_id, user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic QA approval for accounting.")
    await db.commit()
    end = await qs.plan_completion(db, uuid.UUID(plan["sample_id"]))
    assert end["counts"]["qa_approved"] == 1
    assert end["complete"] is False, "one approved member is not a complete plan"


# ── STEP 25/26 — history ─────────────────────────────────────────────────────

async def test_a_new_delivery_does_not_touch_the_previous_plan(rolled_back_db):
    db = rolled_back_db
    first_intake, _ = await _build(db, {"A": 30}, label=f"{SYN}-N")
    plan_n = await qs.finalize_plan(db, first_intake, seed=41)
    await db.commit()
    before = sorted(str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan_n["sample_id"]))
    )).scalars().all())

    second_intake, _ = await _build(db, {"A": 30}, label=f"{SYN}-N1")
    plan_n1 = await qs.finalize_plan(db, second_intake, seed=42)
    await db.commit()

    assert plan_n1["sample_id"] != plan_n["sample_id"]
    after = sorted(str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan_n["sample_id"]))
    )).scalars().all())
    assert after == before, "the new delivery mutated the previous sample"
    # Membership is not carried forward.
    n1_members = {str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan_n1["sample_id"]))
    )).scalars().all()}
    assert not (n1_members & set(before))


# ── STEP 28 — audit ──────────────────────────────────────────────────────────

async def test_a_finalized_plan_is_fully_reconstructable(rolled_back_db):
    db = rolled_back_db
    intake_id, qhins = await _build(db, LIGHT)
    plan = await qs.finalize_plan(db, intake_id, actor=ANALYST.email,
                                  actor_id=ANALYST.id, seed=8080)
    await db.commit()

    for key in ("sample_id", "plan_key", "plan_source", "source_intake_id",
                "population_version", "selection_algorithm", "stratify_by",
                "population_size", "sample_size", "confidence_level",
                "margin_of_error", "proportion", "use_fpc", "random_seed",
                "per_qhin_sizing", "per_qhin_selected", "drawn_at",
                "created_by", "include_held"):
        assert plan[key] is not None, f"{key} missing from the plan record"

    assert plan["confidence_level"] == 0.95
    assert plan["random_seed"] == 8080
    for key, sizing in plan["per_qhin_sizing"].items():
        assert sizing["population_size"] and "sample_size" in sizing

    audit = (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action == "sampling_plan_finalized"))).scalars().all()
    mine = [a for a in audit
            if (a.metadata_ or {}).get("sample_id") == plan["sample_id"]]
    assert len(mine) == 1
    payload = mine[0].metadata_
    assert payload["selection_algorithm"] == qs.SELECTION_ALGORITHM
    assert payload["qhin_strata"] == 4
    assert payload["random_seed"] == 8080


# ── STEP 11/17 — concurrent finalisation, two committing sessions ────────────

SCHEMA = "samp_gate_tmp"


@pytest.fixture
async def sandbox_engine(db_required):
    import urllib.parse as up

    parsed = up.urlparse(_normalize_url(os.environ["DATABASE_URL"]))
    url = up.urlunparse(parsed._replace(path="/docuaction"))
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:                                  # noqa: BLE001
        await admin.dispose()
        pytest.skip(f"no sandbox database for a concurrency test: {exc}")

    engine = create_async_engine(
        url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": SCHEMA}})
    tables = [Base.metadata.tables[t] for t in
              ("rce_source_intakes", "rce_source_records", "rce_curated_records",
               "tefca_reg_entities", "tefca_entity_relationships",
               "review_samples", "sample_entities", "review_records",
               "review_decision_events", "tefca_reg_audit_log")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda s: Base.metadata.create_all(
            s, tables=tables, checkfirst=True))
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await admin.dispose()


async def test_two_concurrent_finalizations_produce_one_official_sample(
        sandbox_engine):
    """STEP 11/17: the race that would otherwise create two official samples."""
    async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
        intake_id, _ = await _build(db, {"A": 40, "B": 60})

    async def finalize(seed):
        async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
            try:
                result = await qs.finalize_plan(db, intake_id, seed=seed)
                await db.commit()
                return "OK", result["sample_id"], result["already_finalized"]
            except Exception as exc:                          # noqa: BLE001
                await db.rollback()
                return f"RAISED {type(exc).__name__}", str(exc)[:120], None

    results = await asyncio.gather(finalize(111), finalize(222))
    assert all(r[0] == "OK" for r in results), results
    assert results[0][1] == results[1][1], "two different official samples"
    assert sorted(r[2] for r in results) == [False, True], (
        "exactly one finaliser must have drawn; the other must find it")

    async with AsyncSession(sandbox_engine) as db:
        plans = int((await db.execute(
            select(func.count()).select_from(reg.ReviewSample))).scalar() or 0)
        members = (await db.execute(select(reg.SampleEntity))).scalars().all()
    assert plans == 1
    ids = [(mem.sample_id, mem.entity_id) for mem in members]
    assert len(ids) == len(set(ids)), "duplicate SampleEntity rows"


# ── remaining closure requirements ───────────────────────────────────────────

async def test_a_preview_sizes_without_selecting_or_persisting(rolled_back_db):
    """A preview that drew records could be run repeatedly to shop for a sample."""
    db = rolled_back_db
    intake_id, _ = await _build(db, LIGHT)

    preview = await qs.preview_plan(db, intake_id)
    await db.commit()

    assert "selected" not in preview and "entity_ids" not in preview
    assert int((await db.execute(
        select(func.count()).select_from(reg.ReviewSample))).scalar() or 0) == 0
    assert int((await db.execute(
        select(func.count()).select_from(reg.SampleEntity))).scalar() or 0) == 0
    assert preview["total_sample_size"] > 0


async def test_an_empty_eligible_population_is_refused_not_papered_over(
        rolled_back_db):
    """An empty plan would read as a completed 95% sample of nothing."""
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 2},
                                held={"9.99.222.A.0", "9.99.222.A.1"})

    with pytest.raises(qs.SamplingRefused, match="no eligible population"):
        await qs.finalize_plan(db, intake_id)
    await db.rollback()


async def test_being_human_required_does_not_discharge_being_sampled(
        rolled_back_db):
    """Exception review and statistical review answer different questions."""
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 12})

    # One record also carries a DQ finding, so it is HUMAN_REQUIRED as well.
    curated = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .order_by(m.RceCuratedRecord.rce_org_oid).limit(1))).scalars().first()
    curated.issue_count = 1
    await db.commit()

    plan = await qs.finalize_plan(db, intake_id, seed=61)
    await db.commit()

    assert plan["population_size"] == 12, (
        "a HUMAN_REQUIRED record is still part of the sampling frame")
    # And it remains eligible for selection rather than being pre-excluded.
    eligible, _ = await qs.resolve_qhin_strata(db, intake_id)
    assert curated.canonical_entity_id in {u["entity_id"] for u in eligible}


async def test_a_member_absent_from_the_next_delivery_keeps_its_history(
        rolled_back_db):
    """NOT_PRESENT_IN_CURRENT_DELIVERY is an observation about a file.

    It is not deletion, and it must not quietly shrink a sample already drawn
    against the delivery the record WAS in.
    """
    db = rolled_back_db
    first_intake, _ = await _build(db, {"A": 20}, label=f"{SYN}-N")
    plan = await qs.finalize_plan(db, first_intake, seed=71)
    await db.commit()
    frozen = sorted(str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
    )).scalars().all())

    # A later delivery containing none of those organisations.
    await _build(db, {"A": 5}, label=f"{SYN}-N1")

    after = await qs.get_plan(db, uuid.UUID(plan["sample_id"]))
    still = sorted(str(mem.entity_id) for mem in (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
    )).scalars().all())
    assert still == frozen
    assert after["sample_size"] == plan["sample_size"]


async def test_an_unpromoted_record_is_reported_not_silently_dropped(
        rolled_back_db):
    """The shape every real HELD record actually has.

    On the delivered population all four HELD records are also UNPROMOTED, so
    they have no canonical entity — and `sample_entities.entity_id` is a NOT
    NULL foreign key. They therefore cannot be sampling units no matter what
    `include_held` says. That limit has to be VISIBLE in the count rather than
    filtered out of the query before anyone looks.
    """
    db = rolled_back_db
    intake_id, _ = await _build(db, {"A": 6})
    orphan = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .order_by(m.RceCuratedRecord.rce_org_oid).limit(1))).scalars().first()
    orphan.record_status = "HELD"
    orphan.canonical_entity_id = None
    await db.commit()

    eligible, unresolved = await qs.resolve_qhin_strata(db, intake_id)
    assert len(eligible) == 5
    assert [u["reason"] for u in unresolved] == [
        "not promoted; no canonical entity, so it cannot be a sampling unit"]

    # And asking for HELD does not conjure a sampling unit that cannot exist.
    with_held, still_unresolved = await qs.resolve_qhin_strata(
        db, intake_id, include_held=True)
    assert len(with_held) == 5 and len(still_unresolved) == 1

    preview = await qs.preview_plan(db, intake_id)
    assert preview["unresolved_units"] == 1, (
        "the count a reviewer reads must include what could not be sampled")


def test_the_proportional_draw_is_unchanged_and_still_available():
    """STEP 30: fixing per-QHIN sampling must not break the national question."""
    sampler = CochranSampler()
    population = [{"q": "BIG"}] * 900 + [{"q": "SMALL"}] * 100
    national = sampler.draw_sample(population, strata=lambda u: u["q"], seed=5)

    assert sum(national.strata_distribution.values()) == national.sample_size
    assert national.sample_size == sampler.calculate_sample_size(1000), (
        "draw_sample must still size ONCE against the whole population")
    assert national.strata_distribution["BIG"] > national.strata_distribution["SMALL"]
    assert national.stratum_sizing == {}, (
        "per-stratum sizing belongs to draw_per_stratum alone")
    assert (national.strata_config or {}).get("allocation") is None


def test_the_two_draws_are_never_confused_in_the_record():
    """A stored plan says which question it answered."""
    sampler = CochranSampler()
    population = [{"q": "A"}] * 10000 + [{"q": "B"}] * 3
    per_qhin = sampler.draw_per_stratum(population, stratum_of=lambda u: u["q"],
                                        seed=5)
    national = sampler.draw_sample(population, strata=lambda u: u["q"], seed=5)

    assert per_qhin.strata_config["allocation"] == "per_stratum_independent"
    assert per_qhin.strata_distribution["B"] == 3
    assert national.strata_distribution["B"] < per_qhin.strata_distribution["B"], (
        "this is the understatement the per-QHIN path exists to fix")


def test_fixtures_are_synthetic_only():
    for actor in (ANALYST, QA):
        assert actor.email.endswith("@synthetic.test")
