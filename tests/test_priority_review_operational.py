"""Task 5 priority reviews, end to end, on the certified review workflow.

    authorized COR request -> target resolution -> review case
      -> assignment -> analyst determination -> independent QA
      -> reportability -> D5.1 content

WHAT THIS GATE EXISTS TO PROVE
──────────────────────────────
Priority reviews already had a table, routes and a report. What they did not
have was the maker-checker chain: one `senior_analyst` could PATCH a root
cause, a severity and a resolution in a single call, and the report printed it.
These tests pin the opposite — that no priority result reaches a report without
an analyst determination and a DIFFERENT person's approval.

They also pin the two things the contract settles and the code must not
re-decide. Task 5 (¶146) says the deadline is "communicated by the COR", so
there is no standing turnaround and none is computed; and it says the entities
are named by the COR, so nothing here can create a priority review from a
data-quality severity.

GOVERNMENT DATA
    Every test runs inside an OUTER transaction that is rolled back, except the
    concurrency test, which needs two committing sessions and uses a throwaway
    schema in a separate database. Fixtures are synthetic: OIDs under an
    unassigned `9.99.777` arc, prefixed names, `@synthetic.test` identities. No
    Government request, case, assignment, decision or report is created.
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
from app.tefca_registry import models as reg
from app.tefca_registry import priority_review as pr
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint

#: Both codes mean "correctly gated": FastAPI answers 401 for a missing
#: credential and 403 for a role refusal, and asserting on one alone breaks
#: across the version boundary. Mirrors `tests/conftest.py`.
GATED = (401, 403)


def _code_of(module) -> str:
    """A module's SOURCE with its own prose removed.

    Scanning the raw file would match this module's own docstring, which
    NAMES the things it refuses to do. The prohibition is about code.
    """
    import ast

    tree = ast.parse(inspect.getsource(module))
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)):
        tree.body = tree.body[1:]
    return ast.unparse(tree)


SYN = "SYNTHETIC-PRIORITY"
ARC = "9.99.777"
COR = "cor.officer@synthetic.test"


def principal(email, role, user_id=None):
    return SimpleNamespace(id=user_id or uuid.uuid4(), email=email, role=role)


ANALYST = principal("analyst@synthetic.test", "reviewer")
ANALYST_B = principal("other.analyst@synthetic.test", "reviewer")
QA = principal("qa@synthetic.test", "qalead")
SUPERVISOR = principal("supervisor@synthetic.test", "senior_analyst")


# ── synthetic population ─────────────────────────────────────────────────────

async def _intake(db, label=f"{SYN}-DELIVERY"):
    intake_id = uuid.uuid4()
    blob = b"synthetic"
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=label, original_filename="synthetic.csv",
        storage_path="(synthetic)",
        sha256=hashlib.sha256(blob + intake_id.bytes).hexdigest(),
        file_size_bytes=len(blob), delimiter="|", encoding="utf-8",
        line_terminator="CRLF", headers=list(RCE_FIELDS),
        schema_fingerprint=schema_fingerprint(list(RCE_FIELDS)),
        record_count=0, received_at=datetime.utcnow(), received_by=SYN,
        status="PARSED", source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()
    return intake_id


async def _org(db, intake_id, tag, *, promote=True, held=False, name=None,
               line=2, qhin_id=None):
    """One delivered organisation, optionally left unpromoted (HELD)."""
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
            id=entity_id, name=name or f"{SYN} ORG {tag}",
            display_name=name or f"{SYN} ORG {tag}", entity_level="participant",
            entity_type="provider", operational_status="active",
            verification_status="not_verified", current_version=1,
            is_active=True, rce_org_oid=oid, source_record_id=source_id))
        await db.flush()
        if qhin_id is not None:
            db.add(reg.TefcaEntityRelationship(
                id=uuid.uuid4(), parent_entity_id=qhin_id,
                child_entity_id=entity_id,
                relationship_type="managed_by_qhin", status="active",
                source="import", effective_date=date(2026, 1, 1)))

    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=source_id,
        record_status="HELD" if held else "CLEAN",
        issue_count=1 if held else 0, correction_count=0, rce_org_oid=oid,
        name=name or f"{SYN} ORG {tag}", transformation_version="test-1.0.0",
        canonical_entity_id=entity_id))
    await db.flush()
    return SimpleNamespace(oid=oid, source_record_id=source_id, entity_id=entity_id)


async def _qhin(db, tag="QHIN"):
    qhin_id = uuid.uuid4()
    db.add(reg.TefcaRegEntity(
        id=qhin_id, name=f"{SYN} {tag}", display_name=f"{SYN} {tag}",
        entity_level="qhin", entity_type="health_information_network",
        operational_status="active", verification_status="not_verified",
        current_version=1, is_active=True))
    await db.flush()
    return qhin_id


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


async def _request(db, org, *, reference="COR-SYNTH-0001", deadline=None,
                   issue=f"{SYN}: the COR reported an addressing concern.",
                   instructions=None, target=None):
    return await pr.receive_request(
        db, cor_reference=reference,
        target_reference=target or org.oid,
        issue_description=issue, requested_by=COR, deadline=deadline,
        instructions=instructions, actor=COR)


# ═══ P01 — a clean resolved entity, the whole path ═══════════════════════════

async def test_a_request_becomes_a_case_on_the_certified_workflow(rolled_back_db):
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P01")
    await db.commit()

    result = await _request(db, org)
    await db.commit()

    assert result["duplicate_request"] is False
    assert result["cor_reference"] == "COR-SYNTH-0001"
    assert result["requested_by"] == COR
    assert result["target_resolution"] == "RESOLVED"
    assert result["entity_id"] == str(org.entity_id)
    assert result["review_id"].startswith("REV-")
    assert result["state"] == "AVAILABLE"
    assert result["reportable"] is False

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == result["review_id"]))).scalars().first()
    payload = record.verification_results
    assert payload["queue_source"] == pr.QUEUE_SOURCE
    assert payload["selection_reason"] == "PRIORITY_REQUEST"
    assert payload["contract_authority"].startswith("SOW Task 5")
    # The case is a QUESTION. Nothing about it is an answer yet.
    assert record.classification_bucket is None
    assert record.reviewer_resolution is None
    assert record.reportable_at is None


async def test_the_request_carries_its_own_authority(rolled_back_db):
    """A priority review that cannot name who asked for it is not one."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P01b")
    await db.commit()

    for kwargs, expected in (
            ({"cor_reference": ""}, "COR reference"),
            ({"requested_by": ""}, "who made the request"),
            ({"issue_description": ""}, "issue the COR described"),
            ({"target_reference": ""}, "organisation the COR named")):
        args = {"cor_reference": "COR-SYNTH-X", "target_reference": org.oid,
                "issue_description": "A synthetic issue description.",
                "requested_by": COR, **kwargs}
        with pytest.raises(pr.PriorityRefused, match=expected):
            await pr.receive_request(db, **args)
        await db.rollback()


