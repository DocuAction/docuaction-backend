"""TEFCA-VER-001..006 - verification workflow."""

import re

from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "tefca_verification"
NEEDS = "requires the TEFCA registry backend on a live target (routes 404 on dev)"


class VerificationTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        conn = self.s.read("app/Tefca/connectors.py")
        eng = self.s.read("app/Tefca/validation_engine.py")

        nppes = bool(re.search(r"(?i)nppes|npiregistry", conn))
        self.s.record(
            "TEFCA-VER-002", CAT, "Verification consults NPPES for NPI validation",
            outcome=Outcome.PASS if nppes else Outcome.FAIL,
            expected="An NPPES / NPI-registry connector is implemented",
            observed=f"NPPES reference {'found' if nppes else 'NOT found'}",
            finding="" if nppes else
                    "No NPPES connector found; NPI values cannot be verified against "
                    "the authoritative source.",
            severity="high" if not nppes else "info",
            source="app/Tefca/connectors.py", owasp=["A04:2021"], cwe=["345"],
            nist=["SI-10"], hipaa=["164.312(c)(1)"],
            remediation="Verify every NPI against NPPES and record the response.")

        evidence_kept = bool(re.search(
            r"(?i)evidence|finding|result_json|raw_response", eng + conn))
        self.s.record(
            "TEFCA-VER-005", CAT, "Verification results retain evidence",
            outcome=Outcome.PASS if evidence_kept else Outcome.WARN,
            expected="The validation engine persists per-check evidence",
            observed=f"evidence retention {'found' if evidence_kept else 'not found'}",
            finding="" if evidence_kept else
                    "No evidence retention found; verification outcomes would not be "
                    "auditable after the fact.",
            severity="medium", source="app/Tefca/validation_engine.py",
            owasp=["A09:2021"], cwe=["778"], nist=["AU-3"], hipaa=["164.312(b)"],
            remediation="Persist the raw upstream response alongside each finding.")

        for tid, nm in (
            ("TEFCA-VER-001", "Verification requires contributor auth"),
            ("TEFCA-VER-003", "Verification creates immutable findings"),
            ("TEFCA-VER-004", "Re-verification is idempotent"),
            ("TEFCA-VER-006", "Unverified entities flagged appropriately"),
        ):
            self.s.stub(tid, CAT, nm, NEEDS, owasp=["A01:2021"], cwe=["862"],
                        nist=["AC-3"], hipaa=["164.312(a)(1)"])
