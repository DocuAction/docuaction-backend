"""CORS-001..006 - cross-origin policy.

The dangerous combination is `Access-Control-Allow-Origin` reflecting an arbitrary
origin together with `Allow-Credentials: true` - that lets any site read authenticated
responses. A wildcard alone is less severe because browsers refuse to send credentials
with it, so the two are graded differently rather than lumped together.
"""

from __future__ import annotations

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "cors"
A05 = ["A05:2021"]
EVIL = "https://attacker.example.com"


class CorsTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    async def run(self) -> None:
        probe = "/api/v1/bulletin/health"

        r = await self.t.request("GET", probe, headers={"Origin": EVIL})
        h = r.headers or {}
        acao = h.get("access-control-allow-origin", "")
        acac = h.get("access-control-allow-credentials", "").lower() == "true"
        reflected = acao.strip().lower() == EVIL.lower()
        wildcard = acao.strip() == "*"

        self.t.generate_evidence(
            "CORS-001", CAT, "Arbitrary origin is not reflected",
            method="GET", endpoint=probe, response=r,
            request_summary={"origin": EVIL},
            expected="Access-Control-Allow-Origin absent, or an allow-listed origin only",
            observed=f"ACAO: {acao or '(absent)'}; Allow-Credentials: {acac}",
            outcome=Outcome.FAIL if reflected else Outcome.PASS,
            finding=("The server reflects an arbitrary Origin"
                     + (" WITH Allow-Credentials: true, so any website can read "
                        "authenticated responses on behalf of a logged-in user."
                        if acac else ", though without credentials.")
                     ) if reflected else "",
            severity=("critical" if (reflected and acac) else
                      ("high" if reflected else "info")),
            owasp=A05, owasp_api=["API8:2023"], cwe=["942", "346"],
            nist=["AC-4", "SC-7"], asvs=["V14.5.3"],
            remediation="Validate Origin against a static allow-list and echo only "
                        "matching values. Never reflect the request Origin.")

        self.t.generate_evidence(
            "CORS-002", CAT, "No wildcard ACAO with credentials",
            method="GET", endpoint=probe, response=r,
            expected="Not both ACAO:* and Allow-Credentials:true",
            observed=f"ACAO: {acao or '(absent)'}; credentials: {acac}",
            outcome=Outcome.FAIL if (wildcard and acac) else Outcome.PASS,
            finding="Wildcard origin combined with Allow-Credentials is an invalid and "
                    "dangerous configuration." if (wildcard and acac) else "",
            severity="high" if (wildcard and acac) else "info",
            owasp=A05, cwe=["942"], nist=["AC-4"], asvs=["V14.5.3"],
            remediation="Never combine ACAO:* with Allow-Credentials:true.")

        rn = await self.t.request("GET", probe, headers={"Origin": "null"})
        acao_n = (rn.headers or {}).get("access-control-allow-origin", "")
        null_ok = acao_n.strip().lower() == "null"
        self.t.generate_evidence(
            "CORS-003", CAT, 'Null origin not allowed',
            method="GET", endpoint=probe, response=rn,
            request_summary={"origin": "null"},
            expected="'null' origin not echoed",
            observed=f"ACAO: {acao_n or '(absent)'}",
            outcome=Outcome.FAIL if null_ok else Outcome.PASS,
            finding="The 'null' origin is allowed; sandboxed iframes and local files "
                    "present this origin and would gain access." if null_ok else "",
            severity="medium" if null_ok else "info",
            owasp=A05, cwe=["942"], nist=["AC-4"],
            remediation="Reject the literal 'null' origin.")

        pre = await self.t.request("OPTIONS", probe, headers={
            "Origin": EVIL, "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization,x-custom"})
        ph = pre.headers or {}
        methods = ph.get("access-control-allow-methods", "")
        allows_delete = "delete" in methods.lower() or methods.strip() == "*"
        self.t.generate_evidence(
            "CORS-004", CAT, "Preflight does not grant unsafe methods to unknown origins",
            method="OPTIONS", endpoint=probe, response=pre,
            request_summary={"origin": EVIL, "requested_method": "DELETE"},
            expected="Preflight refused, or DELETE not granted to an unknown origin",
            observed=f"HTTP {pre.status}; Allow-Methods: {methods or '(absent)'}",
            outcome=Outcome.FAIL if (allows_delete and
                                     ph.get("access-control-allow-origin", "").lower()
                                     == EVIL.lower()) else Outcome.PASS,
            finding="Preflight grants DELETE to an arbitrary origin."
                    if allows_delete and ph.get("access-control-allow-origin",
                                                "").lower() == EVIL.lower() else "",
            severity="high", owasp=A05, cwe=["942"], nist=["AC-4"],
            remediation="Restrict Allow-Methods to what the app needs and only for "
                        "allow-listed origins.")

        allow_headers = ph.get("access-control-allow-headers", "")
        self.t.generate_evidence(
            "CORS-005", CAT, "Allow-Headers is not an unrestricted wildcard",
            method="OPTIONS", endpoint=probe, response=pre,
            expected="Explicit header list rather than '*'",
            observed=f"Allow-Headers: {allow_headers or '(absent)'}",
            outcome=Outcome.WARN if allow_headers.strip() == "*" else Outcome.PASS,
            finding="Allow-Headers is '*'." if allow_headers.strip() == "*" else "",
            severity="low", owasp=A05, cwe=["942"], nist=["AC-4"],
            remediation="Enumerate the headers the API actually accepts.")

        self.t.generate_evidence(
            "CORS-006", CAT, "CORS policy is present and deliberate",
            method="GET", endpoint=probe, response=r,
            expected="A defined CORS posture (either no CORS, or an allow-list)",
            observed=f"ACAO: {acao or '(absent)'}",
            outcome=Outcome.PASS,
            severity="info", owasp=A05, nist=["AC-4"],
            notes="Recorded for evidence. TrustedHostMiddleware plus strict CORS was "
                  "added in an earlier hardening pass; this documents the observed "
                  "state at scan time.")
