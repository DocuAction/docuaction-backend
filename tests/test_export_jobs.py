"""Controlled export as background work, certified on synthetic data.

    request → QUEUED → (poller claims) RUNNING → SUCCEEDED
                                              → FAILED

WHAT THIS GATE PROVES
─────────────────────
Step #17 proved the workbook. This proves the workbook can be ASKED FOR safely:

  * the request returns a receipt, not seven minutes of waiting;
  * asking twice produces one job, and the second caller is told so;
  * two callers racing produce one job, decided by the database rather than by
    a disabled button;
  * polling never starts work;
  * a failed run registers no artifact and leaves nothing downloadable;
  * a worker that dies is noticed, and its export slot is released;
  * a job id is not an enumeration oracle.

GOVERNMENT DATA
    Every test runs inside an OUTER transaction that is rolled back, on a
    synthetic delivery under an unassigned `9.99.999` arc. The delivered
    population is never exported and no export job for it is ever created.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.reports.data import export_jobs
from app.reports.data.export_job_model import ReportExportJob

#: The Step #17 suite already builds a complete synthetic estate — one delivery
#: covering clean, corrected, held, verified and reviewed records. Rebuilding it
#: here would be a second fixture that could drift from the one the workbook is
#: certified against.
_spec = importlib.util.spec_from_file_location(
    "step17_fixtures", os.path.join(os.path.dirname(__file__),
                                    "test_onc_review_workbook.py"))
step17 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(step17)

def _dedent(source: str) -> str:
    """`inspect.getsource` of a nested block keeps its indentation, which is a
    syntax error to `ast.parse`. Textwrap rather than a manual strip so a
    docstring's own indentation is preserved."""
    import textwrap

    return textwrap.dedent(source)


QA = SimpleNamespace(id=uuid.uuid4(), email="qa@synthetic.test", role="qalead")
OTHER = SimpleNamespace(id=uuid.uuid4(), email="other@synthetic.test",
                        role="qalead")
BOSS = SimpleNamespace(id=uuid.uuid4(), email="pm@synthetic.test",
                       role="program_manager")


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


async def _estate(db):
    return await step17._synthetic_estate(db)


def _identity(intake_id, classification="DEVELOPMENT_TEST"):
    from app.reports.data.onc_review_workbook import WORKBOOK_VERSION
    from app.reports.engine.xlsx_engine import XLSX_ENGINE_VERSION

    return export_jobs.job_identity(
        intake_id=str(intake_id), workbook_version=WORKBOOK_VERSION,
        engine_version=XLSX_ENGINE_VERSION, classification=classification,
        export_type="onc_review_workbook")


async def _request(db, intake_id, *, by=QA.email, classification="DEVELOPMENT_TEST"):
    return await export_jobs.request_job(
        db, identity=_identity(intake_id, classification),
        export_type="onc_review_workbook", intake_id=intake_id,
        classification=classification, generator_version="workbook 1.0.0",
        requested_by=by)


# ═══ the request is a receipt ═══════════════════════════════════════════════

async def test_a_request_returns_a_queued_job_not_a_workbook(rolled_back_db):
    db = rolled_back_db
    intake_id = await _estate(db)

    job = await _request(db, intake_id)

    assert job.state == ReportExportJob.STATE_QUEUED
    assert job.active_marker is True
    assert job.requested_by == QA.email
    # Nothing has been produced yet, and the job does not pretend otherwise.
    assert job.report_id is None
    assert job.artifact_id is None


async def test_the_request_endpoint_does_not_generate_the_workbook():
    """The control that makes the whole gate worth doing.

    Asserted on the parsed route rather than by timing it: a synthetic delivery
    renders in under a second, so a stopwatch here would pass just as happily
    with the seven-minute path still wired in.
    """
    import ast

    import app.reports.routes as routes

    tree = ast.parse(_dedent(inspect.getsource(routes.export_onc_review_workbook)))
    function = tree.body[0]

    # The PREVIEW branch legitimately renders — ten rows, returned directly and
    # registered nowhere. Only the statements AFTER it are the controlled
    # export, so the assertion is scoped to those, found in the parsed tree
    # rather than by slicing text (the slice does not dedent to valid Python).
    preview_at = next(
        (i for i, node in enumerate(function.body)
         if isinstance(node, ast.If)
         and "preview" in ast.unparse(node.test)), None)
    assert preview_at is not None, (
        "the preview branch moved; this test is scoped to it")

    # Every NAME mentioned, not only the ones in call position:
    # `run_in_threadpool(render_workbook, dataset)` calls neither by name, and
    # an earlier version of this test passed with exactly that still wired in.
    mentioned = {node.id
                 for statement in function.body[preview_at + 1:]
                 for node in ast.walk(statement)
                 if isinstance(node, ast.Name)}

    for forbidden in ("render_workbook", "finalize_artifact",
                      "run_in_threadpool"):
        assert forbidden not in mentioned, (
            f"the request endpoint mentions {forbidden} after the preview "
            f"branch — it produces the export synchronously instead of "
            f"queueing it")
    assert "request_job" in mentioned


