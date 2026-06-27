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
):
    """
    Append a TEFCA audit record. Every required field is captured:
    user_id, action, resource_type, resource_id, timestamp(created_at),
    ip_address, result. Caller is responsible for committing the session.

    `user` is the authenticated principal (has `.id`); pass None for system
    actions (e.g. background batch validation), which are recorded with a null
    user_id and an explicit actor in `details`.
    """
    payload = {"result": result}
    if details:
        payload.update(details)
    entry = _CanonicalAuditLog(
        user_id=getattr(user, "id", None),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=ip_address,
        details=payload,
    )
    db.add(entry)
    return entry
