"""The staging-capacity probe: numbers only, honest nulls, admin only."""
from __future__ import annotations

import json
from pathlib import Path

from app.tefca_registry.rce import iqvia_staging_capacity as cap


def test_reports_free_space_and_staged_bytes_without_names(tmp_path):
    up = tmp_path / "docuaction-iqvia-uploads"
    up.mkdir()
    (up / "secret-looking-name.csv").write_bytes(b"x" * 1234)
    out = cap.measure(tmp_dir=str(tmp_path), upload_dir=str(up))
    assert out["staged_uploads_bytes"] == 1234
    assert out["temp_dir_filesystem"]["free_bytes"] > 0
    blob = json.dumps(out)
    assert "secret-looking-name" not in blob and str(tmp_path) not in blob


def test_missing_upload_dir_is_zero_not_error(tmp_path):
    assert cap.measure(tmp_dir=str(tmp_path))["staged_uploads_bytes"] == 0


def test_unreadable_linux_files_are_null_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "_read_int", lambda p: None)
    monkeypatch.setattr(cap, "_proc_status_kb", lambda f, status_path="": None)
    out = cap.measure(tmp_dir=str(tmp_path))
    assert out["container_memory"] == {"source": None, "limit_bytes": None, "current_bytes": None}
    assert out["process"]["rss_bytes"] is None and out["process"]["peak_rss_bytes"] is None


def test_v1_unset_limit_sentinel_means_no_limit(tmp_path, monkeypatch):
    vals = {"/sys/fs/cgroup/memory.max": None, "/sys/fs/cgroup/memory.current": None,
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": 9223372036854771712,
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": 5000}
    monkeypatch.setattr(cap, "_read_int", lambda p: vals.get(p))
    out = cap.measure(tmp_dir=str(tmp_path))
    assert out["container_memory"] == {"source": "cgroup_v1", "limit_bytes": None, "current_bytes": 5000}


def test_route_requires_admin():
    from app.tefca_registry.rce import iqvia_routes as r

    route = next(x for x in r.router.routes if getattr(x, "path", "").endswith("/staging-capacity"))
    assert route.methods == {"GET"}
    deps = [d.call.__qualname__ for d in route.dependant.dependencies]
    assert any("require_role" in q for q in deps)
