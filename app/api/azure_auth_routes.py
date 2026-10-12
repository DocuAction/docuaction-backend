"""
Microsoft Entra ID (Azure AD) SSO — an ADDITIONAL sign-in option that lives
alongside the existing email/password authentication. It does not touch, wrap, or
replace the password flow in app/api/routes.py; it only adds two GET endpoints and
mints the SAME application JWT (via create_token_pair) so downstream authz is
identical regardless of how the user signed in.

Flow (OAuth 2.0 authorization-code, confidential client with a client secret):
  GET /api/auth/login/azure     -> 307 redirect to the Microsoft authorize endpoint
  GET /api/auth/callback/azure  -> exchanges the code, resolves/creates the local
                                   user, mints the app token pair, and redirects the
                                   browser to the frontend landing URL with the token
                                   in the URL fragment (no frontend code was modified).

Configuration (environment variables; if any are missing the endpoints return 503
and the rest of the app is unaffected):
  AZURE_AD_CLIENT_ID, AZURE_AD_CLIENT_SECRET, AZURE_AD_TENANT_ID   (required)
  AZURE_AD_DEFAULT_ROLE           default 'viewer' (least privilege on first login)
  AZURE_AD_POST_LOGIN_REDIRECT    default 'https://app.docuaction.io/auth/callback'
  AZURE_AD_ID_TOKEN_VALIDATION    'audit' (default) or 'enforce'. In both modes the
                                  ID token is validated (RS256 signature against the
                                  tenant JWKS, iss, aud, exp/nbf, tid, nonce). 'audit'
                                  only logs a failure and keeps the legacy behaviour;
                                  'enforce' rejects the sign-in. Moving to 'enforce'
                                  is an operator decision (no flag is changed here).
"""
import os
import hmac
import time
import logging
import secrets as _secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_token_pair, hash_password, ROLE_HIERARCHY
from app.models.database import User

logger = logging.getLogger("docuaction.azure_auth")
router = APIRouter()

CLIENT_ID = os.getenv("AZURE_AD_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("AZURE_AD_CLIENT_SECRET", "").strip()
TENANT_ID = os.getenv("AZURE_AD_TENANT_ID", "").strip()
DEFAULT_ROLE = (os.getenv("AZURE_AD_DEFAULT_ROLE", "viewer").strip() or "viewer")
POST_LOGIN_REDIRECT = os.getenv(
    "AZURE_AD_POST_LOGIN_REDIRECT", "https://app.docuaction.io/auth/callback"
).strip()

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
# 'audit' (default, non-breaking) logs ID-token validation failures and keeps the
# legacy behaviour; 'enforce' rejects. Any other value is treated as 'audit'.
ID_TOKEN_VALIDATION = (os.getenv("AZURE_AD_ID_TOKEN_VALIDATION", "audit").strip().lower() or "audit")
if ID_TOKEN_VALIDATION not in ("audit", "enforce"):
    logging.getLogger("docuaction.azure_auth").warning(
        "AZURE_AD_ID_TOKEN_VALIDATION has an unrecognised value; using 'audit'.")
    ID_TOKEN_VALIDATION = "audit"
JWKS_TTL_SECONDS = 3600          # cached signing keys are reused for up to an hour
JWKS_REFRESH_COOLDOWN = 60       # unknown-kid (key rotation) refetch at most once a minute
CLOCK_LEEWAY_SECONDS = 120
_jwks_cache: dict = {"keys": {}, "fetched_at": 0.0}
SCOPE = "openid profile email"
STATE_TTL = timedelta(minutes=10)

# Entra app-role value -> DocuAction RBAC role (see ROLE_HIERARCHY in core/security).
# Define matching app roles in the Entra app registration to drive these; until then
# first-time SSO users fall back to AZURE_AD_DEFAULT_ROLE (least privilege).
ENTRA_ROLE_MAP = {
    "admin": "admin", "Admin": "admin",
    "program_manager": "program_manager", "ProgramManager": "program_manager",
    "qalead": "qalead", "QALead": "qalead",
    "senior_analyst": "senior_analyst", "SeniorAnalyst": "senior_analyst",
    "reviewer": "reviewer", "Reviewer": "reviewer",
    "manager": "manager", "Manager": "manager",
    "contributor": "contributor", "Contributor": "contributor",
    "viewer": "viewer", "Viewer": "viewer",
}


def _configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and TENANT_ID)


def _require_configured():
    if not _configured():
        raise HTTPException(503, "Azure AD SSO is not configured on this server.")


