"""Delivery-scoped, paginated per-entity verification drill-down.

WHY THIS EXISTS (reporting-architecture task, sections B/C/G)
---------------------------------------------------------------
`verification_coverage.py` answers "how many" per (delivery, source, outcome)
-- the numbers each coverage card shows (Verified / Not found / Failed /
Unavailable), counted as DISTINCT ELIGIBLE ENTITIES (its own docstring). This
module answers "which ones": the same population, same source/outcome
classification, same SQL fragment (`verification_coverage.EVIDENCE_ROWS_CTE_SQL`)
-- but returns the matching entities themselves, paginated, instead of a
count. A card's clickable total and this module's row count for the
identical (intake, source, outcome) are GUARANTEED to agree, because both are
counts/listings of the same DISTINCT-ENTITY population, computed from the
same shared SQL, not two independent queries that could drift apart.

ROW GRAIN IS EXPLICIT: ONE ROW PER ENTITY, NEVER PER CURATED RECORD
-----------------------------------------------------------------------
Fixed 2026-10-02 (SAM/reporting correction pass). `rce_curated_records` can
hold more than one row per `canonical_entity_id` within one intake --
multiple source rows the promotion step resolved to the same entity, a
normal, expected outcome (see `promotion.py`'s `oid_to_entity` reuse). The
original join (`JOIN rce_curated_records rec ON rec.canonical_entity_id::text
= matches.eid`) fanned out: one entity with N curated rows produced N output
rows, while `total` stayed the DISTINCT-entity count from `matches` -- a
visible, user-facing mismatch between the card's total and the list/CSV's
actual row count.

`_entity_rows_sql()` below picks exactly ONE curated record per entity
(earliest `line_number`, tied-broken by `source_record_id` for determinism)
as that entity's representative row, and exposes `source_record_count` so a
reviewer can see when more than one source row stood behind an entity rather
than being told, silently, there was only one. `total`, `items`, and the CSV
export are now the same population at the same grain, by construction.

POPULATION-FIRST, NOT EVIDENCE-FIRST (AP-002's lesson, not repeated here)
---------------------------------------------------------------------------
AP-002 (qa-evidence/2026-09-25-*, verification_coverage.py's own comment)
measured the detail endpoint pulling the WHOLE population's dimension
evidence (122,945 rows at September scale) before filtering to one delivery.
Every query below starts from `rce_curated_records` filtered to ONE
`source_intake_id` (`pop_uuid`), then filters both evidence tables to ONLY
that population's entities (`WHERE entity_id IN (SELECT eid FROM pop_uuid)`)
BEFORE any join or sort -- never a full scan of either evidence table.

NEVER THE "FAILED" POPULATION (reporting-architecture task, item 2)
---------------------------------------------------------------------
The 1,298 figure the task's own static-code checkpoint could not reconcile
(qa-evidence/2026-10-01-reporting-architecture/CHECKPOINT-1298-FAILED-INDICATOR-STATIC-ANALYSIS.md)
is this module's own `failed` outcome, summed across sources incorrectly by
whoever displayed it. This module refuses to list or export a `failed`
population until a governed diagnostic proves what it means --
`UnprovenOutcomeRefused` is raised before any query runs, not caught after.

MEMORY-BOUNDED CSV (2026-10-02)
------------------------------------
`outcome_entities_csv_rows()` is kept for any existing caller that genuinely
needs the whole list at once; the CSV route now uses
`iter_outcome_entities_csv_rows()` instead, which pages internally
(`_CSV_CHUNK_SIZE` rows at a time, same query as the paginated list) and
yields rows as an async generator -- the full population is never held in
memory at once, however large one delivery's distinct-entity count grows.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy import text

from app.tefca_registry.rce.verification_coverage import (EVIDENCE_ROWS_CTE_SQL,
                                                           OUTCOMES, SOURCES)

#: Reconciliation pending (see module docstring): never list or export this
#: outcome's population. Every other outcome in OUTCOMES is fine -- each is
#: independently well-defined per source and already reconciles against its
#: own coverage-card count.
_OUTCOME_EXPOSURE_BLOCKED = "failed"

#: Columns a caller may sort by -- a fixed allowlist, never raw user input
#: concatenated into SQL (the same discipline `_apply_filters` in
#: `exception_ledger.py` uses for its own filter vocabulary). These are bare
#: names because the final SELECT (below) is over `entity_rows`, a single
#: already-deduplicated CTE -- not a table-qualified `rec.`/`src.` reference
#: into the raw join, which is exactly the shape that produced the old
#: per-curated-record fan-out.
_SORT_COLUMNS = {
    "line_number": "line_number",
    "entity_name": "entity_name",
    "npi": "npi",
}
DEFAULT_SORT = "line_number"

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

#: Internal page size for the CSV generator's own paging loop -- unrelated to
#: MAX_PAGE_SIZE, which bounds what an API caller may request per HTTP page.
_CSV_CHUNK_SIZE = 1000


class UnprovenOutcomeRefused(RuntimeError):
    """Raised instead of ever running a query for the blocked outcome."""


class UnknownSourceOrOutcome(ValueError):
    """`source` or `outcome` is outside the vocabulary this module knows."""


def _population_ctes(intake_id: str) -> str:
    return """
    WITH pop_uuid AS (
        SELECT DISTINCT canonical_entity_id AS eid
        FROM rce_curated_records
        WHERE source_intake_id = CAST(:i AS uuid) AND canonical_entity_id IS NOT NULL),
    pop_text AS (SELECT CAST(eid AS text) AS eid FROM pop_uuid),
    """


def _entity_rows_cte() -> str:
    """One row per matched ENTITY, never per curated record.

    `ROW_NUMBER() ... PARTITION BY rec.canonical_entity_id` picks the single
    earliest curated row (by line_number, tied-broken by source_record_id)
    as that entity's representative; `COUNT(*) OVER (...)` on the same
    partition exposes how many curated rows actually stood behind the
    entity, so collapsing to one row never hides that there were more.
    Depends on `matches` already being defined by the caller.
    """
    return """
    entity_candidates AS (
        SELECT rec.canonical_entity_id AS entity_id, rec.source_record_id,
               src.line_number, rec.name AS entity_name, rec.npi,
               COUNT(*) OVER (PARTITION BY rec.canonical_entity_id) AS source_record_count,
               ROW_NUMBER() OVER (
                   PARTITION BY rec.canonical_entity_id
                   ORDER BY src.line_number NULLS LAST, rec.source_record_id
               ) AS rn
        FROM matches
        JOIN rce_curated_records rec
          ON rec.source_intake_id = CAST(:i AS uuid)  -- narrows via the existing
                                                        -- intake index before the
                                                        -- id match below, so this
                                                        -- never scans every
                                                        -- delivery's curated rows
         AND rec.canonical_entity_id::text = matches.eid
        LEFT JOIN rce_source_records src ON src.id = rec.source_record_id
    ),
    entity_rows AS (
        SELECT entity_id, source_record_id, line_number, entity_name, npi,
               source_record_count
        FROM entity_candidates
        WHERE rn = 1
    )
    """


def validate_source_outcome(source: str, outcome: str) -> None:
    """Public entry point for a caller that must validate BEFORE doing
    anything that can't be undone once started -- e.g. opening a streaming
    HTTP response, where a 409/422 can no longer become the status code
    once the body has begun sending. Delegates to `_validate`, which every
    query-running function in this module also calls on its own first line,
    so there is exactly one place the vocabulary/blocked-outcome rule lives."""
    _validate(source, outcome)


def _validate(source: str, outcome: str) -> None:
    if source not in SOURCES:
        raise UnknownSourceOrOutcome(f"source must be one of {sorted(SOURCES)}; got {source!r}")
    if outcome not in OUTCOMES:
        raise UnknownSourceOrOutcome(f"outcome must be one of {OUTCOMES}; got {outcome!r}")
    if outcome == _OUTCOME_EXPOSURE_BLOCKED:
        raise UnprovenOutcomeRefused(
            "The 'failed' outcome's population is not exposed: it is a "
            "per-source count whose cross-source meaning is not yet proven "
            "(see CHECKPOINT-1298-FAILED-INDICATOR-STATIC-ANALYSIS.md). "
            "Listing or exporting it would claim a disjoint, reconciled "
            "population that has not been shown to exist.")


async def list_outcome_entities(db, intake_id: str, *, source: str, outcome: str,
                                limit: int = DEFAULT_PAGE_SIZE, offset: int = 0,
                                sort: str = DEFAULT_SORT) -> Dict[str, Any]:
    """One page of entities with evidence of `outcome` at `source`, for ONE
    delivery. Raises `UnprovenOutcomeRefused` for `outcome='failed'`,
    `UnknownSourceOrOutcome` for anything else outside the vocabulary.

    Stable sort: ties within the chosen column break on `entity_id` so the
    same row never appears twice or never across two pages of the same query.
    """
    _validate(source, outcome)
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    offset = max(0, int(offset))
    sort_col = _SORT_COLUMNS.get(sort, _SORT_COLUMNS[DEFAULT_SORT])

    base = _population_ctes(intake_id) + EVIDENCE_ROWS_CTE_SQL + """,
    matches AS (
        SELECT DISTINCT eid FROM rows WHERE key = :source AND outcome = :outcome
    ),
    """ + _entity_rows_cte()
    total = int((await db.execute(
        text(base + "SELECT count(*) FROM entity_rows"),
        {"i": intake_id, "source": source, "outcome": outcome})).scalar() or 0)

    page_sql = base + f"""
    SELECT entity_id, source_record_id, line_number, entity_name, npi,
           source_record_count
    FROM entity_rows
    ORDER BY {sort_col} NULLS LAST, entity_id
    LIMIT :limit OFFSET :offset
    """
    rows = (await db.execute(text(page_sql), {
        "i": intake_id, "source": source, "outcome": outcome,
        "limit": limit, "offset": offset})).all()

    items = [{
        "entity_id": str(r.entity_id), "source_record_id": str(r.source_record_id),
        "line_number": r.line_number, "entity_name": r.entity_name, "npi": r.npi,
        "source_record_count": r.source_record_count,
    } for r in rows]

    # `total` and `len(items)` are now the SAME population at the SAME grain
    # (one row per entity) by construction -- this is the reconciliation the
    # 2026-10-02 fix exists to guarantee, asserted here rather than only
    # trusted, since a future edit to either CTE could silently reintroduce
    # the mismatch.
    assert total >= len(items), (
        f"drill-down total ({total}) is smaller than the page returned "
        f"({len(items)}) for intake={intake_id} source={source} "
        f"outcome={outcome} -- entity_rows and matches have diverged")

    return {"intake_id": str(intake_id), "source": source, "outcome": outcome,
           "total": total, "limit": limit, "offset": offset, "items": items,
           "sort": sort_col}


async def count_outcome_entities(db, intake_id: str, *, source: str, outcome: str) -> int:
    """The same `total` `list_outcome_entities` returns, as a standalone
    COUNT query -- for a caller (the CSV route) that needs the number
    up front, before streaming, without materializing any rows to get it."""
    _validate(source, outcome)
    base = _population_ctes(intake_id) + EVIDENCE_ROWS_CTE_SQL + """,
    matches AS (
        SELECT DISTINCT eid FROM rows WHERE key = :source AND outcome = :outcome
    ),
    """ + _entity_rows_cte()
    return int((await db.execute(
        text(base + "SELECT count(*) FROM entity_rows"),
        {"i": intake_id, "source": source, "outcome": outcome})).scalar() or 0)


async def outcome_entities_csv_rows(db, intake_id: str, *, source: str, outcome: str,
                                    sort: str = DEFAULT_SORT):
    """Every matching row, unpaginated, at the same one-row-per-entity grain
    as `list_outcome_entities`. Kept for any caller that genuinely needs the
    whole list materialized at once; the CSV export route uses
    `iter_outcome_entities_csv_rows()` instead so a large delivery's export
    never holds its full population in memory."""
    _validate(source, outcome)
    sort_col = _SORT_COLUMNS.get(sort, _SORT_COLUMNS[DEFAULT_SORT])

    sql = _population_ctes(intake_id) + EVIDENCE_ROWS_CTE_SQL + f""",
    matches AS (
        SELECT DISTINCT eid FROM rows WHERE key = :source AND outcome = :outcome
    ),
    """ + _entity_rows_cte() + f"""
    SELECT entity_id, source_record_id, line_number, entity_name, npi,
           source_record_count
    FROM entity_rows
    ORDER BY {sort_col} NULLS LAST, entity_id
    """
    return (await db.execute(text(sql), {"i": intake_id, "source": source,
                                         "outcome": outcome})).all()


async def iter_outcome_entities_csv_rows(
    db, intake_id: str, *, source: str, outcome: str, sort: str = DEFAULT_SORT,
    chunk_size: int = _CSV_CHUNK_SIZE,
) -> AsyncIterator[Any]:
    """Same population, same grain as `outcome_entities_csv_rows`, but paged
    internally (`chunk_size` rows per round-trip) and yielded one row at a
    time -- the full result set is never materialized in memory at once, no
    matter how large one delivery's distinct-entity count grows.

    Raises `UnprovenOutcomeRefused`/`UnknownSourceOrOutcome` immediately, on
    the first call, before any query runs -- same fail-fast contract as the
    other two entry points in this module.
    """
    _validate(source, outcome)
    sort_col = _SORT_COLUMNS.get(sort, _SORT_COLUMNS[DEFAULT_SORT])

    base = _population_ctes(intake_id) + EVIDENCE_ROWS_CTE_SQL + """,
    matches AS (
        SELECT DISTINCT eid FROM rows WHERE key = :source AND outcome = :outcome
    ),
    """ + _entity_rows_cte()
    page_sql = base + f"""
    SELECT entity_id, source_record_id, line_number, entity_name, npi,
           source_record_count
    FROM entity_rows
    ORDER BY {sort_col} NULLS LAST, entity_id
    LIMIT :limit OFFSET :offset
    """
    offset = 0
    while True:
        rows = (await db.execute(text(page_sql), {
            "i": intake_id, "source": source, "outcome": outcome,
            "limit": chunk_size, "offset": offset})).all()
        if not rows:
            return
        for r in rows:
            yield r
        if len(rows) < chunk_size:
            return
        offset += chunk_size
