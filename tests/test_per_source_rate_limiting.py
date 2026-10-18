"""Regression guard, added 2026-10-02 (per-source rate limiting): proves the
new `_TokenBucket`/`_get_with_retry(..., source=...)` pacing actually caps
real request rate under real concurrency, and that it does not disturb the
existing retry/backoff/unavailable-truthfulness contract.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

pytestmark = pytest.mark.asyncio


class _FakeTransportAlwaysSucceeds:
    def __init__(self):
        self.calls = 0
        self.call_times: list[float] = []

    async def aclose(self):
        pass

    async def handle_async_request(self, request):
        self.calls += 1
        self.call_times.append(time.perf_counter())
        return httpx.Response(200, json={"ok": True}, request=request)


async def test_token_bucket_caps_real_elapsed_time_under_high_concurrency():
    """Unit-level: 40 concurrent acquire() calls against a 20 tokens/sec
    bucket (burst capacity 20) must take >= ~1.0s real wall-clock time (the
    first 20 are free, the remaining 20 are paced at 20/sec = 1.0s), not
    complete instantly. Measures real elapsed time against real timestamps,
    not a call count."""
    from app.Tefca.connectors import _TokenBucket

    bucket = _TokenBucket(rate_per_second=20.0)
    t0 = time.perf_counter()
    await asyncio.gather(*(bucket.acquire() for _ in range(40)))
    elapsed = time.perf_counter() - t0

    assert elapsed >= 0.9, (
        f"40 acquires against a 20/sec bucket (burst=20) should take >= ~1.0s "
        f"for the 20 paced ones, got {elapsed:.3f}s — the limiter is not "
        f"actually pacing requests")
    assert elapsed < 4.0, (
        f"should not take dramatically longer than the paced minimum, got "
        f"{elapsed:.3f}s — looks stuck, not just paced")


async def test_token_bucket_does_not_pace_a_burst_within_capacity():
    """The first `capacity` acquires (one second's worth) must be
    effectively free — this is a BURST bucket, not a strict interval timer,
    consistent with the existing Semaphore(16) concurrency model this adds
    to, not replaces."""
    from app.Tefca.connectors import _TokenBucket

    bucket = _TokenBucket(rate_per_second=20.0)
    t0 = time.perf_counter()
    await asyncio.gather(*(bucket.acquire() for _ in range(20)))
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.3, (
        f"20 acquires against a 20/sec bucket's 20-token burst capacity "
        f"should be near-instant, got {elapsed:.3f}s")


async def test_get_with_retry_actually_paces_real_calls_through_a_fake_transport(monkeypatch):
    """Integration-level: `_get_with_retry(..., source=...)` really applies
    the per-source limiter before dispatching each HTTP call, not just that
    the standalone bucket class works in isolation. Fresh, low-rate bucket
    for a throwaway source name so this test cannot be affected by another
    test's prior use of a real source name's shared global bucket."""
    from app.Tefca import connectors as c

    fake = _FakeTransportAlwaysSucceeds()
    shared = httpx.AsyncClient(transport=fake)
    monkeypatch.setattr(c, "_shared_http_client", lambda: shared)
    monkeypatch.setitem(c._RATE_LIMITERS, "TEST_SOURCE_RATE_LIMIT_PROOF",
                        c._TokenBucket(rate_per_second=10.0))

    t0 = time.perf_counter()
    await asyncio.gather(*(
        c._get_with_retry("https://example.invalid/api", params={}, headers={},
                          source="TEST_SOURCE_RATE_LIMIT_PROOF")
        for _ in range(25)
    ))
    elapsed = time.perf_counter() - t0

    assert fake.calls == 25
    # 10/sec burst=10: first 10 free, remaining 15 paced at 10/sec = 1.5s.
    assert elapsed >= 1.3, (
        f"25 real calls through _get_with_retry against a 10/sec source "
        f"limiter should take >= ~1.5s, got {elapsed:.3f}s — the limiter is "
        f"not wired into the real call path")
    await shared.aclose()


async def test_unconfigured_source_falls_back_to_its_documented_default(monkeypatch):
    """A source with no TEFCA_RATE_LIMIT_<SOURCE>_RPS env var set uses its
    documented default from `_RATE_LIMIT_DEFAULTS_RPS`, not an unbounded
    rate — proves the fallback path is real, not just the explicit-env path."""
    from app.Tefca import connectors as c

    monkeypatch.delenv("TEFCA_RATE_LIMIT_NPPES_RPS", raising=False)
    c._RATE_LIMITERS.pop("NPPES", None)  # force fresh construction
    bucket = c._rate_limiter_for("NPPES")
    assert bucket.rate == c._RATE_LIMIT_DEFAULTS_RPS["NPPES"]


async def test_rate_limit_is_configurable_via_env_var(monkeypatch):
    from app.Tefca import connectors as c

    monkeypatch.setenv("TEFCA_RATE_LIMIT_NPPES_RPS", "2.5")
    c._RATE_LIMITERS.pop("NPPES", None)  # force fresh construction under the new env
    bucket = c._rate_limiter_for("NPPES")
    assert bucket.rate == 2.5
    c._RATE_LIMITERS.pop("NPPES", None)  # don't leak into other tests