# ═══ idempotency ════════════════════════════════════════════════════════════

async def test_asking_twice_produces_one_job(rolled_back_db):
    db = rolled_back_db
    intake_id = await _estate(db)

    first = await _request(db, intake_id)
    second = await _request(db, intake_id)

    assert str(first.id) == str(second.id)
    assert await _count_jobs(db, _identity(intake_id)) == 1


async def test_a_different_delivery_is_a_different_job(rolled_back_db):
    db = rolled_back_db
    intake_id = await _estate(db)
    other_intake, _ = await step17._delivery(db, [step17._row("OTHERDELIVERY")])
    await db.commit()

    first = await _request(db, intake_id)
    second = await _request(db, other_intake)

    assert str(first.id) != str(second.id), (
        "two deliveries collapsed into one export job")


async def test_a_different_classification_is_a_different_job(rolled_back_db):
    """The same data under a different label is a different artifact.

    If the identity ignored classification, a DEVELOPMENT_TEST export already in
    flight would satisfy a request for a GOVERNMENT one and the caller would
    download a file labelled for the wrong handling.
    """
    db = rolled_back_db
    intake_id = await _estate(db)

    dev = await _request(db, intake_id, classification="DEVELOPMENT_TEST")
    gov = await _request(db, intake_id, classification="GOVERNMENT")

    assert str(dev.id) != str(gov.id)


async def test_a_finished_job_does_not_block_a_new_one(rolled_back_db):
    """A SUCCEEDED job is a record, not a lock.

    Whether the ARTIFACT should be reused is the registry's question and it
    already answers it; the job table only has to stop two live runs.
    """
    db = rolled_back_db
    intake_id = await _estate(db)

    first = await _request(db, intake_id)
    await export_jobs.finish_succeeded(
        db, first.id, report_id="ONC-REVIEW-X", artifact_id="a1",
        artifact_version=1, rendered_sha256="0" * 64, size_bytes=10)

    second = await _request(db, intake_id)
    assert str(second.id) != str(first.id)
    assert second.state == ReportExportJob.STATE_QUEUED
    # and both rows survive — the first is still the receipt for its artifact
    assert await _count_jobs(db, _identity(intake_id)) == 2


async def test_the_concurrency_guard_is_declared_and_present(rolled_back_db):
    """Both halves, because they fail in different ways.

    The DATABASE index is what actually refuses a duplicate — a model that
    stopped declaring it would leave a running system perfectly safe and the
    next `alembic revision --autogenerate` would propose DROPPING it. So the
    declaration is asserted too, against the live index it is supposed to
    describe.
    """
    from sqlalchemy import text

    declared = [arg for arg in ReportExportJob.__table_args__
                if getattr(arg, "name", None) == "uq_export_job_active_identity"]
    assert declared, "the model no longer declares the concurrency guard"
    guard = declared[0]
    assert guard.unique is True
    assert [c.name for c in guard.columns] == ["identity", "active_marker"]
    assert guard.dialect_options["postgresql"]["where"] is not None, (
        "the guard is no longer partial — it would refuse a second FINISHED "
        "job for the same export, not a second live one")

    live = (await rolled_back_db.execute(text(
        "SELECT indexdef FROM pg_indexes "
        "WHERE tablename = 'report_export_jobs' "
        "  AND indexname = 'uq_export_job_active_identity'"))).scalar()
    assert live is not None, "the index is not in the database"
    assert "UNIQUE" in live and "active_marker IS TRUE" in live


async def _count_jobs(db, identity):
    return len((await db.execute(
        select(ReportExportJob)
        .where(ReportExportJob.identity == identity))).scalars().all())


# ═══ concurrency ════════════════════════════════════════════════════════════

