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

# Role spellings that are NOT the canonical key but mean one of the roles above.
#
# QA Round 2: an account whose stored role was a near-miss spelling resolved to
# level 0 through ROLE_HIERARCHY.get(role, 0) and was denied EVERY role-gated
# route, including read-only ones. A Level-2 Contributor logging in as
# "analyst" was refused the entity import they are explicitly authorised to
# perform (IMP-016), and the denial looked identical to a correct one.
#
# Aliasing is deliberately conservative: it only maps spellings onto a role that
# already exists, and it can never resolve to a HIGHER privilege than the name
# it is an alias for. Anything still unrecognised remains level 0 (deny) — the
# fail-closed default is kept, this only stops a typo from acting like a policy.
ROLE_ALIASES = {
    "analyst": "contributor",
    "editor": "contributor",
    "senioranalyst": "senior_analyst",
    "senior analyst": "senior_analyst",
    "qa_lead": "qalead",
    "qa lead": "qalead",
    "qualitylead": "qalead",
    "programmanager": "program_manager",
    "program manager": "program_manager",
    "pm": "program_manager",
    "administrator": "admin",
    "superadmin": "admin",
    "super_admin": "admin",
    "read_only": "viewer",
    "readonly": "viewer",
    "user": "viewer",
}


def canonical_role(role) -> str:
    """Normalise a stored/token role to a ROLE_HIERARCHY key.

    Unknown roles are returned lowercased and unchanged so they still miss the
    hierarchy and deny (level 0). This never invents privilege.
    """
    key = str(role or "").strip().lower().replace("-", "_")
    if key in ROLE_HIERARCHY:
        return key
    return ROLE_ALIASES.get(key, ROLE_ALIASES.get(key.replace("_", " "), key))


def role_level(role) -> int:
    """The privilege level of a role name, after alias normalisation. 0 = unknown."""
    return ROLE_HIERARCHY.get(canonical_role(role), 0)

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
        from app.models.database import User
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "User not found")

        # THE AUTHORIZATION DECISION USES THE DATABASE ROLE, NEVER THE TOKEN ROLE.
        #
        # This check previously read `payload.get("role")`. A JWT is a snapshot of
        # who the user was when it was minted, and it is signed — so a stale role
        # inside one is not tampering, it is simply out of date, and the signature
        # makes it look authoritative. An admin who demoted a reviewer to
        # contributor (or removed their access) in Admin -> Users & Access changed
        # the database and nothing else: the reviewer's browser kept a valid,
        # correctly-signed token asserting `role: reviewer` and kept passing every
        # role gate until it expired on its own — up to 24h for an admin token,
        # 15 minutes otherwise.
        #
        # The user row is already being loaded on the next line for account-state
        # enforcement, so reading the role from it costs nothing extra and makes a
        # role change take effect on the very next request, in BOTH directions: a
        # demotion bites immediately, and a promotion works without forcing the
        # user to sign out and back in.
        #
        # `role_level` is fail-closed — an unknown, missing or NULL role resolves
        # to 0 and is denied.
        required_level = ROLE_HIERARCHY.get(minimum_role, 0)
        db_role = getattr(user, "role", None)
        if role_level(db_role) < required_level:
            raise HTTPException(
                403, f"Required: {minimum_role}, Current: {canonical_role(db_role)}"
            )
        # Same disable/approval/session-revocation enforcement as get_current_user, so
        # role-gated endpoints (e.g. TEFCA) cannot be reached by a disabled or
        # logged-out account holding a still-unexpired token.
        _enforce_account_state(user, payload)
        return user
    # Exposed so the effective gate on a route can be asserted without minting a
    # token per role and seeding a user for each. QA-1.8 was precisely a
    # configuration defect — a router-level gate silently overriding every
    # endpoint's own declaration — and that class of bug is invisible to a test
    # that only checks the deny direction on one role.
    role_checker.minimum_role = minimum_role
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
