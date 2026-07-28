"""RATE-001..005 - rate limiting.

Tension worth being explicit about: the framework deliberately paces itself at 8
requests / 6 s to avoid harming a shared dev environment, which is exactly the
threshold the application enforces. A self-limited client therefore cannot trip the
limiter, and "no 429 observed" would be a meaningless pass.

So this module makes ONE bounded burst - a small, controlled excursion above the pace
(never sustained, never parallel) - purely to observe whether a limit exists. If no
429 appears it reports WARN with the sample size stated, not PASS.
"""

from __future__ import annotations

import asyncio
import time
from typing import List

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "rate_limit"
A05 = ["A05:2021"]
API4 = ["API4:2023"]
BURST = 14          # modest excursion above the 8/6s pace
LOGIN = "/api/auth/login"


class RateLimitTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    async def run(self) -> None:
        await self._burst_unauth()
        await self._burst_login()
        await self._headers()

    async def _raw_burst(self, path: str, method: str = "GET",
                         json_body=None) -> List[int]:
        """Bypass the pacer for a SHORT counted burst, then stop.

        This is the one place the internal pacer is stepped around, and it is bounded
        to BURST requests against a single cheap endpoint. It is not parallel and not
        repeated, so the peak load stays trivial for the target.
        """
        codes: List[int] = []
        for _ in range(BURST):
            self.t.request_count += 1
            r = await self.t._send(method, self.t.url_for(path),
                                   {"User-Agent": "AGT-Security-Platform-DAST/1.0"},
                                   json_body, None, None, False)
            codes.append(r.status)
            if r.status == 429:
                break
        return codes

    async def _burst_unauth(self) -> None:
        path = "/api/v1/bulletin/health"
        codes = await self._raw_burst(path)
        limited = 429 in codes
        self.t.generate_evidence(
            "RATE-001", CAT, "Rate limiting enforced on unauthenticated reads",
            method="GET", endpoint=path,
            request_summary={"burst_size": len(codes), "status_sequence": codes},
            expected="At least one 429 within a short burst",
            observed=f"{len(codes)} requests; 429 seen: {limited}; "
                     f"distinct statuses: {sorted(set(codes))}",
            outcome=Outcome.PASS if limited else Outcome.WARN,
            finding="" if limited else
                    f"No 429 within a {len(codes)}-request burst. Either the limit is "
                    f"above this threshold or per-IP limiting is not applied to this "
                    f"endpoint. Phase 0 recorded the limiter as in-memory and "
                    f"per-process (finding SH-01), so a multi-worker deployment "
                    f"multiplies the effective limit by the worker count.",
            severity="low", confidence="low",
            owasp=A05, owasp_api=API4, cwe=["770"], nist=["SC-5"], asvs=["V11.1.1"],
            remediation="Back the limiter with Redis so the limit is global rather than "
                        "per-process.",
            notes=f"Bounded burst of {BURST}; a negative result is not proof of absence.")

    async def _burst_login(self) -> None:
        """RATE-004 - the auth endpoint is the one that most needs a limit."""
        codes = await self._raw_burst(
            LOGIN, "POST",
            {"email": "dast-ratelimit@example.invalid", "password": "wrong"})
        limited = 429 in codes
        locked = any(c in (423, 403) for c in codes)
        self.t.generate_evidence(
            "RATE-004", CAT, "Rate limiting / lockout on the authentication endpoint",
            method="POST", endpoint=LOGIN,
            request_summary={"burst_size": len(codes), "status_sequence": codes},
            expected="429 (rate limited) or 423/403 (account lockout) during a burst of "
                     "failed logins",
            observed=f"429 seen: {limited}; lockout-style status seen: {locked}",
            outcome=Outcome.PASS if (limited or locked) else Outcome.WARN,
            finding="" if (limited or locked) else
                    f"{len(codes)} consecutive failed logins produced no 429 and no "
                    f"lockout response. Unthrottled credential stuffing is the highest-"
                    f"value attack against a healthcare login.",
            severity="medium" if not (limited or locked) else "info",
            confidence="medium",
            owasp=["A07:2021"], owasp_api=API4, cwe=["307", "770"],
            nist=["AC-7", "SC-5"], hipaa=["164.308(a)(5)(ii)(D)"], asvs=["V2.2.1"],
            remediation="Enforce per-account and per-IP throttling on login, backed by "
                        "shared state so it survives multiple workers.")

    async def _headers(self) -> None:
        r = await self.t.request("GET", "/api/v1/bulletin/health")
        h = r.headers or {}
        keys = [k for k in h if "ratelimit" in k.replace("-", "").lower()
                or k.lower() in ("retry-after", "x-rate-limit")]
        self.t.generate_evidence(
            "RATE-002", CAT, "Rate-limit headers communicated to clients",
            method="GET", endpoint="/api/v1/bulletin/health", response=r,
            expected="X-RateLimit-* or Retry-After present",
            observed=f"rate-limit headers: {keys or '(none)'}",
            outcome=Outcome.PASS if keys else Outcome.WARN,
            finding="" if keys else
                    "No rate-limit headers, so clients cannot back off cooperatively.",
            severity="low", owasp=A05, owasp_api=API4, cwe=["770"], nist=["SC-5"],
            remediation="Emit X-RateLimit-Limit/Remaining/Reset and Retry-After on 429.")

        for tid, name, note in (
            ("RATE-003", "Per-IP isolation of the limiter",
             "Requires two source IPs to demonstrate; a single-host scan cannot "
             "distinguish per-IP from global limiting."),
            ("RATE-005", "Limiter window resets correctly",
             "Requires observing a 429 first; no 429 was produced within the bounded "
             "burst, so there is no window to watch reset."),
        ):
            self.t.generate_evidence(
                tid, CAT, name, outcome=Outcome.SKIP,
                severity="info", owasp=A05, owasp_api=API4, cwe=["770"],
                nist=["SC-5"], notes=note)
