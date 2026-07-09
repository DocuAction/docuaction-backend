"""
JWT revocation (NIST 800-53 AC-12 — session termination; IA-11 — re-authentication).

Provides IMMEDIATE invalidation of issued JWTs for:
  • logout            → revoke the presented token's jti
  • password change   → revoke ALL of a user's tokens issued before the change
  • account disable    → enforced separately via the DB is_active flag (works for
                         every token instantly, no store entry required)

Design goals (all satisfied without changing the token format or auth flow):
  • Additive & backward compatible — nothing is revoked by default, so existing
    sessions continue to work; only explicit revoke_* calls take effect.
  • Pluggable backend behind a single async interface (RevocationStore): Redis
    when REDIS_URL is set and the redis package is importable (e.g. Azure Cache
    for Redis — FedRAMP-ready), otherwise a process-local in-memory store with an
    identical contract for single-instance / dev deployments.
  • Self-expiring entries — revocations live only until the token would have
    expired anyway, so the store never grows unbounded.

The in-memory store is intentionally the documented fallback: swapping to a
shared/distributed cache is a configuration change (set REDIS_URL), not a code
change, so multi-instance and FedRAMP deployments need no redesign.
"""
import os
import time
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("docuaction.revocation")

REVOCATION_ENABLED = os.getenv("TOKEN_REVOCATION_ENABLED", "true").strip().lower() != "false"
_REDIS_URL = os.getenv("REDIS_URL", "").strip()

# Fail-closed policy for the revocation CHECK when the store is unreachable.
#   false (default) → fail OPEN: allow the request and log a warning. Preserves
#                     availability (a cache blip must not 401 the whole API) and
#                     keeps current/production behavior unchanged.
#   true            → fail CLOSED: deny authentication, emit a SECURITY log with
#                     request_id/correlation_id. For high-assurance deployments.
# With the default in-memory store this path is unreachable (its operations do
# not perform I/O and cannot fail); the flag matters only with a remote backend.
REVOCATION_FAIL_CLOSED = os.getenv("REVOCATION_FAIL_CLOSED", "false").strip().lower() == "true"

# Safety ceiling so a revocation entry cannot outlive any conceivable token.
_MAX_TTL_SECONDS = int(os.getenv("TOKEN_REVOCATION_MAX_TTL", str(7 * 24 * 3600)))


class RevocationStore(ABC):
    """Async interface for token/user revocation state."""

    @abstractmethod
    async def revoke_token(self, jti: str, ttl_seconds: int) -> None: ...

    @abstractmethod
    async def is_token_revoked(self, jti: str) -> bool: ...

    @abstractmethod
    async def revoke_user_before(self, user_id: str, cutoff_ts: float, ttl_seconds: int) -> None: ...

    @abstractmethod
    async def user_cutoff(self, user_id: str) -> Optional[float]: ...


class InMemoryRevocationStore(RevocationStore):
    """Process-local fallback. Correct for a single instance; documented seam for
    a distributed backend (set REDIS_URL) with zero code change."""

    def __init__(self):
        self._tokens: dict[str, float] = {}   # jti -> expiry epoch
        self._users: dict[str, float] = {}     # user_id -> (cutoff_ts, expiry epoch)
        self._user_exp: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _prune(self):
        now = time.time()
        for jti in [k for k, exp in self._tokens.items() if exp <= now]:
            self._tokens.pop(jti, None)
        for uid in [k for k, exp in self._user_exp.items() if exp <= now]:
            self._user_exp.pop(uid, None)
            self._users.pop(uid, None)

    async def revoke_token(self, jti: str, ttl_seconds: int) -> None:
        async with self._lock:
            self._prune()
            self._tokens[jti] = time.time() + min(max(ttl_seconds, 1), _MAX_TTL_SECONDS)

    async def is_token_revoked(self, jti: str) -> bool:
        async with self._lock:
            self._prune()
            return jti in self._tokens

    async def revoke_user_before(self, user_id: str, cutoff_ts: float, ttl_seconds: int) -> None:
        async with self._lock:
            self._prune()
            self._users[user_id] = cutoff_ts
            self._user_exp[user_id] = time.time() + min(max(ttl_seconds, 1), _MAX_TTL_SECONDS)

    async def user_cutoff(self, user_id: str) -> Optional[float]:
        async with self._lock:
            self._prune()
            return self._users.get(user_id)


