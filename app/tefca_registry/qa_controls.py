"""Track A3 - QA independence controls as pure, flag-gated checks.

Everything here is DEFAULT OFF (see the ENABLE_* settings). The functions are
pure so they are testable without a database; callers supply the facts.

Denial codes are machine-readable strings returned in the `X-Denial-Code`
response header and (when ENABLE_DENIAL_AUDIT is on) written to the audit
trail, so every refusal of a QA or release act is queryable by code.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# QA gate denial codes
SOD_SELF_QA = "SOD_SELF_QA"
SOD_GRANTOR_MISSING = "SOD_GRANTOR_MISSING"
SOD_GRANTOR_IS_ACTOR = "SOD_GRANTOR_IS_ACTOR"
SOD_GRANTOR_IS_ANALYST = "SOD_GRANTOR_IS_ANALYST"
SOD_GRANTOR_NOT_FOUND = "SOD_GRANTOR_NOT_FOUND"
SOD_GRANTOR_INACTIVE = "SOD_GRANTOR_INACTIVE"
SOD_GRANTOR_NOT_ADMIN = "SOD_GRANTOR_NOT_ADMIN"
QA_REFUSED = "QA_REFUSED"
# Report release denial codes (PROPOSAL, O-01)
RELEASE_GENERATOR_IS_RELEASER = "RELEASE_GENERATOR_IS_RELEASER"
RELEASE_GENERATOR_UNKNOWN = "RELEASE_GENERATOR_UNKNOWN"
RELEASE_READ_ACK_REQUIRED = "RELEASE_READ_ACK_REQUIRED"
RELEASE_TRANSITION_REFUSED = "RELEASE_TRANSITION_REFUSED"

ADMIN_ROLE = "admin"


def flag(name: str) -> bool:
    from app.core.config import settings
    return bool(getattr(settings, name, False))


def grantor_denial(*, grantor, grantor_id, actor_id, analyst_id) -> Optional[str]:
    """Machine code if the SoD-exception grantor is not acceptable, else None.

    `grantor` is the loaded user (or None when no such user exists). Role is
    normalised through the same alias table the role gate uses, and the level
    comparison uses ROLE_HIERARCHY so the ladder keeps one definition.
    """
    from app.core.security import role_level

    if grantor_id is None:
        return SOD_GRANTOR_MISSING
    if grantor_id == actor_id:
        return SOD_GRANTOR_IS_ACTOR
    if analyst_id is not None and grantor_id == analyst_id:
        return SOD_GRANTOR_IS_ANALYST
    if grantor is None:
        return SOD_GRANTOR_NOT_FOUND
    if not bool(getattr(grantor, "is_active", True)) or \
            str(getattr(grantor, "status", "active") or "active").lower() != "active":
        return SOD_GRANTOR_INACTIVE
    if role_level(getattr(grantor, "role", "")) < role_level(ADMIN_ROLE):
        return SOD_GRANTOR_NOT_ADMIN
    return None


def release_denial(*, action: str, generator_id: Any, generator_email: Optional[str],
                   actor_id: Any, actor_email: Optional[str],
                   acknowledge_read: bool,
                   separation_enabled: bool, read_ack_enabled: bool) -> Optional[str]:
    """PROPOSAL (O-01): machine code if a report release act must be refused.

    Applies to PM_REVIEWED and READY_FOR_DELIVERY (RETURNED_TO_DRAFT is always
    allowed: sending work back cannot release anything). Fails CLOSED when
    separation is on but the generator is unknown.
    """
    action = (action or "").strip().upper()
    if separation_enabled and action in ("PM_REVIEWED", "READY_FOR_DELIVERY"):
        if generator_id is None and not generator_email:
            return RELEASE_GENERATOR_UNKNOWN
        same_id = generator_id is not None and actor_id is not None \
            and str(generator_id) == str(actor_id)
        same_mail = bool(generator_email) and bool(actor_email) \
            and generator_email.strip().lower() == actor_email.strip().lower()
        if same_id or same_mail:
            return RELEASE_GENERATOR_IS_RELEASER
    if read_ack_enabled and action == "READY_FOR_DELIVERY" and not acknowledge_read:
        return RELEASE_READ_ACK_REQUIRED
    return None


def denial_http(exc_message: str, code: str) -> dict:
    """kwargs for HTTPException(409, ...) carrying the machine code header."""
    return {"status_code": 409, "detail": exc_message,
            "headers": {"X-Denial-Code": code}}


async def audit_denial(db, *, action: str, code: str, user, ip_address=None,
                       metadata: Optional[dict] = None) -> None:
    """Persist one refusal with its machine code (ENABLE_DENIAL_AUDIT only).

    Rolls back the caller's unit of work first - a refused act must leave no
    partial state - then commits just the audit row. Never raises.
    """
    if not flag("ENABLE_DENIAL_AUDIT"):
        return
    try:
        from app.tefca_registry import audit as reg_audit
        await db.rollback()
        actor_id, actor_email = reg_audit.actor_of(user)
        reg_audit.record(db, action, None, actor_id=actor_id,
                         actor_email=actor_email, ip_address=ip_address,
                         metadata={"denial_code": code, **(metadata or {})})
        await db.commit()
    except Exception:  # an audit failure must not mask the refusal
        logger.exception("denial audit write failed (code=%s)", code)
