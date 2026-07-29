"""ENGINE 2 - Bandit (Python-specific SAST).

Bandit is pure Python, so unlike semgrep it runs anywhere the platform runs.
Invoked as a subprocess against the target tree; JSON on stdout is parsed into
Finding objects and its test IDs are mapped to control frameworks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from core.models import (Category, ComplianceMapping, Confidence, Finding,
                         ScanTarget, Severity)
from plugins.base import ScannerPlugin

# Bandit test-id -> control mapping. Bandit reports CWEs itself for most tests; this
# table adds the OWASP/NIST/HIPAA context Bandit does not carry.
BANDIT_MAP: Dict[str, Dict[str, List[str]]] = {
    "B101": {"owasp": ["A04:2021"], "nist": ["SI-10"]},                    # assert used
    "B102": {"owasp": ["A03:2021"], "nist": ["SI-10"]},                    # exec
    "B103": {"owasp": ["A01:2021"], "nist": ["AC-3"]},                     # perms
    "B105": {"owasp": ["A07:2021"], "nist": ["IA-5", "SC-12"]},            # hardcoded pw
    "B106": {"owasp": ["A07:2021"], "nist": ["IA-5", "SC-12"]},
    "B107": {"owasp": ["A07:2021"], "nist": ["IA-5", "SC-12"]},
    "B108": {"owasp": ["A01:2021"], "nist": ["AC-3"]},                     # temp file
    "B110": {"owasp": ["A09:2021"], "nist": ["SI-11"]},                    # try/except pass
    "B112": {"owasp": ["A09:2021"], "nist": ["SI-11"]},
    "B201": {"owasp": ["A05:2021"], "nist": ["CM-7"]},                     # flask debug
    "B301": {"owasp": ["A08:2021"], "nist": ["SI-10"]},                    # pickle
    "B303": {"owasp": ["A02:2021"], "nist": ["SC-13"]},                    # md5
    "B304": {"owasp": ["A02:2021"], "nist": ["SC-13"]},
    "B305": {"owasp": ["A02:2021"], "nist": ["SC-13"]},
    "B306": {"owasp": ["A01:2021"], "nist": ["AC-3"]},
    "B307": {"owasp": ["A03:2021"], "nist": ["SI-10"]},                    # eval
    "B310": {"owasp": ["A10:2021"], "nist": ["SC-7"]},                     # urlopen
    "B311": {"owasp": ["A02:2021"], "nist": ["SC-13"]},                    # random
    "B321": {"owasp": ["A02:2021"], "nist": ["SC-8"]},                     # ftp
    "B323": {"owasp": ["A02:2021"], "nist": ["SC-8", "SC-13"]},            # unverified ctx
    "B324": {"owasp": ["A02:2021"], "nist": ["SC-13"]},                    # weak hash
    "B501": {"owasp": ["A02:2021"], "nist": ["SC-8", "SC-13"]},            # verify=False
    "B502": {"owasp": ["A02:2021"], "nist": ["SC-8"]},
    "B503": {"owasp": ["A02:2021"], "nist": ["SC-8"]},
    "B506": {"owasp": ["A08:2021"], "nist": ["SI-10"]},                    # yaml.load
    "B507": {"owasp": ["A07:2021"], "nist": ["IA-2"]},                     # ssh no host key
    "B601": {"owasp": ["A03:2021"], "nist": ["SI-10"]},                    # paramiko exec
    "B602": {"owasp": ["A03:2021"], "nist": ["SI-10"]},                    # subprocess shell
    "B603": {"owasp": ["A03:2021"], "nist": ["SI-10"]},
    "B604": {"owasp": ["A03:2021"], "nist": ["SI-10"]},
    "B605": {"owasp": ["A03:2021"], "nist": ["SI-10"]},
    "B607": {"owasp": ["A03:2021"], "nist": ["SI-10"]},
    "B608": {"owasp": ["A03:2021"], "nist": ["SI-10"]},                    # SQL expr
    "B609": {"owasp": ["A03:2021"], "nist": ["SI-10"]},
    "B701": {"owasp": ["A03:2021"], "nist": ["SI-10"]},                    # jinja autoescape
    "B703": {"owasp": ["A03:2021"], "nist": ["SI-10"]},
}

# Tests whose subject matter is PHI-relevant when they fire in a healthcare app.
HIPAA_RELEVANT = {"B105", "B106", "B107", "B110", "B112", "B303", "B304", "B305",
                  "B324", "B501", "B502", "B503", "B608"}


class BanditPlugin(ScannerPlugin):
    name = "bandit"
    display_name = "Bandit (Python SAST)"
    category = Category.SAST
    required_binary = "bandit"

    def version(self) -> str:
        if self._version:
            return self._version
        try:
            proc = self.exec([self.binary_path(), "--version"], timeout=60)
            self._version = (proc.stdout or proc.stderr or "").strip().splitlines()[0]
        except Exception:
            self._version = ""
        return self._version

    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        allowed = set(self.config.get("targets") or [t.name for t in targets])
        findings: List[Finding] = []

        for target in targets:
            if target.name not in allowed:
                continue
            root = Path(os.path.expandvars(target.path))
            scan_root = root / "app" if (root / "app").is_dir() else root
            if not scan_root.exists():
                continue

            cmd = [self.binary_path(), "-r", str(scan_root), "-f", "json",
                   "-q", "--exit-zero"]
            for pat in ("*/node_modules/*", "*/.venv/*", "*/venv/*", "*/pydeps/*",
                        "*/__pycache__/*", "*/alembic/versions/*"):
                cmd += ["--exclude", pat]

            rc, stdout, stderr, timed_out = self.exec_bounded(
                cmd, timeout=int(self.config.get("timeout_seconds", 1800)))
            if timed_out:
                raise RuntimeError(f"bandit exceeded its time budget on {scan_root.name}")
            payload = self._parse_json(stdout)
            if payload is None:
                raise RuntimeError(
                    f"bandit produced no parseable JSON "
                    f"(rc={rc}): {(stderr or '')[:200]}")

            for res in payload.get("results", []):
                findings.append(self._to_finding(res))
        return findings

    @staticmethod
    def _parse_json(stdout: str):
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except Exception:
            start = stdout.find("{")
            if start < 0:
                return None
            try:
                return json.loads(stdout[start:])
            except Exception:
                return None

    def _to_finding(self, res: Dict[str, Any]) -> Finding:
        test_id = res.get("test_id", "")
        extra = BANDIT_MAP.get(test_id, {})
        cwe_obj = res.get("issue_cwe") or {}
        cwe_id = str(cwe_obj.get("id", "")) if isinstance(cwe_obj, dict) else ""

        compliance = ComplianceMapping(
            cwe=[cwe_id] if cwe_id else [],
            owasp_top10=extra.get("owasp", []),
            nist_800_53=extra.get("nist", []),
            hipaa=["164.312(a)(1)"] if test_id in HIPAA_RELEVANT else [],
            cwe_top25=cwe_id in {"20", "22", "77", "78", "79", "89", "94", "125",
                                 "190", "200", "269", "287", "306", "352", "362",
                                 "416", "434", "476", "502", "787", "798", "862",
                                 "863", "918"},
        )
        return Finding(
            rule_id=test_id or res.get("test_name", "bandit"),
            tool="bandit",
            title=f"{test_id}: {res.get('issue_text','')[:150]}",
            severity=Severity.coerce(res.get("issue_severity")),
            category=Category.SAST,
            confidence=Confidence.coerce(res.get("issue_confidence")),
            file_path=self.relative_path(res.get("filename", "")),
            line_start=int(res.get("line_number") or 0),
            line_end=int((res.get("line_range") or [0])[-1] or 0),
            code_snippet=(res.get("code") or "").strip()[:300],
            description=res.get("issue_text", ""),
            remediation=f"See {res.get('more_info','Bandit documentation')}",
            references=[res.get("more_info")] if res.get("more_info") else [],
            compliance=compliance,
            extra={"test_name": res.get("test_name", ""), "engine": "bandit"},
        )
