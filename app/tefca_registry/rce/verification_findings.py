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

    THE CANONICAL EVIDENCE CONTRACT is `DimensionResult.to_dict()`
    (app/Tefca/evidence_dimensions.py) — the one place a dimension dict is
    ever serialised. Its per-source list is keyed `"evidence"`
    (`[i.to_dict() for i in self.items]`); `items` is only the Python
    attribute name on the dataclass, never a key in the dict this function
    actually receives. This function read `dimension["items"]` until
    2026-09-18 — confirmed nowhere else in the codebase (no migration,
    fixture, or preserved log ever produced that shape) — so it silently
    returned None for every real evidence bundle: NPPES NOT_FOUND/
    DEACTIVATED/UNAVAILABLE outcomes reached `_classify_entity_outcome`
    correctly (that function reads the dimension's own top-level
    disposition/applicability, unaffected by this bug) but never wrote an
    issue-ledger finding or an analyst work item, in either
    `arc_pipeline.verify_and_classify` or `automated_verification.py`. A
    malformed or missing `evidence` list is handled the same way an absent
    D1_IDENTITY dimension always was: this function returns None, which its
    caller (`record_from_evidence`) already treats as "nothing to record" -
    never as VERIFIED.
    """
    # 2026-10-04 (Part B): this compared against the literal "D1_IDENTITY" --
    # the enum MEMBER NAME. Every real bundle carries the member's VALUE,
    # "IDENTITY" (`Dimension.D1_IDENTITY.value`; `DimensionResult.to_dict()`),
    # so this loop never matched real evidence and returned None for every
    # entity: a deactivated NPI found by NPPES on the delivery, coverage and
    # recheck paths was never written to the issue ledger, so
    # `has_unresolved_blocking_finding` (the QA gate) never saw it. Hand-built
    # test evidence used the same wrong literal, which is why nothing failed.
    # Both spellings are accepted so those fixtures keep meaning what they did.
    from app.Tefca.evidence_dimensions import Dimension
    identity_names = (Dimension.D1_IDENTITY.value, "D1_IDENTITY")
    for dimension in evidence.get("dimensions") or []:
        if dimension.get("dimension") not in identity_names:
            continue
        dim_evidence = dimension.get("evidence")
        if not isinstance(dim_evidence, list):
            if dim_evidence is not None:
                logger.warning(
                    "D1_IDENTITY evidence is %s, not a list - treating as no "
                    "NPPES evidence for this generation rather than guessing",
                    type(dim_evidence).__name__)
            continue
        for item in dim_evidence:
            if not isinstance(item, dict) or item.get("source") != "NPPES":
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
            if item.get("rule_applied") == "NPPES_IDENTITY_NAME_CONFLICT":
                # Found and active, but the registered legal name does not
                # correspond to the submitted organization — the dimension is
                # REVIEW and the conflict lives on the evidence item. There is
                # no contract-reserved rule id for this outcome yet, so there
                # is nothing to record in the ledger ("None means nothing to
                # record") — but it must NEVER fall through to VERIFIED.
                return None
            return {"outcome": VERIFIED, "detail": "NPI found and active in NPPES.",
                    "npi": values.get("npi")}
    return None


# ── recording ────────────────────────────────────────────────────────────────

async def _curated_for_entity(db, entity_id):
    """Latest curated record that promoted this entity (by promotion, then
    creation time), or None.
    """
    return (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.canonical_entity_id == entity_id)
        .order_by(m.RceCuratedRecord.promoted_at.desc().nullslast(),
                  m.RceCuratedRecord.created_at.desc())
        .limit(1))).scalar_one_or_none()


async def anchor_for_reference(db, *, intake_id, ref, entity_id):
    """The source_record_id a verification of `ref` belongs to, or None.

    ANCHORED only when exactly ONE curated record of the named delivery carries the delivered reference
    (`rce_org_oid`) AND links the very entity being verified - the rule `arc_pipeline._source_anchor` applies. No
    delivery, no such record, a duplicate delivered id or a different linked entity leave it unanchored; an
    unanchored verification writes no ledger row (never "the entity's latest record")."""
    if intake_id is None or not ref or entity_id is None:
        return None
    rows = (await db.execute(
        select(m.RceCuratedRecord.source_record_id, m.RceCuratedRecord.canonical_entity_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.rce_org_oid == ref))).all()
    if len(rows) != 1:
        return None
    source_record_id, canonical = rows[0]
    if canonical is None or str(canonical) != str(entity_id):
        return None
    return source_record_id


def npi_key(value) -> str:
    """One comparable form of an NPI: stripped, a spreadsheet's trailing ".0" tolerated, digits only. No validity
    rule. The ledger writer also uses it for the identifier lookup, the duplicate check and the issue row's
    `original_value`; delivered records are never rewritten."""
    text = str(value or "").strip()
    if "." in text and text.split(".", 1)[1].strip("0") == "" and text.split(".", 1)[0].isdigit():
        text = text.split(".", 1)[0]
    return "".join(ch for ch in text if ch.isdigit())


def delivered_npi_of(record) -> str:
    """The NPI a delivered source record submitted, as delivered (`parsed["NPI"]`, else the record's `npi`)."""
    return str((getattr(record, "parsed", None) or {}).get("NPI", getattr(record, "npi", None)) or "").strip()


async def validated_delivery_context(db, *, intake_id, ref, queued_entity_id, entity, probe_npi):
    """ONE identity validation for a verification of `ref` in a delivery, run BEFORE anything is persisted.
    Shared by the recheck and automated-coverage callers. Returns (context, None) or (None, reason).

    `resolve_entity` resolves a reference GLOBALLY, so before evidence, a ledger row, an identifier status or an
    entity status may be written, all of this must hold: (1) the reference resolves to a registry identity equal to
    the QUEUED entity; (2) exactly one delivered record of the named delivery carries the reference and links that
    entity (`anchor_for_reference`); (3) that record can be read; (4) when the source probes will be asked about an
    NPI (`probe_npi`, the resolved entity's), it is the NPI that record DELIVERED (compared by `npi_key`) - the rule
    `arc_pipeline.verify_and_classify` already applies ("submitted NPI differs from the identifier used by source
    probes"). A refusal means the caller persists nothing."""
    resolved_id = (entity or {}).get("_registry_entity_id")
    if resolved_id is None:
        return None, "the reference has no registry identity now"
    if queued_entity_id is None or str(resolved_id) != str(queued_entity_id):
        return None, "the reference now resolves to a different entity than the one queued"
    anchor = await anchor_for_reference(db, intake_id=intake_id, ref=ref, entity_id=queued_entity_id)
    if anchor is None:
        return None, ("the delivery has no single delivered record carrying this reference and linking the queued "
                      "entity (missing, duplicated, or linked to another entity)")
    record = await db.get(m.RceSourceRecord, anchor)
    if record is None:
        return None, "the delivered record could not be read"
    delivered = delivered_npi_of(record)
    if npi_key(probe_npi) and npi_key(delivered) != npi_key(probe_npi):
        # The probes would be asked about an NPI this delivery did not submit. (No probe NPI at all is not a
        # mismatch: nothing NPI-derived can be produced, the NPI-keyed lookups answer "unavailable" fail-closed.)
        return None, ("the source probes would use an NPI this delivery did not submit; nothing was gathered or "
                      "recorded for this delivery")
    return {"source_record_id": anchor, "intake_id": intake_id, "delivered_npi": delivered}, None


async def _curated_for_anchor(db, entity_id, source_record_id, intake_id=None):
    """The curated record of the NAMED source record, only if it links `entity_id` (and belongs to `intake_id` when
    the caller knows the delivery); otherwise None. Never another delivery's record for the same entity."""
    if source_record_id is None:
        return None
    stmt = select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_record_id == source_record_id,
        m.RceCuratedRecord.canonical_entity_id == entity_id)
    if intake_id is not None:
        stmt = stmt.where(m.RceCuratedRecord.source_intake_id == intake_id)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()


