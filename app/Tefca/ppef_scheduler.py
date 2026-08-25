"""
PPEF ingestion scheduler — a separate AsyncIOScheduler, following the
bulletin_intelligence self-healing pattern without touching it.

WHY A SEPARATE SCHEDULER
Bulletin Intelligence and the TEFCA QA monitor each own their own
AsyncIOScheduler, and both are out of scope for this work. This module follows
their pattern rather than editing them: one scheduler, coroutine jobs awaited on
the app's event loop, interval triggers with coalesce and a misfire grace.

WHAT IT DOES AND DELIBERATELY DOES NOT DO
  poller  — claims QUEUED jobs from the DATABASE and runs them.
  reaper  — marks jobs whose heartbeat went stale as FAILED.

It stores nothing. APScheduler's default MemoryJobStore dies with the process,
which is exactly the failure this whole mechanism exists to survive, so every
fact about a job lives in the database and the scheduler only asks questions of
it. That is the same shape as the bulletin watchdog, which recovers by asking
"does today's briefing exist?" rather than by remembering anything.

THE INGESTION LOGIC IS NOT REIMPLEMENTED HERE.
`PPEFIngestor` is used as-is. The only addition is a subclass that emits a
heartbeat as bytes arrive — extension, not modification, so the tested download,
checksum, schema-validation and truncation behaviour is byte-for-byte the same
code that has already run against CMS.

MULTI-WORKER WARNING
Production runs ONE gunicorn worker today, and nothing enforces that: no
--workers flag, no WEB_CONCURRENCY. If that changes, every worker would start
its own scheduler and poll the same queue. `claim_next_queued()` uses
SELECT ... FOR UPDATE SKIP LOCKED and the job table carries a partial unique
index over active jobs, so duplicate execution is prevented by the DATABASE
rather than by the topology — but the scheduler topology must still be reviewed
before scaling out. APScheduler 3.x provides no distributed coordination of its
own.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_scheduler = None
_startup_task = None          # strong ref so the boot catch-up is not GC'd
_refusal_logged_at = None     # throttles the "ingestion disabled" poller notice

def _log_poller_refusal(reason: str) -> None:
    """Log the refusal, but not 4,320 times a day.

    The poller ticks every 20 seconds. An unthrottled warning would emit ~4,300
    identical lines per day and drown the log that an operator reads to find out
    what actually happened. Once an hour is enough to prove the control is live
    and still leaves the evidence in every reasonable retention window.
    """
    global _refusal_logged_at
    now = datetime.utcnow()
    if _refusal_logged_at is None or (now - _refusal_logged_at).total_seconds() >= 3600:
        _refusal_logged_at = now
        logger.warning("PPEF poller did not claim any job — %s", reason)


#: How often the poller looks for QUEUED work.
POLL_INTERVAL_SECONDS = 20
#: How often the reaper looks for dead workers.
REAP_INTERVAL_SECONDS = 60


class _HeartbeatingIngestor:
    """Wraps PPEFIngestor so long phases report liveness.

    Subclasses rather than edits: `iter_chunks` is the documented seam in
    PPEFIngestor, so overriding it adds heartbeats to the download phase while
    the parser, hasher, schema validator and truncation logic remain the exact
    tested code path.
    """

    def __new__(cls, on_beat, catalog=None):
        from app.Tefca.ppef_ingest import PPEFIngestor

        class _Impl(PPEFIngestor):
            async def iter_chunks(self, resource):
                async for chunk in super().iter_chunks(resource):
                    await on_beat("DOWNLOADING")
                    yield chunk
                # The generator running dry is the one observable moment between
                # "bytes still arriving" and "header about to be schema-checked",
                # so it is where VALIDATING legitimately begins. Without this the
                # job would appear to sit in DOWNLOADING through a phase that is
                # not downloading anything.
                await on_beat("VALIDATING")

        return _Impl(catalog=catalog)


async def run_job(job_id) -> None:
    """Execute one claimed job, driving its state through the database.

    Every transition is committed before the work that follows it, so a process
    killed at any point leaves a truthful record of how far it got.
    """
    from app.core.database import async_session_maker
    from app.Tefca import ppef_jobs
    from app.Tefca.models import TEFCAPPEFIngestJob, TEFCAPPEFRecord, TEFCAPPEFSnapshot
    from app.Tefca.ppef_ingest import IngestError, SchemaDriftError
    from app.Tefca.ppef_store import copy_records

    import uuid as _uuid

    # ENFORCEMENT LAYER 2b — the executor refuses on its own account.
    #
    # _poll_tick already refuses before claiming, so in normal operation this is
    # unreachable. It is here because run_job is a public coroutine: a retry
    # helper, an operational script or a future caller can invoke it directly
    # with a job id, bypassing the poller entirely. The download begins a few
    # lines below, so the last chance to stop it belongs to the function that
    # performs it rather than to its usual caller.
    if not ppef_jobs.bulk_ingest_enabled():
        logger.warning("PPEF run_job(%s) refused — %s", job_id,
                       ppef_jobs.bulk_ingest_refusal_reason())
        return

    async with async_session_maker() as db:
        job = await db.get(TEFCAPPEFIngestJob, job_id)
        if job is None or job.state in TEFCAPPEFIngestJob.TERMINAL_STATES:
            return
        component = job.component
        max_rows = job.max_rows
        snapshot_id = _uuid.uuid4()

        last_beat = {"at": datetime.utcnow(), "phase": None}

        async def beat(phase: str):
            """Heartbeat, rate-limited; also records a phase change immediately."""
            now = datetime.utcnow()
            changed = phase != last_beat["phase"]
            elapsed = (now - last_beat["at"]).total_seconds()
            if changed:
                await ppef_jobs.transition(db, job_id, phase)
                last_beat.update(at=now, phase=phase)
            elif elapsed >= ppef_jobs.HEARTBEAT_INTERVAL_SECONDS:
                await ppef_jobs.heartbeat(db, job_id)
                last_beat["at"] = now

        try:
            # The snapshot row exists before any record references it (FK), and a
            # failed load therefore leaves a `pending`/`failed` row documenting
            # the attempt rather than nothing at all.
            db.add(TEFCAPPEFSnapshot(id=snapshot_id, component=component,
                                     ingest_status="pending",
                                     ingested_by=job.requested_by))
            await db.commit()
            await ppef_jobs.transition(db, job_id, TEFCAPPEFIngestJob.STATE_DOWNLOADING,
                                       snapshot_id=snapshot_id)
            last_beat["phase"] = TEFCAPPEFIngestJob.STATE_DOWNLOADING

            written = {"rows": 0}

            async def write_batch(batch):
                # First batch means parsing began, so the header passed schema
                # validation: VALIDATING is behind us and LOADING has started.
                await beat(TEFCAPPEFIngestJob.STATE_LOADING)
                written["rows"] += await copy_records(db, snapshot_id, component, batch)
                await db.commit()

            ingestor = _HeartbeatingIngestor(on_beat=beat)
            meta = await ingestor.ingest(component, write_batch=write_batch, max_rows=max_rows)

            # ── Activation gate: every validation must pass before the snapshot
            # becomes readable evidence. ────────────────────────────────────────
            await ppef_jobs.transition(db, job_id, TEFCAPPEFIngestJob.STATE_VALIDATING)
            problems = []
            if not meta.sha256:
                problems.append("checksum missing")
            if not meta.schema_fields:
                problems.append("schema not validated")
            if meta.record_count <= 0:
                problems.append("zero rows loaded")
            if written["rows"] != meta.record_count:
                problems.append(f"row-count mismatch: parsed {meta.record_count}, "
                                f"wrote {written['rows']}")
            # Relational validation: the join key this component is queried by
            # must actually be populated, or the load is useless downstream.
            # Every component keys on ENRLMT_ID, which normalize_row lands in
            # `enrollment_id` — including REASSIGNMENT, where it holds
            # REASGN_BNFT_ENRLMT_ID. A load whose join key is empty is not a
            # smaller dataset, it is an unusable one, so it must not activate.
            from sqlalchemy import func, select as _select

            async def _populated(col):
                return await db.scalar(
                    _select(func.count()).select_from(TEFCAPPEFRecord)
                    .where(TEFCAPPEFRecord.snapshot_id == snapshot_id)
                    .where(col.isnot(None)))

            if not await _populated(TEFCAPPEFRecord.enrollment_id):
                problems.append("relational validation failed: no rows carry ENRLMT_ID")
            if component == "REASSIGNMENT":
                # REASSIGNMENT is the only component with a SECOND join key, and
                # it is the one Amendment 5 traverses. Loading it without
                # RCV_BNFT_ENRLMT_ID would silently break entity->practitioner
                # traversal while every row count still looked right.
                if not await _populated(TEFCAPPEFRecord.related_enrollment_id):
                    problems.append("relational validation failed: no rows carry "
                                    "RCV_BNFT_ENRLMT_ID")

            if problems:
                raise IngestError("; ".join(problems))

            snap = await db.get(TEFCAPPEFSnapshot, snapshot_id)
            snap.cms_title = meta.cms_title
            snap.file_name = meta.file_name
            snap.resource_id = meta.resource_id
            snap.parent_dataset_id = meta.parent_dataset_id
            snap.download_url = meta.download_url
            snap.api_endpoint = meta.api_endpoint
            snap.transport = meta.transport
            snap.resource_version = meta.resource_version
            snap.as_of_label = meta.as_of_label
            snap.file_size = meta.file_size
            snap.sha256 = meta.sha256
            snap.schema_fields = meta.schema_fields
            snap.record_count = meta.record_count
            snap.rows_truncated = meta.rows_truncated
            snap.http_last_modified = meta.http_last_modified
            snap.ingest_status = "complete"      # ACTIVATION happens here, and only here
            await db.commit()

            await ppef_jobs.finish_complete(db, job_id, snapshot_id,
                                            meta.sha256, meta.record_count)
            logger.info("PPEF job %s COMPLETE: %s %s rows=%s sha=%s",
                        job_id, component, meta.resource_version,
                        meta.record_count, meta.sha256[:12])

            # ATTRIBUTION. The load is REQUESTED by a named admin and EXECUTED by
            # the scheduler. Both are recorded: user_id is the admin, because
            # /api/admin/users/{id}/activity filters on user_id and a null there
            # is invisible exactly where an operator looks; `executed_by` names
            # the service so the row never implies a human watched 3.9M rows load.
            from app.services.audit import log_tefca_event
            actor = None
            if job.requested_by:
                try:
                    from app.models.database import User
                    from sqlalchemy import select as _sel
                    actor = (await db.execute(
                        _sel(User).where(User.email == job.requested_by))).scalar_one_or_none()
                except Exception:
                    actor = None
            try:
                await log_tefca_event(
                    db, user=actor, action="PPEF_SNAPSHOT_INGESTED",
                    resource_type="tefca_ppef_snapshot", resource_id=str(snapshot_id),
                    result="success",
                    outcome="SUCCESS",
                    details={
                        # Everything an auditor needs to reproduce or challenge a
                        # determination. The START row records an intention; only
                        # this one records what actually landed.
                        "result_status": "PARTIAL" if meta.rows_truncated else "SUCCESS",
                        "job_id": str(job_id),
                        "requested_by": job.requested_by,
                        "executed_by": "system/ppef-scheduler (APScheduler poller)",
                        "dataset": "CMS PPEF",
                        "component": component,
                        "cms_title": meta.cms_title,
                        "file_name": meta.file_name,
                        "resource_id": meta.resource_id,
                        "parent_dataset_id": meta.parent_dataset_id,
                        "resource_version": meta.resource_version,
                        "quarter": meta.as_of_label,
                        "sha256": meta.sha256,
                        "file_size": meta.file_size,
                        "record_count": meta.record_count,
                        "rows_truncated": meta.rows_truncated,
                        "schema_fields": meta.schema_fields,
                        "schema_validated": True,
                        "transport": meta.transport,
                        "download_url": meta.download_url,
                        "retrieved_at": meta.retrieved_at,
                        "completed_at": meta.ingested_at,
                        "realtime": False,
                    })
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("PPEF job %s completion AUDIT FAILED: %s", job_id, exc)

        except SchemaDriftError as exc:
            await _abort(db, job_id, snapshot_id, f"schema_drift: {exc}")
        except IngestError as exc:
            await _abort(db, job_id, snapshot_id, f"ingest_error: {exc}")
        except Exception as exc:
            logger.warning("PPEF job %s failed: %s", job_id, exc)
            await _abort(db, job_id, snapshot_id, f"unexpected: {exc}")


async def _abort(db, job_id, snapshot_id, reason: str) -> None:
    """Fail the job and make sure its partial snapshot can never be read.

    The partial ROWS are deleted while the snapshot ROW is kept. That split is
    deliberate: the snapshot documents that a load was attempted and why it
    stopped, which is worth keeping, whereas a partial row set under a failed
    snapshot is loadable-looking garbage that only invites a future query to
    treat half a quarter as the quarter.

    Only this in-process path deletes. The reaper deliberately does not — see
    reap_stale_jobs — because a dead worker's rows may still be landing from a
    connection that has not yet noticed it is orphaned.
    """
    from sqlalchemy import delete as _delete

    from app.Tefca import ppef_jobs
    from app.Tefca.models import TEFCAPPEFRecord, TEFCAPPEFSnapshot

    try:
        await db.rollback()
        snap = await db.get(TEFCAPPEFSnapshot, snapshot_id)
        if snap is not None and snap.ingest_status != "complete":
            snap.ingest_status = "failed"
            snap.error = reason[:2000]
            await db.execute(_delete(TEFCAPPEFRecord)
                             .where(TEFCAPPEFRecord.snapshot_id == snapshot_id))
            await db.commit()
    except Exception:
        await db.rollback()
    await ppef_jobs.finish_failed(db, job_id, reason)

    # A failed ingest is audited too. An audit trail that records only successes
    # cannot be used to investigate anything, and failure is the case an operator
    # actually needs explained.
    try:
        from app.services.audit import log_tefca_event
        from app.Tefca.models import TEFCAPPEFIngestJob

        job = await db.get(TEFCAPPEFIngestJob, job_id)
        actor = None
        if job is not None and job.requested_by:
            try:
                from sqlalchemy import select as _sel

                from app.models.database import User
                actor = (await db.execute(
                    _sel(User).where(User.email == job.requested_by))).scalar_one_or_none()
            except Exception:
                actor = None
        await log_tefca_event(
            db, user=actor, action="PPEF_SNAPSHOT_INGEST_FAILED",
            resource_type="tefca_ppef_snapshot", resource_id=str(snapshot_id),
            result="failure", outcome="FAILURE",
            details={"job_id": str(job_id),
                     "component": getattr(job, "component", None),
                     "requested_by": getattr(job, "requested_by", None),
                     "executed_by": "system/ppef-scheduler (APScheduler poller)",
                     "reason": reason[:500]})
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("PPEF job %s failure AUDIT FAILED: %s", job_id, exc)


# ── Scheduler jobs ───────────────────────────────────────────────────────────

async def _poll_tick():
    """Claim and run one queued job.

    One at a time on purpose: these are multi-gigabyte quarterly loads, and
    running them serially keeps memory and database load predictable.
    """
    from app.core.database import async_session_maker
    from app.Tefca import ppef_jobs

    # ENFORCEMENT LAYER 2 — refuse before CLAIMING, not merely before running.
    #
    # Layer 1 stops the endpoint creating a job. This layer exists because a
    # QUEUED row can arrive by routes Layer 1 never sees: a row already in the
    # table when the flag was turned off, a restored backup, a manual INSERT, or
    # a future code path someone adds without knowing about the gate. If bulk
    # ingestion is disabled, such a row must sit untouched rather than execute on
    # the next 20-second tick.
    #
    # The check precedes claim_next_queued() deliberately. Claiming mutates the
    # row — it sets STARTED and takes the active-job slot — so claiming and then
    # refusing would leave the job neither queued nor running, and would consume
    # the slot that a later authorized retry needs.
    #
    # The reaper is NOT gated. It only marks dead jobs FAILED and downloads
    # nothing; housekeeping must keep working while ingestion is switched off,
    # otherwise disabling the flag would leave orphaned jobs looking alive.
    if not ppef_jobs.bulk_ingest_enabled():
        _log_poller_refusal(ppef_jobs.bulk_ingest_refusal_reason())
        return

    try:
        async with async_session_maker() as db:
            job = await ppef_jobs.claim_next_queued(db)
        if job is None:
            return
        logger.info("PPEF poller claimed job %s (%s)", job.id, job.component)
        await run_job(job.id)
    except Exception as exc:
        logger.error("PPEF poll tick error: %s", exc)


async def _reap_tick():
    """Fail jobs whose worker stopped heartbeating.

    The self-healing counterpart to the bulletin watchdog: it recovers the case
    the scheduler cannot see, namely a process that died holding a job.
    """
    from app.core.database import async_session_maker
    from app.Tefca import ppef_jobs

    try:
        async with async_session_maker() as db:
            reaped = await ppef_jobs.reap_stale_jobs(db)
            # Snapshots left `pending` by the OLD BackgroundTask path have no job
            # to go stale, so the heartbeat sweep above cannot see them. Same
            # failure, no evidence — closing them is the same act.
            orphans = await ppef_jobs.close_orphaned_snapshots(db)
        if reaped:
            logger.warning("PPEF reaper marked %d stale job(s) FAILED", len(reaped))
            await _audit_reaped(reaped)
        if orphans:
            logger.warning("PPEF reaper closed %d orphaned snapshot(s)", len(orphans))
    except Exception as exc:
        logger.error("PPEF reap tick error: %s", exc)


async def _audit_reaped(reaped) -> None:
    """Write a failure audit for each job the reaper closed.

    WHY THIS IS NOT OPTIONAL
    A reaped job already records its fate in two places — the job row and the
    snapshot. Neither is the audit trail, which is where an operator actually
    goes to ask "what happened to the load I started?". Without this row, the
    single most important failure mode this whole mechanism exists to handle —
    a worker dying mid-load — would leave the audit trail showing a load that
    was QUEUED and then nothing. That is the same silence the original defect
    produced, relocated.

    Attributed to the requesting admin, executed_by naming the reaper: the
    person who asked is who the row belongs to, and no human performed the
    reaping.
    """
    from sqlalchemy import select as _sel

    from app.core.database import async_session_maker
    from app.models.database import User
    from app.services.audit import log_tefca_event

    async with async_session_maker() as db:
        for entry in reaped:
            try:
                actor = None
                if entry.get("requested_by"):
                    actor = (await db.execute(
                        _sel(User).where(User.email == entry["requested_by"]))
                    ).scalar_one_or_none()
                await log_tefca_event(
                    db, user=actor, action="PPEF_SNAPSHOT_INGEST_FAILED",
                    resource_type="tefca_ppef_snapshot",
                    resource_id=entry.get("snapshot_id") or entry["job_id"],
                    result="failure", outcome="FAILURE",
                    details={
                        "result_status": "FAILED",
                        "job_id": entry["job_id"],
                        "component": entry.get("component"),
                        "requested_by": entry.get("requested_by"),
                        "executed_by": "system/ppef-reaper (stale-job sweep)",
                        "failed_in_state": entry.get("was_state"),
                        "attempt_count": entry.get("attempt_count"),
                        "reason": entry.get("reason"),
                        "detected_by": "heartbeat_timeout",
                        "realtime": False,
                    })
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("PPEF reaper AUDIT FAILED for job %s: %s",
                             entry.get("job_id"), exc)


async def _startup_reap():
    """Reap once shortly after boot.

    A deployment or crash is precisely when jobs are orphaned, and waiting a
    full reap interval would leave them looking alive. Mirrors the bulletin
    scheduler's startup catch-up.
    """
    await asyncio.sleep(30)
    await _reap_tick()


def start_ppef_scheduler():
    """Start the PPEF poller + reaper. Safe to call once at app startup."""
    global _scheduler, _startup_task
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = AsyncIOScheduler()
        defaults = dict(coalesce=True, misfire_grace_time=120, replace_existing=True,
                        max_instances=1)

        _scheduler.add_job(_poll_tick, IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
                           id="ppef_poller", name="PPEF ingestion queue poller", **defaults)
        _scheduler.add_job(_reap_tick, IntervalTrigger(seconds=REAP_INTERVAL_SECONDS),
                           id="ppef_reaper", name="PPEF stale-job reaper", **defaults)
        _scheduler.start()
        logger.info("PPEF scheduler started — poller %ss, reaper %ss, stale threshold %ss",
                    POLL_INTERVAL_SECONDS, REAP_INTERVAL_SECONDS, 300)

        try:
            _startup_task = asyncio.get_running_loop().create_task(_startup_reap())
        except RuntimeError:
            from apscheduler.triggers.date import DateTrigger
            _scheduler.add_job(_startup_reap, DateTrigger(run_date=None),
                               id="ppef_startup_reap", replace_existing=True)
    except ImportError:
        logger.warning("APScheduler not installed — PPEF scheduler not started")
    except Exception as exc:
        logger.error("PPEF scheduler failed to start: %s", exc)


def scheduler_status() -> dict:
    """What the scheduler is doing, for health reporting."""
    if _scheduler is None:
        return {"running": False, "jobs": []}
    try:
        return {
            "running": _scheduler.running,
            "jobs": [{"id": j.id, "name": j.name,
                      "next_run": j.next_run_time.isoformat() if j.next_run_time else None}
                     for j in _scheduler.get_jobs()],
        }
    except Exception:
        return {"running": False, "jobs": []}
