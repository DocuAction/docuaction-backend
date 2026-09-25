"""Official ONC/RCE delivery job lifecycle — durable state, database-enforced.

This module owns ONE question: has this delivery been registered, is it
processing, did it finish. It does not parse, does not hash bytes, does not know
what a rule is and does not decide what a record status means. Every one of
those already has an owner in `app/tefca_registry/rce/`, and this hands the work
to them.

THE SHAPE IS `reports/data/export_jobs.py`, DELIBERATELY
────────────────────────────────────────────────────────
Same three-outcome `request_job`, same `FOR UPDATE SKIP LOCKED` claim, same
heartbeat, same reaper. That module in turn took the shape from
`Tefca/ppef_jobs.py`. Three kinds of long work now run the same discipline, and
an operator who has read one queue can read all three.

WHAT A RE-DELIVERY DOES
───────────────────────
`intake.ingest_delivery` accepts byte-identical re-deliveries as their own
intake and links them to the earlier one, because ONC may legitimately resend
and a rejected re-delivery would leave no record that it arrived. That behaviour
is preserved exactly. What the partial unique index prevents is narrower and
different: two registrations of the SAME bytes under the SAME label while one is
still in flight — a double-click, a refresh, a second browser tab. Once a job is
terminal its `active_marker` is NULL, the index no longer applies, and a genuine
re-delivery registers normally.

TWO-AXIS STATUS (2026-09-17)
────────────────────────────
`state` and `stage` are what the worker wrote. `status_for_job` derives the
PROCESSING OUTCOME and the REVIEW STATE from persisted evidence — the latest
reconciliation snapshot, the stage events, the open findings, the unresolved
identifier conflicts and the review records — through `status_model`, which is
the only place that vocabulary lives. `to_dict()` stays cheap and unchanged;
routes call `status_for_job` when they want the derived view.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


class DeliveryJobConflict(RuntimeError):
    """A registration for these bytes is already in flight."""


#: How long a job may go without writing a heartbeat before the reaper decides
#: the worker is gone. Generous: a 100K-record delivery legitimately spends a
#: long time inside one stage, and killing a slow but healthy ingestion would be
#: far worse than leaving a dead one visible for another few minutes. The runner
#: heartbeats between stages AND inside the long ones.
STALE_HEARTBEAT_SECONDS = 1800

#: Written by the runner while work is in flight.
HEARTBEAT_INTERVAL_SECONDS = 20

REAPED_REASON = "worker_stopped_without_reporting"


def job_identity(*, sha256: str, delivery_label: Optional[str],
                 received_date: Optional[datetime]) -> str:
    """What makes two registrations the SAME registration.

    The bytes, the label the operator gave them, and the receipt date. Not the
    registering operator and not the moment: two people registering the
    September delivery a minute apart have registered ONE delivery, and giving
    them two would put two Area 1 intakes of identical content into the evidence
    store for one arrival.

    The receipt date is in here because it is a real distinction. The same file
    genuinely received twice — ONC resending in October what it sent in
    September — is two deliveries, and recording it as one would lose the second
    arrival.
    """
    material = "|".join([
        sha256,
        (delivery_label or "").strip().lower(),
        received_date.date().isoformat() if received_date else "",
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def active_job(db, identity: str):
    """The in-flight job for this identity, if there is one. Read-only."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    return (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.identity == identity,
               RceDeliveryJob.active_marker.is_(True)))).scalars().first()


async def request_job(db, *, identity: str, original_filename: str,
                      storage_path: str, sha256: str, file_size_bytes: int,
                      registered_by: str,
                      delivery_label: Optional[str] = None,
                      declared_delimiter: Optional[str] = None,
                      received_date: Optional[datetime] = None,
                      government_reference: Optional[str] = None,
                      notes: Optional[str] = None,
                      source_name: Optional[str] = None) -> Any:
    """Return the job for this identity, creating one only if none is active.

    THE THREE OUTCOMES, AND WHY EACH IS RIGHT

      * an ACTIVE job exists — return it. A second click, a refresh, and a poll
        that arrives before the first request committed all land here, and all
        three want the same answer: "it is being processed".
      * no active job — create one QUEUED.
      * two callers race — the database decides. The partial unique index on
        (identity, active_marker) has no window; a check-then-insert does. The
        loser catches IntegrityError and re-reads the winner's row, so both
        callers get a job back and there is still only one.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    existing = await active_job(db, identity)
    if existing is not None:
        return existing

    now = datetime.utcnow()
    job = RceDeliveryJob(
        identity=identity,
        delivery_label=delivery_label,
        original_filename=original_filename,
        storage_path=storage_path,
        sha256=sha256,
        file_size_bytes=file_size_bytes,
        declared_delimiter=declared_delimiter,
        received_date=received_date,
        government_reference=government_reference,
        notes=notes,
        source_name=source_name,
        state=RceDeliveryJob.STATE_QUEUED,
        stage=RceDeliveryJob.STAGE_ACCEPTED,
        active_marker=True,
        registered_by=registered_by,
        created_at=now,
        heartbeat_at=now,
        attempt_count=0,
        stage_detail={},
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await active_job(db, identity)
        if winner is None:
            # The index refused the insert but nothing active is there to find.
            # Something other than a race is wrong; say so rather than loop.
            raise DeliveryJobConflict(
                "This delivery was refused as a duplicate registration, but no "
                "active job could be read back.")
        return winner
    await db.refresh(job)
    return job


async def claim_next_queued(db):
    """Take the oldest QUEUED job and move it to RUNNING.

    `with_for_update(skip_locked=True)` is what makes this safe for more than
    one poller: two claimers lock different rows rather than both picking up the
    same delivery. One worker today; correct regardless.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.state == RceDeliveryJob.STATE_QUEUED)
        .order_by(RceDeliveryJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True))).scalar_one_or_none()
    if job is None:
        return None
    now = datetime.utcnow()
    job.state = RceDeliveryJob.STATE_RUNNING
    job.started_at = now
    job.heartbeat_at = now
    job.attempt_count = (job.attempt_count or 0) + 1
    await db.commit()
    await db.refresh(job)
    return job


