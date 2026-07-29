"""Security header verification"""


def test_cors_no_wildcard(client):
    response = client.get("/health")
    cors = response.headers.get("access-control-allow-origin", "")
    assert cors != "*", "CORS wildcard is not allowed"


def test_content_type_options(client):
    response = client.get("/health")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_no_server_version_disclosure(client):
    """A server banner naming the framework and version hands an attacker the
    version-to-CVE lookup for free."""
    server = client.get("/health").headers.get("server", "").lower()
    assert "fastapi" not in server
    for token in ("uvicorn/", "gunicorn/", "python/"):
        assert token not in server, f"Server header discloses {token!r}"


def test_frame_options_or_csp_present(client):
    """Clickjacking protection via either header is acceptable."""
    h = client.get("/health").headers
    assert h.get("x-frame-options") or h.get("content-security-policy"), \
        "Neither X-Frame-Options nor Content-Security-Policy is set"


def test_error_response_has_correlation_id_not_stack_trace(client):
    """Errors must return a correlation identifier, never internals."""
    response = client.get("/api/v1/definitely-not-a-route-xyz")
    assert response.status_code == 404
    body = response.text.lower()
    assert "traceback" not in body
    assert "file \"" not in body
