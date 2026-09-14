"""Server-side module boundary (app/core/modules.py).

A federal TEFCA deployment must not serve GovCon endpoints, and hiding a link is
not a boundary. These tests prove the gate answers 404 before routing for a
disabled module, serves Core and TEFCA paths unchanged, keeps the default
profile identical to pre-gate behaviour, and fails closed on a bad profile name.
"""
import os

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core import modules
from app.core.modules import (MODULE_REGISTRY, PROGRAM_PROFILES, ModuleGateMiddleware,
                              deployment_profile, module_enabled, module_for_path,
                              profile_summary, reset_profile_cache)


@pytest.fixture
def profile(monkeypatch):
    """Set the deployment environment for one test and clear the cached profile."""
    def _set(program=None, disabled=None, enabled=None):
        for k in ("DOCUACTION_PROGRAM", "DOCUACTION_MODULES_DISABLED", "DOCUACTION_MODULES_ENABLED"):
            monkeypatch.delenv(k, raising=False)
        if program is not None:
            monkeypatch.setenv("DOCUACTION_PROGRAM", program)
        if disabled is not None:
            monkeypatch.setenv("DOCUACTION_MODULES_DISABLED", disabled)
        if enabled is not None:
            monkeypatch.setenv("DOCUACTION_MODULES_ENABLED", enabled)
        reset_profile_cache()
    yield _set
    reset_profile_cache()


def _app():
    async def ok(request):
        return JSONResponse({"path": request.url.path})
    app = Starlette(routes=[
        Route("/api/ats/jobs", ok), Route("/ats/jobs", ok), Route("/api/rfq/1/bom", ok),
        Route("/api/healthcare/metrics", ok), Route("/api/v1/bulletin/latest", ok),
        Route("/api/tefca/status", ok), Route("/api/reports/sow", ok), Route("/api/learning/TEFCA_ARC", ok),
        Route("/api/auth/me", ok), Route("/api/admin/users", ok), Route("/api/documents", ok), Route("/health", ok),
    ])
    app.add_middleware(ModuleGateMiddleware)
    return TestClient(app)


# ── path -> module mapping ────────────────────────────────────────────────────

def test_registry_maps_govcon_paths_with_and_without_api_prefix():
    assert module_for_path("/api/ats/jobs") == "govcon"
    assert module_for_path("/ats/jobs") == "govcon"
    assert module_for_path("/api/rfq/1/bom") == "govcon"
    assert module_for_path("/api/deal-registrations") == "govcon"


def test_registry_maps_tefca_and_optional_core_modules():
    assert module_for_path("/api/tefca/arc/cases") == "tefca_arc"
    assert module_for_path("/api/reports/sow") == "tefca_arc"
    assert module_for_path("/api/learning/TEFCA_ARC") == "tefca_arc"
    assert module_for_path("/api/healthcare/metrics") == "healthcare_claims"
    assert module_for_path("/api/v1/case-management/patients") == "case_management"
    assert module_for_path("/api/migration/status") == "migration_intelligence"


def test_core_paths_map_to_no_module_and_are_always_enabled():
    for path in ("/api/auth/me", "/api/admin/users", "/api/documents", "/api/config", "/health", "/api/enterprise/audit"):
        assert module_for_path(path) is None, path
        assert module_enabled(module_for_path(path))


def test_prefix_match_is_segment_bounded():
    assert module_for_path("/api/atsx/jobs") is None
    assert module_for_path("/api/tefcaX") is None


# ── profiles ─────────────────────────────────────────────────────────────────

def test_default_profile_is_all_modules_enabled(profile):
    profile()
    program, enabled = deployment_profile()
    assert program == "ALL"
    assert enabled == frozenset(MODULE_REGISTRY)
    assert profile_summary()["disabled_modules"] == []


def test_tefca_profile_enables_only_tefca_module(profile):
    profile("TEFCA_ARC")
    program, enabled = deployment_profile()
    assert program == "TEFCA_ARC"
    assert enabled == frozenset({"tefca_arc"})
    assert "govcon" in profile_summary()["disabled_modules"]


def test_unknown_profile_fails_closed_to_tefca(profile):
    profile("PRODUCTION-EVERYTHING")
    program, enabled = deployment_profile()
    assert program == "TEFCA_ARC"
    assert enabled == frozenset({"tefca_arc"})


