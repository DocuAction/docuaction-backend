"""Authoritative SAM public-extract enumeration for one delivery.

WHY THIS EXISTS (2026-10-31)
────────────────────────────
The October catch-up left every eligible entity with at least one
`SAM_EXTRACT_SUPPLEMENTARY` evidence row — and no deployed surface that could
say which entities were CANDIDATES. The extract source was unmapped by
`verification_coverage._SOURCE_CASE`, `findings.csv` is issue-ledger-based
(and candidate hits wrote no ledger row until EXC-001), and the batch
responses were the only place candidate ids ever appeared. The authoritative
list lived one SQL query away from every operator, on the wrong side of an
API that did not expose it.

THE GRAIN RULES (the same discipline `verification_drilldown` documents)
────────────────────────────────────────────────────────────────────────
* The population is the delivery's DISTINCT promoted entities — never
  evidence rows. An entity screened three times (the 2026-10 duplicate
  generations) is ONE entity with three rows; the duplicate rows are counted
  and reported separately, never as extra entities.
* The CURRENT outcome per entity is its LATEST extract row (`created_at`
  DESC, id DESC tiebreak). Earlier generations stay visible as
  `extract_row_count`, never silently collapsed: append-only history is the
  point of the evidence table.
* Outcome words are the screen's own: `candidate` (REVIEW — a potential
  match awaiting an analyst, NEVER a confirmed exclusion), `no_candidate`
  (PASS — no candidate in the edition consulted, NEVER exclusion clearance),
  `unavailable` (the screen could not run), `other` (an unrecognised
  disposition, disclosed rather than guessed at), `missing` (no extract row
  at all). They are deliberately NOT the coverage module's generic
  verified/not_found vocabulary, which would label a candidate "not_found".

Reads only, except `backfill_candidate_issues(..., dry_run=False)`, which
writes through `verification_findings.record_sam_extract_candidate` — the
same idempotent writer the live screen path uses — and exists so the
candidate rows persisted BEFORE EXC-001 shipped can be placed on the analyst
worklist under an explicit, audited, PM-authorized act (dry-run is the
default; the plan is the approval artifact).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

EXTRACT_SOURCE = "SAM_EXTRACT_SUPPLEMENTARY"

OUTCOME_WORDS = ("candidate", "no_candidate", "unavailable", "other", "missing")

_TRUTH_NOTE = (
    "A candidate is a potential match awaiting analyst determination — never "
    "a confirmed exclusion. A no_candidate result means no candidate in the "
    "extract edition consulted — never exclusion clearance. This supplementary "
    "screen is separate from, and never substitutes for, the api.sam.gov "
    "primary check (whose own availability is reported under the 'sam' source).")

#: Latest-row-per-entity over the delivery's extract evidence, with the
#: duplicate-generation count carried alongside. Shared by every query below
#: so the summary's totals and the candidate list are the same population at
#: the same grain by construction.
_LATEST_CTE = """
    WITH pop AS (
        SELECT DISTINCT canonical_entity_id AS eid
        FROM rce_curated_records
        WHERE source_intake_id = CAST(:i AS uuid)
          AND canonical_entity_id IS NOT NULL),
    rows_all AS (
        SELECT d.id, d.entity_id,
               upper(btrim(coalesce(d.disposition, ''))) AS disp,
               d.created_at, d.generation_timestamp, d.query_timestamp,
               d.dataset_version_anchor, d.rule_applied, d.original_values,
               ROW_NUMBER() OVER (PARTITION BY d.entity_id
                                  ORDER BY d.created_at DESC, d.id DESC) AS rn,
               COUNT(*) OVER (PARTITION BY d.entity_id) AS extract_row_count
        FROM tefca_dimension_evidence d
        WHERE d.source = :src
          AND d.entity_id IN (SELECT CAST(eid AS text) FROM pop)),
    latest AS (
        SELECT *,
               CASE WHEN disp = 'REVIEW' THEN 'candidate'
                    WHEN disp IN ('PASS', 'CORROBORATED') THEN 'no_candidate'
                    WHEN disp = 'UNAVAILABLE' THEN 'unavailable'
                    ELSE 'other' END AS outcome
        FROM rows_all WHERE rn = 1)
"""

_SUMMARY_SQL = _LATEST_CTE + """
    SELECT (SELECT count(*) FROM pop) AS eligible,
           count(*) AS entities_with_row,
           count(*) FILTER (WHERE outcome = 'candidate') AS candidate,
           count(*) FILTER (WHERE outcome = 'no_candidate') AS no_candidate,
           count(*) FILTER (WHERE outcome = 'unavailable') AS unavailable,
           count(*) FILTER (WHERE outcome = 'other') AS other,
           coalesce(sum(extract_row_count), 0) AS total_rows,
           coalesce(sum(extract_row_count), 0) - count(*) AS duplicate_rows,
           count(*) FILTER (WHERE extract_row_count > 1) AS entities_with_duplicates
    FROM latest
