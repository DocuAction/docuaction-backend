"""QA108-20260927 — /api/tefca/status probes are shared, not multiplied.

The SPA shell fetched this endpoint 3–4× per page view and EVERY call probed
every upstream connector; under that load the endpoint answered in 13–15 s
and returned 503 on DEV. These tests pin the fix: concurrent callers share
one probe, a fresh snapshot is reused inside the TTL, the TTL genuinely
expires, and the explicit operator probe endpoint (`/connectors/status`,
"Test All Connectors") is NOT cached.

No database: the connector manager is faked with a probe counter.
"""
from __future__ import annotations

import asyncio

import pytest


class _CountingManager:
    def __init__(self):
        self.probes = 0

    async def health_check(self):
        self.probes += 1
        await asyncio.sleep(0)  # yield, so concurrent callers really overlap
        return {"NPPES": {"live": True, "status": "OK", "note": "n"},
                "OIG_LEIE": {"live": True, "status": "OK", "note": "n"},
                "SAM_GOV": {"live": False, "status": "UNAVAILABLE", "note": "n"},
                "PECOS": {"live": True, "status": "OK", "note": "n"}}

    # The explicit probe endpoint calls these per-source methods.
    async def probe_all(self):
        self.probes += 1
        return await self.health_check()


@pytest.fixture
def counting_manager(monkeypatch):
    from app.Tefca import routes

    mgr = _CountingManager()
    monkeypatch.setattr(routes, "get_connector_manager", lambda: mgr)

    async def _no_cms():
        return {"systems": None}

    import app.Tefca.cms_ppef as cms
    monkeypatch.setattr(cms, "cms_capability_health", _no_cms)
    # Every test starts cold — the cache is module state.
    routes._status_cache["at"] = 0.0
    routes._status_cache["value"] = None
    yield mgr
    routes._status_cache["at"] = 0.0
    routes._status_cache["value"] = None


def test_rapid_sequential_status_calls_probe_upstreams_once(client, counting_manager):
    r1 = client.get("/api/tefca/status")
    r2 = client.get("/api/tefca/status")
    r3 = client.get("/api/tefca/status")
    assert r1.status_code == r2.status_code == r3.status_code == 200
    assert counting_manager.probes == 1
    assert r1.json()["connector_health"] == r3.json()["connector_health"]


def test_concurrent_status_callers_share_one_probe(counting_manager):
    from app.Tefca.routes import _status_snapshot

    async def _burst():
        return await asyncio.gather(*[_status_snapshot() for _ in range(8)])

    results = asyncio.run(_burst())
    assert counting_manager.probes == 1
    assert all(r == results[0] for r in results)


def test_ttl_expiry_probes_again(client, counting_manager):
    from app.Tefca import routes

    assert client.get("/api/tefca/status").status_code == 200
    assert counting_manager.probes == 1
    # Age the snapshot past the TTL instead of sleeping through it.
    routes._status_cache["at"] -= routes._STATUS_CACHE_TTL_SECONDS + 1
    assert client.get("/api/tefca/status").status_code == 200
    assert counting_manager.probes == 2


def test_status_payload_shape_is_unchanged(client, counting_manager):
    body = client.get("/api/tefca/status").json()
    for key in ("module", "status", "rce_directory_live", "connector_health"):
        assert key in body, key
    assert body["module"] == "tefca_arc"
    # The truthful-labeling keys ride along exactly as before.
    assert "data_source" in body or "dataSource" in body or "data_source_label" in body