async def test_the_database_refuses_a_second_active_job(rolled_back_db):
    """The guard is the partial unique index, not a prior SELECT.

    Inserted directly, bypassing `request_job`, because `request_job`'s own
    check would hide whether the database would have refused. What is being
    tested here is the constraint.
    """
    from sqlalchemy.exc import IntegrityError

    db = rolled_back_db
    intake_id = await _estate(db)
    identity = _identity(intake_id)

    await _request(db, intake_id)

    db.add(ReportExportJob(
        identity=identity, export_type="onc_review_workbook",
        source_intake_id=intake_id, classification="DEVELOPMENT_TEST",
        generator_version="workbook 1.0.0",
        state=ReportExportJob.STATE_QUEUED, active_marker=True,
        requested_by="racer@synthetic.test"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ═══ the runner ═════════════════════════════════════════════════════════════

async def test_a_claimed_job_runs_to_succeeded_and_names_its_artifact(
        rolled_back_db):
    from app.reports.export_runner import run_export_job

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)

    claimed = await export_jobs.claim_next_queued(db)
    assert claimed is not None and str(claimed.id) == str(job.id)
    assert claimed.state == ReportExportJob.STATE_RUNNING

    state = await run_export_job(db, claimed)

    assert state == ReportExportJob.STATE_SUCCEEDED
    done = await export_jobs.get_job(db, job.id)
    assert done.report_id and done.artifact_id
    assert done.rendered_sha256 and len(done.rendered_sha256) == 64
    assert done.size_bytes > 0
    assert done.phase == "Ready"
    # The slot is released, so the same export may be asked for again.
    assert done.active_marker is None


async def test_a_failed_generator_registers_no_artifact(rolled_back_db,
                                                        monkeypatch):
    from app.reports.data import artifact_registry
    import app.reports.export_runner as runner

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)
    claimed = await export_jobs.claim_next_queued(db)

    def explode(_dataset):
        raise MemoryError("synthetic render failure")

    monkeypatch.setattr("app.reports.engine.xlsx_engine.render_workbook", explode)

    registered = []
    original = artifact_registry.finalize_artifact

    async def watched(*a, **k):
        registered.append(k.get("report_id"))
        return await original(*a, **k)

    monkeypatch.setattr(artifact_registry, "finalize_artifact", watched)

    state = await runner.run_export_job(db, claimed)

    assert state == ReportExportJob.STATE_FAILED
    assert registered == [], "a failed export registered an artifact"
    failed = await export_jobs.get_job(db, job.id)
    assert failed.report_id is None and failed.artifact_id is None
    assert failed.active_marker is None
    assert "MemoryError" in (failed.error_reason or "")


async def test_a_failure_reason_is_a_sentence_not_a_traceback(rolled_back_db,
                                                              monkeypatch):
    db = rolled_back_db
    intake_id = await _estate(db)
    await _request(db, intake_id)
    claimed = await export_jobs.claim_next_queued(db)

    def explode(_dataset):
        raise RuntimeError(
            'File "/srv/app/secret/path.py", line 4, in f\n  password=hunter2')

    monkeypatch.setattr("app.reports.engine.xlsx_engine.render_workbook", explode)

    import app.reports.export_runner as runner
    await runner.run_export_job(db, claimed)

    reason = (await export_jobs.get_job(db, claimed.id)).error_reason
    for leak in ("Traceback", "/srv/", "hunter2", "password", "line 4"):
        assert leak not in reason, f"the failure reason leaks {leak!r}"
    assert "RuntimeError" in reason and "Nothing was registered" in reason


async def test_a_refused_workbook_keeps_its_own_reason(rolled_back_db):
    """`WorkbookRefused` is a controlled outcome and its wording is safe."""
    import app.reports.export_runner as runner

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)
    claimed = await export_jobs.claim_next_queued(db)
    claimed.source_intake_id = uuid.uuid4()          # a delivery that is not there

    state = await runner.run_export_job(db, claimed)

    assert state == ReportExportJob.STATE_FAILED
    assert "No delivery" in (await export_jobs.get_job(db, job.id)).error_reason


# ═══ the reaper ═════════════════════════════════════════════════════════════

async def test_a_worker_that_stops_reporting_is_failed_and_releases_its_slot(
        rolled_back_db):
    """A process that dies cannot say it died. Silence is the only signal."""
    from datetime import datetime, timedelta

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)
    claimed = await export_jobs.claim_next_queued(db)

    claimed.heartbeat_at = datetime.utcnow() - timedelta(
        seconds=export_jobs.STALE_HEARTBEAT_SECONDS + 60)
    await db.commit()

    reaped = await export_jobs.reap_stale_jobs(db)

    assert [r["job_id"] for r in reaped] == [str(job.id)]
    dead = await export_jobs.get_job(db, job.id)
    assert dead.state == ReportExportJob.STATE_FAILED
    assert dead.error_reason == export_jobs.REAPED_REASON
    assert dead.active_marker is None
    # and the export can now be asked for again
    again = await _request(db, intake_id)
    assert str(again.id) != str(job.id)


