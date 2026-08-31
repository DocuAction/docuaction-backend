"""The supervisor control plane, over every ARC work source at once.

    DQ exceptions ┐
    sampling      ├─> review_records ─> assignment ─> analyst ─> QA ─> reportable
    priority      ┘                          │
                                             └─> ONE OPERATIONAL VIEW

WHAT THIS GATE PROVES
─────────────────────
That a supervisor can see and move work WITHOUT acquiring review authority,
and that the view never invents what the contract does not establish:

  * a deadline exists only where the COR supplied one;
  * `PAST_DUE` is arithmetic and concludes nothing about compliance;
  * a sampling plan that was never drawn reports NOT_YET_CREATED, never
    "0% complete";
  * a HUMAN_REQUIRED finding is not an analyst case;
  * workload counts are counts, not a performance score.

It also pins the two assignment defects this gate found: `assign` was a
read-modify-write, so two supervisors could both "win" and one assignment was
silently lost; and it took a case off a live holder with no stated reason, so a
handover looked identical in the trail to a case nobody had claimed.

GOVERNMENT DATA
    Every test runs inside an OUTER transaction that is rolled back, except the
    concurrency tests, which need committing sessions and use a throwaway
    schema in a separate database. Fixtures are synthetic: OIDs under an
    unassigned `9.99.888` arc, prefixed names, `@synthetic.test` identities. No
    Government case is created, assigned, decided or reported.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import Base, _normalize_url
from app.tefca_registry import case_assignment as assignment
from app.tefca_registry import models as reg
from app.tefca_registry import priority_review as pr
from app.tefca_registry import supervisor_ops as so
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint

SYN = "SYNTHETIC-OPS"
ARC = "9.99.888"
COR = "cor.officer@synthetic.test"

#: Both codes mean "correctly gated" — 401 for a missing credential, 403 for a
#: role refusal. Mirrors `tests/conftest.py`.
GATED = (401, 403)


def principal(email, role):
    return SimpleNamespace(id=uuid.uuid4(), email=email, role=role)


ANALYST_A = principal("analyst.a@synthetic.test", "reviewer")
ANALYST_B = principal("analyst.b@synthetic.test", "reviewer")
QA = principal("qa@synthetic.test", "qalead")
SUPERVISOR = principal("supervisor@synthetic.test", "senior_analyst")
SUPERVISOR_2 = principal("supervisor2@synthetic.test", "senior_analyst")


def _code_of(module) -> str:
    """A module's source with its own prose removed.

    The docstring NAMES the things the module refuses to do, so scanning the
    raw file would match the prohibition itself. The rule is about code.
    """
    import ast

    tree = ast.parse(inspect.getsource(module))
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)):
        tree.body = tree.body[1:]
    return ast.unparse(tree)


# ── synthetic population ─────────────────────────────────────────────────────

async def _intake(db, label=f"{SYN}-DELIVERY"):
    intake_id = uuid.uuid4()
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=label, original_filename="synthetic.csv",
        storage_path="(synthetic)",
        sha256=hashlib.sha256(intake_id.bytes).hexdigest(),
        file_size_bytes=9, delimiter="|", encoding="utf-8",
        line_terminator="CRLF", headers=list(RCE_FIELDS),
        schema_fingerprint=schema_fingerprint(list(RCE_FIELDS)),
        record_count=0, received_at=datetime.utcnow(), received_by=SYN,
        status="PARSED", source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()
    return intake_id


async def _qhin(db, tag="QHIN-1"):
    qhin_id = uuid.uuid4()
    db.add(reg.TefcaRegEntity(
        id=qhin_id, name=f"{SYN} {tag}", display_name=f"{SYN} {tag}",
        entity_level="qhin", entity_type="health_information_network",
        operational_status="active", verification_status="not_verified",
        current_version=1, is_active=True))
    await db.flush()
    return qhin_id


async def _org(db, intake_id, tag, *, line=2, qhin_id=None, promote=True):
    oid = f"{ARC}.{tag}"
    source_id = uuid.uuid4()
    db.add(m.RceSourceRecord(
        id=source_id, source_intake_id=intake_id, line_number=line,
        raw_line=oid, parsed={"id": oid},
        record_sha256=hashlib.sha256(f"{oid}{line}".encode()).hexdigest(),
        source_rce_id=oid, field_count=len(RCE_FIELDS), parse_status="ok",
        promotion_status="promoted" if promote else "held"))
    await db.flush()

    entity_id = None
    if promote:
        entity_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(
            id=entity_id, name=f"{SYN} ORG {tag}", display_name=f"{SYN} ORG {tag}",
            entity_level="participant", entity_type="provider",
            operational_status="active", verification_status="not_verified",
            current_version=1, is_active=True, rce_org_oid=oid,
            source_record_id=source_id))
        await db.flush()
        if qhin_id is not None:
            db.add(reg.TefcaEntityRelationship(
                id=uuid.uuid4(), parent_entity_id=qhin_id,
                child_entity_id=entity_id, relationship_type="managed_by_qhin",
                status="active", source="import", effective_date=date(2026, 1, 1)))
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=source_id,
        record_status="CLEAN" if promote else "HELD", issue_count=0,
        correction_count=0, rce_org_oid=oid, name=f"{SYN} ORG {tag}",
        transformation_version="test-1.0.0", canonical_entity_id=entity_id))
    await db.flush()
    return SimpleNamespace(oid=oid, source_record_id=source_id, entity_id=entity_id)


_SEQ = {"n": 0}


def _next_review_id():
    _SEQ["n"] += 1
    return f"REV-8100-{_SEQ['n']:06d}"


async def _dq_case(db, org, *, created_at=None, review_id=None):
    """A DQ exception case, made the way `dq_review_bridge` makes one."""
    review_id = review_id or _next_review_id()
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id=review_id, entity_id=org.entity_id,
        source_record_id=org.source_record_id,
        created_at=created_at or datetime.utcnow(),
        verification_results={"queue_source": so.QUEUE_DQ,
                              "case_classification": "IDENTITY",
                              "severity": "HIGH", "priority": 70}))
    await db.flush()
    return review_id


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


async def _through_qa(db, review_id, *, action="APPROVE", analyst=ANALYST_A):
    from app.tefca_registry.qa_gate import (record_analyst_determination,
                                            submit_qa_review)

    await record_analyst_determination(
        db, review_id, user=analyst, determination="CONFIRM",
        rationale="Synthetic determination for a supervisor view.")
    extra = ({"escalated_to_user_id": SUPERVISOR.id,
              "escalation_reason": "Synthetic escalation reason."}
             if action == "ESCALATE" else {})
    await submit_qa_review(db, review_id, user=QA, qa_action=action,
                           qa_reason=f"Synthetic QA {action}.", **extra)
    await db.flush()


# ═══ S01–S07 — the queue states, derived ═════════════════════════════════════

async def test_the_queue_shows_every_state_and_agrees_with_the_case_itself(
        rolled_back_db):
    """The supervisor list must never disagree with the case it summarises."""
    db = rolled_back_db
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"S0{i}", line=2 + i) for i in range(6)]
    await db.commit()

    ids = {}
    ids["unassigned"] = await _dq_case(db, orgs[0])            # S01
    ids["assigned"] = await _dq_case(db, orgs[1])              # S02
    ids["claimed"] = await _dq_case(db, orgs[2])               # S03
    ids["awaiting"] = await _dq_case(db, orgs[3])              # S04
    ids["returned"] = await _dq_case(db, orgs[4])              # S05
    ids["approved"] = await _dq_case(db, orgs[5])              # S07
    await db.commit()

    await assignment.assign(db, ids["assigned"], user=SUPERVISOR,
                            to_user_id=ANALYST_A.id, reason="synthetic")
    await assignment.claim(db, ids["claimed"], user=ANALYST_A)
    await assignment.claim(db, ids["awaiting"], user=ANALYST_A)
    await assignment.claim(db, ids["returned"], user=ANALYST_A)
    await assignment.claim(db, ids["approved"], user=ANALYST_A)
    await db.commit()

    from app.tefca_registry.qa_gate import record_analyst_determination
    await record_analyst_determination(
        db, ids["awaiting"], user=ANALYST_A, determination="CONFIRM",
        rationale="Synthetic determination awaiting QA.")
    await _through_qa(db, ids["returned"], action="RETURN")
    await _through_qa(db, ids["approved"], action="APPROVE")
    await db.commit()

    page = await so.work_queue(db, limit=50)
    states = {i["review_id"]: i["state"] for i in page["items"]}
    assert states[ids["unassigned"]] == "AVAILABLE"
    assert states[ids["assigned"]] == "CLAIMED"
    assert states[ids["claimed"]] == "CLAIMED"
    assert states[ids["awaiting"]] == "SUBMITTED_FOR_QA"
    assert states[ids["returned"]] == "RETURNED"
    assert states[ids["approved"]] == "APPROVED"

    # And the batched derivation agrees with the per-case service, case by case.
    for review_id, state in states.items():
        assert await assignment.case_state(db, review_id) == state, review_id


async def test_the_unassigned_queue_is_exactly_the_unowned_work(rolled_back_db):
    """STEP 7: no phantom work, and none missing."""
    db = rolled_back_db
    intake_id = await _intake(db)
    qhin_id = await _qhin(db)
    orgs = [await _org(db, intake_id, f"U{i}", line=2 + i, qhin_id=qhin_id)
            for i in range(5)]
    await db.commit()
    ids = [await _dq_case(db, org) for org in orgs]
    await db.commit()
    await assignment.claim(db, ids[0], user=ANALYST_A)
    await assignment.claim(db, ids[1], user=ANALYST_B)
    await db.commit()

    page = await so.work_queue(db, queue_source=so.QUEUE_DQ, unassigned_only=True, limit=50)
    assert page["total"] == 3
    assert {i["review_id"] for i in page["items"]} == set(ids[2:])
    for item in page["items"]:
        assert item["assigned_to_user_id"] is None
        assert item["work_reasons"] == [so.HUMAN_REQUIRED]
        assert item["qhin_entity_id"] == str(qhin_id)
        assert item["age_days"] is not None
        assert item["held_days"] is None, "unheld work has no holding clock"


# ═══ S12–S15 — provenance, never collapsed ═══════════════════════════════════

async def test_a_case_keeps_every_reason_it_exists(rolled_back_db):
    """STEPS 5/57: sample + HUMAN_REQUIRED + a QA return are three facts."""
    from app.tefca_registry import qhin_sampling as qs

    db = rolled_back_db
    qhin_id = await _qhin(db)
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"P{i}", line=2 + i, qhin_id=qhin_id)
            for i in range(5)]
    await db.commit()

    plan = await qs.finalize_plan(db, intake_id, seed=515)
    await db.commit()
    member = (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
        .limit(1))).scalars().first()
    target = next(o for o in orgs if o.entity_id == member.entity_id)

    # The same organisation also carries a DQ exception case.
    dq_id = await _dq_case(db, target)
    await db.commit()
    await assignment.claim(db, dq_id, user=ANALYST_A)
    await _through_qa(db, dq_id, action="RETURN")
    await db.commit()

    page = await so.work_queue(db, queue_source=so.QUEUE_DQ, limit=50)
    item = next(i for i in page["items"] if i["review_id"] == dq_id)
    assert so.HUMAN_REQUIRED in item["work_reasons"]
    assert so.STATISTICAL_SAMPLE in item["work_reasons"], (
        "the organisation is in a frozen sample; that reason must survive")
    assert so.QA_RETURN in item["work_reasons"]
    assert item["sample_ids"] == [plan["sample_id"]]


async def test_a_priority_case_is_never_shown_as_a_statistical_selection(
        rolled_back_db):
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "PR1")
    await db.commit()
    request = await pr.receive_request(
        db, cor_reference="COR-OPS-0001", target_reference=org.oid,
        issue_description="Synthetic COR-reported concern.", requested_by=COR,
        deadline=datetime(2026, 12, 1, 17, 0), actor=COR)
    await db.commit()

    page = await so.work_queue(db, queue_source=so.QUEUE_PRIORITY, limit=50)
    item = next(i for i in page["items"] if i["review_id"] == request["review_id"])
    assert item["work_reasons"] == [so.PRIORITY_REQUEST]
    assert so.STATISTICAL_SAMPLE not in item["work_reasons"]
    assert item["cor_reference"] == "COR-OPS-0001"
    assert item["deadline"] == datetime(2026, 12, 1, 17, 0).isoformat()


# ═══ S08–S10 — deadlines, and nothing invented ═══════════════════════════════

async def test_only_a_cor_deadline_is_a_deadline(rolled_back_db):
    """STEPS 12/55: a DQ case has no deadline, and that is a state not a gap."""
    db = rolled_back_db
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"D{i}", line=2 + i) for i in range(4)]
    await db.commit()

    dq_id = await _dq_case(db, orgs[0])
    now = datetime(2026, 10, 1, 12, 0)
    made = {}
    for tag, org, deadline in (
            ("future", orgs[1], now + timedelta(days=7)),      # S08
            ("soon", orgs[2], now + timedelta(hours=5)),       # S09
            ("past", orgs[3], now - timedelta(days=2))):       # S10
        request = await pr.receive_request(
            db, cor_reference=f"COR-OPS-{tag}", target_reference=org.oid,
            issue_description="Synthetic COR-reported concern.",
            requested_by=COR, deadline=deadline, actor=COR)
        made[tag] = request["review_id"]
    await db.commit()

    page = await so.work_queue(db, limit=200, now=now, due_soon_within_hours=6,
                               search=f"{SYN} ORG D")
    by_id = {i["review_id"]: i for i in page["items"]}
    assert by_id[dq_id]["deadline_status"] == so.NO_DEADLINE
    assert by_id[dq_id]["deadline"] is None
    assert by_id[made["future"]]["deadline_status"] == so.ON_TRACK
    assert by_id[made["soon"]]["deadline_status"] == so.DUE_SOON
    assert by_id[made["past"]]["deadline_status"] == so.PAST_DUE
    # And no case, past due or not, carries a contractual verdict.
    assert all(i["compliance_conclusion"] is None for i in page["items"])
    assert len(by_id) == 4


async def test_due_soon_does_not_exist_until_a_caller_defines_it(rolled_back_db):
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "DS1")
    await db.commit()
    now = datetime(2026, 10, 1, 12, 0)
    request = await pr.receive_request(
        db, cor_reference="COR-OPS-DS", target_reference=org.oid,
        issue_description="Synthetic concern.", requested_by=COR,
        deadline=now + timedelta(hours=5), actor=COR)
    await db.commit()

    default = await so.work_queue(db, queue_source=so.QUEUE_PRIORITY, limit=10, now=now)
    assert default["items"][0]["deadline_status"] == so.ON_TRACK
    assert default["due_soon_within_hours"] is None
    warned = await so.work_queue(db, queue_source=so.QUEUE_PRIORITY, limit=10, now=now,
                                 due_soon_within_hours=6)
    assert warned["items"][0]["deadline_status"] == so.DUE_SOON
    assert request["review_id"] == warned["items"][0]["review_id"]


def test_no_universal_sla_exists_anywhere_in_the_control_plane():
    """STEPS 12/39: no standing turnaround, and no hard-coded staleness."""
    code = _code_of(so)
    for invented in ("timedelta(hours=24", "timedelta(days=1", "REVIEW_SLA_DAYS",
                     "tefca_registry.sla", "sla_status", "OVERDUE"):
        assert invented not in code, f"{invented!r} is an invented service level"
    for name, parameter in (("work_queue", "due_soon_within_hours"),
                            ("work_queue", "stale_after_days"),
                            ("dashboard", "due_soon_within_hours"),
                            ("dashboard", "stale_after_days")):
        assert inspect.signature(getattr(so, name)).parameters[parameter].default \
            is None, f"{name}.{parameter} must have no default"


async def test_a_deadline_amendment_moves_the_dashboard_and_keeps_the_original(
        rolled_back_db):
    """STEP 55: D1 retained, D2 current, no compliance assertion generated."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "AM1")
    await db.commit()
    now = datetime(2026, 10, 5, 12, 0)
    d1, d2 = datetime(2026, 10, 3, 17, 0), datetime(2026, 10, 12, 17, 0)
    request = await pr.receive_request(
        db, cor_reference="COR-OPS-AMEND", target_reference=org.oid,
        issue_description="Synthetic concern.", requested_by=COR,
        deadline=d1, actor=COR)
    await db.commit()

    before = await so.work_queue(db, queue_source=so.QUEUE_PRIORITY, limit=10, now=now)
    assert before["items"][0]["deadline_status"] == so.PAST_DUE

    history = await pr.amend_deadline(
        db, uuid.UUID(request["priority_case_id"]), new_deadline=d2,
        reason="COR extended the deadline in writing.", actor=SUPERVISOR.email)
    await db.commit()

    after = await so.work_queue(db, queue_source=so.QUEUE_PRIORITY, limit=10, now=now)
    assert after["items"][0]["deadline_status"] == so.ON_TRACK
    assert after["items"][0]["deadline"] == d2.isoformat()
    assert after["items"][0]["compliance_conclusion"] is None
    assert history["original_deadline"] == d1.isoformat()


