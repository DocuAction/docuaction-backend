"""Per-source verification coverage for one delivery, counted from evidence rows.

CONNECTOR READINESS IS NOT COVERAGE
-----------------------------------
A green connector badge says a source ANSWERED A PROBE. It says nothing about
whether the entities of THIS delivery were looked up in it. Coverage counts
evidence rows for the delivery's promoted entities, per source, and hands the
counts to `status_model.coverage_state`, which is the only place a coverage
word is chosen.

WHERE THE EVIDENCE IS READ FROM (the mapping this module commits to)
--------------------------------------------------------------------
    eligible      distinct `rce_curated_records.canonical_entity_id` of the
                  intake (the promoted population, reconciliation's E)
    attempted     distinct eligible entities with at least one row for the
                  source in EITHER evidence table below
    per outcome   distinct eligible entities whose rows for the source say:

        tefca_verifications.verification_status
            verified | match | matched            -> verified
            not_found | no_match                  -> not_found
            unavailable | source_unavailable      -> unavailable
            failed | error                        -> failed
            deactivated (or detail ~ 'deactivat') -> deactivated
        tefca_dimension_evidence.disposition
            PASS | CORROBORATED                   -> verified
            NOT_FOUND                             -> not_found
            UNAVAILABLE                           -> unavailable
            FAIL | CONFLICT                       -> failed

    `review_records.verification_results` is NOT read: it is a snapshot for
    the review, not a lookup log, and counting it would count the same lookup
    twice. Both evidence tables are append-only; an entity looked up twice
    counts once per outcome (distinct entity), never twice.

Source names are matched case-insensitively on the labels the connectors use
(`nppes`, `pecos`, `leie` / `oig_leie`, `sam` / `sam_gov`).

`configured` is taken from connector configuration without probing: NPPES,
LEIE and PECOS are keyless public sources and are always configured; SAM needs
`SAM_GOV_API_KEY`. The probe result itself is on `GET /api/admin/health`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import text

from app.tefca_registry.rce import status_model

logger = logging.getLogger(__name__)

#: source key -> lower-cased spellings seen in evidence rows
SOURCES: Dict[str, tuple] = {
    "nppes": ("nppes", "npi_registry", "cms_nppes"),
    "pecos": ("pecos",),
    "leie": ("leie", "oig_leie", "oig", "oig-leie"),
    "sam": ("sam", "sam_gov", "sam.gov", "samgov"),
}

_VERIFICATION_STATUS = {
    "verified": "verified", "match": "verified", "matched": "verified",
    "not_found": "not_found", "no_match": "not_found",
    "unavailable": "unavailable", "source_unavailable": "unavailable",
    "failed": "failed", "error": "failed",
    "deactivated": "deactivated",
}
_DIMENSION_DISPOSITION = {
    "PASS": "verified", "CORROBORATED": "verified",
    "NOT_FOUND": "not_found",
    "UNAVAILABLE": "unavailable",
    "FAIL": "failed", "CONFLICT": "failed",
}
OUTCOMES = ("verified", "not_found", "deactivated", "failed", "unavailable")

_ELIGIBLE_SQL = ("SELECT DISTINCT canonical_entity_id FROM rce_curated_records "
                 "WHERE source_intake_id = CAST(:i AS uuid) "
                 "AND canonical_entity_id IS NOT NULL")


def _source_key(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    for key, spellings in SOURCES.items():
        if value in spellings:
            return key
    return None


def configured_sources() -> Dict[str, bool]:
    """Which sources this deployment can consult, from configuration only."""
    sam_key = ""
    try:
        from app.Tefca.connectors import SAMGovConnector
        sam_key = getattr(SAMGovConnector(), "api_key", "") or ""
    except Exception:  # noqa: BLE001 - fall back to the environment
        sam_key = os.environ.get("SAM_GOV_API_KEY", "")
    return {"nppes": True, "pecos": True, "leie": True, "sam": bool(sam_key)}


async def _eligible_count(db, intake_id) -> int:
    return int((await db.execute(
        text(f"SELECT count(*) FROM ({_ELIGIBLE_SQL}) e"), {"i": str(intake_id)})).scalar() or 0)


async def _verification_rows(db, intake_id) -> Iterable:
    """(source, status, detail, entity_id) per tefca_verifications row of the population."""
    return (await db.execute(text(f"""
        SELECT v.source, v.verification_status, v.detail, v.entity_id
        FROM tefca_verifications v
        WHERE v.entity_id IN ({_ELIGIBLE_SQL})"""), {"i": str(intake_id)})).all()


async def _dimension_rows(db, intake_id) -> Iterable:
    """(source, disposition, entity_id) per tefca_dimension_evidence row.

    `entity_id` is TEXT on that table, so the population is cast to text.
    """
    return (await db.execute(text("""
        SELECT d.source, d.disposition, d.entity_id
        FROM tefca_dimension_evidence d
        WHERE d.entity_id IN (SELECT CAST(canonical_entity_id AS TEXT)
                              FROM rce_curated_records
                              WHERE source_intake_id = CAST(:i AS uuid)
                                AND canonical_entity_id IS NOT NULL)"""),
        {"i": str(intake_id)})).all()


def _tally(verification_rows, dimension_rows) -> Dict[str, Dict[str, set]]:
    """Per source: outcome -> set of entity ids, plus 'attempted'."""
    tally: Dict[str, Dict[str, set]] = {
        key: {"attempted": set(), **{o: set() for o in OUTCOMES}} for key in SOURCES}
    for source, status, detail, entity_id in verification_rows:
        key = _source_key(source)
        if key is None:
            continue
        eid = str(entity_id)
        tally[key]["attempted"].add(eid)
        outcome = _VERIFICATION_STATUS.get((status or "").strip().lower())
        if "deactivat" in (detail or "").lower():
            outcome = "deactivated"
        if outcome:
            tally[key][outcome].add(eid)
    for source, disposition, entity_id in dimension_rows:
        key = _source_key(source)
        if key is None:
            continue
        eid = str(entity_id)
        tally[key]["attempted"].add(eid)
        outcome = _DIMENSION_DISPOSITION.get((disposition or "").strip().upper())
        if outcome:
            tally[key][outcome].add(eid)
    return tally


def overall_state(sources: Dict[str, Dict[str, Any]]) -> str:
    """One word for the delivery, from the per-source states."""
    states = [s["state"] for s in sources.values()]
    configured = [s for s in states if s != status_model.COVERAGE_NOT_CONFIGURED]
    if not configured:
        return status_model.COVERAGE_NOT_CONFIGURED
    if any(s == status_model.COVERAGE_IN_PROGRESS for s in configured):
        return status_model.COVERAGE_IN_PROGRESS
    if all(s == status_model.COVERAGE_NOT_RUN for s in configured):
        return status_model.COVERAGE_NOT_RUN
    if all(s == status_model.COVERAGE_COMPLETE for s in configured):
        return status_model.COVERAGE_COMPLETE
    if all(s in (status_model.COVERAGE_UNAVAILABLE, status_model.COVERAGE_NOT_RUN)
           for s in configured):
        return status_model.COVERAGE_UNAVAILABLE
    return status_model.COVERAGE_PARTIAL


async def coverage_for_intake(db, intake_id, *, job=None) -> Dict[str, Any]:
    """Coverage of one delivery, per source and overall. Reads only."""
    eligible = await _eligible_count(db, intake_id)
    tally = _tally(await _verification_rows(db, intake_id),
                   await _dimension_rows(db, intake_id))
    configured = configured_sources()
    in_progress = bool(job is not None and getattr(job, "state", None) == "RUNNING"
                       and getattr(job, "stage", None) == "VERIFICATION")
    sources = {}
    for key in SOURCES:
        counts = tally[key]
        sources[key] = status_model.coverage_state(
            configured=configured[key], eligible=eligible,
            attempted=len(counts["attempted"]),
            verified=len(counts["verified"]), not_found=len(counts["not_found"]),
            deactivated=len(counts["deactivated"]), failed=len(counts["failed"]),
            unavailable=len(counts["unavailable"]), in_progress=in_progress)
    provenance = None
    try:
        from app.Tefca.connectors import data_source_labels
        provenance = data_source_labels()
    except Exception as exc:  # noqa: BLE001
        logger.info("data source labels unavailable: %s", type(exc).__name__)
    return {
        "intake_id": str(intake_id),
        "job_id": str(job.id) if job is not None else None,
        "state": overall_state(sources),
        "eligible": eligible,
        "sources": sources,
        "provenance": provenance,
        "evidence_tables": ["tefca_verifications", "tefca_dimension_evidence"],
        "note": ("Counts are distinct promoted entities of this delivery with an "
                 "evidence row per source. Connector readiness is reported on "
                 "/api/admin/health and is not coverage."),
    }


def empty_coverage(*, reason: str) -> Dict[str, Any]:
    """The shape when there is no intake yet (a job that failed before Area 1)."""
    configured = configured_sources()
    sources = {key: status_model.coverage_state(configured=configured[key], eligible=0,
                                                attempted=0) for key in SOURCES}
    return {"intake_id": None, "job_id": None, "state": overall_state(sources),
            "eligible": 0, "sources": sources, "provenance": None,
            "evidence_tables": ["tefca_verifications", "tefca_dimension_evidence"],
            "note": reason}
