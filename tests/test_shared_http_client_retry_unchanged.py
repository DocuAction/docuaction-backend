"""Regression guard, added 2026-10-02 (concurrency/connection-pooling fix
review): proves `_get_with_retry`'s real retry/backoff behavior is unchanged
by replacing the per-call `httpx.AsyncClient()` with one shared, pooled
client (`_shared_http_client`). Exercises the REAL `AsyncRetrying` logic —
other connector tests monkeypatch `_get_with_retry` itself and so never
touch this code path at all.
"""
from __future__ import annotations

import time

import httpx
import pytest

pytestmark = pytest.mark.asyncio


class _FakeTransportAlwaysFails:
    def __init__(self):
        self.calls = 0

    async def aclose(self):
        pass

    async def handle_async_request(self, request):
        self.calls += 1
        raise httpx.ConnectTimeout("synthetic: no network", request=request)


class _FakeTransportFailsTwiceThenSucceeds:
    def __init__(self):
        self.calls = 0

    async def aclose(self):
        pass

    async def handle_async_request(self, request):
        self.calls += 1
        if self.calls <= 2:
            raise httpx.ConnectTimeout("synthetic: no network", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)


async def test_always_fails_retries_exactly_RETRY_ATTEMPTS_times_then_raises(monkeypatch):
    from app.Tefca import connectors as c

    fake = _FakeTransportAlwaysFails()
    shared = httpx.AsyncClient(transport=fake)
    monkeypatch.setattr(c, "_shared_http_client", lambda: shared)
    # Keep the test fast: real backoff is 1s/2s/4s: confirm the SHAPE
    # (bounded retries, not silently swallowed) without waiting ~7s.
    monkeypatch.setattr(c, "RETRY_ATTEMPTS", 3)

    t0 = time.perf_counter()
    with pytest.raises(httpx.ConnectTimeout):
        await c._get_with_retry("https://example.invalid/api", params={}, headers={})
    elapsed = time.perf_counter() - t0

    assert fake.calls == 3, (
        f"expected exactly RETRY_ATTEMPTS=3 real attempts through the shared "
        f"client, got {fake.calls} — retry count must be unchanged by the "
        f"shared-client refactor")
    # 1s + 2s backoff between the 3 attempts (tenacity wait_exponential
    # multiplier=1, min=1, max=4) — confirms backoff actually ran, wasn't
    # skipped, and wasn't silently caught/swallowed before reaching the caller.
    assert elapsed >= 2.5, (
        f"expected real exponential backoff (>=~3s for 3 attempts), got "
        f"{elapsed:.2f}s — looks like backoff was bypassed")
    await shared.aclose()


async def test_transient_failure_recovers_and_still_reports_through_the_shared_client(monkeypatch):
    from app.Tefca import connectors as c

    fake = _FakeTransportFailsTwiceThenSucceeds()
    shared = httpx.AsyncClient(transport=fake)
    monkeypatch.setattr(c, "_shared_http_client", lambda: shared)

    resp = await c._get_with_retry("https://example.invalid/api", params={}, headers={})
    assert resp.status_code == 200
    assert fake.calls == 3, "should have failed twice, then succeeded on the 3rd real attempt"
    await shared.aclose()


async def test_a_connector_failure_is_never_silently_reported_as_verified(monkeypatch):
    """The end-to-end honesty guarantee: when every attempt fails, the
    CONNECTOR must surface this as an explicit unavailable/failed result —
    never silently default to a "verified"/"clear" outcome."""
    from app.Tefca.connectors import NPPESConnector, _shared_http_client

    fake = _FakeTransportAlwaysFails()
    shared = httpx.AsyncClient(transport=fake)
    monkeypatch.setattr("app.Tefca.connectors._shared_http_client", lambda: shared)

    from test_review_id_concurrency import _valid_npi

    conn = NPPESConnector()
    result = await conn.lookup_by_npi(_valid_npi(42))

    assert result.success is False, (
        "a connector that could never reach the source must report success=False, "
        "never silently succeed")
    assert fake.calls == 3, "the connector call must go through the same 3-attempt retry"
    await shared.aclose()