async def _running_job(db, job_id, *, act: str):
    """The job, ONLY if it is still RUNNING. None otherwise, and it says why.

    WHY EVERY WRITE GOES THROUGH THIS
    ─────────────────────────────────
    The reaper fails a job whose heartbeat went stale. But a stale heartbeat is
    not proof the worker is dead — it may be alive and deep inside a long stage.
    When that worker finally surfaces and calls `finish_succeeded`, an unguarded
    write would flip the reaped FAILED row back to SUCCEEDED, and worse, the
    reaper had already cleared `active_marker`, so a SECOND registration of the
    same delivery may have been accepted and may now be running in parallel.
    Two workers ingesting the same bytes into Area 1 is exactly the duplication
    the job table exists to prevent.

    So a job that is no longer RUNNING is never written to by a worker. The late
    worker's outcome is logged and discarded; the reaper's verdict stands. A
    delivery that genuinely completed under a reaped job is visible in Area 1
    regardless — `bind_intake` ran before the stages, so the intake id is on
    the row and the dashboard reads the truth from reconciliation.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = await db.get(RceDeliveryJob, job_id)
    if job is None:
        return None
    if job.state != RceDeliveryJob.STATE_RUNNING:
        logger.warning(
            "delivery job %s: %s refused — job is %s (%s), not RUNNING. A late "
            "worker does not overwrite a settled job.",
            job_id, act, job.state, job.error_reason or job.stage)
        return None
    return job


async def heartbeat(db, job_id, *, stage: Optional[str] = None,
                    records_received: Optional[int] = None,
                    records_processed: Optional[int] = None,
                    detail: Optional[Dict[str, Any]] = None) -> None:
    """Say the worker is still alive, and what it has actually observed.

    The stage is a real transition, and the counts are rows the stage itself
    counted. Nothing here is estimated: an operator watching a delivery is
    entitled to assume that a number on the screen was measured.
    """
    job = await _running_job(db, job_id, act="heartbeat")
    if job is None:
        return
    job.heartbeat_at = datetime.utcnow()
    if stage:
        job.stage = stage
    if records_received is not None:
        job.records_received = records_received
    if records_processed is not None:
        job.records_processed = records_processed
    if detail:
        # Replaced wholesale rather than mutated in place: SQLAlchemy does not
        # detect an in-place change to a JSONB dict, and a stage report that
        # silently fails to persist is worse than no stage report.
        merged = dict(job.stage_detail or {})
        merged.update(detail)
        job.stage_detail = merged
    await db.commit()


async def bind_intake(db, job_id, intake_id, *, records_received: int) -> None:
    """Record the Area 1 intake this job produced, as soon as it exists.

    Written in its own commit and as early as possible. From this moment the
    delivery is addressable through the existing `/deliveries/{intake_id}`
    surface even if every later stage fails — which is the point: a delivery
    whose Area 1 landed but whose quality run died must still be findable, not
    orphaned behind a FAILED job row.
    """
    job = await _running_job(db, job_id, act="bind_intake")
    if job is None:
        return
    job.source_intake_id = intake_id
    job.records_received = records_received
    job.heartbeat_at = datetime.utcnow()
    await db.commit()


async def finish_succeeded(db, job_id, *, reconciliation_passed: bool,
                           records_processed: Optional[int] = None,
                           detail: Optional[Dict[str, Any]] = None) -> None:
    """Terminal success: the pipeline ran to completion.

    SUCCEEDED means the pipeline RAN to completion, not that reconciliation
    passed. Those are different facts and they are reported separately:
    a delivery can process cleanly end-to-end and still fail the A–F gate, and
    collapsing the two would hide exactly the condition the gate exists to
    surface. The dashboard shows both.

    The STAGE says the same thing honestly: READY_FOR_REVIEW is written only
    when reconciliation passed. A job that finished but did not reconcile stays
    at RECONCILIATION — because that is where the delivery actually is — and the
    derived processing outcome reads Partially Processed.

    `active_marker` is cleared LAST and in the same commit: while it is set the
    partial unique index refuses another job for this identity, and clearing it
    is what makes a legitimate re-delivery possible.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = await _running_job(db, job_id, act="finish_succeeded")
    if job is None:
        return
    now = datetime.utcnow()
    job.state = RceDeliveryJob.STATE_SUCCEEDED
    job.stage = (RceDeliveryJob.STAGE_READY if reconciliation_passed
                 else RceDeliveryJob.STAGE_RECONCILIATION)
    job.completed_at = now
    job.heartbeat_at = now
    job.reconciliation_passed = reconciliation_passed
    if records_processed is not None:
        job.records_processed = records_processed
    if detail:
        merged = dict(job.stage_detail or {})
        merged.update(detail)
        job.stage_detail = merged
    job.active_marker = None
    await db.commit()