async def test_a_healthy_long_run_is_not_reaped(rolled_back_db):
    """The threshold is comfortably longer than the measured full-scale render,
    so a slow but living export is never killed for being slow."""
    db = rolled_back_db
    intake_id = await _estate(db)
    await _request(db, intake_id)
    claimed = await export_jobs.claim_next_queued(db)

    await export_jobs.heartbeat(db, claimed.id, phase="Building workbook")
    assert await export_jobs.reap_stale_jobs(db) == []
    assert export_jobs.STALE_HEARTBEAT_SECONDS > 450, (
        "the reaper would kill a full-scale export, measured at ~7.5 minutes")


# ═══ polling is a read ══════════════════════════════════════════════════════

async def test_reading_status_never_starts_work(rolled_back_db):
    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)

    before = await _count_jobs(db, _identity(intake_id))
    for _ in range(5):
        seen = await export_jobs.get_job(db, job.id)
        assert seen.state == ReportExportJob.STATE_QUEUED
    assert await _count_jobs(db, _identity(intake_id)) == before


def test_the_status_endpoint_mutates_nothing():
    """Asserted structurally as well as behaviourally: a status handler that
    claimed, queued or committed would turn a browser left open on this page
    into a generator of exports."""
    import ast

    import app.reports.routes as routes

    tree = ast.parse(_dedent(inspect.getsource(routes.export_job_status)))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # BOTH shapes. `db.commit()` is an Attribute call and
        # `claim_next_queued(db)` is a Name call; collecting only the first
        # left the second invisible, and a mutation that added exactly that
        # went undetected.
        if isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            called.add(node.func.id)

    for forbidden in ("commit", "add", "flush", "claim_next_queued",
                      "request_job", "run_export_job", "finish_succeeded",
                      "finish_failed", "reap_stale_jobs"):
        assert forbidden not in called, (
            f"the status endpoint calls {forbidden} — polling has side effects")


# ═══ ownership ══════════════════════════════════════════════════════════════

async def test_another_users_job_is_not_readable(rolled_back_db):
    """404, not 403. "Not yours" still confirms the job exists, which is how a
    job id becomes an enumeration oracle."""
    from fastapi import HTTPException

    import app.reports.routes as routes

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id, by=QA.email)

    with pytest.raises(HTTPException) as raised:
        await routes.export_job_status(str(job.id), db=db, user=OTHER)
    assert raised.value.status_code == 404

    mine = await routes.export_job_status(str(job.id), db=db, user=QA)
    assert mine["job_id"] == str(job.id)


async def test_a_supervisor_can_read_any_job(rolled_back_db):
    """Someone has to be able to see a failure that is not their own."""
    import app.reports.routes as routes

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id, by=QA.email)

    seen = await routes.export_job_status(str(job.id), db=db, user=BOSS)
    assert seen["job_id"] == str(job.id)


async def test_an_unknown_job_id_is_a_clean_404(rolled_back_db):
    from fastapi import HTTPException

    import app.reports.routes as routes

    for bad in (str(uuid.uuid4()), "not-a-uuid", "'; DROP TABLE x; --"):
        with pytest.raises(HTTPException) as raised:
            await routes.export_job_status(bad, db=rolled_back_db, user=QA)
        assert raised.value.status_code == 404


async def test_a_job_names_an_artifact_and_never_where_it_lives(rolled_back_db):
    import app.reports.routes as routes

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)
    await export_jobs.finish_succeeded(
        db, job.id, report_id="ONC-REVIEW-Y", artifact_id="a2",
        artifact_version=3, rendered_sha256="a" * 64, size_bytes=99)

    payload = await routes.export_job_status(str(job.id), db=db, user=QA)

    blob = repr(payload).lower()
    for leak in ("storage_locator", "storage_backend", "local://", "c:\\\\",
                 "/srv/", "blob.core.windows.net"):
        assert leak not in blob, f"the job status exposes {leak!r}"
    assert payload["download"].startswith("/api/reports/artifacts/")
    assert payload["rendered_sha256"] == "a" * 64


# ═══ RBAC ═══════════════════════════════════════════════════════════════════

def test_creating_and_reading_an_export_both_need_qalead():
    """A background endpoint must not become a way to gain export authority."""
    from app.core.security import ROLE_HIERARCHY
    import app.reports.routes as routes

    def floors(route):
        found = []
        for dependency in route.dependant.dependencies:
            for cell in getattr(dependency.call, "__closure__", None) or ():
                value = cell.cell_contents
                if isinstance(value, str) and value in ROLE_HIERARCHY:
                    found.append(value)
        return found

    for path in ("/api/reports/exports/onc-review-workbook",
                 "/api/reports/exports/jobs/{job_id}"):
        route = next(r for r in routes.router.routes if r.path == path)
        got = floors(route)
        assert got, f"{path} enforces no role"
        assert min(ROLE_HIERARCHY[f] for f in got) >= ROLE_HIERARCHY["qalead"], (
            f"{path} floor is {got}, below qalead")


