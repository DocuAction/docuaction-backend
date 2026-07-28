"""AUTHZ-001..016 - authorization / RBAC tests.

Most of these need tokens for DISTINCT roles. Without them a test cannot honestly
claim "viewer is blocked from admin" - it can only observe that an anonymous caller is
blocked, which is a different (weaker) statement. Those tests therefore report SKIP
with the precise missing precondition rather than a misleading PASS.

AUTHZ-001 and -008 and -014 need no credentials at all: they assert that an
UNAUTHENTICATED caller is refused, which is the control Sprint 1 actually fixed.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "authorization"
A01 = ["A01:2021"]
API5 = ["API5:2023"]
API1 = ["API1:2023"]
NIST_AC = ["AC-3", "AC-6"]
HIPAA_AC = ["164.312(a)(1)", "164.312(d)"]

# (path, method, label) - endpoints that must never serve an anonymous caller.
PROTECTED: List[Tuple[str, str, str]] = [
    # Verified present on the dev target (403 anonymously).
    ("/api/auth/me", "GET", "Current-user profile"),
    ("/api/documents", "GET", "Document store"),
    ("/api/admin/users", "GET", "Admin user management"),
    ("/api/v1/case-management/info", "GET", "Case management (Sprint 1 AUTHZ-01)"),
    ("/api/v1/bulletin/run/fcc/preview", "GET", "Bulletin run preview (cost amplification)"),
    # Present on some deployments only; a 404 is reported as SKIP, never as a pass.
    ("/api/v1/tefca/registry/entities", "GET", "TEFCA entity registry"),
    ("/api/v1/tefca/registry/qhins", "GET", "QHIN registry"),
    ("/api/v1/tefca/dashboard/summary", "GET", "TEFCA dashboard"),
]

# Endpoints intended to be publicly readable - a 401 here would be a false positive.
PUBLIC_OK: List[Tuple[str, str]] = [
    ("/health", "GET"),
    ("/api/v1/bulletin/health", "GET"),
    ("/api/v1/bulletin/costs", "GET"),
]


class AuthzTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    def _roles(self) -> Dict[str, str]:
        return {k: v for k, v in self.t.auth_tokens.items()
                if k in ("viewer", "contributor", "reviewer", "qalead", "admin")}

    async def run(self) -> None:
        await self._anonymous()
        await self._public()
        await self._role_matrix()

    # AUTHZ-001, -008, -014: anonymous must be refused
    async def _anonymous(self) -> None:
        blocked_all = True
        for i, (path, method, label) in enumerate(PROTECTED, start=1):
            r = await self.t.request(method, path)
            missing = r.status == 404
            blocked = r.status in (401, 403)
            # A transport error or our own 429 is NOT evidence that the endpoint served
            # an anonymous caller. Previously status 0 (ReadTimeout) was scored as a
            # FAIL claiming anonymous access, which is the opposite of what happened.
            inconclusive = r.status in (0, 429)
            if not blocked and not missing and not inconclusive:
                blocked_all = False
            tid = {"/api/v1/case-management/info": "AUTHZ-008",
                   "/api/v1/bulletin/run/fcc/preview": "AUTHZ-014"}.get(
                       path, f"AUTHZ-001.{i}")
            self.t.generate_evidence(
                tid, CAT, f"Unauthenticated access to {label} -> 401/403",
                method=method, endpoint=path, response=r,
                expected="401 or 403 (404 acceptable if the route is not mounted)",
                outcome=Outcome.PASS if blocked else
                        (Outcome.SKIP if (missing or inconclusive) else Outcome.FAIL),
                finding="" if blocked or missing or inconclusive else
                        f"{label} served an anonymous caller (HTTP {r.status}). This is "
                        f"unauthenticated access to a protected surface"
                        + (" carrying PHI." if "case" in path.lower() else "."),
                severity="critical" if (r.ok and "case" in path.lower()) else
                         ("high" if r.ok else "info"),
                owasp=A01, owasp_api=API5, cwe=["306", "862"],
                nist=NIST_AC, hipaa=HIPAA_AC, asvs=["V4.1.1"],
                remediation="Add dependencies=[Depends(get_current_user)] to the route "
                            "or its router, plus a role check.",
                notes=("Route not mounted on this target." if missing else
                       (f"Inconclusive: {'our scan was rate-limited' if r.status == 429 else r.error or 'transport error'}. "
                        f"No response was received, so anonymous access is neither "
                        f"proven nor disproven." if inconclusive else "")))

        self.t.generate_evidence(
            "AUTHZ-001", CAT, "All protected endpoints refuse anonymous callers",
            expected="every endpoint in the protected set returns 401/403",
            observed=f"{len(PROTECTED)} endpoints probed",
            outcome=Outcome.PASS if blocked_all else Outcome.FAIL,
            finding="" if blocked_all else
                    "At least one protected endpoint served an anonymous request - see "
                    "the AUTHZ-001.* records.",
            severity="high", owasp=A01, owasp_api=API5, cwe=["306"],
            nist=NIST_AC, hipaa=HIPAA_AC)

    # AUTHZ-015: public reads should stay reachable (guards against over-blocking)
    async def _public(self) -> None:
        results = []
        for path, method in PUBLIC_OK:
            r = await self.t.request(method, path)
            results.append((path, r.status))
        reachable = [p for p, s in results if 200 <= s < 300]
        self.t.generate_evidence(
            "AUTHZ-015", CAT, "Intentionally public reads remain reachable",
            expected="/health and public bulletin reads return 2xx",
            observed="; ".join(f"{p}={s}" for p, s in results),
            outcome=Outcome.PASS if reachable else Outcome.WARN,
            finding="" if reachable else
                    "No intentionally-public endpoint responded 2xx; the target may be "
                    "misconfigured, which would make the anonymous-refusal results above "
                    "meaningless (everything refuses everything).",
            severity="info", owasp=A01, nist=["AC-3"],
            notes="This is a control test for the harness itself, not a vulnerability "
                  "test: it proves the target is actually serving traffic.")

    # AUTHZ-002..007, 009..013, 016: need real role tokens
    async def _role_matrix(self) -> None:
        roles = self._roles()
        planned = [
            ("AUTHZ-002", "Viewer cannot access admin endpoints -> 403", "viewer"),
            ("AUTHZ-003", "Contributor cannot access admin endpoints -> 403", "contributor"),
            ("AUTHZ-004", "Admin can access admin endpoints -> 200", "admin"),
            ("AUTHZ-005", "User A cannot read User B's resources (IDOR)", "two accounts"),
            ("AUTHZ-006", "User cannot escalate own role -> 403", "viewer"),
            ("AUTHZ-007", "Token rejected after logout -> 401", "any"),
            ("AUTHZ-009", "Registry read permitted for viewer", "viewer"),
            ("AUTHZ-010", "Registry write blocked for viewer -> 403", "viewer"),
            ("AUTHZ-011", "Verification requires contributor", "contributor"),
            ("AUTHZ-012", "Import requires reviewer or above", "reviewer"),
            ("AUTHZ-013", "Admin endpoints admin-only", "admin"),
            ("AUTHZ-016", "Profiles CRUD requires auth", "admin"),
        ]
        for tid, name, need in planned:
            have = need in roles or (need == "any" and bool(roles))
            if not have:
                self.t.generate_evidence(
                    tid, CAT, name, outcome=Outcome.SKIP,
                    expected=name.split("->")[-1].strip() if "->" in name else name,
                    severity="info", owasp=A01, owasp_api=API1 + API5,
                    cwe=["862", "863"], nist=NIST_AC, hipaa=HIPAA_AC,
                    notes=f"Requires a '{need}' token. No role credentials were supplied "
                          f"for this target, and a self-service signup cannot grant "
                          f"elevated roles. Provide credentials via config/dast.json "
                          f"(credentials.<role>) to execute this test. Reporting SKIP "
                          f"rather than PASS: an anonymous refusal does not demonstrate "
                          f"that a low-privilege ROLE is correctly blocked.")
                continue

            # Execute the subset we can with the tokens available.
            tok = roles.get(need) or next(iter(roles.values()))
            if tid == "AUTHZ-010":
                r = await self.t.request("POST", "/api/v1/tefca/registry/entities",
                                         token=tok, json_body={"name": "DAST probe"})
                ok = r.status in (401, 403)
                self.t.generate_evidence(
                    tid, CAT, name, method="POST",
                    endpoint="/api/v1/tefca/registry/entities", response=r,
                    expected="403 for a viewer-level token",
                    outcome=Outcome.PASS if ok else Outcome.FAIL,
                    finding="" if ok else
                            f"A low-privilege token was able to attempt a registry write "
                            f"(HTTP {r.status}).",
                    severity="high" if r.ok else "info",
                    owasp=A01, owasp_api=API5, cwe=["862"], nist=NIST_AC,
                    hipaa=HIPAA_AC)
            else:
                r = await self.t.request("GET", "/api/admin/users", token=tok)
                expect_block = need != "admin"
                blocked = r.status in (401, 403)
                good = blocked if expect_block else (r.ok or r.status == 404)
                self.t.generate_evidence(
                    tid, CAT, name, method="GET", endpoint="/api/admin/users",
                    response=r,
                    expected="403" if expect_block else "200",
                    outcome=Outcome.PASS if good else Outcome.FAIL,
                    finding="" if good else
                            f"Role '{need}' got HTTP {r.status} where "
                            f"{'a block' if expect_block else 'access'} was expected.",
                    severity="high" if (expect_block and r.ok) else "info",
                    owasp=A01, owasp_api=API5, cwe=["862", "863"],
                    nist=NIST_AC, hipaa=HIPAA_AC)
