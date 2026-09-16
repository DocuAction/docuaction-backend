"""
Verification-time NPI outcomes, written to the one issue ledger.

The quality rules describe a delivered NPI's SHAPE (NPI-001..004, NPI-003). What
NPPES says about it is only known later, when a review cycle verifies the
promoted entity. Those outcomes were previously visible only inside the review
record's verification snapshot; this module writes them to `rce_issues` as
well, under the rule ids the contract reserves for them, so an analyst reading
the delivery's exception ledger sees every NPI question in one place:

    outcome                         rule     issue_type                     severity
    ─────────────────────────────   ──────   ────────────────────────────   ─────────
    VERIFIED (found, active)        —        (no issue)                     —
    NPI_NOT_FOUND                   NPI-005  NPI_NOT_FOUND                  MEDIUM
    NPI_DEACTIVATED                 NPI-006  NPI_DEACTIVATED                HIGH
    NPI_VERIFICATION_UNAVAILABLE    NPI-009  NPI_VERIFICATION_UNAVAILABLE   INFORMATIONAL

Findings are anchored to the CURATED RECORD that promoted the entity, so
`run_id` is the current quality run of that delivery and the finding appears
beside the record's other issues. An entity that did not come from a delivery
has no ledger to write to; the outcome is then recorded only in the review.

A repeat verification does not duplicate: an identical outcome already open
(undecided) for the same record is returned as-is. On deactivation the active
NPI identifier row is set to `inactive_pending_review` — never deleted — so the
registry stops presenting a deactivated NPI as current while a human decides
(the stored status is `inactive_pending`; see INACTIVE_PENDING_REVIEW).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce.curation import UNDECIDED_RESOLUTIONS
from app.tefca_registry.rce.quality_rules import (
    NON_QUALITY_ISSUE_TYPES, RULE_BY_ID,
)

logger = logging.getLogger(__name__)

VERIFIED = "VERIFIED"
NPI_NOT_FOUND = "NPI_NOT_FOUND"
NPI_DEACTIVATED = "NPI_DEACTIVATED"
NPI_VERIFICATION_UNAVAILABLE = "NPI_VERIFICATION_UNAVAILABLE"
OUTCOMES = (VERIFIED, NPI_NOT_FOUND, NPI_DEACTIVATED, NPI_VERIFICATION_UNAVAILABLE)

#: `tefca_entity_identifiers.identifier_status` is VARCHAR(20) on every
#: environment and the table is runtime-owned on DEV, so the contract's
#: `inactive_pending_review` (23 chars) cannot be stored without an operator
#: ownership window. The stored value keeps the contract prefix so a
#: `LIKE 'inactive_pending%'` reader matches either spelling.
INACTIVE_PENDING_REVIEW = "inactive_pending"

VERIFICATION_ISSUE_PREFIX = "VR"


def verification_issue_code(when: Optional[datetime] = None) -> str:
    """VR-YYYYMMDD-<16 hex>. Its own namespace; 28 characters."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"{VERIFICATION_ISSUE_PREFIX}-{stamp}-{uuid.uuid4().hex[:16]}"


# ── deriving the outcome ─────────────────────────────────────────────────────

def npi_outcome_from_nppes(data: Optional[Dict[str, Any]], *, ok: bool,
                           error: Optional[str] = None) -> Dict[str, Any]:
    """Classify one NPPES connector result.

    `ok` is the connector's "the query completed" flag; `data` is its payload.
    Found + status DEACTIVATED (or a deactivation date with no later
    reactivation) is NPI_DEACTIVATED; found otherwise is VERIFIED; a completed
    query with no record is NPI_NOT_FOUND; anything else is UNAVAILABLE.
    """
    if not ok:
        return {"outcome": NPI_VERIFICATION_UNAVAILABLE,
                "detail": (error or "NPPES did not complete")[:400]}
    data = data or {}
    if not data.get("found"):
        return {"outcome": NPI_NOT_FOUND,
                "detail": "NPI not present in NPPES.",
                "npi": data.get("npi")}
    status = str(data.get("status") or "").upper()
    deactivated = status in ("DEACTIVATED", "D") or (
        bool(data.get("deactivation_date")) and not data.get("reactivation_date"))
    if deactivated:
        return {"outcome": NPI_DEACTIVATED,
                "detail": (f"NPI deactivated in NPPES"
                           f"{' on ' + str(data['deactivation_date']) if data.get('deactivation_date') else ''}."),
                "status": data.get("status"),
                "deactivation_date": data.get("deactivation_date"),
                "npi": data.get("npi")}
    return {"outcome": VERIFIED, "detail": "NPI found and active in NPPES.",
            "status": data.get("status"), "npi": data.get("npi")}


