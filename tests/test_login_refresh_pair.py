"""MQA-2026-007 — the refresh token must actually reach the client.

`create_token_pair` has always minted an access token (15 min for non-admin
roles) AND a rotating refresh token (7 days, revocation-epoch checked by
`refresh_access_token`). But the login route returned only `access_token`, so
no client could ever call `/api/auth/refresh`: every non-admin session hard
died at the 15-minute mark, and a viewer or reviewer mid-test observed 401s on
read-only surfaces (the "viewer snapshot 401" class).

These tests drive the REAL routes over ASGI against real Postgres:

  1. login answers with the whole pair, and says when the access token expires;
  2. the refresh token exchanges for a NEW pair (rotation) that authenticates;
  3. an ACCESS token is refused by /api/auth/refresh (type check holds).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _synthetic_user(db, role="reviewer"):
    from app.core.security import hash_password
    from app.models.database import User

    email = f"refresh-pair-{uuid.uuid4().hex[:8]}@synthetic-test.docuaction.invalid"
    password = f"RefreshTest!{uuid.uuid4().hex}"
    user = User(id=uuid.uuid4(), tenant_id="synthetic-cert", email=email,
                password_hash=hash_password(password),
                full_name="SYNTHETIC refresh-pair", role=role,
                is_active=True, is_verified=True, status="active",
                allowed_modules=[])
    db.add(user)
    await db.commit()
    return email, password


async def _login(client, email, password):
    """Retry on the app's own login rate limiter — see test_determination_ownership."""
    last = None
    for attempt in range(4):
        last = await client.post("/api/auth/login",
                                 json={"email": email, "password": password})
        if last.status_code != 429:
            return last
        await asyncio.sleep(3 * (attempt + 1))
    pytest.skip(f"login still rate-limited after retries: {last.text[:120]!r}")


async def test_login_returns_the_refresh_token_and_it_rotates(db_required):
    import httpx

    from app.core.database import async_session_maker
    from app.main import app

    async with async_session_maker() as db:
        email, password = await _synthetic_user(db)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        r = await _login(client, email, password)
        assert r.status_code == 200, f"login failed: {r.text[:200]}"
        body = r.json()

        # The defect: this key was minted server-side and then dropped.
        assert body.get("refresh_token"), (
            "login must return the refresh token create_token_pair minted; "
            "without it no client can ever renew a 15-minute session")
        assert body.get("access_token")
        # Non-admin access expiry is 15 minutes and the client must be told.
        assert body.get("expires_in") == 900

        # Exchange: rotation must hand back a NEW authenticating pair.
        r2 = await client.post("/api/auth/refresh",
                               json={"refresh_token": body["refresh_token"]})
        assert r2.status_code == 200, f"refresh failed: {r2.text[:200]}"
        pair = r2.json()
        assert pair.get("access_token") and pair.get("refresh_token")
        assert pair["access_token"] != body["access_token"]
        assert pair["refresh_token"] != body["refresh_token"]

        me = await client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {pair['access_token']}"})
        assert me.status_code == 200, (
            f"refreshed access token must authenticate: {me.text[:200]}")

        # An ACCESS token is not a refresh token and must be refused as such.
        r3 = await client.post("/api/auth/refresh",
                               json={"refresh_token": pair["access_token"]})
        assert r3.status_code == 400, (
            f"an access token must not pass the refresh type check: "
            f"{r3.status_code} {r3.text[:200]}")
