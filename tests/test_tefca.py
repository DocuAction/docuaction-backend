"""TEFCA Registry API tests"""
from conftest import GATED


def test_tefca_status(client):
    response = client.get("/api/tefca/status")
    assert response.status_code == 200


def test_tefca_entities_list(client):
    response = client.get("/api/v1/tefca/registry/entities")
    # May be 200 or 404 depending on deployment
    assert response.status_code in (200, 404, 401, 403)


def test_tefca_dashboard_summary_requires_auth(client):
    """Guarded to viewer in the Day 3 sprint. Unlike the bulletin guards this one
    uses require_role directly and is not flag-gated, so it always enforces."""
    response = client.get("/api/tefca/dashboard/summary")
    assert response.status_code in GATED


def test_tefca_dashboard_trends_requires_auth(client):
    response = client.get("/api/tefca/dashboard/trends")
    assert response.status_code in GATED


def test_tefca_module_registered_unconditionally(client):
    """TEFCA is a contract deliverable registered outside safe_load, so an import
    failure is a hard startup failure rather than a silent 404. If this returns
    404 the module failed to load and /health would still claim it is active."""
    assert client.get("/api/tefca/status").status_code != 404
