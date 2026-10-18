"""Independent RSS sampler: a SEPARATE process that samples another PID's
resident set every N seconds to a CSV, so a benchmark's peak memory is not
self-reported by the process being measured.

Usage:
    python scripts/perf/rss_sampler.py --pid-file bench.pid --out rss.csv --interval 2

Stops when the sampled process exits. Prints the peak at the end.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

import psutil


def _wait_for_pid(pid_file: str, timeout: float) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(pid_file):
            try:
                return int(open(pid_file, encoding="utf-8").read().strip())
            except ValueError:
                pass
        time.sleep(0.5)
    raise SystemExit(f"no pid in {pid_file} after {timeout}s")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pid", type=int)
    p.add_argument("--pid-file")
    p.add_argument("--out", required=True)
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--wait", type=float, default=600.0, help="seconds to wait for the pid file")
    a = p.parse_args()
    pid = a.pid or _wait_for_pid(a.pid_file, a.wait)
    proc = psutil.Process(pid)
    peak = 0.0
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_epoch", "rss_mb", "host_free_mb", "cpu_percent"])
        proc.cpu_percent(None)
        while True:
            try:
                rss = proc.memory_info().rss / 1e6
                cpu = proc.cpu_percent(None)
            except psutil.NoSuchProcess:
                break
            free = psutil.virtual_memory().available / 1e6
            peak = max(peak, rss)
            w.writerow([f"{time.time():.1f}", f"{rss:.1f}", f"{free:.0f}", f"{cpu:.1f}"])
            fh.flush()
            time.sleep(a.interval)
    print(f"peak_rss_mb_external={peak:.1f}")
    sys.exit(0)


if __name__ == "__main__":
    main()
