"""Honouring SAM.gov's own signals (2026-11-26).

Owner: "Use live requests within permitted limits, honour rate-limit responses ... Never bypass quota limits or
repeatedly retry exhausted access." Live requests stay ON by default (no run switch here). Measured at the HTTP
layer with a counting stand-in client; no network.

* HTTP 429 is never retried, and stops live attempts: Retry-After when SAM.gov sends it, else until the next UTC day.
* `X-RateLimit-Remaining: 0` stops live attempts until the next UTC day (the request that carried it was answered).
* A run of empty-404 "no route" answers stops live attempts for a period; one request is then tried again.
* An optional daily request budget stops live attempts when used.
* While stopped: ZERO requests; every leg records "not attempted - live SAM.gov access stopped" with the cause, the
  failure class NOT_ATTEMPTED_STOPPED, and it is never an answer (the policy treats it as incomplete; the extract
  decides exactly as when the API is unavailable).
"""
from __future__ import annotations

import httpx
import pytest

from app.Tefca import connectors, sam_screening
from app.tefca_registry.rce import source_policy as sp
from app.tefca_registry.rce import verification_coverage as vc

pytestmark = pytest.mark.asyncio
SYN = "SYNTHETIC QAREH1126"


class _Client:
    def __init__(self, status, headers=None, body=b""):
        self.status, self.headers, self.body, self.attempts = status, headers or {}, body, 0

    async def get(self, url, params=None, headers=None, timeout=None):
        self.attempts += 1
        return httpx.Response(self.status, request=httpx.Request("GET", url), headers=self.headers,
                              content=self.body)


@pytest.fixture
def wired(monkeypatch):
    import tenacity

    def _wire(client):
        monkeypatch.setenv("SAM_GOV_API_KEY", "synthetic-key-not-a-credential")
        monkeypatch.delenv(sam_screening.LIVE_API_DISABLED_ENV, raising=False)
        monkeypatch.setenv("TEFCA_RATE_LIMIT_SAM_GOV_RPS", "1000")
        monkeypatch.setenv("TEFCA_RATE_LIMIT_SAM_GOV_EXCLUSIONS_RPS", "1000")
        monkeypatch.setattr(connectors, "_RATE_LIMITERS", {})
        monkeypatch.setattr(connectors, "_shared_http_client", lambda: client)
        monkeypatch.setattr(connectors, "wait_exponential", lambda **_k: tenacity.wait_none())
        return connectors.SAMGovConnector()
    sam_screening.reset_live_api_state()
    yield _wire
    sam_screening.reset_live_api_state()


def _stopped(result):
    assert result.success is False
    assert sam_screening.LIVE_API_STOPPED_MARKER in result.error, result.error
    assert sam_screening.classify_failure(result.error) == sam_screening.NOT_ATTEMPTED_STOPPED


async def test_a_429_is_asked_once_never_retried_and_stops_every_later_request(wired):
    client = _Client(429)
    connector = wired(client)
    first = await connector.verify(legal_name=f"{SYN} one")
    # the registration leg got the 429 (ONE request, no retry); the exclusion leg was then not asked at all
    assert client.attempts == 1
    assert first.success is False
    for n in range(5):
        _stopped(await connector.verify(legal_name=f"{SYN} later {n}"))
    assert client.attempts == 1                                       # nothing after the signal
    reason = sam_screening.live_api_stop_reason()
    assert "HTTP 429" in reason and "next UTC day" in reason and "missing key" in reason


async def test_retry_after_is_honoured(wired):
    client = _Client(429, headers={"Retry-After": "120"})
    connector = wired(client)
    import time
    t0 = time.time()
    await connector.lookup_by_name(f"{SYN} r")
    assert "Retry-After 120 s honoured" in sam_screening.live_api_stop_reason()
    assert sam_screening.live_api_stop_reason(now=t0 + 60) is not None
    assert sam_screening.live_api_stop_reason(now=t0 + 125) is None    # then live requests resume


async def test_rate_limit_remaining_zero_stops_after_that_answer(wired):
    body = b'{"totalRecords": 0, "entityData": []}'
    client = _Client(200, headers={"X-RateLimit-Remaining": "0"}, body=body)
    connector = wired(client)
    answered = await connector.lookup_by_name(f"{SYN} last")
    assert answered.success is True                                  # that answer is used
    _stopped(await connector.lookup_by_name(f"{SYN} next"))
    assert client.attempts == 1
    assert "X-RateLimit-Remaining: 0" in sam_screening.live_api_stop_reason()


async def test_a_run_of_no_route_answers_stops_then_one_request_is_tried_again(wired, monkeypatch):
    monkeypatch.setenv(sam_screening.NOT_ROUTING_STOP_AFTER_ENV, "4")
    monkeypatch.setenv(sam_screening.NOT_ROUTING_RETRY_SECONDS_ENV, "600")
    client = _Client(404)
    connector = wired(client)
    for n in range(2):                                    # 2 records x 2 legs = 4 empty 404s
        r = await connector.verify(legal_name=f"{SYN} nr {n}")
        assert sam_screening.classify_failure(r.error) == sam_screening.NOT_ROUTING
    assert client.attempts == 4
    for n in range(10):
        _stopped(await connector.verify(legal_name=f"{SYN} stopped {n}"))
    assert client.attempts == 4
    assert "no route" in sam_screening.live_api_stop_reason()
    # after the period ONE request goes out; still no route -> stopped again at once
    import time
    sam_screening._LIVE["stop_until"] = time.time() - 1
    await connector.lookup_by_name(f"{SYN} probe again")
    assert client.attempts == 5
    _stopped(await connector.lookup_by_name(f"{SYN} after probe"))
    assert client.attempts == 5


