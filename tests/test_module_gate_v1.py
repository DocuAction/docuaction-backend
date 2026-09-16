"""The legacy TEFCA router prefix `/api/v1/tefca` is owned by the TEFCA module
(remediation contract 2026-09-17, section 9).

Before this fix `MODULE_REGISTRY["tefca_arc"]` listed `/api/tefca` but not
`/api/v1/tefca`, so a deployment profile that disabled the TEFCA module still
served every `/api/v1/tefca/*` endpoint. These tests run against the REAL app
so the router that is actually mounted is what is being gated.
"""

from __future__ import annotations

import pytest

from app.core.modules import MODULE_REGISTRY, module_for_path, reset_profile_cache


@pytest.fixture
def profile(monkeypatch):
    def _set(program=None, disabled=None, enabled=None):
        for k in ("DOCUACTION_PROGRAM", "DOCUACTION_MODULES_DISABLED",
                  "DOCUACTION_MODULES_ENABLED"):
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


def test_registry_lists_the_v1_prefix_under_tefca_arc():
    assert "/api/v1/tefca" in MODULE_REGISTRY["tefca_arc"][1]
    assert module_for_path("/api/v1/tefca/cycles") == "tefca_arc"
    assert module_for_path("/api/v1/tefca") == "tefca_arc"
    # segment-bounded: an unrelated prefix is not swept in
    assert module_for_path("/api/v1/tefcax/cycles") is None
    assert module_for_path("/api/v1/usps/metrics") == "tefca_arc"


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/tefca/cycles"),
    ("POST", "/api/v1/tefca/cycles"),
    ("GET", "/api/v1/tefca/priority-cases"),
    ("POST", "/api/v1/tefca/validate/entity"),
    ("GET", "/api/tefca/rce/delivery-jobs"),
])
def test_disabled_tefca_module_answers_404_for_v1_paths(client, profile, method, path):
    profile("ALL", disabled="tefca_arc")
    r = client.request(method, path)
    assert r.status_code == 404, (method, path, r.status_code)
    body = r.json()
    assert body["code"] == "NOT_FOUND" and body["error"] == "Not Found"
    assert set(body) == {"error", "code", "request_id"}


def test_enabled_profile_is_unchanged_for_v1_paths(client, profile):
    """Under the default profile the gate is transparent: the router answers
    (401 for an anonymous caller), never the gate's 404."""
    profile()
    assert client.get("/api/v1/tefca/cycles").status_code == 401
    assert client.post("/api/v1/tefca/cycles").status_code in (401, 422)
    assert client.get("/api/tefca/rce/delivery-jobs").status_code == 401


def test_tefca_profile_itself_serves_v1_paths(client, profile):
    profile("TEFCA_ARC")
    assert client.get("/api/v1/tefca/cycles").status_code == 401
    # and Core stays served, as before
    assert client.get("/health").status_code == 200
