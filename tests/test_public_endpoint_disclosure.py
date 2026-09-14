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


def test_config_is_public_safe_and_names_the_program_profile(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"environment", "version", "api_host", "program", "enabled_modules", "disabled_modules"}
    assert body["program"] in ("ALL", "TEFCA_ARC")
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
    allowed = {"available", "partial", "unavailable"}
    for live in (True, False):
        snap = _connector_health_snapshot(_health(pecos_live=live))
        for k, v in snap.items():
            if k.endswith("_backing"):
                continue
            assert v in allowed, (k, v)
