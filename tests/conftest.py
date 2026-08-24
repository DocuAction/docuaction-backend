"""Shared pytest fixtures.

TWO THINGS THIS FILE EXISTS TO HANDLE

1. Import-time configuration. app.main refuses to import without SECRET_KEY (64
   char minimum), DATABASE_URL, and ALLOWED_HOSTS. These are set here before the
   import so a developer can run the suite without a .env, and so CI does not need
   production values to run tests that never touch a database.

2. Database availability. Most of this suite tests the request boundary - routing,
   authentication, authorization, headers - which does not need a database. A
   smaller set does. Rather than fail the whole suite on a machine with no
   Postgres, the db_required fixture skips those with an explicit reason. A skip
   that names its cause is honest; a suite that cannot run at all gets ignored,
   and a suite that silently passes because it tested nothing is worse than both.

TestClient is deliberately constructed WITHOUT the context-manager form. Starlette
only runs lifespan startup inside `with TestClient(app)`, and startup calls
create_all() against the real database. Plain construction gives a client that
exercises routing and middleware without provisioning schema anywhere.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import unquote, urlparse

import pytest

# Must precede the app import.
os.environ.setdefault("SECRET_KEY", "t" * 64)
os.environ.setdefault("ALLOWED_HOSTS", "*")
os.environ.setdefault("ENABLE_OPENAPI", "true")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test"
)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

# Every TestClient request runs the app on its own short-lived event loop, but
# the async engine's connection pool is module-level and outlives it. A pooled
# connection opened by one test gets handed to the next, where awaiting it fails
# with "Event loop is closed" - which surfaces as a 500 and reads as an
# application bug. Which test takes the hit depends on collection order, so the
# failure moves around as tests are added. NullPool keeps a connection's life
# inside the request that opened it, so nothing crosses a loop boundary. This is
# a test-harness concern only; production keeps the real pool.
#
# There are two engines: app.database.engine, and the lazily-built one behind
# app.core.database.get_db - which is the one the auth routes actually use. Both
# are switched, and the lazy one is forced into existence first so it does not
# come back later with the default pool.
def _use_null_pool() -> None:
    from sqlalchemy.pool import NullPool

    engines = []
    try:
        from app.core import database as core_db

        engines.append(core_db._get_engine())
    except Exception:
        pass
    try:
        from app.database import engine as app_engine

        engines.append(app_engine)
    except Exception:
        pass
    for eng in engines:
        try:
            sync = eng.sync_engine
            sync.pool = NullPool(sync.pool._creator, dialect=sync.dialect)
        except Exception:
            pass


try:
    _use_null_pool()
except Exception:  # pragma: no cover - never block the suite on this
    pass


def _database_reachable() -> bool:
    """Attempt a real connection, not just a TCP handshake.

    A TCP check is not sufficient here: a Postgres listening on the port with
    different credentials accepts the socket and then fails every query, which
    surfaces as a 500 and looks like an application bug rather than a missing
    test database. Authenticating is the only check that distinguishes them.
    """
    raw = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(raw.replace("postgresql+asyncpg://", "postgresql://"))
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except Exception:
        return False
    try:
        import asyncio

        import asyncpg

        # urlparse does NOT percent-decode userinfo. A password containing any
        # URL-reserved character arrives here still encoded ("p+w" as "p%2Bw"),
        # authentication fails, DB_AVAILABLE goes False, and ~41 database-backed
        # tests skip with "No database reachable" against a database that is
        # perfectly reachable. The suite then reports green while covering less.
        #
        # SQLAlchemy decodes correctly, so the application works and only this
        # probe misreads the URL - which is what made it survive so long. Found
        # 2026-08-24 when the DEV runtime credential was rotated to one
        # containing '+' and '@'.
        user = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        database = unquote((parsed.path or "/").lstrip("/")) or "postgres"

        async def _probe():
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=host, port=port,
                    user=user, password=password,
                    database=database,
                ),
                timeout=4,
            )
            await conn.close()
            return True

        return bool(asyncio.run(_probe()))
    except Exception:
        return False


DB_AVAILABLE = _database_reachable()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the in-memory rate-limit windows before each test.

    The global limiter allows 10 requests per burst window per client IP, and
    every test shares the TestClient address. Without this, tests fail with 429
    in whatever order pytest happens to run them - a failure caused by the
    harness, not by the code, and one that moves around as tests are added.
    Resetting is preferable to disabling: the limiter middleware still executes,
    so its own behaviour stays covered.
    """
    try:
        from app.core import rate_limiter as rl

        rl._request_log.clear()
        rl._burst_log.clear()
    except Exception:
        pass
    # The login throttles are separate module-level buckets. Without clearing
    # them, the account-lockout and per-IP windows carry across tests - every
    # test shares one client address, so a later test sees a 429 earned by an
    # earlier one.
    try:
        from app.api import routes as api_routes

        api_routes._login_fail_by_account.clear()
        api_routes._login_attempts_by_ip.clear()
        api_routes._signup_by_ip.clear()
    except Exception:
        pass
    yield


@pytest.fixture(scope="session")
def db_available() -> bool:
    return DB_AVAILABLE


@pytest.fixture
def db_required():
    """Skip a test that cannot run without a database."""
    if not DB_AVAILABLE:
        pytest.skip(
            "No database reachable at DATABASE_URL. This test exercises a "
            "database-backed path; skipping rather than reporting a false failure."
        )


@pytest.fixture
def client() -> TestClient:
    # No `with`: lifespan startup runs create_all() against the real database and
    # is not something a unit test should trigger.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_headers(client, db_required):
    """A bearer header for a test user, or {} if the account could not be created.

    Signup and login both require a database, hence db_required. Tests using this
    fixture assert on authorization behaviour, so an empty dict would make them
    silently test the unauthenticated path instead - they check for a token.
    """
    signup = client.post(
        "/api/auth/signup",
        json={
            "email": "test@test.local",
            "password": "TestPassword123!",
            "full_name": "Test User",
            "company": "Test Corp",
        },
    )
    if signup.status_code not in (200, 201, 409, 400):
        return {}
    login = client.post(
        "/api/auth/login",
        json={"email": "test@test.local", "password": "TestPassword123!"},
    )
    if login.status_code != 200:
        return {}
    token = login.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


# Status codes that both mean "correctly gated". FastAPI 0.140 returns 401 for a
# missing credential where 0.115 returned 403; role refusal for an authenticated
# principal is still 403. Asserting on one alone breaks across that upgrade.
GATED = (401, 403)
