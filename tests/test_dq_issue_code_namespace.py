"""Two legitimate quality runs on one calendar date must both survive.

THE INVARIANTS
──────────────
 1. Every DQ issue's machine identity is its UUID primary key, not its code.
 2. Two legitimate runs must not collide merely because they share a date.
 3. Issues stay attributable to the correct run and the correct delivery.
 4. A re-run never silently overwrites historical issues.
 5. Uniqueness is never weakened to make a second run succeed.
 6. Concurrent runs cannot produce duplicate codes.
 7. Delivered Government issue codes are never rewritten.

WHAT WAS WRONG
──────────────
`issue_code` was `DQ-<YYYYMMDD>-<NNNNNN>` and `sequence` restarts at 1 on every
run, while `rce_issues.issue_code` is globally unique via
`ix_rce_issues_issue_code`. The calendar date is not the identity of anything,
so the namespace held exactly one run per day.

Measured before the fix, on synthetic data: delivery A completed with 17 issues;
delivery B, on the same effective date, was refused with
`UniqueViolationError: duplicate key value violates unique constraint
"ix_rce_issues_issue_code"` and persisted **zero** rows. It had never surfaced
because exactly one Government quality run has ever executed.

THE FIX, AND WHY IT IS SHAPED THIS WAY
──────────────────────────────────────
The run already has a stable identifier — its own UUID, already stored on every
issue as `run_id` — so the code is namespaced by it:
`DQ-<YYYYMMDD>-<8 hex of run id>-<NNNNNN>`, 27 characters inside the existing
`String(30)`.

Deliberately NOT used: `MAX(issue_code)+1` (a read-then-write race), a retry
loop, a sleep, `ON CONFLICT DO NOTHING` (drops issues), a process-local counter,
or relaxing the unique index. Two runs cannot share a prefix by construction,
and the index stays the final boundary rather than something the format is
trusted to replace.

WHAT THIS FIX DOES NOT DECIDE — read before extending
─────────────────────────────────────────────────────
It makes a second run POSSIBLE; it does not say which run is authoritative.
Nothing filters `rce_issues` by `run_id` — curation, reporting and the issue API
all read every issue for the intake — so two runs over one delivery present a
doubled issue population downstream. That is a separate, undefined semantic
(there is no equivalent of `app.Tefca.evidence_version.current_filter` for the
issue ledger) and is deliberately left alone here rather than invented.

GOVERNMENT DATA
Every database test runs inside an OUTER transaction that is rolled back, with
the session joined via `join_transaction_mode="create_savepoint"` so the
engine's internal commits never reach disk. Fixtures are synthetic: OIDs under
an unassigned `9.99.777` arc, prefixed names, no NPI.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import quality_engine as qe
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint
from app.tefca_registry.rce.quality_engine import issue_code, run_quality_engine

SYN = "SYNTHETIC-DQ"
DAY1 = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
DAY2 = DAY1 + timedelta(days=1)

#: The column the code has to fit in.
CODE_MAX = m.RceIssue.__table__.c.issue_code.type.length


# ── the generator, no database needed ────────────────────────────────────────

def test_issue_code_is_namespaced_by_the_run_not_only_the_date():
    """Two runs, one date, same sequence number — the codes must differ."""
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    a = issue_code(1, DAY1, run_ref=run_a)
    b = issue_code(1, DAY1, run_ref=run_b)
    assert a != b, (
        "sequence restarts every run, so the date alone cannot be the namespace"
    )
    assert a.startswith("DQ-20260915-")
    assert run_a.hex[:8] in a and run_b.hex[:8] in b


def test_issue_code_fits_the_column_at_realistic_volumes():
    """The delivered population produced 36,916 issues in one run."""
    run = uuid.uuid4()
    for sequence in (1, 36_916, 999_999):
        code = issue_code(sequence, DAY1, run_ref=run)
        assert len(code) <= CODE_MAX, f"{code!r} exceeds String({CODE_MAX})"


def test_sequence_ordering_within_a_run_is_preserved():
    """Codes must still sort by sequence, so the issue listing stays ordered."""
    run = uuid.uuid4()
    codes = [issue_code(n, DAY1, run_ref=run) for n in range(1, 21)]
    assert codes == sorted(codes)


def test_the_historical_two_part_format_is_still_expressible():
    """TEST 8: the 36,916 delivered codes are never rewritten.

    Nothing parses `issue_code` and no foreign key references it, so the two
    shapes coexist; this pins that the old form is still producible rather than
    silently redefined.
    """
    legacy = issue_code(1, DAY1)
    assert legacy == "DQ-20260915-000001"
    assert re.fullmatch(r"DQ-\d{8}-\d{6}", legacy)


def test_uniqueness_is_still_enforced_by_the_database():
    """TEST 5 / INVARIANT 5: the index was not relaxed to make runs fit."""
    column = m.RceIssue.__table__.c.issue_code
    assert column.nullable is False
    indexes = {i.name: i for i in m.RceIssue.__table__.indexes}
    unique_on_code = [
        i for i in m.RceIssue.__table__.indexes
        if i.unique and [c.name for c in i.columns] == ["issue_code"]
    ]
    assert column.unique is True or unique_on_code, (
        "issue_code must remain uniquely constrained; the fix namespaces the "
        "value, it does not relax the guarantee"
    )
    assert indexes is not None


# ── database-backed ──────────────────────────────────────────────────────────

def _fixed_clock(when: datetime):
    naive = when.replace(tzinfo=None)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when if tz else naive

        @classmethod
        def utcnow(cls):
            return naive

    return _Clock


def _rows(tag, n=4):
    base = {f: "" for f in RCE_FIELDS}
    base.update({
        "domains": "RCE", "orgManagingOrg": "9.99.777.0.1",
        "purposesofuse": "T-TRTMNT", "active": "1",
        "sequoiaorgtype": "Participant", "address_line": "1 Synthetic Way",
        "address_city": "Testville", "address_state": "MA",
        "address_postalCode": "99999", "address_country": "USA",
        "partOf": "9.99.777.0.1",
    })
    out = []
    for i in range(1, n + 1):
        r = dict(base)
        r["id"] = f"9.99.777.{tag}.{i}"
        r["TEFCAID"] = f"{SYN}-{tag}-TEFCAID-{i:04d}"
        r["HCID"] = f"urn:oid:9.99.777.{tag}.{i}"
        r["name"] = f"{SYN} {tag} ORG {i}"
        out.append(r)
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
            field_count=len(RCE_FIELDS), parse_status="ok",
            promotion_status="pending"))
    await db.commit()
    return intake_id


async def _run_on(db, intake_id, when, monkeypatch):
    monkeypatch.setattr(qe, "datetime", _fixed_clock(when))
    try:
        return await run_quality_engine(db, intake_id, executed_by=SYN)
    finally:
        monkeypatch.undo()


async def _issues(db, intake_id):
    return (await db.execute(
        select(m.RceIssue).where(m.RceIssue.source_intake_id == intake_id)
        .order_by(m.RceIssue.issue_code))).scalars().all()


@pytest.fixture
async def rolled_back_db(db_required):
    """Session on an outer transaction that is rolled back.

    A dedicated engine, not the app's global one: `conftest._use_null_pool()`
    rebuilds that engine's pool and loses the `on_connect` listeners that
    register asyncpg's JSON codecs, so JSONB reads back as raw text under
    pytest and `intake.source_metadata` arrives as a str.
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


