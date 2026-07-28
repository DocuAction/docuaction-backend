"""TEFCA-AUD-001..006 - audit trail completeness.

Audit behaviour is largely a code property, so most of these are checkable statically
- and they connect directly to Phase 0 finding AUDIT-MUT.
"""

import re

from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "tefca_audit"
NEEDS = "requires the TEFCA registry backend on a live target (routes 404 on dev)"
HIPAA_AUD = ["164.312(b)", "164.312(c)(1)"]


class AuditTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        audit = self.s.read("app/services/audit.py")
        models = self.s.read("app/models/database.py")
        compliance = self.s.read("app/api/compliance.py")
        admin = self.s.read("app/api/admin_users.py")

        fields = [f for f in ("user_id", "action", "resource", "ip_address",
                              "created_at", "timestamp", "outcome", "status")
                  if re.search(r"\b" + f + r"\b", audit + models)]
        enough = ({"user_id", "action"} <= set(fields)
                  and any(f in fields for f in ("created_at", "timestamp")))
        self.s.record(
            "TEFCA-AUD-002", CAT,
            "Audit records carry who / what / when / where / outcome",
            outcome=Outcome.PASS if enough else Outcome.WARN,
            expected="user, action, timestamp, source address and outcome persisted",
            observed=f"fields present: {fields}",
            finding="" if enough else
                    "The audit record is missing one or more required attributes, so "
                    "entries may not satisfy HIPAA 164.312(b).",
            severity="medium", source="app/services/audit.py",
            owasp=["A09:2021"], cwe=["778"], nist=["AU-3", "AU-12"], hipaa=HIPAA_AUD,
            remediation="Record actor, action, target, timestamp, source IP and outcome "
                        "on every audit row.")

        deletes = re.findall(
            r"(?i)delete\(\s*audit|DELETE FROM audit|audit_logs?\)\.delete",
            compliance + admin + models)
        pseudonymise = bool(re.search(r"(?i)_redact_personal_data|pseudonym", compliance))
        self.s.record(
            "TEFCA-AUD-003", CAT, "Audit entries are not deleted (immutability)",
            outcome=Outcome.PASS if (not deletes or pseudonymise) else Outcome.FAIL,
            expected="No DELETE against audit tables; GDPR handled by pseudonymisation",
            observed=f"{len(deletes)} delete-shaped reference(s); pseudonymisation "
                     f"{'present' if pseudonymise else 'absent'}",
            finding="" if (not deletes or pseudonymise) else
                    "Audit rows appear deletable, which destroys attribution and "
                    "breaches the six-year retention expectation.",
            severity="high" if (deletes and not pseudonymise) else "info",
            source="app/api/compliance.py", owasp=["A08:2021"], cwe=["778"],
            nist=["AU-9"], hipaa=HIPAA_AUD,
            remediation="Retain the row and redact personal fields instead of deleting, "
                        "per HIPAA 164.316(b)(2) and GDPR Art.17(3)(b).",
            notes="Phase 0 finding AUDIT-MUT covers this; the app-layer fix landed in "
                  "Sprint 1. A hash chain for tamper DETECTION remains open.")

        chained = bool(re.search(r"(?i)prev_hash|hash_chain|previous_hash|chain_hash",
                                 audit + models))
        self.s.record(
            "TEFCA-AUD-003b", CAT, "Audit log has tamper-evidence (hash chain)",
            outcome=Outcome.PASS if chained else Outcome.FAIL,
            expected="Each audit row links to the previous row's hash",
            observed=f"hash chain {'found' if chained else 'NOT found'}",
            finding="" if chained else
                    "No hash chain. Application-layer immutability stops the app from "
                    "deleting rows, but anyone with direct database access can still "
                    "alter history undetectably.",
            severity="medium", source="app/services/audit.py",
            owasp=["A08:2021"], cwe=["778", "345"], nist=["AU-9"], hipaa=HIPAA_AUD,
            remediation="Store prev_hash per row and verify the chain periodically.")

        raw_phi = bool(re.search(
            r"(?i)audit[^\n]*(ssn|date_of_birth|\bdob\b|mrn|patient_name|diagnosis)",
            audit))
        self.s.record(
            "TEFCA-AUD-006", CAT, "Audit entries do not contain raw PHI",
            outcome=Outcome.PASS if not raw_phi else Outcome.FAIL,
            expected="No direct PHI identifiers written into audit detail",
            observed=f"PHI-shaped field near audit writes: {raw_phi}",
            finding="" if not raw_phi else
                    "Audit writes reference PHI identifiers directly; audit logs are "
                    "widely readable and long-retained, so this widens PHI exposure.",
            severity="high" if raw_phi else "info",
            source="app/services/audit.py", owasp=["A09:2021"], cwe=["532"],
            nist=["AU-3", "AU-9"], hipaa=["164.312(b)", "164.514(b)(2)"],
            remediation="Log opaque internal identifiers, never the identifier itself.")

        for tid, nm in (
            ("TEFCA-AUD-001", "Every entity CRUD creates an audit entry"),
            ("TEFCA-AUD-004", "Audit entries survive entity deletion"),
            ("TEFCA-AUD-005", "Audit trail queryable by entity / user / date range"),
        ):
            self.s.stub(tid, CAT, nm, NEEDS, owasp=["A09:2021"], cwe=["778"],
                        nist=["AU-2", "AU-12"], hipaa=HIPAA_AUD)
