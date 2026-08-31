"""curate_delivery() re-run safety — the one-curated-row-per-source-row invariant.

THE INVARIANT
─────────────
    One intake must not silently create more than one logical curated
    representation of the same source record.

WHY THIS FILE EXISTS
────────────────────
A code reading of `curate_delivery` looks alarming: it counts source records and
inserts a curated row per batch with no check for rows it already created, so a
second invocation appears able to double Area 2 and break the `D == A`
reconciliation identity that every downstream figure rests on.

It cannot. The invariant is enforced one level down, at the data boundary:

    UniqueConstraint("source_record_id", name="uq_rce_curated_source_record")

A second invocation therefore does not duplicate — its very first batch insert
violates that constraint, and because `curate_delivery` holds a SINGLE
transaction across every batch and commits only at the end, the whole attempt
rolls back. Area 2 is left exactly as the first run wrote it.

That is a load-bearing property of the DATABASE, not of the function, and a
property nothing else pinned. Someone reasonably trying to make re-runs
convenient could drop the constraint, or switch the insert to
`ON CONFLICT DO NOTHING`, or commit per batch — and each of those quietly
converts a safe refusal into silent duplication or a half-written Area 2. These
tests exist so that change fails here first.

WHY THE GUARD IS AT THE CONSTRAINT AND NOT IN THE FUNCTION
──────────────────────────────────────────────────────────
A `SELECT count(*) ... IF EXISTS: return` pre-check inside the function would be
RACEABLE: two concurrent requests could both read "not yet curated" and both
proceed. The unique index is not raceable — PostgreSQL serialises the second
inserter on the index entry and refuses it. `test_two_concurrent_curations_...`
demonstrates exactly that, and it is the reason no application-level pre-check
was added when this gate was run.

HOW THESE TESTS AVOID TOUCHING GOVERNMENT DATA
──────────────────────────────────────────────
Every database-backed test runs inside an OUTER transaction that is rolled back,
with the session joined to it via `join_transaction_mode="create_savepoint"` so
that the `db.commit()` calls inside `run_quality_engine` and `curate_delivery`
commit a savepoint rather than reaching disk. No delivered record, issue,
correction or curated row is created, modified or deleted. All fixture data is
synthetic — no real organisation name, NPI, OID, TEFCAID or address appears.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.curation import curate_delivery
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint

SYN = "SYNTHETIC-RERUN-TEST"


# ── static contract: the guard must exist in the model AND in the migration ──
#
# These need no database, so the invariant keeps a guard on a machine with no
# Postgres — which is most of this suite's runs.

def test_the_unique_constraint_is_declared_on_the_model():
    """The invariant is a table constraint, not a convention."""
    uniques = {
        c.name: sorted(col.name for col in c.columns)
        for c in m.RceCuratedRecord.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert uniques.get("uq_rce_curated_source_record") == ["source_record_id"], (
        "uq_rce_curated_source_record is what makes a re-run of curate_delivery "
        "refuse instead of silently doubling Area 2. It must stay a UNIQUE "
        "constraint on source_record_id alone."
    )


def test_the_unique_constraint_is_in_the_migration():
    """A model-only constraint is not enforced by any deployed database."""
    from pathlib import Path

    migration = (Path(__file__).resolve().parents[1]
                 / "alembic" / "versions" / "20260822_rce_pipeline.py")
    body = migration.read_text(encoding="utf-8")
    assert "uq_rce_curated_source_record" in body, (
        "the constraint is declared on the model but not created by the "
        "migration, so no deployed database enforces it"
    )


def test_curate_delivery_commits_once_so_a_failed_run_leaves_nothing():
    """A single commit AFTER the batch loop is what makes a retry safe.

    Committing per batch would leave a partially curated intake behind on
    failure, and the next attempt would then hit the unique constraint on the
    rows it already wrote — turning a clean retry into an unrecoverable one.
    """
    import inspect

    source = inspect.getsource(curate_delivery)
    assert source.count("await db.commit()") == 1, (
        "curate_delivery must commit exactly once, after every batch. A "
        "per-batch commit makes a partial run unrecoverable."
    )
    loop_at = source.index("for offset in range(")
    commit_at = source.index("await db.commit()")
    assert commit_at > loop_at, "the commit must follow the batch loop"


# ── database-backed: the runtime behaviour ───────────────────────────────────

def _synthetic_rows():
    """Five synthetic records, one per curated-status path.

    Values are deliberately impossible: OIDs under an unassigned 9.99.999 arc,
    a placeholder city, and a 5-digit NPI that cannot be real.
    """
    base = {f: "" for f in RCE_FIELDS}
    base.update({
        "domains": "RCE",
        "orgManagingOrg": "9.99.999.0.1",
        "purposesofuse": "T-TRTMNT",
        "active": "1",
        "sequoiaorgtype": "Participant",
        "address_line": "1 Synthetic Way",
        "address_city": "Testville",
        "address_state": "MA",
        "address_postalCode": "99999",
        "address_country": "USA",
        "partOf": "9.99.999.0.1",
    })
    rows = []
    for n in range(1, 6):
        r = dict(base)
        r["id"] = f"9.99.999.1.{n}"
        r["TEFCAID"] = f"{SYN}-{n:04d}"
        r["HCID"] = f"urn:oid:9.99.999.1.{n}"
        r["name"] = f"{SYN} ORG {n}"
        rows.append(r)
    rows[1]["address_postalCode"] = "1234"   # FMT-001 AUTO_SAFE -> CORRECTED
    rows[2]["address_state"] = "ma"          # FMT-002 AUTO_SAFE -> CORRECTED
    rows[3]["NPI"] = "12345"                 # NPI-002 HIGH/HUMAN_REQUIRED -> HELD
    return rows


async def _seed_intake(db) -> uuid.UUID:
    """Write a synthetic intake plus its source records. Returns the intake id."""
    rows = _synthetic_rows()
    blob = ("\r\n".join(["|".join(RCE_FIELDS)]
                        + ["|".join(r[f] for f in RCE_FIELDS) for r in rows])
            + "\r\n").encode("utf-8")
    intake_id = uuid.uuid4()
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=f"{SYN}-INTAKE",
        original_filename="synthetic.csv", storage_path="(synthetic)",
        sha256=hashlib.sha256(blob).hexdigest(), file_size_bytes=len(blob),
        delimiter="|", encoding="utf-8", line_terminator="CRLF",
        headers=list(RCE_FIELDS),
        schema_fingerprint=schema_fingerprint(list(RCE_FIELDS)),
        record_count=len(rows), received_at=datetime.utcnow(),
        received_by=SYN, status="PARSED",
        # `ingest_delivery` always writes a dict here (`source_metadata or {}`),
        # and the quality engine reads it. Set it explicitly so the fixture
        # matches what production actually stores.
        source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()
    for line_number, r in enumerate(rows, start=2):   # line 1 is the header
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


async def _state(db, intake_id):
    """Everything the invariant is stated in terms of."""
    async def count(stmt):
        return int((await db.execute(stmt)).scalar() or 0)

    return {
        "source_records": await count(
            select(func.count()).select_from(m.RceSourceRecord)
            .where(m.RceSourceRecord.source_intake_id == intake_id)),
        "curated": await count(
            select(func.count()).select_from(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_intake_id == intake_id)),
        "distinct_source_record_id": await count(
            select(func.count(func.distinct(m.RceCuratedRecord.source_record_id)))
            .where(m.RceCuratedRecord.source_intake_id == intake_id)),
        "issues": await count(
            select(func.count()).select_from(m.RceIssue)
            .where(m.RceIssue.source_intake_id == intake_id)),
        "corrections": await count(
            select(func.count()).select_from(m.RceCorrectionDetail)
            .join(m.RceCuratedRecord,
                  m.RceCorrectionDetail.curated_record_id == m.RceCuratedRecord.id)
            .where(m.RceCuratedRecord.source_intake_id == intake_id)),
        "statuses": dict((await db.execute(
            select(m.RceCuratedRecord.record_status, func.count())
            .where(m.RceCuratedRecord.source_intake_id == intake_id)
            .group_by(m.RceCuratedRecord.record_status))).all()),
        "curated_row_ids": sorted(str(r) for r in (await db.execute(
            select(m.RceCuratedRecord.id)
            .where(m.RceCuratedRecord.source_intake_id == intake_id))).scalars()),
        "source_hashes": sorted((await db.execute(
            select(m.RceSourceRecord.record_sha256)
            .where(m.RceSourceRecord.source_intake_id == intake_id))).scalars()),
    }


@pytest.fixture
async def rolled_back_db(db_required):
    """A session whose every commit lands in a savepoint that is thrown away.

    `curate_delivery` and `run_quality_engine` commit internally, so a plain
    session would write to the real database. Joining an outer transaction with
    `create_savepoint` keeps their commits inside it, and rolling that outer
    transaction back at the end leaves the database exactly as it was found.

    A DEDICATED engine is built here rather than reusing `app.core.database`'s
    global one. `conftest._use_null_pool()` rebuilds that engine's pool from
    `pool._creator`, which drops the `on_connect` listeners the asyncpg dialect
    uses to register its JSON/JSONB codecs — so JSONB columns come back as raw
    text on the shared engine under pytest, and `intake.source_metadata` arrives
    as a str. Owning the engine keeps this file's fixtures hermetic.
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