async def finish_failed(db, job_id, reason: str, *,
                        detail: Optional[Dict[str, Any]] = None) -> None:
    """Terminal failure, with a reason a person can act on.

    The intake binding is deliberately NOT cleared. If Area 1 landed before the
    failure, that Area 1 exists and is evidence; unbinding it here would leave a
    real intake with nothing pointing at it. The job says what failed and at
    which stage, and the delivery remains addressable.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = await _running_job(db, job_id, act="finish_failed")
    if job is None:
        return
    now = datetime.utcnow()
    job.state = RceDeliveryJob.STATE_FAILED
    job.failed_at = now
    job.heartbeat_at = now
    job.error_reason = (reason or "")[:2000]
    if detail:
        merged = dict(job.stage_detail or {})
        merged.update(detail)
        job.stage_detail = merged
    job.active_marker = None
    await db.commit()


async def reap_stale_jobs(db, threshold_seconds: int = STALE_HEARTBEAT_SECONDS
                          ) -> List[Dict[str, Any]]:
    """Fail jobs whose worker stopped saying anything - unless the work it was
    doing actually finished first.

    A process that dies cannot report that it died; the only signal it emits is
    silence. Usually that silence means FAILED. But a worker can die AFTER
    reconciliation ran and persisted a real, evidenced snapshot and BEFORE it
    wrote that outcome onto the job row (run 2026-09-17, job bb116cd7 - the
    delivery reconciled 7/7 and passed; the worker never got to say so).
    Declaring that a plain FAILED would discard completed work and contradict
    the snapshot sitting right next to it. So: if a snapshot already exists for
    this job, finalize it the same way the runner itself would have, and close
    out the stage event the dead worker left at STARTED. Only a job with no
    snapshot - one that genuinely never got that far - is marked FAILED.
    """
    from app.tefca_registry.rce import reconciliation, stage_events
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
    stale = (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.state.in_(RceDeliveryJob.ACTIVE_STATES),
               RceDeliveryJob.heartbeat_at < cutoff))).scalars().all()

    reaped = []
    for job in stale:
        snapshot = await reconciliation.latest_snapshot(db, job.id)
        if snapshot is not None:
            outcome = "recovered_succeeded"
            job.state = RceDeliveryJob.STATE_SUCCEEDED
            job.stage = (RceDeliveryJob.STAGE_READY if snapshot.passed
                         else RceDeliveryJob.STAGE_RECONCILIATION)
            job.completed_at = datetime.utcnow()
            job.reconciliation_passed = snapshot.passed
            job.active_marker = None
            await stage_events.close_dangling_started(
                db, job.id, "RECONCILIATION",
                detail={"note": "closed by the reaper: a passing snapshot "
                                 "already existed when the worker's "
                                 "heartbeat went stale"})
        else:
            outcome = "failed"
            job.state = RceDeliveryJob.STATE_FAILED
            job.failed_at = datetime.utcnow()
            job.error_reason = REAPED_REASON
            job.active_marker = None
        reaped.append({
            "job_id": str(job.id), "identity": job.identity, "state": job.state,
            "stage": job.stage, "registered_by": job.registered_by,
            "delivery_label": job.delivery_label,
            "intake_id": (str(job.source_intake_id)
                          if job.source_intake_id else None),
            "last_heartbeat": (job.heartbeat_at.isoformat()
                               if job.heartbeat_at else None),
            "outcome": outcome,
        })
    if reaped:
        await db.commit()
        logger.warning("reaped %d stale delivery job(s)", len(reaped))
    return reaped


async def get_job(db, job_id):
    """One job by id, or None. Reads only — polling must never start work."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    try:
        return await db.get(RceDeliveryJob, job_id)
    except Exception as exc:  # noqa: BLE001 — a malformed id is a 404, not a 500
        logger.info("delivery job lookup failed for %r: %s", job_id, exc)
        return None


