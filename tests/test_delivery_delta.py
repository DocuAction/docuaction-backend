"""Month-over-month delivery comparison, certified on synthetic deliveries.

WHAT IS PROVEN
──────────────
    Delivery N (immutable)  +  Delivery N+1 (immutable)
        -> NEW / CHANGED / UNCHANGED / NOT_PRESENT_IN_CURRENT_DELIVERY
        with HELD reported alongside, never instead of, a classification.

    Delivery N is never overwritten. The delta is DERIVED from two append-only
    Area 1 tables, so the same pair always reconstructs the same answer and
    there is no stored copy to go stale.

WHAT ABSENCE MEANS
──────────────────
    `NOT_PRESENT_IN_CURRENT_DELIVERY` is an observation about a FILE. It is not
    deletion, termination, deactivation or an adverse finding, and these tests
    assert that nothing downstream is touched when a record stops appearing.

GOVERNMENT DATA
    Every test runs inside an OUTER transaction that is rolled back. Fixtures
    are synthetic: OIDs under an unassigned `9.99.444` arc, prefixed names, no
    real NPI. The delivered Government population is never read for comparison
    and never re-processed.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.tefca_registry.rce import delivery_delta as dd
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint

SYN = "SYNTHETIC-DELTA"
BASE_DAY = datetime(2026, 10, 1, 9, 0)


# ── synthetic delivery construction ──────────────────────────────────────────

def _record(tag, **overrides):
    """One synthetic delivered row, all 41 fields present."""
    row = {f: "" for f in RCE_FIELDS}
    row.update({
        "id": f"9.99.444.{tag}", "domains": "RCE",
        "orgManagingOrg": "9.99.444.QHIN", "purposesofuse": "T-TRTMNT",
        "active": "1", "sequoiaorgtype": "Participant",
        "TEFCAID": f"{SYN}-{tag}", "HCID": f"urn:oid:9.99.444.{tag}",
        "name": f"{SYN} ORG {tag}", "address_line": "1 Synthetic Way",
        "address_city": "Testville", "address_state": "MA",
        "address_postalCode": "99999", "address_country": "USA",
        "partOf": "9.99.444.QHIN",
    })
    row.update(overrides)
    return row


async def _deliver(db, rows, *, received_at, label, fingerprint=None):
    """Ingest a synthetic delivery the way intake does: verbatim lines + hashes."""
    header = "|".join(RCE_FIELDS)
    lines = [header] + ["|".join(r[f] for f in RCE_FIELDS) for r in rows]
    blob = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    intake_id = uuid.uuid4()
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=label,
        original_filename=f"{label}.csv", storage_path="(synthetic)",
        sha256=hashlib.sha256(blob).hexdigest(), file_size_bytes=len(blob),
        delimiter="|", encoding="utf-8", line_terminator="CRLF",
        headers=list(RCE_FIELDS),
        schema_fingerprint=fingerprint or schema_fingerprint(list(RCE_FIELDS)),
        record_count=len(rows), received_at=received_at, received_by=SYN,
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


async def _curate_stub(db, intake_id, held_ids=()):
    """Minimal curated rows so HELD can be observed alongside the delta."""
    rows = (await db.execute(
        select(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    for row in rows:
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id,
            source_record_id=row.id,
            record_status="HELD" if row.source_rce_id in held_ids else "CLEAN",
            issue_count=1 if row.source_rce_id in held_ids else 0,
            correction_count=0, rce_org_oid=row.source_rce_id,
            name=(row.parsed or {}).get("name"),
            transformation_version="test-1.0.0"))
    await db.commit()


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


# ── the certification scenario: Month 1 -> Month 2 ───────────────────────────

MONTH1 = ["A", "B", "C", "D", "E", "F", "G"]


async def _month1(db):
    rows = [_record(t) for t in MONTH1]
    return await _deliver(db, rows, received_at=BASE_DAY, label=f"{SYN}-M1")


async def _month2(db):
    """A: unchanged · B: name · C: address · D: NPI · E: relationship
       F: absent · G: present but HELD · H: brand new."""
    rows = [
        _record("A"),
        _record("B", name=f"{SYN} ORG B RENAMED"),
        _record("C", address_line="2 Different Way", address_city="Otherville"),
        _record("D", NPI="1234567893"),
        _record("E", partOf="9.99.444.OTHERPARENT"),
        _record("G"),
        _record("H"),
    ]
    return await _deliver(db, rows, received_at=BASE_DAY + timedelta(days=30),
                          label=f"{SYN}-M2")


@pytest.fixture
async def two_months(rolled_back_db):
    db = rolled_back_db
    m1 = await _month1(db)
    m2 = await _month2(db)
    await _curate_stub(db, m1)
    await _curate_stub(db, m2, held_ids={"9.99.444.G"})
    return db, m1, m2


# ── STEP 8 — first delivery ──────────────────────────────────────────────────

async def test_the_first_delivery_is_a_baseline_not_a_pile_of_new_records(
        rolled_back_db):
    db = rolled_back_db
    # Every earlier delivery must be invisible for this to be the first, so the
    # assertion is made about a delivery received before any other on record.
    earliest = (await db.execute(
        select(func.min(m.RceSourceIntake.received_at)))).scalar()
    first = await _deliver(db, [_record("A")],
                           received_at=(earliest or BASE_DAY) - timedelta(days=365),
                           label=f"{SYN}-FIRST")
    result = await dd.compare_delivery(db, first)

    assert result["comparable"] is False
    assert result["state"] == dd.BASELINE_DELIVERY
    assert result["previous_intake_id"] is None
    assert result["counts"] == {}
    assert "not the same as every record being NEW" in result["reason"]


# ── STEPS 12-13 — exact classification ───────────────────────────────────────

async def test_month_two_classifies_every_record_exactly(two_months):
    db, m1, m2 = two_months
    result = await dd.compare_delivery(db, m2)

    assert result["comparable"] is True
    assert result["previous_intake_id"] == str(m1)
    assert result["counts"] == {
        dd.NEW: 1,                 # H
        dd.CHANGED: 4,             # B, C, D, E
        dd.UNCHANGED: 2,           # A, G
        dd.NOT_PRESENT: 1,         # F
        "HELD_IN_CURRENT_DELIVERY": 1,   # G — orthogonal, not a delta class
    }

    by_id = {r["source_rce_id"]: r for r in result["records"]}
    assert by_id["9.99.444.H"]["classification"] == dd.NEW
    assert by_id["9.99.444.F"]["classification"] == dd.NOT_PRESENT
    assert by_id["9.99.444.A"]["classification"] == dd.UNCHANGED
    for tag in ("B", "C", "D", "E"):
        assert by_id[f"9.99.444.{tag}"]["classification"] == dd.CHANGED


async def test_held_is_orthogonal_to_the_delta_classification(two_months):
    """G is UNCHANGED *and* HELD. HELD is a processing state, not a fifth class."""
    db, m1, m2 = two_months
    result = await dd.compare_delivery(db, m2)
    by_id = {r["source_rce_id"]: r for r in result["records"]}

    g = by_id["9.99.444.G"]
    assert g["classification"] == dd.UNCHANGED
    assert g["held"] is True
    assert by_id["9.99.444.A"]["held"] is False
    assert dd.NEW in result["counts"] and "HELD" not in result["counts"]


# ── STEP 13 / 21 — exact changed fields ──────────────────────────────────────

@pytest.mark.parametrize("tag,expected", [
    ("B", ["name"]),
    ("C", ["address_line", "address_city"]),
    ("D", ["NPI"]),
    ("E", ["partOf"]),
])
async def test_changed_fields_are_exact(two_months, tag, expected):
    """No missing field, and no field reported that did not change."""
    db, m1, m2 = two_months
    result = await dd.compare_delivery(db, m2)
    record = next(r for r in result["records"]
                  if r["source_rce_id"] == f"9.99.444.{tag}")
    assert sorted(record["changed_fields"]) == sorted(expected)
    for change in record["field_changes"]:
        assert change["previous"] != change["current"]


async def test_unchanged_records_report_no_changed_fields(two_months):
    db, m1, m2 = two_months
    result = await dd.compare_delivery(db, m2)
    for record in result["records"]:
        if record["classification"] == dd.UNCHANGED:
            assert record["changed_fields"] == []
            assert record["previous_sha256"] == record["current_sha256"]


async def test_one_record_changing_four_fields_is_one_changed_record(
        rolled_back_db):
    """STEP 21: multiple field changes are one delta record, not four."""
    db = rolled_back_db
    m1 = await _deliver(db, [_record("M")], received_at=BASE_DAY,
                        label=f"{SYN}-M1x")
    m2 = await _deliver(db, [_record(
        "M", name=f"{SYN} ORG M2", address_line="9 Changed Way",
        NPI="1234567893", partOf="9.99.444.NEWPARENT")],
        received_at=BASE_DAY + timedelta(days=30), label=f"{SYN}-M2x")

    result = await dd.compare_delivery(db, m2, previous_intake_id=m1)
    assert result["counts"][dd.CHANGED] == 1
    changed = [r for r in result["records"] if r["classification"] == dd.CHANGED]
    assert len(changed) == 1
    assert sorted(changed[0]["changed_fields"]) == sorted(
        ["name", "address_line", "NPI", "partOf"])


# ── STEP 18 — absence is not removal ─────────────────────────────────────────

async def test_absence_preserves_everything_and_asserts_nothing(two_months):
    db, m1, m2 = two_months
    result = await dd.compare_delivery(db, m2)
    absent = next(r for r in result["records"]
                  if r["classification"] == dd.NOT_PRESENT)
    assert absent["source_rce_id"] == "9.99.444.F"

    # The vocabulary itself refuses to imply anything adverse.
    for forbidden in ("REMOVED", "DELETED", "TERMINATED", "INACTIVE",
                      "REVOKED", "ADVERSE", "NONCOMPLIANT"):
        assert forbidden not in dd.NOT_PRESENT

    # Month 1 still holds the record, its curated row and its hash.
    source = (await db.execute(
        select(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == m1,
               m.RceSourceRecord.source_rce_id == "9.99.444.F"))).scalars().first()
    assert source is not None and source.raw_line
    curated = (await db.execute(
        select(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == m1,
               m.RceCuratedRecord.rce_org_oid == "9.99.444.F"))).scalars().first()
    assert curated is not None
    assert curated.record_status == "CLEAN", "absence must not change prior state"


# ── STEP 19 — HELD across deliveries ─────────────────────────────────────────

async def test_held_then_processable_and_processable_then_held(rolled_back_db):
    db = rolled_back_db
    m1 = await _deliver(db, [_record("P"), _record("Q")],
                        received_at=BASE_DAY, label=f"{SYN}-H1")
    await _curate_stub(db, m1, held_ids={"9.99.444.P"})     # P held in M1
    m2 = await _deliver(db, [_record("P"), _record("Q")],
                        received_at=BASE_DAY + timedelta(days=30),
                        label=f"{SYN}-H2")
    await _curate_stub(db, m2, held_ids={"9.99.444.Q"})     # Q held in M2

    result = await dd.compare_delivery(db, m2, previous_intake_id=m1)
    by_id = {r["source_rce_id"]: r for r in result["records"]}
    assert by_id["9.99.444.P"]["held"] is False, "P is processable in M2"
    assert by_id["9.99.444.Q"]["held"] is True, "Q is held in M2"
    # Both are UNCHANGED as SOURCE — the hold is a processing state, not a change.
    assert by_id["9.99.444.P"]["classification"] == dd.UNCHANGED
    assert by_id["9.99.444.Q"]["classification"] == dd.UNCHANGED

    # Month 1's hold decisions are untouched.
    m1_states = dict((await db.execute(
        select(m.RceCuratedRecord.rce_org_oid, m.RceCuratedRecord.record_status)
        .where(m.RceCuratedRecord.source_intake_id == m1))).all())
    assert m1_states["9.99.444.P"] == "HELD"
    assert m1_states["9.99.444.Q"] == "CLEAN"


# ── STEP 20 — reappearance ───────────────────────────────────────────────────

async def test_a_returning_record_is_new_relative_to_the_previous_delivery(
        rolled_back_db):
    """STEP 20: NEW against M2, with history saying it is not first-ever seen."""
    db = rolled_back_db
    m1 = await _deliver(db, [_record("R"), _record("S")],
                        received_at=BASE_DAY, label=f"{SYN}-R1")
    m2 = await _deliver(db, [_record("S")],
                        received_at=BASE_DAY + timedelta(days=30),
                        label=f"{SYN}-R2")
    m3 = await _deliver(db, [_record("R"), _record("S")],
                        received_at=BASE_DAY + timedelta(days=60),
                        label=f"{SYN}-R3")

    m2_result = await dd.compare_delivery(db, m2, previous_intake_id=m1)
    assert [r["classification"] for r in m2_result["records"]
            if r["source_rce_id"] == "9.99.444.R"] == [dd.NOT_PRESENT]

    m3_result = await dd.compare_delivery(db, m3, previous_intake_id=m2)
    by_id = {r["source_rce_id"]: r for r in m3_result["records"]}
    assert by_id["9.99.444.R"]["classification"] == dd.NEW

    # Explanatory metadata: NEW here, but not first-ever seen.
    context = await dd.reappearance_context(db, m3, ["9.99.444.R"])
    assert context["reappeared"] == ["9.99.444.R"]
    assert context["first_seen"] == []
    # And it is metadata, not a fifth classification.
    assert dd.REAPPEARED not in {r["classification"] for r in m3_result["records"]}


# ── STEP 10 / 23 — idempotency and concurrency ───────────────────────────────

async def test_the_same_pair_reruns_identically(two_months):
    db, m1, m2 = two_months
    first = await dd.compare_delivery(db, m2)
    second = await dd.compare_delivery(db, m2)
    assert first["counts"] == second["counts"]
    assert first["records"] == second["records"]

    # Nothing was written: the delta is derived, so there is nothing to duplicate.
    intakes = int((await db.execute(
        select(func.count()).select_from(m.RceSourceIntake))).scalar() or 0)
    third = await dd.compare_delivery(db, m2)
    assert third["counts"] == first["counts"]
    assert int((await db.execute(
        select(func.count()).select_from(m.RceSourceIntake))).scalar() or 0) == intakes


SCHEMA = "delta_gate_tmp"


@pytest.fixture
async def sandbox_engine(db_required):
    """A throwaway schema in the SEPARATE `docuaction` database.

    Genuine concurrency needs independent sessions on independent connections,
    which the rolled-back session cannot provide — one AsyncSession is not
    usable from two tasks at once. `docuaction-db`, which holds the Government
    delivery, is never opened here.
    """
    import urllib.parse as up

    from sqlalchemy import text

    parsed = up.urlparse(_normalize_url(os.environ["DATABASE_URL"]))
    url = up.urlunparse(parsed._replace(path="/docuaction"))
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:                                  # noqa: BLE001
        await admin.dispose()
        pytest.skip(f"no sandbox database for a concurrency test: {exc}")

    from app.core.database import Base

    engine = create_async_engine(
        url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": SCHEMA}})
    tables = [Base.metadata.tables[t] for t in
              ("rce_source_intakes", "rce_source_records", "rce_curated_records")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda s: Base.metadata.create_all(
            s, tables=tables, checkfirst=True))
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await admin.dispose()


async def test_concurrent_comparisons_agree(sandbox_engine):
    """STEP 23: derived state cannot race.

    Three simultaneous comparisons on independent connections must agree
    exactly, and nothing may be written — there is no persisted delta to
    duplicate, which is the point of deriving it.
    """
    async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
        m1 = await _deliver(db, [_record(t) for t in ("A", "B", "C")],
                            received_at=BASE_DAY, label=f"{SYN}-C1")
        m2 = await _deliver(db, [_record("A"),
                                 _record("B", name=f"{SYN} ORG B2"),
                                 _record("D")],
                            received_at=BASE_DAY + timedelta(days=30),
                            label=f"{SYN}-C2")

    async def compare():
        async with AsyncSession(sandbox_engine, expire_on_commit=False) as db:
            return await dd.compare_delivery(db, m2, previous_intake_id=m1)

    a, b, c = await asyncio.gather(compare(), compare(), compare())
    assert a["counts"] == b["counts"] == c["counts"]
    assert a["records"] == b["records"] == c["records"]
    assert a["counts"] == {dd.NEW: 1, dd.CHANGED: 1, dd.UNCHANGED: 1,
                           dd.NOT_PRESENT: 1, "HELD_IN_CURRENT_DELIVERY": 0}

    async with AsyncSession(sandbox_engine) as db:
        intakes = int((await db.execute(
            select(func.count()).select_from(m.RceSourceIntake))).scalar() or 0)
        records = int((await db.execute(
            select(func.count()).select_from(m.RceSourceRecord))).scalar() or 0)
    assert intakes == 2 and records == 6, "a comparison wrote something"


# ── STEP 22 / 25 / 26 — refusals ─────────────────────────────────────────────

async def test_a_duplicate_stable_identity_makes_the_delivery_non_comparable(
        rolled_back_db):
    """STEP 22: no row is chosen arbitrarily."""
    db = rolled_back_db
    m1 = await _deliver(db, [_record("T")], received_at=BASE_DAY,
                        label=f"{SYN}-D1")
    m2 = await _deliver(db, [_record("T"), _record("T", name=f"{SYN} DUP")],
                        received_at=BASE_DAY + timedelta(days=30),
                        label=f"{SYN}-D2")

    result = await dd.compare_delivery(db, m2, previous_intake_id=m1)
    assert result["comparable"] is False
    assert result["state"] == dd.NON_COMPARABLE_DUPLICATE
    assert result["duplicate_identities"]["current"] == 1
    assert result["counts"] == {}
    assert "no row was chosen arbitrarily" in result["reason"]


async def test_a_schema_change_makes_the_deliveries_non_comparable(rolled_back_db):
    """STEP 26: comparing different schemas would diff one column against another."""
    db = rolled_back_db
    m1 = await _deliver(db, [_record("U")], received_at=BASE_DAY,
                        label=f"{SYN}-S1")
    m2 = await _deliver(db, [_record("U")],
                        received_at=BASE_DAY + timedelta(days=30),
                        label=f"{SYN}-S2", fingerprint="a" * 64)

    result = await dd.compare_delivery(db, m2, previous_intake_id=m1)
    assert result["comparable"] is False
    assert result["state"] == dd.NON_COMPARABLE_SCHEMA
    assert result["counts"] == {}


async def test_an_out_of_order_pair_is_refused_rather_than_guessed(rolled_back_db):
    """STEP 25: naming the pair backwards would report change as its undoing."""
    db = rolled_back_db
    m1 = await _deliver(db, [_record("V")], received_at=BASE_DAY,
                        label=f"{SYN}-O1")
    m3 = await _deliver(db, [_record("V", name=f"{SYN} LATER")],
                        received_at=BASE_DAY + timedelta(days=60),
                        label=f"{SYN}-O3")

    with pytest.raises(dd.DeltaRefused, match="received AFTER"):
        await dd.compare_delivery(db, m1, previous_intake_id=m3)

    # Naming the pair explicitly is how a backfilled delivery is compared.
    forward = await dd.compare_delivery(db, m3, previous_intake_id=m1)
    assert forward["comparable"] is True
    assert forward["explicit_pair"] is True


async def test_a_delivery_cannot_be_compared_with_itself(rolled_back_db):
    db = rolled_back_db
    m1 = await _deliver(db, [_record("W")], received_at=BASE_DAY,
                        label=f"{SYN}-W1")
    with pytest.raises(dd.DeltaRefused, match="compared with itself"):
        await dd.compare_delivery(db, m1, previous_intake_id=m1)


# ── STEP 14 — source change vs curated equivalence ───────────────────────────

async def test_a_source_change_is_reported_even_when_curation_would_equalise_it(
        rolled_back_db):
    """STEP 14: the delta speaks about the GOVERNMENT SOURCE.

    Month 1 delivers a ZIP that FMT-001 would zero-pad; Month 2 delivers the
    padded form directly. The curated values would match — but ONC sent
    different bytes, and a delta that hid that would misreport the delivery.
    """
    db = rolled_back_db
    m1 = await _deliver(db, [_record("Z", address_postalCode="1234")],
                        received_at=BASE_DAY, label=f"{SYN}-N1")
    m2 = await _deliver(db, [_record("Z", address_postalCode="01234")],
                        received_at=BASE_DAY + timedelta(days=30),
                        label=f"{SYN}-N2")

    result = await dd.compare_delivery(db, m2, previous_intake_id=m1)
    assert result["counts"][dd.CHANGED] == 1
    record = result["records"][0]
    assert record["changed_fields"] == ["address_postalCode"]
    assert record["field_changes"][0]["previous"] == "1234"
    assert record["field_changes"][0]["current"] == "01234"


# ── STEP 6 — hash canonicalisation ───────────────────────────────────────────

async def test_row_order_does_not_create_false_changes(rolled_back_db):
    """The same rows in a different file order are UNCHANGED."""
    db = rolled_back_db
    rows = [_record("A"), _record("B"), _record("C")]
    m1 = await _deliver(db, rows, received_at=BASE_DAY, label=f"{SYN}-K1")
    m2 = await _deliver(db, list(reversed(rows)),
                        received_at=BASE_DAY + timedelta(days=30),
                        label=f"{SYN}-K2")

    result = await dd.compare_delivery(db, m2, previous_intake_id=m1)
    assert result["counts"][dd.UNCHANGED] == 3
    assert result["counts"][dd.CHANGED] == 0
    assert result["counts"][dd.NEW] == 0
    assert result["counts"][dd.NOT_PRESENT] == 0


# ── STEP 16 — advisory reprocessing scope ────────────────────────────────────

def test_reprocessing_scope_is_advisory_and_conservative():
    identity = dd.reprocessing_scope(["NPI"])
    assert identity["identity_verification"] is True
    assert identity["informational_only"] is False

    address = dd.reprocessing_scope(["address_line"])
    assert address["address_verification"] is True

    rel = dd.reprocessing_scope(["partOf"])
    assert rel["relationship_interpretation"] is True

    contact = dd.reprocessing_scope(["contact_phone"])
    assert contact["contact_only"] is True
    assert contact["identity_verification"] is False
    assert "not invented here" in contact["note"]


# ── STEP 33 — audit reconstruction ───────────────────────────────────────────

async def test_a_changed_classification_is_fully_reconstructable(two_months):
    db, m1, m2 = two_months
    result = await dd.compare_delivery(db, m2)
    record = next(r for r in result["records"]
                  if r["source_rce_id"] == "9.99.444.B")

    assert result["current_intake_id"] == str(m2)
    assert result["previous_intake_id"] == str(m1)
    assert result["delta_version"] == dd.DELTA_VERSION
    assert record["source_rce_id"]
    assert record["previous_sha256"] and record["current_sha256"]
    assert record["previous_sha256"] != record["current_sha256"]
    assert record["changed_fields"] == ["name"]
    assert record["source_record_id"]

    # The cited hashes are the ones Area 1 actually stores.
    stored = (await db.execute(
        select(m.RceSourceRecord.record_sha256)
        .where(m.RceSourceRecord.source_intake_id == m1,
               m.RceSourceRecord.source_rce_id == "9.99.444.B"))).scalar()
    assert stored == record["previous_sha256"]


# ── STEP 42 — the fixtures touch nothing else ────────────────────────────────

async def test_the_comparison_writes_nothing(two_months):
    db, m1, m2 = two_months
    before = {
        "intakes": int((await db.execute(select(func.count()).select_from(m.RceSourceIntake))).scalar() or 0),
        "records": int((await db.execute(select(func.count()).select_from(m.RceSourceRecord))).scalar() or 0),
        "curated": int((await db.execute(select(func.count()).select_from(m.RceCuratedRecord))).scalar() or 0),
        "issues": int((await db.execute(select(func.count()).select_from(m.RceIssue))).scalar() or 0),
    }
    await dd.compare_delivery(db, m2)
    after = {
        "intakes": int((await db.execute(select(func.count()).select_from(m.RceSourceIntake))).scalar() or 0),
        "records": int((await db.execute(select(func.count()).select_from(m.RceSourceRecord))).scalar() or 0),
        "curated": int((await db.execute(select(func.count()).select_from(m.RceCuratedRecord))).scalar() or 0),
        "issues": int((await db.execute(select(func.count()).select_from(m.RceIssue))).scalar() or 0),
    }
    assert before == after


def test_fixtures_are_synthetic_only():
    for tag in MONTH1:
        row = _record(tag)
        assert row["id"].startswith("9.99.444.")
        assert row["name"].startswith(SYN)
        assert row["TEFCAID"].startswith(SYN)
        assert row["NPI"] == ""
