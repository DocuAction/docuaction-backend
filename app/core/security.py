"""
Enterprise IAM — Admin accounts get 24h tokens, unlimited access
Admin emails: admin@docuaction.io, imran@docuaction.io, imran@agtbi.com
"""
import uuid
import bcrypt
import logging
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("docuaction.iam")
security = HTTPBearer()
ALGORITHM = "HS256"

ADMIN_EMAILS = {"admin@docuaction.io", "imran@docuaction.io", "imran@agtbi.com"}

ACCESS_EXPIRE_NORMAL = timedelta(minutes=15)
ACCESS_EXPIRE_ADMIN = timedelta(hours=24)
REFRESH_EXPIRE = timedelta(days=7)

# Role hierarchy. The TEFCA contract (HHSAR 352.204-71, SOW C.3) review roles are
# inserted as a ladder above the generic product roles. Only "admin" changed
# numeric value (4 -> 8) to make room; the relative ordering of the pre-existing
# viewer/contributor/manager roles is unchanged and admin remains the maximum, so
# every existing require_role("admin") check behaves exactly as before.
ROLE_HIERARCHY = {
    "viewer": 1,
    "contributor": 2,
    "manager": 3,
    # ── TEFCA contract roles (HHSAR 352.204-71 / FAR 52.212-4) ──
    "reviewer": 4,         # Task 3/4/5 front-line reviewers
    "senior_analyst": 5,   # + bucket overrides, Bucket-3 escalation queue, calibration
    "qalead": 6,           # + methodology approval, D3.1 sign-off, view all queues
    "program_manager": 7,  # + deliverable submission, full audit log, cycle management
    "admin": 8,            # full access including user management
}

SAML_CONFIG = {
    "enabled": False,
    "sp_entity_id": "https://api.docuaction.io/saml/metadata",
    "supported_providers": ["Okta", "Azure AD", "OneLogin", "PingFederate"],
    "note": "Enterprise tier. Contact sales@docuaction.io",
}

def hash_password(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw, hashed):
    return bcrypt.checkpw(pw.encode(), hashed.encode())

def create_access_token(data, is_admin=False):
    payload = data.copy()
    expire = ACCESS_EXPIRE_ADMIN if is_admin else ACCESS_EXPIRE_NORMAL
    payload["exp"] = datetime.utcnow() + expire
    payload["iat"] = datetime.utcnow()   # issued-at — checked against tokens_revoked_at
    payload["type"] = "access"
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + REFRESH_EXPIRE
    payload["iat"] = datetime.utcnow()   # issued-at — checked against tokens_revoked_at
    payload["type"] = "refresh"
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def _token_revoked(user, payload) -> bool:
    """True if this token was issued before the user's revocation epoch (logout /
    disable / password reset). Tokens minted at/after the epoch remain valid. Users
    who never revoked (tokens_revoked_at is NULL) are unaffected — grandfathered."""
    revoked_at = getattr(user, "tokens_revoked_at", None)
    if not revoked_at:
        return False
    iat = payload.get("iat")
    if iat is None:
        # Pre-epoch token (issued before this feature) while a revocation is in effect.
        return True
    try:
        issued = datetime.utcfromtimestamp(int(iat))
    except (TypeError, ValueError, OSError):
        return True
    # Compare at whole-second granularity (JWT iat is integer seconds). Flooring the
    # revocation timestamp avoids a sub-second boundary where a token minted in the
    # SAME second as the revocation (e.g. immediate re-login after logout) would be
    # wrongly rejected because iat lost the fractional part.
    return issued < revoked_at.replace(microsecond=0)

def create_token_pair(user_id, role, email=""):
    is_admin = email in ADMIN_EMAILS or role == "admin"
    data = {"sub": user_id, "role": role}
    return {
        "access_token": create_access_token(data, is_admin=is_admin),
        "refresh_token": create_refresh_token(data),
        "token_type": "bearer",
        "expires_in": int((ACCESS_EXPIRE_ADMIN if is_admin else ACCESS_EXPIRE_NORMAL).total_seconds()),
    }

def create_token(data, expires=None):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + (expires or ACCESS_EXPIRE_NORMAL)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token):
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

def _enforce_account_state(user, payload):
    """Shared authorization gate applied on EVERY authenticated request (both
    get_current_user and require_role) so disable/approval/session-revocation cannot
    be bypassed by hitting a role-gated endpoint. Existing active users (is_active=True,
    status not 'disabled', tokens_revoked_at NULL) are unaffected."""
    if not getattr(user, "is_active", True) or (getattr(user, "status", "active") or "active") == "disabled":
        raise HTTPException(403, "Your account has been disabled. Contact your administrator.")
    # Session invalidation: reject tokens revoked by logout / disable / password reset.
    if _token_revoked(user, payload):
        raise HTTPException(401, "Session has expired. Please sign in again.")

async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    payload = decode_token(creds.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token")
    from app.models.database import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    # REMOVED: email-based auto-admin escalation. Role/privilege assignment must
    # happen only via database/admin action, never by email domain
    # (HHSAR 352.204-71, FAR 52.212-4 — least privilege, authorized personnel only).
    # Never reintroduce automatic role elevation based on email address.
    #
    # ENTERPRISE SECURITY: enforce account state on EVERY authenticated request, not
    # just at login. A deactivated/disabled account (or one still awaiting approval)
    # loses access immediately on its next call, rather than lingering until its
    # access token expires.
    _enforce_account_state(user, payload)
    return user

def require_role(minimum_role):
    async def role_checker(
        creds: HTTPAuthorizationCredentials = Depends(security),
        db: AsyncSession = Depends(get_db),
    ):
        payload = decode_token(creds.credentials)
        user_id = payload.get("sub")
        token_role = payload.get("role", "viewer")
        required_level = ROLE_HIERARCHY.get(minimum_role, 0)
        user_level = ROLE_HIERARCHY.get(token_role, 0)
        if user_level < required_level:
            raise HTTPException(403, f"Required: {minimum_role}, Current: {token_role}")
        from app.models.database import User
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "User not found")
        # Same disable/approval/session-revocation enforcement as get_current_user, so
        # role-gated endpoints (e.g. TEFCA) cannot be reached by a disabled or
        # logged-out account holding a still-unexpired token.
        _enforce_account_state(user, payload)
        return user
    return role_checker

async def refresh_access_token(refresh_token, db):
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(400, "Not a refresh token")
    user_id = payload.get("sub")
    from app.models.database import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    # Re-validate account state on refresh so disabling/deactivating an account
    # actually revokes its rolling session instead of it surviving for the full
    # refresh-token lifetime. Also mints tokens with the user's CURRENT role.
    if not getattr(user, "is_active", True) or (getattr(user, "status", "active") or "active") == "disabled":
        raise HTTPException(403, "Your account has been disabled. Contact your administrator.")
    # Reject refresh tokens revoked by logout / disable / password reset.
    if _token_revoked(user, payload):
        raise HTTPException(401, "Session has expired. Please sign in again.")
    return create_token_pair(str(user.id), user.role, user.email)