async def record_npi_outcome(db, *, entity_id, npi: Optional[str], outcome: str,
                             detail: Optional[Any] = None,
                             commit: bool = False,
                             source_record_id=None,
                             intake_id=None) -> Optional[m.RceIssue]:
    """Write the ledger row for one verification outcome. Returns it, or None.

    ANCHORED: the row is written on the curated record of `source_record_id` - the source record the verification
    is about - validated against `entity_id` (and `intake_id` when the caller knows the delivery). The same record
    serves insertion and duplicate detection. WITHOUT an established anchor nothing is written: an entity can be
    promoted by several deliveries, and "the entity's latest record" would put one delivery's finding on another's
    ledger.

    None when the outcome is VERIFIED (nothing to record), when there is no anchor (or it does not match the
    entity/delivery), or when an identical undecided issue already exists for the record (no duplicate). Does not
    commit unless asked: the caller's verification transaction owns the commit.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown NPI outcome {outcome!r}; one of {OUTCOMES}")
    if isinstance(detail, dict):
        detail_text = str(detail.get("detail") or detail)
        deactivation_date = detail.get("deactivation_date")
    else:
        detail_text = str(detail or "")
        deactivation_date = None

    if outcome == VERIFIED:
        return None

    if source_record_id is None:
        logger.warning("NPI outcome %s for entity %s has no delivery-record anchor (intake_id=%s); no ledger row "
                       "written, no identifier status changed", outcome, entity_id, intake_id)
        return None

    # Protect anchor reads as well as writes: a failed best-effort lookup must not abort the caller's transaction.
    # begin_nested flushes pending caller state first; only operations inside this savepoint are rolled back here.
    async with db.begin_nested():
        # VALIDATE FIRST: nothing below - the identifier-status change included - happens without an established anchor
        # and an NPI that anchored record delivered (CodeRabbit on #233 round 6: the status change used to run before
        # the anchor was validated, for every caller).
        curated = await _curated_for_anchor(db, entity_id, source_record_id, intake_id)
        if curated is None:
            logger.warning("NPI outcome %s for entity %s has no established delivery-record anchor (source_record_id=%s, "
                           "intake_id=%s); no ledger row written, no identifier status changed",
                           outcome, entity_id, source_record_id, intake_id)
            return None
        record = await db.get(m.RceSourceRecord, curated.source_record_id)
        if npi_key(npi) and (record is None or npi_key(delivered_npi_of(record)) != npi_key(npi)):
            # A finding ABOUT an NPI belongs only to a record that delivered that NPI (callers hold the registry
            # entity's NPI, which another delivery may have supplied). An explicit finding reported with no NPI at all
            # (post-promotion findings) is not compared; the NPPES-derived helpers below never report one without an NPI.
            logger.warning("NPI outcome %s for entity %s is about an NPI the anchored record (%s) did not deliver; no "
                           "ledger row written, no identifier status changed", outcome, entity_id,
                           curated.source_record_id)
            return None

        # ONE form of the NPI from here on - the digits (`npi_key`): the identifier flip, the duplicate check and the
        # stored `original_value` all use it, so a reported "1234567893.0" cannot write a row that claims a flip it did
        # not make. A value with no digits ("n/a") is no NPI.
        npi = npi_key(npi) or None
        if outcome == NPI_DEACTIVATED and npi is None:
            logger.warning("NPI_DEACTIVATED for entity %s was reported with no NPI; a deactivation is about an NPI - no "
                           "ledger row written", entity_id)
            return None

        # The identifier-status change and the ledger row succeed or fail TOGETHER: a savepoint owned by the writer, so
        # a caller without its own (arc_pipeline, review_service) cannot commit a flushed status change without its row.
        issue = await _write_npi_outcome(db, entity_id=entity_id, npi=npi, outcome=outcome, curated=curated,
                                         detail_text=detail_text, deactivation_date=deactivation_date)
    if commit and issue is not None:
        await db.commit()
    return issue


async def _write_npi_outcome(db, *, entity_id, npi, outcome, curated, detail_text, deactivation_date):
    if outcome == NPI_DEACTIVATED:
        await _mark_identifier_inactive(db, entity_id, npi)

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
    logger.info("NPI verification outcome recorded",
                extra={"entity_id": str(entity_id), "outcome": outcome,
                       "issue_code": issue.issue_code})
    return issue


async def _mark_identifier_inactive(db, entity_id, npi: str) -> int:
    """Set the entity's matching ACTIVE NPI identifier rows to the
    inactive-pending-review status; returns how many changed. Never deletes.
    """
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


SAM_EXTRACT_CANDIDATE = "SAM_EXTRACT_CANDIDATE"


def sam_extract_candidate_values(evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The extract screen's `original_values` from an assembled evidence
    bundle, when (and only when) its row is a CANDIDATE (disposition REVIEW).

    Reads the D3 dimension's SAM_EXTRACT_SUPPLEMENTARY item exactly as
    `sam_extract_evidence_items` writes it. None for a PASS (no candidate),
    an UNAVAILABLE row, or a bundle without the extract item at all — the
    same "None means nothing to record" contract `npi_outcome_from_evidence`
    holds. Never derives a finding from absence: a missing candidate is not
    clearance and records nothing.
    """
    for dimension in evidence.get("dimensions") or []:
        dim_evidence = dimension.get("evidence")
        if not isinstance(dim_evidence, list):
            continue
        for item in dim_evidence:
            if not isinstance(item, dict):
                continue
            if item.get("source") != "SAM_EXTRACT_SUPPLEMENTARY":
                continue
            if item.get("disposition") != "REVIEW":
                return None
            values = item.get("original_values")
            return dict(values) if isinstance(values, dict) else {}
    return None


