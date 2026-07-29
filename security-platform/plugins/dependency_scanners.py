"""PHASE 1D - dependency (SCA) scanning and SBOM generation.

Three plugins in one module because they share the manifest-location logic:

    PipAuditPlugin   - Python deps vs the PyPI advisory database
    NpmAuditPlugin   - JS deps vs the npm advisory database
    CycloneDxPlugin  - SBOM (an artefact, not a finding source)

A CVE finding is only actionable with a fix target, so `fixed_version` is carried
through and surfaced in the report. Where a vulnerability has no fixed version yet,
that is stated explicitly rather than left blank - "no fix available" is a different
decision from "we haven't looked".
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.models import (Category, ComplianceMapping, Confidence, Finding,
                         ScanTarget, Severity)
from plugins.base import ScannerPlugin

# Every dependency CVE maps to the same control family; severity carries the rest.
SCA_COMPLIANCE = ComplianceMapping(
    cwe=["1395", "937"],
    owasp_top10=["A06:2021"],
    owasp_api_top10=["API8:2023"],
    owasp_asvs=["V14.2.1"],
    nist_800_53=["RA-5", "SI-2", "CM-8"],
    hipaa=["164.308(a)(1)(ii)(A)", "164.308(a)(5)(ii)(B)"],
)


def _target_named(targets: List[ScanTarget], names) -> List[ScanTarget]:
    allowed = set(names or [t.name for t in targets])
    return [t for t in targets if t.name in allowed]


class PipAuditPlugin(ScannerPlugin):
    name = "pip_audit"
    display_name = "pip-audit (Python dependency CVEs)"
    category = Category.SCA
    required_binary = "pip-audit"

    def version(self) -> str:
        if not self._version:
            try:
                _, out, err, _ = self.exec_bounded([self.binary_path(), "--version"], timeout=90)
                self._version = (out or err or "").strip().splitlines()[0]
            except Exception:
                self._version = ""
        return self._version

    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        findings: List[Finding] = []
        for target in _target_named(targets, self.config.get("targets")):
            root = Path(os.path.expandvars(target.path))
            manifest = root / (target.manifest or "requirements.txt")
            if not manifest.exists():
                continue

            rc, stdout, stderr, timed_out = self.exec_bounded(
                [self.binary_path(), "-r", str(manifest), "--format", "json",
                 "--progress-spinner", "off"],
                timeout=int(self.config.get("timeout_seconds", 900)))
            if timed_out:
                raise RuntimeError("pip-audit exceeded its time budget")
            if not (stdout or "").strip():
                raise RuntimeError(
                    f"pip-audit produced no output (rc={rc}): {(stderr or '')[:200]}")
            payload = json.loads(stdout)

            for dep in payload.get("dependencies", []):
                name = dep.get("name", "")
                version = dep.get("version", "")
                for vuln in dep.get("vulns", []) or []:
                    findings.append(self._to_finding(name, version, vuln, target))
        return findings

    def _to_finding(self, name: str, version: str, vuln: Dict[str, Any],
                    target: ScanTarget) -> Finding:
        vid = vuln.get("id", "")
        aliases = [a for a in (vuln.get("aliases") or []) if str(a).startswith("CVE-")]
        cve = aliases[0] if aliases else (vid if str(vid).startswith("CVE-") else "")
        fixes = [str(f) for f in (vuln.get("fix_versions") or [])]
        fixed = fixes[0] if fixes else ""

        remediation = (f"Upgrade {name} from {version} to {fixed} or later."
                       if fixed else
                       f"No fixed version is published for {name} {version}. Assess "
                       f"exploitability in context, and consider a workaround, a "
                       f"vendor patch, or replacing the dependency.")
        return Finding(
            rule_id=vid or "PYSEC",
            tool="pip_audit",
            title=f"{name} {version}: {vid}{' (' + cve + ')' if cve and cve != vid else ''}",
            # pip-audit does not emit CVSS; severity is unknown rather than assumed.
            # Reporting every advisory as HIGH would be dishonest, so unfixed and
            # fixed advisories are separated: an available fix makes it actionable.
            severity=Severity.HIGH if fixed else Severity.MEDIUM,
            category=Category.SCA,
            confidence=Confidence.HIGH,
            file_path=f"{target.name}/{target.manifest or 'requirements.txt'}",
            description=(vuln.get("description") or "")[:600],
            remediation=remediation,
            effort="0.5d",
            package_name=name,
            package_version=version,
            fixed_version=fixed,
            cve=cve or vid,
            references=[f"https://osv.dev/vulnerability/{vid}"] if vid else [],
            compliance=SCA_COMPLIANCE,
            extra={"engine": "pip-audit", "aliases": aliases[:5],
                   "all_fix_versions": fixes[:5],
                   "severity_note": "pip-audit does not provide CVSS; severity is "
                                    "derived from fix availability, not impact"},
        )


class NpmAuditPlugin(ScannerPlugin):
    name = "npm_audit"
    display_name = "npm audit (JavaScript dependency CVEs)"
    category = Category.SCA
    required_binary = "npm"

    def is_available(self) -> tuple[bool, str]:
        if not (shutil.which("npm") or shutil.which("npm.cmd")):
            return False, "'npm' not found on PATH"
        return True, ""

    def binary_path(self) -> str:
        return shutil.which("npm") or shutil.which("npm.cmd") or "npm"

    def version(self) -> str:
        if not self._version:
            try:
                _, out, _, _ = self.exec_bounded([self.binary_path(), "--version"], timeout=90)
                self._version = f"npm {(out or '').strip()}"
            except Exception:
                self._version = ""
        return self._version

    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        findings: List[Finding] = []
        for target in _target_named(targets, self.config.get("targets")):
            root = Path(os.path.expandvars(target.path))
            if not (root / "package.json").exists():
                continue
            if not (root / "package-lock.json").exists():
                # npm audit needs a lockfile; without one it errors. Say so plainly
                # instead of reporting zero vulnerabilities.
                raise RuntimeError(
                    f"{target.name}: package-lock.json is missing, so npm audit cannot "
                    f"run. Generate it with `npm install --package-lock-only`.")

            rc, stdout, stderr, timed_out = self.exec_bounded(
                [self.binary_path(), "audit", "--json", "--audit-level=info"],
                cwd=root, timeout=int(self.config.get("timeout_seconds", 900)))
            if timed_out:
                raise RuntimeError("npm audit exceeded its time budget")
            if not (stdout or "").strip():
                raise RuntimeError(
                    f"npm audit produced no output (rc={rc}): {(stderr or '')[:200]}")
            payload = json.loads(stdout)

            for name, adv in (payload.get("vulnerabilities") or {}).items():
                findings.append(self._to_finding(name, adv, target))
        return findings

    def _to_finding(self, name: str, adv: Dict[str, Any], target: ScanTarget) -> Finding:
        via = adv.get("via") or []
        detail = next((v for v in via if isinstance(v, dict)), {})
        fix = adv.get("fixAvailable")
        if isinstance(fix, dict):
            fixed = str(fix.get("version") or "")
            breaking = bool(fix.get("isSemVerMajor"))
        else:
            fixed, breaking = ("" if not fix else "see `npm audit fix`"), False

        remediation = "Run `npm audit fix`." if fix else \
            "No fix is currently available; assess exploitability and consider replacing the package."
        if breaking:
            remediation = ("Fix requires a MAJOR version bump (`npm audit fix --force`) "
                           "- review for breaking changes before applying.")

        cve = ""
        for c in (detail.get("cwe") or []):
            pass
        url = detail.get("url", "")
        if isinstance(detail.get("source"), int):
            cve = f"GHSA-{detail.get('source')}"

        return Finding(
            rule_id=str(detail.get("source") or name),
            tool="npm_audit",
            title=f"{name}: {detail.get('title', 'vulnerable dependency')}"[:180],
            severity=Severity.coerce(adv.get("severity")),
            category=Category.SCA,
            confidence=Confidence.HIGH,
            file_path=f"{target.name}/package.json",
            description=(detail.get("title") or "")[:600],
            remediation=remediation,
            effort="0.5d" if not breaking else "1-2d",
            package_name=name,
            package_version=str(adv.get("range") or ""),
            fixed_version=fixed,
            cve=cve,
            references=[url] if url else [],
            compliance=SCA_COMPLIANCE,
            extra={"engine": "npm-audit", "is_direct": adv.get("isDirect"),
                   "semver_major_fix": breaking,
                   "effects": [str(e) for e in (adv.get("effects") or [])][:8]},
        )


class CycloneDxPlugin(ScannerPlugin):
    """SBOM generation. Produces an artefact, not findings.

    Registered as a plugin so it participates in the same availability/skip
    reporting as every scanner - if the SBOM is missing, the report says why.
    """

    name = "cyclonedx"
    display_name = "CycloneDX (SBOM)"
    category = Category.SCA
    required_binary = "cyclonedx-py"

    def version(self) -> str:
        if not self._version:
            try:
                _, out, err, _ = self.exec_bounded([self.binary_path(), "--version"], timeout=90)
                self._version = (out or err or "").strip().splitlines()[0]
            except Exception:
                self._version = ""
        return self._version

    def _cyclonedx_npm(self) -> str:
        """Locate the locally-installed JS SBOM generator, if present."""
        base = Path(__file__).resolve().parent.parent / "tools" / "npm" / "node_modules" / ".bin"
        for name in ("cyclonedx-npm.cmd", "cyclonedx-npm"):
            p = base / name
            if p.exists():
                return str(p)
        return shutil.which("cyclonedx-npm") or ""

    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        out_dir = Path(__file__).resolve().parent.parent / "reports" / self.project.name
        out_dir.mkdir(parents=True, exist_ok=True)
        self.sboms: Dict[str, str] = {}

        for target in targets:
            root = Path(os.path.expandvars(target.path))
            if not root.exists():
                continue
            dest = out_dir / f"sbom-{target.name}.json"

            if target.package_manager == "pip" and (root / "requirements.txt").exists():
                # cyclonedx-py v7 flags: -o/--output-file and --sv/--spec-version.
                # (--outfile/--schema-version are v4 spellings and are rejected.)
                cmd = [self.binary_path(), "requirements", str(root / "requirements.txt"),
                       "--of", "JSON", "-o", str(dest), "--sv", "1.6"]
            elif target.package_manager == "npm" and (root / "package.json").exists():
                # cyclonedx-py is Python-only, so the JS SBOM needs the companion npm
                # tool. It is installed locally under tools/npm/ (MIT, zero cost) to
                # honour "every tool install lives inside security-platform/".
                npm_bin = self._cyclonedx_npm()
                if not npm_bin:
                    self.sboms[target.name] = (
                        "not generated - @cyclonedx/cyclonedx-npm not installed. "
                        "Install with: npm install --prefix tools/npm @cyclonedx/cyclonedx-npm")
                    continue
                # `--omit` is variadic: written as "--omit dev <path>" it swallows the
                # path as a second omit value and errors. The "=" form binds it.
                cmd = [npm_bin, "--output-format", "JSON", "--output-file", str(dest),
                       "--spec-version", "1.6", "--omit=dev",
                       str(root / "package.json")]
            else:
                continue

            rc, stdout, stderr, timed_out = self.exec_bounded(cmd, timeout=600)
            if timed_out or not dest.exists():
                self.sboms[target.name] = f"FAILED: {(stderr or stdout or 'timed out').strip()[:200]}"
                continue
            self.sboms[target.name] = str(dest)

        # Record outcomes to disk. Without this the SBOM step could fail while the
        # plugin still reported "ran, 0 findings" - which is exactly what happened
        # when the v4 CLI flags were rejected by v7: a silent no-op that looked
        # successful. An artefact-producing plugin must prove it produced something.
        manifest = out_dir / "sbom-manifest.json"
        manifest.write_text(json.dumps(self.sboms, indent=2), encoding="utf-8")

        produced = [v for v in self.sboms.values() if v and not v.startswith(("FAILED", "not generated"))]
        if not produced:
            raise RuntimeError(
                "no SBOM was produced: " + "; ".join(f"{k}: {v}" for k, v in self.sboms.items()))
        return []      # SBOM is an artefact; it produces no findings
