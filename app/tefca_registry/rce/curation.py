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
from app.tefca_registry.rce import run_selection
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

#: Resolutions that mean the question is still open in substance. PROPOSED is in
#: here deliberately: the state machine is
#:     OPEN -> PROPOSED -> APPROVED | REJECTED | WAIVED -> RESOLVED
#: so PROPOSED is an analyst's suggestion that nobody has decided on. Keying the
#: hold on OPEN alone released a record the moment an analyst touched it, which
#: is the opposite of what holding is for. A NULL resolution counts as undecided
#: so the predicate fails closed.
UNDECIDED_RESOLUTIONS = frozenset({"OPEN", "PROPOSED", "UNDER_REVIEW"})


def blocks_promotion(severity: Optional[str], resolution: Optional[str]) -> bool:
    """Does this issue hold its record back from promotion and verification?

    One definition, used by both curate_delivery (at curation time) and
    recompute_hold_status (afterwards). They set the same flag from different
    code paths, and the original defect existed in only one of them - which is
    what happens when the rule gets written out twice.
    """
    return (severity in HOLDING_SEVERITIES
            and (resolution or "OPEN") in UNDECIDED_RESOLUTIONS)

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
    # 1.3.0: order-preserving, de-duplicated, tolerant of ; | and whitespace
    # separators. Identical to the comma split for every July/September token.
    from app.tefca_registry.rce.field_map import split_purpose_tokens
    return split_purpose_tokens(value)


def _contact_block(values: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in values.items()
            if k.startswith("contact_") and (v or "").strip()}


def _rce_attributes(values: Dict[str, str]) -> Dict[str, str]:
    keep = ("domains", "initiatoronly", "stateofoperation", "doa",
            "delegationRole", "phone", "email", "alias", "address_text",
            "address_country", "transaction", "NAIC", "CCN")
    out = {k: values.get(k, "") for k in keep if (values.get(k) or "").strip()}
    # 1.3.0: normalised projections beside the raw values they came from.
    # The raw cell is never replaced; `active_raw`/`purposesofuse_raw` keep the
    # delivered text on the curated row so a reviewer sees both without Area 1.
    from app.tefca_registry.rce.field_map import normalize_active, normalize_naic
    if (values.get("NAIC") or "").strip():
        out["NAIC_normalized"] = normalize_naic(values.get("NAIC"))["normalized"]
    active_raw = (values.get("active") or "").strip()
    if active_raw:
        out["active_raw"] = active_raw
        out["active_normalized"] = normalize_active(active_raw)
    if (values.get("purposesofuse") or "").strip():
        out["purposesofuse_raw"] = values.get("purposesofuse", "").strip()
    return out


