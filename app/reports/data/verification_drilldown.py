"""Delivery-scoped, paginated per-entity verification drill-down.

WHY THIS EXISTS (reporting-architecture task, sections B/C/G)
---------------------------------------------------------------
`verification_coverage.py` answers "how many" per (delivery, source, outcome)
-- the numbers each coverage card shows (Verified / Not found / Failed /
Unavailable). This module answers "which ones": the same population, same
source/outcome classification, same SQL fragment
(`verification_coverage.EVIDENCE_ROWS_CTE_SQL`) -- but returns the matching
entities themselves, paginated, instead of a count. A card's clickable total
and this module's row count for the identical (intake, source, outcome) are
GUARANTEED to agree, because they are the same aggregation computed from the
same shared SQL, not two independent queries that could drift apart.

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
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import text

from app.tefca_registry.rce.verification_coverage import (EVIDENCE_ROWS_CTE_SQL,
                                                           OUTCOMES, SOURCES)

#: Reconciliation pending (see module docstring): never list or export this
#: outcome's population. Every other outcome in OUTCOMES is fine -- each is
#: independently well-defined per source and already reconciles against its
#: own coverage-card count.
_OUTCOME_EXPOSURE_BLOCKED = "failed"

#: Columns a caller may sort by, as a fully-qualified expression -- a fixed
#: allowlist, never raw user input concatenated into SQL (the same discipline
#: `_apply_filters` in `exception_ledger.py` uses for its own filter
#: vocabulary). `line_number` lives on `rce_source_records` (aliased `src`
#: below), not on the curated row (`rec`) -- a bare `rec.line_number` does
#: not exist.
_SORT_COLUMNS = {
    "line_number": "src.line_number",
    "entity_name": "rec.name",
    "npi": "rec.npi",
}
DEFAULT_SORT = "line_number"

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


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
    )
    """
    total = int((await db.execute(
        text(base + "SELECT count(*) FROM matches"),
        {"i": intake_id, "source": source, "outcome": outcome})).scalar() or 0)

    page_sql = base + f"""
    SELECT rec.canonical_entity_id AS entity_id, rec.source_record_id,
           src.line_number, rec.name AS entity_name, rec.npi
    FROM matches
    JOIN rce_curated_records rec
      ON rec.source_intake_id = CAST(:i AS uuid)  -- narrows via the existing
                                                    -- intake index before the
                                                    -- id match below, so this
                                                    -- never scans every
                                                    -- delivery's curated rows
     AND rec.canonical_entity_id::text = matches.eid
    LEFT JOIN rce_source_records src ON src.id = rec.source_record_id
    ORDER BY {sort_col} NULLS LAST, rec.canonical_entity_id
    LIMIT :limit OFFSET :offset
    """
    rows = (await db.execute(text(page_sql), {
        "i": intake_id, "source": source, "outcome": outcome,
        "limit": limit, "offset": offset})).all()

    items = [{
        "entity_id": str(r.entity_id), "source_record_id": str(r.source_record_id),
        "line_number": r.line_number, "entity_name": r.entity_name, "npi": r.npi,
    } for r in rows]

    return {"intake_id": str(intake_id), "source": source, "outcome": outcome,
           "total": total, "limit": limit, "offset": offset, "items": items,
           "sort": sort_col}


async def outcome_entities_csv_rows(db, intake_id: str, *, source: str, outcome: str,
                                    sort: str = DEFAULT_SORT):
    """Every matching row, unpaginated, for a controlled CSV export. Same
    population-first query as `list_outcome_entities` with no LIMIT/OFFSET --
    the caller streams this to a CSV writer rather than holding a page in
    memory twice."""
    _validate(source, outcome)
    sort_col = _SORT_COLUMNS.get(sort, _SORT_COLUMNS[DEFAULT_SORT])

    sql = _population_ctes(intake_id) + EVIDENCE_ROWS_CTE_SQL + f""",
    matches AS (
        SELECT DISTINCT eid FROM rows WHERE key = :source AND outcome = :outcome
    )
    SELECT rec.canonical_entity_id AS entity_id, rec.source_record_id,
           src.line_number, rec.name AS entity_name, rec.npi
    FROM matches
    JOIN rce_curated_records rec
      ON rec.source_intake_id = CAST(:i AS uuid)
     AND rec.canonical_entity_id::text = matches.eid
    LEFT JOIN rce_source_records src ON src.id = rec.source_record_id
    ORDER BY {sort_col} NULLS LAST, rec.canonical_entity_id
    """
    return (await db.execute(text(sql), {"i": intake_id, "source": source,
                                         "outcome": outcome})).all()
