"""Rate limiter: role -> tier mapping for every platform role, and CORS-preflight
exemption.

Background (structural hardening, 2026-09-14): the tier map only knew
admin/manager/contributor/viewer, so every TEFCA operational role fell into the
free tier (60/min, burst 10 per 5 s) and a reviewer opening Mission Control could
be answered 429 on the page's own fan-out. Preflight OPTIONS requests were also
counted against the caller's IP bucket, so a shared office egress address could
exhaust the free-tier burst on preflights alone, which the browser reports as a
CORS failure rather than a 429.
"""
import pytest
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core import rate_limiter as rl
from app.core.rate_limiter import RATE_LIMITS, TIER_BY_ROLE, RateLimitMiddleware, tier_for_role
from app.core.security import ROLE_HIERARCHY


@pytest.fixture(autouse=True)
def _clear_buckets():
    rl._request_log.clear(); rl._burst_log.clear()
    yield
    rl._request_log.clear(); rl._burst_log.clear()


def test_every_platform_role_has_an_explicit_tier():
    for role in ROLE_HIERARCHY:
        assert role in TIER_BY_ROLE, f"{role} would silently fall into the free tier"
        assert TIER_BY_ROLE[role] in RATE_LIMITS


def test_tefca_operational_roles_are_not_free_tier():
    for role in ("program_manager", "qalead", "senior_analyst", "reviewer"):
        assert tier_for_role(role) == "business"
        assert RATE_LIMITS[tier_for_role(role)]["burst_max"] >= 50


def test_role_aliases_resolve_like_rbac():
    assert tier_for_role("pm") == "business"
    assert tier_for_role("QA Lead") == "business"
    assert tier_for_role("Senior Analyst") == "business"
    assert tier_for_role("administrator") == "enterprise"


def test_unknown_role_fails_closed_to_free():
    assert tier_for_role("something_new") == "free"
    assert tier_for_role(None) == "free"
    assert tier_for_role("") == "free"


def test_viewer_and_admin_unchanged():
    assert tier_for_role("viewer") == "free"
    assert tier_for_role("admin") == "enterprise"


def _app():
    async def ok(request):
        return JSONResponse({"ok": True})
    app = Starlette(routes=[Route("/api/thing", ok, methods=["GET", "POST"])])
    # Same order as app/main.py: limiter inside CORS.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=["http://ui.example"], allow_methods=["*"], allow_headers=["*"])
    return TestClient(app)


def test_preflight_is_not_counted_and_is_never_429():
    client = _app()
    pre = {"Origin": "http://ui.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    for _ in range(40):   # far beyond the free-tier burst of 10
        r = client.options("/api/thing", headers=pre)
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "http://ui.example"
    # The IP bucket is still empty: real requests get their full burst.
    assert all(client.get("/api/thing").status_code == 200 for _ in range(10))


def test_real_requests_are_still_limited_after_preflights():
    client = _app()
    for _ in range(10):
        assert client.get("/api/thing").status_code == 200
    r = client.get("/api/thing")
    assert r.status_code == 429
    assert r.json()["code"] == "RATE_LIMIT_EXCEEDED"


def test_429_carries_cors_headers_so_the_browser_sees_the_status():
    client = _app()
    for _ in range(10):
        client.get("/api/thing", headers={"Origin": "http://ui.example"})
    r = client.get("/api/thing", headers={"Origin": "http://ui.example"})
    assert r.status_code == 429
    assert r.headers.get("access-control-allow-origin") == "http://ui.example"


def test_limiter_is_not_disabled_or_raised_globally():
    assert RATE_LIMITS["free"] == {"requests_per_minute": 60, "burst_max": 10}
    assert RATE_LIMITS["business"]["requests_per_minute"] == 500