def build_curated_row(record, values: Dict[str, str], *,
                      issues: List[Any]) -> Dict[str, Any]:
    """Project one source record into its curated shape, pre-correction."""
    from app.tefca_registry.rce.quality_rules import _TEST_NAME_PATTERN

    from app.tefca_registry.rce.field_map import normalize_active

    sequoia = (values.get("sequoiaorgtype") or "").strip()
    active_raw = (values.get("active") or "").strip()
    # 1.3.0: "0.0"/"1.0" (spreadsheet round-trip) curate as "0"/"1". An empty
    # or unsupported value is HELD by CON-003, so its projection never promotes.
    is_active = normalize_active(active_raw) != "0"
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
    "AAID": "aaid",
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

    # Issues by source record, fetched once — from ONE quality run.
    #
    # A delivery may be quality-run more than once, and every run writes a full
    # set of issues. Filtering on the intake alone would build Area 2 from two
    # assessments at once: issue_count doubled, and a record held by a finding
    # that the newer run no longer makes. `run_id` was already a parameter here
    # and was only echoed back; this is what it was for.
    issue_rows = (await db.execute(
        select(m.RceIssue).where(
            run_selection.issues_filter(intake_id, run_id=run_id),
            m.RceIssue.source_record_id.isnot(None)))).scalars().all()
    by_record: Dict[Any, List[Any]] = {}
    for issue in issue_rows:
        by_record.setdefault(issue.source_record_id, []).append(issue)

    total = int((await db.execute(
        select(func.count()).select_from(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == intake_id))).scalar() or 0)

    # RE-RUN GUARD: `uq_rce_curated_source_record` (one curated row per source
    # row) makes a second curation of the same delivery fail at the database,
    # never absorb silently — tests/test_curation_rerun_invariant.py holds
    # that contract. No code-level short-circuit is layered on top of it.

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
                        if blocks_promotion(i.severity, i.resolution)]
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
    if column == "npi":
        # A correction is a human write of an identifier; it passes the same
        # validator promotion applies (review finding L-1, 2026-09-16).
        from app.services.npi_validator import validate_npi
        ok, message = validate_npi(new_value)
        if not ok:
            raise CorrectionRefused(
                f"Corrected NPI {new_value!r} is not a valid NPI ({message}); a "
                f"correction cannot register an invalid identifier.")

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
    curated.reviewed_by = actor
    curated.reviewed_at = datetime.utcnow()
    issue.resolution = "RESOLVED"
    await db.flush()
    # The corrected record is promotable only if NOTHING ELSE holds it. An
    # independent review (2026-09-16, M-1) showed the unconditional CORRECTED
    # assignment releasing a record that still carried a second undecided HIGH
    # finding, which the legacy promote route then promoted.
    still_blocking = (await _blocking_by_record(
        db, curated.source_intake_id)).get(curated.source_record_id)
    if still_blocking:
        curated.record_status = HELD
        curated.status_reason = (
            f"Correction applied; still held by {still_blocking['issues']} undecided "
            f"holding-severity issue(s) and {still_blocking['conflicts']} unresolved "
            f"identifier conflict(s).")
    else:
        curated.record_status = CORRECTED
    await db.commit()

    return {
        "issue_id": str(issue.id), "issue_code": issue.issue_code,
        "curated_record_id": str(curated.id), "column": column,
        "original_value": current, "corrected_value": new_value,
        "correction_authority": issue.correction_authority,
    }


async def _conflict_held_records(db, intake_id) -> Dict[Any, int]:
    """source_record_id -> unresolved identifier conflicts (latest CONFLICT_RAISED)."""
    from sqlalchemy import text

    rows = (await db.execute(text("""
        SELECT x.source_record_id, count(*) AS n FROM (
          SELECT DISTINCT ON (entity_id, identifier_type) decision, source_record_id
          FROM tefca_identifier_decision_events
          WHERE intake_id = CAST(:i AS uuid)
          ORDER BY entity_id, identifier_type, sequence DESC) x
        WHERE x.decision = 'CONFLICT_RAISED' AND x.source_record_id IS NOT NULL
        GROUP BY x.source_record_id"""), {"i": str(intake_id)})).all()
    return {record_id: int(n) for record_id, n in rows}


async def _blocking_by_record(db, intake_id, *, run_id=None) -> Dict[Any, Dict[str, int]]:
    """Everything that holds a record: undecided holding-severity issues of the
    current run, plus unresolved identifier conflicts."""
    undecided_rows = (await db.execute(
        select(m.RceIssue.source_record_id, m.RceIssue.severity,
               m.RceIssue.resolution)
        .where(run_selection.issues_filter(intake_id, run_id=run_id)))).all()
    blocking: Dict[Any, Dict[str, int]] = {}
    for record_id, severity, resolution in undecided_rows:
        if record_id is not None and blocks_promotion(severity, resolution):
            entry = blocking.setdefault(record_id, {"issues": 0, "conflicts": 0})
            entry["issues"] += 1
    for record_id, n in (await _conflict_held_records(db, intake_id)).items():
        entry = blocking.setdefault(record_id, {"issues": 0, "conflicts": 0})
        entry["conflicts"] += n
    return blocking