# ═══ S11 / S16 / S17 — limitations ═══════════════════════════════════════════

async def test_an_unavailable_source_is_shown_as_unavailable(rolled_back_db):
    """STEP 15: never a pass, never a clear, never a no-match."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "S11")
    await db.commit()
    for source, status in (("SAM", "unavailable"), ("NPPES", "verified")):
        db.add(reg.TefcaVerification(
            id=uuid.uuid4(), entity_id=org.entity_id, source=source,
            verification_status=status, verified_at=datetime(2026, 9, 1, 9, 0)))
    review_id = await _dq_case(db, org)
    await db.commit()

    page = await so.work_queue(db, queue_source=so.QUEUE_DQ, limit=10)
    item = next(i for i in page["items"] if i["review_id"] == review_id)
    kinds = {(l["kind"], l["detail"]) for l in item["limitations"]}
    assert ("SOURCE_UNAVAILABLE", "SAM") in kinds
    assert ("SOURCE_UNAVAILABLE", "NPPES") not in kinds
    assert item["attention"] == so.BLOCKED
    assert all("not evidence" in l["meaning"] for l in item["limitations"]
               if l["kind"] == "SOURCE_UNAVAILABLE")


async def test_an_ambiguous_target_is_reported_as_a_limitation(rolled_back_db):
    """S16: a case that cannot proceed says why, and is not called overdue."""
    db = rolled_back_db
    intake_id = await _intake(db)
    shared = f"{SYN} SHARED NAME"
    for i in range(2):
        oid = f"{ARC}.AMB{i}"
        source_id = uuid.uuid4()
        db.add(m.RceSourceRecord(
            id=source_id, source_intake_id=intake_id, line_number=2 + i,
            raw_line=oid, parsed={"id": oid},
            record_sha256=hashlib.sha256(oid.encode()).hexdigest(),
            source_rce_id=oid, field_count=len(RCE_FIELDS), parse_status="ok",
            promotion_status="promoted"))
        await db.flush()
        entity_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(
            id=entity_id, name=shared, display_name=shared,
            entity_level="participant", entity_type="provider",
            operational_status="active", verification_status="not_verified",
            current_version=1, is_active=True, rce_org_oid=oid,
            source_record_id=source_id))
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id,
            source_record_id=source_id, record_status="CLEAN", issue_count=0,
            correction_count=0, rce_org_oid=oid, name=shared,
            transformation_version="test-1.0.0", canonical_entity_id=entity_id))
    await db.commit()

    # A priority request naming them by name only resolves to neither.
    request = await pr.receive_request(
        db, cor_reference="COR-OPS-AMB", target_reference=shared,
        issue_description="Synthetic ambiguous target.", requested_by=COR,
        actor=COR)
    await db.commit()
    assert request["review_id"] is None, (
        "an unresolved target has no case yet; the REQUEST is what is logged")
    assert request["state"] == pr.NEEDS_TARGET_RESOLUTION

    overview = await so.priority_overview(db)
    assert overview["by_state"][pr.NEEDS_TARGET_RESOLUTION] == 1
    assert overview["by_deadline_status"][so.NO_DEADLINE] == 1


# ═══ analyst and QA workload ═════════════════════════════════════════════════

async def test_analyst_workload_counts_work_and_scores_nobody(rolled_back_db):
    """STEP 8: workload management, not HR evaluation."""
    db = rolled_back_db
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"W{i}", line=2 + i) for i in range(5)]
    await db.commit()
    ids = [await _dq_case(db, org) for org in orgs]
    await db.commit()
    for review_id in ids[:3]:
        await assignment.claim(db, review_id, user=ANALYST_A)
    await assignment.claim(db, ids[3], user=ANALYST_B)
    await db.commit()

    workload = await so.analyst_workload(db, queue_source=so.QUEUE_DQ)
    by_analyst = {a["assigned_to_user_id"]: a for a in workload["analysts"]}
    assert by_analyst[str(ANALYST_A.id)]["open_cases"] == 3
    assert by_analyst[str(ANALYST_B.id)]["open_cases"] == 1
    assert workload["unassigned_cases"] == 1
    assert by_analyst[str(ANALYST_A.id)]["by_state"] == {"CLAIMED": 3}
    assert "not a performance measure" in workload["note"]

    # Nothing that could become a league table.
    for analyst in workload["analysts"]:
        for banned in ("score", "rank", "throughput", "productivity",
                       "efficiency", "cases_per_day"):
            assert banned not in analyst, f"{banned} is an employee metric"


async def test_qa_workload_is_separate_and_names_whose_determination_waits(
        rolled_back_db):
    """STEP 9/23: a QA lead must be able to see who they may not be."""
    db = rolled_back_db
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"Q{i}", line=2 + i) for i in range(4)]
    await db.commit()
    ids = [await _dq_case(db, org) for org in orgs]
    await db.commit()
    for review_id in ids:
        await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    from app.tefca_registry.qa_gate import record_analyst_determination
    await record_analyst_determination(
        db, ids[0], user=ANALYST_A, determination="CONFIRM",
        rationale="Synthetic determination awaiting QA.")
    await _through_qa(db, ids[1], action="RETURN")
    await _through_qa(db, ids[2], action="ESCALATE")
    await _through_qa(db, ids[3], action="APPROVE")
    await db.commit()

    qa = await so.qa_workload(db, queue_source=so.QUEUE_DQ)
    assert qa["counts"] == {"awaiting_qa": 1, "returned": 1, "escalated": 1,
                            "approved": 1}
    waiting = qa["awaiting_qa"][0]
    assert waiting["review_id"] == ids[0]
    assert waiting["determined_by"] == ANALYST_A.email
    assert waiting["determined_by_user_id"] == str(ANALYST_A.id)
    assert waiting["waiting_days"] is not None
    assert "may never QA their own" in qa["segregation_note"]


# ═══ S18 / S19 — assignment, and the two defects this gate found ═════════════

async def test_taking_a_case_off_a_live_holder_needs_a_stated_reason(
        rolled_back_db):
    """STEP 22: a silent handover is indistinguishable from an unclaimed case."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "S18")
    await db.commit()
    review_id = await _dq_case(db, org)
    await db.commit()

    # Assigning UNHELD work needs nothing beyond the supervisor role.
    await assignment.assign(db, review_id, user=SUPERVISOR,
                            to_user_id=ANALYST_A.id, reason="synthetic")
    await db.commit()

    with pytest.raises(assignment.AssignmentRefused, match="override_reason"):
        await assignment.assign(db, review_id, user=SUPERVISOR,
                                to_user_id=ANALYST_B.id, reason="synthetic")
    await db.rollback()

    result = await assignment.assign(
        db, review_id, user=SUPERVISOR, to_user_id=ANALYST_B.id,
        override_reason="Analyst A is on leave; synthetic cover assignment.")
    await db.commit()
    assert result["previous_owner"] == str(ANALYST_A.id)
    assert result["assigned_to_user_id"] == str(ANALYST_B.id)


