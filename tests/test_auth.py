from conftest import GATED


def test_login_invalid_credentials(client, db_required):
    response = client.post("/api/auth/login", json={
        "email": "nonexistent@test.local",
        "password": "wrong"
    })
    assert response.status_code in (401, 429)


def test_login_empty_body(client):
    response = client.post("/api/auth/login", json={})
    assert response.status_code == 422


def test_signup_weak_password(client, db_required):
    response = client.post("/api/auth/signup", json={
        "email": "weak@test.local",
        "password": "123",
        "full_name": "Weak User",
        "company": "Test"
    })
    assert response.status_code in (422, 400)


def test_auth_me_no_token(client):
    response = client.get("/api/auth/me")
    assert response.status_code in GATED


def test_auth_me_invalid_token(client):
    response = client.get("/api/auth/me",
                          headers={"Authorization": "Bearer invalid"})
    assert response.status_code in GATED


def test_auth_me_malformed_header(client):
    response = client.get("/api/auth/me",
                          headers={"Authorization": "NotBearer token"})
    assert response.status_code in GATED


def test_auth_me_none_algorithm_token(client):
    """A JWT with alg=none must be rejected. Accepting it is the classic JWT
    library flaw and would let anyone mint an admin token."""
    none_token = (
        "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
        "eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhZG1pbiJ9."
    )
    response = client.get("/api/auth/me",
                          headers={"Authorization": f"Bearer {none_token}"})
    assert response.status_code in GATED


def test_login_does_not_reveal_whether_account_exists(client, db_required):
    """Different responses for unknown-user versus wrong-password turn login into
    an account enumeration oracle."""
    unknown = client.post("/api/auth/login", json={
        "email": "definitely-not-a-user@test.local", "password": "x"})
    assert unknown.status_code in (401, 422, 429)
    if unknown.status_code == 401:
        assert "not found" not in unknown.text.lower()
        assert "no such user" not in unknown.text.lower()
