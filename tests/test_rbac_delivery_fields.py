"""Authorization on every route the delivery-workflow remediation added or
re-floored (contract 2026-09-17, section 6), asserted in BOTH directions.

  anonymous            -> 401
  forged token         -> 401 (wrong signing key)
  expired token        -> 401
  below the floor      -> 403, with required_role / current_role in the body
  at the floor         -> not 403 (200 / 404 / 422 / 500 all mean "let through")

The allow direction uses the same stubbed-session technique as
tests/test_rbac_roles.py: `require_role` resolves the bearer to an in-memory
user and the route body then runs against the stub, which is fine because only
the authorization outcome is asserted here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from jose import jwt

from app.core.database import get_db
from app.core.security import ALGORITHM, ROLE_HIERARCHY, create_access_token
from app.main import app

DENIED = 403
INTAKE = str(uuid.uuid4())
JOB = str(uuid.uuid4())
ISSUE = str(uuid.uuid4())

#: (method, path, floor, body/files) for every route this lane owns or re-floored.
ROUTES = [
    ("GET", f"/api/tefca/rce/delivery-jobs/{JOB}/detail", "viewer", None),
    ("GET", f"/api/tefca/rce/delivery-jobs/{JOB}/timeline", "viewer", None),
    ("GET", "/api/tefca/rce/delivery-jobs", "viewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/verification-coverage", "viewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/dispositions", "reviewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/dispositions.csv", "reviewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/exceptions", "reviewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/audit", "reviewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/records", "reviewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/curated", "reviewer", None),
    ("GET", f"/api/tefca/rce/curated/{uuid.uuid4()}/lineage", "reviewer", None),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/issues", "reviewer", None),
    ("POST", f"/api/tefca/rce/issues/{ISSUE}/dispositions", "reviewer",
     {"json": {"decision": "WAIVED", "reason": "test"}}),
    ("POST", "/api/tefca/rce/identifier-decisions", "reviewer",
     {"json": {"entity_id": str(uuid.uuid4()), "identifier_type": "npi",
               "decision": "CONFIRM_EXISTING", "reason": "test"}}),
    ("POST", "/api/tefca/rce/deliveries", "program_manager",
     {"files": {"file": ("d.txt", b"a|b\n1|2\n", "text/plain")}}),
    ("POST", "/api/tefca/rce/official-deliveries", "program_manager",
     {"files": {"file": ("d.txt", b"a|b\n1|2\n", "text/plain")}}),
    ("GET", "/api/admin/health", "admin", None),
]
IDS = [f"{m} {p.split('/api/')[1]}" for m, p, _f, _b in ROUTES]

ORDER = ["viewer", "contributor", "manager", "reviewer", "senior_analyst",
         "qalead", "program_manager", "admin"]


def _just_below(floor: str):
    idx = ORDER.index(floor)
    return ORDER[idx - 1] if idx > 0 else None


# -- stub session ----------------------------------------------------------------

class _User:
    def __init__(self, role):
        self.id = str(uuid.uuid4()); self.email = f"{role}@test.local"; self.role = role
        self.full_name = role; self.company = ""; self.plan = "enterprise"
        self.tenant_id = "default"; self.is_active = True; self.is_verified = True
        self.status = "active"; self.tokens_revoked_at = None; self.allowed_modules = []


class _Result:
    def __init__(self, user): self._user = user
    def scalar_one_or_none(self): return self._user
    def scalar(self): return None
    def scalars(self): return self
    def all(self): return []
    def first(self): return None
    def one(self): raise RuntimeError("stub")
    def mappings(self): return self
    def __iter__(self): return iter(())


class _Session:
    def __init__(self, user): self._user = user
    async def execute(self, *a, **k): return _Result(self._user)
    async def commit(self): return None
    async def rollback(self): return None
    async def refresh(self, *a, **k): return None
    async def flush(self): return None
    async def close(self): return None
    async def get(self, *a, **k): return None
    def add(self, *a, **k): return None


@pytest.fixture
def as_role(client):
    def _make(role):
        assert role in ROLE_HIERARCHY
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


def _kw(body):
    return dict(body or {})


def _reset_limiter():
    """The tiered limiter allows a 10-request burst per identity; a loop over
    17 routes as ONE user trips it (429), which is the harness, not the gate."""
    from app.core import rate_limiter as rl
    rl._request_log.clear()
    rl._burst_log.clear()


# -- deny direction ----------------------------------------------------------------

@pytest.mark.parametrize("method,path,floor,body", ROUTES, ids=IDS)
def test_anonymous_is_refused_401(client, method, path, floor, body):
    r = client.request(method, path, **_kw(body))
    assert r.status_code == 401, (method, path, r.status_code, r.text[:200])
    assert "request_id" in r.json()


@pytest.mark.parametrize("method,path,floor,body", ROUTES, ids=IDS)
def test_forged_token_is_refused(client, method, path, floor, body):
    forged = jwt.encode({"sub": str(uuid.uuid4()), "role": "admin",
                         "exp": datetime.utcnow() + timedelta(hours=1), "type": "access"},
                        "not-the-real-secret-key-" * 3, algorithm=ALGORITHM)
    r = client.request(method, path, headers={"Authorization": f"Bearer {forged}"}, **_kw(body))
    assert r.status_code == 401, (method, path, r.status_code)


@pytest.mark.parametrize("method,path,floor,body", ROUTES, ids=IDS)
def test_expired_token_is_refused(client, method, path, floor, body):
    from app.core.config import settings

    expired = jwt.encode({"sub": str(uuid.uuid4()), "role": "admin",
                          "exp": datetime.utcnow() - timedelta(minutes=5),
                          "iat": datetime.utcnow() - timedelta(hours=1), "type": "access"},
                         settings.SECRET_KEY, algorithm=ALGORITHM)
    r = client.request(method, path, headers={"Authorization": f"Bearer {expired}"}, **_kw(body))
    assert r.status_code == 401, (method, path, r.status_code)


@pytest.mark.parametrize("method,path,floor,body", ROUTES, ids=IDS)
def test_role_just_below_the_floor_is_refused_403_with_detail(as_role, method, path, floor, body):
    below = _just_below(floor)
    if below is None:
        pytest.skip("viewer is the lowest role; there is nothing below it")
    r = as_role(below)(method, path, **_kw(body))
    assert r.status_code == DENIED, (method, path, below, r.status_code, r.text[:200])
    detail = r.json()
    assert detail["code"] == "FORBIDDEN"
    assert detail["required_role"] == floor
    assert detail["current_role"] == below
    assert "request_id" in detail


def test_viewer_is_denied_every_value_bearing_read(as_role):
    viewer = as_role("viewer")
    for method, path, floor, body in ROUTES:
        if floor == "viewer":
            continue
        _reset_limiter()
        r = viewer(method, path, **_kw(body))
        assert r.status_code == DENIED, (method, path, r.status_code)


# -- allow direction --------------------------------------------------------------

@pytest.mark.parametrize("method,path,floor,body", ROUTES, ids=IDS)
def test_role_at_the_floor_is_let_through(as_role, method, path, floor, body):
    r = as_role(floor)(method, path, **_kw(body))
    assert r.status_code != DENIED, (method, path, floor, r.status_code, r.text[:200])
    assert r.status_code != 401


def test_reviewer_reaches_the_evidence_routes(as_role):
    reviewer = as_role("reviewer")
    for method, path, floor, body in ROUTES:
        if floor in ("viewer", "reviewer"):
            _reset_limiter()
            r = reviewer(method, path, **_kw(body))
            assert r.status_code not in (401, DENIED), (method, path, r.status_code)


def test_program_manager_is_required_for_the_sync_upload(as_role):
    files = {"file": ("d.txt", b"a|b\n1|2\n", "text/plain")}
    assert as_role("reviewer")("POST", "/api/tefca/rce/deliveries", files=files).status_code == DENIED
    assert as_role("qalead")("POST", "/api/tefca/rce/deliveries", files=files).status_code == DENIED
    _reset_limiter()
    r = as_role("program_manager")("POST", "/api/tefca/rce/deliveries", files=files)
    assert r.status_code != DENIED
    # Deprecation headers travel with every answer the authorised route body
    # gives, success or refusal (here the stub session makes the scan refuse).
    assert r.status_code < 500, r.text
    assert r.headers.get("Deprecation") == "true"
    assert r.headers.get("Sunset")
    assert "/api/tefca/rce/official-deliveries" in r.headers.get("Link", "")


def test_admin_is_required_for_admin_health(as_role):
    assert as_role("program_manager")("GET", "/api/admin/health").status_code == DENIED
    assert as_role("admin")("GET", "/api/admin/health").status_code == 200


def test_admin_retains_access_everywhere(as_role):
    admin = as_role("admin")
    for method, path, floor, body in ROUTES:
        _reset_limiter()
        r = admin(method, path, **_kw(body))
        assert r.status_code not in (401, DENIED), (method, path, r.status_code)


# -- static: every route declares a floor ---------------------------------------------

def test_every_delivery_route_declares_a_require_role_floor():
    from app.core.security import ROLE_HIERARCHY as H
    from app.tefca_registry.rce import delivery_routes, routes as rce_routes
    from app.api import admin_health

    def deps(d, seen=None):
        seen = seen if seen is not None else set()
        if id(d) in seen:
            return
        seen.add(id(d))
        if d.call is not None:
            yield d.call
        for s in d.dependencies:
            yield from deps(s, seen)

    expected = {
        "/api/tefca/rce/delivery-jobs/{job_id}/detail": "viewer",
        "/api/tefca/rce/delivery-jobs/{job_id}/timeline": "viewer",
        "/api/tefca/rce/deliveries/{intake_id}/verification-coverage": "viewer",
        "/api/tefca/rce/deliveries/{intake_id}/dispositions": "reviewer",
        "/api/tefca/rce/deliveries/{intake_id}/dispositions.csv": "reviewer",
        "/api/tefca/rce/deliveries/{intake_id}/exceptions": "reviewer",
        "/api/tefca/rce/deliveries/{intake_id}/audit": "reviewer",
        "/api/tefca/rce/issues/{issue_id}/dispositions": "reviewer",
        "/api/tefca/rce/identifier-decisions": "reviewer",
        "/api/tefca/rce/official-deliveries": "program_manager",
        "/api/tefca/rce/deliveries/{intake_id}/records": "reviewer",
        "/api/tefca/rce/deliveries/{intake_id}/curated": "reviewer",
        "/api/tefca/rce/curated/{curated_id}/lineage": "reviewer",
        "/api/tefca/rce/deliveries/{intake_id}/issues": "reviewer",
        "/api/admin/health": "admin",
    }
    seen = {}
    for router in (delivery_routes.router, rce_routes.router, admin_health.router):
        for route in router.routes:
            floors = [getattr(fn, "minimum_role", None) for fn in deps(route.dependant)]
            floors = [f for f in floors if f in H]
            assert floors, f"{route.path} has no require_role"
            seen[route.path] = max(floors, key=H.get)
    for path, floor in expected.items():
        assert seen.get(path) == floor, (path, seen.get(path), floor)
    # the deprecated sync upload
    sync = [r for r in rce_routes.router.routes
            if r.path == "/api/tefca/rce/deliveries" and "POST" in r.methods][0]
    floors = [getattr(fn, "minimum_role", None) for fn in deps(sync.dependant)]
    assert "program_manager" in floors