async def test_reassignment_keeps_the_whole_handover_history(rolled_back_db):
    """STEP 18: the trail names both sides, the actor and the reason."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "S18b")
    await db.commit()
    review_id = await _dq_case(db, org,
                               created_at=datetime(2026, 6, 1, 9, 0))
    await db.commit()

    await assignment.assign(db, review_id, user=SUPERVISOR,
                            to_user_id=ANALYST_A.id, reason="synthetic first")
    await assignment.assign(db, review_id, user=SUPERVISOR,
                            to_user_id=ANALYST_B.id,
                            override_reason="Synthetic handover for cover.")
    await db.commit()

    rows = [r for r in (await db.execute(
        select(reg.TefcaRegAuditLog))).scalars().all()
        if (r.metadata_ or {}).get("review_id") == review_id]
    actions = [r.action for r in rows]
    assert "review_case_assigned" in actions
    assert "review_case_reassigned" in actions
    handover = next(r for r in rows if r.action == "review_case_reassigned")
    assert handover.metadata_["previous_owner"] == str(ANALYST_A.id)
    assert handover.metadata_["new_owner"] == str(ANALYST_B.id)
    assert handover.metadata_["actor_role"] == SUPERVISOR.role
    assert "cover" in handover.metadata_["override_reason"]

    timeline = await so.audit_timeline(db, review_id)
    events = [e["event"] for e in timeline]
    assert events[0] == "case_created"
    assert "review_case_assigned" in events and "review_case_reassigned" in events
    assert timeline == sorted(timeline, key=lambda e: e["at"]), "not chronological"


async def test_an_approved_case_is_not_reassignable(rolled_back_db):
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "S18c")
    await db.commit()
    review_id = await _dq_case(db, org)
    await db.commit()
    await assignment.claim(db, review_id, user=ANALYST_A)
    await _through_qa(db, review_id, action="APPROVE")
    await db.commit()

    with pytest.raises(assignment.AssignmentRefused, match="APPROVED"):
        await assignment.assign(db, review_id, user=SUPERVISOR,
                                to_user_id=ANALYST_B.id,
                                override_reason="Synthetic attempt on a "
                                                "settled determination.")
    await db.rollback()


# ═══ STEPS 24/25 — management authority is not review authority ══════════════

def test_the_control_plane_cannot_decide_anything():
    """STEPS 24/25: no path here records a determination or a QA approval."""
    code = _code_of(so)
    for forbidden in ("record_analyst_determination", "submit_qa_review",
                      "reportable_at =", "reviewer_resolution =",
                      "classification_bucket =", "record_finding",
                      "supersede_determination"):
        assert forbidden not in code, (
            f"{forbidden!r}: management authority must not become review "
            f"authority")
    # And it writes nothing at all: every public entry point is a read.
    for name, fn in inspect.getmembers(so, inspect.isfunction):
        if name.startswith("_") or fn.__module__ != so.__name__:
            continue
        source = inspect.getsource(fn)
        for write in ("db.add(", "db.delete(", "update(", "insert("):
            assert write not in source, f"{name} performs a {write} write"


def test_no_operations_endpoint_can_write():
    """The control plane's HTTP surface is read-only by construction."""
    from app.tefca_registry.review_routes import router

    for route in router.routes:
        if "/operations" in route.path:
            assert route.methods == {"GET"}, (
                f"{route.path} exposes {route.methods}; a supervisor's only "
                f"write is assignment, which lives on the review routes")


