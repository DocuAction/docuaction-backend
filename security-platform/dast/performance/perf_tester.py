"""PERF-001..010 - latency benchmarks against a non-production target.

Only the external read-latency baseline can be measured from outside the application.
Anything needing server-side instrumentation, host metrics, or write access is stubbed
with its reason rather than estimated - an invented number is worse than an absent one.
PERF-004 (concurrent load) is deliberately NOT run: hammering a shared dev environment
would violate the rate-limit safety rule this framework is built around.
"""

import statistics
from typing import Dict, List

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "performance"
ENDPOINTS = [
    ("/health", "health probe"),
    ("/api/v1/bulletin/health", "bulletin health"),
    ("/api/v1/bulletin/costs", "cost summary"),
    ("/api/v1/bulletin/latest/fcc", "latest briefing"),
    ("/api/v1/bulletin/history/fcc", "briefing history"),
]
SAMPLES = 4


class PerfTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    async def run(self) -> None:
        rows: List[Dict] = []
        for path, label in ENDPOINTS:
            lat = []
            for _ in range(SAMPLES):
                r = await self.t.request("GET", path)
                if r.status not in (0, 429):
                    lat.append(r.elapsed_ms)
            if not lat:
                continue
            lat.sort()
            rows.append({"endpoint": path, "label": label, "n": len(lat),
                         "p50": round(statistics.median(lat), 1),
                         "min": round(lat[0], 1), "max": round(lat[-1], 1)})

        if not rows:
            self.t.generate_evidence(
                "PERF-001", CAT, "API latency baseline", outcome=Outcome.SKIP,
                notes="No endpoint produced usable timings (all rate-limited or errored).")
        else:
            slow = [r for r in rows if r["p50"] > 2000]
            worst = max(rows, key=lambda r: r["p50"])
            self.t.generate_evidence(
                "PERF-001", CAT, "API latency baseline across key read endpoints",
                expected="p50 under 2000 ms",
                observed="; ".join(r["endpoint"] + " p50=" + str(r["p50"]) + "ms"
                                   for r in rows),
                outcome=Outcome.PASS if not slow else Outcome.WARN,
                finding="" if not slow else
                        str(len(slow)) + " endpoint(s) exceed a 2 s median, slowest "
                        + worst["endpoint"] + " at " + str(worst["p50"]) + " ms.",
                severity="low", confidence="low", owasp=["A04:2021"], nist=["SC-5"],
                request_summary={"samples_per_endpoint": SAMPLES, "measurements": rows},
                notes="Measured from outside over the public internet against a shared "
                      "dev App Service, with the scan self-rate-limited. Indicative "
                      "only - includes cold start and network time, and is not a "
                      "capacity benchmark.")

        for tid, nm, why in (
            ("PERF-002", "Database query performance",
             "requires server-side instrumentation (pg_stat_statements)"),
            ("PERF-003", "Bulk import at 100/500/1000 entities",
             "requires the TEFCA registry and write access"),
            ("PERF-004", "Concurrent users (5/10/25)",
             "deliberately not run - concurrent load against a shared dev environment "
             "conflicts with the rate-limit safety rule"),
            ("PERF-005", "Memory usage under load",
             "requires host metrics, not observable over HTTP"),
            ("PERF-006", "FHIR Bundle import at scale",
             "requires the TEFCA registry backend"),
            ("PERF-007", "Search performance with filters",
             "requires the TEFCA registry search endpoint"),
            ("PERF-008", "Dashboard loading performance",
             "requires an authenticated session"),
            ("PERF-009", "Report generation time",
             "requires an authenticated session"),
            ("PERF-010", "Long-running job timeout behaviour",
             "would require triggering a full bulletin cycle, which incurs LLM spend"),
        ):
            self.t.generate_evidence(tid, CAT, nm, outcome=Outcome.STUB,
                                     severity="info",
                                     notes="NOT EXECUTED - " + why + ".")
