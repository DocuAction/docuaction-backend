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
            NOT_FOUND | REVIEW                    -> not_found
            UNAVAILABLE                           -> unavailable
            FAIL | CONFLICT                       -> failed

    REVIEW (2026-10-02, SAM verification-contract review): a potential
    exclusion/debarment match pending analyst confirmation -- e.g. an
    ambiguous SAM/LEIE name match, or (since that same pass's connector fix)
    a CONFIRMED exclusion correctly surfaced at the evidence layer, which is
    assembled as REVIEW rather than FAIL because an automated system is
    never the one to pronounce a debarment final (see
    `evidence_assembly._dimension_exclusion`'s own docstring). Before this
    mapping existed, REVIEW was simply absent from every bucket here --
    `eligible`/`attempted` counted the entity, but no outcome did, so a
    pending exclusion silently vanished from the coverage dashboard instead
    of being miscounted. Mapped to `not_found` deliberately, not `failed`:
    it is the SAME choice `arc_pipeline._DISPOSITION_TO_STATE` already makes
    for the bucket classifier (consistent across both consumers of the same
    underlying disposition), and it does NOT touch the `failed` bucket at
    all -- confirmed independent of the still-open "1,298 Failed indicator"
    reconciliation question (qa-evidence/2026-10-01-reporting-architecture/
    CHECKPOINT-1298-FAILED-INDICATOR-STATIC-ANALYSIS.md), which is about
    cross-source `failed` semantics specifically.

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
    "NOT_FOUND": "not_found", "REVIEW": "not_found",
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


#: The source-spelling → key mapping (SOURCES) and the status/disposition →
#: outcome maps, expressed as SQL so the tally is COUNTED in Postgres instead of
#: pulling every evidence row to Python. AP-002: the detail endpoint pulled the
#: whole population's dimension-evidence (122,945 rows at September scale) plus
#: verification rows across the wire and built Python sets. The SQL is optimal
#: (a full-population hash join), so no index helps; the cost was the row
#: transfer + set-building. This aggregation returns at most one row per source.
_SOURCE_CASE = """
        CASE lower(btrim({col}))
          WHEN 'nppes' THEN 'nppes' WHEN 'npi_registry' THEN 'nppes' WHEN 'cms_nppes' THEN 'nppes'
          WHEN 'pecos' THEN 'pecos'
          WHEN 'leie' THEN 'leie' WHEN 'oig_leie' THEN 'leie' WHEN 'oig' THEN 'leie' WHEN 'oig-leie' THEN 'leie'
          WHEN 'sam' THEN 'sam' WHEN 'sam_gov' THEN 'sam' WHEN 'sam.gov' THEN 'sam' WHEN 'samgov' THEN 'sam'
        END"""

#: The per-evidence-row (source, outcome) computation, shared verbatim with
#: `app.reports.data.verification_drilldown` (the paginated per-entity list
#: behind each coverage card's clickable totals): the drill-down's row count
#: for (source, outcome) must equal this module's own aggregate count for the
#: same (source, outcome) EXACTLY, and the only way to guarantee that is one
#: SQL fragment neither module re-derives independently. Requires `pop_uuid`
#: (distinct `canonical_entity_id` of the population) and `pop_text` (the same,
#: cast to text, for `tefca_dimension_evidence.entity_id`) as CTEs already
#: defined by the caller.
EVIDENCE_ROWS_CTE_SQL = f"""
    rows AS (
        SELECT {_SOURCE_CASE.format(col='v.source')} AS key,
               v.entity_id::text AS eid,
               CASE WHEN lower(coalesce(v.detail, '')) LIKE '%deactivat%' THEN 'deactivated'
                    ELSE (CASE lower(btrim(coalesce(v.verification_status, '')))
                       WHEN 'verified' THEN 'verified' WHEN 'match' THEN 'verified' WHEN 'matched' THEN 'verified'
                       WHEN 'not_found' THEN 'not_found' WHEN 'no_match' THEN 'not_found'
                       WHEN 'unavailable' THEN 'unavailable' WHEN 'source_unavailable' THEN 'unavailable'
                       WHEN 'failed' THEN 'failed' WHEN 'error' THEN 'failed'
                       WHEN 'deactivated' THEN 'deactivated' ELSE NULL END) END AS outcome
        FROM tefca_verifications v
        WHERE v.entity_id IN (SELECT eid FROM pop_uuid)
        UNION ALL
        SELECT {_SOURCE_CASE.format(col='d.source')} AS key,
               d.entity_id AS eid,
               CASE upper(btrim(coalesce(d.disposition, '')))
                    WHEN 'PASS' THEN 'verified' WHEN 'CORROBORATED' THEN 'verified'
                    WHEN 'NOT_FOUND' THEN 'not_found' WHEN 'REVIEW' THEN 'not_found'
                    WHEN 'UNAVAILABLE' THEN 'unavailable'
                    WHEN 'FAIL' THEN 'failed' WHEN 'CONFLICT' THEN 'failed' ELSE NULL END AS outcome
        FROM tefca_dimension_evidence d
        WHERE d.entity_id IN (SELECT eid FROM pop_text))
"""

_COVERAGE_COUNTS_SQL = f"""
    WITH pop_uuid AS (
        SELECT DISTINCT canonical_entity_id AS eid
        FROM rce_curated_records
        WHERE source_intake_id = CAST(:i AS uuid) AND canonical_entity_id IS NOT NULL),
    pop_text AS (SELECT CAST(eid AS text) AS eid FROM pop_uuid),
    {EVIDENCE_ROWS_CTE_SQL}
    SELECT key,
           count(DISTINCT eid) AS attempted,
           count(DISTINCT eid) FILTER (WHERE outcome = 'verified') AS verified,
           count(DISTINCT eid) FILTER (WHERE outcome = 'not_found') AS not_found,
           count(DISTINCT eid) FILTER (WHERE outcome = 'deactivated') AS deactivated,
           count(DISTINCT eid) FILTER (WHERE outcome = 'failed') AS failed,
           count(DISTINCT eid) FILTER (WHERE outcome = 'unavailable') AS unavailable
    FROM rows WHERE key IS NOT NULL GROUP BY key
"""


async def coverage_counts(db, intake_id) -> Dict[str, Dict[str, int]]:
    """Per-source distinct-entity outcome COUNTS, computed in Postgres.

    Oracle-equal to `_tally` reduced to `len()` of each set, but it never pulls
    the evidence rows to Python. Sources with no evidence get all-zero counts,
    so callers can index every key unconditionally.
    """
    base = {o: 0 for o in ("attempted", *OUTCOMES)}
    out: Dict[str, Dict[str, int]] = {key: dict(base) for key in SOURCES}
    rows = (await db.execute(text(_COVERAGE_COUNTS_SQL), {"i": str(intake_id)})).all()
    for key, attempted, verified, not_found, deactivated, failed, unavailable in rows:
        if key not in out:
            continue
        out[key] = {"attempted": int(attempted or 0), "verified": int(verified or 0),
                    "not_found": int(not_found or 0), "deactivated": int(deactivated or 0),
                    "failed": int(failed or 0), "unavailable": int(unavailable or 0)}
    return out


# --------------------------------------------------------------------------
# SAM.gov screening-leg breakdown (WP7-A). READ-TIME LABELLING ONLY.
#
# The headline SAM counts above are unchanged: they say how many entities have
# a SAM row and what the row's disposition was. They cannot say WHICH question
# SAM answered. Two different questions share the SAM source:
#   (1) registration-only -- the v3 entity-registration search (the NPI-less
#       `sam_name` branch). A miss there means "no registration record found";
#       it says nothing about exclusion.
#   (2) exclusion screening -- the independent exclusion leg: clear / hit /
#       unavailable.
# Rows persisted before `normalized_values.screening_leg` existed carry no label.
# They are reported as "screening leg not recorded": never as exclusion-clear,
# never relabelled as registration. Stored rows are not rewritten; the label is
# derived when the figures are read. Bucket/rule classification is not touched.
# --------------------------------------------------------------------------
SAM_LEG_NOT_RECORDED_LABEL = "screening leg not recorded"
SAM_LEG_KEYS = ("registration_only", "exclusion", "leg_not_recorded")
_SAM_REGISTRATION_LEGS = ("registration_only",)
_SAM_EXCLUSION_LEGS = ("exclusion", "exclusion_and_registration")


def sam_leg_class(leg: Optional[str]) -> str:
    """Which question a SAM evidence row answered, from its recorded leg label.

    Returns `registration_only`, `exclusion` or `leg_not_recorded`. An absent,
    empty or unrecognised label is `leg_not_recorded` (honest unknown)."""
    value = (leg or "").strip().lower() if isinstance(leg, str) else ""
    if value in _SAM_REGISTRATION_LEGS:
        return "registration_only"
    if value in _SAM_EXCLUSION_LEGS:
        return "exclusion"
    return "leg_not_recorded"


def sam_leg_outcome(leg_class: str, disposition: Optional[str]) -> str:
    """Outcome word for one SAM row within its leg class.

    exclusion: clear (PASS/CORROBORATED/NOT_FOUND) | hit (REVIEW/FAIL/CONFLICT)
    | unavailable. registration_only: no_registration_record_found
    (NOT_FOUND) | registration_record_found (PASS/CORROBORATED) | other |
    unavailable. A registration-only NOT_FOUND is never `clear`. leg_not_recorded
    rows are not interpreted (`not_interpreted`) beyond unavailable."""
    d = (disposition or "").strip().upper()
    if d == "UNAVAILABLE":
        return "unavailable"
    if leg_class == "exclusion":
        if d in ("PASS", "CORROBORATED", "NOT_FOUND"):
            return "clear"
        if d in ("REVIEW", "FAIL", "CONFLICT"):
            return "hit"
        return "other"
    if leg_class == "registration_only":
        if d == "NOT_FOUND":
            return "no_registration_record_found"
        if d in ("PASS", "CORROBORATED"):
            return "registration_record_found"
        return "other"
    return "not_interpreted"


#: SQL twin of sam_leg_class / sam_leg_outcome (tested for parity). SAM rows
#: only, both evidence tables; `tefca_verifications` has no leg label so its SAM
#: rows are `leg_not_recorded` by construction. The outcome is computed per row
#: BEFORE counting so an entity with two rows that map to one outcome counts once.
_SAM_LEG_ROWS_CTE = f"""
    WITH pop_uuid AS (
        SELECT DISTINCT canonical_entity_id AS eid
        FROM rce_curated_records
        WHERE source_intake_id = CAST(:i AS uuid) AND canonical_entity_id IS NOT NULL),
    pop_text AS (SELECT CAST(eid AS text) AS eid FROM pop_uuid),
    leg_rows AS (
        SELECT d.entity_id AS eid,
               CASE WHEN lower(btrim(coalesce(d.normalized_values->>'screening_leg', '')))
                         = 'registration_only' THEN 'registration_only'
                    WHEN lower(btrim(coalesce(d.normalized_values->>'screening_leg', '')))
                         IN ('exclusion', 'exclusion_and_registration') THEN 'exclusion'
                    ELSE 'leg_not_recorded' END AS leg,
               upper(btrim(coalesce(d.disposition, ''))) AS disp
        FROM tefca_dimension_evidence d
        WHERE d.entity_id IN (SELECT eid FROM pop_text)
          AND {_SOURCE_CASE.format(col='d.source')} = 'sam'
        UNION ALL
        SELECT v.entity_id::text AS eid, 'leg_not_recorded' AS leg,
               CASE lower(btrim(coalesce(v.verification_status, '')))
                    WHEN 'unavailable' THEN 'UNAVAILABLE'
                    WHEN 'source_unavailable' THEN 'UNAVAILABLE'
                    ELSE 'OTHER' END AS disp
        FROM tefca_verifications v
        WHERE v.entity_id IN (SELECT eid FROM pop_uuid)
          AND {_SOURCE_CASE.format(col='v.source')} = 'sam'),
    sam_rows AS (
        SELECT eid, leg,
               CASE WHEN disp = 'UNAVAILABLE' THEN 'unavailable'
                    WHEN leg = 'exclusion' AND disp IN ('PASS', 'CORROBORATED', 'NOT_FOUND') THEN 'clear'
                    WHEN leg = 'exclusion' AND disp IN ('REVIEW', 'FAIL', 'CONFLICT') THEN 'hit'
                    WHEN leg = 'registration_only' AND disp = 'NOT_FOUND' THEN 'no_registration_record_found'
                    WHEN leg = 'registration_only' AND disp IN ('PASS', 'CORROBORATED')
                         THEN 'registration_record_found'
                    WHEN leg = 'leg_not_recorded' THEN 'not_interpreted'
                    ELSE 'other' END AS outcome
        FROM leg_rows)
"""
_SAM_LEG_COUNTS_SQL = _SAM_LEG_ROWS_CTE + (
    "SELECT leg, outcome, count(DISTINCT eid) AS entities FROM sam_rows GROUP BY leg, outcome")
_SAM_LEG_ENTITIES_SQL = _SAM_LEG_ROWS_CTE + (
    "SELECT leg, count(DISTINCT eid) AS entities FROM sam_rows GROUP BY leg")


def _empty_sam_legs() -> Dict[str, Any]:
    return {
        "registration_only": {
            "entities": 0, "no_registration_record_found": 0,
            "registration_record_found": 0, "unavailable": 0, "other": 0},
        "exclusion": {"entities": 0, "clear": 0, "hit": 0, "unavailable": 0, "other": 0},
        "leg_not_recorded": {"entities": 0, "unavailable": 0, "not_interpreted": 0,
                             "label": SAM_LEG_NOT_RECORDED_LABEL},
    }


async def sam_screening_legs(db, intake_id) -> Dict[str, Any]:
    """Distinct-entity SAM counts split by which question each row answered.

    An entity with rows of more than one kind is counted in each kind it has
    (and once per outcome within a kind), so the kinds are NOT additive and
    must not be summed into a single "screened" figure. Reads only."""
    out = _empty_sam_legs()
    for leg, outcome, n in (await db.execute(text(_SAM_LEG_COUNTS_SQL), {"i": str(intake_id)})).all():
        out[leg][outcome] = int(n or 0)
    for leg, n in (await db.execute(text(_SAM_LEG_ENTITIES_SQL), {"i": str(intake_id)})).all():
        out[leg]["entities"] = int(n or 0)
    return out


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


# --------------------------------------------------------------------------
# Per-source STATES (2026-10-13). ADDITIVE, READ-TIME ONLY.
#
# The headline counts above answer "how many entities have a row for the source
# and what did the row's disposition map to". They overlap (an entity can be
# `verified` by one row and `failed` by another, so the outcome counts do NOT
# sum to `attempted`) and they cannot say whether a source ANSWERED, or whether
# an attempt that came back "unavailable" was ever an answer. `source_states`
# restates the same evidence as separate, named states per source:
#
#   eligible          promoted entities of this delivery (reconciliation's E);
#                     records that were held / rejected / missing-key / excluded
#                     are NOT promoted and are therefore outside this
#                     denominator -- they are not "unattempted", they were never
#                     eligible for automated lookup
#   attempted         eligible entities with >=1 evidence row for the source
#   answered          attempted entities with >=1 row in which the source gave
#                     an answer: found, not found, potential match or
#                     deactivated. A row that says unavailable / failed /
#                     not-applicable is NOT an answer
#   unavailable       entities with >=1 row recorded as source-unavailable
#   failed            entities with >=1 row recorded as failed/error/conflict
#   potential_match   entities with >=1 REVIEW row: an ambiguous or possible
#                     match awaiting an analyst. NOT confirmed
#   confirmed_result  entities with >=1 DEFINITIVE row: found (PASS /
#                     CORROBORATED / verified) or definitively not found
#                     (NOT_FOUND), or deactivated. A potential match is excluded
#   not_applicable    entities with >=1 row whose disposition is NOT_APPLICABLE
#   not_attempted     eligible - attempted (no evidence row at all)
#   attempted_not_answered
#                     attempted entities with NO answering row and no
#                     not-applicable row (every row unavailable/failed/other)
#
# Entities are counted once per state; states overlap and must not be summed.
#
# AGGREGATION RULE FOR "CMS ENROLLMENT" (documented, enforced by the keys):
#   * the legacy `pecos` key is an NPPES-backed PROXY (connectors.PECOS_BACKING).
#     It keeps its own entry, labelled as the NPPES proxy, and is never added to
#     or reported as genuine CMS enrollment evidence;
#   * genuine CMS enrollment evidence is ONLY rows whose source is
#     CMS_PPEF_ENROLLMENT (`cms_enrollment` below); CMS_REVOCATION is a
#     separate control (`cms_revocation`). Neither is folded into `pecos`,
#     `nppes`, or each other;
#   * `independent_cms_enrollment_entities` is the count for `cms_enrollment`
#     alone. The proxy never raises it.
# SAM: `attempted`/`answered` for SAM say a lookup was made / answered. They are
# NOT exclusion clearance -- see `screening_legs` for which question each row
# answered, and "screening leg not recorded" for historical rows.
# --------------------------------------------------------------------------
STATE_SOURCE_KEYS = ("nppes", "pecos", "leie", "sam", "cms_enrollment", "cms_revocation")

#: Truthful display names. `pecos` is the legacy proxy, never "PECOS (CMS)".
SOURCE_DISPLAY_NAMES: Dict[str, str] = {
    "nppes": "NPPES NPI Registry (CMS)",
    "pecos": "NPPES Registry — Legacy PECOS Proxy (not CMS enrollment)",
    "leie": "OIG LEIE (HHS OIG)",
    "sam": "SAM.gov (GSA) — lookup attempts; not exclusion clearance",
    "cms_enrollment": "CMS Public Provider Enrollment (PPEF, PECOS-derived)",
    "cms_revocation": "CMS Revoked Medicare Providers and Suppliers",
}

AGGREGATION_RULE = (
    "Counts are distinct promoted entities of this delivery, once per source per state; "
    "states overlap and are not additive. Records that were held, rejected, missing a key "
    "or excluded are not promoted and are outside the eligible denominator. 'Attempted' "
    "means an evidence row exists; 'answered' means the source returned found / not found "
    "/ potential match / deactivated; unavailable, failed and not-applicable rows are not "
    "answers. A potential match is not a confirmed result. The legacy 'pecos' key is an "
    "NPPES-backed proxy and is reported separately; it is never counted as CMS enrollment. "
    "Genuine CMS enrollment evidence is only CMS_PPEF_ENROLLMENT rows ('cms_enrollment'). "
    "SAM attempts are not exclusion clearance; rows stored before the screening leg was "
    "recorded are 'screening leg not recorded'.")

#: evidence row -> state word. Python twin of `_STATE_ROWS_SQL` (tested for parity).
_VERIFICATION_STATE = {
    "verified": "found", "match": "found", "matched": "found",
    "not_found": "not_found", "no_match": "not_found",
    "unavailable": "unavailable", "source_unavailable": "unavailable",
    "failed": "failed", "error": "failed",
    "deactivated": "deactivated",
}
_DIMENSION_STATE = {
    "PASS": "found", "CORROBORATED": "found",
    "NOT_FOUND": "not_found", "REVIEW": "potential_match",
    "UNAVAILABLE": "unavailable", "FAIL": "failed", "CONFLICT": "failed",
    "NOT_APPLICABLE": "not_applicable",
}

_STATE_ANSWERING = ("found", "not_found", "potential_match", "deactivated")
_STATE_CONFIRMED = ("found", "not_found", "deactivated")


def evidence_state(table: str, value: Optional[str], detail: Optional[str] = None) -> str:
    """The state word for one evidence row. `table` is 'verification' or
    'dimension'. Unmapped values are `other` (attempted, not an answer)."""
    if table == "verification":
        if "deactivat" in (detail or "").lower():
            return "deactivated"
        return _VERIFICATION_STATE.get((value or "").strip().lower(), "other")
    return _DIMENSION_STATE.get((value or "").strip().upper(), "other")


_STATE_SOURCE_CASE = """
        CASE lower(btrim({col}))
          WHEN 'nppes' THEN 'nppes' WHEN 'npi_registry' THEN 'nppes' WHEN 'cms_nppes' THEN 'nppes'
          WHEN 'pecos' THEN 'pecos'
          WHEN 'leie' THEN 'leie' WHEN 'oig_leie' THEN 'leie' WHEN 'oig' THEN 'leie' WHEN 'oig-leie' THEN 'leie'
          WHEN 'sam' THEN 'sam' WHEN 'sam_gov' THEN 'sam' WHEN 'sam.gov' THEN 'sam' WHEN 'samgov' THEN 'sam'
          WHEN 'cms_ppef_enrollment' THEN 'cms_enrollment'
          WHEN 'cms_revocation' THEN 'cms_revocation'
        END"""

_STATE_ROWS_SQL = f"""
    WITH pop_uuid AS (
        SELECT DISTINCT canonical_entity_id AS eid
        FROM rce_curated_records
        WHERE source_intake_id = CAST(:i AS uuid) AND canonical_entity_id IS NOT NULL),
    pop_text AS (SELECT CAST(eid AS text) AS eid FROM pop_uuid),
    state_rows AS (
        SELECT {_STATE_SOURCE_CASE.format(col='v.source')} AS key, v.entity_id::text AS eid,
               CASE WHEN lower(coalesce(v.detail, '')) LIKE '%deactivat%' THEN 'deactivated'
                    ELSE (CASE lower(btrim(coalesce(v.verification_status, '')))
                       WHEN 'verified' THEN 'found' WHEN 'match' THEN 'found' WHEN 'matched' THEN 'found'
                       WHEN 'not_found' THEN 'not_found' WHEN 'no_match' THEN 'not_found'
                       WHEN 'unavailable' THEN 'unavailable' WHEN 'source_unavailable' THEN 'unavailable'
                       WHEN 'failed' THEN 'failed' WHEN 'error' THEN 'failed'
                       WHEN 'deactivated' THEN 'deactivated' ELSE 'other' END) END AS state
        FROM tefca_verifications v
        WHERE v.entity_id IN (SELECT eid FROM pop_uuid)
        UNION ALL
        SELECT {_STATE_SOURCE_CASE.format(col='d.source')} AS key, d.entity_id AS eid,
               CASE upper(btrim(coalesce(d.disposition, '')))
                    WHEN 'PASS' THEN 'found' WHEN 'CORROBORATED' THEN 'found'
                    WHEN 'NOT_FOUND' THEN 'not_found' WHEN 'REVIEW' THEN 'potential_match'
                    WHEN 'UNAVAILABLE' THEN 'unavailable'
                    WHEN 'FAIL' THEN 'failed' WHEN 'CONFLICT' THEN 'failed'
                    WHEN 'NOT_APPLICABLE' THEN 'not_applicable' ELSE 'other' END AS state
        FROM tefca_dimension_evidence d
        WHERE d.entity_id IN (SELECT eid FROM pop_text)),
    per_entity AS (
        SELECT key, eid,
               bool_or(state IN ('found','not_found','potential_match','deactivated')) AS answered,
               bool_or(state = 'unavailable') AS unavailable,
               bool_or(state = 'failed') AS failed,
               bool_or(state = 'potential_match') AS potential_match,
               bool_or(state IN ('found','not_found','deactivated')) AS confirmed,
               bool_or(state = 'not_applicable') AS not_applicable
        FROM state_rows WHERE key IS NOT NULL GROUP BY key, eid)
    SELECT key, count(*) AS attempted,
           count(*) FILTER (WHERE answered) AS answered,
           count(*) FILTER (WHERE unavailable) AS unavailable,
           count(*) FILTER (WHERE failed) AS failed,
           count(*) FILTER (WHERE potential_match) AS potential_match,
           count(*) FILTER (WHERE confirmed) AS confirmed_result,
           count(*) FILTER (WHERE not_applicable) AS not_applicable,
           count(*) FILTER (WHERE NOT answered AND NOT not_applicable) AS attempted_not_answered
    FROM per_entity GROUP BY key
"""

_STATE_FIELDS = ("attempted", "answered", "unavailable", "failed", "potential_match",
                 "confirmed_result", "not_applicable", "attempted_not_answered")


async def source_states(db, intake_id, *, eligible: int) -> Dict[str, Any]:
    """The per-source state breakdown (see the block comment above).

    Reads only; one aggregate query returning at most one row per source."""
    out: Dict[str, Dict[str, Any]] = {}
    for key in STATE_SOURCE_KEYS:
        out[key] = {"display_name": SOURCE_DISPLAY_NAMES[key], "eligible": eligible,
                    **{f: 0 for f in _STATE_FIELDS}}
    for row in (await db.execute(text(_STATE_ROWS_SQL), {"i": str(intake_id)})).all():
        key = row[0]
        if key not in out:
            continue
        for field, value in zip(_STATE_FIELDS, row[1:]):
            out[key][field] = int(value or 0)
    for block in out.values():
        block["not_attempted"] = max(0, eligible - block["attempted"])
    out["pecos"]["backing"] = "nppes_proxy"
    out["pecos"]["is_independent_cms_enrollment"] = False
    out["sam"]["is_exclusion_clearance"] = False
    return {
        "eligible": eligible,
        "sources": out,
        "independent_cms_enrollment_entities": out["cms_enrollment"]["attempted"],
        "aggregation_rule": AGGREGATION_RULE,
        "overlap_note": ("States overlap: an entity can be answered by one row and "
                         "unavailable on another, so states do not sum to 'attempted'."),
    }


_NOT_ATTEMPTED_SQL = """
    WITH pop_uuid AS (
        SELECT DISTINCT canonical_entity_id AS eid
        FROM rce_curated_records
        WHERE source_intake_id = CAST(:i AS uuid) AND canonical_entity_id IS NOT NULL),
    attempted AS (
        SELECT v.entity_id::text AS eid FROM tefca_verifications v
        WHERE v.entity_id IN (SELECT eid FROM pop_uuid)
          AND {v_key} = :k
        UNION
        SELECT d.entity_id AS eid FROM tefca_dimension_evidence d
        WHERE d.entity_id IN (SELECT CAST(eid AS text) FROM pop_uuid)
          AND {d_key} = :k)
    SELECT rec.canonical_entity_id::text AS entity_id, src.line_number,
           src.source_rce_id, rec.name AS entity_name, rec.npi, rec.source_record_id::text
    FROM pop_uuid p
    JOIN rce_curated_records rec ON rec.canonical_entity_id = p.eid
         AND rec.source_intake_id = CAST(:i AS uuid)
    JOIN rce_source_records src ON src.id = rec.source_record_id
    WHERE CAST(p.eid AS text) NOT IN (SELECT eid FROM attempted)
    ORDER BY src.line_number
    LIMIT :n
"""


async def list_not_attempted(db, intake_id, source: str, *, limit: int = 200) -> Dict[str, Any]:
    """The promoted entities with NO evidence row for `source` (bounded list).

    This is the `not_attempted` state: it identifies WHO was not looked up. It
    says nothing about why; held records are not in this population at all."""
    if source not in STATE_SOURCE_KEYS:
        raise ValueError(f"unknown source {source!r}; expected one of {STATE_SOURCE_KEYS}")
    sql = _NOT_ATTEMPTED_SQL.format(
        v_key=_STATE_SOURCE_CASE.format(col="v.source"),
        d_key=_STATE_SOURCE_CASE.format(col="d.source"))
    rows = (await db.execute(text(sql), {"i": str(intake_id), "k": source,
                                         "n": max(1, min(int(limit), 200))})).all()
    return {"source": source, "outcome": "not_attempted",
            "display_name": SOURCE_DISPLAY_NAMES[source],
            "items": [{"entity_id": r[0], "line_number": r[1], "source_rce_id": r[2],
                       "entity_name": r[3], "npi": r[4], "source_record_id": r[5]}
                      for r in rows],
            "count": len(rows)}


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
    # AP-002: count in Postgres instead of pulling the whole population's
    # evidence rows to Python. Oracle-equal to the old _tally (see
    # test_coverage_sql_matches_python_oracle).
    tally = await coverage_counts(db, intake_id)
    configured = configured_sources()
    in_progress = bool(job is not None and getattr(job, "state", None) == "RUNNING"
                       and getattr(job, "stage", None) == "VERIFICATION")
    sources = {}
    for key in SOURCES:
        counts = tally[key]
        sources[key] = status_model.coverage_state(
            configured=configured[key], eligible=eligible,
            attempted=counts["attempted"],
            verified=counts["verified"], not_found=counts["not_found"],
            deactivated=counts["deactivated"], failed=counts["failed"],
            unavailable=counts["unavailable"], in_progress=in_progress)
    stamp_pecos_backing(sources)
    # Additive: which SAM question each row answered (registration vs exclusion
    # vs leg not recorded). The headline SAM counts above are not changed.
    if isinstance(sources.get("sam"), dict):
        sources["sam"]["screening_legs"] = await sam_screening_legs(db, intake_id)
    states = await source_states(db, intake_id, eligible=eligible)
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
        "source_states": states,
        "provenance": provenance,
        "evidence_tables": ["tefca_verifications", "tefca_dimension_evidence"],
        "note": ("Counts are distinct promoted entities of this delivery with an "
                 "evidence row per source. Connector readiness is reported on "
                 "/api/admin/health and is not coverage."),
    }


def stamp_pecos_backing(sources: Dict[str, Any]) -> Dict[str, Any]:
    """Name what stands behind the `pecos` coverage, from the ONE constant the
    status endpoint already reports (MQA-2026-013).

    No PECOS feed is connected: every PECOS evidence row is an NPPES-derived
    proxy. The Verification tab reads `pecos_backing` and, absent it, can only
    say "backing not stated by the server" — which is true and useless. The
    label/subtitle ride along so no surface invents its own PECOS wording.
    """
    pecos = sources.get("pecos")
    if not isinstance(pecos, dict):
        return sources
    from app.Tefca.connectors import PECOS_BACKING, PECOS_UI_LABEL, PECOS_UI_SUBTITLE
    pecos.setdefault("pecos_backing", PECOS_BACKING)
    pecos.setdefault("pecos_label", PECOS_UI_LABEL)
    pecos.setdefault("pecos_subtitle", PECOS_UI_SUBTITLE)
    return sources


def empty_coverage(*, reason: str) -> Dict[str, Any]:
    """The shape when there is no intake yet (a job that failed before Area 1)."""
    configured = configured_sources()
    sources = {key: status_model.coverage_state(configured=configured[key], eligible=0,
                                                attempted=0) for key in SOURCES}
    stamp_pecos_backing(sources)
    return {"intake_id": None, "job_id": None, "state": overall_state(sources),
            "eligible": 0, "sources": sources, "provenance": None,
            "evidence_tables": ["tefca_verifications", "tefca_dimension_evidence"],
            "note": reason}