async def list_jobs(db, *, limit: int = 50, state: Optional[str] = None,
                    offset: int = 0):
    """Recent registrations, newest first. `offset` pages through them; the
    order is total (created_at, then id) so pages never overlap or skip."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    stmt = (select(RceDeliveryJob)
            .order_by(RceDeliveryJob.created_at.desc(), RceDeliveryJob.id.desc()))
    if state:
        stmt = stmt.where(RceDeliveryJob.state == state)
    if offset:
        stmt = stmt.offset(offset)
    return (await db.execute(stmt.limit(limit))).scalars().all()


async def job_for_intake(db, intake_id):
    """The job that produced this Area 1 intake, if one did.

    A delivery ingested through the pre-existing synchronous route has no job,
    and that is not an error — it returns None and the dashboard reports the
    delivery from Area 1 alone.
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    return (await db.execute(
        select(RceDeliveryJob)
        .where(RceDeliveryJob.source_intake_id == intake_id)
        .order_by(RceDeliveryJob.created_at.desc()))).scalars().first()


# ── two-axis status, derived from evidence ───────────────────────────────────
#
# REVIEW COUNT MAPPING (documented here because status_model is pure and does
# not know the tables):
#
#   population   review_records tied to the delivery: rows whose entity_id is a
#                canonical entity of the intake's curated records, OR whose
#                verification_results->>'source_intake_id' names the intake
#                (DQ bridge cases, including pre-promotion cases with no entity)
#   open         reviewer_resolution IS NULL and assigned_to_user_id IS NULL
#   claimed      reviewer_resolution IS NULL and assigned_to_user_id IS NOT NULL
#   determined   reviewer_resolution IS NOT NULL (an analyst has decided)
#   qa_approved  reportable_at IS NOT NULL — only a QA APPROVE event sets it
#   qa_in_progress  determined, not yet approved, and the latest QA_REVIEW event
#                is RETURN or ESCALATE (QA has acted and the case is back in
#                motion)
#   qa_pending   determined, not yet approved, and no QA_REVIEW event yet
#   closed       never derived here: there is no delivery-level closure event.
#
# `determined_items` passed to status_model is the count of determined cases
# that are neither pending, in progress nor approved — with the mapping above
# that is always zero, and it is passed explicitly so the arithmetic is visible.

async def _review_counts(db, intake_id) -> Dict[str, int]:
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    if intake_id is None:
        return {"open": 0, "claimed": 0, "determined": 0, "qa_pending": 0,
                "qa_in_progress": 0, "qa_approved": 0, "total": 0}

    # Cases created AGAINST this delivery only (QA-039): an entity the
    # delivery contains may carry cases from other deliveries or queues, and
    # those are not this delivery's review work.
    scope = (reg.ReviewRecord.verification_results["source_intake_id"].astext
             == str(intake_id))
    rows = (await db.execute(
        select(reg.ReviewRecord.review_id, reg.ReviewRecord.assigned_to_user_id,
               reg.ReviewRecord.reviewer_resolution, reg.ReviewRecord.reportable_at,
               reg.ReviewRecord.verification_results["queue_source"].astext)
        .where(scope))).all()

    counts = {"open": 0, "claimed": 0, "determined": 0, "qa_pending": 0,
              "qa_in_progress": 0, "qa_approved": 0, "total": len(rows),
              "open_breakdown": {}}
    determined_ids = [r.review_id for r in rows
                      if r.reviewer_resolution is not None and r.reportable_at is None]
    latest_qa: Dict[str, Optional[str]] = {}
    if determined_ids:
        qa_rows = (await db.execute(text("""
            SELECT DISTINCT ON (review_id) review_id, qa_action
            FROM review_decision_events
            WHERE review_id = ANY(:ids) AND event_type = 'QA_REVIEW'
            ORDER BY review_id, sequence_number DESC"""),
            {"ids": determined_ids})).all()
        latest_qa = {rid: action for rid, action in qa_rows}
    for r in rows:
        if r.reportable_at is not None:
            counts["qa_approved"] += 1
        elif r.reviewer_resolution is not None:
            action = latest_qa.get(r.review_id)
            if action in ("RETURN", "ESCALATE"):
                counts["qa_in_progress"] += 1
            else:
                counts["qa_pending"] += 1
        elif r.assigned_to_user_id is not None:
            counts["claimed"] += 1
        else:
            counts["open"] += 1
            source = r[4] or "unknown"
            counts["open_breakdown"][source] = counts["open_breakdown"].get(source, 0) + 1
    return counts


async def _invalid_identifiers_promoted(db, intake_id) -> int:
    """Active NPI identifier rows of the intake's entities failing validate_npi."""
    from app.services.npi_validator import validate_npi
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    if intake_id is None:
        return 0
    promoted_ids = select(m.RceCuratedRecord.canonical_entity_id).where(
        m.RceCuratedRecord.source_intake_id == intake_id,
        m.RceCuratedRecord.canonical_entity_id.isnot(None))
    values = (await db.execute(
        select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.entity_id.in_(promoted_ids),
            reg.TefcaEntityIdentifier.identifier_type == "npi",
            reg.TefcaEntityIdentifier.identifier_status == "active"))).scalars().all()
    return sum(1 for v in values if not validate_npi(v)[0])