def test_profile_name_is_case_and_dash_insensitive(profile):
    profile("tefca-arc")
    assert deployment_profile()[0] == "TEFCA_ARC"


def test_disabled_override_removes_module_from_default_profile(profile):
    profile("ALL", disabled="govcon,healthcare_claims")
    _p, enabled = deployment_profile()
    assert "govcon" not in enabled and "healthcare_claims" not in enabled and "tefca_arc" in enabled


def test_enabled_override_reenables_known_module_only(profile):
    profile("TEFCA_ARC", enabled="bulletin_intelligence,not_a_module")
    _p, enabled = deployment_profile()
    assert enabled == frozenset({"tefca_arc", "bulletin_intelligence"})


def test_profile_summary_is_module_ids_only(profile):
    profile("TEFCA_ARC")
    summary = profile_summary()
    assert set(summary) == {"program", "enabled_modules", "disabled_modules"}
    for value in summary["enabled_modules"] + summary["disabled_modules"]:
        assert value in MODULE_REGISTRY


# ── the gate itself ───────────────────────────────────────────────────────────

def test_gate_is_transparent_under_default_profile(profile):
    profile()
    client = _app()
    for path in ("/api/ats/jobs", "/ats/jobs", "/api/healthcare/metrics", "/api/tefca/status", "/api/auth/me", "/health"):
        assert client.get(path).status_code == 200, path


def test_gate_answers_404_for_disabled_module_before_routing(profile):
    profile("TEFCA_ARC")
    client = _app()
    for path in ("/api/ats/jobs", "/ats/jobs", "/api/rfq/1/bom", "/api/healthcare/metrics", "/api/v1/bulletin/latest"):
        r = client.get(path)
        assert r.status_code == 404, path
        body = r.json()
        assert body["code"] == "NOT_FOUND" and body["error"] == "Not Found" and "request_id" in body


def test_gate_404_is_indistinguishable_from_unmounted_route(profile):
    profile("TEFCA_ARC")
    client = _app()
    gated = client.get("/api/ats/jobs").json()
    assert set(gated) == {"error", "code", "request_id"}
    # No module name, profile name or hint leaks in the body.
    assert "govcon" not in str(gated).lower() and "tefca" not in str(gated).lower()


def test_gate_serves_tefca_and_core_under_tefca_profile(profile):
    profile("TEFCA_ARC")
    client = _app()
    for path in ("/api/tefca/status", "/api/reports/sow", "/api/learning/TEFCA_ARC", "/api/auth/me", "/api/admin/users", "/api/documents", "/health"):
        assert client.get(path).status_code == 200, path


def test_gate_applies_to_every_method(profile):
    profile("TEFCA_ARC")
    client = _app()
    for method in ("get", "post", "put", "delete", "patch"):
        assert getattr(client, method)("/api/ats/jobs").status_code == 404, method


def test_gate_is_wired_into_the_application():
    """The real app mounts the gate (inside CORS, so a gated 404 carries CORS headers)."""
    from app.main import app
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "ModuleGateMiddleware" in names
    # Starlette inserts each add_middleware() at the FRONT of user_middleware, so
    # the list reads outermost first: TrustedHost, CORS, RateLimit, ModuleGate.
    assert names.index("TrustedHostMiddleware") < names.index("CORSMiddleware") < names.index("RateLimitMiddleware") < names.index("ModuleGateMiddleware")


def test_every_govcon_router_prefix_is_covered_by_the_registry():
    """If someone ever mounts app/routers, the gate already owns those prefixes
    (as declared, i.e. without "/api"). "/api/export" and "/api/intel" are Core."""
    import glob, re
    prefixes = set()
    for f in glob.glob(os.path.join(os.path.dirname(__file__), "..", "app", "routers", "*.py")):
        m = re.search(r'prefix="(/[a-z-]+)', open(f, encoding="utf-8").read())
        if m:
            prefixes.add(m.group(1))
    assert prefixes, "no GovCon routers found — inventory changed"
    uncovered = {p for p in prefixes if module_for_path(p) != "govcon"}
    assert not uncovered, uncovered
    # Core document export and meeting intelligence keep their /api paths.
    assert module_for_path("/api/export/abc/pdf") is None
    assert module_for_path("/api/intel/dashboard") == "meeting_intelligence"