def test_no_rule_can_manufacture_a_priority_request():
    """STEP 3/9: priority authority is explicit, never inferred from severity."""
    source = _code_of(pr)
    for inferred in ("HUMAN_REQUIRED", "rce_issues", "quality_run", "sample_id",
                     "dq_review_bridge", "qhin_sampling"):
        assert inferred not in source, (
            f"{inferred!r} in the priority service suggests a request could be "
            f"created from something other than a COR instruction")
    params = inspect.signature(pr.receive_request).parameters
    for required in ("cor_reference", "requested_by", "issue_description",
                     "target_reference"):
        assert params[required].default is inspect.Parameter.empty, (
            f"{required} must have no default; a default is how an AGT-initiated "
            f"review starts looking like a Government request")


# ═══ deadline — the contract's rule, and nothing invented ════════════════════

def test_no_standing_turnaround_is_manufactured():
    """STEP 8: ¶146 sets the deadline per request. Nothing here computes one."""
    source = _code_of(pr)
    for invented in ("timedelta(hours=24", "timedelta(days=1", "SLA_DAYS",
                     "DEFAULT_DEADLINE", "from app.tefca_registry import sla",
                     "from app.tefca_registry.sla"):
        assert invented not in source, f"{invented!r} is an invented service level"
    # And the warning band has no default, so it cannot arrive as one.
    assert (inspect.signature(pr.deadline_status)
            .parameters["due_soon_within_hours"].default is None)


def test_an_absent_deadline_is_a_state_not_a_default():
    status = pr.deadline_status(None)
    assert status["status"] == pr.NO_DEADLINE
    assert status["hours_remaining"] is None
    assert "ask, not to assume" in status["note"]


def test_deadline_status_is_measured_against_the_cor_deadline():
    now = datetime(2026, 9, 1, 12, 0)
    future = pr.deadline_status(now + timedelta(hours=48), now=now)
    past = pr.deadline_status(now - timedelta(hours=1), now=now)
    assert future["status"] == pr.ON_TRACK and future["hours_remaining"] == 48.0
    assert past["status"] == pr.PAST_DUE and past["hours_remaining"] == -1.0


def test_due_soon_only_exists_when_the_caller_defines_it():
    now = datetime(2026, 9, 1, 12, 0)
    deadline = now + timedelta(hours=5)
    assert pr.deadline_status(deadline, now=now)["status"] == pr.ON_TRACK
    warned = pr.deadline_status(deadline, now=now, due_soon_within_hours=6)
    assert warned["status"] == pr.DUE_SOON
    assert warned["due_soon_within_hours"] == 6


def test_past_due_is_not_a_compliance_finding():
    """STEP 22: a timestamp does not know what was agreed or communicated."""
    now = datetime(2026, 9, 1, 12, 0)
    status = pr.deadline_status(now - timedelta(days=9), now=now)
    assert status["status"] == pr.PAST_DUE
    assert status["compliance_conclusion"] is None
    assert "No standing AGT service level" in status["note"]


# ═══ P10 — deadline amendment ════════════════════════════════════════════════

async def test_an_amended_deadline_keeps_the_one_the_cor_first_set(rolled_back_db):
    """STEP 23: D1 retained, D2 current, actor and reason recorded."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P10")
    await db.commit()

    d1 = datetime(2026, 9, 10, 17, 0)
    d2 = datetime(2026, 9, 17, 17, 0)
    request = await _request(db, org, reference="COR-SYNTH-0010", deadline=d1)
    await db.commit()

    history = await pr.amend_deadline(
        db, uuid.UUID(request["priority_case_id"]), new_deadline=d2,
        reason="COR extended the deadline by email of 2026-09-05.",
        actor=SUPERVISOR.email, actor_id=SUPERVISOR.id)
    await db.commit()

    assert history["original_deadline"] == d1.isoformat()
    assert history["current_deadline"] == d2.isoformat()
    assert history["amendments"] == 1
    assert history["history"][-1]["actor"] == SUPERVISOR.email
    assert "COR extended" in history["history"][-1]["reason"]
    # And the receipt value is still readable on the case itself.
    fresh = await pr.get_request(db, uuid.UUID(request["priority_case_id"]))
    assert fresh["deadline_at_receipt"] == d1.isoformat()
    assert fresh["deadline"] == d2.isoformat()


async def test_a_deadline_amendment_must_name_its_authority(rolled_back_db):
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P10b")
    await db.commit()
    request = await _request(db, org, reference="COR-SYNTH-0010b",
                             deadline=datetime(2026, 9, 10, 17, 0))
    await db.commit()

    with pytest.raises(pr.PriorityRefused, match="reason"):
        await pr.amend_deadline(db, uuid.UUID(request["priority_case_id"]),
                                new_deadline=None, reason="fix",
                                actor=SUPERVISOR.email)
    await db.rollback()


# ═══ P11 / P12 — replay and concurrency ══════════════════════════════════════

async def test_the_same_request_submitted_twice_is_one_case(rolled_back_db):
    """STEP 12: a transport retry must never double an analyst's workload."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P11")
    await db.commit()

    first = await _request(db, org, reference="COR-SYNTH-0011")
    await db.commit()
    second = await _request(db, org, reference="COR-SYNTH-0011")
    await db.commit()

    assert second["duplicate_request"] is True
    assert second["priority_case_id"] == first["priority_case_id"]
    assert second["review_id"] == first["review_id"]
    assert int((await db.execute(
        select(func.count()).select_from(reg.ReviewRecord)
        .where(reg.ReviewRecord.verification_results["queue_source"].astext
               == pr.QUEUE_SOURCE))).scalar() or 0) == 1


