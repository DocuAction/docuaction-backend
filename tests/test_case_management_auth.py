"""Sprint 1 security fix verification - case management requires auth"""
from conftest import GATED

CASE_MGMT_ENDPOINTS = [
    "/api/v1/case-management/patients",
    "/api/v1/case-management/notes",
    "/api/v1/case-management/info",
    "/api/v1/case-management/dashboard/stats",
    "/api/v1/case-management/care-plans",
]


def test_case_management_requires_auth(client):
    for endpoint in CASE_MGMT_ENDPOINTS:
        response = client.get(endpoint)
        assert response.status_code in GATED, \
            f"{endpoint} returned {response.status_code}, expected 401/403"


def test_case_management_post_requires_auth(client):
    response = client.post("/api/v1/case-management/patients", json={})
    assert response.status_code in GATED


def test_case_management_rejects_invalid_token(client):
    response = client.get("/api/v1/case-management/patients",
                          headers={"Authorization": "Bearer nonsense"})
    assert response.status_code in GATED
