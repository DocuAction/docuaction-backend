"""Unauthenticated endpoints disclose only what a caller may know.

/health, /api/config and /api/tefca/status answer before login. They must carry
no credentials, no connection strings, no operator contacts and no false source
capability. The PECOS key in the connector snapshot is backed by the NPPES proxy
and must never read as a connected PECOS feed.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.Tefca.connectors import PECOS_BACKING, PECOS_BACKING_NOTE
from app.Tefca.routes import _connector_health_snapshot

SECRET_SHAPES = [re.compile(p, re.I) for p in (
    r"postgres(ql)?(\+asyncpg)?://", r"secret_key", r"api[_-]?key\W*[:=]\W*\S{8,}", r"password", r"bearer\s+[a-z0-9._-]{20,}",
    r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",   # any e-mail address
)]


def _assert_no_secret_shapes(text: str):
    for pat in SECRET_SHAPES:
        assert not pat.search(text), f"public response matched {pat.pattern!r}"


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


def test_health_has_no_operator_email_or_secrets(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "alert_email" not in (body.get("scheduler") or {})
    _assert_no_secret_shapes(r.text)


def test_health_does_not_advertise_a_module_the_profile_disables(monkeypatch):
    """Under TEFCA_ARC the gate answers 404 for healthcare, case management,
    bulletin, meetings and document automation, so /health must report them
    "disabled", not "active" (independent checker finding L2, 2026-09-14)."""
    from app.core.modules import reset_profile_cache
    from app.main import app
    monkeypatch.setenv("DOCUACTION_PROGRAM", "TEFCA_ARC")
    reset_profile_cache()
    try:
        body = TestClient(app).get("/health").json()
    finally:
        monkeypatch.delenv("DOCUACTION_PROGRAM", raising=False)
        reset_profile_cache()
    modules = body["modules"]
    for key in ("healthcare", "case_management", "bulletin_intelligence",
                "comparison", "extraction", "automation", "audio"):
        assert modules[key] == "disabled", key
    assert modules["documents"] == "active" and modules["data_systems"] == "active"
    assert "tefca_review_protocol" in modules


def test_health_reports_every_module_active_under_the_default_profile():
    from app.core.modules import reset_profile_cache
    from app.main import app
    reset_profile_cache()
    modules = TestClient(app).get("/health").json()["modules"]
    for key in ("healthcare", "case_management", "bulletin_intelligence", "documents"):
        assert modules[key] == "active", key


def test_config_is_public_safe_and_names_the_program_profile(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"environment", "version", "api_host", "program", "enabled_modules"}
    assert body["program"] in ("ALL", "TEFCA_ARC")
    # A module the deployment does not serve answers 404 and must not be
    # discoverable from the deployment, so the public config names only what
    # is served (independent checker finding L1, 2026-09-14).
    assert "disabled_modules" not in body
    _assert_no_secret_shapes(r.text)


# ── connector snapshot truth (pure function, no network) ─────────────────────

def _health(pecos_live, nppes_live=True):
    return {
        "NPPES": {"live": nppes_live, "status": "OK" if nppes_live else "UNAVAILABLE"},
        "OIG_LEIE": {"live": True, "status": "OK"},
        "SAM_GOV": {"live": False, "status": "UNAVAILABLE"},
        "PECOS": {"live": pecos_live, "status": "OK" if pecos_live else "UNAVAILABLE", "note": PECOS_BACKING_NOTE},
    }


def test_pecos_is_partial_when_the_proxy_answers_never_available():
    snap = _connector_health_snapshot(_health(pecos_live=True))
    assert snap["pecos"] == "partial"
    assert snap["pecos_backing"] == PECOS_BACKING == "nppes_proxy"


def test_pecos_is_unavailable_when_the_proxy_does_not_answer():
    snap = _connector_health_snapshot(_health(pecos_live=False))
    assert snap["pecos"] == "unavailable"
    assert snap["pecos_backing"] == "nppes_proxy"


def test_other_connector_states_unchanged():
    snap = _connector_health_snapshot(_health(pecos_live=True))
    assert snap["nppes"] == "available" and snap["leie"] == "available" and snap["sam_gov"] == "unavailable"


def test_pecos_health_note_states_not_connected():
    assert "NOT CONNECTED" in PECOS_BACKING_NOTE and "NPPES" in PECOS_BACKING_NOTE


def test_snapshot_vocabulary_matches_frontend_resolver():
    # src/platform/components/ConnectorStatus.js: HEALTHY / DEGRADED / UNAVAILABLE sets.
    # Fix 3 added free-text presentation fields (pecos_label/pecos_subtitle) and
    # the CMS PECOS-derived keys (cms_ppef_enrollment/cms_revocation), which speak
    # their own AVAILABLE/DEGRADED/UNAVAILABLE vocabulary — neither is part of the
    # legacy four-key available/partial/unavailable status vocabulary this test
    # protects, so both are excluded here rather than folded into `allowed`.
    allowed = {"available", "partial", "unavailable"}
    excluded_suffixes = ("_backing", "_label", "_subtitle")
    for live in (True, False):
        snap = _connector_health_snapshot(_health(pecos_live=live))
        for k, v in snap.items():
            if k.endswith(excluded_suffixes) or k.startswith("cms_"):
                continue
            assert v in allowed, (k, v)


def test_pecos_label_never_claims_direct_connection_or_medicare_enrollment():
    """Fix 3: the legacy pecos key must always carry its proxy caption, and
    that caption must never claim a direct PECOS connection or Medicare
    enrolment verification."""
    snap = _connector_health_snapshot(_health(pecos_live=True))
    assert snap["pecos_label"] == "NPPES Registry — Legacy PECOS Proxy"
    subtitle = snap["pecos_subtitle"].lower()
    assert "not a direct pecos query" in subtitle
    assert "does not establish medicare enrollment" in subtitle
    assert "direct pecos connected" not in subtitle
    assert "verifies medicare enrollment" not in subtitle


def test_cms_ppef_and_revocation_reported_separately_from_legacy_pecos():
    """Fix 3: genuine CMS PECOS-derived sources must be labelled and reported
    under their own keys, never folded into the legacy `pecos` key."""
    health = _health(pecos_live=True)
    cms_systems = [
        {"system": "CMS_PPEF", "status": "AVAILABLE"},
        {"system": "CMS_REVOCATION", "status": "DEGRADED"},
    ]
    snap = _connector_health_snapshot(health, cms_systems=cms_systems)
    assert snap["cms_ppef_enrollment"] == "available"
    assert snap["cms_ppef_enrollment_label"] == "CMS Public Provider Enrollment — PECOS-derived"
    assert snap["cms_revocation"] == "degraded"
    assert snap["cms_revocation_label"] == "CMS Revocation — PECOS-derived"
    # Never conflated with the legacy proxy key.
    assert snap["pecos"] != snap["cms_ppef_enrollment"] or snap["pecos_backing"] == "nppes_proxy"