async def test_a_supervisor_cannot_approve_a_case_they_determined(rolled_back_db):
    """STEP 25: holding a management role does not suspend segregation."""
    from app.tefca_registry.qa_gate import (QaGateRefused,
                                            record_analyst_determination,
                                            submit_qa_review)

    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "SEG1")
    await db.commit()
    review_id = await _dq_case(db, org)
    await db.commit()

    # A person with BOTH authorities still cannot check their own work.
    dual = principal("dual.role@synthetic.test", "qalead")
    await record_analyst_determination(
        db, review_id, user=dual, determination="CONFIRM",
        rationale="Synthetic determination by a dual-role principal.")
    await db.commit()
    with pytest.raises(QaGateRefused, match="segregation of duties"):
        await submit_qa_review(db, review_id, user=dual, qa_action="APPROVE",
                               qa_reason="Synthetic self-approval attempt.")
    await db.rollback()


# ═══ STEPS 28-31 — filters, sort, pagination ════════════════════════════════

async def _many(db, count=25):
    intake_id = await _intake(db)
    qhin_id = await _qhin(db)
    orgs = [await _org(db, intake_id, f"M{i}", line=2 + i, qhin_id=qhin_id)
            for i in range(count)]
    await db.commit()
    base = datetime(2026, 6, 1, 9, 0)
    ids = [await _dq_case(db, org, created_at=base + timedelta(minutes=i))
           for i, org in enumerate(orgs)]
    await db.commit()
    return intake_id, qhin_id, ids


