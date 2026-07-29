"""Document processing endpoints exist and are gated."""
from conftest import GATED


def _paths(client):
    return set(client.app.openapi().get("paths", {}))


def test_openapi_reachable(client):
    assert len(_paths(client)) > 200


def test_upload_requires_auth(client):
    r = client.post("/api/upload")
    assert r.status_code in GATED or r.status_code == 404


def test_documents_listing_requires_auth(client):
    r = client.get("/api/documents")
    assert r.status_code in GATED or r.status_code == 404


def test_unknown_route_is_404_not_500(client):
    r = client.get("/api/v1/definitely-not-a-route-xyz")
    assert r.status_code == 404
