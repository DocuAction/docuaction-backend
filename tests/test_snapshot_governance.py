"""P1-1 / P1-2 — fail-closed snapshot approval and the compensating
relationship rollback, on the isolated PostgreSQL.

Every row is synthetic (ARC 9.99.777.95); no delivered value appears.
Assertions use explicit parentheses and exact comparisons.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import relationship_history as rh
from app.tefca_registry.rce import snapshot_effects as se
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce.promotion import REL_SUB_PARTICIPANT_OF, promote_delivery
from app.tefca_registry.rce.reconciliation import latest_snapshot, persist_snapshot, reconcile_delivery
from rce_traceability_support import (  # noqa: F401
    QHIN_OID, SYN, base_row, rolled_back_db, run_quality_and_curation, seed_intake,
)

ARC = "9.99.777.95"
P300, P700 = f"{ARC}.300", f"{ARC}.700"
T0 = datetime(2002, 7, 20, 12, 0, 0)     # far past: previous_delivery is global


class _User:
    def __init__(self, role, email, uid=None):
        self.role, self.email, self.id = role, email, uid


QALEAD = _User("qalead", "qa-lead@synthetic.invalid")
DATAOPS = _User("program_manager", "dataops@synthetic.invalid")


def _rows(moved: bool = False, extra_id: str | None = None):
    rows = []

    def row(i, **over):
        r = base_row(**over)
        r["id"] = f"{ARC}.{i}" if isinstance(i, int) else i
        r["TEFCAID"] = f"{SYN}-{ARC}-T-{str(i)[-4:]}"
        r["HCID"] = f"urn:oid:{r['id']}"
        r["name"] = over.get("name") or f"{SYN} {ARC} ORG {i}"
        rows.append(r)

    row(P300, name=f"{SYN} P300", sequoiaorgtype="Participant", partOf=QHIN_OID)
    row(P700, name=f"{SYN} P700", sequoiaorgtype="Participant", partOf=QHIN_OID)
    for i in (1, 2):
        row(i, sequoiaorgtype="Subparticipant", partOf=(P700 if moved else P300))
    row(3)
    if extra_id:
        row(extra_id, name=f"{SYN} EXTRA")
    return rows


async def _delivery(db, rows, received_at, *, promote=True):
    intake_id, job = await seed_intake(db, rows)
    intake = await db.get(m.RceSourceIntake, intake_id)
    intake.received_at = received_at
    await db.commit()
    await run_quality_and_curation(db, intake_id)
    if promote:
        await promote_delivery(db, intake_id, actor=SYN)
    return intake_id, job


async def _reconcile_and_persist(db, intake_id, job, *, force_fail: bool = False):
    result = await reconcile_delivery(db, intake_id)
    if force_fail:
        result = dict(result, passed=False,
                      checks=result["checks"] + [{"check": "forced", "passed": False, "detail": "test"}],
                      failed_checks=[{"check": "forced", "passed": False, "detail": "test"}])
    snap = await persist_snapshot(db, intake_id, result, job_id=job.id, actor=SYN, trigger="MANUAL")
    return result, snap


async def _entity(db, oid):
    return (await db.execute(
        select(reg.TefcaEntityIdentifier.entity_id)
        .where(reg.TefcaEntityIdentifier.identifier_type == "rce_org_oid",
               reg.TefcaEntityIdentifier.identifier_value == oid))).scalar_one()


async def _active_edges(db, child_id, rel_type=REL_SUB_PARTICIPANT_OF):
    return (await db.execute(
        select(reg.TefcaEntityRelationship)
        .where(reg.TefcaEntityRelationship.child_entity_id == child_id,
               reg.TefcaEntityRelationship.relationship_type == rel_type,
               reg.TefcaEntityRelationship.end_date.is_(None)))).scalars().all()


async def _view_rows(db, intake_id) -> int:
    return int((await db.execute(text(
        "select count(*) from arc_current_stale where intake_id = :i"), {"i": str(intake_id)})).scalar())


# ── registration, current views, failure, retry, approval ────────────────────

@pytest.mark.asyncio
async def test_registration_is_pending_and_pending_is_never_current(rolled_back_db):
    db = rolled_back_db
    july_id, july_job = await _delivery(db, _rows(), T0)
    july_eff = await se.apply_snapshot_effects(db, july_id, actor=SYN)
    assert july_eff["source_snapshot"]["status"] == "PENDING"
    assert (await se.current_snapshot(db))["current"] is None

    sub1 = await _entity(db, f"{ARC}.1")
    db.add(reg.ReviewRecord(review_id=f"REV-9991-{uuid.uuid4().hex[:6].upper()}", entity_id=sub1,
                            verification_results={"source_intake_id": str(july_id)}))
    await db.commit()
    old_edge = (await _active_edges(db, sub1))[0]

    sept_id, sept_job = await _delivery(db, _rows(moved=True), T0 + timedelta(days=44))
    eff = await se.apply_snapshot_effects(db, sept_id, actor=SYN)
    assert eff["source_snapshot"]["status"] == "PENDING"
    # sub1 (has an ARC result) and sub2 (entity-level mark, no review id)
    assert eff["stale"]["marked"] == 2

    # CURRENT VIEWS exclude the PENDING snapshot: stale marks, the SQL view,
    # relationship "current" (the ended edge stays current; the new one is staged)
    assert await se.stale_for_entities(db, [sub1]) == {}
    assert await _view_rows(db, sept_id) == 0
    hist = await rh.history_for_entity(db, sub1)
    new_edge = (await _active_edges(db, sub1))[0]
    assert [e["id"] for e in hist["staged"] if e["relationship_type"] == REL_SUB_PARTICIPANT_OF] == [str(new_edge.id)]
    cur_ids = [e["id"] for e in hist["current"] if e["relationship_type"] == REL_SUB_PARTICIPANT_OF]
    assert cur_ids == [str(old_edge.id)]
    assert (await se.snapshot_state(db, sept_id))["approvable"] is True
    assert (await se.snapshot_state(db, sept_id))["effective"] is False


@pytest.mark.asyncio
async def test_failed_effects_fail_closed_and_retry_is_idempotent(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await _delivery(db, _rows(), T0)
    # the runner's failure path: durable FAILED row, no PENDING
    failure = await se.record_effects_failure(db, intake_id, actor=SYN, error="synthetic failure")
    assert failure["recorded"] is True
    state = await se.snapshot_state(db, intake_id)
    assert (state["status"] == "FAILED") and (state["legacy"] is False) and (state["approvable"] is False)

    # reconciliation fails on the effects check -> never READY / no cycle / no report
    recon = await reconcile_delivery(db, intake_id)
    assert recon["passed"] is False
    failed = [c["check"] for c in recon["checks"] if not c["passed"]]
    assert failed == ["Snapshot effects completed (PENDING or APPROVED snapshot)"]

    # approval refused while FAILED
    with pytest.raises(se.ApprovalRefused, match="not completed"):
        await se.approve_delivery_snapshot(db, intake_id, user=QALEAD, approval_ref="REF-1")

    # a second failure record is not appended (idempotent)
    again = await se.record_effects_failure(db, intake_id, actor=SYN, error="again")
    assert (again["recorded"] is False) and (again["already"] is True)
    assert len(await se.snapshot_chain(db, intake_id)) == 1

    # retry: PENDING supersedes FAILED; effects rows are not duplicated
    r1 = await se.apply_snapshot_effects(db, intake_id, actor=SYN)
    assert (r1["source_snapshot"]["status"] == "PENDING") and (r1["source_snapshot"]["already"] is False)
    r2 = await se.apply_snapshot_effects(db, intake_id, actor=SYN)
    assert (r2["source_snapshot"]["already"] is True) and (r2["presence"]["present"] == 0)
    chain = await se.snapshot_chain(db, intake_id)
    assert [c.status for c in chain] == ["FAILED", "PENDING"]
    assert chain[1].supersedes_snapshot_id == chain[0].id
    assert (await reconcile_delivery(db, intake_id))["passed"] is True


@pytest.mark.asyncio
async def test_approval_needs_passed_reconciliation_and_an_authorised_human(rolled_back_db):
    db = rolled_back_db
    intake_id, job = await _delivery(db, _rows(), T0)
    await se.apply_snapshot_effects(db, intake_id, actor=SYN)

    # no persisted reconciliation snapshot yet
    with pytest.raises(se.ApprovalRefused, match="has not PASSED"):
        await se.approve_delivery_snapshot(db, intake_id, user=QALEAD, approval_ref="REF-1")
    # a FAILED persisted reconciliation snapshot
    await _reconcile_and_persist(db, intake_id, job, force_fail=True)
    with pytest.raises(se.ApprovalRefused, match="has not PASSED"):
        await se.approve_delivery_snapshot(db, intake_id, user=QALEAD, approval_ref="REF-1")
    # a PASSED one
    result, recon = await _reconcile_and_persist(db, intake_id, job)
    assert (result["passed"] is True) and (recon.passed is True) and (recon.hash is not None)

    # unauthorised roles / actors
    with pytest.raises(se.ApprovalRefused, match="requires a human qalead"):
        await se.approve_delivery_snapshot(db, intake_id, user=_User("senior_analyst", "sa@x"), approval_ref="R")
    with pytest.raises(se.ApprovalRefused, match="requires a human qalead"):
        await se.approve_delivery_snapshot(db, intake_id, user=_User("qalead", "SYSTEM"), approval_ref="R")
    with pytest.raises(se.ApprovalRefused, match="maker/checker"):   # the delivery's registrant
        await se.approve_delivery_snapshot(db, intake_id, user=_User("qalead", SYN), approval_ref="R")
    assert (await se.snapshot_state(db, intake_id))["status"] == "PENDING"

    # authorised approval: append-only, auditable evidence
    ok = await se.approve_delivery_snapshot(db, intake_id, user=QALEAD, approval_ref="ONC-SEPT-APPROVAL")
    chain = await se.snapshot_chain(db, intake_id)
    assert [c.status for c in chain] == ["PENDING", "APPROVED"]
    approved = chain[1]
    assert approved.supersedes_snapshot_id == chain[0].id
    assert (approved.approved_by == QALEAD.email) and (approved.approved_role == "qalead")
    assert (approved.reconciliation_snapshot_id == recon.id) and (approved.reconciliation_hash == recon.hash)
    assert (approved.approval_ref == "ONC-SEPT-APPROVAL") and (approved.build_sha is not None)
    assert (approved.correlation_id is not None) and (approved.approved_at is not None)
    assert ok["reconciliation_hash"] == recon.hash
    assert (await se.current_snapshot(db))["current"]["intake_id"] == str(intake_id)

    # cannot approve twice; the PENDING row was never edited
    with pytest.raises(se.ApprovalRefused, match="append-only"):
        await se.approve_delivery_snapshot(db, intake_id, user=QALEAD, approval_ref="R")
    await db.refresh(chain[0])
    assert chain[0].status == "PENDING"


@pytest.mark.asyncio
async def test_newer_approved_snapshot_supersedes_current_and_marks_become_current(rolled_back_db):
    db = rolled_back_db
    july_id, july_job = await _delivery(db, _rows(), T0)
    await se.apply_snapshot_effects(db, july_id, actor=SYN)
    await _reconcile_and_persist(db, july_id, july_job)
    await se.approve_delivery_snapshot(db, july_id, user=QALEAD, approval_ref="JULY")
    assert (await se.current_snapshot(db))["current"]["intake_id"] == str(july_id)

    # (no synthetic ReviewRecord here: a review without dimension evidence would
    # legitimately fail reconciliation; the entity-level mark is what is checked)
    sub1 = await _entity(db, f"{ARC}.1")
    sept_id, sept_job = await _delivery(db, _rows(moved=True), T0 + timedelta(days=44))
    await se.apply_snapshot_effects(db, sept_id, actor=SYN)
    assert (await se.current_snapshot(db))["current"]["intake_id"] == str(july_id)   # still July
    assert await se.stale_for_entities(db, [sub1]) == {}
    await _reconcile_and_persist(db, sept_id, sept_job)
    await se.approve_delivery_snapshot(db, sept_id, user=QALEAD, approval_ref="SEPT")
    assert (await se.current_snapshot(db))["current"]["intake_id"] == str(sept_id)
    live = await se.stale_for_entities(db, [sub1])
    assert [x["reason"] for x in live[str(sub1)]] == ["PART_OF_CHANGED"]
    assert await _view_rows(db, sept_id) == 2      # sub1 and sub2 (both moved)
    hist = await rh.history_for_entity(db, sub1)
    assert [e for e in hist["staged"] if e["relationship_type"] == REL_SUB_PARTICIPANT_OF] == []
    cur = [e for e in hist["current"] if e["relationship_type"] == REL_SUB_PARTICIPANT_OF]
    assert (len(cur) == 1) and (cur[0]["snapshot_status"] == "APPROVED")
    assert len([e for e in hist["historical"] if e["relationship_type"] == REL_SUB_PARTICIPANT_OF]) == 1


# ── P1-2 compensation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_restores_old_edges_retires_replacements_and_is_idempotent(rolled_back_db):
    db = rolled_back_db
    july_id, july_job = await _delivery(db, _rows(), T0)
    await se.apply_snapshot_effects(db, july_id, actor=SYN)
    july_result, july_recon = await _reconcile_and_persist(db, july_id, july_job)
    await se.approve_delivery_snapshot(db, july_id, user=QALEAD, approval_ref="JULY")
    july_hashes = sorted((await db.execute(
        select(m.RceSourceRecord.record_sha256).where(m.RceSourceRecord.source_intake_id == july_id))).scalars().all())

    sub1, sub2 = await _entity(db, f"{ARC}.1"), await _entity(db, f"{ARC}.2")
    p300, p700 = await _entity(db, P300), await _entity(db, P700)
    old1 = (await _active_edges(db, sub1))[0]
    old2 = (await _active_edges(db, sub2))[0]

    sept_id, sept_job = await _delivery(db, _rows(moved=True), T0 + timedelta(days=44))
    await se.apply_snapshot_effects(db, sept_id, actor=SYN)
    await _reconcile_and_persist(db, sept_id, sept_job)
    await se.approve_delivery_snapshot(db, sept_id, user=QALEAD, approval_ref="SEPT")
    new1 = (await _active_edges(db, sub1))[0]
    assert (str(new1.parent_entity_id) == str(p700)) and (new1.id != old1.id)

    # unauthorised role / no reason refused before anything is planned
    with pytest.raises(rh.RollbackRefused, match="program_manager"):
        await rh.compensate_snapshot(db, sept_id, actor="sa@x", role="senior_analyst", reason="r", apply=True)
    with pytest.raises(rh.RollbackRefused, match="reason"):
        await rh.compensate_snapshot(db, sept_id, actor=DATAOPS.email, role="program_manager", reason="", apply=True)

    plan = await rh.compensate_snapshot(db, sept_id, actor=DATAOPS.email, role="program_manager",
                                        reason="bad file", apply=False)
    assert (plan["applied"] is False) and (plan["to_retire"] == 2) and (plan["to_restore"] == 2)
    assert (await _active_edges(db, sub1))[0].id == new1.id     # a plan writes nothing

    done = await rh.compensate_snapshot(db, sept_id, actor=DATAOPS.email, role="program_manager",
                                        reason="bad file", apply=True)
    assert (done["applied"] is True) and (done["retired"] == 2) and (done["restored"] == 2)
    assert (done["build_sha"] is not None) and (done["correlation_id"] is not None)

    for child, old in ((sub1, old1), (sub2, old2)):
        active = await _active_edges(db, child)
        assert (len(active) == 1) and (active[0].id == old.id)      # never two active parents
        await db.refresh(old)
        assert (old.end_date is None) and (old.status == "active") and (str(old.parent_entity_id) == str(p300))
    await db.refresh(new1)
    assert (new1.end_date is not None) and (new1.status == "rolled_back")
    obs = {(o.observation, str(o.relationship_id)) for o in (await db.execute(
        select(sm.TefcaRelationshipObservation)
        .where(sm.TefcaRelationshipObservation.intake_id == sept_id,
               sm.TefcaRelationshipObservation.observation.in_(["ROLLED_BACK", "RESTORED"])))).scalars().all()}
    assert (("ROLLED_BACK", str(new1.id)) in obs) and (("RESTORED", str(old1.id)) in obs)
    assert (await se.snapshot_state(db, sept_id))["status"] == "ROLLED_BACK"
    assert (await se.current_snapshot(db))["current"]["intake_id"] == str(july_id)
    audit = (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "relationship_snapshot_rolled_back",
        reg.TefcaRegAuditLog.actor_email == DATAOPS.email))).scalars().all()
    assert len(audit) >= 1
    md = audit[-1].metadata_
    assert (md["source_intake_id"] == str(sept_id)) and (md["actor_role"] == "program_manager")
    assert (md["reason"] == "bad file") and ("build_sha" in md) and ("correlation_id" in md)
    hist = await rh.history_for_entity(db, sub1)
    cur = [e["id"] for e in hist["current"] if e["relationship_type"] == REL_SUB_PARTICIPANT_OF]
    assert cur == [str(old1.id)]

    # idempotent: a repeat changes nothing and duplicates nothing
    before_obs = int((await db.execute(select(func.count()).select_from(sm.TefcaRelationshipObservation)
                                       .where(sm.TefcaRelationshipObservation.intake_id == sept_id))).scalar())
    before_edges = int((await db.execute(select(func.count()).select_from(reg.TefcaEntityRelationship)
                                         .where(reg.TefcaEntityRelationship.child_entity_id == sub1))).scalar())
    again = await rh.compensate_snapshot(db, sept_id, actor=DATAOPS.email, role="program_manager",
                                         reason="repeat", apply=True)
    assert (again["retired"] == 0) and (again["restored"] == 0) and (again["skipped_already_done"] == 2)
    assert int((await db.execute(select(func.count()).select_from(sm.TefcaRelationshipObservation)
                                 .where(sm.TefcaRelationshipObservation.intake_id == sept_id))).scalar()) == before_obs
    assert int((await db.execute(select(func.count()).select_from(reg.TefcaEntityRelationship)
                                 .where(reg.TefcaEntityRelationship.child_entity_id == sub1))).scalar()) == before_edges
    assert len(await _active_edges(db, sub1)) == 1
    # cannot re-approve a rolled-back snapshot
    with pytest.raises(se.ApprovalRefused, match="append-only"):
        await se.approve_delivery_snapshot(db, sept_id, user=QALEAD, approval_ref="R")

    # historical evidence retained: July source records, July reconciliation snapshot
    assert sorted((await db.execute(
        select(m.RceSourceRecord.record_sha256).where(m.RceSourceRecord.source_intake_id == july_id))).scalars().all()) == july_hashes
    july_latest = await latest_snapshot(db, july_job.id)
    assert (july_latest.id == july_recon.id) and (july_latest.hash == july_recon.hash)
    assert int((await db.execute(select(func.count()).select_from(sm.RceDeliveryDelta)
                                 .where(sm.RceDeliveryDelta.current_intake_id == sept_id))).scalar()) > 0


@pytest.mark.asyncio
async def test_rollback_refuses_later_approved_snapshot_scope_mismatch_and_two_parents(rolled_back_db):
    db = rolled_back_db
    july_id, july_job = await _delivery(db, _rows(), T0)
    await se.apply_snapshot_effects(db, july_id, actor=SYN)
    await _reconcile_and_persist(db, july_id, july_job)
    await se.approve_delivery_snapshot(db, july_id, user=QALEAD, approval_ref="JULY")
    sept_id, sept_job = await _delivery(db, _rows(moved=True), T0 + timedelta(days=44))
    await se.apply_snapshot_effects(db, sept_id, actor=SYN)
    await _reconcile_and_persist(db, sept_id, sept_job)
    await se.approve_delivery_snapshot(db, sept_id, user=QALEAD, approval_ref="SEPT")
    oct_id, oct_job = await _delivery(db, _rows(moved=True, extra_id=f"{ARC}.9"), T0 + timedelta(days=74))
    await se.apply_snapshot_effects(db, oct_id, actor=SYN)
    await _reconcile_and_persist(db, oct_id, oct_job)
    await se.approve_delivery_snapshot(db, oct_id, user=QALEAD, approval_ref="OCT")

    # a later APPROVED snapshot depends on the registry state: refuse
    with pytest.raises(rh.RollbackRefused, match="later APPROVED"):
        await rh.compensate_snapshot(db, sept_id, actor=DATAOPS.email, role="program_manager",
                                     reason="r", apply=True)
    # unknown delivery / cross-delivery scope: refuse
    with pytest.raises(rh.RollbackRefused, match="scope"):
        await rh.compensate_snapshot(db, uuid.uuid4(), actor=DATAOPS.email, role="program_manager",
                                     reason="r", apply=True)
    # rolling back OCT (the latest) is allowed; then SEPT would be next.
    # Two active parents: an extra active edge for sub1 makes the SEPT restore unsafe.
    oct_done = await rh.compensate_snapshot(db, oct_id, actor=DATAOPS.email, role="program_manager",
                                            reason="r", apply=True)
    assert oct_done["applied"] is True      # October asserted nothing new for sub1/sub2 (unchanged)
    sub1 = await _entity(db, f"{ARC}.1")
    p300 = await _entity(db, P300)
    stray = reg.TefcaEntityRelationship(
        id=uuid.uuid4(), parent_entity_id=(await _entity(db, f"{ARC}.3")), child_entity_id=sub1,
        relationship_type=REL_SUB_PARTICIPANT_OF, effective_date=T0.date(), status="active", source="test")
    db.add(stray)
    await db.commit()
    with pytest.raises(rh.RollbackRefused, match="two active parents"):
        await rh.compensate_snapshot(db, sept_id, actor=DATAOPS.email, role="program_manager",
                                     reason="r", apply=True)
    # nothing changed by the refusal
    new1 = [e for e in await _active_edges(db, sub1) if e.id != stray.id]
    assert (len(new1) == 1) and (str(new1[0].parent_entity_id) != str(p300))
    assert (await se.snapshot_state(db, sept_id))["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_rollback_refuses_incomplete_evidence(rolled_back_db):
    db = rolled_back_db
    july_id, july_job = await _delivery(db, _rows(), T0)
    await se.apply_snapshot_effects(db, july_id, actor=SYN)
    sept_id, sept_job = await _delivery(db, _rows(moved=True), T0 + timedelta(days=44))
    await se.apply_snapshot_effects(db, sept_id, actor=SYN)
    # an ASSERTED observation without its edge id = incomplete evidence
    sub1 = await _entity(db, f"{ARC}.1")
    db.add(sm.TefcaRelationshipObservation(
        id=uuid.uuid4(), relationship_id=None, child_entity_id=sub1, parent_entity_id=None,
        relationship_type=REL_SUB_PARTICIPANT_OF, observation="ASSERTED", intake_id=sept_id,
        effective_boundary=(T0 + timedelta(days=44)).date(), reason="synthetic gap", actor=SYN,
        correlation_id="t"))
    await db.commit()
    with pytest.raises(rh.RollbackRefused, match="evidence incomplete"):
        await rh.plan_snapshot_rollback(db, sept_id)