# ═══ audit ══════════════════════════════════════════════════════════════════

async def test_an_export_request_is_audited_without_copying_the_data(
        rolled_back_db):
    from app.models.database import AuditLog
    from app.reports.data.export_audit import (ACTION_REQUESTED,
                                               record_export_event)

    db = rolled_back_db
    intake_id = await _estate(db)
    job = await _request(db, intake_id)

    await record_export_event(
        db, action=ACTION_REQUESTED, actor=QA.email, job_id=str(job.id),
        detail="A controlled export was requested.",
        extra={"delivery": "synthetic", "intake_id": str(intake_id),
               "classification": "DEVELOPMENT_TEST"})

    entry = (await db.execute(
        select(AuditLog).where(AuditLog.resource_id == str(job.id))
    )).scalars().first()

    assert entry is not None
    assert entry.action == ACTION_REQUESTED
    assert entry.event_type == "reporting"
    assert entry.outcome == "success"
    assert entry.details["actor"] == QA.email
    # who, what, when, which delivery, which version, outcome — and no rows.
    blob = repr(entry.details).lower()
    for leak in ("9.99.999.", "synthetic org", "password", "token"):
        assert leak not in blob, f"the audit entry copied {leak!r}"


async def test_a_failed_export_is_audited_as_a_failure(rolled_back_db):
    from app.models.database import AuditLog
    from app.reports.data.export_audit import ACTION_FAILED, record_export_event

    db = rolled_back_db
    job_id = str(uuid.uuid4())
    await record_export_event(db, action=ACTION_FAILED, actor=QA.email,
                              job_id=job_id, detail="Synthetic failure.")

    entry = (await db.execute(
        select(AuditLog).where(AuditLog.resource_id == job_id))).scalars().first()
    assert entry.outcome == "failure", (
        "a failed export was recorded as a success; 'what failed' is an "
        "indexed query and this would not answer it")


# ═══ the fixtures are synthetic ═════════════════════════════════════════════

async def test_no_government_export_job_exists(rolled_back_db):
    """Read-only. The delivered population must have no export job at all."""
    db = rolled_back_db
    government = (await db.execute(text(
        "SELECT count(*) FROM report_export_jobs j "
        "JOIN rce_source_intakes i ON i.id = j.source_intake_id "
        "WHERE i.record_count > 1000"))).scalar()
    assert government == 0, (
        f"{government} export job(s) exist against the delivered population")


def test_fixtures_are_synthetic_only():
    assert step17.ARC.startswith("9.99.")
    for actor in (QA, OTHER, BOSS):
        assert actor.email.endswith("@synthetic.test")


def test_the_export_scheduler_is_started_at_application_startup():
    """A poller that never runs leaves every export QUEUED forever.

    The queue would still accept jobs and the UI would still poll them, so the
    failure would look like a slow export rather than an absent worker.
    """
    import app.main

    source = inspect.getsource(app.main)
    assert "start_export_scheduler" in source


def test_only_one_export_runs_at_a_time():
    """A capacity control, not just a design preference.

    Step #18A measured one full-population render at ~659 MB of python heap
    against a 2 GB App Service plan shared with the web process. Two concurrent
    exports would double that for no gain — both would be competing for the same
    threadpool anyway. The limit is structural rather than a counter: the poller
    claims ONE job and runs it inline, and the scheduler refuses overlapping
    ticks. This asserts both halves, because losing either one silently doubles
    the memory ceiling.
    """
    import ast

    import app.reports.export_scheduler as scheduler

    tick = ast.parse(_dedent(inspect.getsource(scheduler._poll_tick)))
    claims = [n for n in ast.walk(tick)
              if isinstance(n, ast.Call)
              and getattr(n.func, "id", getattr(n.func, "attr", None))
              == "claim_next_queued"]
    assert len(claims) == 1, (
        f"the poller claims {len(claims)} jobs per tick; one export at a time is "
        f"a memory ceiling, not a preference")
    assert not any(isinstance(n, (ast.For, ast.While)) for n in ast.walk(tick)), (
        "the poller loops over the queue — it would run several exports in one "
        "tick")

    start = inspect.getsource(scheduler.start_export_scheduler)
    assert "max_instances=1" in start, (
        "overlapping scheduler ticks are permitted; two ticks means two exports")
