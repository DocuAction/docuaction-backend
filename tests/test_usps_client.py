"""USPS APIs v3 client.

Two properties matter more than the rest and are tested hardest:

  1. With no credentials, nothing breaks. The system has shipped without a USPS
     key and must keep working exactly as before until one is added.
  2. No token or secret ever reaches a log. This runs in a system under
     FedRAMP-style review; a bearer token in a log file is a credential
     disclosure, not a cosmetic defect.

Requests go through httpx.MockTransport rather than a patched method, so retry,
backoff, and 401-refresh are exercised as real HTTP round trips.
"""
import asyncio
import logging

import httpx
import pytest

from app.tefca_registry import usps_client as uc
from app.tefca_registry.usps_client import (AddressMatch, CircuitBreaker,
                                            USPSAddressResult, USPSClient)

TOKEN = "supersecret-token-value-abc123"
SECRET = "client-secret-do-not-log-xyz789"

ADDRESS_OK = {
    "address": {"streetAddress": "123 N MAIN ST", "city": "SPRINGFIELD",
                "state": "VA", "ZIPCode": "22150", "ZIPPlus4": "1234"},
    "additionalInfo": {"DPVConfirmation": "Y"},
}


def _client(handler, **kw) -> USPSClient:
    """A configured client whose network is `handler`. Backoff sleeps are
    swallowed — the schedule is asserted directly instead of waited out."""
    client = USPSClient(client_id="cid", client_secret=SECRET,
                        transport=httpx.MockTransport(handler), **kw)
    client._sleep = _record_sleep(client)
    return client


def _record_sleep(client):
    client.slept = []

    async def _sleep(seconds):
        client.slept.append(seconds)

    return _sleep


def _token_response():
    return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})


# ── Not configured ────────────────────────────────────────────────────────────