async def record_sam_extract_candidate(db, *, entity_id, npi: Optional[str],
                                       org_name: Optional[str] = None,
                                       extract: Optional[Dict[str, Any]] = None,
                                       intake_id=None,
                                       source_record_id=None,
                                       ) -> Optional[m.RceIssue]:
    """Write the ledger finding for one SAM public-extract CANDIDATE match.

    The evidence row (`SAM_EXTRACT_SUPPLEMENTARY`, disposition REVIEW) is the
    record of what the screen saw; this row is what puts it on the analyst's
    worklist — same split as the NPI outcomes above.

    ANCHORING IS DELIVERY-SCOPED. An entity can carry curated records in
    several deliveries (promotion reuses the canonical entity across
    intakes), and the finding must land on the ledger of the delivery whose
    screen found it — not whichever delivery happened to promote the entity
    last. Callers that know the delivery pass `intake_id` (both coverage
    paths do) and/or the exact representative `source_record_id` (the
    backfill does); the unscoped latest-record fallback remains only for a
    caller with neither.

    Idempotent at the ENTITY-per-DELIVERY grain: an undecided EXC-001
    finding already open on ANY of the entity's curated records within the
    same delivery is returned as-is, never duplicated — the grain is one
    open worklist item per entity per delivery (repeat screens may flip the
    lookup basis between NPI and name, and a multi-record entity has several
    curated rows; neither may double the worklist). After an analyst DECIDES
    a finding, a LATER candidate hit opens a new one — new information,
    same semantics as every other verification finding here.

    Never confirms anything: the finding is a candidate requiring human
    determination, and its absence is never exclusion clearance. Does not
    commit; the caller's verification transaction owns the commit.
    """
    extract = extract or {}
    lookup_value = (npi or "").strip() or (org_name or "").strip() or None

    stmt = select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.canonical_entity_id == entity_id)
    if source_record_id is not None:
        stmt = stmt.where(m.RceCuratedRecord.source_record_id == source_record_id)
    if intake_id is not None:
        stmt = stmt.where(m.RceCuratedRecord.source_intake_id == intake_id)
    curated = (await db.execute(
        stmt.order_by(m.RceCuratedRecord.promoted_at.desc().nullslast(),
                      m.RceCuratedRecord.created_at.desc())
        .limit(1))).scalar_one_or_none()
    if curated is None and (source_record_id is not None or intake_id is not None):
        # A mis-stated scope is a caller bug; falling back to "any delivery"
        # would write the finding onto the wrong ledger, which is worse than
        # writing none (the evidence row still exists either way).
        logger.warning("SAM-extract candidate for entity %s: no curated record "
                       "in the stated scope; finding not written", entity_id)
        return None
    if curated is None:
        curated = await _curated_for_entity(db, entity_id)
    if curated is None:
        logger.info("SAM-extract candidate for entity %s has no delivery ledger; "
                    "recorded in the evidence row only", entity_id)
        return None

    rule_id, severity, authority = NON_QUALITY_ISSUE_TYPES[SAM_EXTRACT_CANDIDATE]
    same_delivery_records = select(m.RceCuratedRecord.source_record_id).where(
        m.RceCuratedRecord.canonical_entity_id == entity_id,
        m.RceCuratedRecord.source_intake_id == curated.source_intake_id)
    existing = (await db.execute(
        select(m.RceIssue).where(
            m.RceIssue.source_record_id.in_(same_delivery_records),
            m.RceIssue.rule_id == rule_id,
            m.RceIssue.issue_type == SAM_EXTRACT_CANDIDATE,
            m.RceIssue.resolution.in_(sorted(UNDECIDED_RESOLUTIONS)))
        .limit(1))).scalar_one_or_none()
    if existing is not None:
        return existing

    current_run = await run_selection.current_run(db, curated.source_intake_id)
    edition = extract.get("publication_date") or "not recorded"
    sha = (extract.get("extract_sha256") or "")[:12] or "not recorded"
    methods = ", ".join(extract.get("match_methods") or []) or "npi/normalized_business_name"
    count = extract.get("candidate_count")
    issue = m.RceIssue(
        id=uuid.uuid4(),
        issue_code=verification_issue_code(),
        source_intake_id=curated.source_intake_id,
        source_record_id=curated.source_record_id,
        run_id=current_run.id if current_run is not None else None,
        rule_id=rule_id,
        rule_version=RULE_BY_ID[rule_id].version,
        issue_type=SAM_EXTRACT_CANDIDATE,
        severity=severity,
        field_name="exclusion_screening",
        original_value=lookup_value,
        suggested_value=None,
        suggested_source="SAM_EXTRACT_SUPPLEMENTARY",
        suggested_confidence=None,
        correction_authority=authority,
        description=(
            f"Potential match in the GSA public SAM exclusions extract "
            f"(edition {edition}, sha256 {sha}, "
            f"{count if count is not None else 'unrecorded number of'} candidate "
            f"row(s), matched by {methods}). A CANDIDATE only — never a "
            f"confirmed exclusion; an analyst must confirm or rule it out. "
            f"This supplementary screen does not replace the api.sam.gov "
            f"primary check, and a non-match is never exclusion clearance."),
        resolution="OPEN",
        created_at=datetime.utcnow(),
    )
    db.add(issue)
    await db.flush()
    logger.info("SAM-extract candidate finding recorded",
                extra={"entity_id": str(entity_id), "issue_code": issue.issue_code})
    return issue


