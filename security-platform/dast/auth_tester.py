"""AUTH-001..020 - authentication security tests.

Notable design point: AUTH-002 vs AUTH-003 are compared against EACH OTHER, not
against a fixed expectation. A wrong password and a nonexistent account must produce
indistinguishable responses; if they differ in status, body, or timing, the login
endpoint is a user-enumeration oracle. Testing each in isolation cannot detect that -
only the comparison can.
"""

from __future__ import annotations

import base64
import json
import re
import statistics
import time
from typing import Any, Dict, List, Optional

from dast.api_tester import APISecurityTester, Response
from dast.results import Outcome

CAT = "authentication"
LOGIN = "/api/auth/login"
SIGNUP = "/api/auth/signup"

A07 = ["A07:2021"]
API2 = ["API2:2023"]


_VOLATILE = re.compile(
    r"(?i)\"(?:request_id|requestid|trace_id|correlation_id|timestamp|ts|time|date|"
    r"retry_after)\"\s*:\s*\"?[^,}\"]*\"?"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def normalise_body(text: str) -> str:
    """Strip per-request noise so two error bodies can be compared meaningfully.

    Every error response here carries a fresh request_id UUID. Comparing raw bodies
    therefore ALWAYS reports a difference, which produced a false "user enumeration"
    finding: the wrong-password and unknown-user bodies were in fact byte-identical
    apart from that identifier.
    """
    return _VOLATILE.sub("<VOLATILE>", (text or ""))[:400]


def _b64url(d: dict) -> str:
    raw = json.dumps(d, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def forge_jwt(payload: dict, alg: str = "HS256", secret: str = "wrong-secret-value",
              signature: Optional[str] = None) -> str:
    """Build a token the server should reject. Uses a deliberately wrong key."""
    header = _b64url({"alg": alg, "typ": "JWT"})
    body = _b64url(payload)
    if signature is None:
        import hashlib
        import hmac
        signature = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), f"{header}.{body}".encode(),
                     hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


class AuthTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester
        self.valid_token: Optional[str] = None
        self.test_email: Optional[str] = None

    async def run(self) -> None:
        await self._signup_and_login()      # AUTH-018/019/020, AUTH-001
        await self._negative_login()        # AUTH-002..006
        await self._timing()                # AUTH-007
        await self._token_shape()           # AUTH-008..010
        await self._token_rejection()       # AUTH-011..017

    # ── signup / login ───────────────────────────────────────────────────────

    async def _signup_and_login(self) -> None:
        prefix = self.t.config.test_account_prefix
        email = f"{prefix}{int(time.time())}@example.invalid"
        pw = "Str0ng-DAST-Passw0rd!x9"

        if not self.t.config.allow_write_tests:
            for tid, name in (("AUTH-018", "Valid signup -> 201"),
                              ("AUTH-019", "Duplicate email signup -> 409"),
                              ("AUTH-020", "Weak password signup -> 422"),
                              ("AUTH-001", "Valid login -> 200 + token")):
                self.t.generate_evidence(
                    tid, CAT, name, outcome=Outcome.SKIP,
                    notes="allow_write_tests=false: signup would create a dev user record")
            return

        # AUTH-020 first - a weak password must be refused BEFORE we create anything.
        r = await self.t.request("POST", SIGNUP, json_body={
            "email": f"weak-{email}", "password": "123", "full_name": "DAST Weak"})
        self.t.generate_evidence(
            "AUTH-020", CAT, "Weak password signup -> 422",
            method="POST", endpoint=SIGNUP, response=r,
            request_summary={"email": "[REDACTED]", "password": "[REDACTED]"},
            expected="422 (or 400) rejecting a 3-character password",
            outcome=(Outcome.SKIP if r.status == 429 else
                     (Outcome.PASS if r.status in (400, 422) else Outcome.FAIL)),
            finding="" if r.status in (400, 422, 429) else
                    f"A 3-character password was accepted or mishandled (HTTP {r.status}). "
                    f"Weak-password acceptance undermines every downstream control.",
            severity="medium", owasp=A07, owasp_api=API2, cwe=["521"],
            nist=["IA-5"], asvs=["V2.1.1"],
            remediation="Enforce a minimum length/complexity policy server-side and "
                        "return 422 with a validation error.")

        r = await self.t.request("POST", SIGNUP, json_body={
            "email": email, "password": pw, "full_name": "AGT DAST Test"})
        created = r.status in (200, 201)
        self.t.generate_evidence(
            "AUTH-018", CAT, "Valid signup -> 201",
            method="POST", endpoint=SIGNUP, response=r,
            request_summary={"email": "[REDACTED]", "password": "[REDACTED]"},
            expected="200/201 creating the account",
            outcome=Outcome.PASS if created else
                    (Outcome.SKIP if r.status in (401, 403, 404, 405, 429) else Outcome.WARN),
            finding="" if created or r.status in (401, 403, 404, 405, 429) else
                    f"Signup returned HTTP {r.status} for a well-formed request.",
            severity="low", owasp=A07, cwe=["287"], nist=["IA-2"],
            notes=("Creates a record in the DEV database only." if created else
                   ("The signup endpoint rate-limited OUR OWN scan (429) after earlier "
                    "runs; account creation and every test depending on it are skipped. "
                    "This is a harness artefact - and incidentally positive evidence "
                    "that registration throttling works."
                    if r.status == 429 else
                    f"Signup unavailable (HTTP {r.status}); dependent tests skipped.")))
        if created:
            self.test_email = email

        # AUTH-019 duplicate
        if created:
            r2 = await self.t.request("POST", SIGNUP, json_body={
                "email": email, "password": pw, "full_name": "AGT DAST Dup"})
            ok = r2.status in (400, 409, 422)
            self.t.generate_evidence(
                "AUTH-019", CAT, "Duplicate email signup -> 409 (not 500)",
                method="POST", endpoint=SIGNUP, response=r2,
                expected="409/400/422 - a handled conflict, never a 500",
                outcome=Outcome.PASS if ok else Outcome.FAIL,
                finding="" if ok else
                        f"Duplicate signup returned HTTP {r2.status}. A 500 here means the "
                        f"unique constraint is being surfaced as an unhandled exception, "
                        f"which leaks internals and confirms the address exists.",
                severity="medium" if r2.status >= 500 else "low",
                owasp=["A09:2021"], cwe=["209"], nist=["SI-11"], asvs=["V7.4.1"],
                remediation="Catch the integrity error and return a generic 409.")
        else:
            self.t.generate_evidence("AUTH-019", CAT,
                                     "Duplicate email signup -> 409 (not 500)",
                                     outcome=Outcome.SKIP,
                                     notes="signup unavailable")

        # AUTH-001 login
        if created:
            r3 = await self.t.request("POST", LOGIN,
                                      json_body={"email": email, "password": pw})
            body = r3.json() or {}
            tok = body.get("access_token") or body.get("token") or ""
            got = bool(tok)
            if got:
                self.valid_token = tok
                self.t.auth_tokens.setdefault("self", tok)
            self.t.generate_evidence(
                "AUTH-001", CAT, "Valid login -> 200 + token",
                method="POST", endpoint=LOGIN, response=r3,
                expected="200 with an access token",
                observed=f"HTTP {r3.status}, token {'present' if got else 'absent'}",
                outcome=Outcome.PASS if (r3.ok and got) else Outcome.WARN,
                finding="" if (r3.ok and got) else
                        f"Login with freshly-created valid credentials returned "
                        f"HTTP {r3.status} without a usable token.",
                severity="low", owasp=A07, cwe=["287"], nist=["IA-2"])
        else:
            self.t.generate_evidence("AUTH-001", CAT, "Valid login -> 200 + token",
                                     outcome=Outcome.SKIP,
                                     notes="no account could be created")

    # ── negative login ───────────────────────────────────────────────────────

    async def _negative_login(self) -> None:
        wrong_pw = await self.t.request("POST", LOGIN, json_body={
            "email": self.test_email or "nobody@example.invalid",
            "password": "definitely-not-the-password"})
        self.t.generate_evidence(
            "AUTH-002", CAT, "Invalid password -> 401 (not 500)",
            method="POST", endpoint=LOGIN, response=wrong_pw,
            expected="401 (or 400/403); never 500 and never 200",
            outcome=(Outcome.SKIP if wrong_pw.status == 429 else
                     (Outcome.PASS if wrong_pw.status in (400, 401, 403)
                      else Outcome.FAIL)),
            finding="" if wrong_pw.status in (400, 401, 403, 429) else
                    f"Wrong password produced HTTP {wrong_pw.status}.",
            severity="high" if wrong_pw.ok else
                     ("medium" if wrong_pw.status >= 500 else "info"),
            owasp=A07, owasp_api=API2, cwe=["287"], nist=["IA-2"], asvs=["V2.2.1"],
            remediation="Return a generic 401 for every failed authentication.",
            notes=("The login limiter throttled our own scan (429). Inconclusive for "
                   "this test - and positive evidence that login throttling works."
                   if wrong_pw.status == 429 else ""))

        nouser = await self.t.request("POST", LOGIN, json_body={
            "email": f"absolutely-no-such-user-{int(time.time())}@example.invalid",
            "password": "definitely-not-the-password"})
        same_status = nouser.status == wrong_pw.status
        same_body = normalise_body(nouser.text) == normalise_body(wrong_pw.text)
        indistinguishable = same_status and same_body
        throttled = 429 in (nouser.status, wrong_pw.status)
        self.t.generate_evidence(
            "AUTH-003", CAT, "Nonexistent email -> identical response to wrong password",
            method="POST", endpoint=LOGIN, response=nouser,
            expected="Response identical to AUTH-002 (no user enumeration)",
            observed=f"status {nouser.status} vs {wrong_pw.status}; "
                     f"body {'identical' if same_body else 'DIFFERENT'}",
            outcome=(Outcome.SKIP if throttled else
                     (Outcome.PASS if indistinguishable else Outcome.FAIL)),
            finding="" if (indistinguishable or throttled) else
                    "A nonexistent account and a wrong password are distinguishable, so "
                    "the login endpoint is a user-enumeration oracle: an attacker can "
                    "confirm which email addresses are registered before attacking them.",
            severity="medium", owasp=A07, owasp_api=API2, cwe=["204"],
            nist=["IA-2"], asvs=["V2.2.1"],
            remediation="Return byte-identical responses for unknown-user and "
                        "wrong-password, and keep timing comparable.",
            notes=("Both arms were rate-limited (429), so they matched only because "
                   "each returned the same throttle response. That is a degenerate "
                   "match, not evidence of enumeration resistance - reported as SKIP."
                   if throttled else ""))

        empty = await self.t.request("POST", LOGIN, json_body={})
        self.t.generate_evidence(
            "AUTH-004", CAT, "Empty body -> 422 (not 500)",
            method="POST", endpoint=LOGIN, response=empty,
            expected="422/400 validation error",
            outcome=Outcome.PASS if empty.status in (400, 401, 422) else Outcome.FAIL,
            finding="" if empty.status in (400, 401, 422) else
                    f"Empty login body produced HTTP {empty.status}.",
            severity="low", owasp=["A09:2021"], cwe=["20"], nist=["SI-10"],
            remediation="Validate the request model; return 422.")

        sqli = await self.t.request("POST", LOGIN, json_body={
            "email": "' OR '1'='1'--@example.invalid", "password": "' OR 1=1--"})
        leaked = self.t.leaks_stack_trace(sqli)
        self.t.generate_evidence(
            "AUTH-005", CAT, "SQL injection in email -> 401, no SQL error",
            method="POST", endpoint=LOGIN, response=sqli,
            expected="401/422 with no SQL error and no authentication bypass",
            outcome=Outcome.PASS if (not sqli.ok and not leaked) else Outcome.FAIL,
            finding="" if (not sqli.ok and not leaked) else
                    ("Authentication was BYPASSED by a SQL payload." if sqli.ok else
                     "A database error was returned, indicating the input reached SQL."),
            severity="critical" if sqli.ok else ("high" if leaked else "info"),
            owasp=["A03:2021"], owasp_api=["API8:2023"], cwe=["89"],
            nist=["SI-10"], asvs=["V5.3.4"],
            remediation="Use parameterised queries and never echo database errors.")

        xss = await self.t.request("POST", LOGIN, json_body={
            "email": "<script>alert(1)</script>@example.invalid", "password": "x"})
        reflected = "<script>alert(1)</script>" in (xss.text or "")
        self.t.generate_evidence(
            "AUTH-006", CAT, "XSS in email -> not reflected unescaped",
            method="POST", endpoint=LOGIN, response=xss,
            expected="Payload absent from the response, or HTML-escaped",
            outcome=Outcome.FAIL if reflected else Outcome.PASS,
            finding="Login error response reflects an unescaped script tag." if reflected else "",
            severity="medium" if reflected else "info",
            owasp=["A03:2021"], cwe=["79"], nist=["SI-10"], asvs=["V5.3.3"],
            remediation="Never echo submitted values into responses unescaped.")

    # ── timing ───────────────────────────────────────────────────────────────

    async def _timing(self) -> None:
        """AUTH-007. Compares wrong-password vs unknown-user latency.

        Rate limiting bounds us to a small sample, so a modest difference is reported
        as a WARN rather than a finding - claiming a timing oracle from 5 samples over
        the public internet would not be defensible.
        """
        known, unknown = [], []
        for i in range(4):
            r = await self.t.request("POST", LOGIN, json_body={
                "email": self.test_email or "nobody@example.invalid",
                "password": f"wrong-{i}"})
            # A 429 measures OUR throttling, not the server's auth path.
            if r.status not in (429, 0):
                known.append(r.elapsed_ms)
            r2 = await self.t.request("POST", LOGIN, json_body={
                "email": f"ghost-{i}-{int(time.time())}@example.invalid",
                "password": f"wrong-{i}"})
            if r2.status not in (429, 0):
                unknown.append(r2.elapsed_ms)

        if len(known) < 3 or len(unknown) < 3:
            self.t.generate_evidence(
                "AUTH-007", CAT, "Login timing consistency (no timing oracle)",
                method="POST", endpoint=LOGIN, outcome=Outcome.SKIP, severity="info",
                owasp=A07, cwe=["208"], nist=["IA-2"],
                notes=f"Only {len(known)}/{len(unknown)} usable samples after "
                      f"discarding rate-limited responses - too few to say anything "
                      f"about timing.")
            return

        mk, mu = statistics.median(known), statistics.median(unknown)
        delta = abs(mk - mu)
        ratio = (max(mk, mu) / min(mk, mu)) if min(mk, mu) > 0 else 1.0
        suspicious = delta > 120 and ratio > 1.5
        self.t.generate_evidence(
            "AUTH-007", CAT, "Login timing consistency (no timing oracle)",
            method="POST", endpoint=LOGIN,
            request_summary={"samples_per_arm": len(known)},
            expected="Comparable latency for known-user and unknown-user failures",
            observed=f"median known-user {mk:.0f}ms vs unknown-user {mu:.0f}ms "
                     f"(delta {delta:.0f}ms, ratio {ratio:.2f})",
            outcome=Outcome.WARN if suspicious else Outcome.PASS,
            finding=(f"Median login latency differs by {delta:.0f}ms between existing "
                     f"and nonexistent accounts, which may allow user enumeration by "
                     f"timing." if suspicious else ""),
            severity="low", confidence="low",
            owasp=A07, cwe=["208"], nist=["IA-2"], asvs=["V2.2.1"],
            remediation="Hash a dummy password on the unknown-user path so both "
                        "branches perform equivalent work.",
            notes="Small sample (rate-limited) over a network path; treat as indicative "
                  "only. A local run with many samples is needed to confirm.")

    # ── token shape ──────────────────────────────────────────────────────────

    async def _token_shape(self) -> None:
        if not self.valid_token:
            for tid, nm in (("AUTH-008", "Token is a valid JWT"),
                            ("AUTH-009", "Token contains expected claims"),
                            ("AUTH-010", "Token expiry within configured range")):
                self.t.generate_evidence(tid, CAT, nm, outcome=Outcome.SKIP,
                                         notes="no valid token obtained")
            return

        parts = self.valid_token.split(".")
        three = len(parts) == 3
        self.t.generate_evidence(
            "AUTH-008", CAT, "Token is a valid JWT (three segments)",
            expected="header.payload.signature",
            observed=f"{len(parts)} segment(s)",
            outcome=Outcome.PASS if three else Outcome.WARN,
            finding="" if three else "Issued token is not a three-segment JWT.",
            severity="info", owasp=A07, cwe=["287"], nist=["IA-2"])

        claims: Dict[str, Any] = {}
        if three:
            try:
                pad = parts[1] + "=" * (-len(parts[1]) % 4)
                claims = json.loads(base64.urlsafe_b64decode(pad))
            except Exception:
                claims = {}
        has = [c for c in ("sub", "exp") if c in claims]
        self.t.generate_evidence(
            "AUTH-009", CAT, "Token contains expected claims (sub, exp)",
            expected="sub and exp present",
            observed=f"present: {has or 'none'}; keys: {sorted(claims)[:8]}",
            outcome=Outcome.PASS if len(has) == 2 else Outcome.FAIL,
            finding="" if len(has) == 2 else
                    f"Token is missing {sorted({'sub','exp'} - set(has))}. A token "
                    f"without exp never expires, so a leaked token is valid forever.",
            severity="medium" if "exp" not in claims else "low",
            owasp=A07, owasp_api=API2, cwe=["613"], nist=["AC-12"], asvs=["V3.3.1"],
            remediation="Always issue exp (and iat); keep access-token TTL short.")

        exp, iat = claims.get("exp"), claims.get("iat")
        ttl_h = ((exp - iat) / 3600.0) if (isinstance(exp, (int, float))
                                          and isinstance(iat, (int, float))) else None
        if ttl_h is None and isinstance(exp, (int, float)):
            ttl_h = (exp - time.time()) / 3600.0
        reasonable = ttl_h is not None and 0 < ttl_h <= 8
        self.t.generate_evidence(
            "AUTH-010", CAT, "Token expiry within a reasonable range (<= 8h)",
            expected="0 < TTL <= 8 hours for an access token",
            observed=f"TTL {ttl_h:.2f}h" if ttl_h is not None else "TTL undeterminable",
            outcome=Outcome.PASS if reasonable else
                    (Outcome.SKIP if ttl_h is None else Outcome.WARN),
            finding="" if reasonable or ttl_h is None else
                    f"Access-token lifetime is {ttl_h:.1f}h. Phase 0 finding AUTH-01 "
                    f"recorded a 24h admin TTL; a long-lived bearer token widens the "
                    f"window for any leak.",
            severity="low", owasp=A07, cwe=["613"], nist=["AC-12"], asvs=["V3.3.1"],
            remediation="Reduce access-token TTL to <= 1h and rely on refresh rotation.")

    # ── token rejection ──────────────────────────────────────────────────────

    async def _token_rejection(self) -> None:
        """AUTH-011..017 against a known-protected endpoint."""
        probe = "/api/auth/me"   # verified present; refuses anonymously
        base = {"sub": "dast@example.invalid", "role": "admin", "user_id": 1}

        cases = [
            ("AUTH-011", "Expired token -> 401",
             forge_jwt({**base, "exp": int(time.time()) - 3600,
                        "iat": int(time.time()) - 7200}), ["613"], "high"),
            ("AUTH-012", "Malformed token -> 401", "not.a.jwt", ["287"], "high"),
            ("AUTH-013", "Modified payload -> 401",
             (self.valid_token.rsplit(".", 1)[0] + "." + "AAAA"
              if self.valid_token else forge_jwt({**base, "exp": int(time.time()) + 3600},
                                                 signature="AAAA")), ["347"], "critical"),
            ("AUTH-014", 'Algorithm "none" -> 401',
             f"{_b64url({'alg':'none','typ':'JWT'})}."
             f"{_b64url({**base, 'exp': int(time.time())+3600})}.", ["327", "347"], "critical"),
            ("AUTH-015", "Wrong signing secret -> 401",
             forge_jwt({**base, "exp": int(time.time()) + 3600},
                       secret="attacker-chosen-secret"), ["347"], "critical"),
        ]
        for tid, name, tok, cwe, sev in cases:
            r = await self.t.request("GET", probe, token=tok)
            if r.status == 404:
                # Route absent on this target: the token was never evaluated.
                self.t.generate_evidence(
                    tid, CAT, name, method="GET", endpoint=probe, response=r,
                    expected="401 or 403", outcome=Outcome.SKIP, severity="info",
                    owasp=A07, cwe=cwe, nist=["IA-2"],
                    notes=f"{probe} returned 404 - route not deployed here, so the "
                          f"forged token was never checked.")
                continue
            rejected = r.status in (401, 403)
            self.t.generate_evidence(
                tid, CAT, name, method="GET", endpoint=probe, response=r,
                request_summary={"authorization": "Bearer [forged token]"},
                expected="401 or 403",
                outcome=Outcome.PASS if rejected else Outcome.FAIL,
                finding="" if rejected else
                        f"A forged/invalid token was not rejected (HTTP {r.status}). "
                        f"If this returned 2xx, authentication can be bypassed entirely.",
                severity=sev if r.ok else ("medium" if not rejected else "info"),
                owasp=A07, owasp_api=API2, cwe=cwe, nist=["IA-2", "SC-13"],
                asvs=["V3.5.3"],
                remediation="Verify the signature, pin the algorithm allow-list "
                            "(excluding 'none'), and validate exp/iss/aud.")

        r = await self.t.request("GET", probe, headers={"Authorization": ""})
        self.t.generate_evidence(
            "AUTH-016", CAT, "Empty Authorization header -> 401/403",
            method="GET", endpoint=probe, response=r,
            expected="401 or 403",
            outcome=Outcome.PASS if r.status in (401, 403) else Outcome.FAIL,
            finding="" if r.status in (401, 403) else
                    f"Empty Authorization header produced HTTP {r.status}.",
            severity="high" if r.ok else "info",
            owasp=A07, cwe=["287"], nist=["IA-2"])

        raw = self.valid_token or forge_jwt({**base, "exp": int(time.time()) + 3600})
        r = await self.t.request("GET", probe, headers={"Authorization": raw})
        self.t.generate_evidence(
            "AUTH-017", CAT, "Raw token without Bearer prefix -> 401/403",
            method="GET", endpoint=probe, response=r,
            expected="401 or 403 - the scheme is part of the contract",
            outcome=Outcome.PASS if r.status in (401, 403) else Outcome.FAIL,
            finding="" if r.status in (401, 403) else
                    f"A token without the Bearer scheme was accepted (HTTP {r.status}), "
                    f"indicating lenient header parsing.",
            severity="low", owasp=A07, cwe=["287"], nist=["IA-2"], asvs=["V3.5.1"])