async def recompute_hold_status(db, intake_id, *, run_id=None) -> Dict[str, Any]:
    """Re-derive CLEAN/CORRECTED/HELD after issues have been resolved.

    A record stops being HELD when nothing at holding severity remains OPEN AND
    no identifier conflict raised against it is still undecided (latest
    decision event CONFLICT_RAISED). Run after a batch of analyst resolutions
    so promotion sees current state.

    Scoped to ONE quality run, for the same reason `curate_delivery` is: a
    superseded run's unresolved finding must not hold a record the current run
    no longer objects to. Identifier conflicts are not run-scoped — they are
    statements about the registry, decided per entity and identifier type.

    This function writes NO disposition event. Terminal accounting for a
    released record is written by `promotion.promote_delivery` when the record
    is re-promoted (see `apply_disposition`).
    """
    curated = (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_intake_id == intake_id))).scalars().all()
    blocking = await _blocking_by_record(db, intake_id, run_id=run_id)

    changed = 0
    released: List[str] = []
    for row in curated:
        if row.record_status == REJECTED:
            continue
        if row.canonical_entity_id is not None:
            # Independent review finding M-3 (2026-09-16): a record already
            # PROMOTED must never have `record_status` rewritten again by this
            # function. A post-promotion finding (e.g. NPI-006, written by
            # `verification_findings.record_npi_outcome` under this SAME run
            # id) would otherwise flip an already-promoted record to HELD,
            # contradicting its own disposition with no path to clear it. Its
            # BLOCKING consequences go through
            # `post_promotion_verification.record_finding` instead — entity
            # verification_status, a work item, a new snapshot — never
            # `record_status`. Once promoted, `record_status` is history.
            continue
        should_hold = row.source_record_id in blocking
        if should_hold and row.record_status != HELD:
            row.record_status = HELD
            entry = blocking[row.source_record_id]
            row.status_reason = (
                f"{entry['issues']} unresolved holding-severity issue(s) and "
                f"{entry['conflicts']} unresolved identifier conflict(s). Held "
                f"from promotion until resolved.")
            changed += 1
        elif not should_hold and row.record_status == HELD:
            row.record_status = CORRECTED if row.correction_count else CLEAN
            row.status_reason = ("All holding-severity issues and identifier "
                                 "conflicts resolved.")
            changed += 1
            released.append(str(row.id))
    await db.commit()
    # Counted from the records this function actually holds (pre-promotion
    # only), not from raw `blocking`: a promoted record's source_record_id can
    # appear in `blocking` (a post-promotion finding under the same run) while
    # never being HELD by this function, per the skip above.
    still_held_ids = {row.source_record_id for row in curated
                      if row.canonical_entity_id is None and row.record_status == HELD}
    return {"curated_records": len(curated), "status_changed": changed,
            "still_held": len(still_held_ids), "released": released,
            "held_by_conflict_only": sum(
                1 for record_id, e in blocking.items()
                if record_id in still_held_ids and e["issues"] == 0 and e["conflicts"])}


async def release_check(db, intake_id, *, run_id=None) -> List[Dict[str, Any]]:
    """HELD records that nothing holds any more — releasable, not yet released.

    Read-only: it reports what `recompute_hold_status` WOULD release, so a
    reviewer can see the effect of a batch of decisions before applying it.
    """
    curated = (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_intake_id == intake_id,
            m.RceCuratedRecord.record_status == HELD))).scalars().all()
    blocking = await _blocking_by_record(db, intake_id, run_id=run_id)
    return [{
        "curated_record_id": str(row.id),
        "source_record_id": str(row.source_record_id),
        "rce_org_oid": row.rce_org_oid,
        "name": row.name,
        "would_become": CORRECTED if row.correction_count else CLEAN,
    } for row in curated if row.source_record_id not in blocking]


# ── the analyst disposition ──────────────────────────────────────────────────

#: What a reviewer may decide about one issue, exactly as the UI sends it.
#: Each maps onto the existing issue state machine (`transition_issue` /
#: `apply_correction`) and, for an identifier conflict, onto
#: `identifier_decisions.decide`. Lower-case spellings are accepted too.
DISPOSITION_DECISIONS = ("ACCEPT", "REJECT", "CORRECT", "CONFIRM_EXISTING",
                         "CONFIRM_SUBMITTED", "REQUEST_EVIDENCE", "DEFER",
                         "ESCALATE")

