"""Governed recovery and approval protection for IQVIA reference snapshots.

DRAFT / NOT FINAL / INDEPENDENT QA PENDING. A PROPOSAL, pending owner confirmation.
Nothing here is wired to a route or a job, and the approval protection is OFF
unless `IQVIA_BOM_APPROVAL_GUARD` is switched on. It is not applied to any live
snapshot by this change; it is code and synthetic tests for the owner to decide on.

THE PROBLEM (see iqvia_reader_assessment). Before #216 the importer could retain a
leading byte-order mark in the first column's name. A snapshot staged that way may
be all-rejected (class A), partly staged (B), or fully staged with a mis-keyed field
(C), and "fully staged" does not mean "correctly staged". Re-registering the same
bytes is refused as a duplicate by design (uq_source_snapshot_identity), and
SNAPSHOT_REJECTED has no writer, so today such a snapshot can only sit PENDING.

WHAT THIS ADDS.

  1. Approval protection, in the SHARED approval service (`approve_snapshot`), so a
     direct call cannot bypass the route. Switched on by the flag. Never edits a
     snapshot, never changes a status.
  2. An independent assessment record (`bom_assessment`), written by a QA lead and
     checked against the CURRENT facts, so a changed state invalidates it.
  3. Recovery by an explicit, append-only authorization naming the snapshot id and
     the exact permitted action, and a replacement snapshot LINKED to the original
     (`metadata.recovery_of`). The original row, its staged rows, its file bytes and
     its hash are never touched. The duplicate rule is not weakened anywhere else:
     `_register_and_enqueue` is unchanged, and only this function, holding a valid
     one-time authorization for that exact original, may register identical bytes.

The recovery link is carried by the replacement, not by `supersedes_snapshot_id`:
that column means "a decision row" (approval), and consumers walk it back to the
registration row to find staged rows.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select, text

from app.core import request_context
from app.core.security import role_at_least
from app.tefca_registry.rce import iqvia_reader_assessment as ra
from app.tefca_registry.rce import snapshot_models as sm

GUARD_FLAG = "IQVIA_BOM_APPROVAL_GUARD"
IQVIA_SOURCES = (sm.SOURCE_IQVIA_HCO, sm.SOURCE_IQVIA_HCP, sm.SOURCE_IQVIA_AFFILIATION)
_OBSERVATION_MODEL = {
    sm.SOURCE_IQVIA_HCO: sm.IqviaHcoObservation,
    sm.SOURCE_IQVIA_HCP: sm.IqviaHcpObservation,
    sm.SOURCE_IQVIA_AFFILIATION: sm.IqviaAffiliationObservation,
}

ASSESSOR_ROLE = "qalead"
AUTHORIZER_ROLE = "qalead"
EXECUTOR_ROLE = "reviewer"

OUTCOME_ACCEPTED = "ACCEPTED_FOR_APPROVAL"
OUTCOME_NOT_ACCEPTED = "NOT_ACCEPTED"

ACTION_RETRY_IDENTICAL_BYTES = "RETRY_IDENTICAL_BYTES"
PERMITTED_ACTIONS = (ACTION_RETRY_IDENTICAL_BYTES,)

AUDIT_AUTHORIZED = "IQVIA_RECOVERY_AUTHORIZED"
AUDIT_WITHDRAWN = "IQVIA_RECOVERY_WITHDRAWN"
AUDIT_REGISTERED = "IQVIA_RECOVERY_REGISTERED"
AUDIT_ASSESSMENT = "IQVIA_BOM_ASSESSMENT_RECORDED"

DEFAULT_AUTHORIZATION_HOURS = 72     # a proposal default; the owner decides


class AssessmentRequired(ValueError):
    """Approval is refused by the proposed protection. `code` names why."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class RecoveryRefused(ValueError):
    """A recovery step is refused; `code` names why."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def guard_enabled() -> bool:
    from app.core.config import settings
    value = getattr(settings, GUARD_FLAG, None)
    if value is None:
        value = os.environ.get(GUARD_FLAG, "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _actor(user) -> str:
    return str(getattr(user, "email", None) or "UNKNOWN")[:320]


def _audit_row(db, user, *, action: str, outcome: str, resource_id, details: Dict[str, Any]):
    """One append-only row in the canonical audit trail (`audit_logs`)."""
    from app.models.database import AuditLog

    row = AuditLog(
        user_id=getattr(user, "id", None), action=action, event_type="data_import", outcome=outcome,
        resource_type="iqvia_snapshot", resource_id=str(resource_id) if resource_id is not None else None,
        details={"actor": _actor(user), "actor_role": str(getattr(user, "role", "") or ""),
                 "request_id": request_context.get("request_id"), **details},
        correlation_id=request_context.correlation_id())
    db.add(row)
    return row


# ── facts from the database (the in-process counterpart of the read-only script) ──

async def decision_row(db, snapshot_id) -> Optional[Tuple[Any, str]]:
    found = (await db.execute(
        select(sm.SourceSnapshot.id, sm.SourceSnapshot.status)
        .where(sm.SourceSnapshot.supersedes_snapshot_id == snapshot_id).limit(1))).first()
    return (found[0], found[1]) if found else None


async def gather_facts(db, snapshot, *, include_npi_missing: bool = False) -> ra.SnapshotFacts:
    """Facts for one registration-time snapshot. Counts and key NAMES only: no
    payload value is selected."""
    from app.tefca_registry.rce.iqvia_upload_models import IqviaImportJob

    meta = snapshot.metadata_ or {}
    model = _OBSERVATION_MODEL[snapshot.source_system]
    f = ra.SnapshotFacts(snapshot_id=str(snapshot.id), source_system=snapshot.source_system,
                         status=snapshot.status, declared_record_count=snapshot.record_count,
                         import_status=meta.get("import_status"),
                         reader=meta.get("reference_reader"),
                         reader_refused=bool(meta.get("reference_reader_refused")))
    try:
        rejected = meta.get("rows_rejected")
        f.rows_rejected = None if rejected is None else int(rejected)
    except (TypeError, ValueError):
        f.rows_rejected = None
    f.staged_count = int((await db.execute(
        select(func.count()).select_from(model).where(model.source_snapshot_id == snapshot.id))).scalar() or 0)
    preflight = meta.get("reference_preflight")
    f.preflight_recorded = bool(preflight)
    if preflight:
        f.preflight_gate = preflight.get("gate")
        f.preflight_version = preflight.get("preflight_version")
        f.preflight_bom_finding = any(x.get("code") == "REF-SCH-010" for x in (preflight.get("findings") or []))
    job = (await db.execute(
        select(IqviaImportJob.state).where(IqviaImportJob.snapshot_id == snapshot.id)
        .order_by(IqviaImportJob.created_at.desc()).limit(1))).first()
    f.job_state = job[0] if job else None
    if f.staged_count:
        table = model.__tablename__
        # `table` is the __tablename__ of one of three fixed models, never caller input.
        row = (await db.execute(text(  # noqa: S608
            "select bool_or(left(k, 1) = E'\\ufeff'), count(*) from "  # noqa: S608
            f"(select payload from {table} where source_snapshot_id = :sid limit 1) r "
            "cross join lateral jsonb_object_keys(r.payload) k"), {"sid": snapshot.id})).first()
        if row and row[0] is not None:
            f.sampled_payload_has_bom_key, f.sampled_payload_key_count = bool(row[0]), int(row[1])
        if include_npi_missing and snapshot.source_system != sm.SOURCE_IQVIA_AFFILIATION:
            f.npi_missing_count = int((await db.execute(text(  # noqa: S608 - fixed model table name
                f"select count(*) filter (where npi is null) from {table} where source_snapshot_id = :sid"),  # noqa: S608
                {"sid": snapshot.id})).scalar() or 0)
    decision = await decision_row(db, snapshot.id)
    if decision:
        f.decision_snapshot_id, f.decision_status = str(decision[0]), decision[1]
    return f


async def assess_snapshot(db, snapshot_id, *, include_npi_missing: bool = False) -> ra.Assessment:
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError(f"no snapshot {snapshot_id}")
    if snapshot.source_system not in IQVIA_SOURCES:
        raise ValueError(f"{snapshot.source_system} is not an IQVIA reference source")
    return ra.assess(await gather_facts(db, snapshot, include_npi_missing=include_npi_missing))


# ── the assessment record ────────────────────────────────────────────────────

async def record_assessment(db, snapshot_id, *, user, outcome: str, basis: str, approval_ref: str,
                            commit: bool = True) -> Dict[str, Any]:
    """A QA lead's recorded assessment of ONE pending snapshot, written into its
    metadata (a PENDING row's metadata is already written by the importer) and
    into the audit trail. It states what the evidence was at that moment; the
    approval protection compares it with the evidence at approval time."""
    if not role_at_least(user, ASSESSOR_ROLE):
        raise PermissionError(f"recording an assessment requires {ASSESSOR_ROLE} or above")
    if outcome not in (OUTCOME_ACCEPTED, OUTCOME_NOT_ACCEPTED):
        raise ValueError(f"outcome must be {OUTCOME_ACCEPTED} or {OUTCOME_NOT_ACCEPTED}")
    # Same lock as approve_snapshot, re-read under it: an approval cannot interleave with this write.
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id, with_for_update=True, populate_existing=True)
    if snapshot is None:
        raise ValueError(f"no snapshot {snapshot_id}")
    if snapshot.status != sm.SNAPSHOT_PENDING or await decision_row(db, snapshot.id):
        raise ValueError("an assessment is recorded only on a PENDING snapshot that has no decision")
    current = await assess_snapshot(db, snapshot_id)
    if outcome == OUTCOME_ACCEPTED and current.staging_class in (ra.CLASS_A, ra.CLASS_B, ra.CLASS_U):
        raise ValueError(f"a class {current.staging_class} snapshot cannot be accepted for approval: "
                         + ra.CLASS_MEANING[current.staging_class])
    record = {"snapshot_id": str(snapshot.id), "assessed_by": _actor(user),
              "assessed_at": datetime.now(timezone.utc).isoformat(), "outcome": outcome,
              "staging_class": current.staging_class, "bom_exposure": current.bom_exposure,
              "basis": (basis or "")[:500], "approval_ref": (approval_ref or "")[:120], "version": 1}
    snapshot.metadata_ = {**(snapshot.metadata_ or {}), "bom_assessment": record}
    _audit_row(db, user, action=AUDIT_ASSESSMENT, outcome="success", resource_id=snapshot.id,
               details={"staging_class": current.staging_class, "bom_exposure": current.bom_exposure,
                        "outcome": outcome, "approval_ref": record["approval_ref"]})
    if commit:
        await db.commit()
    return record


# ── approval protection (called from the shared approval service) ────────────

async def recovery_replacement_of(db, original_id) -> Optional[sm.SourceSnapshot]:
    return (await db.execute(
        select(sm.SourceSnapshot)
        .where(sm.SourceSnapshot.metadata_["recovery_of"].astext == str(original_id))
        .order_by(sm.SourceSnapshot.created_at).limit(1))).scalars().first()


async def enforce_assessment_before_approval(db, received, *, approver: str) -> None:
    """PROPOSED protection, called by `approve_snapshot` before it appends an
    APPROVED row. A no-op unless the flag is on and the source is an IQVIA
    reference source. Raises AssessmentRequired; changes nothing.

      * a snapshot that has a recovery replacement is never approved: approve the replacement;
      * class A or B: refused, recovery required (an assessment cannot complete rows);
      * class U: refused, unresolved: it is never treated as safe;
      * class C with the mark CONFIRMED or UNKNOWN: needs a recorded assessment, accepted,
        matching the current evidence, by someone other than the approver;
      * class C with no mark found by a check that could see the header: no extra requirement.
    """
    if not guard_enabled() or received.source_system not in IQVIA_SOURCES:
        return
    replacement = await recovery_replacement_of(db, received.id)
    if replacement is not None:
        raise AssessmentRequired(
            "BOM_ORIGINAL_RECOVERED",
            f"snapshot {received.id} has a recovery replacement ({replacement.id}); approve the replacement")
    current = ra.assess(await gather_facts(db, received))
    if current.staging_class in (ra.CLASS_A, ra.CLASS_B):
        raise AssessmentRequired(
            "BOM_RECOVERY_REQUIRED",
            f"staging class {current.staging_class}: {ra.CLASS_MEANING[current.staging_class]} "
            "It needs a governed recovery, not an approval.")
    if current.staging_class == ra.CLASS_U:
        raise AssessmentRequired(
            "BOM_STAGING_UNRESOLVED",
            "staging class U: " + "; ".join(current.staging_basis) + ". Unresolved is not safe.")
    need, why = ra.needs_assessment_before_approval(current)
    if not need:
        return
    recorded = (received.metadata_ or {}).get("bom_assessment") or {}
    problems = []
    if recorded.get("snapshot_id") != str(received.id):
        problems.append("no assessment is recorded for this snapshot")
    elif recorded.get("outcome") != OUTCOME_ACCEPTED:
        problems.append(f"the recorded assessment outcome is {recorded.get('outcome')!r}")
    elif (recorded.get("staging_class"), recorded.get("bom_exposure")) != (current.staging_class,
                                                                          current.bom_exposure):
        problems.append("the evidence has changed since the assessment was recorded "
                        f"(then {recorded.get('staging_class')}/{recorded.get('bom_exposure')}, "
                        f"now {current.staging_class}/{current.bom_exposure})")
    elif not recorded.get("assessed_by") or recorded.get("assessed_by") == approver:
        problems.append("the assessor and the approver must be different people")
    if problems:
        raise AssessmentRequired("BOM_ASSESSMENT_REQUIRED", f"{why}; " + "; ".join(problems))


# ── recovery ─────────────────────────────────────────────────────────────────

async def _require_real_executor(db, requested_by: str) -> None:
    """`requested_by` must be an existing, active user who may execute a recovery. Free text
    would let a QA lead name any other string and then execute the recovery themselves."""
    from app.models.database import User

    email = str(requested_by or "").strip()
    found = None
    if email:
        found = (await db.execute(select(User).where(func.lower(User.email) == email.lower()))).scalar_one_or_none()
    if found is None or found.is_active is False or not role_at_least(found, EXECUTOR_ROLE):
        raise RecoveryRefused("REQUESTER_NOT_A_USER",
                              f"the requester must be an existing active user with {EXECUTOR_ROLE} or above")


async def authorize_recovery(db, original_id, *, user, requested_by: str, action: str, approval_ref: str,
                             valid_for_hours: int = DEFAULT_AUTHORIZATION_HOURS,
                             commit: bool = True) -> uuid.UUID:
    """Record, append-only, that a named recovery action is permitted on one named
    snapshot. The authorizer is a QA lead and is not the requester. Returns the
    authorization id (the audit row's id). Changes no snapshot."""
    if action not in PERMITTED_ACTIONS:
        raise RecoveryRefused("ACTION_NOT_PERMITTED", f"{action!r} is not a permitted recovery action")
    if not role_at_least(user, AUTHORIZER_ROLE):
        raise PermissionError(f"authorizing a recovery requires {AUTHORIZER_ROLE} or above")
    if not approval_ref or not str(approval_ref).strip():
        raise RecoveryRefused("APPROVAL_REF_REQUIRED", "the authorization must cite the owner's written reference")
    if _actor(user).strip().lower() == str(requested_by or "").strip().lower():
        raise RecoveryRefused("SEGREGATION_OF_DUTIES", "the authorizer cannot be the requester")
    original = await db.get(sm.SourceSnapshot, original_id)
    if original is None or original.source_system not in IQVIA_SOURCES:
        raise RecoveryRefused("NOT_AN_IQVIA_SNAPSHOT", f"no IQVIA reference snapshot {original_id}")
    if original.supersedes_snapshot_id is not None:
        raise RecoveryRefused("NOT_A_REGISTRATION_ROW", "authorize recovery against the registration-time snapshot")
    if await decision_row(db, original.id):
        raise RecoveryRefused(
            "ORIGINAL_HAS_A_DECISION",
            "a decided snapshot is not recovered by this path; it needs an impact assessment first")
    if await recovery_replacement_of(db, original.id):
        raise RecoveryRefused("ALREADY_RECOVERED", "this snapshot already has a recovery replacement")
    await _require_real_executor(db, requested_by)
    row = _audit_row(
        db, user, action=AUDIT_AUTHORIZED, outcome="success", resource_id=original.id,
        details={"permitted_action": action, "snapshot_ids": [str(original.id)],
                 "requested_by": requested_by, "approval_ref": str(approval_ref)[:120],
                 "expires_at": (datetime.now(timezone.utc) + timedelta(hours=valid_for_hours)).isoformat(),
                 "sha256": original.sha256})
    await db.flush()
    authorization_id = row.id
    if commit:
        await db.commit()
    return authorization_id


async def withdraw_recovery_authorization(db, authorization_id, *, user, reason: str,
                                          commit: bool = True) -> None:
    """Append a withdrawal. An unused authorization stops being valid; a used one
    stays on record as used."""
    if not role_at_least(user, AUTHORIZER_ROLE):
        raise PermissionError(f"withdrawing an authorization requires {AUTHORIZER_ROLE} or above")
    auth = await _load_authorization(db, authorization_id)
    _audit_row(db, user, action=AUDIT_WITHDRAWN, outcome="success", resource_id=auth["original_id"],
               details={"authorization_id": str(authorization_id), "reason": (reason or "")[:300]})
    if commit:
        await db.commit()


async def _load_authorization(db, authorization_id) -> Dict[str, Any]:
    from app.models.database import AuditLog

    row = (await db.execute(select(AuditLog).where(
        AuditLog.id == authorization_id, AuditLog.action == AUDIT_AUTHORIZED))).scalars().first()
    if row is None:
        raise RecoveryRefused("NO_AUTHORIZATION", f"no recovery authorization {authorization_id}")
    details = dict(row.details or {})
    ids = details.get("snapshot_ids") or []
    if len(ids) != 1:
        raise RecoveryRefused("AUTHORIZATION_MALFORMED", "an authorization names exactly one snapshot")
    withdrawn = (await db.execute(select(AuditLog).where(
        AuditLog.action == AUDIT_WITHDRAWN, AuditLog.resource_id == str(ids[0])))).scalars().all()
    used = (await db.execute(select(sm.SourceSnapshot.id).where(
        sm.SourceSnapshot.metadata_["recovery_authorization_id"].astext == str(authorization_id)))).first()
    return {"original_id": uuid.UUID(ids[0]), "action": details.get("permitted_action"),
            "requested_by": details.get("requested_by"), "authorized_by": details.get("actor"),
            "expires_at": details.get("expires_at"), "sha256": details.get("sha256"),
            "approval_ref": details.get("approval_ref"),
            "withdrawn": any((w.details or {}).get("authorization_id") == str(authorization_id)
                             for w in withdrawn),
            "used": used is not None}


async def register_recovery_snapshot(db, *, authorization_id, file_path, user,
                                     commit: bool = True) -> sm.SourceSnapshot:
    """Register the replacement for ONE original, under a valid one-time
    authorization, from a file whose hash equals the original's. Stages nothing:
    the existing importer stages the replacement, with the current reader.

    Refused unless: the authorization exists, names this original and the retry
    action, is unexpired, unwithdrawn and unused; the executor is the named
    requester or a QA lead; the original still has no decision and no
    replacement; and the file's sha256 equals the original's. The original is
    never modified."""
    from pathlib import Path

    from app.tefca_registry.rce.iqvia_import import file_sha256
    from app.tefca_registry.rce.source_matching import register_snapshot

    auth = await _load_authorization(db, authorization_id)
    if auth["action"] != ACTION_RETRY_IDENTICAL_BYTES:
        raise RecoveryRefused("ACTION_NOT_PERMITTED", f"the authorization permits {auth['action']!r}, not a retry")
    if auth["withdrawn"]:
        raise RecoveryRefused("AUTHORIZATION_WITHDRAWN", "the authorization was withdrawn")
    if auth["used"]:
        raise RecoveryRefused("AUTHORIZATION_USED", "the authorization was already used; it is one-time")
    if auth["expires_at"] and datetime.fromisoformat(auth["expires_at"]) < datetime.now(timezone.utc):
        raise RecoveryRefused("AUTHORIZATION_EXPIRED", "the authorization has expired")
    actor = _actor(user)
    if actor != auth["requested_by"] and not role_at_least(user, AUTHORIZER_ROLE):
        raise RecoveryRefused("EXECUTOR_NOT_PERMITTED", "only the named requester or a QA lead may execute")
    if not role_at_least(user, EXECUTOR_ROLE):
        raise PermissionError(f"executing a recovery requires {EXECUTOR_ROLE} or above")
    original = await db.get(sm.SourceSnapshot, auth["original_id"])
    if original is None:
        raise RecoveryRefused("ORIGINAL_MISSING", "the original snapshot no longer exists")
    if await decision_row(db, original.id):
        raise RecoveryRefused("ORIGINAL_HAS_A_DECISION", "the original has since been decided")
    if await recovery_replacement_of(db, original.id):
        raise RecoveryRefused("ALREADY_RECOVERED", "the original already has a recovery replacement")
    path = Path(file_path)
    if file_sha256(path) != original.sha256 or (auth["sha256"] and auth["sha256"] != original.sha256):
        raise RecoveryRefused("BYTES_DIFFER", "the file is not byte-identical to the original; a different "
                              "file is a new upload, not a recovery")
    suffix = f" [recovery of {original.id.hex[:8]}]"
    # Truncate the ORIGINAL's label, never the suffix: a 200-character original would otherwise
    # lose the suffix and collide with itself on (source, sha256, label).
    label = f"{original.snapshot_label[:200 - len(suffix)]}{suffix}"
    replacement = await register_snapshot(
        db, source_system=original.source_system, label=label, sha256=original.sha256, record_count=0,
        received_at=datetime.now(timezone.utc), created_by=actor,
        metadata={"original_path": str(path), "import_status": "staging",
                  "recovery_of": str(original.id), "recovery_authorization_id": str(authorization_id),
                  "recovery_action": ACTION_RETRY_IDENTICAL_BYTES, "recovery_approval_ref": auth["approval_ref"]},
        commit=False)
    _audit_row(db, user, action=AUDIT_REGISTERED, outcome="success", resource_id=original.id,
               details={"authorization_id": str(authorization_id), "replacement_snapshot_id": str(replacement.id),
                        "permitted_action": ACTION_RETRY_IDENTICAL_BYTES})
    if commit:
        await db.commit()
    return replacement
