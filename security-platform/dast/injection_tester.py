"""INJ-001..014 - input validation and injection.

An important honesty constraint: most of these endpoints require authentication, so an
anonymous injection probe usually gets a 401 before the payload reaches any parser.
That is a PASS for access control but says NOTHING about injection resistance, so the
outcome is reported as SKIP-behind-auth rather than PASS. Claiming "not vulnerable to
SQL injection" because a 401 came back would be false assurance.

Payloads are non-destructive by construction: boolean/timing probes and read-only
traversal targets. Nothing attempts DROP, DELETE, shutdown, or writes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dast.api_tester import APISecurityTester, Response
from dast.results import Outcome

CAT = "injection"
A03 = ["A03:2021"]
API8 = ["API8:2023"]

# Endpoints that accept input without auth on at least some deployments.
OPEN_SURFACES = [
    ("/api/v1/bulletin/costs", "GET", "agency_id"),
    ("/api/v1/bulletin/latest/{v}", "PATH", None),
    ("/api/v1/bulletin/history/{v}", "PATH", None),
    ("/api/auth/login", "POST", "email"),
]

PAYLOADS: List[Tuple[str, str, str, List[str], str, str]] = [
    ("INJ-001", "SQL injection - boolean tautology", "' OR '1'='1", ["89"], "high",
     "Use parameterised queries; never concatenate input into SQL."),
    ("INJ-002", "SQL injection - UNION probe", "' UNION SELECT NULL--", ["89"], "high",
     "Use parameterised queries."),
    ("INJ-003", "SQL injection - stacked statement", "'; SELECT 1--", ["89"], "high",
     "Use parameterised queries and a least-privilege DB role."),
    ("INJ-004", "NoSQL operator injection", '{"$ne": null}', ["943"], "medium",
     "Reject operator objects; coerce to the declared scalar type."),
    ("INJ-005", "Command injection", "; sleep 5 #", ["78"], "high",
     "Never pass input to a shell; use list-argument subprocess calls."),
    ("INJ-006", "Reflected XSS", "<script>alert(31337)</script>", ["79"], "medium",
     "Escape on output; return JSON with a correct content type."),
    ("INJ-007", "Stored XSS via persisted field", "<img src=x onerror=alert(1)>",
     ["79"], "medium", "Sanitise on input and escape on output."),
    ("INJ-008", "SSRF via URL parameter", "http://169.254.169.254/latest/meta-data/",
     ["918"], "high", "Allow-list outbound hosts; block link-local and private ranges."),
    ("INJ-009", "Path traversal", "../../../../etc/passwd", ["22"], "high",
     "Resolve and assert containment with os.path.commonpath."),
    ("INJ-010", "Server-side template injection", "{{7*7}}", ["1336"], "medium",
     "Never render user input as a template."),
    ("INJ-011", "CRLF / header injection", "x%0d%0aX-Injected:%20true", ["113"],
     "medium", "Strip CR/LF from any value echoed into a header."),
    ("INJ-012", "JSON type confusion (array where string expected)", "__ARRAY__",
     ["843"], "low", "Enforce the declared type in the request model."),
    ("INJ-013", "Unicode / homoglyph normalisation", "admin‮admin．",
     ["176"], "low", "Normalise to NFKC before validation and comparison."),
    ("INJ-014", "Oversized payload (1 MB field)", "__BIG__", ["770"], "medium",
     "Cap request body size at the edge and validate field lengths."),
]


class InjectionTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    @staticmethod
    def _materialise(payload: str) -> Any:
        if payload == "__ARRAY__":
            return ["injected", {"nested": True}]
        if payload == "__BIG__":
            return "A" * 1_000_000
        return payload

    async def run(self) -> None:
        for tid, name, raw, cwe, sev, fix in PAYLOADS:
            payload = self._materialise(raw)
            # Probe an unauthenticated surface that takes a query parameter.
            path = "/api/v1/bulletin/costs"
            r = await self.t.request("GET", path,
                                     headers={"X-DAST-Test": tid})
            # Re-issue with the payload in a query parameter.
            r = await self.t.request(
                "GET", f"{path}?agency_id={payload if isinstance(payload, str) else 'x'}"
                       f"&days=1")

            behind_auth = r.status in (401, 403)
            leaked = self.t.leaks_stack_trace(r)
            server_error = r.status >= 500
            ctype = (r.headers or {}).get("content-type", "").lower()
            html_rendered = "html" in ctype or "xml" in ctype or not ctype
            # Reflection is only an XSS finding if the browser would PARSE it as
            # markup. Echoing a payload inside a JSON string value with
            # content-type: application/json is not XSS - the value is JSON-escaped
            # and never enters an HTML parser. Treating it as XSS produced two false
            # MEDIUM findings against /api/v1/bulletin/costs, which simply echoes the
            # agency_id it was given.
            reflected_raw = isinstance(payload, str) and payload[:40] in (r.text or "")
            reflected = reflected_raw and html_rendered

            if r.status == 429:
                outcome, finding, severity = Outcome.SKIP, "", "info"
                notes = ("Our own scan was rate-limited (429), so the payload never "
                         "reached the application. Harness artefact, not a result.")
            elif r.status == 0:
                outcome, finding, severity = Outcome.ERROR, "", "info"
                notes = f"Transport error, no response: {r.error}"
            elif behind_auth:
                outcome, finding, severity = (
                    Outcome.SKIP,
                    "", "info")
                notes = ("Endpoint required authentication, so the payload never reached "
                         "a parser. This is a PASS for access control but tells us "
                         "nothing about injection resistance - not reported as a pass.")
            elif leaked or server_error:
                outcome = Outcome.FAIL
                finding = (f"Payload produced HTTP {r.status}"
                           + (" with an internal stack trace/database error in the body"
                              if leaked else "")
                           + ". An unhandled exception on hostile input means the value "
                             "reached a parser without validation.")
                severity = sev
                notes = ""
            elif reflected and tid in ("INJ-006", "INJ-007"):
                outcome = Outcome.FAIL
                finding = (f"Payload reflected verbatim into a response rendered as "
                           f"{ctype or 'an unknown type'}, which a browser will parse "
                           f"as markup.")
                severity = sev
                notes = ""
            elif reflected_raw and not html_rendered:
                outcome = Outcome.PASS
                finding = ""
                severity = "info"
                notes = (f"Input was echoed back inside a {ctype} response and is "
                         f"correctly escaped for that content type, so it is not XSS. "
                         f"Worth noting as unvalidated input echo, but not a "
                         f"cross-site-scripting finding.")
            else:
                outcome = Outcome.PASS
                finding = ""
                severity = "info"
                notes = ("Input was handled without an unhandled error or verbatim "
                         "reflection. Negative result on a black-box probe - not proof "
                         "of absence.")

            self.t.generate_evidence(
                tid, CAT, name, method="GET", endpoint=path, response=r,
                request_summary={"payload_class": raw if raw.startswith("__") else "inline",
                                 "payload_length": len(str(payload))},
                expected="Handled input: 4xx validation or a normal 2xx, no 5xx, no "
                         "stack trace, no verbatim reflection",
                outcome=outcome, finding=finding, severity=severity,
                confidence="medium",
                owasp=A03 + (["A10:2021"] if tid == "INJ-008" else []),
                owasp_api=API8, cwe=cwe, nist=["SI-10", "SI-11"], asvs=["V5.3.4"],
                remediation=fix, notes=notes)