async def record_from_sources(db, *, entity_id, sources: Dict[str, Any],
                              source_record_id=None, intake_id=None
                              ) -> Optional[m.RceIssue]:
    """Convenience for `review_service`: read the probed NPPES source dict."""
    nppes = (sources or {}).get("nppes") or {}
    outcome = nppes.get("npi_outcome")
    if not outcome:
        return None
    if not npi_key(nppes.get("lookup_identifier")):
        return None          # an NPPES-derived outcome with no NPI is about nothing: never a ledger row with npi=None
    return await record_npi_outcome(
        db, entity_id=entity_id, npi=nppes.get("lookup_identifier"),
        outcome=outcome, detail=nppes.get("npi_outcome_detail") or nppes.get("reason"),
        source_record_id=source_record_id, intake_id=intake_id)


async def record_from_evidence(db, *, entity_id, npi: Optional[str],
                               evidence: Dict[str, Any],
                               source_record_id=None, intake_id=None) -> Optional[m.RceIssue]:
    """Convenience for the verification callers; they name the source record the evidence is about
    (see `record_npi_outcome` - no anchor, no ledger write)."""
    derived = npi_outcome_from_evidence(evidence)
    if derived is None:
        return None
    # No NPI on the entity: there was nothing for NPPES to verify. That is a
    # fact about the entity (many TEFCA entities legitimately hold no NPI),
    # not a source outage and not a finding. Without this, every NPI-less
    # record would get an NPI_VERIFICATION_UNAVAILABLE issue -- one technical
    # non-event manufactured into a finding per record (2026-10-04, found the
    # moment the dimension-name fix above made this function live).
    # The same holds for every NPPES-derived outcome (2026-11-16): with no NPI there is nothing the outcome is about,
    # so no ledger row is written with npi=None (a both-blank NOT_FOUND used to produce one).
    if not npi_key(npi):
        return None
    return await record_npi_outcome(db, entity_id=entity_id, npi=npi,
                                    outcome=derived["outcome"], detail=derived,
                                    source_record_id=source_record_id, intake_id=intake_id)
