"""HDR-001..008 - security response headers."""

from __future__ import annotations

from typing import List, Optional, Tuple

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "headers"
A05 = ["A05:2021"]


class HeaderTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    async def run(self) -> None:
        r = await self.t.request("GET", "/health")
        h = r.headers or {}

        checks: List[Tuple[str, str, str, str, str, List[str], List[str], str]] = [
            ("HDR-001", "Strict-Transport-Security present",
             "strict-transport-security", "max-age",
             "high" if str(self.t.base_url).startswith("https") else "low",
             ["319"], ["SC-8", "SC-13"],
             "Add Strict-Transport-Security: max-age=31536000; includeSubDomains."),
            ("HDR-002", "X-Content-Type-Options: nosniff",
             "x-content-type-options", "nosniff", "medium", ["430"], ["SC-18"],
             "Add X-Content-Type-Options: nosniff."),
            ("HDR-003", "X-Frame-Options or CSP frame-ancestors",
             "x-frame-options", "", "medium", ["1021"], ["SC-18"],
             "Add X-Frame-Options: DENY or CSP frame-ancestors 'none'."),
            ("HDR-004", "Content-Security-Policy present",
             "content-security-policy", "", "medium", ["1021", "79"], ["SC-18"],
             "Add a restrictive Content-Security-Policy."),
            ("HDR-005", "Referrer-Policy present",
             "referrer-policy", "", "low", ["200"], ["SC-8"],
             "Add Referrer-Policy: strict-origin-when-cross-origin."),
        ]
        for tid, name, header, needle, sev, cwe, nist, fix in checks:
            val = h.get(header, "")
            present = bool(val) and (needle.lower() in val.lower() if needle else True)
            # X-Frame-Options may legitimately be replaced by CSP frame-ancestors.
            if tid == "HDR-003" and not present:
                present = "frame-ancestors" in h.get("content-security-policy", "").lower()
            self.t.generate_evidence(
                tid, CAT, name, method="GET", endpoint="/health", response=r,
                expected=f"{header} present" + (f" containing '{needle}'" if needle else ""),
                observed=f"{header}: {val[:120] or '(absent)'}",
                outcome=Outcome.PASS if present else Outcome.FAIL,
                finding="" if present else f"Response header '{header}' is absent or "
                                           f"does not meet the expected value.",
                severity=sev if not present else "info",
                owasp=A05, cwe=cwe, nist=nist, asvs=["V14.4.1"], remediation=fix)

        # HDR-006 server/version disclosure
        banner = " ".join(filter(None, [h.get("server", ""), h.get("x-powered-by", "")]))
        discloses = any(tok in banner.lower() for tok in
                        ("uvicorn", "gunicorn", "python", "werkzeug", "express", "/"))
        self.t.generate_evidence(
            "HDR-006", CAT, "No server/framework version disclosure",
            method="GET", endpoint="/health", response=r,
            expected="No Server/X-Powered-By revealing product or version",
            observed=f"Server/X-Powered-By: {banner or '(absent)'}",
            outcome=Outcome.WARN if discloses else Outcome.PASS,
            finding=f"Response advertises the server stack ({banner.strip()}), which "
                    f"helps an attacker target known CVEs." if discloses else "",
            severity="low", owasp=A05, cwe=["200"], nist=["SC-30"],
            remediation="Strip or generalise Server and X-Powered-By at the edge.")

        # HDR-007 cache control on an authenticated surface
        r2 = await self.t.request("GET", "/api/v1/tefca/registry/entities")
        cc = (r2.headers or {}).get("cache-control", "")
        safe_cache = any(k in cc.lower() for k in ("no-store", "no-cache", "private"))
        self.t.generate_evidence(
            "HDR-007", CAT, "Sensitive responses are not cacheable",
            method="GET", endpoint="/api/v1/tefca/registry/entities", response=r2,
            expected="Cache-Control containing no-store / no-cache / private",
            observed=f"Cache-Control: {cc or '(absent)'} (HTTP {r2.status})",
            outcome=Outcome.PASS if safe_cache else
                    (Outcome.SKIP if r2.status in (401, 403, 404) else Outcome.WARN),
            finding="" if safe_cache or r2.status in (401, 403, 404) else
                    "No cache directive on a PHI-bearing surface; intermediaries and "
                    "browsers may retain the response.",
            severity="medium", owasp=A05, cwe=["525"], nist=["SC-28"],
            hipaa=["164.312(e)(1)"],
            remediation="Set Cache-Control: no-store on authenticated responses.",
            notes="Endpoint refused anonymously; header policy on the authenticated "
                  "response is untested." if r2.status in (401, 403) else "")

        # HDR-008 no stack traces on error
        r3 = await self.t.request("GET", "/api/v1/definitely-not-a-real-route-dast")
        leaked = self.t.leaks_stack_trace(r3)
        self.t.generate_evidence(
            "HDR-008", CAT, "Error responses contain no stack traces or internals",
            method="GET", endpoint="/api/v1/definitely-not-a-real-route-dast",
            response=r3,
            expected="Generic 404/4xx body with no traceback, file paths or driver names",
            outcome=Outcome.FAIL if leaked else Outcome.PASS,
            finding="Error response exposes internal details (traceback, file paths or "
                    "database driver names)." if leaked else "",
            severity="medium" if leaked else "info",
            owasp=["A09:2021"], cwe=["209"], nist=["SI-11"], asvs=["V7.4.1"],
            remediation="Return generic error bodies; log detail server-side only.")
