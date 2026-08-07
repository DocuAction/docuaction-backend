"""SAM.gov failure reporting.

`HTTP 404` as a bare reason string is what sent an investigation after a key
problem that did not exist: SAM returns 404 both when an entity is absent and
when api.sam.gov is not serving at all, and the connector reported those
identically. These tests pin the distinction, because the string is read by an
operator deciding whether to go request a new credential.
"""
import pytest

from app.Tefca.connectors import SAMGovConnector, _sam_failure_reason


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_empty_404_is_reported_as_upstream_not_a_key_problem():
    reason = _sam_failure_reason(FakeResponse(404, ""))
    assert "upstream" in reason.lower()
    assert "NOT a missing or invalid" in reason
    # The wrong conclusion must not be reachable from this string.
    assert "register" not in reason.lower()


def test_404_with_a_body_is_not_claimed_to_be_an_outage():
    """A 404 that carries an error document came from a live route, so it is a
    different failure and must not borrow the outage explanation."""
    reason = _sam_failure_reason(FakeResponse(404, '{"error":"no match"}'))
    assert "upstream" not in reason.lower()
    assert "no match" in reason


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejection_names_the_key(status):
    reason = _sam_failure_reason(FakeResponse(status, '{"error":"API_KEY_INVALID"}'))
    assert "rejected the API key" in reason
    assert "API_KEY_INVALID" in reason


def test_rate_limit_is_distinguished_from_both():
    reason = _sam_failure_reason(FakeResponse(429))
    assert "rate limit" in reason.lower()
    assert "upstream" not in reason.lower()
    assert "rejected" not in reason.lower()


def test_a_response_whose_body_raises_still_produces_a_reason():
    """Diagnostics run on the failure path. One that can itself fail is one more
    thing to debug during an incident."""
    class Hostile:
        status_code = 500

        @property
        def text(self):
            raise RuntimeError("body unreadable")

    assert "500" in _sam_failure_reason(Hostile())


def test_body_is_truncated_so_an_html_error_page_cannot_flood_the_record():
    reason = _sam_failure_reason(FakeResponse(500, "x" * 5000))
    assert len(reason) < 250


# ── Health note ───────────────────────────────────────────────────────────────

def test_health_note_stops_blaming_the_key_once_one_is_set():
    """The static note read "(requires SAM_GOV_API_KEY)" even on environments
    where the key was set, which is the sentence that pointed at the wrong fix."""
    import asyncio

    from app.Tefca import connectors as c

    class Stub:
        def __init__(self, key):
            self.api_key = key

        async def probe(self):
            return False

    orch = c.SourceConnectorManager.__new__(c.SourceConnectorManager)
    for attr in ("nppes", "leie", "pecos", "rce_directory"):
        setattr(orch, attr, Stub(""))
    orch.sam = Stub("a-real-registered-key")

    health = asyncio.run(orch.health_check())
    note = health["SAM_GOV"]["note"]
    assert "requires SAM_GOV_API_KEY" not in note
    assert "key configured" in note
    assert "upstream" in note


def test_health_note_still_asks_for_a_key_when_there_is_none():
    import asyncio

    from app.Tefca import connectors as c

    class Stub:
        def __init__(self, key):
            self.api_key = key

        async def probe(self):
            return False

    orch = c.SourceConnectorManager.__new__(c.SourceConnectorManager)
    for attr in ("nppes", "leie", "pecos", "rce_directory"):
        setattr(orch, attr, Stub(""))
    orch.sam = Stub("")

    health = asyncio.run(orch.health_check())
    assert "requires SAM_GOV_API_KEY" in health["SAM_GOV"]["note"]


def test_connector_reads_the_key_at_instantiation(monkeypatch):
    monkeypatch.setenv("SAM_GOV_API_KEY", "set-after-import")
    assert SAMGovConnector().api_key == "set-after-import"
