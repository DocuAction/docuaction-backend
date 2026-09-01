"""HUMAN_REQUIRED DQ issues become review cases, then analyst work, then QA.

WHAT THIS PROVES
────────────────
    current quality run
      -> HUMAN_REQUIRED issues        rce_issues
      -> review case                  review_records          (dq_review_bridge)
      -> analyst determination        review_decision_events  (qa_gate)
      -> independent QA               review_decision_events  (qa_gate)
      -> reportability                review_records.reportable_at

    No new case table. `review_records` + `review_decision_events` already own
    every human act on a determination; the bridge is simply a second caller of
    the pattern `app.Tefca.exception_queue.create_work_item` established.

THE CASE BOUNDARY, AND WHY
──────────────────────────
    One case = (current run, source record, case classification).

    Measured on the delivered population: 134 source records carry
    HUMAN_REQUIRED findings — 130 with one, 4 with two — and all 4 of those
    pairs combine findings from DIFFERENT classes. A `review_record` carries
    ONE determination and ONE QA decision, so a per-source-record boundary
    would force a single answer onto two materially different questions, while
    a per-issue boundary would fragment two address findings on one record into
    two queues.

WHAT IS DELIBERATELY NOT TESTED HERE
────────────────────────────────────
    Assignment, claim and release. `review_records` has no ownership column,
    and the only writable home for it — `verification_results` — is documented
    as "a SNAPSHOT taken at review time, not a pointer to live state", which is
    exactly what a report cites. Putting mutable ownership there would let a
    cited snapshot change under a finished report. That needs a migration and
    is held for approval, so nothing here pretends claim exists.

GOVERNMENT DATA
    Every test runs inside an OUTER transaction that is rolled back, with the
    session joined via `join_transaction_mode="create_savepoint"`. Fixtures are
    synthetic: OIDs under an unassigned `9.99.555` arc, prefixed names.
    Identities are synthetic `SimpleNamespace` principals, never real accounts.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry import models as reg
from app.tefca_registry.rce import dq_review_bridge as bridge
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint
from app.tefca_registry.rce.quality_engine import run_quality_engine

SYN = "SYNTHETIC-HRW"


# ── synthetic principals ─────────────────────────────────────────────────────

def principal(email, role):
    """A synthetic actor. Never a real account, never a stored credential."""
    return SimpleNamespace(id=uuid.uuid4(), email=email, role=role)


ANALYST = principal("analyst@synthetic.test", "reviewer")
ANALYST_2 = principal("analyst2@synthetic.test", "reviewer")
QA = principal("qa@synthetic.test", "qalead")
PM = principal("pm@synthetic.test", "program_manager")


# ── fixtures ─────────────────────────────────────────────────────────────────

def _rows(tag, n=4):
    base = {f: "" for f in RCE_FIELDS}
    base.update({
        "domains": "RCE", "orgManagingOrg": "9.99.555.0.1",
        "purposesofuse": "T-TRTMNT", "active": "1",
        "sequoiaorgtype": "Participant", "address_line": "1 Synthetic Way",
        "address_city": "Testville", "address_state": "MA",
        "address_postalCode": "99999", "address_country": "USA",
        "partOf": "9.99.555.0.1",
    })
    out = []
    for i in range(1, n + 1):
        r = dict(base)
        r["id"] = f"9.99.555.{tag}.{i}"
        r["TEFCAID"] = f"{SYN}-{tag}-{i:04d}"
        r["HCID"] = f"urn:oid:9.99.555.{tag}.{i}"
        r["name"] = f"{SYN} {tag} ORG {i}"
        out.append(r)
    # Record 1: a malformed NPI -> NPI-002 HIGH, HUMAN_REQUIRED -> IDENTITY.
    out[0]["NPI"] = "12345"
    # Record 2: a test-pattern name -> BUS-002 HUMAN_REQUIRED -> METHODOLOGY,
    # AND a malformed NPI -> IDENTITY. Two classes on one record.
    out[1]["name"] = f"{SYN} {tag} TEST ORG 2"
    out[1]["NPI"] = "9999"
    return out


async def _seed(db, tag, n=4):
    """Intake + source records + curated rows, all promoted to real entities.

    Entities are created directly rather than through `promote_delivery`:
    promotion is a closed gate and is not what this file is testing, and
    `review_records.entity_id` is NOT NULL so a case needs one to exist.
    """
    rows = _rows(tag, n)
    blob = ("\r\n".join(["|".join(RCE_FIELDS)]
                        + ["|".join(r[f] for f in RCE_FIELDS) for r in rows])
            + "\r\n").encode("utf-8")
    intake_id = uuid.uuid4()
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=f"{SYN}-{tag}",
        original_filename=f"synthetic-{tag}.csv", storage_path="(synthetic)",
        sha256=hashlib.sha256(blob).hexdigest(), file_size_bytes=len(blob),
        delimiter="|", encoding="utf-8", line_terminator="CRLF",
        headers=list(RCE_FIELDS),
        schema_fingerprint=schema_fingerprint(list(RCE_FIELDS)),
        record_count=len(rows), received_at=datetime.utcnow(), received_by=SYN,
        status="PARSED", source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()

    for line_number, r in enumerate(rows, start=2):
        raw = "|".join(r[f] for f in RCE_FIELDS)
        source_id = uuid.uuid4()
        db.add(m.RceSourceRecord(
            id=source_id, source_intake_id=intake_id,
            line_number=line_number, raw_line=raw, parsed=r,
            record_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            source_rce_id=r["id"], tefcaid=r["TEFCAID"], hcid=r["HCID"],
            npi=r["NPI"] or None, field_count=len(RCE_FIELDS),
            parse_status="ok", promotion_status="pending"))
        await db.flush()

        entity_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(
            id=entity_id, name=r["name"], display_name=r["name"],
            entity_level="participant", entity_type="provider",
            operational_status="active", verification_status="not_verified",
            current_version=1, is_active=True, rce_org_oid=r["id"],
            source_record_id=source_id))
        await db.flush()

        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id,
            source_record_id=source_id, record_status="CLEAN",
            issue_count=0, correction_count=0,
            rce_org_oid=r["id"], tefcaid=r["TEFCAID"], hcid=r["HCID"],
            name=r["name"], entity_level="participant",
            sequoia_org_type="Participant", operational_status="active",
            is_active=True, address_line=r["address_line"],
            address_city=r["address_city"], address_state=r["address_state"],
            address_postal_code=r["address_postalCode"],
            address_country=r["address_country"],
            exchange_purposes=["T-TRTMNT"], part_of="9.99.555.0.1",
            org_managing_org="9.99.555.0.1", contact={}, rce_attributes={},
            is_test_record=False, transformation_version="test-1.0.0",
            canonical_entity_id=entity_id))
    await db.commit()
    return intake_id


@pytest.fixture
async def rolled_back_db(db_required):
    """Session on an outer transaction that is rolled back.

    A dedicated engine, not the app's global one: `conftest._use_null_pool()`
    rebuilds that engine's pool and loses the `on_connect` listeners that
    register asyncpg's JSON codecs, so JSONB reads back as raw text.
    """
    import os

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url

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


@pytest.fixture
async def bridged(rolled_back_db):
    """A synthetic delivery, quality-run once, with its DQ cases built."""
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    await run_quality_engine(db, intake_id, executed_by=SYN)
    result = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    return db, intake_id, result


# ═══ PHASE 5 — case bridge ═══════════════════════════════════════════════════

async def test_human_required_issues_create_review_cases(bridged):
    """TEST 1: the bridge turns current HUMAN_REQUIRED findings into cases."""
    db, intake_id, result = bridged
    assert result["cases_created"] > 0
    assert result["cases_already_present"] == 0
    assert result["human_required_issues"] > 0

    cases = await bridge.open_cases(db, intake_id)
    assert len(cases) == result["cases_created"]
    for case in cases:
        assert case["issue_codes"], "a case must cite the findings that justify it"
        assert case["case_classification"] in (
            "DQ", "IDENTITY", "RELATIONSHIP", "METHODOLOGY")
        assert case["reportable"] is False


async def test_a_case_creates_a_question_not_an_answer(bridged):
    """INVARIANT 8: automation raises work; it never decides."""
    db, intake_id, _ = bridged
    records = (await db.execute(
        select(reg.ReviewRecord).where(
            reg.ReviewRecord.verification_results["queue_source"].astext
            == bridge.QUEUE_SOURCE))).scalars().all()
    assert records
    for record in records:
        assert record.classification_bucket is None
        assert record.reviewer_resolution is None
        assert record.reportable_at is None

    # And the issues it cites are untouched — still OPEN, unresolved.
    resolutions = (await db.execute(
        select(m.RceIssue.resolution).where(
            run_selection.current_issues_filter(intake_id),
            m.RceIssue.correction_authority == "HUMAN_REQUIRED"))).scalars().all()
    assert set(resolutions) == {"OPEN"}


async def test_materially_different_findings_on_one_record_stay_separate(bridged):
    """SCENARIO B/C: one record, two classes -> two cases, not one answer."""
    db, intake_id, _ = bridged
    plan = await bridge.plan_cases(db, intake_id)

    per_record = {}
    for case in plan["cases"]:
        per_record.setdefault(case["source_record_id"], []).append(case)
    multi = [cases for cases in per_record.values() if len(cases) > 1]
    assert multi, "the fixture must produce a record with two classes"
    for cases in multi:
        classes = [c["classification"] for c in cases]
        assert len(set(classes)) == len(classes), (
            "two cases on one record must be different classifications — same "
            "class should have aggregated into one case"
        )


async def test_auto_safe_and_no_correction_do_not_create_analyst_work(bridged):
    """SCENARIO F + G: only issues needing judgement become cases."""
    db, intake_id, _ = bridged
    cited = set()
    for case in await bridge.open_cases(db, intake_id):
        cited.update(case["issue_codes"])

    non_human = (await db.execute(
        select(m.RceIssue.issue_code).where(
            run_selection.current_issues_filter(intake_id),
            m.RceIssue.correction_authority.in_(
                ("AUTO_SAFE", "NO_CORRECTION"))))).scalars().all()
    assert non_human, "the fixture must contain AUTO_SAFE/NO_CORRECTION findings"
    assert not (set(non_human) & cited), (
        "a deterministic or informational finding was queued for an analyst"
    )


async def test_rerunning_the_bridge_creates_no_duplicate_cases(bridged):
    """SCENARIO D + INVARIANT 4: idempotent."""
    db, intake_id, first = bridged
    before = len(await bridge.open_cases(db, intake_id))

    second = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()

    assert second["cases_created"] == 0
    assert second["cases_already_present"] == first["cases_created"]
    assert len(await bridge.open_cases(db, intake_id)) == before


async def test_a_superseded_run_does_not_create_current_workload(rolled_back_db):
    """SCENARIO E + INVARIANT 1/2: only the current run raises new work."""
    db = rolled_back_db
    intake_id = await _seed(db, "A")

    first = await run_quality_engine(db, intake_id, executed_by=SYN)
    built_once = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    assert built_once["cases_created"] > 0

    # A second quality run supersedes the first.
    second = await run_quality_engine(db, intake_id, executed_by=SYN)
    assert second["run_id"] != first["run_id"]

    plan = await bridge.plan_cases(db, intake_id)
    assert all(str(c["run_id"]) == second["run_id"] for c in plan["cases"]), (
        "the bridge planned work from a superseded run"
    )
    # The historical run's cases are NOT re-created, and the new run's are new.
    rebuilt = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    assert rebuilt["cases_created"] == built_once["cases_created"], (
        "a new assessment must raise its own work"
    )
    assert rebuilt["cases_already_present"] == 0

    # Explicitly asking for the historical run still finds its cases.
    historical = await bridge.plan_cases(
        db, intake_id, run_id=uuid.UUID(first["run_id"]))
    assert historical["planned_cases"] == built_once["cases_created"]


async def test_an_unclassified_rule_is_refused_not_defaulted():
    """A new rule must be classified deliberately, never routed by accident."""
    with pytest.raises(bridge.BridgeRefused, match="no case classification"):
        bridge.classification_for("ZZZ-999")
    # And every rule that can raise a HUMAN_REQUIRED finding IS classified.
    from app.tefca_registry.rce.quality_rules import RULES

    for rule in RULES:
        assert rule.rule_id in bridge.RULE_CLASSIFICATION, (
            f"{rule.rule_id} can raise findings but has no case classification"
        )


async def test_a_held_record_produces_a_reviewable_pre_promotion_case(
        rolled_back_db):
    """HELD must not mean UNREVIEWABLE.

    A record is HELD precisely because it carries an unresolved substantive
    problem, so it is never promoted and has no entity. Before migration
    `20260831_review_case` that made it the one thing human review could not
    represent — and on the delivered population that was all four HIGH-severity
    identity findings. The case is now anchored to the delivered line instead.
    """
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    await run_quality_engine(db, intake_id, executed_by=SYN)

    # Strip the entity link from one curated row, as a HELD record has none.
    row = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .limit(1))).scalars().first()
    row.canonical_entity_id = None
    row.record_status = "HELD"
    held_source_id = row.source_record_id
    await db.commit()

    plan = await bridge.plan_cases(db, intake_id)
    assert plan["unmappable_issues"] == 0, "a HELD record is now reviewable"
    assert plan["pre_promotion_cases"] > 0
    assert plan["entity_backed_cases"] > 0, "the fixture must have both kinds"

    result = await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()
    assert result["pre_promotion_cases"] > 0

    # The pre-promotion case exists, has NO entity, and IS anchored to Area 1.
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.source_record_id == held_source_id))).scalars().first()
    assert record is not None
    assert record.entity_id is None, "no entity may be synthesised to stand in"
    assert record.source_record_id == held_source_id

    # And the record it is about is still HELD — nothing was promoted for this.
    still = await db.get(m.RceCuratedRecord, row.id)
    assert still.record_status == "HELD"
    assert still.canonical_entity_id is None


async def test_a_case_can_never_be_about_nothing(rolled_back_db):
    """The CHECK that replaces the NOT NULL: entity OR source record."""
    from sqlalchemy.exc import IntegrityError

    db = rolled_back_db
    db.add(reg.ReviewRecord(
        id=uuid.uuid4(), review_id="REV-9999-999999",
        entity_id=None, source_record_id=None,
        verification_results={"queue_source": bridge.QUEUE_SOURCE}))
    with pytest.raises(IntegrityError, match="ck_review_record_has_subject"):
        await db.flush()
    await db.rollback()


# ═══ PHASES 10–11 — analyst determination and independent QA ═════════════════

async def _one_case(db, intake_id) -> str:
    cases = await bridge.open_cases(db, intake_id)
    assert cases
    return cases[0]["review_id"]


async def test_analyst_can_record_a_determination(bridged):
    """PHASE 10: the analyst answers the question the case asks."""
    from app.tefca_registry.qa_gate import effective_determination, _events

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)

    from app.tefca_registry.qa_gate import record_analyst_determination

    result = await record_analyst_determination(
        db, review_id, user=ANALYST, determination="RECLASSIFY",
        determined_bucket="B2",
        rationale="Synthetic: address discrepancy is administrative only.")
    await db.commit()

    assert result["event_type"] == "ANALYST_DETERMINATION"
    events = await _events(db, review_id)
    assert len(events) == 1
    assert events[0].actor_email == ANALYST.email
    assert events[0].actor_role == "reviewer"
    effective = effective_determination(events)
    assert effective["determination"] == "RECLASSIFY"
    assert effective["determined_bucket"] == "B2"


async def test_an_analyst_determination_alone_is_not_reportable(bridged):
    """PHASE 13 CASE B: the analyst cannot make their own answer official."""
    from app.tefca_registry.qa_gate import (_events, is_reportable,
                                            record_analyst_determination)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic rationale for the determination.")
    await db.commit()

    assert is_reportable(await _events(db, review_id)) is False
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.reportable_at is None


async def test_the_analyst_cannot_qa_their_own_determination(bridged):
    """PHASE 11 + TEST 18: segregation of duties, refused in the service."""
    from app.tefca_registry.qa_gate import (QaGateRefused,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic rationale for the determination.")
    await db.commit()

    with pytest.raises(QaGateRefused, match="segregation of duties"):
        await submit_qa_review(
            db, review_id, user=ANALYST, qa_action="APPROVE",
            qa_reason="Synthetic attempt to approve my own work.")
    await db.rollback()

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.reportable_at is None


async def test_a_different_qa_reviewer_can_approve_and_it_becomes_reportable(
        bridged):
    """PHASE 11 + 13 CASE E: the full maker-checker chain, end to end."""
    from app.tefca_registry.qa_gate import (_events, is_reportable,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)

    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="RECLASSIFY",
        determined_bucket="B2",
        rationale="Synthetic: administrative discrepancy, not material.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="APPROVE",
        qa_reason="Synthetic QA: determination supported by the evidence cited.")
    await db.commit()

    events = await _events(db, review_id)
    assert [e.event_type for e in events] == ["ANALYST_DETERMINATION", "QA_REVIEW"]
    assert events[1].actor_email == QA.email != events[0].actor_email
    assert is_reportable(events) is True

    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.reportable_at is not None


async def test_qa_return_leaves_the_case_unreportable_and_reworkable(bridged):
    """PHASE 13 CASE C + TEST 20."""
    from app.tefca_registry.qa_gate import (_events, is_reportable,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)

    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic first determination.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="RETURN",
        qa_reason="Synthetic QA: rationale does not address the cited finding.")
    await db.commit()

    assert is_reportable(await _events(db, review_id)) is False
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assert record.reportable_at is None

    # The analyst may now determine again, and QA may then approve.
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="RECLASSIFY",
        determined_bucket="B3",
        rationale="Synthetic second determination addressing the QA return.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="APPROVE",
        qa_reason="Synthetic QA: the rework answers the point raised.")
    await db.commit()

    events = await _events(db, review_id)
    assert len(events) == 4, "every act must be its own event; none overwritten"
    assert is_reportable(events) is True


async def test_qa_escalate_does_not_make_a_case_reportable(bridged):
    """PHASE 13 CASE D + TEST 21."""
    from app.tefca_registry.qa_gate import (_events, is_reportable,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)

    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic determination pending escalation.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="ESCALATE",
        qa_reason="Synthetic QA: needs programme-level interpretation.",
        escalated_to_user_id=PM.id,
        escalation_reason="Synthetic: methodology question for the programme.")
    await db.commit()

    assert is_reportable(await _events(db, review_id)) is False


async def test_an_unreviewed_case_is_never_reportable(bridged):
    """PHASE 13 CASE A + TEST 25."""
    from app.tefca_registry.qa_gate import _events, is_reportable

    db, intake_id, _ = bridged
    for case in await bridge.open_cases(db, intake_id):
        assert is_reportable(await _events(db, case["review_id"])) is False
        assert case["reportable"] is False


# ═══ PHASE 12 — replay and ordering ══════════════════════════════════════════

async def test_qa_cannot_act_before_an_analyst_determination(bridged):
    """TEST 22: a system recommendation is not a determination."""
    from app.tefca_registry.qa_gate import QaGateRefused, submit_qa_review

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)

    with pytest.raises(QaGateRefused, match="no analyst determination"):
        await submit_qa_review(
            db, review_id, user=QA, qa_action="APPROVE",
            qa_reason="Synthetic QA approving nothing at all.")
    await db.rollback()


async def test_duplicate_qa_approval_is_refused(bridged):
    """TEST 23."""
    from app.tefca_registry.qa_gate import (QaGateRefused,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic determination for duplicate-QA test.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="APPROVE",
        qa_reason="Synthetic QA approval, the first one.")
    await db.commit()

    with pytest.raises(QaGateRefused, match="standing APPROVE"):
        await submit_qa_review(
            db, review_id, user=QA, qa_action="APPROVE",
            qa_reason="Synthetic QA approval, a second time.")
    await db.rollback()


async def test_a_second_determination_against_a_standing_approval_is_refused(
        bridged):
    """TEST 24-adjacent: after approval, the route is supersession, not edit."""
    from app.tefca_registry.qa_gate import (QaGateRefused,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)
    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic determination before approval.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="APPROVE",
        qa_reason="Synthetic QA approval standing.")
    await db.commit()

    with pytest.raises(QaGateRefused, match="superseding determination"):
        await record_analyst_determination(
            db, review_id, user=ANALYST, determination="CONFIRM",
            rationale="Synthetic attempt to redo an approved determination.")
    await db.rollback()


async def test_decision_events_are_append_only(bridged):
    """TEST 24: nothing is overwritten, and there is no MODIFY action."""
    from app.tefca_registry.qa_gate import (_events,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)

    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic first determination, must survive.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="RETURN",
        qa_reason="Synthetic QA return, must survive.")
    await db.commit()
    first_snapshot = [(e.sequence_number, e.event_type, e.actor_email,
                       e.rationale) for e in await _events(db, review_id)]

    await record_analyst_determination(
        db, review_id, user=ANALYST_2, determination="RECLASSIFY",
        determined_bucket="B3",
        rationale="Synthetic replacement determination by a second analyst.")
    await db.commit()

    events = await _events(db, review_id)
    later_snapshot = [(e.sequence_number, e.event_type, e.actor_email,
                       e.rationale) for e in events]
    assert later_snapshot[:2] == first_snapshot, "an earlier event changed"
    assert len(events) == 3
    assert [e.sequence_number for e in events] == [1, 2, 3]


# ═══ PHASE 14 — audit ════════════════════════════════════════════════════════

async def test_the_full_case_history_is_reconstructable(bridged):
    """PHASE 14 + TEST 31: creation, determination, QA — all attributable."""
    from app.tefca_registry.qa_gate import (history, _events,
                                            record_analyst_determination,
                                            submit_qa_review)

    db, intake_id, _ = bridged
    review_id = await _one_case(db, intake_id)

    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="CONFIRM",
        rationale="Synthetic determination for the audit reconstruction.")
    await submit_qa_review(
        db, review_id, user=QA, qa_action="APPROVE",
        qa_reason="Synthetic QA approval for the audit reconstruction.")
    await db.commit()

    # Case creation is audited, naming what justified it.
    created = (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action == "review_case_created"))).scalars().all()
    mine = [a for a in created
            if (a.metadata_ or {}).get("review_id") == review_id]
    assert len(mine) == 1
    payload = mine[0].metadata_
    assert payload["issue_codes"] and payload["case_classification"]
    assert payload["quality_run_id"]

    # And the decision chain names every actor, role and time.
    chain = history(await _events(db, review_id))
    assert [e["event_type"] for e in chain] == [
        "ANALYST_DETERMINATION", "QA_REVIEW"]
    for event in chain:
        assert event["actor_email"] and event["actor_role"]
        assert event["occurred_at"] is not None
        assert event["rationale"]
    assert chain[0]["actor_email"] != chain[1]["actor_email"]

    # QA decisions are audited too.
    qa_events = (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action == "qa_approve"))).scalars().all()
    assert any((a.metadata_ or {}).get("review_id") == review_id
               for a in qa_events)


# ═══ PHASE 16 — supervisor workload ══════════════════════════════════════════

async def test_supervisor_workload_counts_are_available(bridged):
    """PHASE 16: aggregate counts, with aging named operationally."""
    db, intake_id, result = bridged
    summary = await bridge.workload_summary(db, intake_id)

    assert summary["total_cases"] == result["cases_created"]
    assert summary["unresolved"] == result["cases_created"]
    assert summary["reportable"] == 0
    assert sum(summary["by_classification"].values()) == summary["total_cases"]
    assert sum(summary["by_severity"].values()) == summary["total_cases"]
    assert summary["operational_age_days"]["oldest"] is not None
    assert "not a contractual SLA" in summary["note"]
    # The word must not appear as a promise anywhere in the payload.
    assert "sla" not in str(summary).lower().replace("not a contractual sla", "")


# ═══ INTEGRITY ═══════════════════════════════════════════════════════════════

async def test_government_rows_are_untouched(bridged):
    """TEST 34-36: the fixtures write only their own delivery."""
    db, intake_id, result = bridged

    # Every case created belongs to this synthetic intake.
    records = (await db.execute(
        select(reg.ReviewRecord).where(
            reg.ReviewRecord.verification_results["queue_source"].astext
            == bridge.QUEUE_SOURCE))).scalars().all()
    assert records
    for record in records:
        assert (record.verification_results
                or {}).get("source_intake_id") == str(intake_id)

    # The 43 pre-existing review records carry no queue_source and are not ours.
    legacy = int((await db.execute(
        select(func.count()).select_from(reg.ReviewRecord)
        .where(reg.ReviewRecord.verification_results["queue_source"].astext
               .is_(None)))).scalar() or 0)
    assert legacy >= 43, "pre-existing review records disappeared"



# ═══ END TO END — a promoted subject and a HELD subject ══════════════════════

async def _walk_case_to_approval(db, review_id):
    """claim -> determination -> submit for QA -> independent QA approve."""
    from app.tefca_registry import case_assignment as assignment
    from app.tefca_registry.qa_gate import (_events, is_reportable,
                                            record_analyst_determination,
                                            submit_qa_review)

    assert await assignment.case_state(db, review_id) == assignment.AVAILABLE
    await assignment.claim(db, review_id, user=ANALYST)
    await db.commit()
    assert await assignment.case_state(db, review_id) == assignment.CLAIMED

    # Only the holder may work it.
    record = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
    assignment.require_owner(record, ANALYST)
    with pytest.raises(assignment.AssignmentRefused):
        assignment.require_owner(record, ANALYST_2)

    await record_analyst_determination(
        db, review_id, user=ANALYST, determination="RECLASSIFY",
        determined_bucket="B2",
        rationale="Synthetic determination for the end-to-end walk.")
    await db.commit()
    assert await assignment.case_state(db, review_id) == assignment.SUBMITTED_FOR_QA
    assert is_reportable(await _events(db, review_id)) is False

    await submit_qa_review(
        db, review_id, user=QA, qa_action="APPROVE",
        qa_reason="Synthetic QA approval for the end-to-end walk.")
    await db.commit()
    assert await assignment.case_state(db, review_id) == assignment.APPROVED
    assert is_reportable(await _events(db, review_id)) is True
    return record


async def test_end_to_end_case_a_promoted_subject(bridged):
    """CASE A: HUMAN_REQUIRED -> case -> claim -> determine -> QA -> reportable."""
    db, intake_id, _ = bridged
    entity_backed = [c for c in await bridge.open_cases(db, intake_id)
                     if c["entity_id"] is not None]
    assert entity_backed, "the fixture must produce an entity-backed case"

    record = await _walk_case_to_approval(db, entity_backed[0]["review_id"])
    await db.refresh(record)
    assert record.entity_id is not None
    assert record.reportable_at is not None


async def test_end_to_end_case_b_held_subject(rolled_back_db):
    """CASE B: the same journey for a record that was never promoted.

    Proves a pre-promotion case never needs a fabricated entity, and that
    approving the REVIEW does not promote anything.
    """
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    await run_quality_engine(db, intake_id, executed_by=SYN)

    held_row = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .limit(1))).scalars().first()
    held_row.canonical_entity_id = None
    held_row.record_status = "HELD"
    await db.commit()

    await bridge.build_cases(db, intake_id, actor=SYN)
    await db.commit()

    pre = [c for c in await bridge.open_cases(db, intake_id)
           if c["entity_id"] is None]
    assert pre, "the HELD record must have produced a reviewable case"

    record = await _walk_case_to_approval(db, pre[0]["review_id"])
    await db.refresh(record)

    assert record.entity_id is None, "no entity was fabricated"
    assert record.source_record_id == held_row.source_record_id
    assert record.reportable_at is not None

    # REVIEW APPROVAL IS NOT PROMOTION. The record is still HELD and unpromoted.
    await db.refresh(held_row)
    assert held_row.record_status == "HELD"
    assert held_row.canonical_entity_id is None
    source = await db.get(m.RceSourceRecord, held_row.source_record_id)
    assert source.promotion_status == "pending"
    assert source.canonical_entity_id is None


def test_fixtures_are_synthetic_only():
    for tag in ("A",):
        for r in _rows(tag):
            assert r["id"].startswith("9.99.555.")
            assert r["name"].startswith(SYN)
            assert r["TEFCAID"].startswith(SYN)
            assert r["NPI"] in ("", "12345", "9999")
    for actor in (ANALYST, ANALYST_2, QA, PM):
        assert actor.email.endswith("@synthetic.test")
