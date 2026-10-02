"""Verification traceability (reporting-architecture task, item 2): the
paginated per-entity drill-down behind each coverage card's clickable total.

Proves:
  1. the drill-down's row COUNT for (source, outcome) equals
     `verification_coverage.coverage_counts`'s own count for the same
     (source, outcome) EXACTLY -- the "reconcile exactly" requirement, and
     the reason both modules share `EVIDENCE_ROWS_CTE_SQL` rather than two
     independently-written queries that could drift;
  2. pagination, stable sort and CSV export all agree with that same total;
  3. `outcome='failed'` is refused before any query runs, never listed or
     exported (the 1,298 checkpoint's conclusion, enforced in code);
  4. the query is population-first (AP-002's lesson): it returns rows for
     THIS intake's population only, never the whole evidence tables.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.reports.data.verification_drilldown import (UnknownSourceOrOutcome,
                                                      UnprovenOutcomeRefused,
                                                      list_outcome_entities,
                                                      outcome_entities_csv_rows)
from app.tefca_registry.rce import verification_coverage as vc

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded(db_required):
    """Same shape as test_coverage_sql_aggregation.py's `seeded` fixture --
    a small population under a unique intake, isolated and cleaned up."""
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    intake_id = uuid.uuid4()
    ent = [uuid.uuid4() for _ in range(12)]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text("""
            INSERT INTO rce_source_intakes (id, original_filename, storage_path, sha256,
                file_size_bytes, headers, schema_fingerprint)
            VALUES (:i, 'syn.csv', '/dev/null', :h, 1, '["h"]'::jsonb, 'fp')"""),
            {"i": intake_id, "h": "1" * 64})
        srcs = [uuid.uuid4() for _ in ent]
        for k, (e, src) in enumerate(zip(ent, srcs)):
            await db.execute(text("""
                INSERT INTO tefca_reg_entities (id, name, entity_level, entity_type)
                VALUES (:id, :n, 'participant', 'provider')"""), {"id": e, "n": f"DRILL {k}"})
            await db.execute(text("""
                INSERT INTO rce_source_records (id, source_intake_id, line_number, raw_line,
                    record_sha256, field_count)
                VALUES (:id, :i, :ln, :raw, :sha, 3)"""),
                {"id": src, "i": intake_id, "ln": k + 1, "raw": f"DRILL|{k}",
                 "sha": f"{k:064d}"})
            await db.execute(text("""
                INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                    record_status, transformation_version, canonical_entity_id, name, npi)
                VALUES (:id, :i, :src, 'CLEAN', 'v1', :e, :name, :npi)"""),
                {"id": uuid.uuid4(), "i": intake_id, "src": src, "e": e,
                 "name": f"DRILL ENTITY {k}", "npi": f"{1000000000 + k}"})
        # 5 verified, 3 not_found, 2 unavailable, 2 failed at nppes
        vrows = [
            ("nppes", "verified", ent[0]), ("nppes", "verified", ent[1]),
            ("nppes", "verified", ent[2]), ("nppes", "verified", ent[3]),
            ("nppes", "verified", ent[4]),
            ("nppes", "not_found", ent[5]), ("nppes", "not_found", ent[6]),
            ("nppes", "not_found", ent[7]),
            ("nppes", "unavailable", ent[8]), ("nppes", "unavailable", ent[9]),
            ("nppes", "failed", ent[10]), ("nppes", "failed", ent[11]),
        ]
        for s, st, e in vrows:
            await db.execute(text("""
                INSERT INTO tefca_verifications (id, source, verification_status, entity_id)
                VALUES (:id, :s, :st, :e)"""), {"id": uuid.uuid4(), "s": s, "st": st, "e": str(e)})
        await db.commit()
    try:
        yield engine, intake_id, ent
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM tefca_verifications WHERE entity_id = ANY(:e)"),
                             {"e": [str(x) for x in ent]})
            await db.execute(text("DELETE FROM rce_curated_records WHERE source_intake_id = :i"),
                             {"i": intake_id})
            await db.execute(text("DELETE FROM rce_source_records WHERE source_intake_id = :i"),
                             {"i": intake_id})
            await db.execute(text("DELETE FROM tefca_reg_entities WHERE id = ANY(:e)"), {"e": ent})
            await db.execute(text("DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake_id})
            await db.commit()
        await engine.dispose()


async def test_drilldown_count_reconciles_exactly_with_the_coverage_aggregate(seeded):
    engine, intake_id, _ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        counts = await vc.coverage_counts(db, intake_id)
        for outcome in ("verified", "not_found", "unavailable"):
            page = await list_outcome_entities(db, intake_id, source="nppes", outcome=outcome, limit=50)
            assert page["total"] == counts["nppes"][outcome], (
                f"{outcome}: drilldown total {page['total']} != coverage count {counts['nppes'][outcome]}")
            assert len(page["items"]) == page["total"]


async def test_pagination_pages_add_up_to_the_total_with_no_overlap(seeded):
    engine, intake_id, _ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        page1 = await list_outcome_entities(db, intake_id, source="nppes", outcome="verified", limit=2, offset=0)
        page2 = await list_outcome_entities(db, intake_id, source="nppes", outcome="verified", limit=2, offset=2)
        page3 = await list_outcome_entities(db, intake_id, source="nppes", outcome="verified", limit=2, offset=4)
        assert page1["total"] == page2["total"] == page3["total"] == 5
        ids = ([i["entity_id"] for i in page1["items"]] + [i["entity_id"] for i in page2["items"]]
               + [i["entity_id"] for i in page3["items"]])
        assert len(ids) == 5 and len(set(ids)) == 5, "pages must not overlap or repeat a row"


async def test_sort_is_stable_across_repeated_calls(seeded):
    engine, intake_id, _ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        first = await list_outcome_entities(db, intake_id, source="nppes", outcome="verified", sort="entity_name")
        second = await list_outcome_entities(db, intake_id, source="nppes", outcome="verified", sort="entity_name")
        assert [i["entity_id"] for i in first["items"]] == [i["entity_id"] for i in second["items"]]


async def test_csv_export_matches_the_paginated_total(seeded):
    engine, intake_id, _ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        page = await list_outcome_entities(db, intake_id, source="nppes", outcome="not_found", limit=1)
        rows = await outcome_entities_csv_rows(db, intake_id, source="nppes", outcome="not_found")
        assert len(rows) == page["total"] == 3


async def test_failed_outcome_is_refused_before_any_query_runs(seeded):
    engine, intake_id, _ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        with pytest.raises(UnprovenOutcomeRefused):
            await list_outcome_entities(db, intake_id, source="nppes", outcome="failed")
        with pytest.raises(UnprovenOutcomeRefused):
            await outcome_entities_csv_rows(db, intake_id, source="nppes", outcome="failed")


async def test_unknown_source_or_outcome_is_refused(seeded):
    engine, intake_id, _ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        with pytest.raises(UnknownSourceOrOutcome):
            await list_outcome_entities(db, intake_id, source="not-a-real-source", outcome="verified")
        with pytest.raises(UnknownSourceOrOutcome):
            await list_outcome_entities(db, intake_id, source="nppes", outcome="not-a-real-outcome")


async def test_a_different_intake_sees_none_of_this_populations_rows(seeded):
    """Population-first scoping: a query for an unrelated intake must return
    zero rows, never any of THIS fixture's entities."""
    engine, _intake_id, _ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        other = await list_outcome_entities(db, uuid.uuid4(), source="nppes", outcome="verified")
        assert other["total"] == 0 and other["items"] == []
