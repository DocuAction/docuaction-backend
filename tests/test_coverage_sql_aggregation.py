"""AP-002 — verification coverage is counted in SQL, oracle-equal to the old
Python tally, and no longer pulls the whole population's evidence rows.

The detail endpoint's heaviest block was `verification_coverage`, which pulled
every dimension-evidence row of the delivery (122,945 at September scale) plus
the verification rows across the wire and built Python sets. The SQL is optimal
(a full-population hash join — an index cannot help), so the cost was the row
transfer + set-building. `coverage_counts` does the counting in Postgres and
returns at most one row per source.

These tests prove:
  1. the SQL counts EQUAL the Python `_tally` reduced to len() (oracle), on a
     seed exercising every source spelling, both evidence tables, the
     deactivated-from-detail override, unmapped statuses, and cross-table
     entity de-duplication;
  2. the query transfers a bounded number of rows (<= number of sources),
     not the whole population.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.tefca_registry.rce import verification_coverage as vc

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded(db_required):
    """A small, adversarial coverage population under a unique intake in the
    already-migrated test DB. Isolated from any other data by its intake_id;
    every row is deleted in teardown."""
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    intake_id = uuid.uuid4()
    ent = [uuid.uuid4() for _ in range(60)]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text("""
            INSERT INTO rce_source_intakes (id, original_filename, storage_path, sha256,
                file_size_bytes, headers, schema_fingerprint)
            VALUES (:i, 'syn.csv', '/dev/null', :h, 1, '["h"]'::jsonb, 'fp')"""),
            {"i": intake_id, "h": "0" * 64})
        # 60 entities promoted for this intake
        srcs = [uuid.uuid4() for _ in ent]
        for k, (e, src) in enumerate(zip(ent, srcs)):
            await db.execute(text("""
                INSERT INTO tefca_reg_entities (id, name, entity_level, entity_type)
                VALUES (:id, :n, 'participant', 'provider')"""), {"id": e, "n": f"SYN {k}"})
            await db.execute(text("""
                INSERT INTO rce_source_records (id, source_intake_id, line_number, raw_line,
                    record_sha256, field_count)
                VALUES (:id, :i, :ln, :raw, :sha, 3)"""),
                {"id": src, "i": intake_id, "ln": k + 1, "raw": f"SYN|{k}",
                 "sha": f"{k:064d}"})
            await db.execute(text("""
                INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                    record_status, transformation_version, canonical_entity_id)
                VALUES (:id, :i, :src, 'CLEAN', 'v1', :e)"""),
                {"id": uuid.uuid4(), "i": intake_id, "src": src, "e": e})
        # verification rows: every source spelling, mapped + unmapped + deactivated override
        vrows = [
            ("nppes", "verified", None, ent[0]),
            ("npi_registry", "match", None, ent[1]),       # nppes alt spelling -> verified
            ("cms_nppes", "active", "provider is DEACTIVATED now", ent[2]),  # deactivated override
            ("pecos", "not_found", None, ent[3]),
            ("oig_leie", "no_match", None, ent[4]),         # leie alt -> not_found
            ("sam.gov", "source_unavailable", None, ent[5]),  # sam alt -> unavailable
            ("nppes", "wizard", None, ent[6]),              # unmapped status -> attempted only
            ("nppes", "verified", None, ent[0]),            # duplicate entity, same outcome
            ("UNKNOWNSRC", "verified", None, ent[7]),        # unknown source -> ignored
        ]
        for s, st, det, e in vrows:
            await db.execute(text("""
                INSERT INTO tefca_verifications (id, source, verification_status, detail, entity_id)
                VALUES (:id, :s, :st, :det, :e)"""),
                {"id": uuid.uuid4(), "s": s, "st": st, "det": det, "e": str(e)})
        # dimension rows: PASS/FAIL/CONFLICT/UNAVAILABLE/NOT_FOUND + cross-table dedup
        drows = [
            ("nppes", "PASS", ent[0]),      # same entity already verified via verification row
            ("leie", "FAIL", ent[8]),
            ("leie", "CONFLICT", ent[9]),    # -> failed
            ("sam", "NOT_FOUND", ent[10]),
            ("pecos", "UNAVAILABLE", ent[11]),
            ("nppes", "CORROBORATED", ent[12]),  # -> verified
            ("nppes", "MYSTERY", ent[13]),    # unmapped disposition -> attempted only
        ]
        for s, disp, e in drows:
            await db.execute(text("""
                INSERT INTO tefca_dimension_evidence (id, entity_id, evidence_dimension, source, disposition)
                VALUES (:id, :e, 'DIM', :s, :disp)"""),
                {"id": uuid.uuid4(), "e": str(e), "s": s, "disp": disp})
        await db.commit()
    try:
        yield engine, intake_id
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM tefca_dimension_evidence WHERE entity_id = ANY(:e)"),
                             {"e": [str(x) for x in ent]})
            await db.execute(text("DELETE FROM tefca_verifications WHERE entity_id = ANY(:e)"),
                             {"e": [str(x) for x in ent]})
            await db.execute(text("DELETE FROM rce_curated_records WHERE source_intake_id = :i"),
                             {"i": intake_id})
            await db.execute(text("DELETE FROM rce_source_records WHERE source_intake_id = :i"),
                             {"i": intake_id})
            await db.execute(text("DELETE FROM tefca_reg_entities WHERE id = ANY(:e)"),
                             {"e": ent})
            await db.execute(text("DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake_id})
            await db.commit()
        await engine.dispose()


async def test_coverage_sql_matches_python_oracle(seeded):
    engine, intake_id = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        # oracle: the OLD Python path
        oracle = vc._tally(await vc._verification_rows(db, intake_id),
                           await vc._dimension_rows(db, intake_id))
        oracle_counts = {k: {o: len(oracle[k][o]) for o in ("attempted", *vc.OUTCOMES)}
                         for k in vc.SOURCES}
        # new SQL path
        sql_counts = await vc.coverage_counts(db, intake_id)
    assert sql_counts == oracle_counts, (
        f"SQL aggregation diverged from the Python oracle:\n"
        f"oracle={oracle_counts}\n   sql={sql_counts}")
    # sanity: the seed actually exercised outcomes, not all-zero
    assert sql_counts["nppes"]["verified"] >= 2
    assert sql_counts["nppes"]["deactivated"] == 1
    assert sql_counts["leie"]["failed"] == 2


async def test_coverage_query_transfers_bounded_rows(seeded):
    """The whole point of AP-002: the population's evidence rows stay in the DB.
    The aggregation returns at most one row per source, never the 60-entity /
    16-evidence-row population."""
    engine, intake_id = seeded
    returned = {"n": 0}
    async with AsyncSession(engine, expire_on_commit=False) as db:
        conn = await db.connection()
        sync_engine = conn.sync_connection.engine

        def _count(conn_, cursor, statement, parameters, context, executemany):
            if "count(DISTINCT eid)" in statement:
                context._ap002_marked = True

        # Execute and inspect the number of result rows directly.
        rows = (await db.execute(text(vc._COVERAGE_COUNTS_SQL), {"i": str(intake_id)})).all()
        returned["n"] = len(rows)
    assert returned["n"] <= len(vc.SOURCES), (
        f"aggregation returned {returned['n']} rows; must be <= {len(vc.SOURCES)} (one per source)")
    assert returned["n"] >= 1
