"""FHIR-SMART-001..004 - SMART on FHIR authorisation.

SMART on FHIR is not implemented in this codebase: there is no OAuth2 authorisation
server, no scope handling and no launch context. These are recorded as NOT APPLICABLE
rather than as failures - an unimplemented capability is a scope statement, not a
defect. Reporting them as failures would inflate the finding count with work nobody
has decided to do.
"""

from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "fhir_smart"


class SmartTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        blob = (self.s.read("app/api/azure_auth_routes.py")
                + self.s.read("app/core/security.py"))
        implemented = "launch/patient" in blob or "smart-configuration" in blob
        self.s.record(
            "FHIR-SMART-001", CAT, "SMART on FHIR authorisation implemented",
            outcome=Outcome.PASS if implemented else Outcome.SKIP,
            expected="SMART scopes / launch context / .well-known/smart-configuration",
            observed="SMART implementation " + ("found" if implemented else "not found"),
            severity="info", source="app/core/security.py",
            owasp=["A01:2021"], cwe=["862"], nist=["AC-3"],
            notes="SMART on FHIR is not implemented. Recorded as NOT APPLICABLE, not a "
                  "failure: the platform uses its own JWT auth and does not expose a "
                  "FHIR API to third-party apps.")
        for tid, nm in (("FHIR-SMART-002", "SMART scopes enforced per resource"),
                        ("FHIR-SMART-003", "Launch context bound to the token"),
                        ("FHIR-SMART-004", "Refresh token rotation for SMART apps")):
            self.s.stub(tid, CAT, nm,
                        "SMART on FHIR is not implemented in this application",
                        owasp=["A01:2021"], cwe=["862"], nist=["AC-3"])
