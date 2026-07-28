"""TEFCA-ENT-001..015 - entity registry security.

The registry HTTP surface is not deployed to dev, so the RBAC and runtime-behaviour
tests are emitted as STUB. The properties that live in the SCHEMA and the ROUTE
DEFINITIONS - unique constraints, mandatory identifiers, auth dependencies, hierarchy
rules - are checked statically against app/tefca_registry/ instead.
"""

from __future__ import annotations

import re
from typing import List

from dast.results import Outcome
from dast.static_base import StaticTester

CAT = "tefca_entity"
A01 = ["A01:2021"]
HIPAA_AC = ["164.312(a)(1)", "164.312(d)"]
NEEDS_LIVE = "requires the TEFCA registry backend on a live target (routes 404 on dev)"

ROUTES = "app/tefca_registry/routes.py"
MODELS = "app/tefca_registry/models.py"
SCHEMAS = "app/tefca_registry/schemas.py"


class EntityTester:
    def __init__(self, st: StaticTester):
        self.s = st

    def run(self) -> None:
        routes = self.s.read(ROUTES)
        models = self.s.read(MODELS)
        schemas = self.s.read(SCHEMAS)
        have = bool(routes)

        # ENT-001..003: auth dependencies on the registry router (static, checkable)
        if have:
            router_auth = bool(re.search(
                r"APIRouter\([^)]*dependencies\s*=\s*\[[^\]]*Depends", routes, re.S))
            per_route_auth = len(re.findall(
                r"dependencies\s*=\s*\[[^\]]*(?:require_role|get_current_user|guard)",
                routes))
            total_routes = len(re.findall(r"@router\.(?:get|post|put|patch|delete)", routes))
            protected = router_auth or per_route_auth > 0
            self.s.record(
                "TEFCA-ENT-001", CAT, "Registry routes carry an authentication dependency",
                outcome=Outcome.PASS if protected else Outcome.FAIL,
                expected="Router-level dependencies=[Depends(...)] or per-route auth",
                observed=f"{total_routes} routes; router-level auth={router_auth}; "
                         f"{per_route_auth} route-level auth declarations",
                finding="" if protected else
                        "No authentication dependency found on the registry router or "
                        "its routes. This is the shape of Phase 0 finding AUTHZ-01.",
                severity="critical" if not protected else "info",
                source=ROUTES, owasp=A01, owasp_api=["API5:2023"], cwe=["306", "862"],
                nist=["AC-3"], hipaa=HIPAA_AC,
                remediation="Add dependencies=[Depends(get_current_user)] to the router.")

            writes = re.findall(
                r"@router\.(post|put|patch|delete)\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)",
                routes)
            role_gated = len(re.findall(r"require_role\(", routes))
            self.s.record(
                "TEFCA-ENT-002", CAT, "Mutating routes are role-gated (contributor+)",
                outcome=Outcome.PASS if role_gated else Outcome.WARN,
                expected="require_role(...) on POST/PUT/PATCH/DELETE routes",
                observed=f"{len(writes)} mutating routes; {role_gated} require_role uses",
                finding="" if role_gated else
                        "No require_role() call found; mutating registry routes may be "
                        "reachable by any authenticated user regardless of role.",
                severity="high" if not role_gated else "info",
                source=ROUTES, owasp=A01, owasp_api=["API5:2023"], cwe=["862"],
                nist=["AC-3", "AC-6"], hipaa=HIPAA_AC,
                remediation="Gate writes behind require_role('contributor') or higher.")
        else:
            for tid, nm in (("TEFCA-ENT-001", "Registry routes carry auth dependency"),
                            ("TEFCA-ENT-002", "Mutating routes are role-gated")):
                self.s.stub(tid, CAT, nm, "app/tefca_registry/routes.py not readable",
                            owasp=A01, cwe=["306"], nist=["AC-3"], hipaa=HIPAA_AC)

        self.s.stub("TEFCA-ENT-003", CAT, "Delete entity requires admin", NEEDS_LIVE,
                    owasp=A01, cwe=["862"], nist=["AC-3"], hipaa=HIPAA_AC)

        # ENT-004/005/008: uniqueness + hierarchy in the schema (static)
        src = models or schemas
        if src:
            uniq = re.findall(r"unique\s*=\s*True|UniqueConstraint\(", src)
            tefcaid = bool(re.search(r"(?i)tefca_?id", src))
            hcid = bool(re.search(r"(?i)\bhcid\b", src))
            self.s.record(
                "TEFCA-ENT-004", CAT, "Unique constraint present on TEFCA identifiers",
                outcome=Outcome.PASS if (uniq and (tefcaid or hcid)) else Outcome.WARN,
                expected="unique=True / UniqueConstraint covering TEFCAID and HCID",
                observed=f"{len(uniq)} uniqueness declarations; TEFCAID field "
                         f"{'present' if tefcaid else 'absent'}; HCID "
                         f"{'present' if hcid else 'absent'}",
                finding="" if (uniq and (tefcaid or hcid)) else
                        "No uniqueness constraint found covering the TEFCA identifiers; "
                        "duplicate entities could be created.",
                severity="medium", source=MODELS if models else SCHEMAS,
                owasp=["A04:2021"], cwe=["694"], nist=["SI-10"],
                remediation="Add a UniqueConstraint on (system, value) for identifiers.")

            parent = bool(re.search(r"(?i)parent_(?:id|entity)", src))
            self.s.record(
                "TEFCA-ENT-008", CAT, "Entity hierarchy modelled (Sub requires Parent)",
                outcome=Outcome.PASS if parent else Outcome.WARN,
                expected="A parent reference column on the entity model",
                observed=f"parent reference {'found' if parent else 'not found'}",
                finding="" if parent else
                        "No parent reference found; the QHIN / Participant / "
                        "Sub-Participant hierarchy TEFCA requires may not be enforced.",
                severity="medium", source=MODELS if models else SCHEMAS,
                owasp=["A04:2021"], cwe=["1220"], nist=["AC-3"],
                remediation="Model parent_id with a FK and validate the tier rules.")
        else:
            for tid, nm in (("TEFCA-ENT-004", "Unique constraint on TEFCA identifiers"),
                            ("TEFCA-ENT-008", "Entity hierarchy modelled")):
                self.s.stub(tid, CAT, nm, "registry models not readable")

        # Everything else genuinely needs the running registry.
        live_only = [
            ("TEFCA-ENT-005", "Duplicate HCID rejected at the API"),
            ("TEFCA-ENT-006", "Entity without mandatory identifiers -> 422"),
            ("TEFCA-ENT-007", "Entity with invalid NPI rejected (Luhn)"),
            ("TEFCA-ENT-009", "Circular hierarchy prevented"),
            ("TEFCA-ENT-010", "Entity search returns only authorised results"),
            ("TEFCA-ENT-011", "Entity detail includes audit trail"),
            ("TEFCA-ENT-012", "Bulk operations respect RBAC"),
            ("TEFCA-ENT-013", "XSS in entity name sanitised"),
            ("TEFCA-ENT-014", "SQL injection in search -> 422"),
            ("TEFCA-ENT-015", "Entity versions immutable (no overwrite)"),
        ]
        for tid, nm in live_only:
            self.s.stub(tid, CAT, nm, NEEDS_LIVE, owasp=A01,
                        cwe=["862"], nist=["AC-3"], hipaa=HIPAA_AC)
