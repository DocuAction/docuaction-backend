"""Run one controlled export job to a terminal state.

WHAT THIS ADDS TO STEP #17, AND WHAT IT DELIBERATELY DOES NOT
─────────────────────────────────────────────────────────────
Nothing about the workbook. `build_workbook_dataset`, `render_workbook` and
`finalize_artifact` are used exactly as certified — this module decides only
WHEN they run and what is recorded about the attempt. If a change here would
alter a single cell of the produced file, it is the wrong change.

THE ORDER OF THE LAST TWO STEPS IS THE POINT
────────────────────────────────────────────
Bytes are registered BEFORE the job is marked SUCCEEDED, and the job is marked
SUCCEEDED only with the artifact's identifiers in hand. A job that said
SUCCEEDED first would, for the width of one failure, be a receipt for a file
that does not exist — and the caller would follow it to a 404 or, worse, to a
half-written one. Failure at any point leaves the job FAILED naming no artifact,
which is the only honest thing a partial run can say.

WHY A THREAD FOR THE RENDER
───────────────────────────
Rendering is CPU-bound and measured in minutes at the delivered population. On
the event loop it would not slow the other requests in this process, it would
stop them. Running it in a worker thread means the poller's own heartbeat, the
API and every other coroutine keep going. This is process-local isolation, not
a distributed worker — §12 of the gate records exactly what that does and does
not buy.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

#: Phases a caller may see. Each corresponds to a real transition in the run
#: below — there is no percentage anywhere, because nothing here measures one.
PHASE_PREPARING = "Preparing data"
PHASE_BUILDING = "Building workbook"
PHASE_REGISTERING = "Registering artifact"
PHASE_READY = "Ready"


class _Heartbeat:
    """Writes a heartbeat while a long step runs, on its own session.

    The render holds a worker thread for minutes. Without this the reaper would
    see silence and correctly conclude the worker was gone — killing a healthy
    export. A separate session is used deliberately: the job's own session is
    busy, and a heartbeat that had to wait for it would prove nothing.
    """

    def __init__(self, job_id, interval: float):
        self._job_id, self._interval, self._task = job_id, interval, None

    async def _beat(self):
        from app.core.database import async_session_maker
        from app.reports.data import export_jobs

        while True:
            await asyncio.sleep(self._interval)
            try:
                async with async_session_maker() as db:
                    await export_jobs.heartbeat(db, self._job_id)
            except Exception as exc:  # noqa: BLE001
                # A missed heartbeat is not a reason to fail the export. The
                # reaper's threshold is many multiples of the interval.
                logger.warning("export heartbeat failed for %s: %s",
                               self._job_id, exc)

    async def __aenter__(self):
        self._task = asyncio.create_task(self._beat())
        return self

    async def __aexit__(self, *_exc):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return False


async def run_export_job(db, job) -> str:
    """Take a RUNNING job to SUCCEEDED or FAILED. Returns the final state.

    Never raises for an export failure: a raised exception would leave the job
    RUNNING until the reaper noticed, and "failed" is information the caller is
    owed now rather than in fifteen minutes.
    """
    from starlette.concurrency import run_in_threadpool

    from app.reports.data import export_jobs
    from app.reports.data.artifact_registry import finalize_artifact
    from app.reports.data.export_job_model import ReportExportJob
    from app.reports.data.onc_review_workbook import (WORKBOOK_VERSION,
                                                      WorkbookRefused,
                                                      build_workbook_dataset)
    from app.reports.engine.xlsx_engine import (XLSX_CONTENT_TYPE,
                                                XLSX_ENGINE_VERSION,
                                                render_workbook)

    job_id = job.id
    try:
        await export_jobs.heartbeat(db, job_id, phase=PHASE_PREPARING)
        dataset = await build_workbook_dataset(
            db, intake_id=job.source_intake_id,
            classification=job.classification,
            generated_by=job.requested_by)

        await export_jobs.heartbeat(db, job_id, phase=PHASE_BUILDING)
        async with _Heartbeat(job_id, export_jobs.HEARTBEAT_INTERVAL_SECONDS):
            content = await run_in_threadpool(render_workbook, dataset)

        await export_jobs.heartbeat(db, job_id, phase=PHASE_REGISTERING)
        artifact = await finalize_artifact(
            db, report_id=dataset["report_id"],
            report_type=job.export_type, content=content,
            content_type=XLSX_CONTENT_TYPE,
            review_cycle_id=dataset["intake_id"],
            generated_by=job.requested_by,
            template_version=(f"workbook {WORKBOOK_VERSION} / "
                              f"engine {XLSX_ENGINE_VERSION}"),
            source_artifact_sha256=dataset["source_sha256"],
            report_data_hash=dataset["data_hash"],
            data_classification=dataset["classification"])
        await db.commit()

    except WorkbookRefused as exc:
        # The generator declined to assert something untrue — a schema that does
        # not match the contract, or a delivery that is not there. That is a
        # controlled outcome and its reason is safe to show.
        await export_jobs.finish_failed(db, job_id, str(exc))
        return ReportExportJob.STATE_FAILED
    except Exception as exc:  # noqa: BLE001
        # Everything else. The exception text goes to the log, where an
        # administrator can read it; the job records a controlled sentence,
        # because an export job is read by analysts and a traceback is neither
        # useful to them nor safe to circulate.
        logger.exception("export job %s failed", job_id)
        await export_jobs.finish_failed(
            db, job_id,
            f"The workbook could not be produced ({type(exc).__name__}). "
            f"Nothing was registered. Administrator diagnostics are in the "
            f"application log against job {job_id}.")
        return ReportExportJob.STATE_FAILED

    await export_jobs.finish_succeeded(
        db, job_id,
        report_id=dataset["report_id"],
        artifact_id=str(artifact["artifact_id"]),
        artifact_version=int(artifact["artifact_version"]),
        rendered_sha256=artifact["rendered_sha256"],
        size_bytes=int(artifact["size_bytes"] or 0))
    return ReportExportJob.STATE_SUCCEEDED


async def run_report_generation_job(db, job) -> str:
    """Take a RUNNING report-generation job to SUCCEEDED or FAILED.

    Sibling to `run_export_job`, not a branch inside it: that function's own
    docstring says changing it risks the certified workbook's bytes, so a
    second job kind gets a second function instead. Calls `generate_report`
    exactly as `POST /reports/generate` does — same function, same dataset
    build, same snapshot, same artifact registration — the only difference is
    WHEN it runs and that the result is read back from a job row instead of
    an HTTP response body.
    """
    from app.reports.data import export_jobs
    from app.reports.data.export_job_model import ReportExportJob
    from app.reports.generator import (ReportGenerationError, ReportParameterError,
                                       generate_report)

    job_id = job.id
    params = dict(job.request_parameters or {})
    report_format = params.pop("format", "html")
    review_cycle_id = params.pop("review_cycle_id", None)

    try:
        await export_jobs.heartbeat(db, job_id, phase=PHASE_PREPARING)
        # `generate_report` builds HTML (Jinja2 templating, not the CPU-bound
        # WeasyPrint PDF render -- `generate_report`'s own docstring: "PDF is
        # NOT generated here"), so unlike the ONC workbook's `render_workbook`
        # it does not need a worker thread of its own; it is awaited directly.
        async with _Heartbeat(job_id, export_jobs.HEARTBEAT_INTERVAL_SECONDS):
            result = await generate_report(
                db, report_type=job.report_type,
                review_cycle_id=review_cycle_id,
                generated_by=job.requested_by, generated_by_id=None,
                query_parameters=params)
        await export_jobs.heartbeat(db, job_id, phase=PHASE_REGISTERING)
    except (ReportParameterError, ReportGenerationError) as exc:
        # The request named no scope, a delivery that does not exist, or a
        # snapshot that is not the delivery's -- a controlled refusal, same
        # text the synchronous route would have answered with.
        await export_jobs.finish_failed(db, job_id, str(exc))
        return ReportExportJob.STATE_FAILED
    except Exception as exc:  # noqa: BLE001
        logger.exception("report generation job %s failed", job_id)
        await export_jobs.finish_failed(
            db, job_id,
            f"The report could not be generated ({type(exc).__name__}). "
            f"Nothing was registered. Administrator diagnostics are in the "
            f"application log against job {job_id}.")
        return ReportExportJob.STATE_FAILED

    artifacts = result.get("artifacts") or {}
    chosen = artifacts.get(report_format) or artifacts.get("html") or result.get("artifact")
    if not chosen or not chosen.get("id"):
        # Generation itself succeeded (the report is stored and readable
        # through /reports/{id}) but no registry row exists for the job to
        # point at -- report it, never claim success without an artifact
        # (same rule `run_export_job` follows).
        await export_jobs.finish_failed(
            db, job_id,
            f"Report {result.get('report_id')} was generated but no durable "
            f"artifact was registered for format '{report_format}'. The "
            f"report may still be readable at /api/reports/{result.get('report_id')}.")
        return ReportExportJob.STATE_FAILED

    await export_jobs.finish_succeeded(
        db, job_id,
        report_id=result["report_id"],
        artifact_id=str(chosen["id"]),
        artifact_version=int(chosen.get("artifact_version") or 1),
        rendered_sha256=chosen.get("rendered_sha256") or "",
        size_bytes=int(chosen.get("size_bytes") or 0))
    return ReportExportJob.STATE_SUCCEEDED