async def test_pagination_is_deterministic_and_loses_nothing(rolled_back_db):
    """STEP 31: an unstable sort shows one case twice and hides another."""
    db = rolled_back_db
    _, _, ids = await _many(db, 25)

    seen, offset = [], 0
    while True:
        page = await so.work_queue(db, queue_source=so.QUEUE_DQ, sort="age", offset=offset,
                                   limit=7)
        seen.extend(i["review_id"] for i in page["items"])
        assert page["total"] == 25
        if not page["has_more"]:
            break
        offset += 7
    assert len(seen) == 25
    assert len(set(seen)) == 25, "a case appeared on two pages"
    assert set(seen) == set(ids)

    # And the same page asked for twice is the same page.
    first = await so.work_queue(db, queue_source=so.QUEUE_DQ, sort="age", offset=7, limit=7)
    again = await so.work_queue(db, queue_source=so.QUEUE_DQ, sort="age", offset=7, limit=7)
    assert [i["review_id"] for i in first["items"]] \
        == [i["review_id"] for i in again["items"]]


def test_every_sort_is_tie_broken_by_the_one_unique_column():
    """Without this the ordering is partial, and pagination silently lies.

    Asserted on the SQL rather than on an observed page: with equal sort keys
    Postgres MAY happen to return a stable order, so a behavioural test can
    pass while the ordering is undefined. The ORDER BY clause cannot.
    """
    from sqlalchemy import select as _select

    for sort in so.SORTS:
        clause = str(so.ordered(_select(reg.ReviewRecord), sort).compile(
            compile_kwargs={"literal_binds": True}))
        order_by = clause.split("ORDER BY")[-1]
        assert "review_records.review_id" in order_by, (
            f"sort {sort!r} has no unique tie-break: {order_by.strip()}")
        if sort in so._SORT_COLUMNS:
            assert order_by.strip().endswith("review_records.review_id ASC"), (
                f"sort {sort!r} must break ties LAST, not first")


async def test_identical_timestamps_still_paginate_without_loss(rolled_back_db):
    """And the same thing observed end to end, on equal timestamps."""
    db = rolled_back_db
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"T{i}", line=2 + i) for i in range(9)]
    await db.commit()
    same = datetime(2026, 6, 1, 9, 0)
    ids = [await _dq_case(db, org, created_at=same) for org in orgs]
    await db.commit()

    seen = []
    for offset in (0, 3, 6):
        page = await so.work_queue(db, queue_source=so.QUEUE_DQ, sort="age", offset=offset,
                                   limit=3)
        seen.extend(i["review_id"] for i in page["items"])
    assert sorted(seen) == sorted(ids)
    assert len(set(seen)) == 9


async def test_the_queue_filters_on_what_a_supervisor_actually_asks(
        rolled_back_db):
    """STEP 28: filters that mean something operationally, and no more."""
    db = rolled_back_db
    intake_id, qhin_id, ids = await _many(db, 6)
    await assignment.claim(db, ids[0], user=ANALYST_A)
    await assignment.claim(db, ids[1], user=ANALYST_B)
    await db.commit()
    await _through_qa(db, ids[0], action="RETURN")
    await db.commit()

    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ, unassigned_only=True))["total"] == 4
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ, assignee=ANALYST_B.id))["total"] == 1
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ, state="RETURNED"))["total"] == 1
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ, work_reason=so.QA_RETURN))["total"] == 1
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ,
                                work_reason=so.HUMAN_REQUIRED))["total"] == 6
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ))["total"] == 6
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ, qhin_entity_id=qhin_id))["total"] == 6
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ, reportable=True))["total"] == 0


