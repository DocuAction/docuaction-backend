"""UPLOAD-001..006 - upload security.

All payloads are INERT. The "malicious" files are recognisable by signature but do
nothing: the EICAR-style probe is a harmless test string, the PE stub is a truncated
header with no code, and the polyglot is a GIF header followed by inert text. Nothing
uploaded here can execute even if it were stored and served.
"""

from __future__ import annotations

from typing import List, Tuple

from dast.api_tester import APISecurityTester
from dast.results import Outcome

CAT = "file_upload"
A04 = ["A04:2021"]
UPLOAD_PATHS = ["/api/documents/upload", "/api/v1/tefca/registry/import/csv",
                "/api/upload"]

# (test_id, name, filename, content_type, body, cwe, severity, remediation)
CASES: List[Tuple[str, str, str, str, bytes, List[str], str, str]] = [
    ("UPLOAD-001", "Executable disguised with a document extension",
     "invoice.pdf", "application/pdf",
     b"MZ\x90\x00\x03" + b"\x00" * 32 + b"This is an inert truncated PE header.",
     ["434"], "high",
     "Validate magic bytes against the declared extension and content type; reject "
     "on mismatch rather than trusting either."),
    ("UPLOAD-002", "Double extension",
     "report.pdf.exe", "application/octet-stream",
     b"inert-dast-probe", ["434"], "high",
     "Normalise and validate the final extension; use an allow-list."),
    ("UPLOAD-003", "Path traversal in the filename",
     "../../../../tmp/agt-dast-probe.txt", "text/plain",
     b"inert-dast-probe", ["22"], "high",
     "Discard the client filename entirely; store under a server-generated UUID."),
    ("UPLOAD-004", "Antivirus test-signature file",
     "eicar.txt", "text/plain",
     b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-DAST-INERT-PROBE-FILE!$H+H*",
     ["434", "494"], "medium",
     "Scan uploads with a real AV engine; Phase 0 finding FU-02 noted the current "
     "scanner is heuristic only."),
    ("UPLOAD-005", "GIF/HTML polyglot (stored XSS vector)",
     "avatar.gif", "image/gif",
     b"GIF89a<!-- inert dast probe: no script -->",
     ["79", "434"], "medium",
     "Re-encode images server-side and serve user content from a separate origin with "
     "Content-Disposition: attachment."),
    ("UPLOAD-006", "Oversized upload (5 MB)",
     "big.bin", "application/octet-stream", b"A" * (5 * 1024 * 1024),
     ["770"], "medium",
     "Enforce a maximum body size at the edge and stream-validate."),
]


class FileUploadTester:
    def __init__(self, tester: APISecurityTester):
        self.t = tester

    async def _find_endpoint(self) -> str:
        for p in UPLOAD_PATHS:
            r = await self.t.request("POST", p)
            # 401/403/422 all prove the route exists; 404/405 mean it does not.
            if r.status not in (404, 405, 0):
                return p
        return ""

    async def run(self) -> None:
        endpoint = await self._find_endpoint()
        if not endpoint:
            for tid, name, *_ in CASES:
                self.t.generate_evidence(
                    tid, CAT, name, outcome=Outcome.SKIP,
                    severity="info", owasp=A04, cwe=["434"], nist=["SI-3"],
                    notes="No upload endpoint responded on this target "
                          f"(tried {', '.join(UPLOAD_PATHS)}).")
            return

        for tid, name, fname, ctype, body, cwe, sev, fix in CASES:
            files = {"file": (fname, body, ctype)}
            r = await self.t.request("POST", endpoint, files=files)

            behind_auth = r.status in (401, 403)
            accepted = r.ok
            rejected = r.status in (400, 415, 422, 413, 431)
            leaked = self.t.leaks_stack_trace(r)

            if r.status == 429:
                outcome, finding, severity = Outcome.SKIP, "", "info"
                notes = ("Our own scan was rate-limited (429); the upload never reached "
                         "the validator. Harness artefact, not a result.")
            elif behind_auth:
                outcome, finding, severity = Outcome.SKIP, "", "info"
                notes = ("Upload endpoint required authentication, so the payload never "
                         "reached the validator. Access control passed; upload "
                         "validation is untested and NOT reported as passing.")
            elif accepted:
                outcome = Outcome.FAIL
                finding = (f"The endpoint accepted '{fname}' (HTTP {r.status}). "
                           f"Inert probe, but a real payload of the same shape would "
                           f"also have been stored.")
                severity, notes = sev, ""
            elif rejected:
                outcome, finding, severity = Outcome.PASS, "", "info"
                notes = f"Rejected with HTTP {r.status} as expected."
            elif leaked or r.status >= 500:
                outcome = Outcome.FAIL
                finding = (f"Hostile upload produced HTTP {r.status}"
                           + " with internal details in the body" if leaked else "")
                severity, notes = sev, ""
            else:
                outcome, finding, severity = Outcome.WARN, \
                    f"Unexpected HTTP {r.status} for a hostile upload.", "low"
                notes = ""

            self.t.generate_evidence(
                tid, CAT, name, method="POST", endpoint=endpoint, response=r,
                request_summary={"filename": fname, "content_type": ctype,
                                 "size_bytes": len(body), "payload": "INERT probe"},
                expected="4xx rejection (400/415/422/413)",
                outcome=outcome, finding=finding, severity=severity,
                owasp=A04, owasp_api=["API8:2023"], cwe=cwe,
                nist=["SI-3", "SI-10"], hipaa=["164.312(c)(1)"], asvs=["V12.1.1"],
                remediation=fix, notes=notes)
