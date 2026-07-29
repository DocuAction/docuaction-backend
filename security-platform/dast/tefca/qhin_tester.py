"""TEFCA-QHIN-001..005 - QHIN management. All require the live registry."""

from dast.static_base import StaticTester

CAT = "tefca_qhin"
NEEDS = "requires the TEFCA registry backend on a live target (routes 404 on dev)"
CASES = [
    ("TEFCA-QHIN-001", "QHIN creation requires admin"),
    ("TEFCA-QHIN-002", "QHIN designation date validated"),
    ("TEFCA-QHIN-003", "QHIN with participants cannot be deleted"),
    ("TEFCA-QHIN-004", "QHIN status transitions enforced"),
    ("TEFCA-QHIN-005", "Only designated QHINs can have participants"),
]


class QhinTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        for tid, nm in CASES:
            self.s.stub(tid, CAT, nm, NEEDS, owasp=["A01:2021"], cwe=["862"],
                        nist=["AC-3"], hipaa=["164.312(a)(1)"])