async def _failed_required_verification(db, intake_id) -> int:
    """Dimension evidence rows with disposition FAIL for the intake's entities.

    `tefca_dimension_evidence.entity_id` is a string column; the comparison is
    against the text form of the canonical entity ids. Zero when the table
    holds nothing for this delivery.
    """
    if intake_id is None:
        return 0
    try:
        return int((await db.execute(text("""
            SELECT count(*) FROM tefca_dimension_evidence e
            WHERE e.disposition = 'FAIL'
              AND e.entity_id IN (
                SELECT CAST(canonical_entity_id AS text) FROM rce_curated_records
                WHERE source_intake_id = CAST(:i AS uuid)
                  AND canonical_entity_id IS NOT NULL)"""),
            {"i": str(intake_id)})).scalar() or 0)
    except Exception as exc:  # noqa: BLE001 — an absent table is zero evidence, not an error
        logger.warning("failed-verification count unavailable: %s",
                       type(exc).__name__, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


async def _open_findings(db, intake_id) -> int:
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import run_selection

    if intake_id is None:
        return 0
    return int((await db.execute(
        select(func.count()).select_from(m.RceIssue).where(
            run_selection.issues_filter(intake_id),
            m.RceIssue.severity.in_(("CRITICAL", "HIGH")),
            m.RceIssue.resolution.in_(("OPEN", "PROPOSED", "UNDER_REVIEW"))))
    ).scalar() or 0)


async def status_for_job(db, job) -> Dict[str, Any]:
    """Assemble the inputs and derive `processing_outcome` and `review_state`.

    Every input is read from persisted rows: the latest snapshot for the job,
    the stage-event timeline, the current run's open HIGH/CRITICAL findings,
    the unresolved identifier conflicts, invalid identifiers that reached the
    registry, failed required verifications and the review records tied to the
    delivery. Nothing is recomputed from the job's own counters.

    Since 2026-09-25 this is the one-job case of `status_for_jobs`, which reads
    the same evidence with a bounded number of grouped queries. The per-row
    derivation is kept as `_status_for_job_reference` and the equivalence of
    the two is pinned by tests/test_delivery_jobs_list_perf.py.
    """
    return (await status_for_jobs(db, [job]))[0]


async def _status_for_job_reference(db, job) -> Dict[str, Any]:
    """The original per-row derivation (about eight queries per job).

    NOT used by any route. Retained as the oracle the bulk path is tested
    against, so a change to either that alters an outcome is caught.
    """
    from app.tefca_registry.rce import dispositions as disp
    from app.tefca_registry.rce import identifier_decisions, stage_events
    from app.tefca_registry.rce import reconciliation, status_model

    intake_id = job.source_intake_id
    snapshot = await reconciliation.latest_snapshot(db, job.id)
    snapshot_dict = snapshot.to_dict() if snapshot is not None else None
    events = await stage_events.timeline(db, job.id)
    summary = stage_events.summarise(events)

    unresolved_findings = await _open_findings(db, intake_id)
    unresolved_conflicts = (await identifier_decisions.unresolved_for_intake(db, intake_id)
                            if intake_id is not None else 0)
    invalid_promoted = await _invalid_identifiers_promoted(db, intake_id)
    failed_verification = await _failed_required_verification(db, intake_id)
    unexplained = (await disp.records_without_disposition(db, intake_id)
                   if intake_id is not None else 0)

    outcome = status_model.processing_outcome(
        job_state=job.state, job_stage=job.stage,
        failed_stage=summary.get("failed_stage"),
        error_reason=job.error_reason,
        snapshot=snapshot_dict,
        stages_completed=summary.get("completed_stages") or [],
        unresolved_findings=unresolved_findings,
        unresolved_conflicts=unresolved_conflicts,
        invalid_identifiers_promoted=invalid_promoted,
        failed_required_verification=failed_verification,
        unexplained_records=unexplained,
    )
    review_counts = await _review_counts(db, intake_id)
    review = status_model.review_state(
        outcome_code=outcome["code"],
        snapshot_passed=bool(snapshot_dict and snapshot_dict.get("passed")),
        open_work_items=review_counts["open"],
        open_breakdown=review_counts.get("open_breakdown"),
        claimed_work_items=review_counts["claimed"],
        determined_items=0,
        qa_pending=review_counts["qa_pending"],
        qa_in_progress=review_counts["qa_in_progress"],
        qa_approved=review_counts["qa_approved"],
        closed=False,
    )
    return {
        "processing_outcome": outcome,
        "review_state": review,
        "inputs": {
            "snapshot_id": snapshot_dict.get("id") if snapshot_dict else None,
            "snapshot_passed": snapshot_dict.get("passed") if snapshot_dict else None,
            "stage_attempts": summary.get("attempts", 0),
            "completed_stages": summary.get("completed_stages") or [],
            "failed_stage": summary.get("failed_stage"),
            "unresolved_findings": unresolved_findings,
            "unresolved_conflicts": unresolved_conflicts,
            "invalid_identifiers_promoted": invalid_promoted,
            "failed_required_verification": failed_verification,
            "records_without_disposition": unexplained,
            "review_counts": review_counts,
        },
    }


# ── the same derivation for a page of jobs, in a bounded number of queries ──
#
# The list endpoint used to call `status_for_job` once per row: eight queries
# per job, several of them scanning the delivery's curated records, so a page
# of 50 jobs cost 400+ statements and, on the DEV database, 134-271 s. Each
# input below is one grouped query over the page's job ids / intake ids, so
# the statement count is a constant (nine at most) whatever the page size.
# Every filter is the SAME as the per-row helper it replaces; the only change
# is GROUP BY instead of one WHERE per job.

def _intake_ids_of(jobs) -> List[Any]:
    seen: Dict[Any, None] = {}
    for job in jobs:
        if job.source_intake_id is not None:
            seen.setdefault(job.source_intake_id, None)
    return list(seen)


async def _latest_snapshots_by_job(db, job_ids) -> Dict[Any, Any]:
    """job id -> its highest-sequence snapshot (the same row
    `reconciliation.latest_snapshot` returns)."""
    from app.tefca_registry.rce import traceability_models as tm

    if not job_ids:
        return {}
    rows = (await db.execute(
        select(tm.RceReconciliationSnapshot)
        .where(tm.RceReconciliationSnapshot.job_id.in_(job_ids))
        .distinct(tm.RceReconciliationSnapshot.job_id)
        .order_by(tm.RceReconciliationSnapshot.job_id,
                  tm.RceReconciliationSnapshot.sequence.desc()))).scalars().all()
    return {row.job_id: row for row in rows}


async def _timelines_by_job(db, job_ids) -> Dict[Any, List[Dict[str, Any]]]:
    """job id -> its stage events oldest first (as `stage_events.timeline`)."""
    from app.tefca_registry.rce import traceability_models as tm

    out: Dict[Any, List[Dict[str, Any]]] = {}
    if not job_ids:
        return out
    rows = (await db.execute(
        select(tm.RceDeliveryStageEvent)
        .where(tm.RceDeliveryStageEvent.job_id.in_(job_ids))
        .order_by(tm.RceDeliveryStageEvent.job_id,
                  tm.RceDeliveryStageEvent.started_at,
                  tm.RceDeliveryStageEvent.attempt))).scalars().all()
    for row in rows:
        out.setdefault(row.job_id, []).append(row.to_dict())
    return out


async def _open_findings_by_intake(db, intake_ids) -> Dict[Any, int]:
    """`_open_findings` for several intakes: open HIGH/CRITICAL issues of each
    intake's CURRENT run (`run_selection`'s definition, applied per intake)."""
    if not intake_ids:
        return {}
    rows = (await db.execute(text("""
        WITH current_run AS (
            SELECT DISTINCT ON (source_intake_id) source_intake_id, id AS run_id
            FROM rce_ingestion_runs
            WHERE source_intake_id = ANY(CAST(:ids AS uuid[]))
              AND run_status = 'COMPLETE' AND completed_at IS NOT NULL
            ORDER BY source_intake_id, completed_at DESC, started_at DESC, id DESC)
        SELECT i.source_intake_id, count(*) AS n
        FROM rce_issues i
        JOIN current_run c ON c.source_intake_id = i.source_intake_id
                          AND c.run_id = i.run_id
        WHERE i.severity IN ('CRITICAL', 'HIGH')
          AND i.resolution IN ('OPEN', 'PROPOSED', 'UNDER_REVIEW')
        GROUP BY i.source_intake_id"""),
        {"ids": [str(i) for i in intake_ids]})).all()
    return {row[0]: int(row[1] or 0) for row in rows}


async def _unresolved_conflicts_by_intake(db, intake_ids) -> Dict[Any, int]:
    """`identifier_decisions.unresolved_for_intake`, grouped by intake."""
    if not intake_ids:
        return {}
    rows = (await db.execute(text("""
        SELECT x.intake_id, count(*) AS n FROM (
          SELECT DISTINCT ON (intake_id, entity_id, identifier_type) intake_id, decision
          FROM tefca_identifier_decision_events
          WHERE intake_id = ANY(CAST(:ids AS uuid[]))
          ORDER BY intake_id, entity_id, identifier_type, sequence DESC) x
        WHERE x.decision = 'CONFLICT_RAISED'
        GROUP BY x.intake_id"""),
        {"ids": [str(i) for i in intake_ids]})).all()
    return {row[0]: int(row[1] or 0) for row in rows}


async def _invalid_identifiers_promoted_by_intake(db, intake_ids) -> Dict[Any, int]:
    """`_invalid_identifiers_promoted`, grouped by intake. The DISTINCT on
    (intake, entity) keeps the per-row `IN (subquery)` semantics: an entity
    promoted from two lines of one delivery is one entity, and its identifier
    rows are counted once."""
    from app.services.npi_validator import validate_npi
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    if not intake_ids:
        return {}
    promoted = (select(m.RceCuratedRecord.source_intake_id.label("intake_id"),
                       m.RceCuratedRecord.canonical_entity_id.label("entity_id"))
                .where(m.RceCuratedRecord.source_intake_id.in_(intake_ids),
                       m.RceCuratedRecord.canonical_entity_id.isnot(None))
                .distinct().subquery())
    rows = (await db.execute(
        select(promoted.c.intake_id, reg.TefcaEntityIdentifier.identifier_value)
        .join(reg.TefcaEntityIdentifier,
              reg.TefcaEntityIdentifier.entity_id == promoted.c.entity_id)
        .where(reg.TefcaEntityIdentifier.identifier_type == "npi",
               reg.TefcaEntityIdentifier.identifier_status == "active"))).all()
    out: Dict[Any, int] = {}
    for intake_id, value in rows:
        if not validate_npi(value)[0]:
            out[intake_id] = out.get(intake_id, 0) + 1
    return out


async def _failed_required_verification_by_intake(db, intake_ids) -> Dict[Any, int]:
    """`_failed_required_verification`, grouped by intake: FAIL evidence rows
    whose entity_id (a string column) is the text form of a canonical entity
    of the intake. An absent table is zero evidence for every intake."""
    if not intake_ids:
        return {}
    try:
        rows = (await db.execute(text("""
            SELECT c.intake_id, count(*) AS n
            FROM tefca_dimension_evidence e
            JOIN (SELECT DISTINCT source_intake_id AS intake_id,
                                  CAST(canonical_entity_id AS text) AS entity_id
                  FROM rce_curated_records
                  WHERE source_intake_id = ANY(CAST(:ids AS uuid[]))
                    AND canonical_entity_id IS NOT NULL) c
              ON c.entity_id = e.entity_id
            WHERE e.disposition = 'FAIL'
            GROUP BY c.intake_id"""),
            {"ids": [str(i) for i in intake_ids]})).all()
    except Exception as exc:  # noqa: BLE001 — an absent table is zero evidence, not an error
        logger.warning("failed-verification count unavailable: %s",
                       type(exc).__name__, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}
    return {row[0]: int(row[1] or 0) for row in rows}


async def _records_without_disposition_by_intake(db, intake_ids) -> Dict[Any, int]:
    """`dispositions.records_without_disposition`, grouped by intake."""
    from app.tefca_registry.rce import traceability_models as tm

    if not intake_ids:
        return {}
    rows = (await db.execute(text(f"""
        SELECT s.source_intake_id, count(*) AS n
        FROM rce_source_records s
        LEFT JOIN {tm.CURRENT_DISPOSITIONS_VIEW} d ON d.source_record_id = s.id
        WHERE s.source_intake_id = ANY(CAST(:ids AS uuid[])) AND d.id IS NULL
        GROUP BY s.source_intake_id"""),
        {"ids": [str(i) for i in intake_ids]})).all()
    return {row[0]: int(row[1] or 0) for row in rows}


def _empty_review_counts() -> Dict[str, Any]:
    return {"open": 0, "claimed": 0, "determined": 0, "qa_pending": 0,
            "qa_in_progress": 0, "qa_approved": 0, "total": 0,
            "open_breakdown": {}}


async def _review_counts_by_intake(db, intake_ids) -> Dict[Any, Dict[str, Any]]:
    """`_review_counts` for several intakes: the delivery's own cases (stamped
    `source_intake_id`) in one read, the latest QA action of every determined
    case in a second, then the same bucketing per intake."""
    from app.tefca_registry import models as reg

    out: Dict[Any, Dict[str, Any]] = {}
    if not intake_ids:
        return out
    by_text = {str(i): i for i in intake_ids}
    stamp = reg.ReviewRecord.verification_results["source_intake_id"].astext
    rows = (await db.execute(
        select(stamp, reg.ReviewRecord.review_id, reg.ReviewRecord.assigned_to_user_id,
               reg.ReviewRecord.reviewer_resolution, reg.ReviewRecord.reportable_at,
               reg.ReviewRecord.verification_results["queue_source"].astext)
        .where(stamp.in_(list(by_text))))).all()
    for intake_id in intake_ids:
        out[intake_id] = _empty_review_counts()
    determined_ids = [r[1] for r in rows
                      if r[3] is not None and r[4] is None]
    latest_qa: Dict[str, Optional[str]] = {}
    if determined_ids:
        qa_rows = (await db.execute(text("""
            SELECT DISTINCT ON (review_id) review_id, qa_action
            FROM review_decision_events
            WHERE review_id = ANY(:ids) AND event_type = 'QA_REVIEW'
            ORDER BY review_id, sequence_number DESC"""),
            {"ids": determined_ids})).all()
        latest_qa = {rid: action for rid, action in qa_rows}
    for stamped, review_id, assigned_to, resolution, reportable_at, source in rows:
        counts = out[by_text[stamped]]
        counts["total"] += 1
        if reportable_at is not None:
            counts["qa_approved"] += 1
        elif resolution is not None:
            action = latest_qa.get(review_id)
            if action in ("RETURN", "ESCALATE"):
                counts["qa_in_progress"] += 1
            else:
                counts["qa_pending"] += 1
        elif assigned_to is not None:
            counts["claimed"] += 1
        else:
            counts["open"] += 1
            source = source or "unknown"
            counts["open_breakdown"][source] = counts["open_breakdown"].get(source, 0) + 1
    return out


async def status_for_jobs(db, jobs) -> List[Dict[str, Any]]:
    """`status_for_job` for a page of jobs, one result per job in order.

    Reads the same persisted evidence through nine grouped queries at most
    (snapshots, stage events, open findings, identifier conflicts, invalid
    promoted identifiers, failed verifications, records without disposition,
    review records, QA events) instead of eight per job, and feeds
    `status_model` exactly the inputs the per-row derivation would.
    """
    from app.tefca_registry.rce import stage_events, status_model

    jobs = list(jobs)
    if not jobs:
        return []
    job_ids = [job.id for job in jobs]
    intake_ids = _intake_ids_of(jobs)

    snapshots = await _latest_snapshots_by_job(db, job_ids)
    timelines = await _timelines_by_job(db, job_ids)
    findings = await _open_findings_by_intake(db, intake_ids)
    conflicts = await _unresolved_conflicts_by_intake(db, intake_ids)
    invalid = await _invalid_identifiers_promoted_by_intake(db, intake_ids)
    failed = await _failed_required_verification_by_intake(db, intake_ids)
    unexplained = await _records_without_disposition_by_intake(db, intake_ids)
    reviews = await _review_counts_by_intake(db, intake_ids)

    results: List[Dict[str, Any]] = []
    for job in jobs:
        intake_id = job.source_intake_id
        snapshot = snapshots.get(job.id)
        snapshot_dict = snapshot.to_dict() if snapshot is not None else None
        summary = stage_events.summarise(timelines.get(job.id, []))

        unresolved_findings = findings.get(intake_id, 0) if intake_id is not None else 0
        unresolved_conflicts = conflicts.get(intake_id, 0) if intake_id is not None else 0
        invalid_promoted = invalid.get(intake_id, 0) if intake_id is not None else 0
        failed_verification = failed.get(intake_id, 0) if intake_id is not None else 0
        records_unexplained = unexplained.get(intake_id, 0) if intake_id is not None else 0

        outcome = status_model.processing_outcome(
            job_state=job.state, job_stage=job.stage,
            failed_stage=summary.get("failed_stage"),
            error_reason=job.error_reason,
            snapshot=snapshot_dict,
            stages_completed=summary.get("completed_stages") or [],
            unresolved_findings=unresolved_findings,
            unresolved_conflicts=unresolved_conflicts,
            invalid_identifiers_promoted=invalid_promoted,
            failed_required_verification=failed_verification,
            unexplained_records=records_unexplained,
        )
        if intake_id is None:
            # The per-row helper's no-intake shape (no breakdown key).
            review_counts: Dict[str, Any] = {
                "open": 0, "claimed": 0, "determined": 0, "qa_pending": 0,
                "qa_in_progress": 0, "qa_approved": 0, "total": 0}
        else:
            review_counts = reviews.get(intake_id) or _empty_review_counts()
        review = status_model.review_state(
            outcome_code=outcome["code"],
            snapshot_passed=bool(snapshot_dict and snapshot_dict.get("passed")),
            open_work_items=review_counts["open"],
            open_breakdown=review_counts.get("open_breakdown"),
            claimed_work_items=review_counts["claimed"],
            determined_items=0,
            qa_pending=review_counts["qa_pending"],
            qa_in_progress=review_counts["qa_in_progress"],
            qa_approved=review_counts["qa_approved"],
            closed=False,
        )
        results.append({
            "processing_outcome": outcome,
            "review_state": review,
            "inputs": {
                "snapshot_id": snapshot_dict.get("id") if snapshot_dict else None,
                "snapshot_passed": snapshot_dict.get("passed") if snapshot_dict else None,
                "stage_attempts": summary.get("attempts", 0),
                "completed_stages": summary.get("completed_stages") or [],
                "failed_stage": summary.get("failed_stage"),
                "unresolved_findings": unresolved_findings,
                "unresolved_conflicts": unresolved_conflicts,
                "invalid_identifiers_promoted": invalid_promoted,
                "failed_required_verification": failed_verification,
                "records_without_disposition": records_unexplained,
                "review_counts": review_counts,
            },
        })
    return results
