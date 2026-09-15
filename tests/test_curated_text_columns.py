"""Source-derived name columns are TEXT — a delivered value is never too long.

WHY THIS FILE EXISTS
────────────────────
On 2026-09-15 a 184-record DEV delivery landed in Area 1 completely, passed
the quality run, and FAILED at CURATION:

    StringDataRightTruncationError: value too long for type character varying(500)
    INSERT INTO rce_curated_records (...)

The parameter was `name`: a delivered organisation name of 580 characters.
Area 1 holds that value unbounded and Area 2 is a 1:1 projection of it, so a
VARCHAR(500) on the curated column (and on the registry columns the same value
is promoted into) turned one long delivered value into the loss of the whole
curated stage. Migration 20260915_curated_text_columns lifts the cap to TEXT
on exactly the five columns that carry delivered free text 1:1.

WHAT IS PINNED
──────────────
  * the model declares TEXT (no length) on those five columns, and the
    migration that widens them exists and names each one;
  * nothing between the parser and the curated row shortens a value — no
    slice, no max_length — so a long value is preserved EXACTLY;
  * with a database: a synthetic 41-field record whose name is far longer
    than 500 characters ingests, curates CLEAN, promotes into the registry
    with the full value, and reconciles A == D with the normal records around
    it; the Area 1 row is byte-identical before and after.

HOW THE DATABASE TESTS AVOID TOUCHING GOVERNMENT DATA
─────────────────────────────────────────────────────
Same pattern as test_curation_rerun_invariant.py: every commit lands in a
savepoint inside an outer transaction that is rolled back. All fixture data
is synthetic — OIDs under an unassigned 9.99.999 arc, a placeholder city, no
real organisation, NPI, TEFCAID or address.
"""
from __future__ import annotations

import hashlib
import inspect
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.curation import build_curated_row, curate_delivery
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint

SYN = "SYNTHETIC-TEXTCOL-TEST"

#: (model column, migration table.column) — the five source-derived free-text
#: columns and nothing else.
WIDENED = (
    (m.RceCuratedRecord.__table__.c.name, "rce_curated_records", "name"),
    (reg.TefcaRegEntity.__table__.c.name, "tefca_reg_entities", "name"),
    (reg.TefcaRegEntity.__table__.c.display_name, "tefca_reg_entities", "display_name"),
    (m.TefcaEntityContact.__table__.c.company, "tefca_entity_contacts", "company"),
    (m.TefcaEntityContact.__table__.c.name, "tefca_entity_contacts", "name"),
)

MIGRATION = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
             / "20260915_curated_text_columns.py")


def _long_name(length: int = 640) -> str:
    """A synthetic organisation name longer than the old cap, with the kind of
    internal whitespace runs the DEV delivery carried. Contains no real name."""
    tokens = [f"{SYN}", "ORG", "LONG", " M.D.", " P.A."]
    body = (" " * 120).join(tokens)
    return (body + " " + SYN * 40)[:length]


# ── model and migration ──────────────────────────────────────────────────────

class TestTheModelDeclaresText:
    @pytest.mark.parametrize("column,table,name", WIDENED,
                             ids=[f"{t}.{c}" for _, t, c in WIDENED])
    def test_column_is_text_with_no_length(self, column, table, name):
        assert isinstance(column.type, Text), (
            f"{table}.{name} is {column.type!r}; a length-capped type here "
            f"fails the whole curation/promotion stage on one long delivered "
            f"value (DEV, 2026-09-15).")
        assert getattr(column.type, "length", None) is None

    def test_bounded_columns_keep_their_limits(self):
        """The fix is surgical: identifiers, codes and address components stay
        bounded. Widening everything would be the arbitrary change in the other
        direction."""
        c = m.RceCuratedRecord.__table__.c
        assert c.rce_org_oid.type.length == 200
        assert c.tefcaid.type.length == 100
        assert c.npi.type.length == 40
        assert c.record_status.type.length == 20
        assert c.address_state.type.length == 10
        assert reg.TefcaEntityIdentifier.__table__.c.identifier_value.type.length == 500

    def test_the_migration_exists_and_names_every_widened_column(self):
        body = MIGRATION.read_text(encoding="utf-8")
        assert 'down_revision = "20260903_delivery_grants"' in body
        for _, table, name in WIDENED:
            assert f'("{table}", "{name}"' in body, f"{table}.{name} not migrated"
        assert "sa.Text()" in body
        # The downgrade must refuse rather than truncate.
        assert "DowngradeWouldTruncateError" in body
        assert "length(" in body

    def test_the_migration_only_alters_column_types(self):
        """Non-destructive by construction: no drop, no delete, no update."""
        body = MIGRATION.read_text(encoding="utf-8").lower()
        for forbidden in ("drop_table", "drop_column", "delete from",
                          "update ", "truncate table", "substr(", "left("):
            assert forbidden not in body, forbidden


# ── nothing between parser and curated row shortens a value ─────────────────

class TestNothingTruncates:
    def test_build_curated_row_preserves_a_long_name_exactly(self):
        values = {f: "" for f in RCE_FIELDS}
        values["name"] = _long_name()
        values["sequoiaorgtype"] = "Participant"

        class Rec:
            id = uuid.uuid4()
            source_intake_id = uuid.uuid4()

        row = build_curated_row(Rec(), values, issues=[])
        assert row["name"] == values["name"].strip()
        assert len(row["name"]) > 500

    def test_no_slice_or_max_length_on_the_curation_path(self):
        from app.tefca_registry.rce import curation, promotion
        for module in (curation, promotion):
            source = inspect.getsource(module)
            assert "[:500]" not in source and "[:499]" not in source, module.__name__
            assert "max_length" not in source, module.__name__


