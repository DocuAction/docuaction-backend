"""USPS: a rejected credential is said to be a rejected credential, and is not retried.

Cause of the DEV failure (recorded live 2026-10-29; re-confirmed 2026-11-13 by
calling the token endpoint with placeholder values): `POST /oauth2/v3/token`
answers HTTP 401 `{"error": "invalid_client", "error_description": "InvalidApiKey:
The client application credentials provided in the request are missing, invalid,
inactive or not approved for access."}`. The request format is the one USPS
documents. Before this change the client kept only the status code, reported the
address check as "request could not be completed", and asked for a token again
on every call.

Every response here is CONTROLLED (httpx.MockTransport) - no test calls USPS.
The 401 body is the one USPS returned on 2026-11-13.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.tefca_registry import usps_client as uc
from app.tefca_registry.usps_client import USPSClient

SECRET = "client-secret-do-not-log-xyz789"
CLIENT_ID = "consumer-key-do-not-echo-123"
INVALID_CLIENT = {
    "error": "invalid_client",
    "error_description": ("InvalidApiKey: The client application credentials provided in the "
                          "request are missing, invalid, inactive or not approved for access."),
    "error_uri": "https://datatracker.ietf.org/doc/html/rfc6749#page-45",
}
ADDRESS_OK = {"address": {"streetAddress": "123 N MAIN ST", "city": "SPRINGFIELD", "state": "VA",
                          "ZIPCode": "22150", "ZIPPlus4": "1234"},
              "additionalInfo": {"DPVConfirmation": "Y"}}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _client(handler, **kw) -> USPSClient:
    client = USPSClient(client_id=CLIENT_ID, client_secret=SECRET,
                        transport=httpx.MockTransport(handler), **kw)
    client.slept = []

    async def _sleep(seconds):
        client.slept.append(seconds)

    client._sleep = _sleep
    return client


def _check(client, street="123 Main St"):
    return asyncio.run(client.standardize(street, city="Springfield", state="VA"))


def test_rejected_credentials_are_reported_as_such_and_not_asked_again():
    calls = {"token": 0, "address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            calls["token"] += 1
            return httpx.Response(401, json=INVALID_CLIENT)
        calls["address"] += 1
        return httpx.Response(200, json=ADDRESS_OK)

    clock = FakeClock()
    client = _client(handler, clock=clock)
    first = _check(client)
    assert first.available is False and first.method == "usps_error"       # never "usps_api"
    assert first.failure_kind == uc.FAILURE_CREDENTIALS_REJECTED
    assert "rejected this deployment's credentials (invalid_client)" in first.error
    assert "not a finding about the address" in first.error
    assert first.standardized_street is None and first.dpv_confirmed is False

    for _ in range(12):                                # a queue of reviews being opened
        again = _check(client)
        assert again.failure_kind == uc.FAILURE_CREDENTIALS_REJECTED and again.available is False
    assert calls == {"token": 1, "address": 0}, "an authorization failure was retried"
    assert client.slept == []                          # no backoff loop either
    # the breaker measures USPS's health, not our settings: it stays closed
    assert client.circuit.status == uc.CircuitBreaker.CLOSED

    # Default payload (copied into each delivery job's stored readiness, readable
    # by the viewer role): the status line only - no USPS detail, no setting names.
    plain = client.health()
    assert plain["status"] == "Authentication failed"
    assert not {"auth_error", "auth_retry_in_s", "action_required"} & set(plain)

    health = client.health(include_auth_detail=True)      # the administrator health route
    assert health["status"] == "Authentication failed"
    assert health["auth_error"] == {"http_status": 401, "usps_error": "invalid_client",
                                    "usps_description": INVALID_CLIENT["error_description"],
                                    "endpoint": uc.OAUTH_ENDPOINT, "environment": "production"}
    assert 0 < health["auth_retry_in_s"] <= uc.AUTH_FAILURE_HOLD_S
    assert "USPS_CLIENT_ID" in health["action_required"]
    assert SECRET not in repr(health) and CLIENT_ID not in repr(health)


def test_one_new_attempt_after_the_hold_and_a_good_credential_clears_the_state():
    state = {"good": False, "token": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            state["token"] += 1
            if state["good"]:
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return httpx.Response(401, json=INVALID_CLIENT)
        return httpx.Response(200, json=ADDRESS_OK)

    clock = FakeClock()
    client = _client(handler, clock=clock)
    _check(client)
    clock.advance(uc.AUTH_FAILURE_HOLD_S - 1)
    _check(client)
    assert state["token"] == 1                          # still inside the hold
    clock.advance(2)
    assert _check(client).failure_kind == uc.FAILURE_CREDENTIALS_REJECTED
    assert state["token"] == 2                          # exactly one new attempt, then held again
    _check(client)
    assert state["token"] == 2

    state["good"] = True
    clock.advance(uc.AUTH_FAILURE_HOLD_S + 1)
    ok = _check(client)
    assert ok.available is True and ok.method == "usps_api" and ok.failure_kind is None
    assert ok.dpv_confirmed is True and ok.zip4 == "1234"
    health = client.health(include_auth_detail=True)
    assert health["status"] == "Operational" and "auth_error" not in health


@pytest.mark.parametrize("status,kind,address_calls", [
    (403, uc.FAILURE_NOT_ENTITLED, 1),          # not retried
    (400, uc.FAILURE_INVALID_REQUEST, 1),       # not retried
    (404, uc.FAILURE_INVALID_REQUEST, 1),       # not retried
    (429, uc.FAILURE_RATE_LIMITED, 1 + uc.MAX_RETRIES),     # bounded backoff, then says so
    (503, uc.FAILURE_UPSTREAM, 1 + uc.MAX_RETRIES),
])
def test_each_unavailable_outcome_says_which_it_is_and_none_is_a_validation(status, kind, address_calls):
    calls = {"address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        calls["address"] += 1
        return httpx.Response(status, json={"error": {"code": str(status)}})

    client = _client(handler)
    result = _check(client)
    assert result.available is False and result.method == "usps_error"
    assert result.failure_kind == kind and f"HTTP {status}" in result.error
    assert result.dpv_confirmed is False and result.is_deliverable is False
    assert calls["address"] == address_calls
    assert len(client.slept) == address_calls - 1 and client.slept == list(uc.BACKOFF_SCHEDULE[:address_calls - 1])


def test_not_configured_circuit_open_and_network_failure_are_distinct_kinds(monkeypatch):
    monkeypatch.delenv("USPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("USPS_CLIENT_SECRET", raising=False)
    bare = USPSClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    none = asyncio.run(bare.standardize("1 Main St", state="VA"))
    assert (none.method, none.failure_kind) == ("usps_not_configured", uc.FAILURE_NOT_CONFIGURED)

    def boom(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raise httpx.ConnectTimeout("synthetic timeout")

    client = _client(boom)
    assert _check(client).failure_kind == uc.FAILURE_UPSTREAM
    for _ in range(uc.CIRCUIT_FAILURE_THRESHOLD):
        _check(client)
    assert _check(client).failure_kind == uc.FAILURE_CIRCUIT_OPEN


def test_the_rejection_is_logged_with_usps_reason_and_without_any_credential(caplog):
    def handler(request):
        return httpx.Response(401, json=INVALID_CLIENT)

    client = _client(handler)
    with caplog.at_level(logging.DEBUG):
        _check(client)
    text = caplog.text
    assert "USPS token rejected" in text and "usps_error=invalid_client" in text and "http_status=401" in text
    assert SECRET not in text and CLIENT_ID not in text


async def test_workspace_shows_a_rejected_credential_as_unavailable_never_as_an_address_result(monkeypatch):
    """The analyst's case view (GET /api/tefca/workflow/reviews/{id}/workspace ->
    workspace._section_usps): the submitted address is shown untouched, there is no
    USPS-observed address, and the reason is the deployment's credentials."""
    from app.tefca_registry import workspace

    client = _client(lambda request: httpx.Response(401, json=INVALID_CLIENT))
    monkeypatch.setattr(uc, "_client", client)
    source = SimpleNamespace(parsed={"address_line": "1 Synthetic Way", "address_city": "Testville",
                                     "address_state": "MA", "address_postalCode": "02101",
                                     "address_country": "USA"})
    section = await workspace._section_usps(None, source, None)
    assert section["available"] is False and section["status"] == "SOURCE_UNAVAILABLE"
    assert section["failure_kind"] == uc.FAILURE_CREDENTIALS_REJECTED
    assert section["method"] == "usps_error" and section["usps_observed_address"] is None
    assert section["dpv_confirmed"] is False and section["is_deliverable"] is False
    assert section["source_modified"] is False
    assert section["onc_address"]["street"] == "1 Synthetic Way"          # submitted address preserved
    assert "credentials" in section["error"] and SECRET not in repr(section)


