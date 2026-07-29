def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_health_reports_version(client):
    data = client.get("/health").json()
    assert "version" in data
    assert "modules" in data


def test_health_never_leaks_configuration(client):
    """/health is unauthenticated. It must not disclose secret VALUES.

    Deliberately does not match on the substring "api_key": /health legitimately
    names SAM_GOV_API_KEY when reporting that a connector is unconfigured. A
    variable name is not a credential, and a test that cannot tell the difference
    produces a false finding on a correct behaviour.
    """
    body = client.get("/health").text
    lowered = body.lower()
    for leak in ("postgresql://", "postgres://", "@microsoft.keyvault", "sk-ant-", "sk-"):
        assert leak not in lowered, f"/health disclosed {leak!r}"
    # A long opaque token would indicate a real value rather than a name.
    import re
    assert not re.search(r'"[A-Za-z0-9+/=_\-]{40,}"', body),         "/health contains a long opaque literal that may be a credential"