def test_not_configured_returns_unavailable(monkeypatch):
    monkeypatch.delenv("USPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("USPS_CLIENT_SECRET", raising=False)
    client = USPSClient()
    assert client.configured is False
    result = asyncio.run(client.standardize("123 Main St", city="Reston", state="VA"))
    assert result.available is False
    assert result.method == "usps_not_configured"
    assert result.standardized_street is None


def test_not_configured_falls_back_to_code(monkeypatch):
    """The whole system must behave as it did before USPS existed."""
    from app.tefca_registry.address_normalizer import ThreeLayerAddressNormalizer

    monkeypatch.delenv("USPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("USPS_CLIENT_SECRET", raising=False)
    uc.reset_usps_client()

    match = asyncio.run(ThreeLayerAddressNormalizer().standardize_and_compare(
        "123 North Main Street, Reston, VA 20190",
        "456 Oak Avenue, Reston, VA 20190"))
    assert match.method == "code_normalization"
    assert match.match is False
    assert match.dpv_confirmed is None


# ── Tokens ────────────────────────────────────────────────────────────────────

def test_token_caching():
    """One token for many requests. USPS counts token calls."""
    calls = {"token": 0, "address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            calls["token"] += 1
            return _token_response()
        calls["address"] += 1
        return httpx.Response(200, json=ADDRESS_OK)

    client = _client(handler)

    async def run():
        for _ in range(3):
            await client.standardize("123 Main St", city="Springfield", state="VA")

    asyncio.run(run())
    assert calls["address"] == 3
    assert calls["token"] == 1


def test_token_refresh_on_401():
    """A stale token is refreshed and the request retried ONCE. A second 401 is a
    real auth failure and must not loop."""
    calls = {"token": 0, "address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            calls["token"] += 1
            return _token_response()
        calls["address"] += 1
        if calls["address"] == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json=ADDRESS_OK)

    client = _client(handler)
    result = asyncio.run(client.standardize("123 Main St", city="Springfield",
                                            state="VA"))
    assert result.available is True
    assert calls["token"] == 2, "token should have been refetched after the 401"
    assert calls["address"] == 2


def test_repeated_401_fails_without_looping():
    calls = {"address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return _token_response()
        calls["address"] += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _client(handler)
    result = asyncio.run(client.standardize("123 Main St"))
    assert result.available is False
    assert result.method == "usps_error"
    assert calls["address"] == 2, "exactly one refresh-and-retry, then stop"
    assert client._auth_failed is True
    assert client.health()["status"] == "Authentication failed"


# ── Retries ───────────────────────────────────────────────────────────────────

def test_retry_on_500():
    calls = {"address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return _token_response()
        calls["address"] += 1
        if calls["address"] < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=ADDRESS_OK)

    client = _client(handler)
    result = asyncio.run(client.standardize("123 Main St"))
    assert result.available is True
    assert calls["address"] == 3
    assert client.slept == [1.0, 2.0]


def test_retry_on_429_with_backoff():
    """Exponential, bounded, and in that order — a flat or unbounded retry against
    a rate limiter turns a slow period into an outage."""
    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return _token_response()
        return httpx.Response(429, json={"error": "rate limited"})

    client = _client(handler)
    result = asyncio.run(client.standardize("123 Main St"))
    assert result.available is False
    assert client.slept == [1.0, 2.0, 4.0], "3 retries, doubling"


@pytest.mark.parametrize("status", [400, 404])
def test_no_retry_on_400(status):
    """A malformed request fails identically on repeat. Retrying spends quota to
    receive the same rejection."""
    calls = {"address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return _token_response()
        calls["address"] += 1
        return httpx.Response(status, json={"error": "bad"})

    client = _client(handler)
    result = asyncio.run(client.standardize("123 Main St"))
    assert result.available is False
    assert calls["address"] == 1
    assert client.slept == []


# ── Circuit breaker ───────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _always_500(request):
    if request.url.path == uc.OAUTH_ENDPOINT:
        return _token_response()
    return httpx.Response(500, json={"error": "down"})


def test_circuit_breaker_opens_after_5_failures():
    clock = FakeClock()
    client = _client(_always_500, clock=clock)

    async def run():
        for _ in range(5):
            await client.standardize("123 Main St")

    asyncio.run(run())
    assert client.circuit.status == CircuitBreaker.OPEN
    assert client.metrics_snapshot()["usps_circuit_breaker_status"] == "open"

    # Sixth call must not reach the network at all.
    before = client.metrics.errors_total
    result = asyncio.run(client.standardize("123 Main St"))
    assert result.error == "circuit breaker open"
    assert client.metrics.errors_total == before, "no upstream call was made"


def test_circuit_breaker_closes_on_success():
    clock = FakeClock()
    state = {"fail": True}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return _token_response()
        if state["fail"]:
            return httpx.Response(500, json={"error": "down"})
        return httpx.Response(200, json=ADDRESS_OK)

    client = _client(handler, clock=clock)

    async def run():
        for _ in range(5):
            await client.standardize("123 Main St")

    asyncio.run(run())
    assert client.circuit.status == CircuitBreaker.OPEN

    # Still inside the cooldown — no probe yet.
    clock.advance(299)
    assert asyncio.run(client.standardize("123 Main St")).error == "circuit breaker open"

    # Cooldown elapsed and upstream recovered: one probe closes the circuit.
    clock.advance(2)
    state["fail"] = False
    result = asyncio.run(client.standardize("123 Main St"))
    assert result.available is True
    assert client.circuit.status == CircuitBreaker.CLOSED
    assert client.circuit.consecutive_failures == 0


def test_circuit_breaker_reopens_when_the_probe_fails():
    """A failed probe must serve another full cooldown, not re-probe immediately."""
    clock = FakeClock()
    client = _client(_always_500, clock=clock)

    async def run():
        for _ in range(5):
            await client.standardize("123 Main St")

    asyncio.run(run())
    clock.advance(301)
    asyncio.run(client.standardize("123 Main St"))       # probe, fails
    assert client.circuit.status == CircuitBreaker.OPEN
    result = asyncio.run(client.standardize("123 Main St"))
    assert result.error == "circuit breaker open"


def test_circuit_breaker_falls_back_to_code(monkeypatch):
    """With the circuit open the three-layer comparison must still answer, from
    code normalization, rather than propagating the outage."""
    from app.tefca_registry.address_normalizer import ThreeLayerAddressNormalizer

    clock = FakeClock()
    client = _client(_always_500, clock=clock)

    async def run():
        for _ in range(5):
            await client.standardize("123 Main St")

    asyncio.run(run())
    assert client.circuit.is_open() is True

    match = asyncio.run(
        ThreeLayerAddressNormalizer(client=client).standardize_and_compare(
            "123 North Main Street, Reston, VA 20190",
            "999 Oak Avenue, Reston, VA 20190"))
    assert match.method == "code_normalization"
    assert match.match is False


# ── Safe logging ──────────────────────────────────────────────────────────────

def test_never_logs_token_or_secret(caplog):
    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return _token_response()
        return httpx.Response(200, json=ADDRESS_OK)

    client = _client(handler)
    with caplog.at_level(logging.DEBUG, logger="docuaction.tefca.usps_v3"):
        asyncio.run(client.standardize("123 Main St", city="Springfield", state="VA"))

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN not in blob
    assert SECRET not in blob
    assert "Bearer" not in blob
    # The useful fields ARE logged — a log with nothing in it also passes a
    # "no secrets" check, so pin that this one is actually informative.
    assert "endpoint=" in blob
    assert "latency_ms=" in blob


def test_safe_log_drops_credential_fields(caplog):
    with caplog.at_level(logging.INFO, logger="docuaction.tefca.usps_v3"):
        uc._safe_log(logging.INFO, "probe", request_id="abc",
                     access_token="LEAK", client_secret="LEAK",
                     authorization="Bearer LEAK", city="Reston")
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "LEAK" not in blob
    assert "request_id=abc" in blob
    assert "city=Reston" in blob


def test_full_street_is_not_logged(caplog):
    """City and state are enough to trace a request; the street is PII."""
    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return _token_response()
        return httpx.Response(200, json=ADDRESS_OK)

    client = _client(handler)
    with caplog.at_level(logging.DEBUG, logger="docuaction.tefca.usps_v3"):
        asyncio.run(client.standardize("1600 Pennsylvania Avenue NW",
                                       city="Washington", state="DC"))
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "Pennsylvania" not in blob
    assert "city=" in blob


# ── Models ────────────────────────────────────────────────────────────────────

def test_pydantic_model_validation():
    result = USPSAddressResult(available=True, method="usps_api")
    assert result.corrections == []
    assert result.dpv_confirmed is False
    assert result.zip4 is None

    # Default lists must not be shared between instances.
    result.corrections.append("x")
    assert USPSAddressResult(available=True, method="usps_api").corrections == []

    with pytest.raises(Exception):
        USPSAddressResult(method="usps_api")          # `available` is required

    match = AddressMatch(match=True, confidence=1.0, method="usps_api")
    assert match.usps_zip4_match is None
    assert match.model_dump()["confidence"] == 1.0


def test_parse_maps_dpv_codes_to_deliverability():
    """D and S mean the building is deliverable but the suite is wrong. Treating
    them as confirmed would assert a precision USPS did not give us."""
    def build(code):
        payload = {"address": ADDRESS_OK["address"],
                   "additionalInfo": {"DPVConfirmation": code}}
        return USPSClient._parse(payload, street="123 N Main St", city="",
                                 state="", zip5="", latency_ms=1.0)

    assert build("Y").dpv_confirmed is True and build("Y").is_deliverable is True
    assert build("D").dpv_confirmed is False and build("D").is_deliverable is True
    assert build("S").dpv_confirmed is False and build("S").is_deliverable is True
    assert build("N").dpv_confirmed is False and build("N").is_deliverable is False


# ── Endpoints (Task 4) ────────────────────────────────────────────────────────

def test_health_reports_usps_as_not_configured(client, monkeypatch):
    """The state the system ships in. Anything else here would misreport an
    unconfigured optional integration as a degraded one."""
    monkeypatch.delenv("USPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("USPS_CLIENT_SECRET", raising=False)
    uc.reset_usps_client()

    body = client.get("/health").json()
    assert body["usps"]["status"] == "Not configured — code normalization active"
    assert body["usps"]["configured"] is False
    assert body["usps"]["circuit_breaker"] == "closed"


def test_health_never_calls_usps(client, monkeypatch):
    """/health is a liveness probe. If it awaited an external API, USPS being
    slow would make this instance look unhealthy to Azure."""
    monkeypatch.setenv("USPS_CLIENT_ID", "cid")
    monkeypatch.setenv("USPS_CLIENT_SECRET", "sec")
    uc.reset_usps_client()

    def _explode(*a, **kw):
        raise AssertionError("/health must not make a network call")

    monkeypatch.setattr(uc.USPSClient, "standardize", _explode)
    body = client.get("/health").json()
    assert body["usps"]["status"] == "Operational"
    uc.reset_usps_client()


def test_metrics_endpoint_is_admin_only(client):
    assert client.get("/api/v1/usps/metrics").status_code in (401, 403)


def test_metrics_endpoint_is_registered():
    from app.main import app
    assert "/api/v1/usps/metrics" in app.openapi()["paths"]
