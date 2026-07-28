"""FHIR-BUN-001..008 - FHIR Bundle import security. Needs the live registry."""

from dast.static_base import StaticTester

CAT = "fhir_bundle"
NEEDS = "requires the TEFCA registry backend on a live target (routes 404 on dev)"
CASES = [
    ("FHIR-BUN-001", "Valid Bundle imported correctly"),
    ("FHIR-BUN-002", "Bundle with an invalid entry -> partial success"),
    ("FHIR-BUN-003", "Bundle with circular references detected"),
    ("FHIR-BUN-004", "Bundle with duplicate entries deduplicated"),
    ("FHIR-BUN-005", "Bundle ordering handled (two-pass import)"),
    ("FHIR-BUN-006", "Oversized Bundle (1000+ entries) handled"),
    ("FHIR-BUN-007", "Empty Bundle -> 422 not 500"),
    ("FHIR-BUN-008", "Bundle with mixed resource types handled"),
]


class BundleTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        for tid, nm in CASES:
            self.s.stub(tid, CAT, nm, NEEDS, owasp=['A04:2021'], cwe=['20'],
                        nist=['SI-10'], hipaa=['164.312(c)(1)'])
