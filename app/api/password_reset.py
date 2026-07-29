"""
Password Reset Flow — Secure + Auditable
POST /api/auth/forgot-password — sends reset link
POST /api/auth/reset-password — validates token and resets password

Security:
- Single-use JWT reset tokens (1-hour expiry; single-use enforced by password
  fingerprint — a token stops working the moment the password changes)
- Rate limited (3 requests per 15 minutes per IP)
- NEVER reveals whether email exists
- Bcrypt password hashing
- Full audit logging (success, failure, expired)
"""
import os
import uuid
import time
import hashlib
import logging
import re
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError

from app.core.config import settings
from app.core.database import get_db
from app.core.security import hash_password
from app.core.email import (
    send_email, send_password_reset_email, send_password_changed_email, app_url,
)
from app.models.database import User, AuditLog
from app.services.audit_logger import log_ai_request
from app.core.client_ip import get_client_ip

logger = logging.getLogger("docuaction.auth.reset")
router = APIRouter(prefix="/api/auth", tags=["Auth — Password Reset"])

RESET_TOKEN_EXPIRE = timedelta(hours=1)     # password-reset links: 1 hour
INVITE_TOKEN_EXPIRE = timedelta(hours=72)   # invitation set-password links: 3 days
ALGORITHM = "HS256"

# Rate limiting: {ip: [timestamp, ...]}
_reset_rate_limit = defaultdict(list)
RATE_LIMIT_MAX = 3        # max 3 requests
RATE_LIMIT_WINDOW = 900   # per 15 minutes


# ═══ SCHEMAS ═══

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class ForgotPasswordResponse(BaseModel):
    message: str


# ═══ HELPERS ═══

def _check_rate_limit(ip: str) -> bool:
    """Returns True if rate limit exceeded."""
    now = time.time()
    _reset_rate_limit[ip] = [t for t in _reset_rate_limit[ip] if t > now - RATE_LIMIT_WINDOW]
    if len(_reset_rate_limit[ip]) >= RATE_LIMIT_MAX:
        return True
    _reset_rate_limit[ip].append(now)
    return False