async def test_a_new_request_for_the_same_organisation_is_allowed(rolled_back_db):
    """STEP 11: the Government may ask about an organisation more than once."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P11b")
    await db.commit()

    first = await _request(db, org, reference="COR-SYNTH-0011-A")
    await db.commit()
    second = await _request(db, org, reference="COR-SYNTH-0011-B")
    await db.commit()

    assert second["duplicate_request"] is False
    assert second["priority_case_id"] != first["priority_case_id"]
    assert second["review_id"] != first["review_id"]


# ═══ P05 / P06 — ambiguity and the unknown organisation ══════════════════════

async def test_an_ambiguous_target_is_routed_to_a_human_with_its_candidates(
        rolled_back_db):
    """STEP 44: never the first match, and the candidates are preserved."""
    db = rolled_back_db
    intake_id = await _intake(db)
    shared = f"{SYN} SHARED NAME"
    a = await _org(db, intake_id, "P05a", name=shared, line=2)
    b = await _org(db, intake_id, "P05b", name=shared, line=3)
    await db.commit()

    request = await pr.receive_request(
        db, cor_reference="COR-SYNTH-0005", target_reference=shared,
        issue_description="The COR named an organisation by name only.",
        requested_by=COR, actor=COR)
    await db.commit()

    assert request["target_resolution"] == "AMBIGUOUS"
    assert request["state"] == pr.NEEDS_TARGET_RESOLUTION
    assert request["review_id"] is None, "no case exists until a human decides"
    assert sorted(request["candidate_entity_ids"]) == sorted(
        [str(a.entity_id), str(b.entity_id)])

    # A human names the entity, WITH a rationale, and only then is there a case.
    resolved = await pr.resolve_target_manually(
        db, uuid.UUID(request["priority_case_id"]), entity_id=a.entity_id,
        rationale="COR confirmed the TEFCAID by email; the other is a namesake.",
        actor=SUPERVISOR.email, actor_id=SUPERVISOR.id)
    await db.commit()
    assert resolved["entity_id"] == str(a.entity_id)
    assert resolved["review_id"].startswith("REV-")
    assert resolved["state"] == "AVAILABLE"


async def test_an_unknown_organisation_is_recorded_not_invented(rolled_back_db):
    """STEP 43: the request is logged; no entity is created to satisfy it."""
    db = rolled_back_db
    await _intake(db)
    await db.commit()
    before = int((await db.execute(
        select(func.count()).select_from(reg.TefcaRegEntity))).scalar() or 0)

    request = await pr.receive_request(
        db, cor_reference="COR-SYNTH-0006",
        target_reference=f"{ARC}.NOT-IN-THE-REGISTRY",
        issue_description="The COR named an organisation AGT cannot find.",
        requested_by=COR, actor=COR)
    await db.commit()

    assert request["target_resolution"] == "NOT_FOUND"
    assert request["state"] == pr.NEEDS_TARGET_RESOLUTION
    assert request["review_id"] is None
    assert request["cor_reference"] == "COR-SYNTH-0006", (
        "the request itself must be logged — the COR asked, and AGT recording "
        "nothing would be the opposite of the procedure")
    after = int((await db.execute(
        select(func.count()).select_from(reg.TefcaRegEntity))).scalar() or 0)
    assert after == before, "an organisation was created to satisfy a request"


async def test_a_target_cannot_be_resolved_to_an_entity_that_does_not_exist(
        rolled_back_db):
    db = rolled_back_db
    await _intake(db)
    await db.commit()
    request = await pr.receive_request(
        db, cor_reference="COR-SYNTH-0006b", target_reference=f"{ARC}.MISSING",
        issue_description="Unresolvable target.", requested_by=COR, actor=COR)
    await db.commit()

    with pytest.raises(pr.PriorityRefused, match="not a registry entity"):
        await pr.resolve_target_manually(
            db, uuid.UUID(request["priority_case_id"]), entity_id=uuid.uuid4(),
            rationale="A synthetic attempt to invent a target.",
            actor=SUPERVISOR.email)
    await db.rollback()


async def test_a_resolved_target_cannot_be_switched_after_review_begins(
        rolled_back_db):
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P05c")
    other = await _org(db, intake_id, "P05d", line=3)
    await db.commit()
    request = await _request(db, org, reference="COR-SYNTH-0005c")
    await db.commit()

    with pytest.raises(pr.PriorityRefused, match="already has a review case"):
        await pr.resolve_target_manually(
            db, uuid.UUID(request["priority_case_id"]), entity_id=other.entity_id,
            rationale="A synthetic attempt to move the target.",
            actor=SUPERVISOR.email)
    await db.rollback()


# ═══ P14 — HELD / unpromoted target ══════════════════════════════════════════

async def test_a_held_unpromoted_organisation_can_still_be_priority_reviewed(
        rolled_back_db):
    """STEP 42: statistical sampling cannot reach it; a COR request can.

    The Government asks about an organisation in ITS delivery, not about the
    subset AGT was able to promote. `review_records.source_record_id` is what
    lets the case be about the delivered line, so nothing has to be promoted to
    make the request answerable.
    """
    db = rolled_back_db
    intake_id = await _intake(db)
    held = await _org(db, intake_id, "P14", promote=False, held=True)
    await db.commit()

    request = await _request(db, held, reference="COR-SYNTH-0014")
    await db.commit()

    assert request["entity_id"] is None
    assert request["source_record_id"] == str(held.source_record_id)
    assert request["pre_promotion"] is True
    assert request["review_id"].startswith("REV-")
    assert request["state"] == "AVAILABLE"

    # And nothing was promoted to achieve it.
    curated = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_record_id == held.source_record_id)
    )).scalars().first()
    assert curated.canonical_entity_id is None
    assert curated.record_status == "HELD"


# ═══ P02 / P04 / P03 — overlaps ══════════════════════════════════════════════

async def test_priority_and_statistical_provenance_both_survive(rolled_back_db):
    """STEP 37: one organisation, two selection authorities, two records of why."""
    from app.tefca_registry import qhin_sampling as qs

    db = rolled_back_db
    qhin_id = await _qhin(db)
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"P04-{i}", line=2 + i, qhin_id=qhin_id)
            for i in range(6)]
    await db.commit()

    plan = await qs.finalize_plan(db, intake_id, seed=404)
    await db.commit()
    sampled = (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
        .limit(1))).scalars().first()
    target = next(o for o in orgs if o.entity_id == sampled.entity_id)

    request = await _request(db, target, reference="COR-SYNTH-0004")
    await db.commit()

    # The sample membership is untouched, and the priority case is its own case.
    assert sampled.entity_id == target.entity_id
    assert request["review_id"] is not None
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == request["review_id"]))).scalars().first()
    assert record.verification_results["selection_reason"] == "PRIORITY_REQUEST"
    assert record.verification_results.get("sample_id") is None, (
        "a COR request must never be presented as a statistical selection")
    still = (await db.execute(
        select(func.count()).select_from(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == uuid.UUID(plan["sample_id"]))
    )).scalar()
    assert still == plan["membership_count"]


async def test_a_human_required_finding_and_a_priority_request_coexist(
        rolled_back_db):
    """STEP 38: two reasons, two cases, neither reason disappears."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P03")
    await db.commit()

    # A DQ exception case for the same subject, made the way the bridge makes it.
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id="REV-9000-000001", entity_id=org.entity_id,
        source_record_id=org.source_record_id,
        verification_results={"queue_source": "RCE_DQ_HUMAN_REQUIRED",
                              "case_classification": "IDENTITY"}))
    await db.commit()

    request = await _request(db, org, reference="COR-SYNTH-0003")
    await db.commit()

    rows = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.entity_id == org.entity_id))).scalars().all()
    sources = sorted((r.verification_results or {}).get("queue_source") for r in rows)
    assert sources == ["RCE_DQ_HUMAN_REQUIRED", "TEFCA_ARC_PRIORITY"]
    assert request["review_id"] != "REV-9000-000001"


