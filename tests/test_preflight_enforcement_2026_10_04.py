"""Preflight enforcement (ENABLE_PREFLIGHT_ENFORCEMENT) -- Round 22, Part A.

Four cases, each against a real delivery_runner._run_after_area1 call over
a seeded synthetic intake (never a delivered Government row -- see
rce_traceability_support's own module docstring):

    flag off (default)             PREFLIGHT never appears in the stage
                                    timeline at all; the pipeline is
                                    byte-for-byte what it was before this
                                    round (test_delivery_runner_events.py's
                                    existing assertions are unmodified and
                                    still pass -- see that file).
    flag on, clean delivery        PREFLIGHT completes normally
                                    (GATE_CLEAR); the rest of the pipeline
                                    runs exactly as the flag-off case.
    flag on, bad id shape           a per-record PF-OID-001 finding
                                    (DISP_OPEN) produces GATE_CLEAR_WITH_
                                    FINDINGS: PREFLIGHT is recorded `held`,
                                    not failed, and QUALITY/CURATION/
                                    PROMOTION still run -- "hold affected
                                    records... continue independent
                                    supported checks."
    flag on, missing 41-field column  the delivery-level PF-SCH-001
                                    finding (DISP_BLOCKED) produces
                                    GATE_BLOCKED: the job fails AT
                                    PREFLIGHT and no later stage runs --
                                    "block an entire delivery only when
                                    parsing, completeness or identity
                                    cannot be trusted."
"""
from __future__ import annotations

from app.core.config import settings
from app.tefca_registry.rce import delivery_runner, stage_events
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_intake  # noqa: F401


async def _run_recoverable(db, job, intake_id, n):
    received = {"intake_id": str(intake_id), "record_count": n, "records_stored": n,
                "parse_ok": n, "parse_malformed": 0, "schema_drift": False,
                "every_line_stored": True}
    detail = {}
    for stage in ("SCHEMA_VALIDATION", "PARSING"):
        await stage_events.record_instant(db, job.id, stage, intake_id=intake_id,
                                          input_count=n, output_count=n)
    state = await delivery_runner._run_after_area1(db, job, detail, intake_id,
                                                   received, SYN)
    return state, detail


async def test_flag_off_by_default_preflight_never_appears_in_the_timeline(rolled_back_db):
    db = rolled_back_db
    assert settings.ENABLE_PREFLIGHT_ENFORCEMENT is False  # the documented default

    rows = make_rows(2, arc="9.99.777.81")
    intake_id, job = await seed_intake(db, rows)

    state, _ = await _run_recoverable(db, job, intake_id, 2)
    assert state == RceDeliveryJob.STATE_SUCCEEDED

    events = await stage_events.timeline(db, job.id)
    stages = [e["stage"] for e in events]
    assert "PREFLIGHT" not in stages, stages
    assert stages == ["SCHEMA_VALIDATION", "PARSING", "QUALITY", "CURATION", "PROMOTION",
                      "MATCHING", "RELATIONSHIPS", "VERIFICATION_READINESS",
                      "RECONCILIATION", "READY_FOR_REVIEW"], stages


async def test_flag_on_clean_delivery_does_not_block_and_the_pipeline_continues(
        rolled_back_db, monkeypatch):
    """A synthetic fixture with every REQUIRED field populated still carries
    some CONDITIONAL_BLANK findings by construction (blank NPI, blank
    hl7orgrole -- the exact "do not assume every field is knowable" case the
    contract itself calls out, see docs/review/DELTA-2026-10-04.md section 1)
    -- so this does not assert GATE_CLEAR specifically, only that a
    non-untrustworthy delivery is never BLOCKED and the pipeline proceeds
    past PREFLIGHT regardless of which non-blocking gate it lands on."""
    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_PREFLIGHT_ENFORCEMENT", True)

    rows = make_rows(2, arc="9.99.777.82")
    intake_id, job = await seed_intake(db, rows)

    state, detail = await _run_recoverable(db, job, intake_id, 2)
    assert state == RceDeliveryJob.STATE_SUCCEEDED

    events = await stage_events.timeline(db, job.id)
    by_stage = {e["stage"]: e for e in events}
    assert by_stage["PREFLIGHT"]["status"] in ("COMPLETED", "SKIPPED")
    # The rest of the pipeline ran regardless of PREFLIGHT's own status.
    assert by_stage["QUALITY"]["status"] == "COMPLETED"
    assert by_stage["PROMOTION"]["output_count"] == 2


async def test_flag_on_bad_id_shape_holds_preflight_but_the_pipeline_continues(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_PREFLIGHT_ENFORCEMENT", True)

    rows = make_rows(2, arc="9.99.777.83")
    rows[0]["id"] = "not-an-oid-or-uuid"  # triggers PF-OID-001, disposition=open
    intake_id, job = await seed_intake(db, rows)

    state, detail = await _run_recoverable(db, job, intake_id, 2)
    assert state == RceDeliveryJob.STATE_SUCCEEDED, (state, detail.get("PREFLIGHT"))

    events = await stage_events.timeline(db, job.id)
    by_stage = {e["stage"]: e for e in events}
    # Held, not failed -- the same shape _stage_promotion already uses when
    # it declines rather than fails (see that function's docstring).
    assert by_stage["PREFLIGHT"]["status"] == "SKIPPED"
    assert by_stage["PREFLIGHT"]["detail"]["held"] is True
    assert "open findings" in (by_stage["PREFLIGHT"].get("failure_reason") or "")
    # "Continue independent supported checks": every later stage still ran.
    assert by_stage["QUALITY"]["status"] == "COMPLETED"
    assert by_stage["CURATION"]["status"] == "COMPLETED"
    assert by_stage["PROMOTION"]["output_count"] == 2
    assert by_stage["RECONCILIATION"]["detail"]["passed"] is True


async def test_flag_on_missing_41_field_column_blocks_the_whole_delivery(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_PREFLIGHT_ENFORCEMENT", True)

    rows = make_rows(2, arc="9.99.777.84")
    intake_id, job = await seed_intake(db, rows)

    # Simulate a delivery missing one of the locked 41 columns -- mutate the
    # persisted header list the same way a genuinely short-columned delivery
    # would have been ingested, rather than inventing a new fixture shape.
    from app.tefca_registry.rce import models as m
    intake = await db.get(m.RceSourceIntake, intake_id)
    intake.headers = [h for h in intake.headers if h != "CCN"]
    await db.commit()

    state, detail = await _run_recoverable(db, job, intake_id, 2)
    assert state == RceDeliveryJob.STATE_FAILED, (state, detail)

    events = await stage_events.timeline(db, job.id)
    by_stage = {e["stage"]: e for e in events}
    assert by_stage["PREFLIGHT"]["status"] == "FAILED"
    # "Block an entire delivery": no later stage ran at all.
    assert "QUALITY" not in by_stage, by_stage.keys()
    assert "CURATION" not in by_stage, by_stage.keys()
    assert "PROMOTION" not in by_stage, by_stage.keys()
