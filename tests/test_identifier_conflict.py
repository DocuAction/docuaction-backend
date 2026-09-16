"""Identifier conflicts at promotion: raised, held, never overwritten; decided.

Contract Scenario B: a delivery submits NPI 1982916079 (fails the Luhn check,
so the record is HELD by NPI-003) for an organisation whose registered NPI is
1982916078. Expected: BOTH an NPI_CHECKSUM_INVALID finding and an NPI-008
NPI_EXISTING_VALUE_CONFLICT finding, a CONFLICT_RAISED decision event with
both values, the record HELD, and no change to the entity.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import curation, dispositions as disp
from app.tefca_registry.rce import identifier_decisions, reconciliation
from app.tefca_registry.rce import traceability_models as tm
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_BAD_CHECKSUM, NPI_REGISTERED, NPI_VALID_OTHER, SYN, active_npi_rows,
    curated_by_oid, issues_for, make_rows, rolled_back_db,
    run_quality_and_curation, seed_entity, seed_intake,
)

ANALYST = "analyst@example.test"


async def _decision_events(db, entity_id, itype="npi"):
    return (await db.execute(
        select(tm.TefcaIdentifierDecisionEvent).where(
            tm.TefcaIdentifierDecisionEvent.entity_id == entity_id,
            tm.TefcaIdentifierDecisionEvent.identifier_type == itype)
        .order_by(tm.TefcaIdentifierDecisionEvent.sequence))).scalars().all()


async def _versions(db, entity_id):
    return (await db.execute(
        select(reg.TefcaEntityVersion).where(reg.TefcaEntityVersion.entity_id == entity_id)
        .order_by(reg.TefcaEntityVersion.version_number))).scalars().all()


async def _entity_state(db, entity_id):
    e = await db.get(reg.TefcaRegEntity, entity_id)
    await db.refresh(e)
    return {"name": e.name, "current_version": e.current_version,
            "npi_rows": [(r.identifier_value, r.identifier_status)
                         for r in await active_npi_rows(db, entity_id)],
            "versions": len(await _versions(db, entity_id))}


# ── Scenario B and its length/format siblings ────────────────────────────────

@pytest.mark.parametrize("submitted, quality_type, quality_rule", [
    (NPI_BAD_CHECKSUM, "NPI_CHECKSUM_INVALID", "NPI-003"),
    ("198291607", "NPI_LENGTH_INVALID", "NPI-002"),
    ("19829160A8", "NPI_FORMAT_INVALID", "NPI-004"),
])
async def test_invalid_submitted_value_against_a_registered_npi_raises_both_findings(
        rolled_back_db, submitted, quality_type, quality_rule):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.21")
    rows[0]["NPI"] = submitted
    entity_id = await seed_entity(db, oid=rows[0]["id"], name=rows[0]["name"],
                                  npi=NPI_REGISTERED, tefcaid=rows[0]["TEFCAID"])
    before = await _entity_state(db, entity_id)
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    assert curated.record_status == "HELD", "the quality rule holds the record first"

    result = await promote_delivery(db, intake_id, actor=SYN)

    # Both findings, side by side, in the one ledger.
    issues = await issues_for(db, curated.source_record_id)
    by_type = {i.issue_type: i for i in issues}
    assert quality_type in by_type and by_type[quality_type].rule_id == quality_rule
    conflict = by_type["NPI_EXISTING_VALUE_CONFLICT"]
    assert conflict.rule_id == "NPI-008"
    assert conflict.severity == "HIGH"
    assert conflict.correction_authority == "HUMAN_REQUIRED"
    assert conflict.original_value == submitted
    assert conflict.suggested_value == NPI_REGISTERED
    assert conflict.field_name == "NPI"
    assert conflict.resolution == "OPEN"
    assert conflict.issue_code.startswith("PR-")
    assert conflict.run_id is not None, "written under the current quality run"

    # The decision event, with both values.
    events = await _decision_events(db, entity_id)
    assert len(events) == 1
    assert events[0].decision == "CONFLICT_RAISED"
    assert events[0].submitted_value == submitted
    assert events[0].existing_value == NPI_REGISTERED
    assert events[0].issue_id == conflict.id
    assert events[0].source_record_id == curated.source_record_id

    # Held, not promoted, entity untouched.
    await db.refresh(curated)
    assert curated.record_status == "HELD"
    assert curated.canonical_entity_id is None
    assert curated.npi == submitted, "the submitted value is preserved in Area 2"
    assert await _entity_state(db, entity_id) == before
    assert result["conflicts_raised"] == 1
    assert result["records_in_identifier_conflict"] == 1

    # Disposition: HELD, quality reason, both codes in the text.
    current = (await disp.current_for_intake(db, intake_id))[0]
    assert current["disposition"] == "HELD"
    assert current["reason_code"] == disp.REASON_HELD_QUALITY
    assert disp.REASON_HELD_QUALITY in current["reason"]
    assert disp.REASON_HELD_CONFLICT in current["reason"]
    assert await identifier_decisions.unresolved_for_intake(db, intake_id) == 1


async def test_a_rerun_does_not_duplicate_the_conflict(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.22")
    rows[0]["NPI"] = NPI_BAD_CHECKSUM
    entity_id = await seed_entity(db, oid=rows[0]["id"], name=rows[0]["name"],
                                  npi=NPI_REGISTERED)
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    again = await promote_delivery(db, intake_id, actor=SYN)

    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    conflicts = await issues_for(db, curated.source_record_id, rule_id="NPI-008")
    assert len(conflicts) == 1
    assert len(await _decision_events(db, entity_id)) == 1
    n_events = (await db.execute(
        select(func.count()).select_from(tm.RceDispositionEvent).where(
            tm.RceDispositionEvent.intake_id == intake_id))).scalar()
    assert n_events == 1
    assert again["conflicts_raised"] == 0


# ── a valid but different value: conflict is the only hold ───────────────────

async def _conflict_only_setup(db, arc):
    rows = make_rows(1, arc=arc)
    rows[0]["NPI"] = NPI_VALID_OTHER
    entity_id = await seed_entity(db, oid=rows[0]["id"], name=rows[0]["name"],
                                  npi=NPI_REGISTERED, tefcaid=rows[0]["TEFCAID"])
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    assert curated.record_status == "CLEAN"
    await promote_delivery(db, intake_id, actor=SYN)
    await db.refresh(curated)
    assert curated.record_status == "HELD" and curated.canonical_entity_id is None
    conflict = (await issues_for(db, curated.source_record_id, rule_id="NPI-008"))[0]
    current = (await disp.current_for_intake(db, intake_id))[0]
    assert (current["disposition"], current["reason_code"]) == ("HELD", disp.REASON_HELD_CONFLICT)
    return rows, entity_id, intake_id, job, curated, conflict


async def test_valid_different_value_is_held_by_the_conflict_alone(rolled_back_db):
    db = rolled_back_db
    rows, entity_id, intake_id, job, curated, conflict = await _conflict_only_setup(
        db, "9.99.777.23")
    assert "identifier conflict" in (curated.status_reason or "")
    assert conflict.issue_type == "NPI_EXISTING_VALUE_CONFLICT"
    assert conflict.original_value == NPI_VALID_OTHER
    assert conflict.suggested_value == NPI_REGISTERED
    # Reconciliation sees the hold as legitimate: the record is HELD.
    recon = await reconciliation.reconcile_delivery(db, intake_id)
    check = next(c for c in recon["checks"]
                 if c["check"].startswith("Unresolved identifier conflicts"))
    assert check["passed"] is True
    assert recon["identifier_conflicts"] == {"unresolved": 1, "not_held": 0}


async def test_confirm_existing_releases_without_changing_the_registry(rolled_back_db):
    db = rolled_back_db
    rows, entity_id, intake_id, job, curated, conflict = await _conflict_only_setup(
        db, "9.99.777.24")
    before = await _entity_state(db, entity_id)

    out = await curation.apply_disposition(
        db, conflict.id, decision="CONFIRM_EXISTING",
        reason="Registered NPI confirmed against NPPES; delivered value is a typo.",
        actor=ANALYST)

    assert out["identifier_decision"]["decision"] == "CONFIRM_EXISTING"
    assert out["identifier_decision"]["selected_value"] == NPI_REGISTERED
    assert out["registry_changed"] is False
    assert out["resolution"] == "RESOLVED"
    assert out["record_status_before"] == "HELD"
    assert out["record_status_after"] == "CLEAN"
    assert out["re_promoted"] is True
    assert out["snapshot_id"] is not None

    await db.refresh(curated)
    assert curated.canonical_entity_id == entity_id
    assert curated.npi == NPI_VALID_OTHER, "Area 2 keeps the submitted value"
    assert await _entity_state(db, entity_id) == before, "registry untouched"

    history = await disp.history_for_record(db, curated.source_record_id)
    assert [(h["sequence"], h["disposition"], h["actor_type"]) for h in history] == [
        (1, "HELD", "SYSTEM"), (2, "MATCHED_UNCHANGED", "HUMAN")]
    assert history[1]["reason_code"] == disp.REASON_ANALYST
    assert "CONFIRM_EXISTING" in history[1]["reason"]
    assert out["disposition_event"]["sequence"] == 2
    assert out["hold_released"] is True
    assert history[1]["actor"] == ANALYST

    events = await _decision_events(db, entity_id)
    assert [e.decision for e in events] == ["CONFLICT_RAISED", "CONFIRM_EXISTING"]
    assert await identifier_decisions.unresolved_for_intake(db, intake_id) == 0

    snapshot = await reconciliation.latest_snapshot(db, job.id)
    assert snapshot.trigger == "DISPOSITION" and snapshot.actor == ANALYST
    assert snapshot.passed is True, snapshot.failure_reason

    # A further promotion raises nothing new: the decision stands.
    again = await promote_delivery(db, intake_id, actor=SYN)
    assert again["conflicts_raised"] == 0
    assert len(await _decision_events(db, entity_id)) == 2


async def test_confirm_submitted_writes_identifier_version_and_audit(rolled_back_db):
    db = rolled_back_db
    rows, entity_id, intake_id, job, curated, conflict = await _conflict_only_setup(
        db, "9.99.777.25")

    out = await curation.apply_disposition(
        db, conflict.id, decision="CONFIRM_SUBMITTED",
        reason="NPPES shows the organisation under the delivered NPI.",
        actor=ANALYST)

    assert out["identifier_decision"]["decision"] == "CONFIRM_SUBMITTED"
    assert out["identifier_decision"]["selected_value"] == NPI_VALID_OTHER
    assert out["registry_changed"] is True
    assert out["resolution"] == "RESOLVED"
    assert out["re_promoted"] is True

    npi_rows = {r.identifier_value: r.identifier_status
                for r in await active_npi_rows(db, entity_id)}
    assert npi_rows == {NPI_REGISTERED: "superseded", NPI_VALID_OTHER: "active"}, \
        "old row retired, new row active, nothing deleted"
    versions = await _versions(db, entity_id)
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[1].change_reason == "identifier_confirmed_submitted"
    assert versions[1].snapshot_data["previous_value"] == NPI_REGISTERED
    assert versions[1].snapshot_data["new_value"] == NPI_VALID_OTHER
    audit = (await db.execute(
        select(reg.TefcaRegAuditLog).where(
            reg.TefcaRegAuditLog.entity_id == entity_id,
            reg.TefcaRegAuditLog.action == "identifier_changed"))).scalars().all()
    assert len(audit) == 1 and audit[0].actor_email == ANALYST
    events = await _decision_events(db, entity_id)
    assert events[-1].decision == "CONFIRM_SUBMITTED"
    assert events[-1].version_id == versions[1].id

    await db.refresh(curated)
    assert curated.canonical_entity_id == entity_id
    history = await disp.history_for_record(db, curated.source_record_id)
    assert [h["disposition"] for h in history] == ["HELD", "MATCHED_UNCHANGED"]
    # Re-promotion compares the submitted value with the NEW active row: equal.
    again = await promote_delivery(db, intake_id, actor=SYN)
    assert again["conflicts_raised"] == 0


async def test_a_decision_without_a_reason_is_refused(rolled_back_db):
    db = rolled_back_db
    rows, entity_id, intake_id, job, curated, conflict = await _conflict_only_setup(
        db, "9.99.777.26")
    with pytest.raises(ValueError):
        await curation.apply_disposition(db, conflict.id, decision="CONFIRM_EXISTING",
                                         reason="   ", actor=ANALYST)
    with pytest.raises(ValueError):
        await curation.apply_disposition(db, conflict.id, decision="APPROVE",
                                         reason="x", actor=ANALYST)
    with pytest.raises(ValueError):
        await curation.apply_disposition(db, conflict.id, decision="CORRECT",
                                         reason="x", actor=ANALYST)


async def test_defer_keeps_the_hold_and_writes_no_disposition(rolled_back_db):
    db = rolled_back_db
    rows, entity_id, intake_id, job, curated, conflict = await _conflict_only_setup(
        db, "9.99.777.27")
    out = await curation.apply_disposition(
        db, conflict.id, decision="DEFER", reason="Awaiting QHIN response.",
        actor=ANALYST)
    assert out["identifier_decision"]["decision"] == "DEFERRED"
    assert out["resolution"] == "UNDER_REVIEW"
    assert out["record_status_after"] == "HELD"
    assert out["re_promoted"] is False
    assert out["hold_released"] is False
    assert out["disposition_event"] is None and out["snapshot_id"] is None
    history = await disp.history_for_record(db, curated.source_record_id)
    assert len(history) == 1
    assert await identifier_decisions.unresolved_for_intake(db, intake_id) == 0, \
        "DEFERRED is a decision; the latest event is no longer CONFLICT_RAISED"
    # ...but the issue is still undecided, so the record stays held.
    assert (await curation.release_check(db, intake_id)) == []


async def test_recompute_hold_status_holds_on_an_unresolved_conflict_alone(rolled_back_db):
    db = rolled_back_db
    rows, entity_id, intake_id, job, curated, conflict = await _conflict_only_setup(
        db, "9.99.777.28")
    # Resolve the ISSUE directly (as an out-of-band tool might) but leave the
    # decision event at CONFLICT_RAISED: the conflict still holds the record.
    conflict.resolution = "RESOLVED"
    await db.commit()
    out = await curation.recompute_hold_status(db, intake_id)
    assert out["still_held"] == 1 and out["held_by_conflict_only"] == 1
    await db.refresh(curated)
    assert curated.record_status == "HELD"


# ── a valid delivered NPI for an entity that has none is written, not dropped ─

async def test_a_valid_npi_for_an_entity_without_one_is_registered_as_an_update(rolled_back_db):
    """No conflict exists when the registry holds no NPI; the delivered value is
    a material addition. It must become an active identifier row, the match
    must be accounted UPDATED (never MATCHED_UNCHANGED), and a version row must
    record it. Dropping it silently would be the loss this remediation exists
    to prevent."""
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.31")
    rows[0]["NPI"] = NPI_VALID_OTHER
    entity_id = await seed_entity(db, oid=rows[0]["id"], name=rows[0]["name"],
                                  npi=None, tefcaid=rows[0]["TEFCAID"])
    assert await active_npi_rows(db, entity_id) == []
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    assert curated.record_status in ("CLEAN", "CORRECTED"), curated.status_reason

    result = await promote_delivery(db, intake_id, actor=SYN)

    assert result["conflicts_raised"] == 0
    npi_rows = await active_npi_rows(db, entity_id)
    assert [(r.identifier_value, r.identifier_status) for r in npi_rows] == \
        [(NPI_VALID_OTHER, "active")]
    current = (await disp.current_for_intake(db, intake_id))[0]
    assert current["disposition"] == "UPDATED"
    assert "identifier:npi" in current["changed_fields"]
    versions = await _versions(db, entity_id)
    assert len(versions) == 2
    assert versions[-1].snapshot_data["identifiers_added"] == {"npi": NPI_VALID_OTHER}
    events = await _decision_events(db, entity_id)
    assert events == [], "no conflict is raised when nothing was registered"
    await db.refresh(curated)
    assert curated.canonical_entity_id == entity_id
