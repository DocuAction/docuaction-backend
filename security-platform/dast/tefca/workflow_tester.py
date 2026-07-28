"""TEFCA-WF-001..007 - workflow state transitions.

The state MACHINE is a property of the code, so its shape is checked statically:
which states exist, whether transitions are validated against an allowed map, and
whether writes run inside a transaction. Whether the running service actually
enforces them needs the live registry and is stubbed.
"""

import re

from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "tefca_workflow"
NEEDS = "requires the TEFCA registry backend on a live target (routes 404 on dev)"
SRC = ["app/tefca_registry/models.py", "app/tefca_registry/routes.py",
       "app/tefca_registry/queries.py", "app/platform_config/models.py"]
STATES = ("draft", "pending", "verified", "active", "rejected", "inactive")


class WorkflowTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        blob = "\n".join(self.s.read(p) for p in SRC)
        found = sorted({s for s in STATES
                        if re.search(r"['\"]" + s + r"['\"]", blob, re.I)})
        self.s.record(
            "TEFCA-WF-001", CAT, "Entity lifecycle states are defined in code",
            outcome=Outcome.PASS if len(found) >= 3 else Outcome.WARN,
            expected="draft / pending / verified / active present as declared states",
            observed=f"states found: {found or 'none'}",
            finding="" if len(found) >= 3 else
                    "Fewer than three lifecycle states are declared; the TEFCA review "
                    "workflow may not be modelled explicitly.",
            severity="medium", source=",".join(SRC), owasp=["A04:2021"],
            cwe=["840"], nist=["AC-3", "SI-10"],
            remediation="Declare the lifecycle as an enum and validate every transition "
                        "against an explicit allowed-transition map.")

        guarded = bool(re.search(
            r"(?i)allowed_transitions|VALID_TRANSITIONS|can_transition|_transition_map",
            blob))
        self.s.record(
            "TEFCA-WF-002", CAT,
            "Transitions validated against an explicit map (cannot skip states)",
            outcome=Outcome.PASS if guarded else Outcome.FAIL,
            expected="An allowed-transition table consulted before a status change",
            observed=f"transition map {'found' if guarded else 'NOT found'}",
            finding="" if guarded else
                    "No allowed-transition map found, so a status field can most likely "
                    "be set directly to any value - draft could jump straight to active, "
                    "bypassing review. In TEFCA terms an unverified entity could be "
                    "marked active.",
            severity="high" if not guarded else "info",
            source=",".join(SRC), owasp=["A04:2021"], owasp_api=["API5:2023"],
            cwe=["840", "863"], nist=["AC-3", "SI-10"], hipaa=["164.312(a)(1)"],
            remediation="Centralise transitions: assert (from, to) is in an explicit "
                        "allow-list before persisting a status change.")

        tx = bool(re.search(r"(?i)begin\(\)|\.commit\(\)|transaction\(", blob))
        self.s.record(
            "TEFCA-WF-007", CAT, "State changes run inside a transaction",
            outcome=Outcome.PASS if tx else Outcome.WARN,
            expected="Explicit transaction/commit around multi-step writes",
            observed=f"transaction usage {'found' if tx else 'not found'}",
            finding="" if tx else
                    "No explicit transaction boundary found around state changes; a "
                    "partial failure could leave the entity and its audit row "
                    "inconsistent.",
            severity="medium", source=",".join(SRC), owasp=["A04:2021"],
            cwe=["662"], nist=["SI-10"],
            remediation="Wrap the status change and its audit write in one transaction.")

        for tid, nm in (
            ("TEFCA-WF-003", "Only reviewer can approve (pending -> verified)"),
            ("TEFCA-WF-004", "Rejected entity can be resubmitted"),
            ("TEFCA-WF-005", "Every state change creates an audit log entry"),
            ("TEFCA-WF-006", "Concurrent state changes handled (no race)"),
        ):
            self.s.stub(tid, CAT, nm, NEEDS, owasp=["A01:2021"], cwe=["862", "362"],
                        nist=["AC-3", "AU-2"], hipaa=["164.312(b)"])
