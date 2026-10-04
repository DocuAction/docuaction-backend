"""Three different things that were all being called "verified" (2026-10-04).

    1. A SOURCE CHECK succeeded     -- SAM.gov answered and listed nothing.
    2. An ENTITY was CLASSIFIED     -- the active rule set put it in B1, or an
                                       analyst disposed of it.
    3. Verification is COMPLETE     -- every applicable check produced an answer.

They are independent. With the active (v3) rules an entity is classified B1,
and its registry status becomes `verified`, while SAM.gov timed out: (2) is
B1, (1) for SAM.gov is NOT a success, and (3) is INCOMPLETE. Reporting that
entity under a bare "verified" hides the third fact behind the second.

This module only DESCRIBES. It reads the classifier input already snapshotted
on the review record and never writes. It does not change a bucket, a rule, a
reportability gate or an entity status: whether an entity with incomplete
screening may be reportable is an unapproved policy decision
(ENFORCE_COMPLETE_EXCLUSION_SCREENING, default off). What it guarantees is that
no outward-facing count or label says "verified" without the qualification.

An unavailable source is never turned into an exclusion, a non-compliance or a
"not found" here: UNAVAILABLE stays UNAVAILABLE.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce.prior_risk import EXCLUSION_CONTROLS

# -- 1. what one source check did ---------------------------------------------
CONFIRMED = "CONFIRMED"                    # answered; confirms the entity
NOT_LISTED = "NOT_LISTED"                  # exclusion list answered; nothing listed
ANSWERED_WITH_FINDING = "ANSWERED_WITH_FINDING"   # answered; adverse / differs / no record
UNAVAILABLE = "UNAVAILABLE"                # timeout, 429, outage, error body
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"   # nothing to look up with
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_EVALUATED = "NOT_EVALUATED"            # never attempted / not recorded

#: The check produced an answer about the entity (favourable or not).
ANSWERED = frozenset({CONFIRMED, NOT_LISTED, ANSWERED_WITH_FINDING})
#: The check SUCCESSFULLY VERIFIED: answered, and favourably. Nothing else is
#: ever counted as a successful source verification.
SUCCESSFUL = frozenset({CONFIRMED, NOT_LISTED})
#: The check did not produce an answer although one was owed.
MISSING = frozenset({UNAVAILABLE, INSUFFICIENT_EVIDENCE, NOT_EVALUATED})

# -- 3. overall completeness --------------------------------------------------
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
NOT_RECORDED = "NOT_RECORDED"   # no classifier input on file: unknown, NOT complete

# -- the qualified overall status ---------------------------------------------
VERIFIED_COMPLETE = "verified"
VERIFIED_CHECKS_INCOMPLETE = "verified_checks_incomplete"
VERIFIED_CHECKS_NOT_RECORDED = "verified_checks_not_recorded"

DISPLAY = {
    VERIFIED_COMPLETE: "Verified",
    VERIFIED_CHECKS_INCOMPLETE: "Verified - checks incomplete",
    VERIFIED_CHECKS_NOT_RECORDED: "Verified - completeness not recorded",
}

SOURCE_DISPLAY = {
    "nppes": "NPPES", "oig_leie": "OIG LEIE", "sam_gov": "SAM.gov",
    "pecos": "Medicare enrollment (CMS PPEF)", "cms_revocation": "CMS revocation",
    "pecos_practice_location": "CMS practice location",
    "pecos_reassignment": "CMS reassignment", "rce_directory": "RCE directory",
    "website": "Entrant website",
}

OUTCOME_DISPLAY = {
    CONFIRMED: "Confirmed",
    NOT_LISTED: "Checked - not listed",
    ANSWERED_WITH_FINDING: "Checked - needs review",
    UNAVAILABLE: "Source unavailable",
    INSUFFICIENT_EVIDENCE: "Could not be checked (no identifier or name to search)",
    NOT_APPLICABLE: "Not applicable",
    NOT_EVALUATED: "Not checked",
}


def source_outcome(raw: Any) -> str:
    """What one source check did, from its classifier-input entry."""
    if raw is None:
        return NOT_EVALUATED
    status = (raw.get("status") if isinstance(raw, dict) else raw) or ""
    disposition = ((raw.get("disposition") if isinstance(raw, dict) else None) or "").upper()
    status = str(status).lower()
    if disposition == "NOT_APPLICABLE":
        return NOT_APPLICABLE
    if disposition == "UNAVAILABLE" or status == "unavailable":
        return UNAVAILABLE
    if disposition == "INSUFFICIENT_EVIDENCE":
        return INSUFFICIENT_EVIDENCE
    if status == "verified":
        return CONFIRMED
    if status == "clear":
        return NOT_LISTED
    if status in ("not_checked", ""):
        return NOT_EVALUATED
    # failed / not_found / excluded / debarred / anything else the source SAID
    return ANSWERED_WITH_FINDING


def completeness(verification_results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-source outcomes and overall completeness for one review record.

    Completeness is owed by (a) every exclusion control, whether or not it
    appears in the record -- an absent screen is NOT_EVALUATED, not complete --
    and (b) every other source that was attempted. A source the record never
    attempted and that is not an exclusion control is not counted against it.
    """
    from app.tefca_registry.rce.shadow_reassessment import classifier_input

    inp = classifier_input(verification_results)
    sources = (inp or {}).get("sources")
    if not isinstance(sources, dict) or not sources:
        return {"state": NOT_RECORDED, "source_outcomes": {}, "incomplete": [],
                "successful_sources": [],
                "note": "No source results are stored for this record; completeness "
                        "is unknown and is not assumed."}
    outcomes = {name: source_outcome(raw) for name, raw in sources.items()}
    for control in EXCLUSION_CONTROLS:
        outcomes.setdefault(control, NOT_EVALUATED)
    incomplete = [{"source": name, "outcome": outcome,
                   "label": SOURCE_DISPLAY.get(name, name),
                   "outcome_label": OUTCOME_DISPLAY[outcome]}
                  for name, outcome in sorted(outcomes.items()) if outcome in MISSING]
    return {
        "state": INCOMPLETE if incomplete else COMPLETE,
        "source_outcomes": outcomes,
        "successful_sources": sorted(n for n, o in outcomes.items() if o in SUCCESSFUL),
        "incomplete": incomplete,
    }


