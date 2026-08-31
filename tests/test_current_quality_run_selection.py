"""Exactly one quality run is CURRENT for a delivery; history stays reachable.

THE INVARIANT
─────────────
    Historical DQ runs and their issues remain immutable and reconstructable,
    while normal operational processing for one delivery uses exactly one
    explicitly defined current quality run.

WHAT WAS WRONG
──────────────
    `RceIngestionRun` exists because "a delivery may be processed repeatedly as
    rules change", and every run writes a FULL set of issues for the delivery.
    Every operational reader filtered on `source_intake_id` alone, so after two
    runs they saw both assessments at once. Measured on synthetic data before
    the fix: run 1 wrote 17 issues, run 2 wrote 17, and the operational query
    returned 34 — doubled counts, doubled records-affected, and a doubled
    HUMAN_REQUIRED workload for whoever picks these up next.

    It had never surfaced because the same-day `issue_code` collision made a
    second run impossible until the previous gate fixed it. Fixing that made
    this reachable, which is why it is being closed before anything is built on
    the issue ledger.

THE SEMANTIC
────────────
    CURRENT = the most recently COMPLETED run for that intake,
              ordered `completed_at DESC, started_at DESC, id DESC`.
    HISTORY = every earlier run, still queryable, never deleted.

    Selection is gated on completion, not on `MAX(created_at)`: a RUNNING run
    has not finished and a FAILED one did not succeed, so neither displaces the
    last good assessment. An aborted run cannot displace anything at all,
    because the run row and its issues commit in one transaction — a run that
    dies leaves no row and no issues.

    Determinism is not the same as safety. Two concurrent runs over ONE intake
    both complete, and which finishes last is a race. The selector answers
    consistently; it does not make that race a good idea. See
    `test_two_concurrent_same_intake_runs_leave_one_unambiguous_current`.

GOVERNMENT DATA
    Every database test runs inside an OUTER transaction that is rolled back,
    with the session joined via `join_transaction_mode="create_savepoint"`.
    Fixtures are synthetic: OIDs under an unassigned `9.99.666` arc, prefixed
    names, no NPI.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import quality_engine as qe
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint
from app.tefca_registry.rce.quality_engine import issue_summary, run_quality_engine

SYN = "SYNTHETIC-RUNSEL"
DAY = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


# ── fixtures ─────────────────────────────────────────────────────────────────

def _rows(tag, n=4):
    base = {f: "" for f in RCE_FIELDS}
    base.update({
        "domains": "RCE", "orgManagingOrg": "9.99.666.0.1",
        "purposesofuse": "T-TRTMNT", "active": "1",
        "sequoiaorgtype": "Participant", "address_line": "1 Synthetic Way",
        "address_city": "Testville", "address_state": "MA",
        "address_postalCode": "99999", "address_country": "USA",
        "partOf": "9.99.666.0.1",
    })
    out = []
    for i in range(1, n + 1):
        r = dict(base)
        r["id"] = f"9.99.666.{tag}.{i}"
        r["TEFCAID"] = f"{SYN}-{tag}-{i:04d}"
        r["HCID"] = f"urn:oid:9.99.666.{tag}.{i}"
        r["name"] = f"{SYN} {tag} ORG {i}"
        out.append(r)
    # One malformed NPI so the fixture carries a HUMAN_REQUIRED finding.
    out[0]["NPI"] = "12345"
    return out


async def _seed(db, tag, n=4):
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
        db.add(m.RceSourceRecord(
            id=uuid.uuid4(), source_intake_id=intake_id,
            line_number=line_number, raw_line=raw, parsed=r,
            record_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            source_rce_id=r["id"], tefcaid=r["TEFCAID"], hcid=r["HCID"],
            npi=r["NPI"] or None, field_count=len(RCE_FIELDS),
            parse_status="ok", promotion_status="pending"))
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


async def _count(db, predicate) -> int:
    return int((await db.execute(
        select(func.count()).select_from(m.RceIssue).where(predicate))).scalar() or 0)


async def _all_runs_count(db, intake_id) -> int:
    return await _count(db, run_selection.issues_filter(intake_id, all_runs=True))


async def _current_count(db, intake_id) -> int:
    return await _count(db, run_selection.current_issues_filter(intake_id))


# ── TEST 1 ───────────────────────────────────────────────────────────────────

async def test_a_single_completed_run_is_current(rolled_back_db):
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    result = await run_quality_engine(db, intake_id, executed_by=SYN)

    run = await run_selection.current_run(db, intake_id)
    assert run is not None
    assert str(run.id) == result["run_id"]
    assert run.run_status == "COMPLETE" and run.completed_at is not None

    n = result["issues_generated"]
    assert n > 0
    assert await _current_count(db, intake_id) == n
    assert await _all_runs_count(db, intake_id) == n


# ── TEST 2 / 3 / 4 / 5 / 9 — the gate ────────────────────────────────────────

async def test_two_runs_yield_one_current_set_while_history_is_preserved(
        rolled_back_db):
    db = rolled_back_db
    intake_id = await _seed(db, "A")

    first = await run_quality_engine(db, intake_id, executed_by=SYN)
    second = await run_quality_engine(db, intake_id, executed_by=SYN)
    per_run = first["issues_generated"]
    assert per_run == second["issues_generated"] > 0

    run_a, run_b = uuid.UUID(first["run_id"]), uuid.UUID(second["run_id"])
    assert run_a != run_b

    # TEST 3 — history still holds both, undeleted.
    assert await _all_runs_count(db, intake_id) == per_run * 2

    # TEST 2 + 9 — the operational view is ONE run, not the union.
    assert await _current_count(db, intake_id) == per_run

    # The newer completed run is the current one.
    current = await run_selection.current_run(db, intake_id)
    assert current.id == run_b

    # TEST 4 + 5 — each run is separately addressable.
    assert await _count(
        db, run_selection.issues_filter(intake_id, run_id=run_a)) == per_run
    assert await _count(
        db, run_selection.issues_filter(intake_id, run_id=run_b)) == per_run

    # Run A's rows are untouched — superseded is not deleted.
    a_rows = (await db.execute(
        select(m.RceIssue.issue_code)
        .where(run_selection.issues_of_run_filter(run_a)))).scalars().all()
    assert len(a_rows) == per_run

    # And both runs remain in the history listing, newest first.
    assert await run_selection.run_history(db, intake_id) == [run_b, run_a]


# ── TEST 6 — a failed newer run must not take over ───────────────────────────

async def test_a_failed_newer_run_does_not_replace_the_current_run(
        rolled_back_db, monkeypatch):
    """A run that dies leaves no row and no issues, so the last good one stands."""
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    good = await run_quality_engine(db, intake_id, executed_by=SYN)
    good_id = uuid.UUID(good["run_id"])
    per_run = good["issues_generated"]

    # Force the second run to abort partway through writing its issues.
    monkeypatch.setattr(
        qe, "issue_code",
        lambda sequence, when=None, run_ref=None: "DQ-FORCED-COLLISION")
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await run_quality_engine(db, intake_id, executed_by=SYN)
    await db.rollback()
    monkeypatch.undo()

    current = await run_selection.current_run(db, intake_id)
    assert current.id == good_id, "a failed run displaced the good one"
    assert await _current_count(db, intake_id) == per_run
    assert await _all_runs_count(db, intake_id) == per_run, (
        "the aborted run left issues behind"
    )
    runs = int((await db.execute(
        select(func.count()).select_from(m.RceIngestionRun)
        .where(m.RceIngestionRun.source_intake_id == intake_id))).scalar() or 0)
    assert runs == 1


# ── TEST 7 — a RUNNING newer run must not take over ──────────────────────────

async def test_a_running_newer_run_does_not_replace_the_current_run(
        rolled_back_db):
    """Selection is gated on COMPLETE, not on being the newest row."""
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    good = await run_quality_engine(db, intake_id, executed_by=SYN)
    good_id = uuid.UUID(good["run_id"])

    # A run row that exists but never finished — what a killed worker would
    # leave if the run row were committed on its own.
    later = m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id,
        rule_set_version="1.0.0", rule_config_hash="x" * 64,
        field_map_version="1.0.0",
        started_at=datetime.utcnow() + timedelta(minutes=5),
        run_status="RUNNING", executed_by=SYN)
    db.add(later)
    await db.commit()

    assert (await run_selection.current_run(db, intake_id)).id == good_id

    # And a FAILED one is equally ignored.
    later.run_status = "FAILED"
    later.completed_at = datetime.utcnow() + timedelta(minutes=6)
    await db.commit()
    assert (await run_selection.current_run(db, intake_id)).id == good_id


# ── TEST 8 — three runs, deterministic ───────────────────────────────────────

async def test_a_third_run_selects_deterministically(rolled_back_db):
    db = rolled_back_db
    intake_id = await _seed(db, "A")

    a = uuid.UUID((await run_quality_engine(db, intake_id, executed_by=SYN))["run_id"])
    b = uuid.UUID((await run_quality_engine(db, intake_id, executed_by=SYN))["run_id"])
    third = await run_quality_engine(db, intake_id, executed_by=SYN)
    c = uuid.UUID(third["run_id"])
    per_run = third["issues_generated"]

    assert await _all_runs_count(db, intake_id) == per_run * 3
    assert await _current_count(db, intake_id) == per_run

    # Repeated calls agree — the ordering is total, not incidental.
    picks = {(await run_selection.current_run(db, intake_id)).id for _ in range(5)}
    assert picks == {c}
    assert await run_selection.run_history(db, intake_id) == [c, b, a]
    # A and B remain separately retrievable.
    for run in (a, b):
        assert await _count(db, run_selection.issues_of_run_filter(run)) == per_run


# ── TEST 10 — the workload that matters next ─────────────────────────────────

async def test_human_required_workload_does_not_double(rolled_back_db):
    """The count a future review_records bridge will consume.

    This gate builds no bridge; it proves the selector that one would use.
    """
    db = rolled_back_db
    intake_id = await _seed(db, "A")

    await run_quality_engine(db, intake_id, executed_by=SYN)
    human = run_selection.current_issues_filter(intake_id) & (
        m.RceIssue.correction_authority == "HUMAN_REQUIRED")
    before = await _count(db, human)
    assert before > 0, "the fixture must produce HUMAN_REQUIRED findings"

    await run_quality_engine(db, intake_id, executed_by=SYN)
    after = await _count(db, human)
    assert after == before, (
        f"HUMAN_REQUIRED workload doubled from {before} to {after} merely "
        f"because the delivery was quality-run twice"
    )
    all_runs = await _count(
        db, run_selection.issues_filter(intake_id, all_runs=True)
        & (m.RceIssue.correction_authority == "HUMAN_REQUIRED"))
    assert all_runs == before * 2, "history must still show both assessments"


# ── TEST 12 — intakes are independent ────────────────────────────────────────

async def test_each_intake_selects_its_own_current_run(rolled_back_db):
    db = rolled_back_db
    a = await _seed(db, "A")
    b = await _seed(db, "B")

    a1 = await run_quality_engine(db, a, executed_by=SYN)
    b1 = await run_quality_engine(db, b, executed_by=SYN)
    a2 = await run_quality_engine(db, a, executed_by=SYN)   # A gets a newer run

    assert (await run_selection.current_run(db, a)).id == uuid.UUID(a2["run_id"])
    assert (await run_selection.current_run(db, b)).id == uuid.UUID(b1["run_id"])
    assert await _current_count(db, a) == a1["issues_generated"]
    assert await _current_count(db, b) == b1["issues_generated"]
    # B is unaffected by A having been re-run.
    assert await _all_runs_count(db, b) == b1["issues_generated"]


# ── TEST 13 — concurrency, stated honestly ───────────────────────────────────

async def test_two_concurrent_same_intake_runs_leave_one_unambiguous_current(
        rolled_back_db):
    """Two completed runs, whatever their timing, resolve to exactly one current.

    True simultaneity needs two committing transactions and so cannot run
    inside this rolled-back session; what is proven here is the property that
    matters — that the ordering is TOTAL, so no pair of completed runs can leave
    "current" ambiguous, including two that finish in the same microsecond.

    This is determinism, not a licence to run two assessments of one delivery at
    once: which run finishes last is still a race, and same-intake concurrency
    should be serialised operationally.
    """
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    first = await run_quality_engine(db, intake_id, executed_by=SYN)
    second = await run_quality_engine(db, intake_id, executed_by=SYN)

    # Force the pathological case: identical completion instants.
    same_instant = datetime.utcnow()
    for run_id in (uuid.UUID(first["run_id"]), uuid.UUID(second["run_id"])):
        run = await db.get(m.RceIngestionRun, run_id)
        run.completed_at = same_instant
        run.started_at = same_instant
    await db.commit()

    picks = {(await run_selection.current_run(db, intake_id)).id for _ in range(5)}
    assert len(picks) == 1, "a tie left 'current' ambiguous"
    # Still exactly one run's worth of issues, never the union.
    assert await _current_count(db, intake_id) == first["issues_generated"]


# ── TEST 11 — nothing outside the fixtures moves ─────────────────────────────

async def test_government_issue_rows_are_untouched(rolled_back_db):
    db = rolled_back_db
    before = int((await db.execute(
        select(func.count()).select_from(m.RceIssue))).scalar() or 0)
    orphans_before = int((await db.execute(
        select(func.count()).select_from(m.RceIssue)
        .where(m.RceIssue.run_id.is_(None)))).scalar() or 0)

    intake_id = await _seed(db, "A")
    result = await run_quality_engine(db, intake_id, executed_by=SYN)

    after = int((await db.execute(
        select(func.count()).select_from(m.RceIssue))).scalar() or 0)
    assert after - before == result["issues_generated"]
    # The write path must keep every issue attributable to its run, or the
    # current-run filter would hide it rather than report it.
    assert await _count(db, run_selection.orphaned_issue_filter(intake_id)) == 0
    orphans_after = int((await db.execute(
        select(func.count()).select_from(m.RceIssue)
        .where(m.RceIssue.run_id.is_(None)))).scalar() or 0)
    assert orphans_after == orphans_before


# ── the consumers ────────────────────────────────────────────────────────────

async def test_issue_summary_reports_one_run_by_default(rolled_back_db):
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    first = await run_quality_engine(db, intake_id, executed_by=SYN)
    per_run = first["issues_generated"]

    await run_quality_engine(db, intake_id, executed_by=SYN)

    current = await issue_summary(db, intake_id)
    assert current["total"] == per_run, (
        f"the summary summed every run: {current['total']} vs {per_run}"
    )
    history = await issue_summary(db, intake_id, all_runs=True)
    assert history["total"] == per_run * 2, "the audit view must still show both"
    named = await issue_summary(db, intake_id, run_id=uuid.UUID(first["run_id"]))
    assert named["total"] == per_run


async def test_curation_builds_area_2_from_one_run(rolled_back_db):
    """Curation is the consumer whose doubling would reach Area 2 itself."""
    from app.tefca_registry.rce.curation import curate_delivery

    db = rolled_back_db
    intake_id = await _seed(db, "A")
    first = await run_quality_engine(db, intake_id, executed_by=SYN)
    await run_quality_engine(db, intake_id, executed_by=SYN)

    result = await curate_delivery(db, intake_id, curated_by=SYN)
    assert result["every_source_record_curated"] is True

    rows = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id))).scalars().all()
    total_issue_count = sum(r.issue_count for r in rows)

    # Expected is computed from ONE run's id directly, NOT through the selector
    # under test — otherwise a broken selector would move both sides of the
    # assertion together and the test would pass against the old behaviour.
    one_run = await _count(
        db, run_selection.issues_of_run_filter(uuid.UUID(first["run_id"]))
        & m.RceIssue.source_record_id.isnot(None))
    both_runs = await _count(
        db, (m.RceIssue.source_intake_id == intake_id)
        & m.RceIssue.source_record_id.isnot(None))
    assert both_runs == one_run * 2, "the fixture must actually have two runs"
    assert total_issue_count == one_run, (
        f"Area 2 counted {total_issue_count} issues against {one_run} in a "
        f"single run — curation summed two assessments"
    )


# ── TEST 14 ──────────────────────────────────────────────────────────────────

def test_fixtures_are_synthetic_only():
    for tag in ("A", "B"):
        for r in _rows(tag):
            assert r["id"].startswith("9.99.666.")
            assert r["name"].startswith(SYN)
            assert r["TEFCAID"].startswith(SYN)
            assert r["NPI"] in ("", "12345"), "no real NPI in a fixture"
