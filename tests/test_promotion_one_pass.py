"""promote_delivery() must finish a multi-batch delivery in ONE invocation.

THE INVARIANTS
──────────────
 1. One eligible curated record creates at most one logical promoted entity.
 2. One logical identifier is never inserted twice against `idx_tefca_ident_unique`.
 3. A normal first pass never depends on a unique-constraint exception as control flow.
 4. A batch boundary never re-presents a row the run already processed.
 5. Autoflush never persists a duplicate pending identifier.
 6. Resume after a genuine interruption converges without duplicates.
 7. The Area 1 promotion marking reflects committed database state.

WHAT THESE TESTS EXIST TO CATCH
───────────────────────────────
The drain loop selects rows with `canonical_entity_id IS NULL`. A row that can
never be promoted — HELD, REJECTED, or missing its key — keeps that column NULL
forever, so it stays in the result set on every pass. `unpromotable` is what
removes it, and it is consulted in the query but has to be POPULATED at the two
`continue` sites for that to mean anything.

It was not. The final iteration — the one where only HELD rows remain — set
`progressed = False`, hit `continue`, and re-selected the identical rows for
ever. Promotion never returned, so the Area 1 marking loop and the whole of
pass 2 (relationships) were unreachable. Measured on synthetic data at
fdc99c7: a fresh 8-row / 3-batch population did not terminate in 25s, and neither
did a resume, while every row had in fact already been promoted correctly.

That is why `test_multi_batch_population_promotes_in_one_invocation` wraps the
call in `asyncio.wait_for`: a re-introduced spin must FAIL, not hang a suite.

HOW THESE TESTS AVOID TOUCHING GOVERNMENT DATA
──────────────────────────────────────────────
Every test runs inside an OUTER transaction that is rolled back, with the session
joined to it via `join_transaction_mode="create_savepoint"`, so the many internal
`db.commit()` calls in `promote_delivery` commit savepoints rather than reaching
disk. Fixtures are synthetic throughout: OIDs live under an unassigned `9.99.888`
arc, names are prefixed, and no NPI, TEFCAID, address or delivered row is real.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import promotion as promotion_module
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint
from app.tefca_registry.rce.promotion import promote_delivery

SYN = "SYNTHETIC-PROMO"
QHIN_OID = "9.99.888.0.1"
BATCH = 3          # 8 rows -> 3 batches
N_ROWS = 8

#: A spin must fail the test rather than hang the suite.
PROMOTE_TIMEOUT = 60


# ── the regression guard, no database needed ─────────────────────────────────

def test_unpromotable_rows_are_recorded_so_the_drain_terminates():
    """`unpromotable` is consulted by the query — it must also be populated.

    A source-level assertion because it is cheap, runs everywhere, and names the
    exact line whose absence made promotion unable to finish.
    """
    import inspect

    source = inspect.getsource(promote_delivery)
    assert source.count("unpromotable.add(") == 2, (
        "both non-promotable paths (status, and missing key) must record the "
        "row, or the drain loop re-selects it on every pass and never ends"
    )
    # Populated before the `continue`, not after it.
    for marker in ("skipped_status.get(row.record_status, 0) + 1",
                   'skipped_status.get("MISSING_KEY", 0) + 1'):
        tail = source.split(marker, 1)[1][:200]
        assert "unpromotable.add(" in tail.split("continue", 1)[0], (
            f"the row is not recorded before `continue` after {marker!r}"
        )


# ── fixtures ─────────────────────────────────────────────────────────────────

def _rows():
    """8 synthetic records: 6 Participants, 1 Subparticipant, 1 HELD."""
    base = {f: "" for f in RCE_FIELDS}
    base.update({
        "domains": "RCE", "orgManagingOrg": QHIN_OID, "purposesofuse": "T-TRTMNT",
        "active": "1", "sequoiaorgtype": "Participant",
        "address_line": "1 Synthetic Way", "address_city": "Testville",
        "address_state": "MA", "address_postalCode": "99999",
        "address_country": "USA", "partOf": QHIN_OID,
    })
    out = []
    for i in range(1, N_ROWS + 1):
        r = dict(base)
        r["id"] = f"9.99.888.1.{i}"
        r["TEFCAID"] = f"{SYN}-TEFCAID-{i:04d}"
        r["HCID"] = f"urn:oid:9.99.888.1.{i}"
        r["name"] = f"{SYN} ORG {i}"
        out.append(r)
    # Row 7 is a Subparticipant of row 1, so pass 2 has a real parent edge to build.
    out[6]["sequoiaorgtype"] = "Subparticipant"
    out[6]["partOf"] = "9.99.888.1.1"
    return out


async def _seed(db):
    """One synthetic intake with 7 promotable curated rows and 1 HELD."""
    rows = _rows()
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
        record_count=len(rows), received_at=datetime.utcnow(), received_by=SYN,
        status="PARSED", source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()

    for line_number, r in enumerate(rows, start=2):
        raw = "|".join(r[f] for f in RCE_FIELDS)
        source_id = uuid.uuid4()
        db.add(m.RceSourceRecord(
            id=source_id, source_intake_id=intake_id, line_number=line_number,
            raw_line=raw, parsed=r,
            record_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            source_rce_id=r["id"], tefcaid=r["TEFCAID"], hcid=r["HCID"],
            field_count=len(RCE_FIELDS), parse_status="ok",
            promotion_status="pending"))
        await db.flush()
        held = line_number - 1 == N_ROWS          # the last row is HELD
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id,
            source_record_id=source_id,
            record_status="HELD" if held else "CLEAN",
            issue_count=1 if held else 0, correction_count=0,
            status_reason="synthetic hold" if held else None,
            rce_org_oid=r["id"], tefcaid=r["TEFCAID"], hcid=r["HCID"],
            name=r["name"], entity_level="participant",
            sequoia_org_type=r["sequoiaorgtype"], operational_status="active",
            is_active=True, address_line=r["address_line"],
            address_city=r["address_city"], address_state=r["address_state"],
            address_postal_code=r["address_postalCode"],
            address_country=r["address_country"],
            exchange_purposes=["T-TRTMNT"], part_of=r["partOf"],
            org_managing_org=QHIN_OID, contact={}, rce_attributes={},
            is_test_record=False, transformation_version="test-1.0.0"))
    await db.commit()
    return intake_id


@pytest.fixture
async def rolled_back_db(db_required, monkeypatch):
    """Session on an outer transaction that is rolled back; BATCH_SIZE reduced.

    A dedicated engine, not the app's global one: `conftest._use_null_pool()`
    rebuilds that engine's pool and loses the `on_connect` listeners registering
    asyncpg's JSON codecs, so JSONB reads back as raw text under pytest.

    BATCH_SIZE is monkeypatched rather than changed in the module, so the batch
    boundary is crossed by 8 rows instead of 1,001.
    """
    import os

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url

    monkeypatch.setattr(promotion_module, "BATCH_SIZE", BATCH)

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
def entity_counter():
    """Counts TefcaRegEntity / TefcaEntityIdentifier constructions.

    Construction count, not row count: it catches a rival entity that was built
    and then rolled back, which a row count cannot see.
    """
    counts = {"entities": 0, "identifiers": 0, "identifier_keys": []}

    @event.listens_for(reg.TefcaRegEntity, "init")
    def _entity(target, args, kw):
        counts["entities"] += 1

    @event.listens_for(reg.TefcaEntityIdentifier, "init")
    def _identifier(target, args, kw):
        counts["identifiers"] += 1
        counts["identifier_keys"].append(
            (kw.get("identifier_type"), kw.get("identifier_value"),
             kw.get("system_uri")))

    yield counts
    event.remove(reg.TefcaRegEntity, "init", _entity)
    event.remove(reg.TefcaEntityIdentifier, "init", _identifier)


async def _state(db, intake_id):
    async def count(stmt):
        return int((await db.execute(stmt)).scalar() or 0)

    return {
        "promoted": await count(
            select(func.count()).select_from(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None))),
        "unpromoted": await count(
            select(func.count()).select_from(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.is_(None))),
        "distinct_entities": await count(
            select(func.count(func.distinct(m.RceCuratedRecord.canonical_entity_id)))
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None))),
        "area1_marked": await count(
            select(func.count()).select_from(m.RceSourceRecord)
            .where(m.RceSourceRecord.source_intake_id == intake_id,
                   m.RceSourceRecord.promotion_status == "promoted")),
        "held_promoted": await count(
            select(func.count()).select_from(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.record_status == "HELD",
                   m.RceCuratedRecord.canonical_entity_id.isnot(None))),
        "duplicate_identifier_keys": await _duplicate_identifier_keys(db, intake_id),
    }


async def _duplicate_identifier_keys(db, intake_id) -> int:
    """Committed identifier rows for this intake sharing one unique key.

    Scoped to this delivery's entities, because the surrounding database holds
    unrelated rows — including some with a NULL `system_uri`, which PostgreSQL
    treats as distinct and the index therefore does not constrain.
    """
    entity_ids = select(m.RceCuratedRecord.canonical_entity_id).where(
        m.RceCuratedRecord.source_intake_id == intake_id,
        m.RceCuratedRecord.canonical_entity_id.isnot(None))
    dupes = (await db.execute(
        select(func.count()).select_from(
            select(reg.TefcaEntityIdentifier.identifier_type,
                   reg.TefcaEntityIdentifier.identifier_value,
                   reg.TefcaEntityIdentifier.system_uri)
            .where(reg.TefcaEntityIdentifier.entity_id.in_(entity_ids))
            .group_by(reg.TefcaEntityIdentifier.identifier_type,
                      reg.TefcaEntityIdentifier.identifier_value,
                      reg.TefcaEntityIdentifier.system_uri)
            .having(func.count() > 1).subquery()))).scalar()
    return int(dupes or 0)


async def _promote(db, intake_id):
    """Always time-boxed: a re-introduced drain spin must fail, not hang."""
    try:
        return await asyncio.wait_for(
            promote_delivery(db, intake_id, actor=SYN), timeout=PROMOTE_TIMEOUT)
    except asyncio.TimeoutError:
        pytest.fail(
            f"promote_delivery did not terminate within {PROMOTE_TIMEOUT}s. The "
            f"drain loop is re-selecting rows it can never promote — check that "
            f"`unpromotable` is populated at both `continue` sites.")


# ── TEST 1 / 2 — single batch ────────────────────────────────────────────────

async def test_a_single_batch_promotes_each_record_exactly_once(
        rolled_back_db, entity_counter, monkeypatch):
    """TEST 1 + TEST 2: whole population inside one batch."""
    monkeypatch.setattr(promotion_module, "BATCH_SIZE", 1000)
    db = rolled_back_db
    intake_id = await _seed(db)

    result = await _promote(db, intake_id)

    assert result["entities_created"] == 7
    assert result["not_promoted_by_status"] == {"HELD": 1}
    state = await _state(db, intake_id)
    assert state["promoted"] == 7
    assert state["distinct_entities"] == 7, "one entity per promoted record"
    # 7 delivered entities + 1 synthesised QHIN.
    assert entity_counter["entities"] == 8


# ── TEST 3 — the gate ────────────────────────────────────────────────────────

async def test_multi_batch_population_promotes_in_one_invocation(
        rolled_back_db, entity_counter):
    """TEST 3 + 4 + 5 + 6 + 7: one uninterrupted call across three batches."""
    db = rolled_back_db
    intake_id = await _seed(db)

    result = await _promote(db, intake_id)

    assert result["entities_created"] == 7
    assert result["entities_updated"] == 0, (
        "an update on a first pass means a record was visited twice"
    )
    assert result["not_promoted_by_status"] == {"HELD": 1}, (
        "a status counted more than once means the batch was re-selected"
    )

    state = await _state(db, intake_id)
    assert state["promoted"] == 7
    assert state["unpromoted"] == 1
    assert state["distinct_entities"] == 7

    # TEST 5 — no rival entity was even constructed (7 delivered + 1 QHIN).
    assert entity_counter["entities"] == 8

    # TEST 6 + 7 — every identifier key built during the run is distinct, so no
    # insert could have collided with idx_tefca_ident_unique.
    keys = entity_counter["identifier_keys"]
    assert len(keys) == len(set(keys)), (
        f"a duplicate identifier key was constructed: "
        f"{len(keys) - len(set(keys))} repeat(s)"
    )


# ── TEST 11 / 12 / 13 ────────────────────────────────────────────────────────

async def test_area1_marking_matches_committed_promoted_state(rolled_back_db):
    """TEST 11: the marking loop is only reachable once the drain terminates."""
    db = rolled_back_db
    intake_id = await _seed(db)
    await _promote(db, intake_id)

    state = await _state(db, intake_id)
    assert state["area1_marked"] == state["promoted"] == 7, (
        "Area 1 markers must equal the curated rows that actually carry an "
        "entity — this is reconciliation check E"
    )


async def test_held_records_are_not_promoted_and_stay_held(rolled_back_db):
    """TEST 12."""
    db = rolled_back_db
    intake_id = await _seed(db)
    await _promote(db, intake_id)

    state = await _state(db, intake_id)
    assert state["held_promoted"] == 0
    held = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.record_status == "HELD"))).scalars().all()
    assert len(held) == 1
    assert held[0].canonical_entity_id is None
    assert held[0].promoted_at is None


async def test_relationship_behaviour_is_preserved(rolled_back_db):
    """TEST 13: pass 2 runs, and a Participant gets no second parent edge."""
    db = rolled_back_db
    intake_id = await _seed(db)
    result = await _promote(db, intake_id)

    # Every promoted entity gets its QHIN edge...
    assert result["relationships_managed_by_qhin"] == 7
    # ...and only the one Subparticipant gets a partOf edge. A Participant's
    # partOf repeats its QHIN, so emitting it again would assert the same fact.
    assert result["relationships_sub_participant_of"] == 1
    assert result["unresolved_parents"] == 0


# ── TEST 8 / 9 / 10 — interruption, resume, re-run ───────────────────────────

async def test_resume_after_interruption_converges_without_duplicates(
        rolled_back_db, entity_counter):
    """TEST 8 + 9 + 10.

    The interruption is deterministic and fires during batch 2, after batch 1 has
    already committed: entity construction 1 is the synthesised QHIN, 2-4 are
    batch 1, and 5 is the first row of batch 2. Raising there leaves exactly the
    partial state a killed run leaves behind, and — unlike raising from an
    `after_commit` handler — leaves the session recoverable by a rollback.
    """
    db = rolled_back_db
    intake_id = await _seed(db)

    class Interrupt(RuntimeError):
        pass

    seen = {"entities": 0}

    @event.listens_for(reg.TefcaRegEntity, "init")
    def _stop(target, args, kw):
        seen["entities"] += 1
        if seen["entities"] == 5:
            raise Interrupt("induced interruption during the second batch")

    try:
        with pytest.raises(Interrupt):
            await promote_delivery(db, intake_id, actor=SYN)
    finally:
        event.remove(reg.TefcaRegEntity, "init", _stop)
    # Discard the half-built second batch; batch 1 stays committed.
    await db.rollback()

    partial = await _state(db, intake_id)
    assert 0 < partial["promoted"] < 7, (
        f"the interruption must leave a genuinely PARTIAL promotion, "
        f"got {partial['promoted']}"
    )

    # TEST 8 + 9 — resume completes and creates no duplicates.
    result = await _promote(db, intake_id)
    resumed = await _state(db, intake_id)
    assert resumed["promoted"] == 7
    assert resumed["distinct_entities"] == 7, "one entity per curated record"
    assert resumed["area1_marked"] == 7
    # COMMITTED state is what matters here, not construction counts: the
    # interrupted run built one row's identifiers and rolled them back, so the
    # resume legitimately builds them again.
    assert resumed["duplicate_identifier_keys"] == 0

    # TEST 10 — a further run is a no-op under current drain semantics.
    before = entity_counter["entities"]
    again = await _promote(db, intake_id)
    assert again["entities_created"] == 0
    assert entity_counter["entities"] == before, (
        "a completed delivery must not construct another entity"
    )
    final = await _state(db, intake_id)
    assert final == resumed


# ── TEST 14 ──────────────────────────────────────────────────────────────────

def test_no_government_data_is_used_by_these_fixtures():
    """TEST 14: the fixtures must stay synthetic, permanently.

    `9.99.888` is not an assigned OID arc, and every name carries the prefix.
    """
    rows = _rows()
    assert len(rows) == N_ROWS
    for r in rows:
        assert r["id"].startswith("9.99.888."), "OIDs must stay in the unassigned arc"
        assert r["name"].startswith(SYN), "names must be marked synthetic"
        assert r["TEFCAID"].startswith(SYN)
        assert r["NPI"] == "", "no NPI, real or invented, belongs in a fixture"
    assert QHIN_OID.startswith("9.99.888.")
