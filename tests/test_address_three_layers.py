"""Three-layer address comparison: code → USPS → code-only.

The layering is a cost control. Layer 1 settles the overwhelming majority of real
differences — abbreviations, directionals, unit designators — for free, and every
one it settles is a USPS call not made. The test that matters most is therefore
`test_code_match_skips_usps`: if that regresses, a 383-entity sample quietly
becomes 766 API calls.
"""
import asyncio

import httpx
import pytest

from app.tefca_registry import usps_client as uc
from app.tefca_registry.address_normalizer import (ThreeLayerAddressNormalizer,
                                                   USPSNormalizer)

TOKEN = "tok"


def _std(street, city, state, zip5, zip4, dpv="Y"):
    return {"address": {"streetAddress": street, "city": city, "state": state,
                        "ZIPCode": zip5, "ZIPPlus4": zip4},
            "additionalInfo": {"DPVConfirmation": dpv}}


def _client(responses):
    """A configured client returning `responses` to successive address calls."""
    calls = {"address": 0}

    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return httpx.Response(200, json={"access_token": TOKEN,
                                             "expires_in": 3600})
        payload = responses[min(calls["address"], len(responses) - 1)]
        calls["address"] += 1
        return httpx.Response(200, json=payload)

    client = uc.USPSClient(client_id="cid", client_secret="sec",
                           transport=httpx.MockTransport(handler))
    client.calls = calls
    return client


def _compare(client, a, b):
    return asyncio.run(
        ThreeLayerAddressNormalizer(client=client).standardize_and_compare(a, b))


# ── Layer 1 ───────────────────────────────────────────────────────────────────

def test_code_match_skips_usps():
    """Two renderings of one address. USPS must never be called."""
    client = _client([_std("X", "X", "XX", "00000", "0000")])
    match = _compare(client,
                     "123 North Main Street, Suite 400, Reston, VA 20190",
                     "123 N MAIN ST STE 400, RESTON, VA 20190")
    assert match.match is True
    assert match.method == "code_normalization"
    assert client.calls["address"] == 0, "layer 1 settled it — no API call"
    assert client.metrics.requests_total == 0


def test_a_differing_zip_does_not_reach_usps():
    """A different ZIP is a substantive difference, not a formatting one. Asking
    USPS spends a call to be told what code normalization already knows."""
    client = _client([_std("X", "X", "XX", "00000", "0000")])
    match = _compare(client,
                     "123 N Main St, Reston, VA 20190",
                     "123 N Main St, Reston, VA 22102")
    assert match.match is False
    assert client.calls["address"] == 0


# ── Layer 2 ───────────────────────────────────────────────────────────────────

def test_code_mismatch_tries_usps():
    client = _client([_std("100 CAMPUS DR", "RESTON", "VA", "20190", "1111")])
    match = _compare(client,
                     "100 Campus Drive, Reston, VA 20190",
                     "100 Campus Dr Building B, Reston, VA 20190")
    assert client.calls["address"] == 2, "both sides standardized"
    assert match.method in ("usps_api", "usps_zip4")


def test_usps_resolves_format_difference():
    """Both sides standardize to the same address — a stronger statement than
    token overlap, which had called them different."""
    same = _std("100 CAMPUS DR", "RESTON", "VA", "20190", "1111")
    client = _client([same, same])
    match = _compare(client,
                     "100 Campus Drive, Reston, VA 20190",
                     "100 Campus Dr Bldg B, Reston, VA 20190")
    assert match.match is True
    assert match.method == "usps_api"
    assert match.dpv_confirmed is True
    assert match.confidence == 1.0


def test_zip4_match():
    """Differing street text, identical ZIP+4. Reported as a match at reduced
    confidence — ZIP+4 is a delivery point, which is a secondary signal, not
    proof of equivalence."""
    client = _client([
        _std("100 CAMPUS DR STE 1", "RESTON", "VA", "20190", "1111"),
        _std("100 CAMPUS DR STE 2", "RESTON", "VA", "20190", "1111"),
    ])
    match = _compare(client,
                     "100 Campus Drive Suite 1, Reston, VA 20190",
                     "100 Campus Drive Suite 2, Reston, VA 20190")
    assert match.match is True
    assert match.method == "usps_zip4"
    assert match.usps_zip4_match is True
    assert match.confidence == 0.9