def _pw_fingerprint(password_hash: str) -> str:
    """Short, non-reversible fingerprint of the CURRENT password hash.

    Embedded in the reset token and re-checked at reset time. Because the fingerprint
    changes the instant the password changes, a token is single-use — once it (or any
    other outstanding token) resets the password, every token minted against the old
    hash stops matching. No storage / migration needed; works across workers/restarts.
    """
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def _create_reset_token(user_id: str, email: str, password_hash: str = "",
                        expires: timedelta = None) -> str:
    """Create a single-use JWT reset/set-password token.

    Single-use is enforced by binding the token to the current password fingerprint
    (see _pw_fingerprint), not just the jti. `expires` defaults to the 1-hour reset
    window; invitations pass INVITE_TOKEN_EXPIRE.
    """
    payload = {
        "sub": user_id,
        "email": email,
        "type": "password_reset",
        "jti": str(uuid.uuid4()),
        "pwf": _pw_fingerprint(password_hash),
        "exp": datetime.utcnow() + (expires or RESET_TOKEN_EXPIRE),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def _validate_password(password: str) -> str:
    """Enforce strong password rules. Returns error message or empty string."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if len(password) > 128:
        return "Password must be less than 128 characters"
    if not re.search(r'[A-Z]', password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r'[0-9]', password):
        return "Password must contain at least one number"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return "Password must contain at least one special character"
    return ""


async def _log_reset_event(db: AsyncSession, user_id: str, ip: str, status: str, details: str = ""):
    """Log password reset attempt to audit trail."""
    audit = AuditLog(
        tenant_id="default",
        user_id=user_id if user_id != "unknown" else None,
        action="password_reset",
        resource_type="auth",
        resource_id=None,
        details={
            "status": status,
            "ip_address": ip,
            "details": details,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
        ip_address=ip,
    )
    db.add(audit)
    try:
        await db.commit()
    except Exception:
        await db.rollback()

    logger.info(f"PASSWORD RESET AUDIT | user={user_id} | status={status} | ip={ip} | {details}")


# ═══ ENDPOINTS ═══

@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Request a password reset link.
    
    Security:
    - Rate limited: 3 requests per 15 minutes per IP
    - NEVER reveals whether email exists (always returns success message)
    - Generates single-use JWT reset token (1-hour expiry)
    """
    ip = get_client_ip(request) or "unknown"

    # Rate limit check
    if _check_rate_limit(ip):
        await _log_reset_event(db, "unknown", ip, "rate_limited", f"email={data.email}")
        # Still return generic message — don't reveal rate limiting
        return ForgotPasswordResponse(
            message="If an account with this email exists, a password reset link has been sent."
        )

    # Look up user (but NEVER reveal if email exists)
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if user:
        # Bind the token to the current password fingerprint (single-use).
        reset_token = _create_reset_token(str(user.id), user.email, user.password_hash)

        reset_url = f"{app_url()}/reset-password?token={reset_token}"
        logger.info(f"RESET LINK GENERATED | email={user.email}")

        # Send via the platform's SendGrid sender. Best-effort: send_email never raises
        # and is a no-op dry-run when SENDGRID_API_KEY is unset, so email problems never
        # change the generic response below (so it never becomes an enumeration oracle).
        await send_password_reset_email(user.email, reset_url)

        await _log_reset_event(db, str(user.id), ip, "link_sent", f"email={user.email}")
    else:
        # Log attempt but don't reveal email doesn't exist
        await _log_reset_event(db, "unknown", ip, "email_not_found", f"email={data.email}")

    # Always return same message regardless of whether email exists
    return ForgotPasswordResponse(
        message="If an account with this email exists, a password reset link has been sent."
    )


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Reset password using a valid reset token.
    
    Security:
    - Token must be valid JWT with type=password_reset
    - Token must not be expired (1-hour window)
    - Strong password validation enforced
    - Full audit logging
    """
    ip = get_client_ip(request) or "unknown"

    # Validate password strength
    password_error = _validate_password(data.new_password)
    if password_error:
        raise HTTPException(400, password_error)

    # Decode and validate token
    try:
        payload = jwt.decode(data.token, settings.SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("type") != "password_reset":
            raise HTTPException(400, "Invalid reset token")

        user_id = payload.get("sub")
        email = payload.get("email")

    except JWTError:
        await _log_reset_event(db, "unknown", ip, "invalid_token", "Token decode failed")
        raise HTTPException(400, "Invalid or expired reset token")

    # Find user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        await _log_reset_event(db, user_id, ip, "user_not_found")
        raise HTTPException(400, "Invalid or expired reset token")

    # SINGLE-USE: the token's password fingerprint must match the user's CURRENT
    # password hash. Once this (or any other outstanding) token resets the password,
    # the hash — and therefore the fingerprint — changes, so the token can never be
    # replayed. Tokens minted before this change (older code) have no "pwf" and are
    # also rejected here, which is safe (they can request a fresh link).
    if payload.get("pwf") != _pw_fingerprint(user.password_hash):
        await _log_reset_event(db, str(user.id), ip, "token_already_used",
                               "reset token fingerprint mismatch")
        raise HTTPException(400, "Invalid or expired reset token")

    # Update password
    user.password_hash = hash_password(data.new_password)
    user.updated_at = datetime.utcnow()
    # Terminate existing sessions on credential change (NIST 800-63B / OWASP ASVS
    # session termination). Any token issued before now is rejected after this reset.
    try:
        user.tokens_revoked_at = datetime.utcnow()
    except Exception:
        pass  # column absent on a not-yet-migrated DB — reset itself must still succeed
    await db.commit()

    await _log_reset_event(db, str(user.id), ip, "success", f"Password reset for {email}")

    # Confirmation email (best-effort — never blocks the successful reset).
    await send_password_changed_email(user.email)

    return {
        "message": "Password has been reset successfully. You can now log in with your new password.",
        "status": "success",
    }
