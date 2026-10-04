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

    Two independent sources, either is enough:
      * the MOST RECENT review record carrying an exclusion signal, when no
        person cleared it (see `clearance_state`);
      * an OPEN BLOCKING finding in the issue ledger
        (`post_promotion_verification.has_unresolved_blocking_finding`).
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

    if not out:
        return None
    out.setdefault("open_blocking_finding", False)
    out.setdefault("signals", [])
    out["note"] = ("A prior risk signal has not been cleared by a person. A clean "
                   "result in this cycle is not reinstatement evidence; the entity "
                   "stays in review until the prior signal is individually "
                   "adjudicated and independently QA-approved.")
    return out


def rationale_suffix(prior: Dict[str, Any]) -> str:
    parts = []
    if prior.get("prior_review_id"):
        parts.append(f"prior review {prior['prior_review_id']} carries "
                     f"{', '.join(prior['signals'])} ({prior['why_not_cleared']})")
    if prior.get("open_blocking_finding"):
        parts.append("an open BLOCKING ledger finding exists")
    return (" [PRIOR-RISK-NOT-CLEARED: " + "; ".join(parts) + ". This cycle's clean "
            "result does not clear it; entity held in review.]")