#: Issue types raised by promotion for identifier conflicts (rule NPI-008).
_CONFLICT_ISSUE_TYPES = frozenset({"NPI_EXISTING_VALUE_CONFLICT",
                                   "IDENTIFIER_EXISTING_VALUE_CONFLICT"})

#: Reviewer decision -> identifier decision event, for conflict issues.
_CONFLICT_DECISION = {
    "CONFIRM_EXISTING": "CONFIRM_EXISTING",
    "CONFIRM_SUBMITTED": "CONFIRM_SUBMITTED",
    "CORRECT": "CORRECTED",
    "REJECT": "REJECTED",
    "REQUEST_EVIDENCE": "REQUEST_EVIDENCE",
    "DEFER": "DEFERRED",
    "ESCALATE": "ESCALATED",
}


async def _walk(db, issue_id, path, *, actor: str, notes: str,
                qa_actor: Optional[str]) -> None:
    """Move an issue along `path`, skipping states it is already at or past."""
    issue = await db.get(m.RceIssue, issue_id)
    for to_status in path:
        current = issue.resolution or "OPEN"
        if current == to_status:
            continue
        if to_status not in _ALLOWED_TRANSITIONS.get(current, set()):
            # Already past this step (e.g. PROPOSED when asked for OPEN→PROPOSED).
            if to_status in {"PROPOSED", "UNDER_REVIEW"} and current in (
                    "PROPOSED", "UNDER_REVIEW", "APPROVED"):
                continue
            raise CorrectionRefused(
                f"Cannot move issue {issue.issue_code} from {current} to "
                f"{to_status}; allowed: "
                f"{sorted(_ALLOWED_TRANSITIONS.get(current, set()))}.")
        await transition_issue(db, issue_id, to_status=to_status, actor=actor,
                               notes=notes, qa_actor=qa_actor)
        await db.refresh(issue)


async def _flag_review_case_escalated(db, issue, *, actor: str, reason: str) -> Optional[str]:
    """Mark the DQ review case that cites this issue as escalated, if one exists.

    The case has no status column by design (`review_decision_events` owns
    workflow state), so the flag lives in the case snapshot and the audit log.
    """
    from app.tefca_registry import audit as reg_audit
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce.dq_review_bridge import QUEUE_SOURCE

    record = (await db.execute(
        select(reg.ReviewRecord).where(
            reg.ReviewRecord.verification_results["queue_source"].astext == QUEUE_SOURCE,
            reg.ReviewRecord.source_record_id == issue.source_record_id,
            reg.ReviewRecord.verification_results["issue_ids"].astext.contains(
                str(issue.id)))
        .limit(1))).scalar_one_or_none()
    if record is None:
        return None
    payload = dict(record.verification_results or {})
    payload["escalated"] = {"by": actor, "reason": reason,
                            "issue_code": issue.issue_code,
                            "at": datetime.utcnow().isoformat()}
    payload["priority"] = max(int(payload.get("priority") or 0), 95)
    record.verification_results = payload
    reg_audit.record(db, "review_case_escalated", record.entity_id, actor_email=actor,
                     metadata={"review_id": record.review_id, "issue_code": issue.issue_code,
                               "reason": reason})
    await db.flush()
    return record.review_id