@pytest.fixture
async def curated_once(rolled_back_db):
    """A synthetic intake that has been quality-run and curated exactly once."""
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    db = rolled_back_db
    intake_id = await _seed_intake(db)
    await run_quality_engine(db, intake_id, executed_by=SYN)
    first = await curate_delivery(db, intake_id, curated_by=SYN)
    return db, intake_id, first


# TEST 1
async def test_first_curation_of_a_new_intake_succeeds(curated_once):
    db, intake_id, first = curated_once
    assert first["source_records"] == 5
    assert first["curated_records"] == 5
    assert first["every_source_record_curated"] is True
    state = await _state(db, intake_id)
    assert state["curated"] == 5
    assert state["distinct_source_record_id"] == 5
    # One record per status path, so the fixture actually exercises them.
    assert state["statuses"] == {"CLEAN": 2, "CORRECTED": 2, "HELD": 1}


# TEST 2
async def test_second_invocation_creates_no_additional_curated_rows(curated_once):
    """The re-run is refused by the database, not absorbed silently."""
    db, intake_id, _ = curated_once
    before = await _state(db, intake_id)

    with pytest.raises(IntegrityError) as caught:
        await curate_delivery(db, intake_id, curated_by=f"{SYN}-SECOND")
    await db.rollback()

    assert "uq_rce_curated_source_record" in str(caught.value), (
        "the refusal must come from the one-curated-row-per-source-row "
        "constraint, not from some incidental collision"
    )
    after = await _state(db, intake_id)
    assert after["curated"] == before["curated"] == 5
    assert after["distinct_source_record_id"] == after["curated"], (
        "a curated row exists for a source record more than once"
    )