async def test_search_finds_a_case_by_reference_and_by_organisation(
        rolled_back_db):
    """STEP 29: operational identifiers only, and no leading wildcard."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "SRCH")
    await db.commit()
    review_id = await _dq_case(db, org)
    request = await pr.receive_request(
        db, cor_reference="COR-OPS-SEARCH", target_reference=org.oid,
        issue_description="Synthetic concern.", requested_by=COR, actor=COR)
    await db.commit()

    by_reference = await so.work_queue(db, search=review_id)
    assert [i["review_id"] for i in by_reference["items"]] == [review_id]

    by_cor = await so.work_queue(db, search="COR-OPS-SEARCH")
    assert [i["review_id"] for i in by_cor["items"]] == [request["review_id"]]

    by_name = await so.work_queue(db, search=f"{SYN} ORG SRCH")
    assert {i["review_id"] for i in by_name["items"]} == {review_id,
                                                          request["review_id"]}
    assert (await so.work_queue(db, search="no-such-thing"))["total"] == 0


async def test_sorting_puts_the_oldest_and_the_soonest_first(rolled_back_db):
    """STEP 30: and a case with NO deadline is not the most urgent one."""
    db = rolled_back_db
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"SO{i}", line=2 + i) for i in range(3)]
    await db.commit()
    base = datetime(2026, 6, 1, 9, 0)
    oldest = await _dq_case(db, orgs[0], created_at=base)
    await _dq_case(db, orgs[1], created_at=base + timedelta(days=5))
    request = await pr.receive_request(
        db, cor_reference="COR-OPS-SORT", target_reference=orgs[2].oid,
        issue_description="Synthetic concern.", requested_by=COR,
        deadline=datetime(2026, 7, 1, 9, 0), actor=COR)
    await db.commit()

    by_age = await so.work_queue(db, sort="age", search=f"{SYN} ORG SO")
    assert by_age["items"][0]["review_id"] == oldest

    by_deadline = await so.work_queue(db, sort="deadline",
                                      search=f"{SYN} ORG SO")
    assert by_deadline["items"][0]["review_id"] == request["review_id"]
    assert by_deadline["items"][-1]["deadline"] is None, (
        "a case with no deadline must sort last, not first")

    with pytest.raises(so.SupervisorRefused, match="sort must be"):
        await so.work_queue(db, sort="whatever_the_client_asked_for")


# ═══ STEP 32 / 34 — empty states ════════════════════════════════════════════

async def test_no_sampling_plan_reads_as_a_zero_state_not_as_late_work(
        rolled_back_db):
    """STEP 34: a plan nobody drew is not a plan running behind."""
    db = rolled_back_db
    overview = await so.sampling_overview(db)
    assert overview["status"] == so.NOT_YET_CREATED
    assert overview["official_plans"] == 0
    assert "%" not in str(overview)
    assert "overdue" not in str(overview).lower()
    # No progress number of any kind: a figure implies a denominator, and there
    # is no plan to be a fraction of.
    for numeric in ("completion", "progress", "selected", "remaining",
                    "sample_size", "population_size"):
        assert numeric not in overview, f"{numeric} implies a plan that exists"
    assert "No official sampling plan has been created" in overview["note"]


async def test_an_empty_estate_produces_an_honest_dashboard(rolled_back_db):
    """STEP 32: zero is a number, not an error and not a failure."""
    db = rolled_back_db
    board = await so.dashboard(db, queue_source=so.QUEUE_DQ)
    assert board["total_cases"] == 0
    assert board["sampling"]["status"] == so.NOT_YET_CREATED
    assert board["priority"]["active_requests"] == 0
    assert board["priority"]["by_deadline_status"] == {}
    assert board["reportable"] == 0


async def test_the_dashboard_counts_reconcile_with_the_queue(rolled_back_db):
    """STEP 48: the summary and the list must be the same answer."""
    db = rolled_back_db
    intake_id, _, ids = await _many(db, 8)
    await assignment.claim(db, ids[0], user=ANALYST_A)
    await assignment.claim(db, ids[1], user=ANALYST_A)
    await db.commit()
    await _through_qa(db, ids[0], action="APPROVE")
    await db.commit()

    board = await so.dashboard(db, queue_source=so.QUEUE_DQ)
    queue = await so.work_queue(db, queue_source=so.QUEUE_DQ, limit=200)
    assert board["total_cases"] == queue["total"]
    assert board["unassigned"] == (
        await so.work_queue(db, queue_source=so.QUEUE_DQ, unassigned_only=True))["total"]
    assert board["reportable"] == (
        await so.work_queue(db, queue_source=so.QUEUE_DQ, reportable=True))["total"]
    assert board["by_state"]["APPROVED"] == 1
    assert board["by_work_reason"][so.HUMAN_REQUIRED] == 8


# ═══ STEP 56 — sampling counts reconcile to frozen membership ════════════════

async def test_sampling_progress_reconciles_to_the_frozen_membership(
        rolled_back_db):
    from app.tefca_registry import qhin_sampling as qs

    db = rolled_back_db
    qhin_id = await _qhin(db)
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"SM{i}", line=2 + i, qhin_id=qhin_id)
            for i in range(6)]
    await db.commit()
    plan = await qs.finalize_plan(db, intake_id, seed=5656)
    await db.commit()

    overview = await so.sampling_overview(db)
    assert overview["status"] == "PLANS_EXIST"
    entry = overview["plans"][0]
    assert entry["sample_id"] == plan["sample_id"]
    assert entry["plan_source"] == "TEFCA_ARC_PER_QHIN"
    assert entry["stratify_by"] == "managed_by_qhin"
    assert entry["counts"]["selected"] == plan["membership_count"]
    members = int((await db.execute(
        select(func.count()).select_from(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
    )).scalar() or 0)
    assert entry["counts"]["selected"] == members
    assert entry["complete"] is False


# ═══ STEPS 52-54 — the end-to-end supervisor paths ═══════════════════════════

async def test_the_supervisor_can_follow_one_case_from_intake_to_reportable(
        rolled_back_db):
    """STEP 52: unassigned → assigned → in progress → QA → reportable."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "E2E")
    await db.commit()
    # An explicit creation time: Postgres stamps the audit rows with
    # transaction-start time, so a case "created" later in the same transaction
    # would sort after its own assignment.
    review_id = await _dq_case(db, org, created_at=datetime(2026, 6, 1, 9, 0))
    await db.commit()

    async def seen_state():
        return (await so.case_detail(db, review_id))["state"]

    assert await seen_state() == "AVAILABLE"
    assert (await so.dashboard(db, queue_source=so.QUEUE_DQ))["unassigned"] == 1

    await assignment.assign(db, review_id, user=SUPERVISOR,
                            to_user_id=ANALYST_A.id, reason="synthetic")
    await db.commit()
    assert await seen_state() == "CLAIMED"
    assert (await so.dashboard(db, queue_source=so.QUEUE_DQ))["in_progress"] == 1

    from app.tefca_registry.qa_gate import record_analyst_determination
    await record_analyst_determination(
        db, review_id, user=ANALYST_A, determination="CONFIRM",
        rationale="Synthetic determination on the end-to-end path.")
    await db.commit()
    assert await seen_state() == "SUBMITTED_FOR_QA"
    assert (await so.qa_workload(db, queue_source=so.QUEUE_DQ))["counts"]["awaiting_qa"] == 1

    from app.tefca_registry.qa_gate import submit_qa_review
    await submit_qa_review(db, review_id, user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic QA approval, end-to-end path.")
    await db.commit()
    detail = await so.case_detail(db, review_id)
    assert detail["state"] == "APPROVED" and detail["reportable"] is True
    assert (await so.dashboard(db, queue_source=so.QUEUE_DQ))["reportable"] == 1
    events = [e["event"] for e in detail["timeline"]]
    assert events[0] == "case_created"
    assert "qa_approve" in events and "became_reportable" in events


async def test_a_qa_return_stays_one_case_all_the_way_through(rolled_back_db):
    """STEPS 37/53: returned work is the same case, never a duplicate."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "RET")
    await db.commit()
    review_id = await _dq_case(db, org)
    await db.commit()
    await assignment.claim(db, review_id, user=ANALYST_A)
    await _through_qa(db, review_id, action="RETURN")
    await db.commit()

    returned = await so.work_queue(db, queue_source=so.QUEUE_DQ, state="RETURNED")
    assert returned["total"] == 1
    item = returned["items"][0]
    assert item["review_id"] == review_id
    assert item["assigned_to_user_id"] == str(ANALYST_A.id), (
        "a returned case stays with the analyst who must revise it")
    assert so.QA_RETURN in item["work_reasons"]
    assert so.HUMAN_REQUIRED in item["work_reasons"]

    await _through_qa(db, review_id, action="APPROVE")
    await db.commit()
    assert (await so.work_queue(db, queue_source=so.QUEUE_DQ, limit=50))["total"] == 1, (
        "the revision must not have created a second case")
    assert (await so.case_detail(db, review_id))["reportable"] is True


async def test_an_escalation_is_visible_and_resolves_nothing(rolled_back_db):
    """STEPS 38/54: a supervisor can see it; nobody here can settle it."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "ESC")
    await db.commit()
    review_id = await _dq_case(db, org)
    await db.commit()
    await assignment.claim(db, review_id, user=ANALYST_A)
    await _through_qa(db, review_id, action="ESCALATE")
    await db.commit()

    escalated = await so.work_queue(db, queue_source=so.QUEUE_DQ, state="ESCALATED")
    assert escalated["total"] == 1
    assert so.QA_ESCALATION in escalated["items"][0]["work_reasons"]
    assert escalated["items"][0]["reportable"] is False
    assert (await so.qa_workload(db, queue_source=so.QUEUE_DQ))["counts"]["escalated"] == 1
    assert (await so.dashboard(db, queue_source=so.QUEUE_DQ))["escalated"] == 1


# ═══ STEP 64 — the DQ ledger is not the operational queue ═══════════════════

async def test_a_dq_finding_is_not_an_analyst_case(rolled_back_db):
    """STEP 64: reported separately, and the difference is stated, not closed."""
    db = rolled_back_db
    readiness = await so.government_readiness(db)
    assert readiness["dq_human_required_findings"] >= readiness["operational_dq_review_cases"]
    assert readiness["unoperationalized_findings"] == (
        readiness["dq_human_required_findings"]
        - readiness["operational_dq_review_cases"])
    assert "not an analyst case" in readiness["note"]


# ═══ STEP 59 — performance ══════════════════════════════════════════════════

async def test_a_page_of_work_costs_a_fixed_number_of_queries(rolled_back_db):
    """STEP 59: the N+1 that a per-case state lookup would have produced."""
    db = rolled_back_db
    _, _, ids = await _many(db, 30)
    for review_id in ids[:10]:
        await assignment.claim(db, review_id, user=ANALYST_A)
    await db.commit()

    counted = {"n": 0}
    original = db.execute

    async def counting(*args, **kwargs):
        counted["n"] += 1
        return await original(*args, **kwargs)

    db.execute = counting
    try:
        page = await so.work_queue(db, queue_source=so.QUEUE_DQ, limit=25)
    finally:
        db.execute = original

    assert page["returned"] == 25
    assert counted["n"] <= 12, (
        f"{counted['n']} queries for one page — the derivation is per-case "
        f"again, which is the N+1 this module exists to avoid")


# ═══ STEP 58 — volume ═══════════════════════════════════════════════════════

async def test_a_month_of_priority_work_plus_a_surge_stays_coherent(
        rolled_back_db):
    """24 requests: the ~20 the contract anticipates plus a synthetic surge.

    The 20% is TEST LOAD. The contract sets no surge requirement, and this
    asserts nothing about one.
    """
    db = rolled_back_db
    intake_id = await _intake(db)
    qhin_id = await _qhin(db)
    orgs = [await _org(db, intake_id, f"V{i}", line=2 + i, qhin_id=qhin_id)
            for i in range(24)]
    await db.commit()
    now = datetime(2026, 11, 1, 12, 0)
    for i, org in enumerate(orgs):
        await pr.receive_request(
            db, cor_reference=f"COR-OPS-V{i:03d}", target_reference=org.oid,
            issue_description="Synthetic COR-reported concern.",
            requested_by=COR, deadline=now + timedelta(days=1 + i), actor=COR)
    await db.commit()

    board = await so.dashboard(db, queue_source=so.QUEUE_PRIORITY, now=now)
    assert board["total_cases"] == 24
    assert board["by_work_reason"][so.PRIORITY_REQUEST] == 24
    assert board["by_deadline_status"] == {so.ON_TRACK: 24}
    assert board["priority"]["active_requests"] == 24

    seen = set()
    for offset in range(0, 24, 10):
        page = await so.work_queue(db, queue_source=so.QUEUE_PRIORITY, offset=offset, limit=10,
                                   now=now)
        seen.update(i["review_id"] for i in page["items"])
    assert len(seen) == 24


# ═══ STEPS 21/47 — concurrency, two committing sessions ═════════════════════

SCHEMA = "ops_gate_tmp"


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
               "review_decision_events", "tefca_reg_audit_log",
               "tefca_verifications")]
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