def test_a_rejected_token_on_the_half_open_probe_does_not_wedge_the_breaker():
    """Breaker open after upstream failures; the cooldown passes; the one probe
    allowed through meets a rejected credential. The probe slot must be given
    back - otherwise every later call is refused until restart, even once USPS
    accepts the credentials again."""
    state = {"mode": "down", "token": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            state["token"] += 1
            if state["mode"] == "rejected":
                return httpx.Response(401, json=INVALID_CLIENT)
            return httpx.Response(200, json={"access_token": f"t{state['token']}", "expires_in": 90})
        if state["mode"] == "down":
            return httpx.Response(500, json={})
        return httpx.Response(200, json=ADDRESS_OK)

    clock = FakeClock()
    client = _client(handler, clock=clock)
    for _ in range(uc.CIRCUIT_FAILURE_THRESHOLD):
        _check(client)
    assert client.circuit.status == uc.CircuitBreaker.OPEN
    clock.advance(uc.CIRCUIT_COOLDOWN_S + 1)              # cooldown over; the cached token has expired too
    state["mode"] = "rejected"
    assert _check(client).failure_kind == uc.FAILURE_CREDENTIALS_REJECTED      # this WAS the probe
    assert client.circuit._half_open is False, "the probe slot was never released"
    state["mode"] = "up"                                   # the app is approved again
    clock.advance(uc.AUTH_FAILURE_HOLD_S + 1)
    recovered = _check(client)
    assert recovered.available is True and recovered.method == "usps_api"
    assert client.circuit.status == uc.CircuitBreaker.CLOSED


def test_an_upstream_description_that_echoes_a_credential_is_scrubbed():
    body = {"error": "invalid_client",
            "error_description": f"client {CLIENT_ID} with secret {SECRET} is not\x07 approved\n" + "x" * 400}
    client = _client(lambda request: httpx.Response(401, json=body))
    result = _check(client)
    health = client.health(include_auth_detail=True)
    text = repr(health) + repr(result)
    assert SECRET not in text and CLIENT_ID not in text
    description = health["auth_error"]["usps_description"]
    assert "[redacted]" in description and len(description) <= 200
    assert "\x07" not in description and "\n" not in description


def test_delivery_readiness_and_the_admin_route_ask_for_different_detail():
    """The two callers of health(): the delivery runner (stored per job, viewer
    readable) takes the default; only the admin health route opts in."""
    import inspect

    from app.api import admin_health
    from app.tefca_registry.rce import delivery_runner

    assert "health(include_auth_detail=True)" in inspect.getsource(admin_health._usps)
    runner = inspect.getsource(delivery_runner)
    assert "get_usps_client().health()" in runner and "include_auth_detail" not in runner
