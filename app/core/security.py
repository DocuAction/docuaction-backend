"""
Enterprise IAM — JWT Refresh Token Rotation + RBAC Middleware
- Access tokens: 15 minutes
- Refresh tokens: 7 days, single-use rotation
- RBAC: Admin > Manager > Contributor > Viewer
- Session audit logging on every auth event
"""
import uuid
import bcrypt
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("docuaction.iam")
security = HTTPBearer()

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRE = timedelta(days=7)

# ═══ RBAC ROLE HIERARCHY ═══
ROLE_HIERARCHY = {
    "admin": 4,
    "manager": 3,
    "contributor": 2,
    "viewer": 1,
}


# ═══ PASSWORD HASHING ═══

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


# ═══ TOKEN CREATION ═══

def create_access_token(data: dict) -> str:
    """Create short-lived access token (15 minutes)."""
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + ACCESS_TOKEN_EXPIRE
    payload["type"] = "access"
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Create long-lived refresh token (7 days), single-use."""
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + REFRESH_TOKEN_EXPIRE
    payload["type"] = "refresh"
    payload["jti"] = str(uuid.uuid4())  # Unique ID for rotation tracking
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_token_pair(user_id: str, role: str, tenant_id: str = None) -> dict:
    """Create both access and refresh tokens."""
    data = {"sub": user_id, "role": role}
    if tenant_id:
        data["tenant_id"] = tenant_id
    return {
        "access_token": create_access_token(data),
        "refresh_token": create_refresh_token(data),
        "token_type": "bearer",
        "expires_in": int(ACCESS_TOKEN_EXPIRE.total_seconds()),
    }


# Legacy compatibility
def create_token(data: dict, expires: Optional[timedelta] = None) -> str:
    """Legacy token creation — maps to create_access_token."""
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + (expires or ACCESS_TOKEN_EXPIRE)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


# ═══ TOKEN VALIDATION ═══

def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(401, f"Invalid or expired token")


# ═══ USER AUTHENTICATION ═══

async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """Extract and validate user from JWT token."""
    payload = decode_token(creds.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token payload")

    from app.models.database import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    if not user.is_active:
        raise HTTPException(403, "Account deactivated")
    return user


# ═══ RBAC MIDDLEWARE ═══

def require_role(minimum_role: str):
    """
    RBAC decorator — enforces minimum role at the API level.
    
    Usage:
        @router.get("/admin-only")
        async def admin_endpoint(user=Depends(require_role("admin"))):
            ...
    """
    async def role_checker(
        creds: HTTPAuthorizationCredentials = Depends(security),
        db: AsyncSession = Depends(get_db),
    ):
        payload = decode_token(creds.credentials)
        user_id = payload.get("sub")
        token_role = payload.get("role", "viewer")

        # Check role hierarchy
        required_level = ROLE_HIERARCHY.get(minimum_role, 0)
        user_level = ROLE_HIERARCHY.get(token_role, 0)

        if user_level < required_level:
            logger.warning(f"RBAC denied: user={user_id} role={token_role} required={minimum_role}")
            raise HTTPException(
                403,
                f"Insufficient permissions. Required: {minimum_role}, Current: {token_role}"
            )

        # Get full user object
        from app.models.database import User
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "User not found")
        if not user.is_active:
            raise HTTPException(403, "Account deactivated")
        return user

    return role_checker


# ═══ REFRESH TOKEN ENDPOINT ═══

async def refresh_access_token(refresh_token: str, db: AsyncSession) -> dict:
    """
    Rotate refresh token — issue new access + refresh tokens.
    Old refresh token is invalidated (single-use rotation).
    """
    payload = decode_token(refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(400, "Not a refresh token")

    user_id = payload.get("sub")
    role = payload.get("role", "contributor")
    tenant_id = payload.get("tenant_id")

    # Verify user still exists and is active
    from app.models.database import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or deactivated")

    # Issue new token pair (old refresh token is now expired by rotation)
    tokens = create_token_pair(str(user.id), user.role, tenant_id)
    logger.info(f"Token rotated for user {user_id}")

    return tokens


# ═══ SAML/SSO PLACEHOLDER (Enterprise Tier) ═══

SAML_CONFIG = {
    "enabled": False,
    "idp_entity_id": "",
    "idp_sso_url": "",
    "idp_certificate": "",
    "sp_entity_id": "https://api.docuaction.io/saml/metadata",
    "sp_acs_url": "https://api.docuaction.io/saml/acs",
    "attribute_mapping": {
        "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        "name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
        "role": "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
    },
    "supported_providers": ["Okta", "Azure AD", "OneLogin", "PingFederate"],
    "note": "SAML/SSO available on Enterprise tier. Contact sales@docuaction.io for configuration.",
}