def _redirect_uri(request: Request) -> str:
    """Backend callback URL, built from the public host the user reached and forced to
    https (App Service terminates TLS at the edge, so request.url.scheme can be http).
    Must exactly match a redirect URI registered on the Entra app."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"https://{host}/api/auth/callback/azure"


def _make_state(redirect_uri: str, nonce: str = None) -> str:
    """Signed, expiring CSRF state — no server-side session storage needed. The
    state's nonce is also sent to Microsoft as the OIDC nonce so the returned
    ID token can be tied to this sign-in attempt."""
    payload = {
        "typ": "oauth_state",
        "nonce": nonce or _secrets.token_urlsafe(16),
        "ru": redirect_uri,
        "exp": datetime.utcnow() + STATE_TTL,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _check_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(400, "Invalid or expired SSO state.")
    if payload.get("typ") != "oauth_state":
        raise HTTPException(400, "Invalid SSO state.")
    return payload


class IdTokenError(Exception):
    """The Microsoft ID token failed validation. `reason` is a short, value-free
    code that is safe to log; `status` is the HTTP status used when enforcing."""

    def __init__(self, reason: str, status: int = 403):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _issuer() -> str:
    return f"{AUTHORITY}/v2.0"


async def _fetch_jwks() -> list:
    """Download the tenant's signing keys (the JWKS published by Microsoft)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{AUTHORITY}/discovery/v2.0/keys")
        resp.raise_for_status()
        return list(resp.json().get("keys") or [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Azure JWKS fetch failed: %s", type(e).__name__)
        raise IdTokenError("jwks_unavailable", 502)


async def _get_signing_key(kid: str) -> dict:
    """Signing key for `kid`: cached for JWKS_TTL_SECONDS; an unknown kid (key
    rotation) triggers one refetch, rate limited by JWKS_REFRESH_COOLDOWN."""
    now = time.monotonic()
    age = now - _jwks_cache["fetched_at"]
    if kid in _jwks_cache["keys"] and age < JWKS_TTL_SECONDS:
        return _jwks_cache["keys"][kid]
    if _jwks_cache["fetched_at"] == 0.0 or age >= JWKS_REFRESH_COOLDOWN:
        keys = await _fetch_jwks()
        _jwks_cache["keys"] = {k["kid"]: k for k in keys if k.get("kid")}
        _jwks_cache["fetched_at"] = time.monotonic()
    key = _jwks_cache["keys"].get(kid)
    if key is None:
        raise IdTokenError("unknown_kid")
    return key


async def validate_id_token(id_token: str, expected_nonce: str = None) -> dict:
    """Fully validate a Microsoft ID token and return its claims.

    Checks: RS256 only (alg=none / HS* rejected), signature against the tenant JWKS,
    iss == the tenant v2.0 issuer, aud == this client id, exp/nbf (with a small
    leeway), tid == the configured tenant, and the nonce when one is expected.
    Raises IdTokenError on any failure."""
    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError:
        raise IdTokenError("malformed")
    if header.get("alg") != "RS256":
        raise IdTokenError("bad_alg")
    kid = header.get("kid")
    if not kid:
        raise IdTokenError("no_kid")
    key = await _get_signing_key(kid)
    try:
        claims = jwt.decode(
            id_token, key, algorithms=["RS256"],
            audience=CLIENT_ID, issuer=_issuer(),
            options={"require_exp": True, "require_iss": True, "require_aud": True,
                     "require_sub": True, "leeway": CLOCK_LEEWAY_SECONDS},
        )
    except JWTError as e:
        raise IdTokenError("invalid_" + type(e).__name__.lower())
    if claims.get("tid") != TENANT_ID:
        raise IdTokenError("tid_mismatch")
    if expected_nonce is not None and not hmac.compare_digest(
            str(claims.get("nonce") or ""), str(expected_nonce)):
        raise IdTokenError("nonce_mismatch")
    return claims


def _map_role(claims: dict) -> str:
    """Highest-privilege DocuAction role among the Entra 'roles' claim, else the
    configured default. Never elevates an EXISTING user (handled by the caller)."""
    best, best_level = None, -1
    for r in (claims.get("roles") or []):
        mapped = ENTRA_ROLE_MAP.get(r)
        if mapped and ROLE_HIERARCHY.get(mapped, 0) > best_level:
            best, best_level = mapped, ROLE_HIERARCHY.get(mapped, 0)
    if best:
        return best
    return DEFAULT_ROLE if DEFAULT_ROLE in ROLE_HIERARCHY else "viewer"


@router.get("/api/auth/sso/status", tags=["Auth"])
async def sso_status():
    """Whether Entra SSO is usable on THIS server. Public and unauthenticated —
    the sign-in page must be able to ask before anyone has a token.

    LOGIN-007: the login page had no "Sign in with Microsoft" control at all, so
    the case could not pass. It cannot simply be added unconditionally either:
    the initiation route 503s when AZURE_AD_* is unset, and a button that always
    fails is worse than no button. The page asks here and shows the control only
    where SSO actually works.

    Deliberately returns nothing but a boolean and a provider name. Client ids
    and tenant ids are not secrets, but an unauthenticated endpoint has no
    reason to hand out the tenant's identifiers.
    """
    return {"enabled": _configured(), "provider": "microsoft" if _configured() else None}


@router.get("/api/auth/login/azure", tags=["Auth"])
async def login_azure(request: Request):
    """Kick off Entra sign-in — redirect the browser to Microsoft."""
    _require_configured()
    redirect_uri = _redirect_uri(request)
    nonce = _secrets.token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": SCOPE,
        "state": _make_state(redirect_uri, nonce),
        "nonce": nonce,
        "prompt": "select_account",
    }
    return RedirectResponse(f"{AUTHORITY}/oauth2/v2.0/authorize?{urlencode(params)}", status_code=307)


@router.get("/api/auth/callback/azure", tags=["Auth"])
async def callback_azure(
    request: Request,
    db: AsyncSession = Depends(get_db),
    code: str = Query(default=None),
    state: str = Query(default=None),
    error: str = Query(default=None),
    error_description: str = Query(default=None),
):
    """Handle the Microsoft redirect: validate state, exchange code, resolve/create the
    local user, mint the SAME JWT as password login, hand it to the frontend."""
    _require_configured()
    if error:
        raise HTTPException(400, f"Microsoft sign-in failed: {error_description or error}")
    if not code or not state:
        raise HTTPException(400, "Missing authorization code or state.")
    st = _check_state(state)
    redirect_uri = st.get("ru") or _redirect_uri(request)

    token_url = f"{AUTHORITY}/oauth2/v2.0/token"
    form = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(token_url, data=form)
    except httpx.HTTPError as e:
        logger.warning("Azure token endpoint unreachable: %s", e)
        raise HTTPException(502, "Could not reach Microsoft to complete sign-in.")
    if resp.status_code != 200:
        logger.warning("Azure token exchange failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "Microsoft token exchange failed.")

    id_token = resp.json().get("id_token")
    if not id_token:
        raise HTTPException(400, "No ID token returned by Microsoft.")
    # The id_token arrives directly from Microsoft's token endpoint over TLS in the
    # confidential (client-secret) code flow. It is nevertheless validated in full
    # (signature, iss, aud, exp/nbf, tid, nonce). In the default 'audit' mode a
    # validation failure is logged and the legacy behaviour (unverified claims plus
    # the tid/aud checks below) is kept; 'enforce' rejects the sign-in.
    try:
        claims = await validate_id_token(id_token, st.get("nonce"))
    except IdTokenError as e:
        logger.warning("Azure ID token validation failed: reason=%s mode=%s",
                       e.reason, ID_TOKEN_VALIDATION)
        if ID_TOKEN_VALIDATION == "enforce":
            raise HTTPException(e.status, "ID token validation failed.")
        try:
            claims = jwt.get_unverified_claims(id_token)
        except JWTError:
            raise HTTPException(400, "Could not read Microsoft ID token.")

    if claims.get("tid") and claims["tid"] != TENANT_ID:
        raise HTTPException(403, "Sign-in is restricted to the organization tenant.")
    if claims.get("aud") and claims["aud"] != CLIENT_ID:
        raise HTTPException(403, "ID token audience mismatch.")

    email = (claims.get("preferred_username") or claims.get("email") or claims.get("upn") or "").strip().lower()
    if not email:
        raise HTTPException(400, "Microsoft account has no email/UPN claim.")
    name = claims.get("name") or email.split("@")[0]

    # Link by email: an existing password user with the same email is reused (no
    # duplicate account), otherwise a local account is provisioned on first SSO login.
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()
    created = False
    if user is None:
        # is_verified/status exist on the hardened User model but not on older branches;
        # set them only when the column is present so this module stays portable (on a
        # model without them the account is simply active by default).
        _extra = {k: v for k, v in (("is_verified", True), ("status", "active")) if hasattr(User, k)}
        user = User(
            email=email,
            password_hash=hash_password(_secrets.token_urlsafe(32)),  # random, unusable — SSO-only
            full_name=name,
            role=_map_role(claims),
            is_active=True,
            **_extra,
        )
        db.add(user)
        created = True
    elif name and not user.full_name:
        user.full_name = name       # backfill only; never override an existing role via SSO
    user.last_active_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    logger.info("Azure SSO login: %s (%s, role=%s)", email, "new" if created else "existing", user.role)

    tokens = create_token_pair(str(user.id), user.role, user.email)
    # Deliver the token to the frontend via URL fragment (not query) so it never lands
    # in server logs or Referer headers. Frontend wiring to read it is a follow-up
    # (frontend was intentionally not modified here).
    fragment = urlencode({
        "access_token": tokens["access_token"],
        "token_type": tokens["token_type"],
        "expires_in": tokens["expires_in"],
        "new_user": "1" if created else "0",
    })
    return RedirectResponse(f"{POST_LOGIN_REDIRECT}#{fragment}", status_code=302)