"""

_EDITIONS_SQL = _LATEST_CTE + """
    SELECT coalesce(dataset_version_anchor, 'not recorded') AS anchor,
           coalesce(original_values->>'publication_date', 'not recorded') AS publication_date,
           coalesce(original_values->>'extract_sha256', 'not recorded') AS extract_sha256,
           count(*) AS entities
    FROM latest
    GROUP BY 1, 2, 3
    ORDER BY entities DESC
"""

#: One representative curated row per candidate entity (earliest line_number,
#: `source_record_id` tiebreak — `verification_drilldown._entity_rows_cte`'s
#: rule), with the entity's latest-ledger-finding state alongside so the list
#: itself shows which candidates still lack an EXC-001 worklist row.
_CANDIDATES_SQL = _LATEST_CTE + """,
    reps AS (
        SELECT l.entity_id, l.id AS evidence_row_id, l.created_at,
               l.generation_timestamp, l.query_timestamp,
               l.dataset_version_anchor, l.extract_row_count,
               l.original_values,
               rec.name AS entity_name, rec.npi, rec.source_record_id,
               src.line_number,
               ROW_NUMBER() OVER (
                   PARTITION BY l.entity_id
                   ORDER BY src.line_number NULLS LAST, rec.source_record_id) AS rep_rn
        FROM latest l
        JOIN rce_curated_records rec
          ON rec.source_intake_id = CAST(:i AS uuid)
         AND rec.canonical_entity_id::text = l.entity_id
        LEFT JOIN rce_source_records src ON src.id = rec.source_record_id
        WHERE l.outcome = 'candidate')
    SELECT r.entity_id, r.entity_name, r.npi, r.line_number,
           r.evidence_row_id, r.created_at, r.generation_timestamp,
           r.query_timestamp, r.dataset_version_anchor, r.extract_row_count,
           r.original_values,
           (SELECT i.issue_code FROM rce_issues i
            WHERE i.source_record_id = r.source_record_id
              AND i.issue_type = 'SAM_EXTRACT_CANDIDATE'
            ORDER BY i.created_at DESC LIMIT 1) AS ledger_issue_code
    FROM reps r
    WHERE r.rep_rn = 1
    ORDER BY r.line_number NULLS LAST, r.entity_id
    LIMIT :limit OFFSET :offset
"""

_CANDIDATES_COUNT_SQL = _LATEST_CTE + """
    SELECT count(*) FROM latest WHERE outcome = 'candidate'
"""

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def _provenance(values: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The edition/match facts of one extract row, from its own
    `original_values` — exactly as `SAMExtractConnector._edition_evidence`
    recorded them, never re-derived. Absent keys stay absent-honest."""
    if isinstance(values, str):
        # A text() query can hand JSONB back as its serialized form depending
        # on the connection's codec state; parse rather than silently losing
        # the provenance.
        import json
        try:
            values = json.loads(values)
        except ValueError:
            values = {}
    values = values if isinstance(values, dict) else {}
    return {
        "publication_date": values.get("publication_date"),
        "listing_date_modified": values.get("listing_date_modified"),
        "http_last_modified": values.get("http_last_modified"),
        "extract_sha256": values.get("extract_sha256"),
        "source_dataset": values.get("source_dataset"),
        "candidate_count": values.get("candidate_count"),
        "match_methods": values.get("match_methods"),
    }


async def extract_summary(db, intake_id) -> Dict[str, Any]:
    """Exact per-entity extract outcome counts for one delivery, reconciled.

    `eligible = candidate + no_candidate + unavailable + other + missing` is
    asserted into the payload (`reconciles`) rather than trusted — the caller
    and the reader get the equation, not a claim about it."""
    row = (await db.execute(text(_SUMMARY_SQL),
                            {"i": str(intake_id), "src": EXTRACT_SOURCE})).one()
    eligible = int(row.eligible or 0)
    with_row = int(row.entities_with_row or 0)
    counts = {
        "candidate": int(row.candidate or 0),
        "no_candidate": int(row.no_candidate or 0),
        "unavailable": int(row.unavailable or 0),
        "other": int(row.other or 0),
        "missing": max(0, eligible - with_row),
    }
    editions = [{
        "dataset_version_anchor": r.anchor,
        "publication_date": r.publication_date,
        "extract_sha256": r.extract_sha256,
        "entities": int(r.entities or 0),
    } for r in (await db.execute(text(_EDITIONS_SQL),
                                 {"i": str(intake_id), "src": EXTRACT_SOURCE})).all()]
    return {
        "intake_id": str(intake_id),
        "source": EXTRACT_SOURCE,
        "eligible": eligible,
        "entities_with_extract_row": with_row,
        "current_outcomes": counts,
        "reconciles": eligible == with_row + counts["missing"]
                      and with_row == sum(counts[k] for k in
                                          ("candidate", "no_candidate", "unavailable", "other")),
        "evidence_rows": {
            "total": int(row.total_rows or 0),
            "duplicate_rows_beyond_latest": int(row.duplicate_rows or 0),
            "entities_with_more_than_one_row": int(row.entities_with_duplicates or 0),
            "note": ("Historical generations are counted here and preserved "
                     "append-only; the current outcome above reads each "
                     "entity's latest row only."),
        },
        "editions": editions,
        "note": _TRUTH_NOTE,
    }


