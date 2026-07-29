"""ENGINE 3 - custom AGT/DocuAction rule packs.

Pure stdlib. This plugin has no external dependency and therefore always runs,
which is what guarantees the platform produces evidence even when every third-party
scanner is unavailable.

HONESTY ABOUT PRECISION
    Regex and single-file AST cannot prove taint. A finding here says "this pattern
    warrants review", not "this is exploitable". Confidence is set per rule and
    carried into the report so a LOW-confidence hit is never presented as a proven
    vulnerability.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from core.models import Category, Confidence, Finding, ScanTarget, Severity
from plugins.base import ScannerPlugin
from plugins.rules.ast_rules import run_ast_checks
from plugins.rules.patterns import PatternRule, all_rules

# Lines longer than this are almost always minified or generated; scanning them
# produces noise and can pathologically slow the regex engine.
MAX_LINE_LENGTH = 2000
MAX_FILE_BYTES = 2_000_000


class CustomRulesPlugin(ScannerPlugin):
    name = "custom_rules"
    display_name = "AGT Custom Rules (OWASP / Auth / Healthcare / Azure)"
    category = Category.SAST
    required_binary = ""
    required_module = ""

    def is_available(self) -> tuple[bool, str]:
        return True, ""          # stdlib only - always available, by design

    def version(self) -> str:
        packs = self.config.get("rule_packs") or ["owasp", "auth", "healthcare", "azure"]
        return f"packs={','.join(packs)}"

    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        packs = self.config.get("rule_packs") or None
        rules = all_rules(packs)
        findings: List[Finding] = []
        self._parse_errors: List[str] = []

        for target in targets:
            root = Path(os.path.expandvars(target.path))
            if not root.exists():
                continue
            for path in self._iter_files(root):
                rel = self.relative_path(str(path))
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                findings.extend(self._apply_patterns(rules, path, rel, text))
                if path.suffix.lower() == ".py":
                    findings.extend(self._apply_ast(path, rel))
        return findings

    # ── pattern engine ───────────────────────────────────────────────────────

    def _apply_patterns(self, rules: List[PatternRule], path: Path, rel: str,
                        text: str) -> List[Finding]:
        out: List[Finding] = []
        ext = path.suffix.lower()
        lines = text.splitlines()
        for rule in rules:
            if ext not in rule.extensions:
                continue
            if rule.path_include and not re.search(rule.path_include, rel):
                continue
            if rule.path_exclude and re.search(rule.path_exclude, rel):
                continue
            rx = rule.compiled()
            for idx, line in enumerate(lines, start=1):
                if len(line) > MAX_LINE_LENGTH:
                    continue
                if not rx.search(line):
                    continue
                if rule.suppressed(line):
                    continue
                out.append(Finding(
                    rule_id=rule.id,
                    tool="custom_rules",
                    title=rule.title,
                    severity=rule.severity,
                    category=rule.category,
                    confidence=rule.confidence,
                    file_path=rel,
                    line_start=idx,
                    line_end=idx,
                    code_snippet=line.strip()[:300],
                    description=rule.description,
                    remediation=rule.remediation,
                    effort=rule.effort,
                    compliance=rule.compliance,
                    extra={"engine": "pattern"},
                ))
        return out

    # ── AST engine ───────────────────────────────────────────────────────────

    def _apply_ast(self, path: Path, rel: str) -> List[Finding]:
        results, err = run_ast_checks(path)
        if err:
            self._parse_errors.append(f"{rel}: {err}")
            return []
        return [Finding(
            rule_id=r.rule_id,
            tool="custom_rules",
            title=r.title,
            severity=r.severity,
            category=Category.SAST,
            confidence=r.confidence,
            file_path=rel,
            line_start=r.line,
            line_end=r.line,
            code_snippet=r.snippet[:300],
            description=r.description,
            remediation=r.remediation,
            effort=r.effort,
            compliance=r.compliance,
            extra={"engine": "ast"},
        ) for r in results]

    # ── file walk ────────────────────────────────────────────────────────────

    def _iter_files(self, root: Path):
        wanted = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yml", ".yaml",
                  ".bicep"}
        excludes = self.project.exclude_patterns
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            dirnames[:] = [x for x in dirnames
                           if not self._excluded(str(d / x).replace("\\", "/") + "/", excludes)]
            for fn in filenames:
                fp = d / fn
                if fp.suffix.lower() not in wanted:
                    continue
                if self._excluded(str(fp).replace("\\", "/"), excludes):
                    continue
                yield fp

    @staticmethod
    def _excluded(path_str: str, patterns: List[str]) -> bool:
        return any(fnmatch.fnmatch(path_str, p) or fnmatch.fnmatch(path_str, f"*/{p}")
                   for p in patterns)
