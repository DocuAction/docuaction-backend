"""PHASE 1C - Secrets detection via Gitleaks.

SECRET VALUES ARE NEVER STORED
    This is the one plugin whose raw output is itself sensitive. Gitleaks reports the
    matched credential in its `Secret` and `Match` fields; writing either into the
    findings database or a report would turn the security report into a second copy
    of every secret - readable by anyone the report is shared with, and persisted in
    git if reports are ever committed.

    So the plugin discards both fields immediately and stores only:
        rule id, file, line, entropy, commit/author/date (history hits),
        and a redaction of the form  ****(len=64, sha256:ab12cd34)

    The sha256 prefix is a correlation handle: it lets you tell "the same secret in
    three files" from "three different secrets", and lets fingerprints stay stable
    across scans, without disclosing anything. Reversing an 8-hex-char prefix of a
    SHA-256 to a high-entropy credential is not feasible.

SCAN MODES
    `gitleaks git <path>` walks the full commit history - this is what answers "was a
    secret ever committed?", which is the question that actually matters, since a
    credential removed in a later commit is still in the history and still burned.
    If the target is not a git repository (or the history scan errors), the plugin
    falls back to `gitleaks dir <path>` over the working tree and says so, rather
    than silently reporting a clean history it never checked.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.models import (Category, ComplianceMapping, Confidence, Finding,
                         ScanTarget, Severity)
from plugins.base import ScannerPlugin

# Gitleaks rule-id fragments -> the credential class we report.
SECRET_TYPES = {
    "anthropic": "Anthropic API key",
    "openai": "OpenAI API key",
    "sendgrid": "SendGrid API key",
    "azure": "Azure credential",
    "aws": "AWS credential",
    "github": "GitHub token",
    "slack": "Slack token",
    "stripe": "Stripe key",
    "jwt": "JWT / signing secret",
    "private-key": "Private key",
    "rsa": "Private key",
    "pgp": "Private key",
    "ssh": "Private key",
    "connection-string": "Connection string",
    "postgres": "Database connection string",
    "mysql": "Database connection string",
    "mongodb": "Database connection string",
    "password": "Password",
    "generic-api-key": "Generic API key / high-entropy string",
    "entropy": "High-entropy string",
}

# Rules whose hit is severe enough to block a release outright.
CRITICAL_HINTS = ("private-key", "rsa", "aws", "azure", "postgres", "mysql",
                  "mongodb", "connection-string", "anthropic", "openai", "sendgrid")


def redact(secret: str, gitleaks_fingerprint: str = "") -> str:
    """Irreversible, correlatable placeholder. Never returns any part of the input.

    Correlation uses gitleaks' own `Fingerprint` (file:rule:line - contains no secret
    material), NOT a hash of the value. We run gitleaks with `--redact`, so the
    `Secret` field arrives as the literal string "REDACTED"; hashing that produced the
    same digest for every finding and made the handle useless for telling one secret
    from another. Keeping --redact matters more: it means the real credential is never
    written to the intermediate report file on disk.
    """
    ref = (gitleaks_fingerprint or "").strip()
    if ref:
        short = hashlib.sha256(ref.encode("utf-8", "replace")).hexdigest()[:8]
        return f"****(redacted at source; ref:{short})"
    return "****(redacted at source)"


class GitleaksPlugin(ScannerPlugin):
    name = "gitleaks"
    display_name = "Gitleaks (secrets detection)"
    category = Category.SECRETS
    required_binary = "gitleaks"

    def version(self) -> str:
        if self._version:
            return self._version
        try:
            rc, out, err, _ = self.exec_bounded([self.binary_path(), "version"], timeout=60)
            self._version = (out or err or "").strip().splitlines()[0] if (out or err) else ""
        except Exception:
            self._version = ""
        return self._version

    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        timeout = int(self.config.get("timeout_seconds", 900))
        scan_history = bool(self.config.get("scan_history", True))
        findings: List[Finding] = []
        self.modes: Dict[str, str] = {}

        for target in targets:
            root = Path(os.path.expandvars(target.path))
            if not root.exists():
                continue

            # BOTH modes: they answer different questions and neither subsumes the
            # other. History answers "was this ever committed?" - the question that
            # decides whether rotation is mandatory, since a secret deleted in a later
            # commit is still disclosed. The directory scan answers "is it on disk
            # now?" and is the only one that can see files git never tracked
            # (.env, *.local.json), which is exactly where local credentials live.
            collected: List[tuple] = []
            modes_used: List[str] = []

            if scan_history and (root / ".git").exists():
                hist = self._scan(["git", str(root)], timeout)
                if hist is not None:
                    modes_used.append("git history")
                    collected += [(i, "git history") for i in hist]

            tree = self._scan(["dir", str(root)], timeout)
            if tree is not None:
                modes_used.append("working tree")
                collected += [(i, "working tree") for i in tree]

            if not modes_used:
                raise RuntimeError(f"gitleaks failed on target '{target.name}'")

            self.modes[target.name] = " + ".join(modes_used)
            seen = set()
            for item, mode in collected:
                f = self._to_finding(item, target, mode)
                # gitleaks does not know the project's exclude list, so it happily
                # scans build output. Without this, Next.js's .next/ cache contributed
                # 5 "high-entropy string" findings that are generated artefacts, not
                # source, and would be regenerated on every build.
                if self._excluded(f.file_path):
                    continue
                # The same secret found in both passes is one finding; the history hit
                # arrives first and wins, carrying the stronger remediation obligation.
                key = (f.file_path, f.line_start, f.rule_id)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(f)
        return findings

    def _excluded(self, rel_path: str) -> bool:
        import fnmatch
        p = (rel_path or "").replace("\\", "/")
        for pat in self.project.exclude_patterns:
            if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(p, f"*/{pat}") \
                    or fnmatch.fnmatch(f"/{p}", pat):
                return True
        return False

    def _scan(self, args: List[str], timeout: int) -> Optional[List[Dict[str, Any]]]:
        """Run gitleaks, return parsed report or None on failure."""
        import tempfile
        out_path = Path(tempfile.mkstemp(suffix=".json")[1])
        try:
            cmd = [self.binary_path(), *args,
                   "--report-format", "json", "--report-path", str(out_path),
                   "--exit-code", "0",          # findings are not a process failure
                   "--redact",                  # belt-and-braces: ask gitleaks to redact too
                   "--no-banner", "-l", "error"]
            rc, stdout, stderr, timed_out = self.exec_bounded(cmd, timeout=timeout)
            if timed_out:
                return None
            if not out_path.exists():
                return None
            text = out_path.read_text(encoding="utf-8", errors="replace").strip()
            return json.loads(text) if text else []
        except Exception:
            return None
        finally:
            try:
                out_path.unlink()
            except OSError:
                pass

    def _to_finding(self, item: Dict[str, Any], target: ScanTarget, mode: str) -> Finding:
        rule_id = str(item.get("RuleID") or "unknown")
        low = rule_id.lower()
        secret_type = next((v for k, v in SECRET_TYPES.items() if k in low),
                           "Generic secret")
        severity = (Severity.CRITICAL if any(h in low for h in CRITICAL_HINTS)
                    else Severity.HIGH)

        # Secret/Match must never survive into storage; only a non-secret handle does.
        masked = redact(str(item.get("Secret") or ""), str(item.get("Fingerprint") or ""))

        commit = str(item.get("Commit") or "")
        in_history = bool(commit) and mode == "git history"

        # `git` mode returns repo-relative paths; `dir` mode returns absolute ones.
        # Prefixing the target name unconditionally produced paths like
        # "backend/C:/Imran_Coding projects/.../backend/.env".
        raw_path = str(item.get("File") or "").replace("\\", "/")
        if not raw_path:
            rel = target.name
        elif Path(raw_path).is_absolute() or ":" in raw_path[:3]:
            rel = self.relative_path(raw_path)
        else:
            rel = f"{target.name}/{raw_path}"

        desc = (f"{secret_type} detected by gitleaks rule '{rule_id}'. "
                f"Value redacted: {masked}. ")
        if in_history:
            desc += (f"Present in COMMIT HISTORY (commit {commit[:12]}). Removing the "
                     f"file in a later commit does not remove it from history - the "
                     f"credential must be treated as disclosed and rotated.")
        else:
            desc += ("Found in the working tree. Verify whether it was ever committed; "
                     "if not, rotation is precautionary rather than mandatory.")

        return Finding(
            rule_id=rule_id,
            tool="gitleaks",
            title=f"{secret_type} in {Path(rel).name}",
            severity=severity,
            category=Category.SECRETS,
            confidence=Confidence.HIGH if item.get("Entropy", 0) else Confidence.MEDIUM,
            file_path=rel,
            line_start=int(item.get("StartLine") or 0),
            line_end=int(item.get("EndLine") or 0),
            # Redacted stand-in for the source line. The real line is NOT stored.
            code_snippet=f"[REDACTED {secret_type}] {masked}",
            description=desc,
            remediation=("Rotate the credential now, then remove it from source and "
                         "load it from Azure Key Vault or an environment variable. If "
                         "it is in git history, rotation is mandatory - rewriting "
                         "history does not un-disclose a value that has been pushed."),
            effort="0.5d + rotation",
            compliance=ComplianceMapping(
                cwe=["798", "540"],
                owasp_top10=["A07:2021", "A05:2021"],
                owasp_api_top10=["API8:2023"],
                owasp_asvs=["V2.10.4", "V14.3.2"],
                nist_800_53=["IA-5", "SC-12", "SC-28"],
                hipaa=["164.312(a)(2)(i)", "164.312(e)(2)(ii)"],
                cwe_top25=True,
            ),
            extra={
                "engine": "gitleaks",
                "scan_mode": mode,
                "in_git_history": in_history,
                "commit": commit[:12],
                "commit_date": str(item.get("Date") or "")[:19],
                "author": str(item.get("Author") or "")[:80],
                "entropy": round(float(item.get("Entropy") or 0), 2),
                "secret_type": secret_type,
                # Deliberately absent: Secret, Match, and the raw source line.
            },
        )
