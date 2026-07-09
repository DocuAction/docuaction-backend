"""
API Rate Limiting & Burst Protection
Tier-based limits: Free=60/min, Pro=200/min, Business=500/min, Enterprise=unlimited
In-memory sliding window counter (no Redis dependency for MVP).

SECURITY HARDENING (NIST 800-53 SC-5 — Denial-of-Service protection):
By default the middleware runs in SCOPED mode and enforces a strict brute-force
limit ONLY on sensitive authentication endpoints (login / signup / refresh /
password reset & change). Every other endpoint passes through untouched, so
existing traffic — TEFCA data endpoints, health checks, and internal scheduler
jobs (which never traverse HTTP) — is unaffected. All limits and the sensitive
path set are environment-configurable, so tightening is a config change (no code
change) — FedRAMP-ready. Set RATE_LIMIT_SCOPE=all to also apply tier limits
globally; RATE_LIMIT_ENABLED=false disables entirely.
"""
import os
import re
import time
import logging
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("docuaction.ratelimit")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (ValueError, AttributeError):
        return default


# ── Environment-configurable knobs (safe defaults; no behavior change for
#    non-sensitive traffic) ────────────────────────────────────────────────
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() != "false"
RATE_LIMIT_SCOPE = os.getenv("RATE_LIMIT_SCOPE", "sensitive").strip().lower()  # "sensitive" | "all"
AUTH_RATE_PER_MINUTE = _env_int("RATE_LIMIT_AUTH_PER_MINUTE", 10)
AUTH_RATE_BURST = _env_int("RATE_LIMIT_AUTH_BURST", 5)

# Sensitive endpoints (brute-force / credential-stuffing surface). Matched by URL
# path substring, so it is independent of which router serves them. Override with
# a comma-separated RATE_LIMIT_SENSITIVE_PATHS to add e.g. TEFCA high-risk routes.
_DEFAULT_SENSITIVE = (
    "/api/auth/login,/api/auth/signup,/api/auth/register,/api/auth/refresh,"
    "/api/auth/forgot-password,/api/auth/reset-password,/api/auth/change-password,"
    "/api/auth/emergency-reset,/auth/login,/auth/register,/auth/reset-password,"
    "/auth/change-password,/set-password"
)
SENSITIVE_PATHS = [
    p.strip() for p in os.getenv("RATE_LIMIT_SENSITIVE_PATHS", _DEFAULT_SENSITIVE).split(",") if p.strip()
]

# ═══ RATE LIMIT TIERS ═══
RATE_LIMITS = {
    "free": {"requests_per_minute": 60, "burst_max": 10},
    "pro": {"requests_per_minute": 200, "burst_max": 30},
    "business": {"requests_per_minute": 500, "burst_max": 50},
    "enterprise": {"requests_per_minute": 10000, "burst_max": 500},
    "admin": {"requests_per_minute": 10000, "burst_max": 500},
    "default": {"requests_per_minute": 60, "burst_max": 10},
    # Strict brute-force ceiling for authentication endpoints (per client IP).
    "auth": {"requests_per_minute": AUTH_RATE_PER_MINUTE, "burst_max": AUTH_RATE_BURST},
}

# Sliding window storage: {user_key: [timestamp, timestamp, ...]}
_request_log = defaultdict(list)
_burst_log = defaultdict(list)


def _clean_old_entries(entries: list, window_seconds: int) -> list:
    """Remove entries older than the window."""
    cutoff = time.time() - window_seconds
    return [t for t in entries if t > cutoff]


def check_rate_limit(user_key: str, tier: str = "free") -> dict:
    """
    Check if a request is within rate limits.
    Returns: {"allowed": True/False, "remaining": int, "reset_in": int}
    """
    limits = RATE_LIMITS.get(tier, RATE_LIMITS["default"])
    max_requests = limits["requests_per_minute"]
    burst_max = limits["burst_max"]
    now = time.time()

    # Clean old entries (60-second window for rate limit)
    _request_log[user_key] = _clean_old_entries(_request_log[user_key], 60)

    # Check rate limit
    current_count = len(_request_log[user_key])
    if current_count >= max_requests:
        oldest = min(_request_log[user_key]) if _request_log[user_key] else now
        reset_in = int(60 - (now - oldest))
        logger.warning(f"RATE LIMIT HIT | user={user_key} tier={tier} count={current_count}/{max_requests}")
        return {"allowed": False, "remaining": 0, "reset_in": max(reset_in, 1), "limit": max_requests}

    # Check burst (5-second window)
    _burst_log[user_key] = _clean_old_entries(_burst_log[user_key], 5)
    burst_count = len(_burst_log[user_key])
    if burst_count >= burst_max:
        logger.warning(f"BURST LIMIT HIT | user={user_key} tier={tier} burst={burst_count}/{burst_max}")
        return {"allowed": False, "remaining": 0, "reset_in": 5, "limit": max_requests}

    # Allow request
    _request_log[user_key].append(now)
    _burst_log[user_key].append(now)

    return {
        "allowed": True,
        "remaining": max_requests - current_count - 1,
        "reset_in": 60,
        "limit": max_requests,
    }


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that enforces rate limits on all API endpoints.
    Extracts user identity from JWT token or falls back to IP address.
    """
    # Endpoints exempt from rate limiting
    EXEMPT_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        # Master switch — disabled → transparent pass-through.
        if not RATE_LIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path

        # Skip rate limiting for health checks and docs. (Internal scheduler jobs
        # never traverse HTTP, so they are inherently exempt.)
        if path in self.EXEMPT_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        is_sensitive = any(p in path for p in SENSITIVE_PATHS)

        # SCOPED (default) mode: only sensitive auth endpoints are throttled;
        # every other path passes through exactly as before (no regression).
        if RATE_LIMIT_SCOPE != "all" and not is_sensitive:
            return await call_next(request)

        # Sensitive endpoints are keyed per client IP (login is pre-auth) with the
        # strict "auth" ceiling; other paths (scope=all) use the tier model.
        if is_sensitive:
            client_ip = request.client.host if request.client else "unknown"
            user_key, tier = f"auth:{client_ip}:{path}", "auth"
        else:
            user_key, tier = self._extract_identity(request)

        # Check rate limit
        result = check_rate_limit(user_key, tier)

        if not result["allowed"]:
            from app.core.error_handler import create_error_response
            return create_error_response(
                status_code=429,
                error=f"Rate limit exceeded. {result['limit']} requests/minute allowed for {tier} tier.",
                code="RATE_LIMIT_EXCEEDED",
            )

        # Process request and add rate limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(result["limit"])
        response.headers["X-RateLimit-Remaining"] = str(result["remaining"])
        response.headers["X-RateLimit-Reset"] = str(result["reset_in"])
        return response

    def _extract_identity(self, request: Request) -> tuple:
        """Extract user ID and tier from JWT, or fall back to IP."""
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            try:
                from app.core.security import decode_token
                payload = decode_token(auth.replace("Bearer ", ""))
                user_id = payload.get("sub", "unknown")
                role = payload.get("role", "contributor")
                # Map role to tier
                tier_map = {"admin": "enterprise", "manager": "business", "contributor": "pro", "viewer": "free"}
                tier = tier_map.get(role, "free")
                return f"user:{user_id}", tier
            except Exception:
                pass

        # Fallback to IP address
        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}", "free"