async def test_an_answer_resets_the_no_route_count(wired, monkeypatch):
    monkeypatch.setenv(sam_screening.NOT_ROUTING_STOP_AFTER_ENV, "3")
    sam_screening.note_live_api_response(404, empty_body=True)
    sam_screening.note_live_api_response(404, empty_body=True)
    sam_screening.note_live_api_response(200)
    sam_screening.note_live_api_response(404, empty_body=True)
    sam_screening.note_live_api_response(404, empty_body=True)
    assert sam_screening.live_api_stop_reason() is None


async def test_a_daily_budget_stops_at_the_budget(wired, monkeypatch):
    monkeypatch.setenv(sam_screening.DAILY_REQUEST_BUDGET_ENV, "3")
    client = _Client(200, body=b'{"totalRecords": 0, "entityData": []}')
    connector = wired(client)
    for n in range(3):
        assert (await connector.lookup_by_name(f"{SYN} b{n}")).success is True
    _stopped(await connector.lookup_by_name(f"{SYN} over budget"))
    assert client.attempts == 3
    assert "daily budget of 3" in sam_screening.live_api_stop_reason()


async def test_without_any_signal_live_requests_continue_as_before(wired):
    client = _Client(200, body=b'{"totalRecords": 0, "entityData": []}')
    connector = wired(client)
    for n in range(30):
        assert (await connector.lookup_by_name(f"{SYN} ok {n}")).success is True
    assert client.attempts == 30 and sam_screening.live_api_stop_reason() is None


async def test_the_health_probe_asks_nothing_while_stopped(wired):
    client = _Client(429)
    connector = wired(client)
    await connector.lookup_by_name(f"{SYN} trip")
    assert await connector.probe() is False
    assert client.attempts == 1


def test_stopped_is_never_an_answer_and_is_counted_as_not_attempted():
    assert sam_screening.NOT_ATTEMPTED_STOPPED in sam_screening.FAILURE_CLASSES
    # the stored state is the ordinary non-answer: the policy reads it as incomplete
    assert "unavailable" in sp.INCOMPLETE_STATES
    # coverage counts it as not attempted, never as an attempt or an unavailable answer
    assert vc.is_not_attempted_disabled("SAM_GOV", {"failure_class": sam_screening.NOT_ATTEMPTED_STOPPED})
    assert not vc.is_not_attempted_disabled("SAM_GOV", {"failure_class": sam_screening.NOT_ROUTING})
    assert not vc.is_not_attempted_disabled("SAM_GOV", {"failure_class": sam_screening.QUOTA})


def test_the_report_cell_says_not_attempted_and_why():
    from types import SimpleNamespace
    from app.reports.data import october_review_loader as ld
    row = SimpleNamespace(source="SAM_GOV", disposition="UNAVAILABLE", note="n", retrieved_at=None,
                          evidence_dimension="EXCLUSION_REVOCATION",
                          generation_timestamp=None, created_at=None, query_timestamp=None,
                          normalized_values={"failure_class": sam_screening.NOT_ATTEMPTED_STOPPED})
    cell = ld._evidence_cell("sam", row)
    assert "not attempted - access stopped" in cell and "retrieved" not in cell


async def test_remaining_zero_stops_until_the_next_utc_day_even_with_retry_after(wired):
    """Review 2026-11-26: Retry-After is honoured for a 429 only; "Remaining: 0" means the daily quota is used."""
    import time
    now = time.time()
    sam_screening.note_live_api_response(200, remaining="0", retry_after="60", now=now)
    assert sam_screening.live_api_stop_reason(now=now + 120) is not None          # not resumed after 60 s
    assert sam_screening._LIVE["stop_until"] == sam_screening._next_utc_midnight(now)
    assert "next UTC day" in sam_screening.live_api_stop_reason(now=now + 1)


async def test_a_call_that_already_sent_a_request_keeps_its_real_failure(wired, monkeypatch):
    """Review 2026-11-26: the budget is used by a request that answers HTTP 500; the call stops retrying but is
    recorded with its real failure (attempted, unavailable), never as 'not attempted'."""
    monkeypatch.setenv(sam_screening.DAILY_REQUEST_BUDGET_ENV, "1")
    client = _Client(500)
    connector = wired(client)
    result = await connector.lookup_by_name(f"{SYN} five hundred")
    assert client.attempts == 1                                   # no retry after the budget is used
    assert result.success is False and "HTTP 500" in result.error
    assert sam_screening.classify_failure(result.error) != sam_screening.NOT_ATTEMPTED_STOPPED
    _stopped(await connector.lookup_by_name(f"{SYN} next"))      # the NEXT call sends nothing
    assert client.attempts == 1
