"""
P6 + P7 — the Curated Working Dataset and the human/auto correction gate.

NOT "THE FIXED FILE"
────────────────────
Area 2 is a WORKING dataset. It normalises, corrects, enriches and reconciles,
and every row points back to exactly one Area 1 source row. Area 1 remains the
record of what was delivered; nothing here edits it.

THE GATE
────────
    AUTO_SAFE       applied automatically. Confined to deterministic,
                    non-substantive normalisation: whitespace, state-code case,
                    ZIP zero-padding. Enforced against an explicit allow-list of
                    RULE IDS, not against the authority string alone, so a
                    mislabelled finding cannot smuggle itself through.
    HUMAN_REQUIRED  a reviewer must approve. Identity, organisation name,
                    entity type, relationship, substantive address.
    QA_REQUIRED     reviewer AND QA. Critical severity, cross-record impact.
    NO_CORRECTION   the issue is recorded; the value is preserved as delivered.

CONFIDENCE IS NOT AUTHORITY. A HIGH-confidence NPI suggestion is still
HUMAN_REQUIRED. The two fields never influence one another.

THE STALENESS GUARD
───────────────────
A reviewer approves a correction against a value they read. `original_value_hash`
records what that value was. If the value has changed by the time the correction
is applied, the approval was given for something else — applying it would
attribute a decision to a human who never made it. `apply_correction` re-checks
the hash and INVALIDATES the approval on mismatch rather than proceeding.

HELD RECORDS DO NOT ENTER VERIFICATION
A record with an unresolved substantive problem is HELD. `promotion.py` promotes
only CLEAN and CORRECTED records, so a held record cannot reach ARC verification
by any path that does not first resolve its issues.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import FIELD_MAP_VERSION
from app.tefca_registry.rce.quality_rules import AUTO_SAFE_RULES

logger = logging.getLogger(__name__)

TRANSFORMATION_VERSION = f"curation-1.0.0/map-{FIELD_MAP_VERSION}"

BATCH_SIZE = 2000

AUTO_SAFE = "AUTO_SAFE"
HUMAN_REQUIRED = "HUMAN_REQUIRED"
QA_REQUIRED = "QA_REQUIRED"
NO_CORRECTION = "NO_CORRECTION"

CLEAN, CORRECTED, HELD, REJECTED = "CLEAN", "CORRECTED", "HELD", "REJECTED"

#: Severities that HOLD a record until a human resolves them. A record carrying
#: an unresolved issue at one of these levels never reaches verification.
HOLDING_SEVERITIES = frozenset({"CRITICAL", "HIGH"})

#: Fields whose modification is an identity or relationship change. Listed
#: explicitly so the AUTO_SAFE guard is a membership test rather than a
#: judgement call made per rule.
SUBSTANTIVE_FIELDS = frozenset({
    "id", "NPI", "TEFCAID", "HCID", "AAID", "name", "sequoiaorgtype",
    "partOf", "orgManagingOrg", "active", "address_line", "address_city",
})


class CorrectionRefused(RuntimeError):
    """A correction was refused. The reason is always specific."""


def value_hash(value: Optional[str]) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def is_auto_safe(issue) -> Tuple[bool, str]:
    """Whether an issue may be applied without a human, and why not if not.

    THREE independent conditions, all required. The rule-id allow-list is the
    one that matters most: it means a finding cannot become auto-applicable
    merely by carrying the AUTO_SAFE string, which a future rule could set by
    mistake.
    """
    if issue.correction_authority != AUTO_SAFE:
        return False, (f"correction_authority is {issue.correction_authority}, "
                       f"not AUTO_SAFE")
    if issue.rule_id not in AUTO_SAFE_RULES:
        return False, (f"rule {issue.rule_id} is not in the AUTO_SAFE allow-list "
                       f"{sorted(AUTO_SAFE_RULES)}. An issue cannot become "
                       f"auto-applicable merely by declaring itself AUTO_SAFE.")
    if issue.field_name in SUBSTANTIVE_FIELDS and issue.rule_id != "FMT-004":
        return False, (f"{issue.field_name} is a substantive field; changing it "
                       f"is an identity or relationship edit and requires a "
                       f"human whatever the rule's confidence.")
    if issue.suggested_value is None:
        return False, "no suggested value to apply"
    return True, ""


# ── curation ─────────────────────────────────────────────────────────────────

def _canonical_entity_level(sequoia: str) -> str:
    return {"Participant": "participant",
            "Subparticipant": "sub_participant"}.get(sequoia, "participant")


def _split_purposes(value: str) -> List[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _contact_block(values: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in values.items()
            if k.startswith("contact_") and (v or "").strip()}


def _rce_attributes(values: Dict[str, str]) -> Dict[str, str]:
    keep = ("domains", "initiatoronly", "stateofoperation", "doa",
            "delegationRole", "phone", "email", "alias", "address_text",
            "address_country", "transaction", "NAIC", "CCN")
    return {k: values.get(k, "") for k in keep if (values.get(k) or "").strip()}


def build_curated_row(record, values: Dict[str, str], *,
                      issues: List[Any]) -> Dict[str, Any]:
    """Project one source record into its curated shape, pre-correction."""
    from app.tefca_registry.rce.quality_rules import _TEST_NAME_PATTERN

    sequoia = (values.get("sequoiaorgtype") or "").strip()
    active_raw = (values.get("active") or "").strip()
    is_active = active_raw != "0"
    name = (values.get("name") or "").strip()

    return {
        "source_record_id": record.id,
        "source_intake_id": record.source_intake_id,
        "rce_org_oid": (values.get("id") or "").strip() or None,
        "tefcaid": (values.get("TEFCAID") or "").strip() or None,
        "hcid": (values.get("HCID") or "").strip() or None,
        "aaid": (values.get("AAID") or "").strip() or None,
        "npi": (values.get("NPI") or "").strip() or None,
        "name": name or None,
        "entity_level": _canonical_entity_level(sequoia),
        "sequoia_org_type": sequoia or None,
        "org_node_type": (values.get("organizationNodeType") or "").strip() or None,
        "hl7_org_role": (values.get("hl7orgrole") or "").strip() or None,
        "operational_status": "active" if is_active else "inactive",
        "is_active": is_active,
        "address_line": (values.get("address_line") or "").strip() or None,
        "address_city": (values.get("address_city") or "").strip() or None,
        "address_state": (values.get("address_state") or "").strip() or None,
        "address_postal_code": (values.get("address_postalCode") or "").strip() or None,
        "address_country": (values.get("address_country") or "").strip() or None,
        "exchange_purposes": _split_purposes(values.get("purposesofuse", "")),
        "part_of": (values.get("partOf") or "").strip() or None,
        "org_managing_org": (values.get("orgManagingOrg") or "").strip() or None,
        "contact": _contact_block(values),
        "rce_attributes": _rce_attributes(values),
        "is_test_record": bool(name and _TEST_NAME_PATTERN.search(name)),
        "transformation_version": TRANSFORMATION_VERSION,
    }


#: Curated column that a given RCE field maps to, for applying a correction.
_FIELD_TO_CURATED_COLUMN = {
    "address_postalCode": "address_postal_code",
    "address_state": "address_state",
    "address_line": "address_line",
    "address_city": "address_city",
    "address_text": None,      # preserved in rce_attributes; not a curated column
    "name": "name",
    "NPI": "npi",
    "TEFCAID": "tefcaid",
    "HCID": "hcid",
    "partOf": "part_of",
    "orgManagingOrg": "org_managing_org",
    "sequoiaorgtype": "sequoia_org_type",
    "active": "operational_status",
}


async def curate_delivery(db, intake_id, *, run_id=None,
                          curated_by: str = "SYSTEM") -> Dict[str, Any]:
    """Build Area 2 for a delivery and apply AUTO_SAFE corrections.

    Exactly one curated record per source record. Status is decided by the
    issues that remain OPEN at holding severity — a record with an unresolved
    CRITICAL or HIGH issue is HELD, and HELD records never reach verification.
    """
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ValueError(f"No intake {intake_id}")

    # Issues by source record, fetched once.
    issue_rows = (await db.execute(
        select(m.RceIssue).where(
            m.RceIssue.source_intake_id == intake_id,
            m.RceIssue.source_record_id.isnot(None)))).scalars().all()
    by_record: Dict[Any, List[Any]] = {}
    for issue in issue_rows:
        by_record.setdefault(issue.source_record_id, []).append(issue)

    total = int((await db.execute(
        select(func.count()).select_from(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == intake_id))).scalar() or 0)

    created = 0
    corrections_applied = 0
    status_counts: Dict[str, int] = {CLEAN: 0, CORRECTED: 0, HELD: 0, REJECTED: 0}
    now = datetime.utcnow()

    for offset in range(0, total, BATCH_SIZE):
        records = (await db.execute(
            select(m.RceSourceRecord)
            .where(m.RceSourceRecord.source_intake_id == intake_id)
            .order_by(m.RceSourceRecord.line_number)
            .limit(BATCH_SIZE).offset(offset))).scalars().all()

        curated_rows: List[Dict[str, Any]] = []
        correction_rows: List[Dict[str, Any]] = []

        for record in records:
            values = dict(record.parsed or {})
            issues = by_record.get(record.id, [])
            row = build_curated_row(record, values, issues=issues)
            row["id"] = __import__("uuid").uuid4()
            row["created_at"] = now
            # Every row carries EVERY key, including the ones that stay None.
            # A bulk insert compiles one statement for the whole batch and
            # binds by key, so a dict that omits a column raises rather than
            # defaulting — and a per-row-shaped insert would silently become a
            # row-at-a-time loop over 23,566 records.
            row.setdefault("status_reason", None)
            row.setdefault("record_status", CLEAN)
            row.setdefault("issue_count", 0)
            row.setdefault("correction_count", 0)
            row.setdefault("canonical_entity_id", None)
            row.setdefault("promoted_at", None)
            row.setdefault("reviewed_by", None)
            row.setdefault("reviewed_at", None)

            # A row that could not be parsed is REJECTED — its values cannot be
            # trusted positionally. It is still in Area 1, still counted, and
            # still reconcilable; it simply does not proceed.
            if record.parse_status != "ok":
                row["record_status"] = REJECTED
                row["status_reason"] = (
                    f"Source line could not be parsed ({record.parse_status}). "
                    f"Preserved in Area 1; not curated, because positional "
                    f"values cannot be trusted.")
                row["issue_count"] = len(issues)
                row["correction_count"] = 0
                curated_rows.append(row)
                status_counts[REJECTED] += 1
                created += 1
                continue

            applied = 0
            for issue in issues:
                safe, _reason = is_auto_safe(issue)
                if not safe or issue.resolution != "OPEN":
                    continue
                column = _FIELD_TO_CURATED_COLUMN.get(issue.field_name)
                if column is None or column not in row:
                    continue
                original = row.get(column)
                # Apply only when the value still matches what the rule saw.
                if issue.original_value is not None and \
                        (original or "") != issue.original_value.strip():
                    continue
                correction_rows.append({
                    "id": __import__("uuid").uuid4(),
                    "curated_record_id": row["id"],
                    "source_record_id": record.id,
                    "issue_id": issue.id,
                    "column_name": column,
                    "original_value": original,
                    "original_value_hash": value_hash(original),
                    "corrected_value": issue.suggested_value,
                    "correction_reason": (
                        f"{issue.rule_id}: {issue.issue_type}. Deterministic "
                        f"non-substantive normalisation applied automatically."),
                    "correction_rule_id": issue.rule_id,
                    "correction_authority": AUTO_SAFE,
                    "corrected_by": curated_by,
                    "approval_actor": None,
                    "confidence": issue.suggested_confidence,
                    "qa_status": None,
                    "created_at": now,
                })
                row[column] = issue.suggested_value
                applied += 1

            blocking = [i for i in issues
                        if i.resolution == "OPEN" and i.severity in HOLDING_SEVERITIES]
            row["issue_count"] = len(issues)
            row["correction_count"] = applied
            if blocking:
                row["record_status"] = HELD
                row["status_reason"] = (
                    f"{len(blocking)} unresolved issue(s) at "
                    f"{'/'.join(sorted({i.severity for i in blocking}))} severity: "
                    f"{', '.join(sorted({i.rule_id for i in blocking}))}. Held "
                    f"from verification until resolved.")
                status_counts[HELD] += 1
            elif applied:
                row["record_status"] = CORRECTED
                row["status_reason"] = (
                    f"{applied} AUTO_SAFE correction(s) applied; no unresolved "
                    f"issue at holding severity.")
                status_counts[CORRECTED] += 1
            else:
                row["record_status"] = CLEAN
                row["status_reason"] = None
                status_counts[CLEAN] += 1

            curated_rows.append(row)
            corrections_applied += applied
            created += 1

        if curated_rows:
            await db.execute(m.RceCuratedRecord.__table__.insert(), curated_rows)
        if correction_rows:
            await db.execute(m.RceCorrectionDetail.__table__.insert(),
                             correction_rows)

    await db.commit()

    stored = int((await db.execute(
        select(func.count()).select_from(m.RceCuratedRecord)
        .where(m.RceCuratedRecord.source_intake_id == intake_id))).scalar() or 0)

    return {
        "intake_id": str(intake_id),
        "run_id": str(run_id) if run_id else None,
        "source_records": total,
        "curated_records": stored,
        "every_source_record_curated": stored == total,
        "status_counts": status_counts,
        "auto_safe_corrections_applied": corrections_applied,
        "transformation_version": TRANSFORMATION_VERSION,
    }


# ── P7 — the human gate ──────────────────────────────────────────────────────

_ALLOWED_TRANSITIONS = {
    "OPEN": {"PROPOSED", "UNDER_REVIEW", "WAIVED", "REJECTED"},
    "PROPOSED": {"UNDER_REVIEW", "APPROVED", "REJECTED", "WAIVED"},
    "UNDER_REVIEW": {"APPROVED", "REJECTED", "WAIVED"},
    "APPROVED": {"RESOLVED"},
    "REJECTED": {"RESOLVED"},
    "WAIVED": {"RESOLVED"},
    "RESOLVED": set(),
}


async def transition_issue(db, issue_id, *, to_status: str, actor: str,
                           notes: Optional[str] = None,
                           qa_actor: Optional[str] = None) -> Dict[str, Any]:
    """Move an issue through the resolution workflow.

    QA_REQUIRED issues cannot reach APPROVED without a QA actor DISTINCT from
    the reviewer. Allowing one person to be both would make the second approval
    a formality, which is the opposite of what a two-person control is for.
    """
    issue = await db.get(m.RceIssue, issue_id)
    if issue is None:
        raise CorrectionRefused(f"No issue {issue_id}")

    current = issue.resolution or "OPEN"
    if to_status not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise CorrectionRefused(
            f"Cannot move an issue from {current} to {to_status}. Allowed from "
            f"{current}: {sorted(_ALLOWED_TRANSITIONS.get(current, set()))}.")

    if to_status == "APPROVED":
        if issue.correction_authority == NO_CORRECTION:
            raise CorrectionRefused(
                f"Issue {issue.issue_code} is NO_CORRECTION: the finding is "
                f"recorded as evidence and the delivered value is preserved. "
                f"There is nothing to approve.")
        if issue.correction_authority == QA_REQUIRED:
            if not qa_actor:
                raise CorrectionRefused(
                    f"Issue {issue.issue_code} is QA_REQUIRED and needs a QA "
                    f"approver in addition to the reviewer.")
            if qa_actor == actor:
                raise CorrectionRefused(
                    f"QA approval must come from someone other than the "
                    f"reviewer ({actor}). A single person supplying both "
                    f"approvals defeats the control.")
            issue.qa_approved_by = qa_actor
            issue.qa_approved_at = datetime.utcnow()

    issue.resolution = to_status
    issue.resolved_by = actor
    issue.resolved_at = datetime.utcnow()
    if notes:
        issue.resolution_notes = notes
    await db.commit()
    return {
        "issue_id": str(issue.id), "issue_code": issue.issue_code,
        "resolution": issue.resolution, "resolved_by": issue.resolved_by,
        "qa_approved_by": issue.qa_approved_by,
        "correction_authority": issue.correction_authority,
    }


async def apply_correction(db, issue_id, *, actor: str,
                           corrected_value: Optional[str] = None) -> Dict[str, Any]:
    """Apply an APPROVED correction to its curated record.

    THE STALENESS GUARD LIVES HERE. The current value is re-hashed and compared
    against what the issue recorded. On mismatch the approval is INVALIDATED and
    the issue is returned to UNDER_REVIEW — because the human approved a change
    to a value that no longer exists, and applying it anyway would put their
    name on a decision they did not make.
    """
    issue = await db.get(m.RceIssue, issue_id)
    if issue is None:
        raise CorrectionRefused(f"No issue {issue_id}")
    if issue.resolution != "APPROVED":
        raise CorrectionRefused(
            f"Issue {issue.issue_code} is {issue.resolution}, not APPROVED. "
            f"Only an approved correction may be applied.")
    if issue.correction_authority == NO_CORRECTION:
        raise CorrectionRefused(
            f"Issue {issue.issue_code} is NO_CORRECTION and must not be applied.")

    curated = (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_record_id == issue.source_record_id)
    )).scalar_one_or_none()
    if curated is None:
        raise CorrectionRefused(
            f"No curated record for source record {issue.source_record_id}.")

    column = _FIELD_TO_CURATED_COLUMN.get(issue.field_name)
    if column is None:
        raise CorrectionRefused(
            f"Field {issue.field_name!r} does not map to a curated column.")

    current = getattr(curated, column, None)
    expected_hash = value_hash(issue.original_value)
    if value_hash(current) != expected_hash:
        issue.resolution = "UNDER_REVIEW"
        issue.resolution_notes = (
            f"Approval invalidated before application: the value of {column} "
            f"changed after approval (approved against "
            f"{issue.original_value!r}, found {current!r}). Re-review required — "
            f"the approval was given for a value that no longer exists.")
        await db.commit()
        raise CorrectionRefused(issue.resolution_notes)

    new_value = corrected_value if corrected_value is not None else issue.suggested_value
    if new_value is None:
        raise CorrectionRefused(
            f"Issue {issue.issue_code} carries no corrected value to apply.")

    db.add(m.RceCorrectionDetail(
        curated_record_id=curated.id,
        source_record_id=issue.source_record_id,
        issue_id=issue.id,
        column_name=column,
        original_value=current,
        original_value_hash=value_hash(current),
        corrected_value=new_value,
        correction_reason=(
            f"{issue.rule_id}: {issue.issue_type}. Approved by "
            f"{issue.resolved_by}"
            + (f", QA {issue.qa_approved_by}" if issue.qa_approved_by else "")),
        correction_rule_id=issue.rule_id,
        correction_authority=issue.correction_authority,
        corrected_by=actor,
        approval_actor=issue.resolved_by,
        confidence=issue.suggested_confidence,
        qa_status="APPROVED" if issue.qa_approved_by else None,
    ))
    setattr(curated, column, new_value)
    curated.correction_count = (curated.correction_count or 0) + 1
    curated.record_status = CORRECTED
    curated.reviewed_by = actor
    curated.reviewed_at = datetime.utcnow()
    issue.resolution = "RESOLVED"
    await db.commit()

    return {
        "issue_id": str(issue.id), "issue_code": issue.issue_code,
        "curated_record_id": str(curated.id), "column": column,
        "original_value": current, "corrected_value": new_value,
        "correction_authority": issue.correction_authority,
    }


async def recompute_hold_status(db, intake_id) -> Dict[str, Any]:
    """Re-derive CLEAN/CORRECTED/HELD after issues have been resolved.

    A record stops being HELD when nothing at holding severity remains OPEN. Run
    after a batch of analyst resolutions so promotion sees current state.
    """
    curated = (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_intake_id == intake_id))).scalars().all()
    open_rows = (await db.execute(
        select(m.RceIssue.source_record_id, m.RceIssue.severity)
        .where(m.RceIssue.source_intake_id == intake_id,
               m.RceIssue.resolution == "OPEN"))).all()
    blocking: Dict[Any, int] = {}
    for record_id, severity in open_rows:
        if severity in HOLDING_SEVERITIES and record_id is not None:
            blocking[record_id] = blocking.get(record_id, 0) + 1

    changed = 0
    for row in curated:
        if row.record_status == REJECTED:
            continue
        should_hold = row.source_record_id in blocking
        if should_hold and row.record_status != HELD:
            row.record_status = HELD
            changed += 1
        elif not should_hold and row.record_status == HELD:
            row.record_status = CORRECTED if row.correction_count else CLEAN
            row.status_reason = "All holding-severity issues resolved."
            changed += 1
    await db.commit()
    return {"curated_records": len(curated), "status_changed": changed,
            "still_held": len(blocking)}
