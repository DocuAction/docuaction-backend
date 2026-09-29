"""AP-002 residual — invalid promoted NPIs are counted in SQL, oracle-equal to
`validate_npi`, and no longer pull every identifier row to Python.

`_invalid_identifiers_promoted_by_intake` used to SELECT every active NPI
identifier row of the page's promoted entities (24,589 at September scale) and
run the Python Luhn check per value. The CMS check-digit rule is pure digit
arithmetic, so well-formed values are judged in Postgres; only values that are
not exactly 10 ASCII digits after a space-trim are returned and validated with
the real `validate_npi` (Python's strip() also removes tabs and unicode
whitespace, so the SQL fast path must never claim those).

Proven here:
  1. the SQL Luhn expression agrees with `validate_npi` digit-for-digit over
     valid NPIs and every single-digit corruption of them (no tables needed);
  2. the grouped helper EQUALS the per-row Python helper on an adversarial
     seed: valid, bad-checksum, wrong-length, letters, unicode digits,
     space/tab/NBSP padding, inactive rows, non-npi rows, entity dedup and
     multi-intake grouping;
  3. the query transfers one row per intake, with only the rare
     cannot-judge-in-SQL values coming back.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.services.npi_validator import make_valid_npi, validate_npi
from app.tefca_registry.rce import delivery_jobs as jobs

pytestmark = pytest.mark.asyncio


def _engine():
    return create_async_engine(_normalize_url(os.environ["DATABASE_URL"]),
                               poolclass=NullPool)


# ── 1. the Luhn arithmetic itself, digit for digit ───────────────────────────

async def test_sql_luhn_agrees_with_validate_npi_on_wellformed_values(db_required):
    """Every generated valid NPI, plus every single-digit corruption of a
    sample of them, judged by the SQL expression and by `validate_npi`, must
    agree. This pins the constant-prefix contribution (24) and the
    doubled-position parity without seeding any table."""
    values = []
    for i in range(50):
        good = make_valid_npi(f"{123456789 + i * 7919:09d}")
        values.append(good)
        for pos in range(10):                      # all corruptions of each
            corrupted = (good[:pos]
                         + str((int(good[pos]) + 3) % 10)
                         + good[pos + 1:])
            values.append(corrupted)
    expected = {v: validate_npi(v)[0] for v in values}

    engine = _engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            rows = (await db.execute(text(f"""
                SELECT v, mod({jobs._luhn_sum_sql('v')}, 10) = 0
                FROM unnest(CAST(:vals AS text[])) AS v"""),
                {"vals": values})).all()
    finally:
        await engine.dispose()
    got = {v: bool(ok) for v, ok in rows}
    assert len(got) == len(expected)
    diverged = {v: (expected[v], got[v]) for v in expected if expected[v] != got[v]}
    assert not diverged, f"SQL Luhn diverged from validate_npi: {diverged}"
    # the sample must exercise both verdicts, or the comparison proves little
    assert any(expected.values()) and not all(expected.values())


# ── 2. the grouped helper against the per-row Python oracle ──────────────────

#: (label, identifier_value) — every shape the validator distinguishes.
_ADVERSARIAL_VALUES = [
    ("valid", make_valid_npi("199999990")),
    ("valid_space_padded", f" {make_valid_npi('199999991')} "),
    ("valid_tab_padded", f"\t{make_valid_npi('199999992')}"),      # SQL must defer
    ("valid_nbsp_padded", f" {make_valid_npi('199999993')}"),  # SQL must defer
    ("bad_checksum", "1999999949"),
    ("nine_digits", "123456789"),
    ("eleven_digits", "12345678901"),
    ("letters", "12345abcde"),
    ("unicode_digits", "١٢٣٤٥٦٧٨٩٠"),
    ("whitespace_only", "   "),
]


@pytest.fixture
async def seeded(db_required):
    """Two intakes with promoted entities carrying the adversarial identifier
    rows, one entity promoted from two curated lines (dedup), one INACTIVE NPI
    row and one non-npi row (both excluded). Cleaned up row-by-row."""
    engine = _engine()
    intake_a, intake_b = uuid.uuid4(), uuid.uuid4()
    ents: list = []
    async with AsyncSession(engine, expire_on_commit=False) as db:
        for intake in (intake_a, intake_b):
            await db.execute(text("""
                INSERT INTO rce_source_intakes (id, original_filename, storage_path, sha256,
                    file_size_bytes, headers, schema_fingerprint)
                VALUES (:i, 'syn.csv', '/dev/null', :h, 1, '["h"]'::jsonb, 'fp')"""),
                {"i": intake, "h": str(intake).replace("-", "").ljust(64, "0")})

        async def add_entity(intake, values, *, lines=1, status="active",
                             id_type="npi"):
            e = uuid.uuid4()
            ents.append(e)
            await db.execute(text("""
                INSERT INTO tefca_reg_entities (id, name, entity_level, entity_type)
                VALUES (:id, :n, 'participant', 'provider')"""),
                {"id": e, "n": f"SYN NPI {len(ents)}"})
            for line in range(lines):
                src = uuid.uuid4()
                await db.execute(text("""
                    INSERT INTO rce_source_records (id, source_intake_id, line_number,
                        raw_line, record_sha256, field_count)
                    VALUES (:id, :i, :ln, 'SYN', :sha, 3)"""),
                    {"id": src, "i": intake, "ln": len(ents) * 10 + line,
                     "sha": uuid.uuid4().hex.ljust(64, "0")})
                await db.execute(text("""
                    INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                        record_status, transformation_version, canonical_entity_id)
                    VALUES (:id, :i, :src, 'CLEAN', 'v1', :e)"""),
                    {"id": uuid.uuid4(), "i": intake, "src": src, "e": e})
            for value in values:
                await db.execute(text("""
                    INSERT INTO tefca_entity_identifiers (id, entity_id, identifier_type,
                        identifier_value, system_uri, identifier_status)
                    VALUES (:id, :e, :t, :v, :u, :s)"""),
                    {"id": uuid.uuid4(), "e": e, "t": id_type, "v": value,
                     "u": f"syn://{uuid.uuid4().hex}", "s": status})
            return e

        # intake A: every adversarial value, one entity each
        for _, value in _ADVERSARIAL_VALUES:
            await add_entity(intake_a, [value])
        # an entity promoted from TWO curated lines: its invalid row counts once
        await add_entity(intake_a, ["1999999949"], lines=2)
        # excluded rows: inactive NPI, and a non-npi identifier
        await add_entity(intake_a, ["1999999949"], status="retired")
        await add_entity(intake_a, ["not-an-npi-at-all"], id_type="other")
        # intake B: one valid + two invalid, proving per-intake grouping
        await add_entity(intake_b, [make_valid_npi("299999990")])
        await add_entity(intake_b, ["2999999949"])
        await add_entity(intake_b, ["29999"])
        await db.commit()
    try:
        yield engine, intake_a, intake_b
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text(
                "DELETE FROM tefca_entity_identifiers WHERE entity_id = ANY(:e)"),
                {"e": ents})
            for intake in (intake_a, intake_b):
                await db.execute(text(
                    "DELETE FROM rce_curated_records WHERE source_intake_id = :i"),
                    {"i": intake})
                await db.execute(text(
                    "DELETE FROM rce_source_records WHERE source_intake_id = :i"),
                    {"i": intake})
                await db.execute(text(
                    "DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake})
            await db.execute(text("DELETE FROM tefca_reg_entities WHERE id = ANY(:e)"),
                             {"e": ents})
            await db.commit()
        await engine.dispose()


async def test_grouped_helper_equals_per_row_python_helper(seeded):
    engine, intake_a, intake_b = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        oracle = {
            intake_a: await jobs._invalid_identifiers_promoted(db, intake_a),
            intake_b: await jobs._invalid_identifiers_promoted(db, intake_b),
        }
        bulk = await jobs._invalid_identifiers_promoted_by_intake(
            db, [intake_a, intake_b])
    got = {i: bulk.get(i, 0) for i in (intake_a, intake_b)}
    assert got == oracle, (
        f"SQL aggregation diverged from the per-row Python helper:\n"
        f"oracle={oracle}\n   sql={got}")
    # the seed must actually exercise both intakes and a non-trivial count:
    # 7 invalid on A (bad_checksum, nine, eleven, letters, unicode, whitespace,
    # dedup entity) and 2 on B — recomputed here from the validator itself so
    # the pin follows the oracle, not a hand-typed number.
    expected_a = sum(1 for _, v in _ADVERSARIAL_VALUES if not validate_npi(v)[0]) + 1
    assert oracle[intake_a] == expected_a
    assert oracle[intake_b] == 2


async def test_query_transfers_one_row_per_intake_with_rare_deferrals(seeded):
    """The point of the change: identifier values stay in the database. One
    result row per intake; only values the SQL fast path cannot judge (tab or
    unicode-whitespace padding, and other non-10-ASCII-digit shapes) come back
    for real validation."""
    engine, intake_a, intake_b = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        rows = (await db.execute(
            text(jobs._NPI_INVALID_COUNTS_SQL),
            {"ids": [str(intake_a), str(intake_b)]})).all()
    assert len(rows) == 2
    by_intake = {r[0]: r for r in rows}
    deferred_a = list(by_intake[intake_a][2] or [])
    deferred_b = list(by_intake[intake_b][2] or [])
    # A defers exactly the shapes btrim+regex cannot claim — nothing more: the
    # plain and space-padded valid values and the plain bad-checksum value are
    # judged in SQL and must not come back.
    assert sorted(deferred_a) == sorted([
        f"\t{make_valid_npi('199999992')}",
        f" {make_valid_npi('199999993')}",
        "123456789", "12345678901", "12345abcde", "١٢٣٤٥٦٧٨٩٠", "   "])
    # B's deferral is its wrong-length value only.
    assert deferred_b == ["29999"]
    # and every value the SQL judged valid/invalid agrees with the validator by
    # construction of test_grouped_helper_equals_per_row_python_helper.