async def _sandbox_case(engine):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        intake_id = await _intake(db)
        org = await _org(db, intake_id, "CONC")
        review_id = await _dq_case(db, org)
        await db.commit()
        return review_id


async def test_two_supervisors_assigning_at_once_produce_one_owner(
        sandbox_engine):
    """STEP 21: the lost update `assign` used to allow.

    The old implementation read the record, set the column and flushed. Two
    supervisors therefore both succeeded, one assignment vanished, and BOTH
    audit rows recorded `previous_owner: None` — so the trail said the case had
    been assigned twice from nobody.
    """
    review_id = await _sandbox_case(sandbox_engine)

    async def attempt(user, to_user_id):
        async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
            try:
                result = await assignment.assign(db, review_id, user=user,
                                                 to_user_id=to_user_id,
                                                 reason="synthetic race")
                await db.commit()
                return "OK", result["assigned_to_user_id"]
            except Exception as exc:                          # noqa: BLE001
                await db.rollback()
                return type(exc).__name__, str(exc)[:120]

    results = await asyncio.gather(attempt(SUPERVISOR, ANALYST_A.id),
                                   attempt(SUPERVISOR_2, ANALYST_B.id))
    winners = [r for r in results if r[0] == "OK"]
    assert len(winners) == 1, f"both assignments succeeded: {results}"
    assert results[0][0] == "AssignmentRefused" or results[1][0] == "AssignmentRefused"

    async with AsyncSession(sandbox_engine) as db:
        record = (await db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
        audits = [r for r in (await db.execute(
            select(reg.TefcaRegAuditLog))).scalars().all()
            if (r.metadata_ or {}).get("review_id") == review_id]
    assert str(record.assigned_to_user_id) == winners[0][1]
    assigned_rows = [a for a in audits if a.action == "review_case_assigned"]
    assert len(assigned_rows) == 1, "a losing assignment still wrote an audit row"


async def test_a_claim_racing_a_supervisor_assignment_leaves_one_owner(
        sandbox_engine):
    """STEP 47: an analyst claiming while a supervisor assigns."""
    review_id = await _sandbox_case(sandbox_engine)

    async def claim():
        async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
            try:
                await assignment.claim(db, review_id, user=ANALYST_A)
                await db.commit()
                return "CLAIM_OK"
            except Exception as exc:                          # noqa: BLE001
                await db.rollback()
                return f"CLAIM_{type(exc).__name__}"

    async def assign():
        async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
            try:
                await assignment.assign(db, review_id, user=SUPERVISOR,
                                        to_user_id=ANALYST_B.id,
                                        reason="synthetic race")
                await db.commit()
                return "ASSIGN_OK"
            except Exception as exc:                          # noqa: BLE001
                await db.rollback()
                return f"ASSIGN_{type(exc).__name__}"

    results = await asyncio.gather(claim(), assign())
    async with AsyncSession(sandbox_engine) as db:
        record = (await db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.review_id == review_id))).scalars().first()

    owner = record.assigned_to_user_id
    assert owner is not None, f"the case ended up unowned: {results}"
    assert owner in (ANALYST_A.id, ANALYST_B.id)
    # Whichever act lost must have said so rather than silently disappearing.
    assert not all(r.endswith("_OK") for r in results) or owner == ANALYST_B.id, (
        f"both succeeded without a stated handover: {results}")