async def test_prior_review_history_is_context_not_a_current_answer(
        rolled_back_db):
    """STEP 39: a previous approval does not answer a new Government question."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P39")
    await db.commit()
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id="REV-9000-000002", entity_id=org.entity_id,
        reportable_at=datetime(2026, 4, 1, 9, 0),
        verification_results={"queue_source": "RCE_DQ_HUMAN_REQUIRED"}))
    await db.commit()

    request = await _request(db, org, reference="COR-SYNTH-0039")
    await db.commit()

    package = await pr.analyst_package(db, uuid.UUID(request["priority_case_id"]))
    prior = [p for p in package["prior_reviews"] if p["review_id"] == "REV-9000-000002"]
    assert prior and prior[0]["reportable_at"] is not None
    assert package["request"]["reportable"] is False, (
        "an earlier approval must not make this request reportable")
    assert "own determination" in package["note"]


# ═══ P13 — a target absent from the current delivery ═════════════════════════

async def test_a_target_not_in_the_current_delivery_is_not_refused(rolled_back_db):
    """STEP 41: absence from a file is not grounds to reject a COR request."""
    db = rolled_back_db
    old_intake = await _intake(db, label=f"{SYN}-N")
    org = await _org(db, old_intake, "P13")
    await _intake(db, label=f"{SYN}-N1")     # a later delivery without it
    await db.commit()

    request = await _request(db, org, reference="COR-SYNTH-0013")
    await db.commit()

    assert request["target_resolution"] == "RESOLVED"
    assert request["entity_id"] == str(org.entity_id)
    assert request["review_id"].startswith("REV-")


# ═══ STEPS 20/24/27/28/29/32 — assignment, analyst, QA, reportability ════════

async def _claimed_request(db, tag, *, reference, deadline=None, user=ANALYST):
    from app.tefca_registry import case_assignment as assignment

    intake_id = await _intake(db)
    org = await _org(db, intake_id, tag)
    await db.commit()
    request = await _request(db, org, reference=reference, deadline=deadline)
    await db.commit()
    await assignment.claim(db, request["review_id"], user=user)
    await db.commit()
    return request


async def test_the_analyst_package_is_a_coherent_case(rolled_back_db):
    """STEP 24: request context, target, QHIN, delivered record, history."""
    db = rolled_back_db
    qhin_id = await _qhin(db)
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P24", qhin_id=qhin_id)
    await db.commit()
    request = await _request(db, org, reference="COR-SYNTH-0024",
                             deadline=datetime(2026, 12, 1, 17, 0),
                             instructions="Confirm the registered address.")
    await db.commit()

    package = await pr.analyst_package(db, uuid.UUID(request["priority_case_id"]))
    assert package["request"]["cor_reference"] == "COR-SYNTH-0024"
    assert package["request"]["instructions"] == "Confirm the registered address."
    assert package["entity"]["entity_id"] == str(org.entity_id)
    assert package["qhin"]["entity_id"] == str(qhin_id)
    assert package["delivered_record"]["record_status"] == "CLEAN"
    assert package["decision_history"] == []


async def test_a_finding_requires_a_determination_and_is_not_reportable(
        rolled_back_db):
    """STEPS 27/28/32: the analyst records content AND an event, never a result."""
    db = rolled_back_db
    request = await _claimed_request(db, "P27", reference="COR-SYNTH-0027")

    outcome = await pr.record_finding(
        db, uuid.UUID(request["priority_case_id"]), user=ANALYST,
        root_cause_determination="ADDRESS_STATE_CONFLICT",
        root_cause_description="The delivered state disagrees with the source.",
        severity="MEDIUM",
        recommendations=[{"recommendation": "QHIN to correct and resubmit."}],
        prevention_recommendation="Add a pre-submission address check.",
        resolution_notes="Referred to the QHIN.",
        rationale="Synthetic analyst determination for a priority review.")
    await db.commit()

    assert outcome["reportable"] is False
    events = (await db.execute(
        select(reg.ReviewDecisionEvent)
        .where(reg.ReviewDecisionEvent.review_id == request["review_id"])
    )).scalars().all()
    assert [e.event_type for e in events] == ["ANALYST_DETERMINATION"]

    result = await pr.reportable_result(db, uuid.UUID(request["priority_case_id"]))
    assert result["reportable"] is False
    assert result["root_cause_determination"] is None, (
        "an un-approved determination must not be readable as reported content")
    assert result["severity"] is None and result["recommendations"] is None
    assert "No standing QA approval" in result["withheld_reason"]
    # The identified issue is the COR's own words and is not withheld.
    assert result["identified_issue"]


async def test_only_an_independent_qa_approval_releases_the_result(rolled_back_db):
    """STEP 29/32: the five ¶147 elements appear only after QA approves."""
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P29", reference="COR-SYNTH-0029")
    case_id = uuid.UUID(request["priority_case_id"])
    await pr.record_finding(
        db, case_id, user=ANALYST, root_cause_determination="NPI_MISMATCH",
        root_cause_description="The delivered NPI does not match NPPES.",
        severity="HIGH",
        recommendations=[{"recommendation": "QHIN to verify the NPI."}],
        rationale="Synthetic analyst determination before QA.")
    await db.commit()

    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic independent QA approval.")
    await db.commit()

    result = await pr.reportable_result(db, case_id)
    assert result["reportable"] is True
    assert result["reportable_at"] is not None
    assert result["root_cause_determination"] == "NPI_MISMATCH"
    assert result["severity"] == "HIGH"
    assert result["recommendations"] == [{"recommendation": "QHIN to verify the NPI."}]
    assert result["withheld_reason"] is None


async def test_an_analyst_cannot_qa_their_own_priority_review(rolled_back_db):
    """STEP 29: priority does not suspend segregation of duties."""
    from app.tefca_registry.qa_gate import QaGateRefused, submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P29b", reference="COR-SYNTH-0029b")
    await pr.record_finding(
        db, uuid.UUID(request["priority_case_id"]), user=ANALYST,
        root_cause_determination=None, root_cause_description=None,
        severity="LOW", recommendations=[],
        rationale="Synthetic determination before a self-approval attempt.")
    await db.commit()

    with pytest.raises(QaGateRefused, match="segregation of duties"):
        await submit_qa_review(db, request["review_id"], user=ANALYST,
                               qa_action="APPROVE",
                               qa_reason="Synthetic self-approval attempt.")
    await db.rollback()


async def test_root_cause_not_determined_is_a_legitimate_outcome(rolled_back_db):
    """¶147 asks for root cause IF DETERMINED. A blank beats a manufactured one."""
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P27b", reference="COR-SYNTH-0027b")
    case_id = uuid.UUID(request["priority_case_id"])
    await pr.record_finding(
        db, case_id, user=ANALYST, root_cause_determination=None,
        root_cause_description="Two sources disagree and neither is decisive.",
        severity="MEDIUM", recommendations=[],
        rationale="Synthetic determination with no root cause established.")
    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic QA approval of an open root cause.")
    await db.commit()

    result = await pr.reportable_result(db, case_id)
    assert result["reportable"] is True
    assert result["root_cause_determination"] is None
    assert "neither is decisive" in result["root_cause_description"]


# ═══ P08 / P09 — QA return and escalation ════════════════════════════════════

async def test_a_qa_return_keeps_the_same_request_and_its_evidence(rolled_back_db):
    """STEP 30/57: returned, revised, approved — one case throughout."""
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    deadline = datetime(2026, 11, 1, 17, 0)
    request = await _claimed_request(db, "P08", reference="COR-SYNTH-0008",
                                     deadline=deadline)
    case_id = uuid.UUID(request["priority_case_id"])
    await pr.record_finding(
        db, case_id, user=ANALYST, root_cause_determination="NAME_MISMATCH",
        root_cause_description="First pass.", severity="LOW",
        recommendations=[], rationale="Synthetic first determination.")
    await submit_qa_review(db, request["review_id"], user=QA, qa_action="RETURN",
                           qa_reason="Synthetic QA return: rationale too thin.")
    await db.commit()

    assert (await pr.reportable_result(db, case_id))["reportable"] is False

    await pr.record_finding(
        db, case_id, user=ANALYST, root_cause_determination="NAME_MISMATCH",
        root_cause_description="Second pass, with the registry extract.",
        severity="LOW", recommendations=[{"recommendation": "QHIN to correct."}],
        rationale="Synthetic revised determination after the QA return.")
    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic QA approval of the revision.")
    await db.commit()

    after = await pr.get_request(db, case_id)
    assert after["review_id"] == request["review_id"], "a replacement case was made"
    assert after["cor_reference"] == "COR-SYNTH-0008"
    assert after["deadline"] == deadline.isoformat()
    assert after["reportable"] is True
    # Every act is still on the record; nothing was erased by the revision.
    events = (await db.execute(
        select(reg.ReviewDecisionEvent)
        .where(reg.ReviewDecisionEvent.review_id == request["review_id"])
        .order_by(reg.ReviewDecisionEvent.sequence_number))).scalars().all()
    assert [e.event_type for e in events] == [
        "ANALYST_DETERMINATION", "QA_REVIEW", "ANALYST_DETERMINATION", "QA_REVIEW"]


async def test_a_qa_escalation_invents_no_disposition(rolled_back_db):
    """STEP 31/58: preserved, and explicitly not resolved."""
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P09", reference="COR-SYNTH-0009")
    case_id = uuid.UUID(request["priority_case_id"])
    await pr.record_finding(
        db, case_id, user=ANALYST, root_cause_determination="LEIE_ACTIVE_EXCLUSION",
        root_cause_description="A possible exclusion match.", severity="CRITICAL",
        recommendations=[], rationale="Synthetic determination before escalation.")
    await submit_qa_review(
        db, request["review_id"], user=QA, qa_action="ESCALATE",
        qa_reason="Synthetic escalation to a senior reviewer.",
        escalated_to_user_id=SUPERVISOR.id,
        escalation_reason="Synthetic escalation reason for a critical finding.")
    await db.commit()

    result = await pr.reportable_result(db, case_id)
    assert result["reportable"] is False
    assert result["severity"] is None, "an escalated case has no released content"
    state = await pr.request_state(db, case_id)
    assert state == "ESCALATED"
    assert (await pr.get_request(db, case_id))["cor_reference"] == "COR-SYNTH-0009"


async def test_reported_content_cannot_be_edited_after_approval(rolled_back_db):
    """STEP 36: a correction is a new determination, never an overwrite."""
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P36", reference="COR-SYNTH-0036")
    case_id = uuid.UUID(request["priority_case_id"])
    await pr.record_finding(
        db, case_id, user=ANALYST, root_cause_determination="NPI_MISMATCH",
        root_cause_description="Original.", severity="HIGH", recommendations=[],
        rationale="Synthetic determination that will be approved.")
    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic approval before an edit attempt.")
    await db.commit()

    with pytest.raises(pr.PriorityRefused, match="standing QA approval"):
        await pr.record_finding(
            db, case_id, user=ANALYST, root_cause_determination="NAME_MISMATCH",
            root_cause_description="Quietly changed.", severity="LOW",
            recommendations=[], rationale="Synthetic post-approval edit attempt.")
    await db.rollback()
    assert (await pr.reportable_result(db, case_id))["root_cause_determination"] \
        == "NPI_MISMATCH"


# ═══ STEP 49 — one analyst's case is not another's ═══════════════════════════

async def test_an_analyst_cannot_record_a_finding_on_someone_elses_case(
        rolled_back_db):
    from app.tefca_registry.case_assignment import AssignmentRefused

    db = rolled_back_db
    request = await _claimed_request(db, "P49", reference="COR-SYNTH-0049",
                                     user=ANALYST)
    with pytest.raises(AssignmentRefused):
        await pr.record_finding(
            db, uuid.UUID(request["priority_case_id"]), user=ANALYST_B,
            root_cause_determination=None, root_cause_description=None,
            severity="LOW", recommendations=[],
            rationale="Synthetic attempt on another analyst's case.")
    await db.rollback()


# ═══ P15 — a multi-entity request ════════════════════════════════════════════

async def test_one_request_may_name_several_organisations(rolled_back_db):
    """STEP 7: one COR reference, one case per named organisation."""
    db = rolled_back_db
    intake_id = await _intake(db)
    orgs = [await _org(db, intake_id, f"P15-{i}", line=2 + i) for i in range(3)]
    await db.commit()

    made = [await _request(db, org, reference="COR-SYNTH-0015") for org in orgs]
    await db.commit()

    assert len({r["priority_case_id"] for r in made}) == 3
    assert len({r["review_id"] for r in made}) == 3
    assert {r["cor_reference"] for r in made} == {"COR-SYNTH-0015"}
    # And re-submitting the whole request creates nothing further.
    again = [await _request(db, org, reference="COR-SYNTH-0015") for org in orgs]
    await db.commit()
    assert all(r["duplicate_request"] for r in again)


# ═══ STEP 15 — withdrawal ════════════════════════════════════════════════════

async def test_a_withdrawn_request_is_preserved_never_deleted(rolled_back_db):
    db = rolled_back_db
    request = await _claimed_request(db, "P15w", reference="COR-SYNTH-0015W")
    case_id = uuid.UUID(request["priority_case_id"])

    after = await pr.withdraw_request(
        db, case_id, reason="COR withdrew the request by email of 2026-09-06.",
        actor=SUPERVISOR.email, actor_id=SUPERVISOR.id)
    await db.commit()

    assert after["state"] == pr.WITHDRAWN
    assert after["withdrawn_at"] is not None
    assert after["review_id"] == request["review_id"], "the case still exists"
    with pytest.raises(pr.PriorityRefused, match="withdrawn"):
        await pr.record_finding(
            db, case_id, user=ANALYST, root_cause_determination=None,
            root_cause_description=None, severity="LOW", recommendations=[],
            rationale="Synthetic finding on a withdrawn request.")
    await db.rollback()


# ═══ STEP 47 — audit reconstruction ══════════════════════════════════════════

async def test_the_whole_request_can_be_reconstructed_from_the_audit_trail(
        rolled_back_db):
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P47", reference="COR-SYNTH-0047",
                                     deadline=datetime(2026, 10, 1, 17, 0))
    case_id = uuid.UUID(request["priority_case_id"])
    await pr.amend_deadline(db, case_id, new_deadline=datetime(2026, 10, 8, 17, 0),
                            reason="COR extended the deadline in writing.",
                            actor=SUPERVISOR.email, actor_id=SUPERVISOR.id)
    await pr.record_finding(
        db, case_id, user=ANALYST, root_cause_determination="PECOS_ENROLLMENT_DISCREPANCY",
        root_cause_description="Enrollment status differs.", severity="MEDIUM",
        recommendations=[{"recommendation": "QHIN to reconcile."}],
        rationale="Synthetic determination for audit reconstruction.")
    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic approval for audit reconstruction.")
    await db.commit()

    rows = (await db.execute(select(reg.TefcaRegAuditLog))).scalars().all()
    mine = [r for r in rows
            if (r.metadata_ or {}).get("priority_case_id") == str(case_id)]
    actions = {r.action for r in mine}
    assert {pr.ACT_RECEIVED, pr.ACT_DEADLINE_AMENDED,
            pr.ACT_CONTENT_RECORDED} <= actions

    receipt = next(r for r in mine if r.action == pr.ACT_RECEIVED).metadata_
    assert receipt["cor_reference"] == "COR-SYNTH-0047"
    assert receipt["requested_by"] == COR
    assert receipt["deadline_stated"] is True
    assert receipt["contract_authority"].startswith("SOW Task 5")

    # The QA and assignment acts are on the same trail, keyed by review id.
    by_review = [r for r in rows
                 if (r.metadata_ or {}).get("review_id") == request["review_id"]]
    assert any(r.action.startswith("qa_") for r in by_review)
    assert any("claim" in r.action for r in by_review)


# ═══ STEP 60 — the contract's monthly volume, plus surge ═════════════════════

async def test_the_expected_monthly_volume_and_a_surge_are_handled(rolled_back_db):
    """~20 a month with capability to exceed — a capacity figure, not a cap."""
    db = rolled_back_db
    intake_id = await _intake(db)
    volume = 24                       # 20 anticipated + 20% surge
    orgs = [await _org(db, intake_id, f"P60-{i}", line=2 + i) for i in range(volume)]
    await db.commit()

    for i, org in enumerate(orgs):
        await _request(db, org, reference=f"COR-SYNTH-VOL-{i:03d}",
                       deadline=datetime(2026, 12, 1, 17, 0))
    await db.commit()

    queue = await pr.open_requests(db, limit=100)
    assert len(queue) == volume
    assert len({q["review_id"] for q in queue}) == volume, "duplicate work created"

    summary = await pr.workload_summary(db, now=datetime(2026, 11, 30, 12, 0))
    assert summary["total_requests"] == volume
    assert summary["by_state"] == {"AVAILABLE": volume}
    assert summary["by_deadline_status"] == {pr.ON_TRACK: volume}
    assert summary["reportable"] == 0
    assert "not a cap" in summary["note"]


# ═══ STEP 13 — concurrent submission of one request ══════════════════════════

SCHEMA = "priority_gate_tmp"


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
               "tefca_reg_entities", "tefca_entity_identifiers",
               "tefca_entity_relationships", "review_samples", "sample_entities",
               "review_records", "review_decision_events", "tefca_reg_audit_log",
               "tefca_entities", "tefca_review_cycles", "tefca_evidence_records",
               "tefca_priority_cases")]
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


async def test_two_concurrent_submissions_produce_one_case(sandbox_engine):
    """STEP 13: the race a transport retry actually causes."""
    async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
        intake_id = await _intake(db)
        org = await _org(db, intake_id, "P12")
        await db.commit()
        oid = org.oid

    async def submit():
        async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
            try:
                result = await pr.receive_request(
                    db, cor_reference="COR-SYNTH-0012", target_reference=oid,
                    issue_description="Concurrent synthetic submission.",
                    requested_by=COR, actor=COR)
                await db.commit()
                return "OK", result["priority_case_id"], result["duplicate_request"]
            except Exception as exc:                          # noqa: BLE001
                await db.rollback()
                return f"RAISED {type(exc).__name__}", str(exc)[:120], None

    results = await asyncio.gather(submit(), submit())
    assert all(r[0] == "OK" for r in results), results
    assert results[0][1] == results[1][1], "two cases for one request"
    assert sorted(r[2] for r in results) == [False, True]

    async with AsyncSession(sandbox_engine) as db:
        cases = int((await db.execute(text(
            "select count(*) from tefca_priority_cases"))).scalar() or 0)
        reviews = int((await db.execute(
            select(func.count()).select_from(reg.ReviewRecord))).scalar() or 0)
    assert cases == 1 and reviews == 1


# ═══ STEPS 48/50/53 — the HTTP surface ═══════════════════════════════════════

PRIORITY_ENDPOINTS = [
    ("POST", "/api/tefca/arc/priority-requests"),
    ("GET", "/api/tefca/arc/priority-requests"),
    ("GET", "/api/tefca/arc/priority-requests/workload"),
    ("GET", f"/api/tefca/arc/priority-requests/{uuid.uuid4()}"),
    ("GET", f"/api/tefca/arc/priority-requests/{uuid.uuid4()}/package"),
    ("GET", f"/api/tefca/arc/priority-requests/{uuid.uuid4()}/result"),
    ("POST", f"/api/tefca/arc/priority-requests/{uuid.uuid4()}/finding"),
    ("POST", f"/api/tefca/arc/priority-requests/{uuid.uuid4()}/deadline"),
    ("POST", f"/api/tefca/arc/priority-requests/{uuid.uuid4()}/withdraw"),
    ("POST", f"/api/tefca/arc/priority-requests/{uuid.uuid4()}/resolve-target"),
    ("GET", "/api/tefca/arc/available-cases"),
    ("GET", "/api/tefca/arc/my-work"),
    ("POST", "/api/tefca/arc/reviews/REV-2026-000001/claim"),
]


@pytest.mark.parametrize("method,path", PRIORITY_ENDPOINTS)
def test_every_priority_endpoint_requires_authentication(client, method, path):
    response = client.request(method, path, json={})
    assert response.status_code in GATED, (
        f"{method} {path} answered {response.status_code} unauthenticated")


def test_logging_a_government_request_needs_more_than_a_signed_in_user(
        client, auth_headers):
    """STEP 48: a default account cannot log a COR request."""
    if not auth_headers:
        pytest.skip("no authenticated test account available")
    response = client.post(
        "/api/tefca/arc/priority-requests", headers=auth_headers,
        json={"cor_reference": "COR-SYNTH-RBAC", "target_reference": f"{ARC}.RBAC",
              "issue_description": "A synthetic request.", "requested_by": COR})
    assert response.status_code in GATED


#: The authority each act requires, from `app.core.security.ROLE_HIERARCHY`.
#: Asserted against the routes themselves so the ladder is verifiable without a
#: seeded account for every role — which this environment does not have.
EXPECTED_ROLES = {
    ("POST", "/api/tefca/arc/priority-requests"): "program_manager",
    ("POST", "/api/tefca/arc/priority-requests/{case_id}/deadline"): "program_manager",
    ("POST", "/api/tefca/arc/priority-requests/{case_id}/withdraw"): "program_manager",
    ("POST", "/api/tefca/arc/priority-requests/{case_id}/resolve-target"): "senior_analyst",
    ("POST", "/api/tefca/arc/priority-requests/{case_id}/finding"): "reviewer",
    # Reads sit at the viewer floor, like every other TEFCA read
    # (`test_rbac_roles::test_no_tefca_read_endpoint_sits_above_the_viewer_floor`).
    ("GET", "/api/tefca/arc/priority-requests/{case_id}/package"): "viewer",
    ("GET", "/api/tefca/arc/available-cases"): "viewer",
    ("GET", "/api/tefca/arc/my-work"): "viewer",
    ("GET", "/api/tefca/arc/priority-requests"): "viewer",
    ("GET", "/api/tefca/arc/priority-requests/{case_id}"): "viewer",
    ("GET", "/api/tefca/arc/priority-requests/{case_id}/result"): "viewer",
    ("GET", "/api/tefca/arc/priority-requests/workload"): "viewer",
    ("POST", "/api/tefca/arc/reviews/{review_id}/claim"): "reviewer",
    ("POST", "/api/tefca/arc/reviews/{review_id}/release"): "reviewer",
    ("POST", "/api/tefca/arc/reviews/{review_id}/assign"): "senior_analyst",
}


def test_each_priority_act_requires_the_authority_it_should():
    """STEP 48: logging a Government request is not an analyst's act.

    Read off the routes themselves. This environment has no seeded account per
    role, and a suite that skips every role check would report a gate it never
    exercised — asserting the declared dependency is the honest alternative.
    """
    from app.core.security import ROLE_HIERARCHY
    from app.tefca_registry.review_routes import router

    seen = {}
    for route in router.routes:
        for dependency in getattr(route, "dependencies", []):
            closure = getattr(dependency.dependency, "__closure__", None) or ()
            for cell in closure:
                if isinstance(cell.cell_contents, str) and                         cell.cell_contents in ROLE_HIERARCHY:
                    for method in route.methods:
                        seen[(method, route.path)] = cell.cell_contents

    missing = set(EXPECTED_ROLES) - set(seen)
    assert not missing, f"these endpoints declare no role gate: {sorted(missing)}"
    wrong = {k: (seen[k], v) for k, v in EXPECTED_ROLES.items() if seen[k] != v}
    assert not wrong, f"role mismatches (actual, expected): {wrong}"


def test_no_protected_field_is_settable_by_a_client():
    """STEP 50: mass assignment, checked against the actual request models."""
    from app.tefca_registry import review_routes as rr

    protected = {"reportable", "reportable_at", "completed_at", "created_by",
                 "assigned_reviewer_id", "assigned_to_user_id", "case_status",
                 "actor", "actor_id", "qa_action", "review_id",
                 "priority_case_id", "entity_id", "source_record_id"}
    for model in (rr.PriorityRequestCreate, rr.PriorityDeadlineAmend,
                  rr.PriorityWithdraw, rr.PriorityFinding):
        fields = set(model.model_fields)
        leaked = fields & protected
        assert not leaked, f"{model.__name__} exposes {leaked}"

    # The one place an entity id is accepted is the deliberate human resolution
    # of an ambiguous target, which is attributable and requires a rationale.
    assert set(rr.PriorityTargetResolve.model_fields) == {"entity_id", "rationale"}


def test_the_request_model_offers_no_deadline_default():
    """STEP 34: nothing in the transport layer can supply a turnaround."""
    from app.tefca_registry import review_routes as rr

    assert rr.PriorityRequestCreate.model_fields["deadline"].default is None
    source = inspect.getsource(rr.create_priority_request)
    assert "timedelta" not in source and "24" not in source


def test_the_priority_surface_never_borrows_the_sampled_review_sla():
    """The 3-day `sla.py` window is a display policy for SAMPLED reviews."""
    from app.tefca_registry import sla

    assert sla.REVIEW_SLA_DAYS.get("priority") == 3, (
        "if this changed, re-check what still depends on it")
    code = _code_of(pr)
    for borrowed in ("tefca_registry.sla", "import sla", "REVIEW_SLA_DAYS",
                     "sla_status", "due_date_for", "sla.describe"):
        assert borrowed not in code, (
            f"{borrowed!r}: the Task 5 deadline comes from the COR, never "
            f"from a review cadence")


async def test_a_second_qa_approval_cannot_stand_beside_the_first(rolled_back_db):
    """STEP 52: two QA actors cannot produce contradictory final states.

    The second approval is refused rather than appended, so there is never a
    moment where the case has two standing approvals from two people and no way
    to say which released the result.
    """
    from app.tefca_registry.qa_gate import QaGateRefused, submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P52", reference="COR-SYNTH-0052")
    await pr.record_finding(
        db, uuid.UUID(request["priority_case_id"]), user=ANALYST,
        root_cause_determination=None, root_cause_description=None,
        severity="LOW", recommendations=[],
        rationale="Synthetic determination before two QA attempts.")
    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic first QA approval.")
    await db.commit()

    second = principal("qa2@synthetic.test", "qalead")
    with pytest.raises(QaGateRefused, match="standing APPROVE"):
        await submit_qa_review(db, request["review_id"], user=second,
                               qa_action="RETURN",
                               qa_reason="Synthetic contradictory QA action.")
    await db.rollback()
    approvals = (await db.execute(
        select(reg.ReviewDecisionEvent)
        .where(reg.ReviewDecisionEvent.review_id == request["review_id"],
               reg.ReviewDecisionEvent.event_type == "QA_REVIEW"))).scalars().all()
    assert len(approvals) == 1


async def test_generating_the_report_twice_creates_nothing(rolled_back_db):
    """STEP 53: a read is a read. Retrying it must not mint a business object."""
    from app.reports.data.sow_report_data import SowReportDataService
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P53", reference="COR-SYNTH-0053")
    case_id = request["priority_case_id"]
    await pr.record_finding(
        db, uuid.UUID(case_id), user=ANALYST,
        root_cause_determination="NAME_MISMATCH", root_cause_description="x" * 20,
        severity="LOW", recommendations=[],
        rationale="Synthetic determination before repeated report reads.")
    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic approval before repeated reads.")
    await db.commit()

    service = SowReportDataService(db=db)
    first = await service.priority_status(case_id=case_id)
    second = await service.priority_status(case_id=case_id)
    assert first["case"] == second["case"]
    events = int((await db.execute(
        select(func.count()).select_from(reg.ReviewDecisionEvent)
        .where(reg.ReviewDecisionEvent.review_id == request["review_id"])
    )).scalar() or 0)
    assert events == 2, "reading a report created a decision event"


# ═══ P07 / STEPS 16-18/45 — evidence, and the two frozen boundaries ══════════

async def test_an_unavailable_source_stays_unavailable_under_a_deadline(
        rolled_back_db):
    """STEP 17/45: priority does not convert an outage into an answer."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P07")
    for source, status in (("SAM", "unavailable"), ("NPPES", "verified"),
                           ("LEIE", "not_found")):
        db.add(reg.TefcaVerification(
            id=uuid.uuid4(), entity_id=org.entity_id, source=source,
            verification_status=status, data_source_label=f"{SYN} {source}",
            verified_at=datetime(2026, 9, 1, 9, 0)))
    await db.commit()

    request = await _request(db, org, reference="COR-SYNTH-0007",
                             deadline=datetime(2026, 9, 1, 10, 0))
    await db.commit()

    package = await pr.analyst_package(db, uuid.UUID(request["priority_case_id"]))
    by_source = {v["source"]: v["verification_status"]
                 for v in package["verifications"]}
    assert by_source["SAM"] == "unavailable"
    assert by_source["SAM"] not in ("verified", "not_found", "PASS", "CLEAR")
    limitation = next(l for l in package["source_limitations"]
                      if l["source"] == "SAM")
    assert "not evidence" in limitation["meaning"]
    # An expired deadline changes nothing about what the source said.
    assert pr.deadline_status(datetime(2026, 9, 1, 10, 0),
                              now=datetime(2026, 9, 2))["status"] == pr.PAST_DUE
    assert by_source["SAM"] == "unavailable"


