"""Post-promotion verification: findings discovered AFTER a record has
already been promoted (pre-merge review Decision 2, 2026-09-16/18).

THE RULE THIS MODULE EXISTS TO ENFORCE
───────────────────────────────────────
A promotion event (`rce_disposition_events`, `rce_curated_records.
record_status`, `canonical_entity_id`) is a historical fact once written.
Independent review finding M-3 (2026-09-16) showed a later verification
outcome — `verification_findings.record_npi_outcome` writing an NPI-006
HIGH finding — flowing into `curation.recompute_hold_status`, which then
REWROTE an already-promoted record's `record_status` to HELD, contradicting
its own disposition (CREATED/UPDATED/still on record) and permanently
breaking reconciliation's E == C check with no path to clear it. That is
`curation._blocking_by_record` now refusing to consider a promoted record at
all (see the guard there); this module is the REPLACEMENT mechanism for what
a post-promotion blocking finding must do instead.

BLOCKING vs NONBLOCKING
───────────────────────
    BLOCKING     — an invalid active identifier, a confirmed deactivation
                   requiring review, or a material identifier conflict. Sets
                   the entity's verification_status to "in_review" (an
                   existing state — see `arc_pipeline.py`'s own use of it),
                   opens exactly one analyst work item (idempotent per
                   issue_code, via `dq_review_bridge`), and creates a NEW
                   reconciliation snapshot. The ORIGINAL promotion event and
                   `record_status` are never touched.
    NONBLOCKING  — everything else (NPI_NOT_FOUND, NPI_VERIFICATION_
                   UNAVAILABLE). Written to the one ledger (`rce_issues`),
                   visible in the exception ledger, counted by whatever the
                   approved status model already counts by severity. No
                   entity-state change, no forced work item, no snapshot.

APPEND-ONLY
───────────
Every write here is a NEW `rce_issues` row (never an edit to
`rce_disposition_events`, never a resolution write to a PRIOR issue) or a NEW
`rce_reconciliation_snapshots` row (append-only by grant; see
`20260917_delivery_traceability.py`). `resolve_post_promotion_finding` is the
only function that changes an EXISTING row, and only the same
resolve-workflow columns (`resolution`, `resolved_by`, `qa_approved_by`, ...)
every other issue in this ledger already uses.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.core import request_context
from app.tefca_registry import models as reg
from app.tefca_registry.rce import curation
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce.quality_rules import NON_QUALITY_ISSUE_TYPES, RULE_BY_ID
from app.tefca_registry.rce.verification_findings import (
    NPI_DEACTIVATED, NPI_NOT_FOUND, NPI_VERIFICATION_UNAVAILABLE,
    record_npi_outcome, verification_issue_code,
)

logger = logging.getLogger(__name__)

INVALID_ACTIVE_IDENTIFIER = "INVALID_ACTIVE_IDENTIFIER"
MATERIAL_IDENTIFIER_CONFLICT = "MATERIAL_IDENTIFIER_CONFLICT"

#: The three named triggers. Each is HIGH and QA_REQUIRED
#: (`quality_rules.NON_QUALITY_ISSUE_TYPES`) — independent QA is required
#: because each one can change final eligibility or classification.
BLOCKING_OUTCOMES = frozenset({
    NPI_DEACTIVATED, INVALID_ACTIVE_IDENTIFIER, MATERIAL_IDENTIFIER_CONFLICT,
})
#: Medium/informational findings the approved rule does not define as
#: blocking. They never hold, exclude, or force a work item.
NONBLOCKING_OUTCOMES = frozenset({NPI_NOT_FOUND, NPI_VERIFICATION_UNAVAILABLE})

#: `TefcaRegEntity.verification_status` — reusing the SAME value
#: `arc_pipeline.py` already writes for "who must look", not a new
#: vocabulary. Set while a blocking finding is unresolved; cleared back to
#: VERIFIED_STATUS only when none remain.
REVIEW_REQUIRED_STATUS = "in_review"
VERIFIED_STATUS = "verified"


def is_blocking(outcome: str) -> bool:
    return outcome in BLOCKING_OUTCOMES


class UnknownOutcome(ValueError):
    pass


# ── writing the finding (append-only) ────────────────────────────────────────

async def _curated_for_entity(db, entity_id):
    return (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.canonical_entity_id == entity_id)
        .order_by(m.RceCuratedRecord.promoted_at.desc().nullslast(),
                  m.RceCuratedRecord.created_at.desc())
        .limit(1))).scalar_one_or_none()


async def _write_generic_finding(db, *, entity_id, outcome: str,
                                 original_value: Optional[str],
                                 description: str) -> Optional[m.RceIssue]:
    """The same shape and dedup rule as `verification_findings.
    record_npi_outcome`, for the two outcomes that are not an NPPES NPI
    check. Returns None when the entity has no delivery ledger, or when an
    identical undecided finding already exists (no duplicate)."""
    curated = await _curated_for_entity(db, entity_id)
    if curated is None:
        logger.info("post-promotion finding %s for entity %s has no delivery "
                   "ledger; not recorded", outcome, entity_id)
        return None

    rule_id, severity, authority = NON_QUALITY_ISSUE_TYPES[outcome]
    existing = (await db.execute(
        select(m.RceIssue).where(
            m.RceIssue.source_record_id == curated.source_record_id,
            m.RceIssue.rule_id == rule_id,
            m.RceIssue.issue_type == outcome,
            m.RceIssue.original_value == (original_value or None),
            m.RceIssue.resolution.in_(sorted(curation.UNDECIDED_RESOLUTIONS)))
        .limit(1))).scalar_one_or_none()
    if existing is not None:
        return existing

    current_run = await run_selection.current_run(db, curated.source_intake_id)
    issue = m.RceIssue(
        id=uuid.uuid4(), issue_code=verification_issue_code(),
        source_intake_id=curated.source_intake_id,
        source_record_id=curated.source_record_id,
        run_id=current_run.id if current_run is not None else None,
        rule_id=rule_id, rule_version=RULE_BY_ID[rule_id].version,
        issue_type=outcome, severity=severity, field_name="NPI",
        original_value=original_value, suggested_source="post_promotion_verification",
        correction_authority=authority, description=description,
        resolution="OPEN", created_at=datetime.utcnow())
    db.add(issue)
    await db.flush()
    return issue


async def record_finding(db, *, entity_id, outcome: str, npi: Optional[str] = None,
                         identifier_type: str = "npi", existing_value: Optional[str] = None,
                         submitted_value: Optional[str] = None,
                         detail: Optional[Any] = None, actor: str = "SYSTEM",
                         actor_id=None, commit: bool = False) -> Dict[str, Any]:
    """Record one post-promotion finding and, if it is BLOCKING, its
    consequences. Never touches the original promotion event.

    Returns `{"issue": issue-dict-or-None, "blocking": bool, "case": ...,
    "verification_status": ..., "snapshot_id": ...}`.
    """
    if outcome in NON_QUALITY_ISSUE_TYPES:
        pass
    else:
        raise UnknownOutcome(f"not a known post-promotion outcome: {outcome!r}")

    if outcome in (NPI_DEACTIVATED, NPI_NOT_FOUND, NPI_VERIFICATION_UNAVAILABLE):
        issue = await record_npi_outcome(db, entity_id=entity_id, npi=npi,
                                         outcome=outcome, detail=detail)
    elif outcome == INVALID_ACTIVE_IDENTIFIER:
        value = npi or submitted_value
        issue = await _write_generic_finding(
            db, entity_id=entity_id, outcome=outcome, original_value=value,
            description=(f"The entity's active {identifier_type} {value!r} does "
                        f"not pass validation. A post-promotion re-check found "
                        f"this; the registry value is left in place pending an "
                        f"analyst decision. {detail or ''}".strip()))
    elif outcome == MATERIAL_IDENTIFIER_CONFLICT:
        issue = await _write_generic_finding(
            db, entity_id=entity_id, outcome=outcome, original_value=submitted_value,
            description=(f"A later verification found a {identifier_type} value "
                        f"({submitted_value!r}) materially different from the "
                        f"registered value ({existing_value!r}) for this "
                        f"already-promoted entity. Neither value was changed; "
                        f"the registered value is retained pending an analyst "
                        f"decision. {detail or ''}".strip()))
    else:  # pragma: no cover - guarded above
        raise UnknownOutcome(outcome)

    if issue is not None:
        issue.correlation_id = request_context.correlation_id()[:64]
        issue.build_sha = request_context.build_sha()
        if issue.before_state is None:
            issue.before_state = {"note": "no prior state; this is the first "
                                          "post-promotion finding of this kind "
                                          "for this record"}
        await db.flush()

    result: Dict[str, Any] = {
        "issue": ({"id": str(issue.id), "issue_code": issue.issue_code,
                  "issue_type": issue.issue_type, "severity": issue.severity,
                  "resolution": issue.resolution} if issue is not None else None),
        "blocking": is_blocking(outcome), "case": None,
        "verification_status": None, "snapshot_id": None,
    }
    if not is_blocking(outcome) or issue is None:
        if commit:
            await db.commit()
        return result

    entity = await db.get(reg.TefcaRegEntity, entity_id)
    if entity is not None and entity.verification_status != REVIEW_REQUIRED_STATUS:
        entity.verification_status = REVIEW_REQUIRED_STATUS
        entity.updated_at = datetime.utcnow()
        await db.flush()
    result["verification_status"] = REVIEW_REQUIRED_STATUS

    from app.tefca_registry.rce import dq_review_bridge
    case = await dq_review_bridge.open_post_promotion_case(
        db, issue, entity_id=entity_id, actor=actor)
    result["case"] = case

    curated = await _curated_for_entity(db, entity_id)
    if curated is not None:
        from app.tefca_registry.rce import reconciliation
        from app.tefca_registry.rce.delivery_jobs import job_for_intake

        job = await job_for_intake(db, curated.source_intake_id)
        if job is not None:
            full = await reconciliation.reconcile_delivery(db, curated.source_intake_id)
            snapshot = await reconciliation.persist_snapshot(
                db, curated.source_intake_id, full, job_id=job.id, actor=actor,
                trigger="POST_PROMOTION_VERIFICATION")
            result["snapshot_id"] = str(snapshot.id)

    if commit:
        await db.commit()
    return result


# ── the other two named triggers ─────────────────────────────────────────────

async def check_active_identifier_validity(db, entity_id, *, identifier_type: str = "npi",
                                           actor: str = "SYSTEM") -> Optional[Dict[str, Any]]:
    """Re-validate the entity's current ACTIVE identifier of `identifier_type`.
    Records INVALID_ACTIVE_IDENTIFIER (and its blocking consequences) if it no
    longer validates. None when the active value is valid or absent."""
    from app.services.npi_validator import validate_npi

    row = (await db.execute(select(reg.TefcaEntityIdentifier).where(
        reg.TefcaEntityIdentifier.entity_id == entity_id,
        reg.TefcaEntityIdentifier.identifier_type == identifier_type,
        reg.TefcaEntityIdentifier.identifier_status == "active"))).scalars().first()
    if row is None or identifier_type != "npi":
        return None
    ok, message = validate_npi(row.identifier_value)
    if ok:
        return None
    return await record_finding(
        db, entity_id=entity_id, outcome=INVALID_ACTIVE_IDENTIFIER,
        npi=row.identifier_value, identifier_type=identifier_type,
        detail=message, actor=actor)


async def record_post_promotion_conflict(db, *, entity_id, identifier_type: str,
                                         submitted_value: str, existing_value: Optional[str],
                                         actor: str = "SYSTEM") -> Dict[str, Any]:
    """A later verification found the registered value materially disagrees
    with a re-checked submitted/authoritative value, for an entity already
    promoted. Neither value is changed here."""
    return await record_finding(
        db, entity_id=entity_id, outcome=MATERIAL_IDENTIFIER_CONFLICT,
        identifier_type=identifier_type, submitted_value=submitted_value,
        existing_value=existing_value, actor=actor)


# ── resolution (append-only: a new decision, never a rewrite) ───────────────

async def resolve_post_promotion_finding(db, issue_id, *, decision: str, actor: str,
                                         qa_actor: Optional[str] = None,
                                         notes: Optional[str] = None,
                                         commit: bool = False) -> Dict[str, Any]:
    """Resolve one post-promotion finding.

    `decision`: "CONFIRMED" (the finding was correct; routes through APPROVED,
    which `curation.transition_issue` requires an independent `qa_actor` for
    on a QA_REQUIRED issue — every post-promotion blocking issue type is
    QA_REQUIRED), "FALSE_POSITIVE" (WAIVED; no QA needed), or "RETURNED"
    (QA sends it back to UNDER_REVIEW).

    After resolution, the entity's `verification_status` is restored to
    VERIFIED only when no other unresolved blocking finding remains for it —
    never unconditionally, and never for an entity this function did not just
    finish clearing the last finding on. A new reconciliation snapshot is
    created either way; the resolved-away snapshot is never modified.
    """
    issue = await db.get(m.RceIssue, issue_id)
    if issue is None:
        raise curation.CorrectionRefused(f"No issue {issue_id}")
    if issue.issue_type not in NON_QUALITY_ISSUE_TYPES or not is_blocking(issue.issue_type):
        raise curation.CorrectionRefused(
            f"{issue_id} is not a post-promotion BLOCKING finding; use the "
            f"ordinary disposition workflow for it.")

    path = {"CONFIRMED": ("PROPOSED", "APPROVED", "RESOLVED"),
           "FALSE_POSITIVE": ("PROPOSED", "WAIVED", "RESOLVED"),
           "RETURNED": ("UNDER_REVIEW",)}.get(decision)
    if path is None:
        raise ValueError(f"decision must be one of CONFIRMED, FALSE_POSITIVE, RETURNED, "
                         f"got {decision!r}")
    before = {"resolution": issue.resolution}
    await curation._walk(db, issue.id, path, actor=actor, notes=notes or "", qa_actor=qa_actor)
    await db.refresh(issue)
    issue.after_state = {"resolution": issue.resolution}
    issue.before_state = before
    await db.flush()

    entity_id = None
    curated = None
    if issue.source_record_id is not None:
        curated = (await db.execute(select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_record_id == issue.source_record_id))).scalar_one_or_none()
        entity_id = curated.canonical_entity_id if curated is not None else None

    restored = False
    if entity_id is not None and issue.resolution == "RESOLVED":
        remaining = (await db.execute(select(m.RceIssue.id).where(
            m.RceIssue.issue_type.in_(sorted(BLOCKING_OUTCOMES)),
            m.RceIssue.resolution.in_(sorted(curation.UNDECIDED_RESOLUTIONS)),
            m.RceIssue.source_record_id.in_(
                select(m.RceCuratedRecord.source_record_id).where(
                    m.RceCuratedRecord.canonical_entity_id == entity_id))))).scalars().all()
        if not remaining:
            entity = await db.get(reg.TefcaRegEntity, entity_id)
            if entity is not None and entity.verification_status == REVIEW_REQUIRED_STATUS:
                entity.verification_status = VERIFIED_STATUS
                entity.updated_at = datetime.utcnow()
                await db.flush()
                restored = True

    snapshot_id = None
    if curated is not None:
        from app.tefca_registry.rce import reconciliation
        from app.tefca_registry.rce.delivery_jobs import job_for_intake

        job = await job_for_intake(db, curated.source_intake_id)
        if job is not None:
            full = await reconciliation.reconcile_delivery(db, curated.source_intake_id)
            snapshot = await reconciliation.persist_snapshot(
                db, curated.source_intake_id, full, job_id=job.id, actor=actor,
                trigger="POST_PROMOTION_RESOLUTION")
            snapshot_id = str(snapshot.id)

    if commit:
        await db.commit()
    return {"issue_id": str(issue.id), "resolution": issue.resolution,
            "decision": decision, "entity_restored_to_verified": restored,
            "snapshot_id": snapshot_id}
