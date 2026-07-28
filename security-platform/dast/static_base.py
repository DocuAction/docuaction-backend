"""Base for tests that analyse SOURCE or a LOCAL database rather than a live target.

The TEFCA registry is not deployed to dev, so its HTTP surface cannot be exercised.
That does not make every TEFCA test impossible: state-machine correctness, audit
coverage, identifier validation and schema constraints are all properties of the code
and the local schema, and can be checked without a running server.

What this deliberately does NOT do is let a static result masquerade as a live one.
Every record says how it was obtained, and anything that genuinely needs the running
registry is emitted as STUB with the precondition named.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from dast.results import Evidence, EvidenceWriter, Outcome, TestRun

BACKEND = Path(r"C:/Imran_Coding projects/DocuAction/backend")


class StaticTester:
    """Emits Evidence without making HTTP requests."""

    def __init__(self, run: TestRun, writer: EvidenceWriter,
                 backend: Optional[Path] = None):
        self.run = run
        self.writer = writer
        self.backend = Path(backend or BACKEND)
        self._src_cache: Dict[str, str] = {}

    # ── source helpers ───────────────────────────────────────────────────────

    def read(self, rel: str) -> str:
        if rel not in self._src_cache:
            p = self.backend / rel
            try:
                self._src_cache[rel] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                self._src_cache[rel] = ""
        return self._src_cache[rel]

    def exists(self, rel: str) -> bool:
        return (self.backend / rel).exists()

    def glob(self, pattern: str) -> List[Path]:
        return sorted(self.backend.glob(pattern))

    def tree(self, rel: str) -> Optional[ast.AST]:
        src = self.read(rel)
        if not src:
            return None
        try:
            return ast.parse(src)
        except SyntaxError:
            return None

    # ── evidence ─────────────────────────────────────────────────────────────

    def record(self, test_id: str, category: str, name: str, *,
               outcome: Outcome, expected: str = "", observed: str = "",
               finding: str = "", severity: str = "info", confidence: str = "medium",
               source: str = "", owasp: Optional[List[str]] = None,
               owasp_api: Optional[List[str]] = None,
               cwe: Optional[List[str]] = None, nist: Optional[List[str]] = None,
               hipaa: Optional[List[str]] = None, asvs: Optional[List[str]] = None,
               remediation: str = "", notes: str = "",
               request_summary: Optional[Dict[str, Any]] = None) -> Evidence:
        method_note = ("static source analysis" if source else "analysis")
        ev = Evidence(
            test_id=test_id, category=category, test_name=name,
            endpoint=source, method="STATIC",
            request_summary=request_summary or {"analysis": method_note,
                                                "source": source},
            expected=expected, observed=observed, outcome=outcome,
            finding=finding, severity=severity, confidence=confidence,
            owasp=owasp or [], owasp_api=owasp_api or [], cwe=cwe or [],
            nist=nist or [], hipaa=hipaa or [], asvs=asvs or [],
            remediation=remediation,
            notes=(notes + (" " if notes else "") +
                   "Determined by static analysis of source, not by exercising a "
                   "running system.") if source else notes,
        )
        self.writer.write(ev)
        return self.run.add(ev)

    def stub(self, test_id: str, category: str, name: str, precondition: str,
             *, owasp: Optional[List[str]] = None, cwe: Optional[List[str]] = None,
             nist: Optional[List[str]] = None, hipaa: Optional[List[str]] = None,
             owasp_api: Optional[List[str]] = None) -> Evidence:
        ev = Evidence(
            test_id=test_id, category=category, test_name=name,
            outcome=Outcome.STUB, severity="info",
            owasp=owasp or [], owasp_api=owasp_api or [], cwe=cwe or [],
            nist=nist or [], hipaa=hipaa or [],
            notes=f"NOT EXECUTED - {precondition}. The test is implemented and will "
                  f"run once the precondition is met.")
        self.writer.write(ev)
        return self.run.add(ev)
