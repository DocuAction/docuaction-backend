"""Perigon quota-protection tests.

Context: the 150/day free tier was exhausted by ~06:00. The drain was
amplification — 9 Boolean profiles x 3 cycle attempts = 27 calls per cycle, and
scheduler._run_cycle_with_retry counts a cycle as failed when it returns no
articles, so an exhausted quota made every subsequent hourly watchdog tick spend
another 27 calls.

These tests pin the three guards that stop it: a 24h response cache, a per-run
call cap, and a daily budget that latches on 429. All of them must degrade to
"return what we have" rather than raising, because a Perigon failure must never
stop a bulletin.
"""
import httpx
import pytest

from app.bulletin_intelligence.providers import perigon


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"articles": []}
        self.text = text

    def json(self):
        return self._payload


class CountingClient:
    """Stands in for httpx.AsyncClient, counting billable GETs."""

    def __init__(self, response=None, responses=None):
        self.calls = 0
        self._response = response or FakeResponse(payload={"articles": [{"x": 1}]})
        self._responses = list(responses) if responses else None

    async def get(self, url, params=None):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0) if self._responses else self._response
        return self._response


@pytest.fixture(autouse=True)
def _clean_state():
    perigon._reset_for_tests()
    yield
    perigon._reset_for_tests()


@pytest.mark.asyncio
async def test_cache_prevents_a_second_billable_call():
    """The retry/watchdog loop is what burned the quota. A repeated identical
    query must be served from cache."""
    client = CountingClient()
    first = await perigon._fetch(client, "FCC AND spectrum", "2026-08-04")
    second = await perigon._fetch(client, "FCC AND spectrum", "2026-08-04")

    assert first == second == [{"x": 1}]
    assert client.calls == 1, "second identical query must not hit the API"


@pytest.mark.asyncio
async def test_distinct_queries_are_cached_separately():
    client = CountingClient()
    await perigon._fetch(client, "query A", "2026-08-04")
    await perigon._fetch(client, "query B", "2026-08-04")
    assert client.calls == 2


@pytest.mark.asyncio
async def test_cache_key_includes_from_date():
    """A new day must not serve yesterday's answer."""
    client = CountingClient()
    await perigon._fetch(client, "same query", "2026-08-04")
    await perigon._fetch(client, "same query", "2026-08-05")
    assert client.calls == 2


@pytest.mark.asyncio
async def test_daily_budget_stops_calls_and_returns_empty(monkeypatch):
    monkeypatch.setattr(perigon, "DAILY_BUDGET", 2)
    client = CountingClient()
    for i in range(5):
        result = await perigon._fetch(client, f"q{i}", "2026-08-04")
        assert isinstance(result, list)  # never raises, always a list

    assert client.calls == 2, "budget must hard-stop billable calls"
    assert perigon.budget_status()["remaining"] == 0


@pytest.mark.asyncio
async def test_429_latches_exhausted_and_stops_further_calls():
    """Once the tier is spent, further probes only burn latency."""
    client = CountingClient(responses=[FakeResponse(status_code=429, text="quota")])
    out = await perigon._fetch(client, "q", "2026-08-04")

    assert out == []
    assert perigon.budget_status()["exhausted"] is True

    await perigon._fetch(client, "another q", "2026-08-04")
    assert client.calls == 1, "no further calls once exhausted latches"


@pytest.mark.asyncio
async def test_failures_are_not_cached():
    """Caching a failure would suppress a legitimate retry after quota reset."""
    client = CountingClient(responses=[
        FakeResponse(status_code=500, text="server error"),
        FakeResponse(payload={"articles": [{"ok": True}]}),
    ])
    first = await perigon._fetch(client, "q", "2026-08-04")
    second = await perigon._fetch(client, "q", "2026-08-04")

    assert first == []
    assert second == [{"ok": True}]
    assert client.calls == 2


@pytest.mark.asyncio
async def test_budget_rolls_over_on_new_utc_date(monkeypatch):
    monkeypatch.setattr(perigon, "DAILY_BUDGET", 1)
    client = CountingClient()
    await perigon._fetch(client, "q1", "2026-08-04")
    assert perigon.budget_status()["remaining"] == 0

    perigon._budget["date"] = "2026-08-03"  # simulate the date advancing
    assert perigon.budget_status()["remaining"] == 1
    assert perigon.budget_status()["exhausted"] is False


@pytest.mark.asyncio
async def test_ingest_returns_empty_without_key(monkeypatch):
    """No key must be a silent no-op, not an error that fails the cycle."""
    monkeypatch.setattr(perigon, "PERIGON_ENABLED", False)
    assert await perigon.ingest_perigon(object()) == []


@pytest.mark.asyncio
async def test_health_probe_respects_budget(monkeypatch):
    """Polling provider health was silently competing for the same 150/day."""
    monkeypatch.setattr(perigon, "PERIGON_ENABLED", True)
    monkeypatch.setattr(perigon, "PERIGON_API_KEY", "test-key")
    perigon._budget["exhausted"] = True
    perigon._budget["date"] = perigon.datetime.now(perigon.timezone.utc).strftime("%Y-%m-%d")

    out = await perigon.perigon_health()
    assert out["status"] == "quota_exhausted"
    assert out["budget"]["exhausted"] is True


def test_budget_status_shape():
    s = perigon.budget_status()
    for key in ("date", "calls_today", "daily_budget", "remaining",
                "exhausted", "cache_entries", "cache_ttl_s", "max_calls_per_run"):
        assert key in s, f"budget_status missing {key}"


def test_daily_budget_is_below_the_free_tier():
    """A ceiling at or above 150 would not protect anything."""
    assert perigon.DAILY_BUDGET < 150
