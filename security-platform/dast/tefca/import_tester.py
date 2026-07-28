"""TEFCA-IMP-001..008 - import / export security."""

import re

from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "tefca_import"
NEEDS = "requires the TEFCA registry backend on a live target (routes 404 on dev)"


class ImportTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        fhir = self.s.read("app/tefca_registry/fhir_import.py")
        csv = self.s.read("app/tefca_registry/csv_import.py")
        blob = fhir + csv

        checksum = bool(re.search(r"(?i)sha256|sha_256|hashlib\.sha", blob))
        self.s.record(
            "TEFCA-IMP-005", CAT, "Import idempotency via SHA-256 checksum",
            outcome=Outcome.PASS if checksum else Outcome.WARN,
            expected="A SHA-256 checksum of the payload used to detect re-imports",
            observed=f"checksum logic {'found' if checksum else 'not found'}",
            finding="" if checksum else
                    "No payload checksum found; re-importing the same bundle could "
                    "duplicate entities instead of being recognised as a repeat.",
            severity="medium", source="app/tefca_registry/fhir_import.py",
            owasp=["A04:2021"], cwe=["694"], nist=["SI-10"],
            remediation="Hash the payload and short-circuit on a known checksum.")

        batch = bool(re.search(r"(?i)batch|import_run|import_id", blob))
        self.s.record(
            "TEFCA-IMP-006", CAT, "Imports tracked as an auditable batch",
            outcome=Outcome.PASS if batch else Outcome.WARN,
            expected="An import batch/run identifier persisted with the results",
            observed=f"batch tracking {'found' if batch else 'not found'}",
            finding="" if batch else
                    "No import batch identifier found; individual imports could not be "
                    "traced or rolled back.",
            severity="medium", source="app/tefca_registry/csv_import.py",
            owasp=["A09:2021"], cwe=["778"], nist=["AU-3"], hipaa=["164.312(b)"],
            remediation="Record an import batch row and link created entities to it.")

        for tid, nm in (
            ("TEFCA-IMP-001", "FHIR Bundle import requires reviewer+"),
            ("TEFCA-IMP-002", "CSV import requires reviewer+"),
            ("TEFCA-IMP-003", "Malformed FHIR Bundle -> 422 not 500"),
            ("TEFCA-IMP-004", "Oversized import handled gracefully"),
            ("TEFCA-IMP-007", "Export includes all entity fields"),
            ("TEFCA-IMP-008", "Export respects RBAC (viewer gets limited fields)"),
        ):
            self.s.stub(tid, CAT, nm, NEEDS, owasp=["A01:2021"], cwe=["862"],
                        nist=["AC-3"], hipaa=["164.312(a)(1)"])
