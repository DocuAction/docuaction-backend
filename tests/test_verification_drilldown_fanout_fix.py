"""Regression test for the 2026-10-02 duplicate-entity JOIN fan-out fix.

`verification_drilldown.py`'s original join (`matches JOIN rce_curated_records
rec ON rec.canonical_entity_id::text = matches.eid`) produced one output row
per CURATED RECORD, while `total` was computed as `COUNT(DISTINCT eid)` --
one per ENTITY. An entity resolved from more than one source row (a normal,
expected outcome of promotion, not an edge case) therefore appeared more than
once in the list/CSV while `total` counted it once: a visible, user-facing
mismatch between a coverage card's total and what clicking into it showed.

This test seeds exactly that scenario -- one entity backed by TWO curated
records within the same intake -- and proves the fix: `total`, the paginated
list, the unpaginated CSV helper, and the memory-bounded CSV generator all
agree on ONE row for that entity, and `source_record_count` says there were
two curated records behind it rather than silently picking one and staying
quiet about the other.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.reports.data.verification_drilldown import (count_outcome_entities,
                                                      iter_outcome_entities_csv_rows,
                                                      list_outcome_entities,
                                                      outcome_entities_csv_rows)
from app.tefca_registry.rce import verification_coverage as vc

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def fanned_out(db_required):
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    intake_id = uuid.uuid4()
    # One entity with TWO curated records (the fan-out case), plus one
    # ordinary single-record entity as a control.
    fanned_entity = uuid.uuid4()
    single_entity = uuid.uuid4()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text("""
            INSERT INTO rce_source_intakes (id, original_filename, storage_path, sha256,
                file_size_bytes, headers, schema_fingerprint)
            VALUES (:i, 'fanout.csv', '/dev/null', :h, 1, '["h"]'::jsonb, 'fp')"""),
            {"i": intake_id, "h": "2" * 64})
        for e, label in ((fanned_entity, "FANNED"), (single_entity, "SINGLE")):
            await db.execute(text("""
                INSERT INTO tefca_reg_entities (id, name, entity_level, entity_type)
                VALUES (:id, :n, 'participant', 'provider')"""), {"id": e, "n": label})

        # fanned_entity: two source rows, two curated rows, ONE canonical entity.
        src_a, src_b = uuid.uuid4(), uuid.uuid4()
        for src, ln in ((src_a, 1), (src_b, 2)):
            await db.execute(text("""
                INSERT INTO rce_source_records (id, source_intake_id, line_number, raw_line,
                    record_sha256, field_count)
                VALUES (:id, :i, :ln, :raw, :sha, 3)"""),
                {"id": src, "i": intake_id, "ln": ln, "raw": f"FANNED|{ln}",
                 "sha": f"{ln:064d}"})
            await db.execute(text("""
                INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                    record_status, transformation_version, canonical_entity_id, name, npi)
                VALUES (:id, :i, :src, 'CLEAN', 'v1', :e, :name, :npi)"""),
                {"id": uuid.uuid4(), "i": intake_id, "src": src, "e": fanned_entity,
                 "name": "FANNED ENTITY", "npi": "1111111111"})

        # single_entity: the ordinary one-curated-record case.
        src_c = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO rce_source_records (id, source_intake_id, line_number, raw_line,
                record_sha256, field_count)
            VALUES (:id, :i, 3, 'SINGLE|3', :sha, 3)"""),
            {"id": src_c, "i": intake_id, "sha": "3" * 64})
        await db.execute(text("""
            INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                record_status, transformation_version, canonical_entity_id, name, npi)
            VALUES (:id, :i, :src, 'CLEAN', 'v1', :e, 'SINGLE ENTITY', '2222222222')"""),
            {"id": uuid.uuid4(), "i": intake_id, "src": src_c, "e": single_entity})

        for e in (fanned_entity, single_entity):
            await db.execute(text("""
                INSERT INTO tefca_verifications (id, source, verification_status, entity_id)
                VALUES (:id, 'nppes', 'verified', :e)"""), {"id": uuid.uuid4(), "e": str(e)})
        await db.commit()
    try:
        yield engine, intake_id, fanned_entity, single_entity
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM tefca_verifications WHERE entity_id = ANY(:e)"),
                             {"e": [str(fanned_entity), str(single_entity)]})
            await db.execute(text("DELETE FROM rce_curated_records WHERE source_intake_id = :i"),
                             {"i": intake_id})
            await db.execute(text("DELETE FROM rce_source_records WHERE source_intake_id = :i"),
                             {"i": intake_id})
            await db.execute(text("DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake_id})
            await db.execute(text("DELETE FROM tefca_reg_entities WHERE id = ANY(:e)"),
                             {"e": [fanned_entity, single_entity]})
            await db.commit()
    await engine.dispose()


async def test_total_counts_the_fanned_entity_once(fanned_out):
    engine, intake_id, fanned_entity, single_entity = fanned_out
    async with AsyncSession(engine, expire_on_commit=False) as db:
        total = await count_outcome_entities(db, intake_id, source="nppes", outcome="verified")
    assert total == 2  # fanned_entity once + single_entity once, never 3


async def test_list_returns_exactly_one_row_per_entity_with_record_count(fanned_out):
    engine, intake_id, fanned_entity, single_entity = fanned_out
    async with AsyncSession(engine, expire_on_commit=False) as db:
        result = await list_outcome_entities(db, intake_id, source="nppes", outcome="verified",
                                             limit=50)
    assert result["total"] == 2
    assert len(result["items"]) == 2  # total and the actual page AGREE
    by_id = {item["entity_id"]: item for item in result["items"]}
    assert by_id[str(fanned_entity)]["source_record_count"] == 2
    assert by_id[str(single_entity)]["source_record_count"] == 1


async def test_csv_helper_matches_the_same_grain(fanned_out):
    engine, intake_id, fanned_entity, single_entity = fanned_out
    async with AsyncSession(engine, expire_on_commit=False) as db:
        rows = await outcome_entities_csv_rows(db, intake_id, source="nppes", outcome="verified")
    assert len(rows) == 2
    counts = {str(r.entity_id): r.source_record_count for r in rows}
    assert counts[str(fanned_entity)] == 2
    assert counts[str(single_entity)] == 1


async def test_streaming_csv_generator_matches_total_exactly(fanned_out):
    """The memory-bounded path must reconcile with `total` exactly, the same
    guarantee the JSON drill-down already proves -- including at a chunk
    size of 1, which forces the generator's internal paging loop to run more
    than once for a population this small."""
    engine, intake_id, fanned_entity, single_entity = fanned_out
    async with AsyncSession(engine, expire_on_commit=False) as db:
        total = await count_outcome_entities(db, intake_id, source="nppes", outcome="verified")
        streamed = [r async for r in iter_outcome_entities_csv_rows(
            db, intake_id, source="nppes", outcome="verified", chunk_size=1)]
    assert len(streamed) == total == 2
    seen = {str(r.entity_id) for r in streamed}
    assert seen == {str(fanned_entity), str(single_entity)}  # no duplicates, none missing
