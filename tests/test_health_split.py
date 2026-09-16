"""Public /health versus admin /api/admin/health (contract 2026-09-17, section 8).

The public probe says exactly: status, service, version, git_sha, build_time,
environment, modules. Nothing operational, nothing about the database, no
migration revision, no hostnames. The admin surface carries the rest and is
admin-only.
"""

from __future__ import annotations

import re
import uuid

import pytest

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app

PUBLIC_KEYS = {"status", "service", "version", "git_sha", "build_time",
               "environment", "modules"}


# -- public --------------------------------------------------------------------

def test_public_health_has_exactly_the_allowed_keys(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == PUBLIC_KEYS, sorted(body)
    assert body["status"] == "healthy"
    assert body["service"] == "docuaction-backend"
    assert isinstance(body["modules"], dict) and body["modules"]
    assert set(body["modules"].values()) <= {"active", "disabled"}


def test_public_health_carries_build_identity(client, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96")
    monkeypatch.setenv("BUILD_TIME", "2026-09-17T01:02:03Z")
    body = client.get("/health").json()
    assert body["git_sha"] == "75383cf8ebfd5822aebbe50a61cad0a7c6f8ab96"
    assert body["build_time"] == "2026-09-17T01:02:03Z"


def test_public_health_has_no_secrets_hostnames_or_migration_revision(client):
    text = client.get("/health").text.lower()
    for leak in ("postgresql://", "postgres://", "@microsoft.keyvault", "sk-ant-",
                 "migration_revision", "alembic", "azurewebsites", "database",
                 "connector", "scheduler", "usps", "latency", "checked_at"):
        assert leak not in text, leak
    # no hostname-shaped values at all
    assert not re.search(r"[a-z0-9-]+\.(net|com|gov|io|org)\b", text)
    assert not re.search(r'"[A-Za-z0-9+/=_\-]{40,}"', client.get("/health").text)


def test_public_health_is_unauthenticated_and_module_gate_transparent(client):
    assert client.get("/health").status_code == 200


def test_deploy_gate_fields_are_still_present(client):
    """deploy-backend.yml reads `status` (200 wait) and `git_sha` (provenance)."""
    body = client.get("/health").json()
    assert body["status"] == "healthy" and "git_sha" in body


def test_config_reports_git_sha(client):
    body = client.get("/api/config").json()
    assert "git_sha" in body
    assert body["git_sha"] == client.get("/health").json()["git_sha"]


# -- admin -----------------------------------------------------------------------

class _User:
    def __init__(self, role):
        self.id = str(uuid.uuid4()); self.email = f"{role}@test.local"; self.role = role
        self.is_active = True; self.status = "active"; self.tokens_revoked_at = None


class _Result:
    def __init__(self, user): self._user = user
    def scalar_one_or_none(self): return self._user
    def scalar(self): return None


class _Session:
    def __init__(self, user): self._user = user
    async def execute(self, *a, **k): return _Result(self._user)
    async def commit(self): return None
    async def rollback(self): return None
    async def close(self): return None
    def add(self, *a, **k): return None


@pytest.fixture
def as_role(client):
    def _make(role):
        user = _User(role)

        async def _override():
            yield _Session(user)

        app.dependency_overrides[get_db] = _override
        token = create_access_token({"sub": user.id, "role": role}, is_admin=(role == "admin"))

        def call(method, path, **kw):
            kw.setdefault("headers", {}).update({"Authorization": f"Bearer {token}"})
            return client.request(method, path, **kw)
        return call
    yield _make
    app.dependency_overrides.pop(get_db, None)


def test_admin_health_refuses_anonymous(client):
    assert client.get("/api/admin/health").status_code == 401


@pytest.mark.parametrize("role", ["viewer", "contributor", "manager", "reviewer",
                                  "senior_analyst", "qalead", "program_manager"])
def test_admin_health_refuses_every_non_admin_role(as_role, role):
    r = as_role(role)("GET", "/api/admin/health")
    assert r.status_code == 403, (role, r.status_code)
    body = r.json()
    assert body["required_role"] == "admin" and body["current_role"] == role


def test_admin_health_serves_admin_with_operational_fields(as_role):
    r = as_role("admin")("GET", "/api/admin/health")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in PUBLIC_KEYS | {"migration_revision", "database", "scheduler", "connectors",
                              "usps", "program_profile", "request_id"}:
        assert key in body, key
    assert "reachable" in body["database"] and "latency_ms" in body["database"]
    assert "alert_email" not in (body.get("scheduler") or {})
    assert body["request_id"] == r.headers.get("X-Request-ID")
    # still never a secret or connection string
    lowered = r.text.lower()
    for leak in ("postgresql://", "postgres://", "@microsoft.keyvault", "sk-ant-"):
        assert leak not in lowered


def test_admin_health_reads_the_real_migration_revision(client, db_required):
    """Against the isolated database the revision is the Alembic head."""
    from support_delivery_api import headers_for

    r = client.get("/api/admin/health", headers=headers_for("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["migration_revision"] == "20260917_delivery_traceability"
    assert body["database"]["reachable"] is True
    assert isinstance(body["database"]["latency_ms"], int)