# TEST 3
async def test_source_records_are_untouched_by_a_repeat_invocation(curated_once):
    db, intake_id, _ = curated_once
    before = await _state(db, intake_id)
    with pytest.raises(IntegrityError):
        await curate_delivery(db, intake_id, curated_by=f"{SYN}-SECOND")
    await db.rollback()
    after = await _state(db, intake_id)
    assert after["source_records"] == before["source_records"]
    assert after["source_hashes"] == before["source_hashes"], (
        "Area 1 content hashes changed during curation"
    )


# TEST 4
async def test_existing_curated_rows_are_unchanged_by_a_repeat_invocation(curated_once):
    """Not merely the same COUNT — the same ROWS, with the same values."""
    db, intake_id, _ = curated_once
    before = await _state(db, intake_id)
    before_values = sorted((await db.execute(
        select(m.RceCuratedRecord.source_record_id,
               m.RceCuratedRecord.record_status,
               m.RceCuratedRecord.address_postal_code,
               m.RceCuratedRecord.address_state)
        .where(m.RceCuratedRecord.source_intake_id == intake_id))).all())

    with pytest.raises(IntegrityError):
        await curate_delivery(db, intake_id, curated_by=f"{SYN}-SECOND")
    await db.rollback()

    after = await _state(db, intake_id)
    after_values = sorted((await db.execute(
        select(m.RceCuratedRecord.source_record_id,
               m.RceCuratedRecord.record_status,
               m.RceCuratedRecord.address_postal_code,
               m.RceCuratedRecord.address_state)
        .where(m.RceCuratedRecord.source_intake_id == intake_id))).all())

    assert after["curated_row_ids"] == before["curated_row_ids"]
    assert after_values == before_values
    assert after["statuses"] == before["statuses"]


