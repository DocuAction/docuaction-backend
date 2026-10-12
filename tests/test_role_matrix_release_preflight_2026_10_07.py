"""Per-role authorization for report release, preflight, delivery registration and report generation.

Every role is exercised in BOTH directions with a real signed token (same technique and fixtures as
test_rbac_roles.py): below the floor the answer must be 403, at or above it the role gate must let the call
through (any status other than 403 - the route body runs against a stub session and may fail, which is not what
is being asserted here). A gate that denies everyone passes a deny-only suite, which is exactly the gap
test_rbac_roles.py documents, so the allow direction is asserted for every role that is meant to get in.

This proves the SERVER-SIDE role gates of the code on this build. It is not evidence about any deployed
environment or about real accounts: a 401 (no or bad token) proves only that authentication is required, and is
never counted here as role separation.
"""
from __future__ import annotations

import pytest

from app.core.security import ROLE_HIERARCHY
from test_rbac_roles import as_role  # noqa: F401  (fixture; `client` comes from conftest.py)

ROLES = ["viewer", "contributor", "manager", "reviewer", "senior_analyst", "qalead", "program_manager", "admin"]
RID = "DA-ARC-2026-999"
INTAKE = "00000000-0000-0000-0000-000000000001"
JOB = "00000000-0000-0000-0000-000000000002"

# (method, path, floor role, request kwargs)
ENDPOINTS = [
    ("POST", f"/api/reports/{RID}/release", "program_manager",
     {"json": {"action": "PM_REVIEWED", "note": "x"}}),
    ("GET", f"/api/reports/{RID}/release", "viewer", {}),
    ("POST", "/api/reports/generate", "reviewer",
     {"json": {"report_type": "delivery_processing", "parameters": {"job_id": JOB}}}),
    ("POST", f"/api/tefca/rce/deliveries/{INTAKE}/preflight", "reviewer", {}),
    ("GET", f"/api/tefca/rce/deliveries/{INTAKE}/preflight", "reviewer", {}),
    ("POST", "/api/tefca/rce/official-deliveries", "program_manager",
     {"files": {"file": ("synthetic.psv", b"id|name\n1|x\n", "text/plain")}}),
    ("GET", "/api/tefca/rce/delivery-jobs", "viewer", {}),
]


def _id(e):
    return f"{e[0]} {e[1].replace(RID, '{report}').replace(INTAKE, '{intake}')}"


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=_id)
@pytest.mark.parametrize("role", ROLES)
def test_role_gate_matches_the_documented_floor(as_role, endpoint, role):
    method, path, floor, kwargs = endpoint
    status = as_role(role)(method, path, **kwargs).status_code
    if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[floor]:
        assert status == 403, f"{role} must be denied {method} {path} (floor {floor}); got {status}"
    else:
        assert status != 403, f"{role} must pass the role gate on {method} {path} (floor {floor}); got 403"


def test_the_floors_asserted_here_are_the_ones_the_code_declares():
    """If someone moves a floor in code, this fails until the table above (and the claim) is revisited."""
    from app.reports import routes as report_routes
    from app.tefca_registry.rce import delivery_routes, preflight_shadow_routes

    assert delivery_routes.DATA_OPERATIONS_ROLE == "program_manager"
    assert preflight_shadow_routes.EVIDENCE_ROLE == "reviewer"
    assert callable(report_routes.post_release)


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=_id)
def test_anonymous_and_bad_tokens_are_401_and_are_not_a_role_check(client, endpoint):
    """Documented so nobody counts these as role separation: they show authentication is required, nothing more.
    One endpoint per test so the per-IP burst limiter (reset between tests) is never what answers."""
    method, path, _floor, kwargs = endpoint
    assert client.request(method, path, **kwargs).status_code == 401, (method, path)
    bad = client.request(method, path, headers={"Authorization": "Bearer not.a.token"}, **kwargs)
    assert bad.status_code == 401, (method, path)