# TEST 1
async def test_one_run_creates_unique_issue_codes(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id = await _seed(db, "A")
    result = await _run_on(db, intake_id, DAY1, monkeypatch)

    issues = await _issues(db, intake_id)
    assert len(issues) == result["issues_generated"] > 0
    codes = [i.issue_code for i in issues]
    assert len(codes) == len(set(codes))
    assert all(len(c) <= CODE_MAX for c in codes)


# TEST 2 + 3 + 4 + 5 + 6 + 13 — the gate
async def test_two_deliveries_on_the_same_date_both_complete(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    a = await _seed(db, "A")
    b = await _seed(db, "B")

    result_a = await _run_on(db, a, DAY1, monkeypatch)
    codes_a = [i.issue_code for i in await _issues(db, a)]
    assert codes_a, "delivery A must produce issues for this test to mean anything"

    # TEST 2 — the second delivery, same calendar date, must not be refused.
    result_b = await _run_on(db, b, DAY1, monkeypatch)
    issues_b = await _issues(db, b)
    codes_b = [i.issue_code for i in issues_b]

    # TEST 13 — nothing was dropped to dodge a collision.
    assert len(codes_b) == result_b["issues_generated"] > 0

    # TEST 3 — no overlap.
    assert set(codes_a).isdisjoint(codes_b)
    assert all(c.startswith("DQ-20260915-") for c in codes_a + codes_b), (
        "both runs are on the same date, which is the whole point"
    )

    # TEST 4 — every issue names its own run, and the code carries that run.
    run_b = uuid.UUID(result_b["run_id"])
    assert {i.run_id for i in issues_b} == {run_b}
    assert all(run_b.hex[:8] in i.issue_code for i in issues_b)

    # TEST 5 — and the correct delivery.
    assert {i.source_intake_id for i in issues_b} == {b}

    # TEST 6 — A is untouched.
    assert [i.issue_code for i in await _issues(db, a)] == codes_a
    assert uuid.UUID(result_a["run_id"]) != run_b


# TEST 9
async def test_different_dates_continue_to_work(rolled_back_db, monkeypatch):
    db = rolled_back_db
    a = await _seed(db, "A")
    c = await _seed(db, "C")

    await _run_on(db, a, DAY1, monkeypatch)
    await _run_on(db, c, DAY2, monkeypatch)

    codes_a = [i.issue_code for i in await _issues(db, a)]
    codes_c = [i.issue_code for i in await _issues(db, c)]
    assert all(x.startswith("DQ-20260915-") for x in codes_a)
    assert all(x.startswith("DQ-20260916-") for x in codes_c)
    assert set(codes_a).isdisjoint(codes_c)


# TEST 10
async def test_rules_produce_the_same_substantive_results_across_runs(
        rolled_back_db, monkeypatch):
    """The namespace changed; the findings must not have.

    Compares the substance of two runs over one delivery — rule, type, severity,
    authority, field and the record it is about — ignoring only the identity
    columns that are expected to differ.
    """
    db = rolled_back_db
    intake_id = await _seed(db, "A")

    first = await _run_on(db, intake_id, DAY1, monkeypatch)
    run_one = uuid.UUID(first["run_id"])
    before = [
        (i.rule_id, i.issue_type, i.severity, i.correction_authority,
         i.field_name, i.source_record_id, i.issue_code.rsplit("-", 1)[1])
        for i in await _issues(db, intake_id) if i.run_id == run_one
    ]

    second = await _run_on(db, intake_id, DAY1, monkeypatch)
    run_two = uuid.UUID(second["run_id"])
    after = [
        (i.rule_id, i.issue_type, i.severity, i.correction_authority,
         i.field_name, i.source_record_id, i.issue_code.rsplit("-", 1)[1])
        for i in await _issues(db, intake_id) if i.run_id == run_two
    ]

    assert sorted(before) == sorted(after), (
        "the same delivery and rule set must yield the same findings, with the "
        "same sequence positions — only the run namespace differs"
    )
    assert first["issues_generated"] == second["issues_generated"]
    assert run_one != run_two


# TEST 7
async def test_a_colliding_run_persists_nothing(rolled_back_db, monkeypatch):
    """TEST 7: atomicity — a refused run leaves no partial issue ledger.

    The collision is forced by making the generator return one constant, which
    is the only way to provoke it now that the namespace is correct. What is
    under test is the transaction, not the format.
    """
    db = rolled_back_db
    intake_id = await _seed(db, "A")

    monkeypatch.setattr(qe, "issue_code",
                        lambda sequence, when=None, run_ref=None: "DQ-COLLIDE-000001")
    with pytest.raises(IntegrityError):
        await run_quality_engine(db, intake_id, executed_by=SYN)
    await db.rollback()
    monkeypatch.undo()

    assert await _issues(db, intake_id) == [], "a refused run persisted issues"
    runs = int((await db.execute(
        select(func.count()).select_from(m.RceIngestionRun)
        .where(m.RceIngestionRun.source_intake_id == intake_id))).scalar() or 0)
    assert runs == 0, "a refused run left an orphan ingestion-run row"

    # And the delivery can still be quality-run afterwards.
    result = await _run_on(db, intake_id, DAY1, monkeypatch)
    assert result["issues_generated"] > 0


# TEST 11
async def test_only_the_synthetic_intakes_are_touched(rolled_back_db, monkeypatch):
    """TEST 11: nothing outside this test's own deliveries changes."""
    db = rolled_back_db
    before_total = int((await db.execute(
        select(func.count()).select_from(m.RceIssue))).scalar() or 0)

    intake_id = await _seed(db, "A")
    result = await _run_on(db, intake_id, DAY1, monkeypatch)

    after_total = int((await db.execute(
        select(func.count()).select_from(m.RceIssue))).scalar() or 0)
    assert after_total - before_total == result["issues_generated"], (
        "the run wrote outside its own delivery"
    )
    # Pre-existing rows keep whatever code shape they already had.
    foreign = int((await db.execute(
        select(func.count()).select_from(m.RceIssue)
        .where(m.RceIssue.source_intake_id != intake_id,
               m.RceIssue.issue_code.like("DQ-20260915-%")))).scalar() or 0)
    assert foreign == 0


# TEST 14
def test_fixtures_are_synthetic_only():
    for tag in ("A", "B", "C"):
        for r in _rows(tag):
            assert r["id"].startswith("9.99.777."), "OIDs stay in an unassigned arc"
            assert r["name"].startswith(SYN)
            assert r["TEFCAID"].startswith(SYN)
            assert r["NPI"] == ""
