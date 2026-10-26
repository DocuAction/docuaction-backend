"""Route-level checks for GET /api/tefca/rce/records/search (PR #169, added 2026-10-26).

The trailing window (`as_of`, `months`) is passed to the service exactly as stated by the route,
`months` is bounded 1..36, at least one search field is required, and the role floor is viewer.
Pure: the service is replaced by a recorder, the session by an in-memory stub.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from test_rbac_roles import _Session, _User

SEARCH = "/api/tefca/rce/records/search"


@pytest.fixture
def as_role(client):
    installed = []

    def _make(role):
        user = _User(role)

        async def _override():
            yield _Session(user)

        app.dependency_overrides[get_db] = _override
        installed.append(get_db)
        token = create_access_token({"sub": user.id, "role": role})

        def call(method, path, **kw):
            kw.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
            return client.request(method, path, **kw)

        return call

    yield _make
    for dep in installed:
        app.dependency_overrides.pop(dep, None)


@pytest.fixture
def captured_search(monkeypatch):
    from app.tefca_registry.rce import record_history_comparison as svc

    calls = []

    async def fake_search(db, **kw):
        calls.append(kw)
        return {"results": [], "count": 0, "window": {"as_of": str(kw.get("as_of")), "months": kw.get("months")}}

    monkeypatch.setattr(svc, "search_records", fake_search)
    return calls


def test_search_requires_a_signed_in_user(client, captured_search):
    assert client.get(f"{SEARCH}?npi=1234567893").status_code == 401
    assert captured_search == []


def test_window_parameters_reach_the_service_unchanged(as_role, captured_search):
    r = as_role("viewer")("GET", f"{SEARCH}?npi=1234567893&as_of=2026-10-25&months=3")
    assert r.status_code == 200, r.text
    kw = captured_search[-1]
    assert kw["npi"] == "1234567893" and kw["as_of"] == date(2026, 10, 25) and kw["months"] == 3
    assert kw["reviewer_or_above"] is False, "viewer is below reviewer: restricted fields stay hidden"
    r = as_role("reviewer")("GET", f"{SEARCH}?hcid=HCID-1&as_of=2026-10-25")
    assert r.status_code == 200
    assert captured_search[-1]["months"] is None, "default (12) is the service's stated default, not the route's"
    assert captured_search[-1]["reviewer_or_above"] is True


def test_months_is_bounded_and_a_field_is_required(as_role, captured_search):
    call = as_role("viewer")
    assert call("GET", f"{SEARCH}?npi=1&months=0").status_code == 422
    assert call("GET", f"{SEARCH}?npi=1&months=37").status_code == 422
    assert call("GET", f"{SEARCH}?as_of=2026-10-25&months=12").status_code == 422   # no npi/hcid/tefcaid/name
    assert captured_search == [], "rejected requests never reach the service"


def test_only_get_is_exposed(as_role):
    assert as_role("admin")("POST", f"{SEARCH}?npi=1234567893").status_code == 405
