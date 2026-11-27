"""A risk signal that disappears has not been cleared (2026-10-04, Part B).

THE FALSE PASS THIS CLOSES
──────────────────────────
`arc_pipeline.verify_and_classify` set `TefcaRegEntity.verification_status`
to "verified" whenever the CURRENT cycle classified B1 -- unconditionally.
An entity that carried a SAM.gov / OIG LEIE / CMS-revocation signal on its
last review, or that still has an open BLOCKING ledger finding (deactivated
NPI, invalid active identifier, material identifier conflict), was therefore
"verified" by the next clean cycle: cleared by absence alone, with no human
decision and no reinstatement evidence. Reproduced end to end in
tests/test_prior_risk_not_cleared_2026_10_04.py before this module existed.

WHAT THIS DOES, AND DELIBERATELY DOES NOT
─────────────────────────────────────────
It answers one question for one entity: "is there a prior risk signal that
no person has cleared?" The caller keeps the entity `in_review` and records
the answer beside the new review record. It does NOT change the classifier's
bucket (the rules engine stays the sole classifier), does NOT edit the prior
record, and does NOT decide the signal either way -- that remains an
individual, maker/checker adjudication through the ordinary review routes.

CLEARED means BOTH: a reviewer reclassified the signal-carrying record to
B1/B2 AND independent QA made it reportable (`reportable_at`). A maker's
reclassification alone is not a clearance.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.tefca_registry import models as reg

#: How far back to look for the most recent signal-carrying record.
HISTORY_LIMIT = 25

NOT_ADJUDICATED = "not_adjudicated"
CONFIRMED_BY_REVIEWER = "confirmed_by_reviewer"
RECLASSIFIED_TO_FINDING = "reclassified_to_finding"
NO_INDEPENDENT_QA = "no_independent_qa"


def clearance_state(record) -> Optional[str]:
    """None when a person cleared this record's signal under maker/checker;
    otherwise the reason it still stands."""
    resolution = getattr(record, "reviewer_resolution", None)
    if resolution is None:
        return NOT_ADJUDICATED
    if resolution != "reclassified":
        return CONFIRMED_BY_REVIEWER
    if getattr(record, "reclassified_to", None) not in ("B1", "B2"):
        return RECLASSIFIED_TO_FINDING
    if getattr(record, "reportable_at", None) is None:
        return NO_INDEPENDENT_QA
    return None


def exclusion_signals(verification_results: Optional[Dict[str, Any]]) -> List[str]:
    from app.tefca_registry.rce.shadow_reassessment import classifier_input, risk_signals

    return [s for s in risk_signals(classifier_input(verification_results))
            if s.startswith("EXCLUSION:")]


async def unresolved_prior_risk(db, entity_id) -> Optional[Dict[str, Any]]:
    """The entity's uncleared prior risk, or None.

    Independent sources, any one is enough:
      * the MOST RECENT review record carrying an exclusion signal, when no
        person cleared it (see `clearance_state`);
      * an OPEN BLOCKING finding in the issue ledger
        (`post_promotion_verification.has_unresolved_blocking_finding`);
      * an OPEN SAM public-extract candidate finding on any record of the entity;
      * a recheck RISK_SIGNAL no person-resolved, QA-reportable review postdates.
    """
    from app.tefca_registry.rce import post_promotion_verification as ppv

    out: Dict[str, Any] = {}
    rows = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.entity_id == entity_id,
               reg.ReviewRecord.classification_bucket.isnot(None))
        .order_by(reg.ReviewRecord.created_at.desc(), reg.ReviewRecord.review_id.desc())
        .limit(HISTORY_LIMIT))).scalars().all()
    for record in rows:
        signals = exclusion_signals(record.verification_results)
        if not signals:
            continue
        why = clearance_state(record)
        if why is not None:
            out.update({"prior_review_id": record.review_id,
                        "prior_bucket": record.classification_bucket,
                        "signals": signals, "why_not_cleared": why})
        break   # only the most recent signal-carrying record decides

    if await ppv.has_unresolved_blocking_finding(db, entity_id):
        out["open_blocking_finding"] = True

    # 2026-11-27 (full-population frame no longer excludes on `in_review`, independent review BLOCKING 1): two more
    # risk sources set the entity to `in_review` WITHOUT a review record or a BLOCKING finding, so neither check above
    # saw them, and a clean B1 would have marked the entity verified while they stood.
    #   * an OPEN SAM public-extract candidate on any delivered record of the entity (a potential exclusion match no
    #     analyst has confirmed or dismissed);
    #   * a recheck RISK_SIGNAL that no human-adjudicated, QA-reportable review of the entity postdates.
    if await _open_extract_candidate(db, entity_id):
        out["open_extract_candidate"] = True
    recheck_signal_at = await _uncleared_recheck_risk_signal(db, entity_id)
    if recheck_signal_at is not None:
        out["uncleared_recheck_risk_signal_at"] = recheck_signal_at.isoformat()

    if not out:
        return None
    out.setdefault("open_blocking_finding", False)
    out.setdefault("signals", [])
    out["note"] = ("A prior risk signal has not been cleared by a person. A clean "
                   "result in this cycle is not reinstatement evidence; the entity "
                   "stays in review until the prior signal is individually "
                   "adjudicated and independently QA-approved.")
    return out


async def _open_extract_candidate(db, entity_id) -> bool:
    from sqlalchemy import func
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.curation import UNDECIDED_RESOLUTIONS
    from app.tefca_registry.rce.verification_findings import SAM_EXTRACT_CANDIDATE
    records = select(m.RceCuratedRecord.source_record_id).where(m.RceCuratedRecord.canonical_entity_id == entity_id)
    return bool((await db.execute(
        select(func.count()).select_from(m.RceIssue).where(
            m.RceIssue.issue_type == SAM_EXTRACT_CANDIDATE,
            m.RceIssue.resolution.in_(sorted(UNDECIDED_RESOLUTIONS)),
            m.RceIssue.source_record_id.in_(records)))).scalar())


async def _uncleared_recheck_risk_signal(db, entity_id):
    """The newest recheck RISK_SIGNAL time for the entity, unless a review of the entity was CLEARED by a person AFTER
    it. Cleared means exactly what `clearance_state` means for an exclusion signal (reclassified to B1/B2 by a person
    and made reportable by independent QA - a confirmation, or a reclassification to a finding, does not clear), and the
    person's act (`reclassified_at`) must be at or after the signal: a review resolved before the signal and approved
    later never adjudicated it (CodeRabbit, 2026-11-27). None when there is no such signal or it was cleared."""
    from sqlalchemy import func
    from app.tefca_registry.rce import recheck_models as rm
    signal_at = (await db.execute(
        select(func.max(rm.RceRecheckItem.processed_at)).where(
            rm.RceRecheckItem.entity_id == entity_id,
            rm.RceRecheckItem.outcome == rm.OUTCOME_RISK_SIGNAL))).scalar()
    if signal_at is None:
        return None
    resolved_after = (await db.execute(
        select(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == entity_id,
            reg.ReviewRecord.reclassified_at.isnot(None),
            reg.ReviewRecord.reclassified_at >= signal_at)
        .order_by(reg.ReviewRecord.reclassified_at.desc()).limit(HISTORY_LIMIT))).scalars().all()
    if any(clearance_state(record) is None for record in resolved_after):
        return None
    return signal_at


def rationale_suffix(prior: Dict[str, Any]) -> str:
    parts = []
    if prior.get("prior_review_id"):
        parts.append(f"prior review {prior['prior_review_id']} carries "
                     f"{', '.join(prior['signals'])} ({prior['why_not_cleared']})")
    if prior.get("open_blocking_finding"):
        parts.append("an open BLOCKING ledger finding exists")
    if prior.get("open_extract_candidate"):
        parts.append("an open SAM public-extract candidate finding exists")
    if prior.get("uncleared_recheck_risk_signal_at"):
        parts.append(f"a recheck risk signal ({prior['uncleared_recheck_risk_signal_at']}) has not been adjudicated")
    return (" [PRIOR-RISK-NOT-CLEARED: " + "; ".join(parts) + ". This cycle's clean "
            "result does not clear it; entity held in review.]")


# ── exclusion screening that did not complete ────────────────────────────────
#
# 2026-10-04 (Part B). Reproduced against the ACTIVE v3 rule set:
#
#     nppes verified, oig_leie clear, pecos verified, sam_gov UNAVAILABLE  -> B1 RULE-001
#     nppes verified, oig_leie clear, pecos + sam_gov UNAVAILABLE          -> B1 RULE-002
#     ... sam_gov never checked / insufficient                             -> B1 RULE-001
#     ... cms_revocation UNAVAILABLE                                       -> B1 RULE-001
#
# Only OIG LEIE is required for B1. The rules are approved text ("an outage
# is not a discrepancy") and are NOT changed here. What is recorded is the
# consequence the rules do not state: such an entity is marked `verified`
# although a debarment or revocation screen never completed.
#
# `exclusion_screening_gaps` names the gap on the record, always. Whether the
# gap also withholds `verified` is `ENFORCE_COMPLETE_EXCLUSION_SCREENING`
# (default False = official behaviour unchanged; the proposed policy is
# visible but inactive).

EXCLUSION_CONTROLS = ("oig_leie", "sam_gov", "cms_revocation")

GAP_UNAVAILABLE = "UNAVAILABLE"
GAP_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
GAP_NOT_EVALUATED = "NOT_EVALUATED"
GAP_FAILED = "FAILED"


def exclusion_screening_gaps(classifier_input: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Exclusion controls that did not produce an answer for this record.

    NOT_APPLICABLE is not a gap: a control that does not attach to the entity
    is reported as not applicable, with its documentary basis, elsewhere."""
    sources = (classifier_input or {}).get("sources") or {}
    gaps: List[Dict[str, str]] = []
    for control in EXCLUSION_CONTROLS:
        raw = sources.get(control)
        if raw is None:
            gaps.append({"control": control, "gap": GAP_NOT_EVALUATED})
            continue
        status = raw.get("status") if isinstance(raw, dict) else raw
        disposition = (raw.get("disposition") if isinstance(raw, dict) else None) or ""
        if disposition == "NOT_APPLICABLE":
            continue
        if status == "unavailable":
            gap = {"control": control, "gap": GAP_UNAVAILABLE}
            if isinstance(raw, dict) and raw.get("not_attempted"):
                # Same gap (the control did not answer); the reason is that it was not asked, not that it was
                # unreachable. Additive key, present only for a control switched off for the run.
                gap["not_attempted"] = raw["not_attempted"]
            gaps.append(gap)
        elif status == "failed":
            gaps.append({"control": control, "gap": GAP_FAILED})
        elif disposition == "INSUFFICIENT_EVIDENCE":
            gaps.append({"control": control, "gap": GAP_INSUFFICIENT})
        elif status == "not_checked":
            gaps.append({"control": control, "gap": GAP_NOT_EVALUATED})
    return gaps


def enforce_complete_exclusion_screening() -> bool:
    from app.core.config import settings
    return bool(getattr(settings, "ENFORCE_COMPLETE_EXCLUSION_SCREENING", False))


def verification_claim(bucket: str, gaps: List[Dict[str, str]],
                       prior: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Official and inactive-proposed answer to "may this entity be marked
    verified?", for a record the classifier put in B1."""
    enforced = enforce_complete_exclusion_screening()
    official_verified = bucket == "B1" and not prior and not (enforced and gaps)
    return {
        "exclusion_screening_incomplete": gaps,
        "official": {"entity_marked_verified": official_verified,
                     "basis": "active rule set; an unavailable SAM / CMS-revocation "
                              "screen does not prevent B1" if not enforced else
                              "ENFORCE_COMPLETE_EXCLUSION_SCREENING is on"},
        "proposed_inactive": {
            "entity_would_be_marked_verified": bucket == "B1" and not prior and not gaps,
            "basis": "verified requires every applicable exclusion control to have "
                     "answered; an unavailable, insufficient or unevaluated screen is "
                     "never a pass",
            "status": "ENFORCED" if enforced else "INACTIVE_UNAPPROVED"},
        "enforced": enforced,
    }
