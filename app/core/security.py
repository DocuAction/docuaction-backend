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
    payload["type"] = "access"
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + REFRESH_EXPIRE
    payload["type"] = "refresh"
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)

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
    return create_token_pair(str(user.id), user.role, user.email)
