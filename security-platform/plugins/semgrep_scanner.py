"""ENGINE 1 - Semgrep.

AVAILABILITY IS PROBED, NOT ASSUMED
    `semgrep --version` succeeds on Windows even though the scanner does not work
    there: the pip wheel installs the Python front-end, but the analysis engine
    (`semgrep-core`, an OCaml binary) has no official Windows build. The front-end
    then hangs indefinitely instead of failing. Observed here: 10+ minutes on a
    three-line file with no output and no error.

    A version check would therefore mark semgrep "available", and the plugin would
    hang every scan until the timeout. So is_available() runs a real scan against a
    throwaway file with a short timeout and only reports available if it returns
    parseable JSON. This is the difference between "the binary exists" and "the
    scanner works", and only the second one is useful.

    On Linux/macOS (and CI), the probe passes in a couple of seconds and the plugin
    runs normally.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.models import (Category, ComplianceMapping, Confidence, Finding,
                         ScanTarget, Severity)
from plugins.base import ScannerPlugin

DEFAULT_RULESETS = ["p/python", "p/javascript", "p/owasp-top-ten", "p/jwt",
                    "p/sql-injection", "p/xss", "p/secrets"]

# OWASP category string in semgrep metadata -> our canonical A0N:2021 id.
_OWASP_PREFIX = {
    "a01": "A01:2021", "a02": "A02:2021", "a03": "A03:2021", "a04": "A04:2021",
    "a05": "A05:2021", "a06": "A06:2021", "a07": "A07:2021", "a08": "A08:2021",
    "a09": "A09:2021", "a10": "A10:2021",
}

CWE_TOP25 = {"20", "22", "77", "78", "79", "89", "94", "125", "190", "200", "269",
             "287", "306", "352", "362", "416", "434", "476", "502", "787", "798",
             "862", "863", "918"}


class SemgrepPlugin(ScannerPlugin):
    name = "semgrep"
    display_name = "Semgrep (multi-language SAST)"
    category = Category.SAST
    required_binary = "semgrep"

    PROBE_TIMEOUT = 60          # a working semgrep answers a 2-line file in ~5s

    def is_available(self) -> tuple[bool, str]:
        ok, reason = super().is_available()
        if not ok:
            return False, reason

        if os.name == "nt" and not self.config.get("force_on_windows"):
            # Still probe - if a future release ships a Windows core, this starts
            # working automatically rather than staying disabled by a hardcoded rule.
            works, why = self._probe()
            if not works:
                return False, (
                    f"semgrep is installed but not functional on this platform "
                    f"({why}). semgrep-core has no official Windows build; run under "
                    f"WSL, Docker, or Linux CI to enable it.")
            return True, ""

        works, why = self._probe()
        return (True, "") if works else (False, f"semgrep probe failed: {why}")

    def _probe(self) -> tuple[bool, str]:
        """Run a real (tiny) scan with an INLINE pattern. Never raises.

        Uses -e/--lang rather than a registry ruleset so the probe needs no network.
        Otherwise an offline machine would look identical to a broken engine, and the
        report would blame the wrong thing.
        """
        tmp = Path(tempfile.mkdtemp(prefix="agt_semgrep_probe_"))
        try:
            (tmp / "probe.py").write_text("import os\nx = eval('1')\n", encoding="utf-8")
            rc, stdout, stderr, timed_out = self.exec_bounded(
                [self.binary_path(), "--lang", "python", "-e", "eval(...)",
                 "--json", "--quiet", "--metrics=off", "--no-git-ignore", str(tmp)],
                timeout=self.PROBE_TIMEOUT)
            if timed_out:
                return False, (f"produced no result within {self.PROBE_TIMEOUT}s on a "
                               f"2-line file (process tree killed)")
            if not (stdout or "").strip():
                return False, f"no output (rc={rc}): {(stderr or '').strip()[:120]}"
            json.loads(stdout)
            return True, ""
        except json.JSONDecodeError:
            return False, "output was not valid JSON"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def version(self) -> str:
        if self._version:
            return self._version
        try:
            proc = self.exec([self.binary_path(), "--version"], timeout=60)
            self._version = (proc.stdout or "").strip().splitlines()[0]
        except Exception:
            self._version = ""
        return self._version

    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        rulesets = self.config.get("rulesets") or DEFAULT_RULESETS
        timeout = int(self.config.get("timeout_seconds", 1800))

        scan_paths: List[str] = []
        for t in targets:
            root = Path(os.path.expandvars(t.path))
            if not root.exists():
                continue
            # Prefer the application source subtree when it exists, to keep the scan
            # focused on first-party code.
            for sub in ("app", "src"):
                if (root / sub).is_dir():
                    scan_paths.append(str(root / sub))
                    break
            else:
                scan_paths.append(str(root))
        if not scan_paths:
            return []

        # All rulesets in ONE invocation: semgrep parses each target file once
        # instead of once per ruleset.
        cmd = [self.binary_path()]
        for r in rulesets:
            cmd += ["--config", r]
        cmd += ["--json", "--quiet", "--metrics=off", "--no-git-ignore"]
        for pat in ("node_modules", ".venv", "venv", "pydeps", "__pycache__", "dist",
                    "build", ".next"):
            cmd += ["--exclude", pat]
        cmd += scan_paths

        rc, stdout, stderr, timed_out = self.exec_bounded(cmd, timeout=timeout)
        if timed_out:
            raise RuntimeError(
                f"semgrep exceeded its {timeout}s budget and was killed; no partial "
                f"results are available")
        if not (stdout or "").strip():
            raise RuntimeError(
                f"semgrep returned no output (rc={rc}): {(stderr or '')[:200]}")
        payload = json.loads(stdout)

        findings = [self._to_finding(r) for r in payload.get("results", [])]
        for err in (payload.get("errors") or [])[:5]:
            # Surfaced rather than swallowed: a partial scan must not look complete.
            self._parse_note = str(err)[:200]
        return findings

    def _to_finding(self, r: Dict[str, Any]) -> Finding:
        extra = r.get("extra") or {}
        meta = extra.get("metadata") or {}

        cwes = [self._cwe_id(c) for c in self._as_list(meta.get("cwe"))]
        cwes = [c for c in cwes if c]
        owasp = []
        for o in self._as_list(meta.get("owasp")):
            key = str(o).strip().lower().replace("a0", "a").replace(":", "")[:3]
            key = key if key.startswith("a") else ""
            norm = _OWASP_PREFIX.get(key[:3]) or _OWASP_PREFIX.get(
                str(o).strip().lower()[:3])
            if norm and norm not in owasp:
                owasp.append(norm)

        compliance = ComplianceMapping(
            cwe=cwes,
            owasp_top10=owasp,
            owasp_asvs=[str(x) for x in self._as_list(meta.get("asvs"))][:4],
            nist_800_53=self._nist_for(cwes),
            cwe_top25=any(c in CWE_TOP25 for c in cwes),
        )
        refs = [str(x) for x in self._as_list(meta.get("references"))][:5]

        return Finding(
            rule_id=r.get("check_id", "semgrep"),
            tool="semgrep",
            title=(extra.get("message") or r.get("check_id", ""))[:180],
            severity=Severity.coerce(extra.get("severity")),
            category=Category.SAST,
            confidence=Confidence.coerce(meta.get("confidence")),
            file_path=self.relative_path(r.get("path", "")),
            line_start=int((r.get("start") or {}).get("line") or 0),
            line_end=int((r.get("end") or {}).get("line") or 0),
            code_snippet=(extra.get("lines") or "").strip()[:300],
            description=extra.get("message", ""),
            remediation=(meta.get("fix") or extra.get("fix") or
                         "See the rule reference for remediation guidance."),
            references=refs,
            compliance=compliance,
            extra={"engine": "semgrep", "ruleset": meta.get("source", "")},
        )

    @staticmethod
    def _as_list(v) -> List[Any]:
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    @staticmethod
    def _cwe_id(raw: Any) -> str:
        s = str(raw)
        import re
        m = re.search(r"CWE-(\d+)", s, re.I)
        return m.group(1) if m else ""

    @staticmethod
    def _nist_for(cwes: List[str]) -> List[str]:
        table = {
            "79": ["SI-10"], "89": ["SI-10"], "78": ["SI-10"], "94": ["SI-10"],
            "22": ["AC-3"], "306": ["AC-3", "IA-2"], "862": ["AC-3"],
            "863": ["AC-3"], "798": ["IA-5", "SC-12"], "327": ["SC-13"],
            "295": ["SC-8", "SC-13"], "502": ["SI-10"], "532": ["AU-9"],
            "209": ["SI-11"], "918": ["SC-7"], "352": ["SC-8"], "613": ["AC-12"],
        }
        out: List[str] = []
        for c in cwes:
            for n in table.get(c, []):
                if n not in out:
                    out.append(n)
        return out