# ═══ STEPS 43/45/46/49 — the HTTP surface ═══════════════════════════════════

OPERATIONS_ENDPOINTS = [
    "/api/tefca/arc/operations/dashboard",
    "/api/tefca/arc/operations/work-queue",
    "/api/tefca/arc/operations/analyst-workload",
    "/api/tefca/arc/operations/qa-workload",
    "/api/tefca/arc/operations/sampling",
    "/api/tefca/arc/operations/priority",
    "/api/tefca/arc/operations/readiness",
    "/api/tefca/arc/operations/cases/REV-2026-000001",
    "/api/tefca/arc/operations/cases/REV-2026-000001/timeline",
]


@pytest.mark.parametrize("path", OPERATIONS_ENDPOINTS)
def test_every_operations_endpoint_requires_authentication(client, path):
    response = client.get(path)
    assert response.status_code in GATED, (
        f"GET {path} answered {response.status_code} unauthenticated")


def test_each_operations_act_requires_the_authority_it_should():
    """STEP 43: reads at the viewer floor; the authority is on the writes."""
    from app.core.security import ROLE_HIERARCHY
    from app.tefca_registry.review_routes import router

    seen = {}
    for route in router.routes:
        for dependency in getattr(route, "dependencies", []):
            closure = getattr(dependency.dependency, "__closure__", None) or ()
            for cell in closure:
                if isinstance(cell.cell_contents, str) and \
                        cell.cell_contents in ROLE_HIERARCHY:
                    for method in route.methods:
                        seen[(method, route.path)] = cell.cell_contents

    for path in ("/api/tefca/arc/operations/dashboard",
                 "/api/tefca/arc/operations/work-queue",
                 "/api/tefca/arc/operations/analyst-workload",
                 "/api/tefca/arc/operations/qa-workload",
                 "/api/tefca/arc/operations/readiness",
                 "/api/tefca/arc/operations/cases/{review_id}"):
        assert seen.get(("GET", path)) == "viewer", path
    # The write a supervisor owns is gated where it always was.
    assert seen.get(("POST", "/api/tefca/arc/reviews/{review_id}/assign")) \
        == "senior_analyst"


def test_no_protected_field_is_settable_through_the_assignment_model():
    """STEPS 45/46: the actor comes from the token, never from the body."""
    from app.tefca_registry import review_routes as rr

    fields = set(rr.CaseAssign.model_fields)
    assert fields == {"to_user_id", "reason", "override_reason"}
    for protected in ("assigned_by", "supervisor_id", "actor", "actor_id",
                      "approved_by", "created_by", "reportable", "determination",
                      "qa_action", "qa_result"):
        assert protected not in fields, f"{protected} is settable by a client"

    # And the handler may never REBIND the injected principal. Checking for
    # the string `user=user` is not enough: a handler can reassign `user` from
    # the body first and still pass `user=user` to the service, which is
    # exactly how an actor taken from client JSON would look.
    import ast

    tree = ast.parse(inspect.getsource(rr.assign_case).lstrip())
    handler = tree.body[0]
    assert isinstance(handler, (ast.AsyncFunctionDef, ast.FunctionDef))
    assert "user" in {a.arg for a in handler.args.args + handler.args.kwonlyargs}, (
        "the handler must receive the authenticated principal as a parameter")
    for node in ast.walk(handler):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            assert not (isinstance(target, ast.Name) and target.id == "user"), (
                "the handler reassigns `user`; the audit actor must come from "
                "the authenticated context and nowhere else")

    source = inspect.getsource(rr.assign_case)
    assert "user=user" in source, "the actor must come from the authenticated user"
    assert "req.assigned_by" not in source and "req.actor" not in source


def test_the_queue_payload_carries_no_delivered_government_values():
    """STEP 49: managing work needs references, not the delivery's contents."""
    code = _code_of(so)
    for leaked in ("raw_line", "parsed[", "original_value", "suggested_value",
                   "api_key", "password", "token", "connection_string"):
        assert leaked not in code, f"{leaked!r} would put source content on a list view"


async def test_case_detail_shows_management_facts_not_evidence_values(
        rolled_back_db):
    """STEP 26: enough to manage the work; reading the evidence is not that."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "DET")
    await db.commit()
    review_id = await _dq_case(db, org)
    await db.commit()

    detail = await so.case_detail(db, review_id)
    for expected in ("review_id", "entity_name", "work_reasons", "state",
                     "assigned_to_user_id", "age_days", "idle_days",
                     "limitations", "attention", "reportable", "timeline"):
        assert expected in detail, expected
    for absent in ("raw_line", "parsed", "original_value", "evidence"):
        assert absent not in detail, f"{absent} does not belong on a management view"

    with pytest.raises(so.SupervisorRefused, match="no review exists"):
        await so.case_detail(db, "REV-0000-000000")


def test_fixtures_are_synthetic_only():
    for actor in (ANALYST_A, ANALYST_B, QA, SUPERVISOR, SUPERVISOR_2):
        assert actor.email.endswith("@synthetic.test")
    assert COR.endswith("@synthetic.test")
    assert ARC.startswith("9.99.")
