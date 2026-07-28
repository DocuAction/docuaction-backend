"""JWT-001..012 - JWT-specific attacks.

Every case here sends a token the server MUST refuse. A 2xx on any of them is a
complete authentication bypass, so the pass condition is uniformly "401 or 403".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "jwt"
PROBE = "/api/auth/me"   # verified to exist and refuse anonymously
A07 = ["A07:2021"]
API2 = ["API2:2023"]


def _seg(d: Any) -> str:
    raw = json.dumps(d, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _sign(header: str, body: str, secret: str, digest=hashlib.sha256) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{header}.{body}".encode(), digest).digest()
    ).rstrip(b"=").decode()


class JwtTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    def _claims(self, **over) -> Dict[str, Any]:
        now = int(time.time())
        c = {"sub": "dast@example.invalid", "user_id": 1, "role": "admin",
             "email": "dast@example.invalid", "iat": now, "exp": now + 3600}
        c.update(over)
        return c

    def _cases(self) -> List[Tuple[str, str, str, List[str], str]]:
        c = self._claims()
        now = int(time.time())
        h_hs = _seg({"alg": "HS256", "typ": "JWT"})
        b = _seg(c)

        alg_none = f"{_seg({'alg':'none','typ':'JWT'})}.{b}."
        alg_none_sig = f"{_seg({'alg':'None','typ':'JWT'})}.{b}.{_sign(h_hs,b,'x')}"
        # RS256 header with an HMAC signature computed over a guessable key: the
        # classic algorithm-confusion attack where a server verifies an RS256 header
        # using the public key as an HMAC secret.
        h_rs = _seg({"alg": "RS256", "typ": "JWT"})
        confusion = f"{h_rs}.{b}.{_sign(h_rs, b, 'public-key-as-hmac-secret')}"
        # Downgrade: strong declared alg, weak actual signature.
        h_512 = _seg({"alg": "HS512", "typ": "JWT"})
        downgrade = f"{h_512}.{b}.{_sign(h_512, b, 'weak', hashlib.sha256)}"
        # kid injection / key confusion
        h_kid = _seg({"alg": "HS256", "typ": "JWT", "kid": "../../dev/null"})
        kid = f"{h_kid}.{b}.{_sign(h_kid, b, '')}"
        jwk_inject = _seg({"alg": "HS256", "typ": "JWT",
                           "jwk": {"kty": "oct", "k": "YXR0YWNrZXI"}})
        embedded = f"{jwk_inject}.{b}.{_sign(jwk_inject, b, 'attacker')}"

        return [
            ("JWT-001", "Algorithm confusion (RS256 header, HMAC signature)",
             confusion, ["327", "347"], "critical"),
            ("JWT-002", "Algorithm downgrade (HS512 declared, SHA-256 signature)",
             downgrade, ["327"], "critical"),
            ("JWT-003", 'alg "none" - unsigned token accepted',
             alg_none, ["327", "347"], "critical"),
            ("JWT-004", 'alg "None" case variant with a stray signature',
             alg_none_sig, ["327"], "critical"),
            ("JWT-005", "Missing signature segment",
             f"{h_hs}.{b}", ["347"], "critical"),
            ("JWT-006", "Empty signature segment",
             f"{h_hs}.{b}.", ["347"], "critical"),
            ("JWT-007", "Modified payload with original-shaped signature",
             f"{h_hs}.{_seg(self._claims(role='superadmin', user_id=999))}."
             f"{_sign(h_hs, b, 'not-the-server-secret')}", ["347"], "critical"),
            ("JWT-008", "kid path-traversal injection", kid, ["347", "22"], "high"),
            ("JWT-009", "Embedded JWK in header (self-signed key)",
             embedded, ["347"], "critical"),
            ("JWT-010", "Future iat (issued in the future)",
             f"{h_hs}.{_seg(self._claims(iat=now + 86400, exp=now + 90000))}."
             f"{_sign(h_hs, _seg(self._claims(iat=now + 86400, exp=now + 90000)), 'x')}",
             ["613"], "medium"),
            ("JWT-011", "Negative / already-past exp",
             f"{h_hs}.{_seg(self._claims(exp=now - 10, iat=now - 3600))}."
             f"{_sign(h_hs, _seg(self._claims(exp=now - 10, iat=now - 3600)), 'x')}",
             ["613"], "high"),
            ("JWT-012", "Oversized token (100 KB claim padding)",
             f"{h_hs}.{_seg(self._claims(pad='A' * 100_000))}.{_sign(h_hs, b, 'x')}",
             ["400", "770"], "medium"),
        ]

    async def run(self) -> None:
        for tid, name, token, cwe, sev in self._cases():
            r = await self.t.request("GET", PROBE, token=token)
            rejected = r.status in (401, 403, 422)
            # A 431/413 on the oversized case is a correct, defensive answer.
            if tid == "JWT-012" and r.status in (413, 431):
                rejected = True
            # A 404 means the ROUTE does not exist on this target, so the token was
            # never evaluated. That is a SKIP, not a pass and certainly not a failure:
            # reporting "forged token rejected" because the endpoint is absent would be
            # false assurance, and reporting a FAIL would be a false alarm.
            if r.status == 404:
                self.t.generate_evidence(
                    tid, CAT, name, method="GET", endpoint=PROBE, response=r,
                    expected="401/403 (rejected)", outcome=Outcome.SKIP,
                    severity="info", owasp=A07, owasp_api=API2, cwe=cwe,
                    nist=["IA-2", "SC-13"],
                    notes=f"Probe endpoint {PROBE} returned 404 on this target - the "
                          f"route is not deployed, so the crafted token was never "
                          f"evaluated. Not a pass and not a failure.")
                continue
            self.t.generate_evidence(
                tid, CAT, name, method="GET", endpoint=PROBE, response=r,
                request_summary={"authorization": "Bearer [crafted token]",
                                 "token_length": len(token)},
                expected="401/403 (rejected)",
                outcome=Outcome.PASS if rejected else Outcome.FAIL,
                finding="" if rejected else
                        (f"A crafted token was ACCEPTED (HTTP {r.status}) - this is a "
                         f"full authentication bypass." if r.ok else
                         f"Unexpected HTTP {r.status}; the token was neither accepted nor "
                         f"cleanly rejected, which may indicate an unhandled error path."),
                severity=sev if r.ok else ("low" if not rejected else "info"),
                owasp=A07 + (["A02:2021"] if "alg" in name.lower() else []),
                owasp_api=API2, cwe=cwe, nist=["IA-2", "SC-13"], asvs=["V3.5.3"],
                remediation="Verify the signature with a pinned algorithm allow-list "
                            "that excludes 'none' and asymmetric/symmetric mixing; "
                            "ignore attacker-supplied kid/jwk headers; validate "
                            "exp/iat/iss/aud; cap token size before parsing.")

        # Transport hygiene: token accepted from a URL parameter is a leak vector,
        # since query strings land in access logs, proxies and browser history.
        r = await self.t.request(
            "GET", f"{PROBE}?access_token={self._cases()[0][2]}")
        accepted = r.ok
        self.t.generate_evidence(
            "JWT-013", CAT, "Token in URL query parameter must not authenticate",
            method="GET", endpoint=PROBE, response=r,
            expected="401/403 - bearer tokens belong in the Authorization header",
            outcome=Outcome.FAIL if accepted else Outcome.PASS,
            finding="Token accepted from a query parameter; query strings are recorded "
                    "in access logs, proxies and browser history." if accepted else "",
            severity="medium" if accepted else "info",
            owasp=A07, owasp_api=API2, cwe=["598"], nist=["SC-8", "AU-9"],
            asvs=["V3.5.1"], hipaa=["164.312(e)(1)"],
            remediation="Accept credentials only from the Authorization header.")
