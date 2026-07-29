"""Bulletin Intelligence API tests"""
from conftest import GATED


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


def test_bulletin_run_requires_auth(client):
    import os
    response = client.post("/api/v1/bulletin/run/fcc")
    if os.environ.get("BULLETIN_AUTH_ENABLED", "").lower() == "true":
        assert response.status_code in GATED
    else:
        assert response.status_code != 404


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
