"""Bulletin public/guarded contract (Phase 5.5 + Day 3 hardening)."""
import os
from conftest import GATED

FLAG = os.environ.get("BULLETIN_AUTH_ENABLED", "").lower() == "true"


def test_sources_endpoint_public(client, db_required):
    assert client.get("/api/v1/bulletin/sources").status_code == 200


def test_sources_health_public(client):
    assert client.get("/api/v1/bulletin/sources/health").status_code in (200, 500)


def test_quality_latest_public(client):
    assert client.get("/api/v1/bulletin/quality/latest").status_code == 200


def test_preview_public(client):
    """FCC contacts open this from an email link and have no DocuAction account.
    A 404 for an unknown id is fine; 401/403 is not."""
    r = client.get("/api/v1/bulletin/briefings/does-not-exist/preview")
    assert r.status_code not in GATED, \
        "preview must never require auth - FCC recipients have no accounts"


def test_latest_public(client, db_required):
    assert client.get("/api/v1/bulletin/latest/fcc").status_code in (200, 404)


def test_costs_requires_auth(client):
    r = client.get("/api/v1/bulletin/costs")
    if FLAG:
        assert r.status_code in GATED
    else:
        assert r.status_code != 404


def test_run_requires_auth(client):
    r = client.post("/api/v1/bulletin/run/fcc")
    if FLAG:
        assert r.status_code in GATED
    else:
        assert r.status_code != 404


def test_archive_requires_auth(client):
    r = client.get("/api/v1/bulletin/archive/fcc")
    if FLAG:
        assert r.status_code in GATED
    else:
        assert r.status_code != 404


def test_profiles_requires_auth(client):
    """Guarded 2026-07-29: editorial configuration, no public consumer."""
    r = client.get("/api/v1/bulletin/profiles")
    if FLAG:
        assert r.status_code in GATED
    else:
        assert r.status_code != 404


def test_feed_expansion_present():
    """Phase 5.5 added four feeds that were genuinely missing."""
    from app.bulletin_intelligence.engine import MAJOR_OUTLET_FEEDS
    names = {n for _, n, _ in MAJOR_OUTLET_FEEDS}
    for expected in ("Fox Business", "Nextgov", "Government Executive", "StateScoop"):
        assert expected in names, f"{expected} missing from MAJOR_OUTLET_FEEDS"