def npi_outcome_from_evidence(evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The NPPES outcome from an assembled D1-D6 evidence bundle, or None.

    Reads the D1_IDENTITY dimension's NPPES item as `_dimension_identity`
    writes it: disposition PASS / NOT_FOUND / UNAVAILABLE / REVIEW, with
    `original_values.status` carrying the NPPES status.
    """
    for dimension in evidence.get("dimensions") or []:
        if dimension.get("dimension") != "D1_IDENTITY":
            continue
        for item in dimension.get("items") or []:
            if item.get("source") != "NPPES":
                continue
            disposition = item.get("disposition")
            values = item.get("original_values") or {}
            if disposition == "UNAVAILABLE":
                return {"outcome": NPI_VERIFICATION_UNAVAILABLE,
                        "detail": item.get("note") or "NPPES unavailable"}
            if disposition == "NOT_FOUND":
                return {"outcome": NPI_NOT_FOUND,
                        "detail": item.get("note") or "NPI not present in NPPES."}
            if item.get("rule_applied") == "NPPES_NPI_DEACTIVATED" or \
                    str(values.get("status") or "").upper() in ("DEACTIVATED", "D"):
                return {"outcome": NPI_DEACTIVATED,
                        "detail": item.get("note") or "NPI deactivated in NPPES.",
                        "status": values.get("status"),
                        "deactivation_date": values.get("deactivation_date"),
                        "npi": values.get("npi")}
            return {"outcome": VERIFIED, "detail": "NPI found and active in NPPES.",
                    "npi": values.get("npi")}
    return None


# ── recording ────────────────────────────────────────────────────────────────

async def _curated_for_entity(db, entity_id):
    return (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.canonical_entity_id == entity_id)
        .order_by(m.RceCuratedRecord.promoted_at.desc().nullslast(),
                  m.RceCuratedRecord.created_at.desc())
        .limit(1))).scalar_one_or_none()


async def record_npi_outcome(db, *, entity_id, npi: Optional[str], outcome: str,
                             detail: Optional[Any] = None,
                             commit: bool = False) -> Optional[m.RceIssue]:
    """Write the ledger row for one verification outcome. Returns it, or None.

    None when the outcome is VERIFIED (nothing to record), when the entity did
    not come from a delivery (no ledger), or when an identical undecided issue
    already exists for the record (no duplicate). Does not commit unless asked:
    the caller's verification transaction owns the commit.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown NPI outcome {outcome!r}; one of {OUTCOMES}")
    if isinstance(detail, dict):
        detail_text = str(detail.get("detail") or detail)
        deactivation_date = detail.get("deactivation_date")
    else:
        detail_text = str(detail or "")
        deactivation_date = None

    if outcome == NPI_DEACTIVATED and npi:
        await _mark_identifier_inactive(db, entity_id, npi)

    if outcome == VERIFIED:
        return None

    curated = await _curated_for_entity(db, entity_id)
    if curated is None:
        logger.info("NPI outcome %s for entity %s has no delivery ledger; recorded "
                    "in the review only", outcome, entity_id)
        return None

    rule_id, severity, authority = NON_QUALITY_ISSUE_TYPES[outcome]
    existing = (await db.execute(
        select(m.RceIssue).where(
            m.RceIssue.source_record_id == curated.source_record_id,
            m.RceIssue.rule_id == rule_id,
            m.RceIssue.issue_type == outcome,
            m.RceIssue.original_value == (npi or None),
            m.RceIssue.resolution.in_(sorted(UNDECIDED_RESOLUTIONS)))
        .limit(1))).scalar_one_or_none()
    if existing is not None:
        return existing

    current_run = await run_selection.current_run(db, curated.source_intake_id)
    descriptions = {
        NPI_NOT_FOUND: (
            f"NPI {npi!r} was not found in NPPES at verification. The identifier "
            f"stays on the entity, flagged; NPPES is the identity authority and "
            f"an analyst must determine whether the delivered value is wrong or "
            f"the registry has not yet caught up."),
        NPI_DEACTIVATED: (
            f"NPI {npi!r} is DEACTIVATED in NPPES"
            f"{' (deactivation date ' + str(deactivation_date) + ')' if deactivation_date else ''}. "
            f"The identifier row was set to '{INACTIVE_PENDING_REVIEW}' — not "
            f"deleted — so the registry stops presenting it as current while an "
            f"analyst decides."),
        NPI_VERIFICATION_UNAVAILABLE: (
            f"NPPES could not be reached to verify NPI {npi!r}: {detail_text}. "
            f"This is an outage, not a finding about the entity; recorded so the "
            f"gap in verification coverage is visible and can be retried."),
    }
    issue = m.RceIssue(
        id=uuid.uuid4(),
        issue_code=verification_issue_code(),
        source_intake_id=curated.source_intake_id,
        source_record_id=curated.source_record_id,
        run_id=current_run.id if current_run is not None else None,
        rule_id=rule_id,
        rule_version=RULE_BY_ID[rule_id].version,
        issue_type=outcome,
        severity=severity,
        field_name="NPI",
        original_value=npi,
        suggested_value=None,
        suggested_source="NPPES",
        suggested_confidence=None,
        correction_authority=authority,
        description=descriptions[outcome],
        resolution="OPEN",
        created_at=datetime.utcnow(),
    )
    db.add(issue)
    await db.flush()
    if commit:
        await db.commit()
    logger.info("NPI verification outcome recorded",
                extra={"entity_id": str(entity_id), "outcome": outcome,
                       "issue_code": issue.issue_code})
    return issue


async def _mark_identifier_inactive(db, entity_id, npi: str) -> int:
    rows = (await db.execute(
        select(reg.TefcaEntityIdentifier).where(
            reg.TefcaEntityIdentifier.entity_id == entity_id,
            reg.TefcaEntityIdentifier.identifier_type == "npi",
            reg.TefcaEntityIdentifier.identifier_value == npi,
            reg.TefcaEntityIdentifier.identifier_status == "active"))).scalars().all()
    for row in rows:
        row.identifier_status = INACTIVE_PENDING_REVIEW
    if rows:
        await db.flush()
    return len(rows)


async def record_from_sources(db, *, entity_id, sources: Dict[str, Any]
                              ) -> Optional[m.RceIssue]:
    """Convenience for `review_service`: read the probed NPPES source dict."""
    nppes = (sources or {}).get("nppes") or {}
    outcome = nppes.get("npi_outcome")
    if not outcome:
        return None
    return await record_npi_outcome(
        db, entity_id=entity_id, npi=nppes.get("lookup_identifier"),
        outcome=outcome, detail=nppes.get("npi_outcome_detail") or nppes.get("reason"))


async def record_from_evidence(db, *, entity_id, npi: Optional[str],
                               evidence: Dict[str, Any]) -> Optional[m.RceIssue]:
    """Convenience for `arc_pipeline.verify_and_classify`."""
    derived = npi_outcome_from_evidence(evidence)
    if derived is None:
        return None
    return await record_npi_outcome(db, entity_id=entity_id, npi=npi,
                                    outcome=derived["outcome"], detail=derived)