async def apply_disposition(db, issue_id, *, decision: str, reason: str,
                            actor: str, corrected_value: Optional[str] = None,
                            actor_id=None, qa_actor: Optional[str] = None
                            ) -> Dict[str, Any]:
    """One analyst decision about one issue, end to end.

        decision            non-conflict issue                  conflict issue (NPI-008)
        ─────────────────   ─────────────────────────────────   ────────────────────────
        ACCEPT              PROPOSED → APPROVED → apply          (same)
                            (NO_CORRECTION: WAIVED → RESOLVED)
        REJECT              UNDER_REVIEW; hold STAYS,            decide REJECTED (releases;
                            reason recorded                      registry unchanged)
        CORRECT             PROPOSED → APPROVED → apply(value)   + decide CORRECTED
        CONFIRM_EXISTING    refused                              decide CONFIRM_EXISTING;
                                                                 issue REJECTED → RESOLVED
        CONFIRM_SUBMITTED   refused                              decide CONFIRM_SUBMITTED
                                                                 (identifier + version +
                                                                 audit); issue RESOLVED
        REQUEST_EVIDENCE    UNDER_REVIEW; hold stays             + decide REQUEST_EVIDENCE
        DEFER               UNDER_REVIEW; hold stays             + decide DEFERRED
        ESCALATE            UNDER_REVIEW; hold stays; review     + decide ESCALATED
                            case flagged escalated

    A non-blank reason is required (ValueError, so the API maps it to 422).
    After the decision the record's hold is recomputed; if the record is
    released it is re-promoted through the idempotent drain with a HUMAN
    disposition event (reason ANALYST_DISPOSITION) and a reconciliation
    snapshot is persisted with trigger DISPOSITION. Area 1 is never touched.
    """
    decision = (decision or "").strip().upper()
    if decision not in DISPOSITION_DECISIONS:
        raise ValueError(
            f"unknown decision {decision!r}; one of {DISPOSITION_DECISIONS}")
    if not (reason or "").strip():
        raise ValueError("a disposition reason is required")
    reason = reason.strip()
    if decision == "CORRECT" and (corrected_value is None or not str(corrected_value).strip()):
        raise ValueError("CORRECT requires corrected_value")

    issue = await db.get(m.RceIssue, issue_id)
    if issue is None:
        raise CorrectionRefused(f"No issue {issue_id}")
    if issue.source_record_id is None:
        raise CorrectionRefused(
            f"Issue {issue.issue_code} names no source record; it is a "
            f"delivery-level finding and has no record disposition.")
    intake_id = issue.source_intake_id
    curated = (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_record_id == issue.source_record_id)
    )).scalar_one_or_none()
    status_before = curated.record_status if curated is not None else None
    is_conflict = issue.issue_type in _CONFLICT_ISSUE_TYPES
    if decision in ("CONFIRM_EXISTING", "CONFIRM_SUBMITTED") and not is_conflict:
        raise CorrectionRefused(
            f"{decision} applies only to identifier conflicts; issue "
            f"{issue.issue_code} is {issue.issue_type}.")

    from app.tefca_registry.rce import dispositions as disp
    history_before = len(await disp.history_for_record(db, issue.source_record_id))

    notes = f"[{decision}] {reason}"
    identifier_event = None
    correction = None
    escalated_case = None

    # ── the identifier decision, when the issue is a conflict ──
    if is_conflict and decision in _CONFLICT_DECISION:
        from app.tefca_registry.rce import identifier_decisions
        from app.tefca_registry.rce import traceability_models as tm

        latest = (await db.execute(
            select(tm.TefcaIdentifierDecisionEvent).where(
                tm.TefcaIdentifierDecisionEvent.issue_id == issue.id)
            .order_by(tm.TefcaIdentifierDecisionEvent.sequence.desc())
            .limit(1))).scalar_one_or_none()
        if latest is None:
            raise CorrectionRefused(
                f"Issue {issue.issue_code} is an identifier conflict but no "
                f"conflict event names it; the ledger and the decision table "
                f"disagree and must be reconciled before a decision is recorded.")
        identifier_event = await identifier_decisions.decide(
            db, entity_id=latest.entity_id,
            identifier_type=latest.identifier_type,
            decision=_CONFLICT_DECISION[decision], reason=reason, actor=actor,
            selected_value=(corrected_value if decision == "CORRECT" else None),
            source_record_id=issue.source_record_id, intake_id=intake_id,
            issue_id=issue.id, actor_id=actor_id)

    # ── the issue state machine ──
    if decision == "ACCEPT":
        if issue.correction_authority == NO_CORRECTION or (
                issue.suggested_value is None and corrected_value is None):
            await _walk(db, issue.id, ("WAIVED", "RESOLVED"), actor=actor,
                        notes=notes, qa_actor=qa_actor)
        else:
            await _walk(db, issue.id, ("PROPOSED", "APPROVED"), actor=actor,
                        notes=notes, qa_actor=qa_actor)
            correction = await apply_correction(db, issue.id, actor=actor)
    elif decision == "CORRECT":
        await _walk(db, issue.id, ("PROPOSED", "APPROVED"), actor=actor,
                    notes=notes, qa_actor=qa_actor)
        correction = await apply_correction(db, issue.id, actor=actor,
                                            corrected_value=corrected_value)
    elif decision == "CONFIRM_EXISTING" or (decision == "REJECT" and is_conflict):
        # The submitted value is not adopted; the finding is settled.
        await _walk(db, issue.id, ("REJECTED", "RESOLVED"), actor=actor,
                    notes=notes, qa_actor=qa_actor)
    elif decision == "CONFIRM_SUBMITTED":
        # The registry change was made by identifier_decisions.decide; the
        # curated value already IS the submitted value, so nothing is applied.
        await _walk(db, issue.id, ("PROPOSED", "APPROVED", "RESOLVED"),
                    actor=actor, notes=notes, qa_actor=qa_actor)
    else:
        # REJECT (non-conflict), REQUEST_EVIDENCE, DEFER, ESCALATE: the record
        # stays HELD. The question is still open in substance, so the issue
        # stays undecided (UNDER_REVIEW) and the reason is recorded on it.
        await _walk(db, issue.id, ("UNDER_REVIEW",), actor=actor, notes=notes,
                    qa_actor=qa_actor)
        if decision == "ESCALATE":
            escalated_case = await _flag_review_case_escalated(
                db, issue, actor=actor, reason=reason)
            await db.commit()

    # ── hold, re-promotion, snapshot ──
    hold = await recompute_hold_status(db, intake_id)
    if curated is not None:
        await db.refresh(curated)
    status_after = curated.record_status if curated is not None else None
    promotion_result = None
    snapshot_id = None
    if curated is not None and status_after != status_before:
        from app.tefca_registry.rce import promotion as promotion_module
        from app.tefca_registry.rce import reconciliation
        from app.tefca_registry.rce.delivery_jobs import job_for_intake

        promotion_result = await promotion_module.promote_delivery(
            db, intake_id, actor=actor, actor_id=actor_id,
            disposition_actor_type="HUMAN",
            disposition_reason=f"Analyst decision {decision}: {reason}")
        job = await job_for_intake(db, intake_id)
        if job is not None:
            full = await reconciliation.reconcile_delivery(db, intake_id)
            snapshot = await reconciliation.persist_snapshot(
                db, intake_id, full, job_id=job.id, actor=actor,
                trigger="DISPOSITION")
            snapshot_id = str(snapshot.id)
        await db.refresh(curated)

    history = await disp.history_for_record(db, issue.source_record_id)
    disposition_event = history[-1] if len(history) > history_before else None
    await db.refresh(issue)
    return {
        "issue_id": str(issue.id), "issue_code": issue.issue_code,
        "decision": decision, "resolution": issue.resolution,
        "identifier_decision": identifier_event["event"] if identifier_event else None,
        "registry_changed": bool(identifier_event and identifier_event["registry_changed"]),
        "correction": correction,
        "escalated_review_id": escalated_case,
        "record_status_before": status_before,
        "record_status_after": (curated.record_status if curated is not None else None),
        "hold_released": status_before == HELD and status_after != HELD,
        "hold": hold,
        "re_promoted": promotion_result is not None,
        "promotion": ({k: promotion_result[k] for k in (
            "entities_created", "entities_updated", "entities_unchanged",
            "conflicts_raised", "dispositions_written_this_run")}
                      if promotion_result else None),
        "disposition_event": disposition_event,
        "snapshot_id": snapshot_id,
    }
