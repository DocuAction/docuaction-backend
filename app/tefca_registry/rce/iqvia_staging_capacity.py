"""Read-only measurement of the staging capacity of THIS container, for the IQVIA benchmark and import.

Why it exists: the assembled upload (9.8 GB for the HCP_AFFIL file) is written to the container's temp directory, and the
importer streams from it. Neither the free space of that directory nor the process memory against its container limit was
measurable from outside the app (no SSH in the image, no diagnostics endpoint). This returns numbers only:
no file names, paths, row values or credentials.

Everything is best-effort and states what it could not read (`null`), because a measurement that silently falls back is not
a measurement. Linux cgroup v2 and v1 files are both tried; on Windows (tests) the Linux-only fields are null.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


def _read_int(path: str) -> Optional[int]:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
        return None if raw in ("", "max") else int(raw)
    except Exception:  # noqa: BLE001 - absent or unreadable is a legitimate answer
        return None


def _proc_status_kb(field: str, status_path: str = "/proc/self/status") -> Optional[int]:
    try:
        for line in Path(status_path).read_text(encoding="utf-8").splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1])
    except Exception:  # noqa: BLE001
        return None
    return None


def _dir_bytes(path: Path, *, limit_entries: int = 200000) -> Optional[int]:
    """Total size of regular files under `path` (the staged uploads). Bounded so a runaway directory cannot stall a request."""
    try:
        total = n = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                n += 1
                if n > limit_entries:
                    return None
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
        return total
    except Exception:  # noqa: BLE001
        return None


def measure(*, tmp_dir: Optional[str] = None, upload_dir: Optional[str] = None) -> Dict[str, Any]:
    tmp = Path(tmp_dir or tempfile.gettempdir())
    uploads = Path(upload_dir) if upload_dir else tmp / "docuaction-iqvia-uploads"
    try:
        du = shutil.disk_usage(tmp)
        disk = {"total_bytes": du.total, "used_bytes": du.used, "free_bytes": du.free}
    except Exception:  # noqa: BLE001
        disk = {"total_bytes": None, "used_bytes": None, "free_bytes": None}

    mem_limit = _read_int("/sys/fs/cgroup/memory.max")
    mem_current = _read_int("/sys/fs/cgroup/memory.current")
    source = "cgroup_v2"
    if mem_limit is None and mem_current is None:
        mem_limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        mem_current = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        source = "cgroup_v1"
    # an unset v1 limit is a huge sentinel (about 2**63); report it as no limit
    if mem_limit is not None and mem_limit >= 1 << 60:
        mem_limit = None
    if mem_limit is None and mem_current is None:
        source = None

    rss_kb = _proc_status_kb("VmRSS")
    hwm_kb = _proc_status_kb("VmHWM")
    return {
        "temp_dir_filesystem": disk,
        "staged_uploads_bytes": _dir_bytes(uploads) if uploads.exists() else 0,
        "process": {"rss_bytes": rss_kb * 1024 if rss_kb is not None else None,
                    "peak_rss_bytes": hwm_kb * 1024 if hwm_kb is not None else None},
        "container_memory": {"source": source, "limit_bytes": mem_limit, "current_bytes": mem_current},
        "cpu_count": os.cpu_count(),
    }