async def test_taxpayer_identity_stays_behind_the_government_boundary(
        rolled_back_db):
    """STEP 18: no authorized IRS mechanism means no PASS, and no NPI inference."""
    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "P18")
    await db.commit()
    request = await _request(db, org, reference="COR-SYNTH-0018",
                             issue="The COR asked AGT to confirm the TIN.")
    await db.commit()

    package = await pr.analyst_package(db, uuid.UUID(request["priority_case_id"]))
    restricted = {r["identifier"]: r
                  for r in package["government_restricted_identifiers"]}
    assert set(restricted) == {"TIN", "EIN", "FEIN"}
    assert all(r["state"] == "PENDING_GOVERNMENT_VERIFICATION"
               for r in restricted.values())


def test_the_priority_service_collects_no_evidence_of_its_own():
    """STEP 19/46: automation prepares; it does not decide, and does not retry."""
    source = _code_of(pr)
    for forbidden in ("httpx", "import requests", "aiohttp",
                      "SourceConnectorManager", "run_entity_review",
                      "connectors", "retry", "sleep"):
        assert forbidden not in source, (
            f"{forbidden!r}: the priority path reads recorded evidence and "
            f"neither collects it nor drives an authoritative source itself")


def test_no_determination_is_computed_from_the_issue_text():
    """The legacy `execute_priority_review` did exactly this. This path cannot."""
    source = _code_of(pr)
    for forbidden in ("severity_from_issue", "root_cause_from_issue",
                      "_BUCKET_SEVERITY", "classify"):
        assert forbidden not in source
    params = inspect.signature(pr.record_finding).parameters
    assert "user" in params
    assert params["rationale"].default is inspect.Parameter.empty


