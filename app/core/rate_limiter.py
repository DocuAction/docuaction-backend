"""
API Rate Limiting & Burst Protection
Tier-based limits: Free=60/min, Pro=200/min, Business=500/min, Enterprise=unlimited
In-memory sliding window counter (no Redis dependency for MVP).
"""
import time
import logging
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("docuaction.ratelimit")

# ═══ RATE LIMIT TIERS ═══
RATE_LIMITS = {
    "free": {"requests_per_minute": 60, "burst_max": 10},
    "pro": {"requests_per_minute": 200, "burst_max": 30},
    "business": {"requests_per_minute": 500, "burst_max": 50},
    "enterprise": {"requests_per_minute": 10000, "burst_max": 500},
    "admin": {"requests_per_minute": 10000, "burst_max": 500},
    "default": {"requests_per_minute": 60, "burst_max": 10},
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
        path = request.url.path

        # Skip rate limiting for health checks and docs
        if path in self.EXEMPT_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        # Determine user key and tier
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
