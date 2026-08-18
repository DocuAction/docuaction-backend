from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AuditLog


async def log_action(
    db: AsyncSession,
    table_name: str,
    record_id: str,
    action: str,
    field_name: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    user_id: str | None = None,
):
    entry = AuditLog(
        table_name=table_name,
        record_id=str(record_id),
        action=action,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        user_id=user_id,
    )
    db.add(entry)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 9 — TEFCA audit trail (NIST 800-53 AU-2 / AU-3 / AU-12)
#
# The legacy log_action() above targets columns that do not exist on the live
# AuditLog model and would raise at runtime, so a new, correct helper is added
# here (the legacy function is left untouched to avoid breaking other callers).
# This writes to the canonical AuditLog table (app.models.database.AuditLog):
#   user_id, action, resource_type, resource_id, ip_address, created_at(=timestamp)
# Action-specific context (result, entity_id, bucket, reason, reviewer_id, …) is
# captured in the `details` JSON column.
# ─────────────────────────────────────────────────────────────────────────────
from app.models.database import AuditLog as _CanonicalAuditLog


# ─────────────────────────────────────────────────────────────────────────────
# AT-001 / AT-009 — event_type, outcome and correlation_id are DERIVED HERE.
#
# There are ~60 audit call sites across the codebase. Requiring each one to pass
# three more arguments guarantees the columns are populated unevenly — which is
# how the trail ended up with holes in the first place — and the next call site
# someone adds would inherit the omission. Deriving them centrally means every
# existing caller gets correct values without being edited, and a caller that
# knows better can still override.
# ─────────────────────────────────────────────────────────────────────────────

# THE SINGLE SOURCE for the event-type vocabulary.
#
# This previously existed twice: once here (deriving the stored column) and once
# in app/Tefca/routes.py as _AUDIT_EVENT_TYPES (backing the Audit Trail's filter
# dropdown). Two lists meant the value STORED on a row and the value the filter
# offered could disagree — a row displaying "review_decision" that the "review"
# filter did not return, and a "security" option the endpoint would reject with
# a 400. AT-007's filter is only trustworthy if the write path and the read path
# are quoting the same vocabulary, so there is now one list and routes.py
# imports it.
EVENT_TYPE_ACTIONS = {
    "authentication": [
        "login_success", "login_failed", "login_failure", "login_blocked",
        "login_throttled", "logout", "signup", "signup_rejected",
        "signup_throttled", "email_verified", "email_verification_failed",
        "password_reset",
    ],
    "security": ["file_scan", "permission_denied"],
    "data_import": ["entity_import", "import_completed", "fhir_import", "csv_import"],
    "review": [
        "review_executed", "review_decision", "entity_verified", "bucket_override",
        "verification_started", "verification_completed",
    ],
    "data_change": [
        "entity_created", "entity_updated", "status_changed",
        "status_change_refused", "npi_flagged",
    ],
    "administration": [
        "user_approved", "user_rejected", "user_disabled", "user_role_changed",
        "user_invited", "password_set",
    ],
    "reporting": ["report_generated", "report_downloaded", "export"],
}

# Flattened action -> bucket, built once from the list above.
_ACTION_EVENT_TYPE = {
    action: bucket
    for bucket, actions in EVENT_TYPE_ACTIONS.items()
    for action in actions
}

# `result` vocabulary in use across existing call sites -> canonical outcome.
_RESULT_OUTCOME = {
    "success": "success",
    "pass": "success",
    "ok": "success",
    "fail": "failure",
    "failure": "failure",
    "error": "failure",
    "rejected": "rejected",
    "blocked": "blocked",
    "denied": "blocked",
}


def classify_event_type(action: str) -> str:
    """Coarse category for an action name.

    Unknown actions are 'other' — never null — so the Audit Trail's Event Type
    filter has no blank bucket quietly hiding events. 'other' rather than
    'system' because that is the label the filter has always offered for the
    residue, and inventing a second name for the same bucket is how the two
    vocabularies drifted apart in the first place.
    """
    a = (action or "").strip().lower()
    if a in _ACTION_EVENT_TYPE:
        return _ACTION_EVENT_TYPE[a]
    # Report generation is emitted with varied uppercase action names
    # (e.g. QUARTERLY_REPORT_GENERATED); classify by shape rather than by
    # enumerating every one.
    if "report" in a or "export" in a:
        return "reporting"
    if a.startswith("login") or a.startswith("signup") or a.startswith("auth"):
        return "authentication"
    if a.startswith("user_"):
        return "administration"
    return "other"


def classify_outcome(action: str, result: str | None) -> str:
    """Canonical outcome. `result` wins when recognised; otherwise the action
    name decides, so `login_failed` is never recorded as a success merely
    because the caller left `result` at its default."""
    r = (result or "").strip().lower()
    if r in _RESULT_OUTCOME:
        outcome = _RESULT_OUTCOME[r]
    else:
        outcome = "success"
    a = (action or "").strip().lower()
    # A default `result` must not override what the action plainly says.
    if r in ("", "success") and (
        a.endswith("_failed") or a.endswith("_failure") or "denied" in a
    ):
        return "failure"
    if r in ("", "success") and (a.endswith("_blocked") or a.endswith("_throttled")):
        return "blocked"
    if r in ("", "success") and a.endswith("_rejected"):
        return "rejected"
    return outcome


async def log_tefca_event(
    db: AsyncSession,
    *,
    user=None,
    action: str,
    resource_type: str,
    resource_id=None,
    result: str = "success",
    ip_address: str | None = None,
    details: dict | None = None,
    event_type: str | None = None,
    outcome: str | None = None,
    correlation_id: str | None = None,
):
    """
    Append a TEFCA audit record. Every required field is captured:
    user_id, action, event_type, outcome, resource_type, resource_id,
    timestamp(created_at), ip_address, correlation_id, result.
    Caller is responsible for committing the session.

    `user` is the authenticated principal (has `.id`); pass None for system
    actions (e.g. background batch validation), which are recorded with a null
    user_id and an explicit actor in `details`.

    `event_type`/`outcome` are derived from `action`/`result` when not given
    (AT-001), and `correlation_id` is read from `details` when the caller
    already threaded one through there (AT-009).
    """
    payload = {"result": result}
    if details:
        payload.update(details)

    # Callers that already carry a correlation id do so inside details (the auth
    # routes have done this since the enterprise auth work); promote it to the
    # column rather than making every one of them pass it twice.
    cid = correlation_id or payload.get("correlation_id")

    entry = _CanonicalAuditLog(
        user_id=getattr(user, "id", None),
        action=action,
        event_type=event_type or classify_event_type(action),
        outcome=outcome or classify_outcome(action, result),
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=ip_address,
        details=payload,
        correlation_id=str(cid) if cid else None,
    )
    db.add(entry)
    return entry


async def log_audit_event(
    db: AsyncSession,
    *,
    user=None,
    action: str,
    resource_type: str,
    resource_id=None,
    result: str = "success",
    ip_address: str | None = None,
    details: dict | None = None,
    event_type: str | None = None,
    outcome: str | None = None,
    correlation_id: str | None = None,
):
    """Generic audit-trail writer for the canonical AuditLog table.

    Identical shape/semantics to log_tefca_event but named for non-TEFCA
    events (e.g. upload malware/file scans, event_type "file_scan"). `result`
    ("pass"/"fail"/…) and any extra context land in the `details` JSON column.
    Caller is responsible for committing the session.
    """
    return await log_tefca_event(
        db,
        user=user,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        ip_address=ip_address,
        details=details,
        event_type=event_type,
        outcome=outcome,
        correlation_id=correlation_id,
    )
