"""Client-IP derivation tests.

These cover the incident of 2026-07-29: behind Azure App Service every caller
shared one ``request.client.host``, so the per-IP login throttle held a single
global bucket and twenty attempts locked out the whole user base.

The regression these guard against is subtler than "read X-Forwarded-For" — it
is reading the *wrong end* of it. The leftmost entry is supplied by the caller,
so trusting it hands an attacker a fresh throttle bucket per request and lets
them forge the source address recorded in the PHI audit trail.
"""

from types import SimpleNamespace

import pytest

from app.core.client_ip import _strip_port, get_client_ip


def _req(xff=None, peer="10.0.0.1"):
    headers = {} if xff is None else {"x-forwarded-for": xff}
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=peer) if peer else None,
    )


class TestRightmostEntryWins:
    def test_takes_platform_appended_entry_not_client_supplied(self):
        # App Service appends what it observed; the caller invented the rest.
        assert get_client_ip(_req("1.1.1.1, 2.2.2.2, 203.0.113.9")) == "203.0.113.9"

    def test_single_entry(self):
        assert get_client_ip(_req("203.0.113.9")) == "203.0.113.9"

    def test_spoofed_chain_cannot_change_the_bucket(self):
        # The attack: vary the leftmost entry to mint a new rate-limit bucket.
        real = "203.0.113.9"
        seen = {
            get_client_ip(_req(f"10.0.0.{n}, {real}"))
            for n in range(1, 26)
        }
        assert seen == {real}, "spoofed prefixes must all collapse to one bucket"

    def test_strips_port_from_ipv4(self):
        assert get_client_ip(_req("1.1.1.1, 203.0.113.9:54321")) == "203.0.113.9"

    def test_strips_port_from_bracketed_ipv6(self):
        assert get_client_ip(_req("[2001:db8::1]:54321")) == "2001:db8::1"

    def test_preserves_bare_ipv6(self):
        assert get_client_ip(_req("2001:db8::1")) == "2001:db8::1"

    def test_ignores_trailing_empty_entries(self):
        assert get_client_ip(_req("203.0.113.9, ")) == "203.0.113.9"


class TestFallback:
    def test_falls_back_to_peer_when_no_header(self):
        assert get_client_ip(_req(None, peer="10.0.0.7")) == "10.0.0.7"

    def test_empty_header_falls_back(self):
        assert get_client_ip(_req("", peer="10.0.0.7")) == "10.0.0.7"

    def test_no_client_and_no_header_is_none(self):
        assert get_client_ip(_req(None, peer=None)) is None

    def test_header_only_commas_falls_back(self):
        assert get_client_ip(_req(" , , ", peer="10.0.0.7")) == "10.0.0.7"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3.4", "1.2.3.4"),
        ("1.2.3.4:80", "1.2.3.4"),
        ("[::1]:80", "::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
        ("", ""),
        ("  1.2.3.4  ", "1.2.3.4"),
    ],
)
def test_strip_port(raw, expected):
    assert _strip_port(raw) == expected