def test_usps_disagreement_is_a_mismatch():
    client = _client([
        _std("100 CAMPUS DR", "RESTON", "VA", "20190", "1111"),
        _std("900 ELM ST", "RESTON", "VA", "20190", "9999"),
    ])
    match = _compare(client,
                     "100 Campus Drive, Reston, VA 20190",
                     "900 Elm Street, Reston, VA 20190")
    assert match.match is False
    assert match.method == "usps_api"
    assert match.usps_zip4_match is False


# ── Layer 3 ───────────────────────────────────────────────────────────────────

def test_usps_unavailable_uses_code():
    """USPS erroring must degrade to the code answer, not to no answer."""
    def handler(request):
        if request.url.path == uc.OAUTH_ENDPOINT:
            return httpx.Response(200, json={"access_token": TOKEN,
                                             "expires_in": 3600})
        return httpx.Response(400, json={"error": "bad request"})

    client = uc.USPSClient(client_id="cid", client_secret="sec",
                           transport=httpx.MockTransport(handler))
    match = _compare(client,
                     "100 Campus Drive, Reston, VA 20190",
                     "100 Campus Dr Bldg B, Reston, VA 20190")
    assert match.match is False
    assert match.method == "code_normalization_usps_unavailable"
    assert match.submitted_normalized     # the code answer is still reported


# ── Metrics ───────────────────────────────────────────────────────────────────

def test_metrics_tracked():
    client = _client([_std("100 CAMPUS DR", "RESTON", "VA", "20190", "1111")])
    _compare(client,
             "100 Campus Drive, Reston, VA 20190",
             "100 Campus Dr Bldg B, Reston, VA 20190")

    snap = client.metrics_snapshot()
    assert snap["usps_requests_total"] == 2
    assert snap["usps_errors_total"] == 0
    assert snap["usps_latency_avg_ms"] >= 0
    assert snap["usps_dpv_success_rate"] == 1.0
    assert snap["usps_zip4_success_rate"] == 1.0
    assert snap["usps_fallback_rate"] == 0.0
    assert snap["usps_circuit_breaker_status"] == "closed"
    assert set(snap) == {
        "usps_requests_total", "usps_latency_avg_ms", "usps_errors_total",
        "usps_dpv_success_rate", "usps_zip4_success_rate", "usps_fallback_rate",
        "usps_circuit_breaker_status"}


def test_fallback_rate_counts_attempts_not_requests():
    """When the circuit is open nothing reaches USPS, so a rate over completed
    requests would be undefined exactly when it matters most."""
    client = uc.USPSClient()          # unconfigured
    asyncio.run(client.standardize("123 Main St"))
    asyncio.run(client.standardize("456 Oak Ave"))
    snap = client.metrics_snapshot()
    assert snap["usps_requests_total"] == 0
    assert snap["usps_fallback_rate"] == 1.0


# ── Address parsing ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("123 N Main St, Reston, VA 20190",
     {"street": "123 N Main St", "city": "Reston", "state": "VA", "zip5": "20190"}),
    ("100 Campus Dr, Suite 400, Reston, VA 20190-1234",
     {"street": "100 Campus Dr Suite 400", "city": "Reston", "state": "VA",
      "zip5": "20190"}),
])
def test_parse_line_splits_conventional_addresses(line, expected):
    assert USPSNormalizer().parse_line(line) == expected


def test_parse_line_reports_what_it_found_rather_than_guessing():
    """No commas means the city is unrecoverable without guessing, and a guess
    would be sent to USPS as fact."""
    parsed = USPSNormalizer().parse_line("123 N Main St Reston VA 20190")
    assert parsed["state"] == "VA"
    assert parsed["zip5"] == "20190"
    assert parsed["city"] == ""
    assert "Main" in parsed["street"]


def test_parse_line_on_empty_input():
    assert USPSNormalizer().parse_line("") == {
        "street": "", "city": "", "state": "", "zip5": ""}
    assert USPSNormalizer().parse_line(None)["street"] == ""
