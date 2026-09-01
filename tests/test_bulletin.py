"""Bulletin Intelligence API tests.

WHY THIS FILE HAS A NETWORK GUARD
─────────────────────────────────
These are API-BOUNDARY tests: they assert that routes exist, answer, and are
guarded. Not one of them intends to collect anything from the Internet.

`POST /run/{agency_id}` nevertheless did. The route schedules the real
collection cycle with `background_tasks.add_task(run_daily_cycle, ...)`, and
Starlette's TestClient runs background tasks SYNCHRONOUSLY before returning the
response — so asserting "this route is guarded" executed a full live cycle
against BlueSky, GDELT and C-SPAN. On a CI runner those hang or 403, and the
whole suite never reached a terminal state. Reproduced identically on two
commits, which is how it was established as a harness defect rather than a
product one.

Two changes, both confined to the harness, and both in conftest.py because
`tests/test_bulletin_sources.py` had the identical defect:

  * `no_collection_cycle` replaces `run_daily_cycle` at the point the route
    looks it up, so the route is exercised and the cycle is not. The narrowest
    boundary that fixes it — the endpoint, its guard, its 404 path and its
    response are all still the real ones.

  * `no_outbound_network` fails any test that reaches the Internet anyway.
    A future edit that escapes the mock now gets a loud, immediate failure
    naming the host, instead of a hang that looks like a slow runner.

Live third-party connectivity is a real thing to test, but it is not this
file's job and it must not gate a deploy. Anything of that kind belongs behind
the `network` marker, which the normal suite does not run.
"""
import socket

import pytest

from conftest import GATED

#: Addresses a test may legitimately open: the in-process TestClient transport
#: and a local database. Everything else is the Internet.
#: Both fixtures live in conftest.py: two test modules hit the same defect,
#: and one definition is the only way they cannot drift apart. The guard is
#: applied to every test in this file; `no_collection_cycle` is requested by
#: the tests that exercise the /run route.
pytestmark = pytest.mark.usefixtures("no_outbound_network")


def test_bulletin_health(client):
    response = client.get("/api/v1/bulletin/health")
    assert response.status_code == 200
    data = response.json()
    assert data["module"] == "bulletin_intelligence"
    assert data["status"] == "active"


def test_bulletin_latest(client, db_required):
    """404 is a correct answer when no briefing has been generated yet; the
    endpoint working and the data existing are different assertions."""
    response = client.get("/api/v1/bulletin/latest/fcc")
    assert response.status_code in (200, 404)


def test_bulletin_sources(client, db_required):
    response = client.get("/api/v1/bulletin/sources")
    assert response.status_code == 200


def test_bulletin_quality(client):
    response = client.get("/api/v1/bulletin/quality/latest")
    assert response.status_code == 200


def test_bulletin_costs_requires_auth(client):
    """Guarded in the Day 3 hardening sprint. /costs publishes spend and per-call
    token counts, which is the measurement needed to size a cost-amplification
    attack. Only enforced when BULLETIN_AUTH_ENABLED is set."""
    import os
    response = client.get("/api/v1/bulletin/costs")
    if os.environ.get("BULLETIN_AUTH_ENABLED", "").lower() == "true":
        assert response.status_code in GATED
    else:
        # guard() is a documented no-op when the flag is unset; assert the route
        # exists rather than asserting an enforcement that is switched off.
        assert response.status_code != 404


def test_bulletin_run_requires_auth(client, no_collection_cycle):
    """The guard on /run, asserted without running a collection cycle.

    `no_collection_cycle` is what makes this deterministic. The route, its
    guard, its agency lookup and its response are all still the real ones; only
    the work it schedules is replaced.
    """
    import os
    response = client.post("/api/v1/bulletin/run/fcc")
    if os.environ.get("BULLETIN_AUTH_ENABLED", "").lower() == "true":
        assert response.status_code in GATED
        # Rejected before the handler ran, so nothing was ever scheduled.
        assert no_collection_cycle == []
    else:
        assert response.status_code != 404


def test_the_run_route_schedules_the_cycle_it_claims_to(client,
                                                        no_collection_cycle):
    """The coverage the mock would otherwise have silently removed.

    Replacing the cycle proves the route does not hang; it does not prove the
    route still asks for the right work. This asserts the arguments the
    endpoint hands to the collection cycle, which is the part a caller depends
    on and the part a refactor could quietly change.
    """
    import os

    if os.environ.get("BULLETIN_AUTH_ENABLED", "").lower() == "true":
        pytest.skip("the guard rejects this request before the handler runs")

    response = client.post("/api/v1/bulletin/run/fcc?lookback_hours=24")
    assert response.status_code == 200
    assert response.json()["status"] == "started"
    assert no_collection_cycle == [
        {"agency_id": "fcc", "auto_deliver": False, "lookback_hours": 24}]


def test_an_unknown_agency_is_404_and_schedules_nothing(client,
                                                        no_collection_cycle):
    """The negative path, and the one that matters operationally: a typo in an
    agency id must not start a collection run."""
    import os

    if os.environ.get("BULLETIN_AUTH_ENABLED", "").lower() == "true":
        pytest.skip("the guard rejects this request before the handler runs")

    response = client.post("/api/v1/bulletin/run/not-a-real-agency")
    assert response.status_code == 404
    assert no_collection_cycle == []


def test_the_network_guard_actually_fires():
    """Proves the guard above is real.

    A guard nobody exercises is a guard that quietly stops working. This opens
    a socket to a routable address and requires the AssertionError - so if the
    fixture is ever removed or weakened, this test says so directly rather than
    the suite going back to hanging.
    """
    with pytest.raises(AssertionError, match="outbound connection to"):
        socket.create_connection(("example.com", 80), timeout=1)


def test_bulletin_archive_route_exists(client):
    """Guarded to viewer in the Day 3 sprint; assert the route was not lost."""
    response = client.get("/api/v1/bulletin/archive/fcc")
    assert response.status_code != 404


def test_bulletin_quality_gate_is_advisory(client):
    """The quality gate must never block generation. Before any run it reports
    unavailable with a reason rather than erroring."""
    data = client.get("/api/v1/bulletin/quality/latest").json()
    assert "available" in data
    if data.get("available") is False:
        assert "reason" in data