class RedisRevocationStore(RevocationStore):
    """Distributed backend. Keys self-expire via Redis TTL. Compatible with Azure
    Cache for Redis / any managed Redis (FedRAMP-ready)."""

    def __init__(self, client):
        self._r = client

    async def revoke_token(self, jti: str, ttl_seconds: int) -> None:
        await self._r.set(f"revoked:jti:{jti}", "1", ex=min(max(ttl_seconds, 1), _MAX_TTL_SECONDS))

    async def is_token_revoked(self, jti: str) -> bool:
        return bool(await self._r.exists(f"revoked:jti:{jti}"))

    async def revoke_user_before(self, user_id: str, cutoff_ts: float, ttl_seconds: int) -> None:
        await self._r.set(f"revoked:user:{user_id}", str(cutoff_ts), ex=min(max(ttl_seconds, 1), _MAX_TTL_SECONDS))

    async def user_cutoff(self, user_id: str) -> Optional[float]:
        val = await self._r.get(f"revoked:user:{user_id}")
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None


_store: Optional[RevocationStore] = None


def get_store() -> RevocationStore:
    """Lazily build the revocation store: Redis if configured & importable, else
    in-memory. Any Redis failure degrades safely to in-memory (logged)."""
    global _store
    if _store is not None:
        return _store
    if _REDIS_URL:
        try:
            import redis.asyncio as aioredis  # type: ignore
            _store = RedisRevocationStore(aioredis.from_url(_REDIS_URL, decode_responses=True))
            logger.info("Token revocation: using Redis backend")
            return _store
        except Exception as e:
            logger.warning(f"Token revocation: Redis unavailable ({e}); falling back to in-memory")
    _store = InMemoryRevocationStore()
    logger.info("Token revocation: using in-memory backend (set REDIS_URL for distributed revocation)")
    return _store


def _remaining_ttl(payload: dict) -> int:
    """Seconds until the token naturally expires (so the revocation entry can be
    pruned at the same moment). Defaults to the max ceiling if exp is absent."""
    exp = payload.get("exp")
    if not exp:
        return _MAX_TTL_SECONDS
    try:
        return max(int(exp - time.time()), 1)
    except (TypeError, ValueError):
        return _MAX_TTL_SECONDS


async def revoke_current_token(payload: dict) -> None:
    """Logout: revoke exactly the presented token by jti."""
    if not REVOCATION_ENABLED:
        return
    jti = payload.get("jti")
    if jti:
        await get_store().revoke_token(jti, _remaining_ttl(payload))


async def revoke_all_user_tokens(user_id: str, ttl_seconds: int = _MAX_TTL_SECONDS) -> None:
    """Password change / forced re-auth: revoke every token for a user issued
    before now. Only tokens carrying an `iat` claim are subject to the cutoff, so
    legacy tokens (which lack iat) are never retroactively killed unexpectedly —
    they simply expire on their existing short schedule."""
    if not REVOCATION_ENABLED:
        return
    await get_store().revoke_user_before(str(user_id), time.time(), ttl_seconds)


async def is_revoked(payload: dict) -> bool:
    """True if this token has been logged out or predates a user-level revocation."""
    if not REVOCATION_ENABLED:
        return False
    store = get_store()
    jti = payload.get("jti")
    if jti and await store.is_token_revoked(jti):
        return True
    sub = payload.get("sub")
    iat = payload.get("iat")
    if sub and iat is not None:
        cutoff = await store.user_cutoff(str(sub))
        if cutoff is not None:
            try:
                if float(iat) < float(cutoff):
                    return True
            except (TypeError, ValueError):
                return False
    return False