# ── with a database ──────────────────────────────────────────────────────────

def _synthetic_rows():
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
        "contact_purpose": "ADMIN",
    })
    rows = []
    for n in range(1, 4):
        r = dict(base)
        r["id"] = f"9.99.999.2.{n}"
        r["TEFCAID"] = f"{SYN}-{n:04d}"
        r["HCID"] = f"urn:oid:9.99.999.2.{n}"
        r["name"] = f"{SYN} ORG {n}"
        rows.append(r)
    rows[1]["name"] = _long_name()                 # > 500: the DEV failure shape
    rows[1]["contact_name"] = _long_name(560)      # same class, contact column
    rows[1]["contact_company"] = _long_name(520)
    rows[2]["address_postalCode"] = "1234"         # FMT-001 AUTO_SAFE -> CORRECTED
    return rows


async def _seed_intake(db) -> uuid.UUID:
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
        source_metadata={"origin": "synthetic test fixture"}))
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


async def _area1_snapshot(db, intake_id):
    rows = (await db.execute(
        select(m.RceSourceRecord.line_number, m.RceSourceRecord.raw_line,
               m.RceSourceRecord.record_sha256)
        .where(m.RceSourceRecord.source_intake_id == intake_id)
        .order_by(m.RceSourceRecord.line_number))).all()
    return [tuple(r) for r in rows]


@pytest.fixture
async def rolled_back_db(db_required):
    """Every commit lands in a savepoint that is thrown away at the end."""
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


@pytest.mark.asyncio
async def test_a_long_delivered_name_ingests_curates_promotes_and_reconciles(rolled_back_db):
    from app.tefca_registry.rce.promotion import promote_delivery
    from app.tefca_registry.rce.quality_engine import run_quality_engine
    from app.tefca_registry.rce.reconciliation import reconcile_delivery

    db = rolled_back_db
    intake_id = await _seed_intake(db)
    before = await _area1_snapshot(db, intake_id)
    long_row = _synthetic_rows()[1]
    assert len(long_row["name"]) > 500

    # INGEST: Area 1 holds the value unbounded and exactly.
    stored = (await db.execute(
        select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id,
            m.RceSourceRecord.line_number == 3))).scalar_one()
    assert stored.parsed["name"] == long_row["name"]

    # QUALITY then CURATION: the stage that failed in DEV now completes.
    await run_quality_engine(db, intake_id, executed_by=SYN)
    result = await curate_delivery(db, intake_id, curated_by=SYN)
    assert result["every_source_record_curated"] is True
    assert result["curated_records"] == 3

    curated = (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_record_id == stored.id))).scalar_one()
    # SOURCE_VALUE_PRESERVED_EXACTLY / NO_TRUNCATION: the curated projection is
    # the delivered value with outer whitespace stripped, nothing shorter.
    assert curated.name == long_row["name"].strip()
    assert len(curated.name) > 500
    assert curated.record_status in ("CLEAN", "CORRECTED")
    # Contact values are carried raw (no strip) — exact bytes, both columns.
    assert curated.contact["contact_name"] == long_row["contact_name"]
    assert curated.contact["contact_company"] == long_row["contact_company"]

    # The normal records around it are unaffected.
    statuses = dict((await db.execute(
        select(m.RceCuratedRecord.record_status, func.count())
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .group_by(m.RceCuratedRecord.record_status))).all())
    assert sum(statuses.values()) == 3
    assert statuses.get("CORRECTED", 0) >= 1          # FMT-001 on row 3

    # RELATIONSHIP RESOLUTION / PROMOTION: the same value lands in the registry
    # and in the contact row, full length.
    promoted = await promote_delivery(db, intake_id, actor=SYN)
    assert promoted.get("curated_records") == 3
    entity = (await db.execute(
        select(reg.TefcaRegEntity).join(
            reg.TefcaEntityIdentifier,
            reg.TefcaEntityIdentifier.entity_id == reg.TefcaRegEntity.id)
        .where(reg.TefcaEntityIdentifier.identifier_type == "rce_org_oid",
               reg.TefcaEntityIdentifier.identifier_value == long_row["id"]))
        ).scalars().first()
    assert entity is not None
    assert entity.name == long_row["name"].strip()
    assert entity.display_name == long_row["name"].strip()
    contact = (await db.execute(
        select(m.TefcaEntityContact).where(
            m.TefcaEntityContact.entity_id == entity.id))).scalars().first()
    assert contact is not None
    assert contact.name == long_row["contact_name"]
    assert contact.company == long_row["contact_company"]
    assert len(contact.name) > 500 and len(contact.company) > 500

    # RECONCILIATION: A == D, every row accounted for.
    recon = await reconcile_delivery(db, intake_id)
    assert recon["passed"] is True, recon
    pops = recon["populations"]
    assert pops["A_source_records_received"] == 3
    assert pops["D_curated_records"] == 3

    # Area 1 is byte-identical before and after the run.
    assert await _area1_snapshot(db, intake_id) == before


@pytest.mark.asyncio
async def test_a_normal_short_record_still_curates_the_same_way(rolled_back_db):
    """No regression on the ordinary path: a short name is a short name."""
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    db = rolled_back_db
    intake_id = await _seed_intake(db)
    await run_quality_engine(db, intake_id, executed_by=SYN)
    await curate_delivery(db, intake_id, curated_by=SYN)
    short = (await db.execute(
        select(m.RceCuratedRecord).join(
            m.RceSourceRecord, m.RceSourceRecord.id == m.RceCuratedRecord.source_record_id)
        .where(m.RceSourceRecord.source_intake_id == intake_id,
               m.RceSourceRecord.line_number == 2))).scalar_one()
    assert short.name == f"{SYN} ORG 1"
    assert short.record_status == "CLEAN"
