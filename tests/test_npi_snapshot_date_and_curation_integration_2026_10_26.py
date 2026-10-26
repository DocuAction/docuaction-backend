"""Integration (database-backed): snapshot-date provenance and the non-holding NPI observation
through the REAL quality engine and curation — not the pure module.

Skips without DATABASE_URL (conftest `db_required`); CI job `isolation-postgres` runs it.

Scenario required by the 2026-10-26 instruction: a delivery whose snapshot is 2026-09-02, received
(registrant-stated) after 2026-09-03 and ingested/assessed in October, must be assessed PRE-DEADLINE
for the SOP §7 requirement — and only because an authorised operator recorded the snapshot date with
its basis. Without that record the deadline applicability stays unresolved: receipt date, ingestion
date, assessment date and file name are never substituted.
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import snapshot_date as sd
from app.tefca_registry.rce.curation import CLEAN, HELD, curate_delivery
from app.tefca_registry.rce.quality_engine import run_quality_engine
from rce_traceability_support import (  # noqa: F401  (fixture registered by import)
    ARC, SYN, curated_by_oid, issues_for, make_rows, rolled_back_db, seed_intake, source_by_oid)

pytestmark = pytest.mark.asyncio

PM = SimpleNamespace(id="00000000-0000-0000-0000-00000000aa01", email="pm@synthetic.invalid", role="program_manager")
VALID = "1982916078"
BAD_CHECKSUM = "1982916079"


async def _seed(db, *, received: datetime, snapshot: date | None):
    rows = make_rows(4, arc=f"{ARC}.26")
    # row 1: case 1 — T-TRTMNT, blank NPI, blank role (observation only)
    rows[0].update({"purposesofuse": "T-TRTMNT", "NPI": "", "hl7orgrole": ""})
    # row 2: T-TRTMNT with a checksum-invalid NPI — identifier validity still holds
    rows[1].update({"purposesofuse": "T-TRTMNT", "NPI": BAD_CHECKSUM, "hl7orgrole": ""})
    # row 3: case 1 PLUS an independent CRITICAL defect (no name) — that hold must survive
    rows[2].update({"purposesofuse": "T-TRTMNT", "NPI": "", "hl7orgrole": "", "name": ""})
    # row 4: case 5 — no purpose, no NPI (review required, not a hold)
    rows[3].update({"purposesofuse": "", "NPI": "", "hl7orgrole": ""})
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    intake = await db.get(m.RceSourceIntake, intake_id)
    intake.received_at = received            # registrant-stated receipt date, AFTER the §7 date
    if snapshot is not None:
        sd.record_snapshot_date(db, intake, snapshot_date=snapshot, user=PM,
                                basis="Synthetic test: publisher extract represents the directory as of "
                                      f"{snapshot.isoformat()} (basis text recorded verbatim)")
    await db.flush()
    return intake_id, rows


async def test_snapshot_sept_2_received_after_sept_3_assessed_later_is_pre_deadline(rolled_back_db):
    db = rolled_back_db
    intake_id, rows = await _seed(db, received=datetime(2026, 9, 24), snapshot=date(2026, 9, 2))
    await run_quality_engine(db, intake_id, executed_by=SYN)
    src = await source_by_oid(db, intake_id, rows[0]["id"])
    [finding] = await issues_for(db, src.id, rule_id="NPI-001")
    assert finding.issue_type == "NPI_REQUIRED" and finding.severity == "HIGH"
    d = finding.description
    assert "case 1" in d and "NPI=missing" in d
    assert "snapshot=2026-09-02" in d, d
    assert "deadline=pre_deadline" in d, d
    assert "governing date=2026-09-03" in d
    assert "assessed=2026-09-02" not in d and "assessed=2026-09-24" not in d, "assessment date is the run date, not the snapshot or receipt date"
    assert "no compliance determination" in d


async def test_without_a_recorded_snapshot_date_the_deadline_stays_unresolved(rolled_back_db):
    db = rolled_back_db
    intake_id, rows = await _seed(db, received=datetime(2026, 9, 2), snapshot=None)   # receipt date alone proves nothing
    await run_quality_engine(db, intake_id, executed_by=SYN)
    src = await source_by_oid(db, intake_id, rows[0]["id"])
    [finding] = await issues_for(db, src.id, rule_id="NPI-001")
    assert "snapshot=missing" in finding.description and "deadline=unresolved" in finding.description
    assert "2026-09-02" not in finding.description.split("snapshot=")[1].split(";")[0]


async def test_snapshot_date_recording_is_audited_and_bounded_by_receipt(rolled_back_db):
    from sqlalchemy import select
    from app.tefca_registry import models as reg
    db = rolled_back_db
    intake_id, _ = await _seed(db, received=datetime(2026, 9, 24), snapshot=date(2026, 9, 2))
    intake = await db.get(m.RceSourceIntake, intake_id)
    assert intake.source_metadata["snapshot_date"] == "2026-09-02"
    assert intake.source_metadata["snapshot_date_recorded_by"] == PM.email
    assert intake.received_at == datetime(2026, 9, 24), "receipt date untouched"
    rows = (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == sd.AUDIT_ACTION,
        reg.TefcaRegAuditLog.metadata_["source_intake_id"].astext == str(intake_id)))).scalars().all()
    assert len(rows) == 1 and rows[0].metadata_["snapshot_date"] == "2026-09-02"
    with pytest.raises(sd.SnapshotDateRefused):
        sd.record_snapshot_date(db, intake, snapshot_date=date(2026, 9, 25), user=PM, basis="after receipt - must be refused")


async def test_curation_missing_npi_observation_does_not_hold_but_real_defects_still_do(rolled_back_db):
    db = rolled_back_db
    intake_id, rows = await _seed(db, received=datetime(2026, 9, 24), snapshot=date(2026, 9, 2))
    await run_quality_engine(db, intake_id, executed_by=SYN)
    await curate_delivery(db, intake_id, curated_by=SYN)

    case1 = await curated_by_oid(db, intake_id, rows[0]["id"])
    assert case1.record_status == CLEAN, case1.status_reason
    src1 = await source_by_oid(db, intake_id, rows[0]["id"])
    assert [i.issue_type for i in await issues_for(db, src1.id, rule_id="NPI-001")] == ["NPI_REQUIRED"], "observation stays open"

    malformed = await curated_by_oid(db, intake_id, rows[1]["id"])
    assert malformed.record_status == HELD and "NPI-003" in (malformed.status_reason or "")
    src2 = await source_by_oid(db, intake_id, rows[1]["id"])
    types = {i.rule_id: i.issue_type for i in await issues_for(db, src2.id)}
    assert types.get("NPI-003") == "NPI_CHECKSUM_INVALID" and "NPI-001" not in types, "validity finding is independent of presence"

    held_for_name = await curated_by_oid(db, intake_id, rows[2]["id"])
    assert held_for_name.record_status == HELD
    assert "REQ-002" in (held_for_name.status_reason or "") and "NPI-001" not in (held_for_name.status_reason or ""), \
        "the independent CRITICAL hold is preserved and attributed to its own rule"

    case5 = await curated_by_oid(db, intake_id, rows[3]["id"])
    assert case5.record_status == CLEAN
    src4 = await source_by_oid(db, intake_id, rows[3]["id"])
    [f] = await issues_for(db, src4.id, rule_id="NPI-001")
    assert f.issue_type == "NPI_APPLICABILITY_UNRESOLVED" and f.severity == "MEDIUM", "visible to analysts as a review item"