def overall_status(entity_verification_status: Optional[str],
                   comp: Optional[Dict[str, Any]]) -> str:
    """The entity's registry status, QUALIFIED when it says `verified`.

    Every status other than `verified` passes through unchanged."""
    if (entity_verification_status or "") != "verified":
        return entity_verification_status or "unknown"
    state = (comp or {}).get("state", NOT_RECORDED)
    if state == COMPLETE:
        return VERIFIED_COMPLETE
    if state == INCOMPLETE:
        return VERIFIED_CHECKS_INCOMPLETE
    return VERIFIED_CHECKS_NOT_RECORDED


def describe(entity_verification_status: Optional[str],
             comp: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The block DTOs carry beside `verification_status`."""
    comp = comp or {"state": NOT_RECORDED, "source_outcomes": {}, "incomplete": [],
                    "successful_sources": []}
    overall = overall_status(entity_verification_status, comp)
    return {
        "state": comp["state"],
        "overall_status": overall,
        "overall_label": DISPLAY.get(
            overall, (overall or "unknown").replace("_", " ").capitalize()),
        "incomplete": comp.get("incomplete", []),
        "source_outcomes": comp.get("source_outcomes", {}),
        "successful_sources": comp.get("successful_sources", []),
        # Stated on the block so no consumer infers a bucket or policy change.
        "classification_unchanged": True,
    }


async def latest_completeness(db, entity_ids: Optional[Iterable[Any]] = None, *,
                              only_verified: bool = True) -> Dict[Any, Dict[str, Any]]:
    """{entity_id: completeness} from each entity's most recent classified
    review record. One query; only the two small JSON sub-objects are read."""
    R, E = reg.ReviewRecord, reg.TefcaRegEntity
    stmt = (select(R.entity_id,
                   R.verification_results["classifier_input"]["sources"],
                   R.verification_results["sources"])
            .where(R.entity_id.isnot(None), R.classification_bucket.isnot(None))
            .order_by(R.entity_id, R.created_at.desc(), R.review_id.desc())
            .distinct(R.entity_id))
    if only_verified:
        stmt = stmt.join(E, E.id == R.entity_id).where(E.verification_status == "verified")
    if entity_ids is not None:
        ids = list(entity_ids)
        if not ids:
            return {}
        stmt = stmt.where(R.entity_id.in_(ids))
    out: Dict[Any, Dict[str, Any]] = {}
    for entity_id, pipeline_sources, manual_sources in (await db.execute(stmt)).all():
        sources = pipeline_sources if isinstance(pipeline_sources, dict) else manual_sources
        out[entity_id] = completeness({"sources": sources} if isinstance(sources, dict) else None)
    return out


async def split_verified_counts(db, counts: Dict[str, int],
                                entity_ids: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Replace the bare `verified` count with its qualified parts.

    The parts always sum to the original count, so totals still reconcile."""
    verified = int(counts.get("verified", 0))
    if not verified:
        return {"counts": dict(counts), "verified_total": 0, "split": {},
                "incomplete_by_source": {}}
    by_entity = await latest_completeness(db, entity_ids)
    incomplete = min(sum(1 for c in by_entity.values() if c["state"] == INCOMPLETE), verified)
    complete = min(sum(1 for c in by_entity.values() if c["state"] == COMPLETE),
                   verified - incomplete)
    not_recorded = verified - complete - incomplete
    out = {k: v for k, v in counts.items() if k != "verified"}
    split = {VERIFIED_COMPLETE: complete,
             VERIFIED_CHECKS_INCOMPLETE: incomplete,
             VERIFIED_CHECKS_NOT_RECORDED: not_recorded}
    for key, value in split.items():
        if value:
            out[key] = value
    by_source: Dict[str, int] = {}
    for c in by_entity.values():
        for item in c.get("incomplete", []):
            by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    return {"counts": out, "verified_total": verified, "split": split,
            "incomplete_by_source": by_source}
