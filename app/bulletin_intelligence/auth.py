"""FCC Bulletin — flag-gated endpoint authorization + rate limiting (Phase 2).

Reuses the SHARED auth (app.core.security.require_role) — imports only, never
modifies it. Gated by env flags so the change is ADDITIVE and REVERSIBLE:

  * BULLETIN_AUTH_ENABLED=false (default): guards are no-ops → endpoints behave
    exactly as before (no regression). No token required.
  * BULLETIN_AUTH_ENABLED=true: the shared require_role(role) is enforced on
    state-changing / costly endpoints.

  * BULLETIN_RATE_LIMIT_ENABLED=false (default): rate_limit() is a no-op.
  * BULLETIN_RATE_LIMIT_ENABLED=true: per-client hourly cap on the wired
    endpoints (collect/send), returning 429 when exceeded.

Flags are read at import time (deploy-time config). Nothing here runs unless a
flag is explicitly turned on.
"""
import os
import time
from fastapi import Depends, Request, HTTPException
from app.core.security import require_role

BULLETIN_AUTH_ENABLED = os.getenv("BULLETIN_AUTH_ENABLED", "false").strip().lower() == "true"
BULLETIN_RATE_LIMIT_ENABLED = os.getenv("BULLETIN_RATE_LIMIT_ENABLED", "false").strip().lower() == "true"
_RATE_MAX_PER_HOUR = int(os.getenv("BULLETIN_RATE_MAX_PER_HOUR", "20"))

# Role floor per bulletin action (uses the shared ROLE_HIERARCHY names):
#   contributor -> trigger collection / refresh / run / LLM check
#   qalead      -> deliver (send) / approve
#   admin       -> purge archive / register agencies


def guard(role: str):
    """FastAPI `dependencies=` list. [] when auth is off (current behavior),
    else [Depends(require_role(role))]."""
    return [Depends(require_role(role))] if BULLETIN_AUTH_ENABLED else []


# ── Lightweight in-memory rate limiter (best-effort, per-process) ────────────
_BUCKET: dict = {}


async def rate_limit(request: Request):
    """Dependency: per-client hourly cap on costly endpoints. No-op unless
    BULLETIN_RATE_LIMIT_ENABLED=true. Never raises when disabled."""
    if not BULLETIN_RATE_LIMIT_ENABLED:
        return
    key = (request.client.host if request and request.client else "anon")
    now = time.time()
    window = 3600.0
    hits = [t for t in _BUCKET.get(key, []) if now - t < window]
    if len(hits) >= _RATE_MAX_PER_HOUR:
        raise HTTPException(429, "Rate limit exceeded — try again later.")
    hits.append(now)
    _BUCKET[key] = hits
