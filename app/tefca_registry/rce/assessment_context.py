"""The assessment date a classification must use, taken from the delivery's own run.

WHY THIS EXISTS
    `quality_engine.run_quality_engine` already supports a pinned
    `assessment_as_of` (D-12) and records the effective date on the run
    (`rce_ingestion_runs.assessment_metadata`). The two Treatment producer calls
    (bulk `arc_pipeline.verify_and_classify`, single-record
    `review_service.run_review`) instead read `datetime.now(UTC).date()`, so a
    re-classification of a delivery assessed as of 2026-11-08 would silently be
    assessed as of whatever day it happened to be run. Date-gated rules
    (BR-NPI-TRTMNT-001) make that a different answer for the same inputs.

WHAT IT DOES
    Resolves the date from the run belonging to THE SAME INTAKE:
      1. an explicit `run_id` is accepted only if that run's
         `source_intake_id` equals the intake — a run of another delivery is a
         refusal (ValueError), never a quiet substitution;
      2. otherwise the intake's CURRENT run, via the existing
         `run_selection.current_run` (most recently COMPLETED run for that
         intake) — never "the latest run in the table";
      3. no completed run, or no intake, falls back to today's UTC date and
         SAYS SO in `source` — today's behaviour, now labelled instead of
         silent.
    Nothing here writes. Nothing hardcodes a date: the authorised October
    assessment's 2026-11-08 is simply what that run's metadata records, and a
    future delivery gets its own run's date.

HISTORY IS NOT REWRITTEN
    This only chooses the input for NEW classification calls. It never edits a
    stored determination, a run row or a ReviewRecord.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

SOURCE_RUN_METADATA = "run_metadata"
SOURCE_RUN_STARTED_AT = "run_started_at_unannotated"
SOURCE_FALLBACK_NO_INTAKE = "fallback_utc_today_no_intake"
SOURCE_FALLBACK_NO_RUN = "fallback_utc_today_no_completed_run"


@dataclass(frozen=True)
class AssessmentDate:
    """The date, where it came from, and which run (if any) supplied it."""
    value: date
    source: str
    run_id: Optional[str] = None
    intake_id: Optional[str] = None

    @property
    def from_run(self) -> bool:
        return self.run_id is not None

    def as_dict(self) -> dict:
        return {"assessment_date": self.value.isoformat(), "source": self.source,
                "run_id": self.run_id, "intake_id": self.intake_id}


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _date_of(run: Any) -> AssessmentDate:
    """The date a specific run assessed as of: its recorded metadata when
    present and parseable, else the run's own start date (what the engine used
    before D-12 annotated runs), labelled as such."""
    meta = getattr(run, "assessment_metadata", None) or {}
    raw = meta.get("assessment_date") if isinstance(meta, dict) else None
    if raw:
        try:
            return AssessmentDate(date.fromisoformat(str(raw)), SOURCE_RUN_METADATA,
                                  str(run.id), str(run.source_intake_id))
        except ValueError:
            pass  # unparseable metadata is not trusted; fall through, labelled
    return AssessmentDate(run.started_at.date(), SOURCE_RUN_STARTED_AT,
                          str(run.id), str(run.source_intake_id))


async def resolve_assessment_date(db, intake_id: Any, *,
                                  run_id: Any = None) -> AssessmentDate:
    """See module docstring. Raises ValueError for a run that does not exist or
    does not belong to `intake_id`."""
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import run_selection

    intake_uuid = _as_uuid(intake_id)
    run_uuid = _as_uuid(run_id)

    if run_uuid is not None:
        if intake_uuid is None:
            raise ValueError(
                "assessment run_id given without an intake: the run/intake "
                "linkage cannot be validated, so the run is refused")
        run = await db.get(m.RceIngestionRun, run_uuid)
        if run is None:
            raise ValueError(f"No ingestion run {run_uuid}")
        if run.source_intake_id != intake_uuid:
            raise ValueError(
                f"ingestion run {run_uuid} belongs to intake "
                f"{run.source_intake_id}, not {intake_uuid}; refusing to take "
                f"an assessment date from an unrelated run")
        if run.run_status != run_selection.COMPLETED_STATUS:
            raise ValueError(
                f"ingestion run {run_uuid} is {run.run_status}, not "
                f"{run_selection.COMPLETED_STATUS}; an unfinished or failed "
                f"run does not supply an assessment date")
        return _date_of(run)

    if intake_uuid is None:
        return AssessmentDate(_utc_today(), SOURCE_FALLBACK_NO_INTAKE)

    run = await run_selection.current_run(db, intake_uuid)
    if run is None:
        return AssessmentDate(_utc_today(), SOURCE_FALLBACK_NO_RUN, None,
                              str(intake_uuid))
    return _date_of(run)
