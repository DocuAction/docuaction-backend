"""FHIR-RES-001..003 - FHIR resource validation. Needs the live registry."""

from dast.static_base import StaticTester

CAT = "fhir_resource"
NEEDS = "requires the TEFCA registry backend on a live target (routes 404 on dev)"
CASES = [
    ("FHIR-RES-001", "Organization resource validated on ingest"),
    ("FHIR-RES-002", "Bundle resource validated on ingest"),
    ("FHIR-RES-003", "Endpoint resource validated on ingest"),
]


class ResourceTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        for tid, nm in CASES:
            self.s.stub(tid, CAT, nm, NEEDS, owasp=['A04:2021'], cwe=['20'],
                        nist=['SI-10'], hipaa=['164.312(c)(1)'])