async def list_candidates(db, intake_id, *, limit: int = DEFAULT_PAGE_SIZE,
                          offset: int = 0) -> Dict[str, Any]:
    """The candidate entities themselves (latest row REVIEW), paginated, one
    row per entity, with evidence references and per-row edition provenance.

    `ledger_issue_code` is the entity's newest SAM_EXTRACT_CANDIDATE finding
    when one exists — null marks a candidate persisted before EXC-001 that
    the (owner-authorized) backfill has not yet placed on the worklist."""
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    offset = max(0, int(offset))
    params = {"i": str(intake_id), "src": EXTRACT_SOURCE,
              "limit": limit, "offset": offset}
    total = int((await db.execute(
        text(_CANDIDATES_COUNT_SQL),
        {"i": str(intake_id), "src": EXTRACT_SOURCE})).scalar() or 0)
    rows = (await db.execute(text(_CANDIDATES_SQL), params)).all()
    items = [{
        "entity_id": str(r.entity_id),
        "entity_name": r.entity_name,
        "npi": r.npi,
        "line_number": r.line_number,
        "evidence_row_id": str(r.evidence_row_id),
        "evidence_created_at": r.created_at.isoformat() if r.created_at else None,
        "generation_timestamp": r.generation_timestamp,
        "query_timestamp": r.query_timestamp,
        "dataset_version_anchor": r.dataset_version_anchor,
        "extract_row_count": int(r.extract_row_count or 0),
        "provenance": _provenance(r.original_values),
        "ledger_issue_code": r.ledger_issue_code,
    } for r in rows]
    return {"intake_id": str(intake_id), "source": EXTRACT_SOURCE,
            "outcome": "candidate", "total": total,
            "limit": limit, "offset": offset, "items": items,
            "note": _TRUTH_NOTE}


async def backfill_candidate_issues(db, intake_id, *, dry_run: bool = True,
                                    actor: str = "SYSTEM") -> Dict[str, Any]:
    """Place pre-EXC-001 candidate rows on the analyst worklist, idempotently.

    For every entity whose LATEST extract row is a candidate, run the same
    `record_sam_extract_candidate` writer the live path uses: an undecided
    finding already open is returned, not duplicated, so running this twice
    — or running it after the live path already wrote some — changes nothing
    it already did. `dry_run=True` (the default) only reports what a write
    would do; it is the exact write-scope statement an approver reads.

    Never commits on dry-run; commits once at the end of a real run. Never
    touches evidence rows, dispositions, buckets or entity status — the ONE
    write is the EXC-001 ledger finding per not-yet-listed candidate.
    """
    from app.tefca_registry.rce import verification_findings as vf

    # Page through ALL candidates (the enumeration is bounded by the
    # delivery's own candidate count, small by construction; the October
    # delivery's is two digits).
    offset, planned, written, already = 0, [], 0, 0
    while True:
        page = await list_candidates(db, intake_id, limit=MAX_PAGE_SIZE, offset=offset)
        for item in page["items"]:
            has_issue = bool(item["ledger_issue_code"])
            planned.append({
                "entity_id": item["entity_id"],
                "entity_name": item["entity_name"],
                "npi": item["npi"],
                "line_number": item["line_number"],
                "evidence_row_id": item["evidence_row_id"],
                "already_on_worklist": has_issue,
            })
            if has_issue:
                already += 1
                continue
            if not dry_run:
                import uuid as _uuid
                issue = await vf.record_sam_extract_candidate(
                    db, entity_id=_uuid.UUID(item["entity_id"]), npi=item["npi"],
                    org_name=item["entity_name"],
                    extract={**(item["provenance"] or {})})
                if issue is not None:
                    planned[-1]["issue_code"] = issue.issue_code
                    written += 1
        if len(page["items"]) < MAX_PAGE_SIZE:
            break
        offset += MAX_PAGE_SIZE
    if not dry_run:
        await db.commit()
        logger.info("SAM-extract candidate backfill wrote %s finding(s) for "
                    "intake %s (actor %s)", written, intake_id, actor)
    return {
        "intake_id": str(intake_id),
        "dry_run": bool(dry_run),
        "candidates": len(planned),
        "already_on_worklist": already,
        "written": written if not dry_run else 0,
        "would_write": (len(planned) - already) if dry_run else None,
        "items": planned,
        "write_scope": ("One rce_issues row (EXC-001 / SAM_EXTRACT_CANDIDATE, "
                        "HIGH, HUMAN_REQUIRED, resolution OPEN) per candidate "
                        "not already carrying an undecided finding. Nothing "
                        "else is written: no evidence row, no disposition, no "
                        "bucket, no entity status, no adjudication."),
        "note": _TRUTH_NOTE,
    }