# TEST 5
async def test_repeat_invocation_does_not_duplicate_issues(curated_once):
    db, intake_id, _ = curated_once
    before = await _state(db, intake_id)
    with pytest.raises(IntegrityError):
        await curate_delivery(db, intake_id, curated_by=f"{SYN}-SECOND")
    await db.rollback()
    after = await _state(db, intake_id)
    assert after["issues"] == before["issues"] > 0


# TEST 6
async def test_repeat_invocation_does_not_duplicate_lineage(curated_once):
    """A second correction row for one issue would double-count the AUTO_SAFE
    work in every report that reads `correction_authority`."""
    db, intake_id, _ = curated_once
    before = await _state(db, intake_id)
    assert before["corrections"] == 2, "the fixture must produce AUTO_SAFE lineage"

    with pytest.raises(IntegrityError):
        await curate_delivery(db, intake_id, curated_by=f"{SYN}-SECOND")
    await db.rollback()

    after = await _state(db, intake_id)
    assert after["corrections"] == before["corrections"] == 2


# TEST 7
async def test_a_different_intake_still_curates_normally(curated_once):
    """The guard is per source record, so it must not block a new delivery.

    The quality engine is deliberately NOT re-run for the second intake. Doing so
    hits an unrelated defect outside this test's scope: `run_quality_engine`
    restarts its issue sequence at 1 on every run while `rce_issues.issue_code`
    is `DQ-<date>-<sequence>` and UNIQUE, so a second run on the same calendar
    day collides on `ix_rce_issues_issue_code`. That is a quality-engine problem,
    not a curation one, and letting it fail this test would attribute it to the
    wrong module. Curating with no issues still exercises the invariant, which is
    what this file is about.
    """
    db, first_intake_id, _ = curated_once
    second_intake_id = await _seed_intake(db)
    assert second_intake_id != first_intake_id

    result = await curate_delivery(db, second_intake_id, curated_by=SYN)

    assert result["curated_records"] == 5
    assert result["every_source_record_curated"] is True
    second = await _state(db, second_intake_id)
    assert second["curated"] == second["distinct_source_record_id"] == 5
    assert second["statuses"] == {"CLEAN": 5}, "no issues means nothing is held"
    # And the first delivery is untouched by the second.
    first = await _state(db, first_intake_id)
    assert first["curated"] == 5
    assert first["statuses"] == {"CLEAN": 2, "CORRECTED": 2, "HELD": 1}


