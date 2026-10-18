"""Regression guard (2026-10-03): the OIG LEIE CSV is loaded ONCE when many
concurrent callers find a cold cache -- not once per caller.

Why it matters (measured on the development host, scripts/perf/leie_load_timing.py):
one cold load ~3.5s / +120MB; sixteen simultaneous cold loads -- what
`arc_pipeline`'s first 16-wide evidence-gather wave did before the fix --
44.2s wall-clock and a 452MB process peak. No network here: the download is
stubbed with a tiny in-memory CSV; the test asserts the CALL COUNT and the
shared result, which is the whole contract.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

pytestmark = pytest.mark.asyncio

_CSV = ("LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,"
        "EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\n"
        "DOE,JANE,,,,,,1234567893,,,,,,1128a1,20200101,00000000,,\n"
        ",,,SYNTHETIC EXCLUDED ORG,,,,0000000000,,,,,,1128b4,20210101,00000000,,\n")


class _FakeClient:
    calls = 0
    delay = 0.05

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        _FakeClient.calls += 1
        await asyncio.sleep(self.delay)   # let every waiter pile up on the lock
        return httpx.Response(200, text=_CSV, request=httpx.Request("GET", url))


@pytest.fixture
def cold_leie(monkeypatch):
    from app.Tefca import connectors as c
    monkeypatch.setitem(c._LEIE_CACHE, "row_count", 0)
    monkeypatch.setitem(c._LEIE_CACHE, "loaded_at", 0.0)
    monkeypatch.setitem(c._LEIE_CACHE, "by_npi", {})
    monkeypatch.setitem(c._LEIE_CACHE, "by_name", {})
    monkeypatch.setattr(c, "_LEIE_LOAD_LOCK", None)
    monkeypatch.setattr(c.httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = 0
    return c


async def test_sixteen_concurrent_cold_callers_share_one_download(cold_leie):
    c = cold_leie
    results = await asyncio.gather(*(c._ensure_leie_loaded() for _ in range(16)))
    assert all(results), "every caller must see a successful load"
    assert _FakeClient.calls == 1, (
        f"the CSV must be downloaded once for 16 concurrent cold callers, got {_FakeClient.calls}")
    assert c._LEIE_CACHE["row_count"] == 2


async def test_warm_cache_never_downloads_again(cold_leie):
    c = cold_leie
    assert await c._ensure_leie_loaded() is True
    assert await c._ensure_leie_loaded() is True
    assert _FakeClient.calls == 1


async def test_lookup_results_are_identical_to_before(cold_leie):
    """The index content and the connector's answer are unchanged by the
    guard -- an excluded NPI is still excluded, a clean one still clean."""
    c = cold_leie
    conn = c.OIGLEIEConnector()
    hit = await conn.lookup_by_npi("1234567893")
    assert hit.success and hit.data["excluded"] is True
    org = await conn.lookup_by_name(last="", first="", org="Synthetic Excluded Org")
    assert org.success and org.data["excluded"] is True
    assert _FakeClient.calls == 1


async def test_failed_download_stays_fail_closed_and_retries_next_time(cold_leie, monkeypatch):
    c = cold_leie

    class _Down(_FakeClient):
        async def get(self, url, headers=None):
            _FakeClient.calls += 1
            return httpx.Response(503, text="", request=httpx.Request("GET", url))

    monkeypatch.setattr(c.httpx, "AsyncClient", _Down)
    results = await asyncio.gather(*(c._ensure_leie_loaded() for _ in range(4)))
    assert results == [False, False, False, False], "an unavailable CSV must never read as loaded"
    # Each caller that reaches the loader while the cache is still cold makes
    # its own attempt (the lock serialises them; it does not cache a failure)
    # -- the next call must still be able to recover.
    monkeypatch.setattr(c.httpx, "AsyncClient", _FakeClient)
    assert await c._ensure_leie_loaded() is True
