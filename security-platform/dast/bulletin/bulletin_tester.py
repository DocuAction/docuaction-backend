"""BULL-001..004 - Bulletin API security. Executes against the dev target."""

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "bulletin"


class BulletinTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    async def run(self) -> None:
        r = await self.t.request("GET", "/api/v1/bulletin/run/fcc/preview")
        blocked = r.status in (401, 403)
        inconclusive = r.status in (0, 429)
        self.t.generate_evidence(
            "BULL-001", CAT, "Bulletin run preview requires authentication",
            method="GET", endpoint="/api/v1/bulletin/run/fcc/preview", response=r,
            expected="401/403 - this endpoint triggers a full collection cycle",
            outcome=Outcome.PASS if blocked else
                    (Outcome.SKIP if inconclusive else Outcome.FAIL),
            finding="" if blocked or inconclusive else
                    "The preview endpoint served an unauthenticated caller (HTTP "
                    + str(r.status) + "). It runs a full bulletin cycle, so this is an "
                    "unauthenticated cost-amplification vector against the LLM budget.",
            severity="high" if r.ok else "info",
            owasp=["A01:2021"], owasp_api=["API4:2023"], cwe=["306", "770"],
            nist=["AC-3", "SC-5"],
            remediation="Gate behind guard('contributor') and ensure "
                        "BULLETIN_AUTH_ENABLED is set in every environment.",
            notes=("Inconclusive - no response received." if inconclusive else
                   ("BULLETIN_AUTH_ENABLED is unset on dev, so guard() returns an empty "
                    "dependency list there. This result reflects dev CONFIG, not the "
                    "presence or absence of the code-level guard."
                    if not blocked else "")))

        for tid, path, name in (
            ("BULL-002", "/api/v1/bulletin/costs", "Cost endpoint exposes no secrets"),
            ("BULL-003", "/api/v1/bulletin/health", "Health endpoint exposes no secrets"),
            ("BULL-004", "/api/v1/bulletin/latest/fcc", "Latest briefing metadata only"),
        ):
            r = await self.t.request("GET", path)
            body = (r.text or "").lower()
            leaks = [k for k in ("api_key", "apikey", "secret", "password",
                                 "connection_string", "postgres://") if k in body]
            self.t.generate_evidence(
                tid, CAT, name, method="GET", endpoint=path, response=r,
                expected="No credential-shaped keys in the response body",
                observed="HTTP " + str(r.status) + "; suspicious keys: "
                         + (str(leaks) if leaks else "none"),
                outcome=Outcome.FAIL if leaks else Outcome.PASS,
                finding=("Response exposes credential-shaped fields: " + str(leaks))
                        if leaks else "",
                severity="high" if leaks else "info",
                owasp=["A01:2021"], cwe=["200"], nist=["SC-28"],
                remediation="Never serialise configuration or credentials into API "
                            "responses.")