# ═══ STEPS 33-35 — the D5.1 report ═══════════════════════════════════════════

async def test_the_d51_report_withholds_content_until_qa_approves(rolled_back_db):
    """STEP 33/35: the report is the last place the gate can still be applied."""
    from app.reports.data.sow_report_data import SowReportDataService
    from app.tefca_registry.qa_gate import submit_qa_review

    db = rolled_back_db
    request = await _claimed_request(db, "P33", reference="COR-SYNTH-0033",
                                     deadline=datetime(2026, 12, 1, 17, 0))
    case_id = request["priority_case_id"]
    await pr.record_finding(
        db, uuid.UUID(case_id), user=ANALYST,
        root_cause_determination="ADDRESS_STATE_CONFLICT",
        root_cause_description="The delivered state disagrees with the source.",
        severity="MEDIUM",
        recommendations=[{"recommendation": "QHIN to correct and resubmit."}],
        prevention_recommendation="Add a pre-submission address check.",
        resolution_notes="Referred to the QHIN.",
        rationale="Synthetic determination for the D5.1 report test.")
    await db.commit()

    service = SowReportDataService(db=db)
    before = await service.priority_status(case_id=case_id)
    assert before["case"]["reportable"] is False
    assert before["case"]["root_cause"] is None
    assert before["case"]["severity"] is None
    assert before["case"]["recommendations"] is None
    assert "QA-approved" in before["release_gate"]
    # The five elements are still named, in order, whatever the gate says.
    assert before["required_content"][0] == "The identified issue"
    assert before["required_content"][-1] == "Resolution"
    assert "no fixed contractual SLA" in before["turnaround"]["basis"]

    await submit_qa_review(db, request["review_id"], user=QA, qa_action="APPROVE",
                           qa_reason="Synthetic QA approval for the D5.1 report.")
    await db.commit()

    after = await service.priority_status(case_id=case_id)
    assert after["case"]["reportable"] is True
    assert after["case"]["root_cause"] == "ADDRESS_STATE_CONFLICT"
    assert after["case"]["severity"] == "MEDIUM"
    assert after["case"]["resolution"] == "Referred to the QHIN."
    assert after["case"]["cor_reference"] == "COR-SYNTH-0033"
    assert after["case"]["review_id"] == request["review_id"]