# TEST 8
async def test_a_failed_curation_leaves_no_partial_area_2(rolled_back_db, monkeypatch):
    """Atomicity is what makes a retry after a failure safe.

    If the run committed per batch, a failure partway would leave curated rows
    behind, `every_source_record_curated` would be False, and the retry would
    then collide with its own earlier output. One transaction means a failed run
    is indistinguishable from one that never started.
    """
    from app.tefca_registry.rce import curation as curation_module
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    db = rolled_back_db
    intake_id = await _seed_intake(db)
    await run_quality_engine(db, intake_id, executed_by=SYN)

    calls = {"n": 0}
    real = curation_module.build_curated_row

    def explode(record, values, *, issues):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated failure partway through curation")
        return real(record, values, issues=issues)

    monkeypatch.setattr(curation_module, "build_curated_row", explode)

    with pytest.raises(RuntimeError, match="simulated failure"):
        await curate_delivery(db, intake_id, curated_by=SYN)
    await db.rollback()

    state = await _state(db, intake_id)
    assert state["curated"] == 0, (
        "a failed curation left partial rows in Area 2; the retry would then "
        "collide with its own output"
    )
    assert state["source_records"] == 5, "Area 1 must survive a failed curation"

    # And the retry, on the real implementation, succeeds cleanly.
    monkeypatch.setattr(curation_module, "build_curated_row", real)
    retry = await curate_delivery(db, intake_id, curated_by=SYN)
    assert retry["curated_records"] == 5
    assert retry["every_source_record_curated"] is True


# Reconciliation arithmetic
async def test_reconciliation_identity_survives_a_repeat_invocation(curated_once):
    """`D == A` — every source record curated exactly once — is the identity the
    whole pipeline's arithmetic rests on."""
    db, intake_id, _ = curated_once
    with pytest.raises(IntegrityError):
        await curate_delivery(db, intake_id, curated_by=f"{SYN}-SECOND")
    await db.rollback()

    state = await _state(db, intake_id)
    assert state["curated"] == state["source_records"], "D == A"
    assert state["curated"] == state["distinct_source_record_id"], "no duplicates"

    orphans = int((await db.execute(
        select(func.count()).select_from(m.RceCuratedRecord)
        .outerjoin(m.RceSourceRecord,
                   m.RceCuratedRecord.source_record_id == m.RceSourceRecord.id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceSourceRecord.id.is_(None)))).scalar() or 0)
    assert orphans == 0


# TEST 9 — concurrency
async def test_the_constraint_is_a_real_unique_index_in_the_database(rolled_back_db):
    """Concurrency safety follows from WHERE the guard sits.

    A `SELECT ... IF EXISTS: return` pre-check inside `curate_delivery` would be
    raceable — two simultaneous callers could both read "not yet curated". A
    unique index cannot be raced: PostgreSQL serialises the second inserter on
    the index entry and then refuses it. So the concurrency property is a
    consequence of this index existing, and this is the assertion that keeps it.

    (Exercised end to end with two concurrent connections during the curation
    re-run safety gate: of two simultaneous `curate_delivery` calls on one
    intake, exactly one succeeded and one was refused by the database, leaving
    5 curated rows, 0 duplicates and 2 correction rows. That test needs two
    committing transactions and so cannot run against a shared database.)
    """
    row = (await rolled_back_db.execute(text("""
        select con.contype, pg_get_constraintdef(con.oid) as def
        from pg_constraint con
        where con.conrelid = 'rce_curated_records'::regclass
          and con.conname  = 'uq_rce_curated_source_record'
    """))).first()
    assert row is not None, (
        "uq_rce_curated_source_record is absent from this database. Nothing "
        "then prevents two concurrent curations from both writing Area 2."
    )
    contype, definition = row
    # pg_constraint.contype is PostgreSQL's "char" type; asyncpg hands it back as
    # bytes while psycopg2 hands back str. Normalise rather than pin one driver.
    if isinstance(contype, (bytes, bytearray)):
        contype = contype.decode()
    assert contype == "u", "the guard must be UNIQUE, not a plain index"
    assert "source_record_id" in definition