async def test_the_d51_report_states_the_cor_deadline_and_draws_no_conclusion(
        rolled_back_db):
    """STEP 34: no fixed-SLA claim — the deadline reported is the COR's own."""
    from app.reports.data.sow_report_data import SowReportDataService

    db = rolled_back_db
    d1 = datetime(2026, 10, 1, 17, 0)
    d2 = datetime(2026, 10, 8, 17, 0)
    request = await _claimed_request(db, "P34", reference="COR-SYNTH-0034",
                                     deadline=d1)
    await pr.amend_deadline(db, uuid.UUID(request["priority_case_id"]),
                            new_deadline=d2,
                            reason="COR extended the deadline in writing.",
                            actor=SUPERVISOR.email)
    await db.commit()

    data = await SowReportDataService(db=db).priority_status(
        case_id=request["priority_case_id"])
    assert data["case"]["deadline"] == d2.isoformat()
    assert data["case"]["original_deadline"] == d1.isoformat()
    assert data["case"]["deadline_amendments"] == 1
    assert data["case"]["compliance_conclusion"] is None
    assert "SLA met" not in str(data)


async def test_the_report_family_still_works_without_a_case(rolled_back_db):
    """A family envelope has always been valid on its own and stays so."""
    from app.reports.data.sow_report_data import SowReportDataService

    data = await SowReportDataService(db=rolled_back_db).priority_status(
        case_id=None)
    assert data["case"] is None
    assert data["family"] == "D5.1_PRIORITY_STATUS"


def test_fixtures_are_synthetic_only():
    for actor in (ANALYST, ANALYST_B, QA, SUPERVISOR):
        assert actor.email.endswith("@synthetic.test")
    assert COR.endswith("@synthetic.test")
    assert ARC.startswith("9.99.")
