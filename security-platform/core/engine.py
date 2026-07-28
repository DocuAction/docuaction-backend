"""Orchestration.

    load project config -> discovery -> plugins -> findings -> compliance
    -> reports -> release gate -> pass/fail

READ-ONLY GUARANTEE
    Nothing here writes to, imports from, or executes any code in the projects being
    scanned. Scanners are run as external processes with the target as an argument,
    and every artefact is written under security-platform/. A scan therefore has zero
    effect on the scanned application, running or not.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.findings_db import FindingsDB
from core.gate_engine import GateEngine, compute_security_score
from core.models import (Category, Finding, GateResult, Project, Scan, ScanTarget,
                         Severity, ToolStatus)
from core.plugin_manager import PluginManager
from core.report_engine import ReportEngine

PLATFORM_ROOT = Path(__file__).resolve().parent.parent

# Extension -> language, for the discovery inventory.
LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML",
    ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell", ".html": "HTML",
    ".css": "CSS", ".md": "Markdown", ".bicep": "Bicep", ".tf": "Terraform",
    ".dockerfile": "Docker",
}


class SecurityEngine:
    """The platform's entry point. One instance per project per invocation."""

    def __init__(self, project: Project, platform_root: Path = PLATFORM_ROOT,
                 verbose: bool = False):
        self.project = project
        self.root = Path(platform_root)
        self.verbose = verbose
        self.db = FindingsDB(self.root / "data" / "findings.db")
        self.plugins = PluginManager(project, verbose=verbose)
        self.reports = ReportEngine(self.root / "reports" / project.name, self.root)

    # ── config loading ───────────────────────────────────────────────────────

    @classmethod
    def load_project(cls, name_or_path: str,
                     platform_root: Path = PLATFORM_ROOT) -> Project:
        p = Path(name_or_path)
        if not p.exists():
            p = Path(platform_root) / "config" / "projects" / f"{name_or_path}.json"
        return Project.load(p)

    @classmethod
    def list_projects(cls, platform_root: Path = PLATFORM_ROOT) -> List[str]:
        d = Path(platform_root) / "config" / "projects"
        return sorted(f.stem for f in d.glob("*.json")) if d.exists() else []

    # ── discovery ────────────────────────────────────────────────────────────

    def discover(self) -> Dict[str, Any]:
        """Inventory the targets: files, languages, LOC, manifests, entry points.

        Pure filesystem read. Feeds the reports and tells the operator immediately if
        a configured path is wrong - the most common cause of a "clean" scan that
        actually scanned nothing.
        """
        out: Dict[str, Any] = {
            "project": self.project.name,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "targets": [],
            "warnings": [],
        }
        for t in self.project.enabled_targets():
            path = Path(os.path.expandvars(t.path))
            entry: Dict[str, Any] = {
                "name": t.name, "path": str(path), "exists": path.exists(),
                "language": t.language, "package_manager": t.package_manager,
            }
            if not path.exists():
                out["warnings"].append(
                    f"target '{t.name}' path does not exist: {path} - it will not be scanned")
                out["targets"].append(entry)
                continue

            by_lang: Dict[str, int] = {}
            loc_by_lang: Dict[str, int] = {}
            total_files = 0
            for f in self._walk(path):
                ext = f.suffix.lower()
                lang = LANG_BY_EXT.get(ext)
                if not lang:
                    continue
                total_files += 1
                by_lang[lang] = by_lang.get(lang, 0) + 1
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                        loc_by_lang[lang] = loc_by_lang.get(lang, 0) + sum(1 for _ in fh)
                except Exception:
                    pass

            manifest = path / t.manifest if t.manifest else None
            entry.update({
                "files_scanned": total_files,
                "files_by_language": dict(sorted(by_lang.items(), key=lambda kv: -kv[1])),
                "loc_by_language": dict(sorted(loc_by_lang.items(), key=lambda kv: -kv[1])),
                "total_loc": sum(loc_by_lang.values()),
                "manifest": str(manifest) if manifest else "",
                "manifest_exists": bool(manifest and manifest.exists()),
                "dockerfile": (path / "Dockerfile").exists(),
            })
            if manifest and not manifest.exists():
                out["warnings"].append(
                    f"target '{t.name}' declares manifest '{t.manifest}' which is missing "
                    f"- dependency scanning will be skipped for it")
            out["targets"].append(entry)

        out["totals"] = {
            "targets": len(out["targets"]),
            "files": sum(t.get("files_scanned", 0) for t in out["targets"]),
            "loc": sum(t.get("total_loc", 0) for t in out["targets"]),
        }
        self.plugins.discover()
        out["plugins_registered"] = self.plugins.available_plugin_names()
        out["plugin_load_errors"] = self.plugins.load_errors
        path = self.root / "reports" / self.project.name
        path.mkdir(parents=True, exist_ok=True)
        (path / "discovery.json").write_text(
            json.dumps(out, indent=2, default=str), encoding="utf-8")
        return out

    def _walk(self, root: Path):
        excludes = self.project.exclude_patterns
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = Path(dirpath)
            dirnames[:] = [
                d for d in dirnames
                if not self._excluded(str((rel_dir / d)).replace("\\", "/") + "/")]
            for fn in filenames:
                fp = rel_dir / fn
                if not self._excluded(str(fp).replace("\\", "/")):
                    yield fp

    def _excluded(self, path_str: str) -> bool:
        return any(fnmatch.fnmatch(path_str, pat) or fnmatch.fnmatch(path_str, f"*/{pat}")
                   for pat in self.project.exclude_patterns)

    # ── scanning ─────────────────────────────────────────────────────────────

    def scan(self, categories: Optional[List[Category]] = None,
             extra_findings: Optional[List[Finding]] = None,
             extra_tools: Optional[List[ToolStatus]] = None) -> Scan:
        """Run the selected scanners and persist the result.

        extra_findings lets the DAST and Azure suites contribute to the SAME scan
        record as the static scanners, so the score, the gate and the compliance
        matrices all see one consistent finding set instead of three.
        """
        started = time.time()
        scan = Scan(scan_id=Scan.new_id(self.project.name),
                    project_name=self.project.name)
        scan.git_ref, scan.git_commit = self._git_info()

        findings, statuses = self.plugins.run_all(
            self.project.enabled_targets(), categories)

        findings = list(findings) + list(extra_findings or [])
        scan.findings = self._disambiguate(findings)
        scan.tools = statuses + list(extra_tools or [])
        scan.categories_run = sorted({c.value for c in categories}) if categories else \
            sorted({f.category.value for f in findings} |
                   {p.category.value for p in self.plugins.select(categories)})
        scan.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        scan.duration_seconds = time.time() - started

        # Persist first: this assigns new/existing/reopened and applies suppressions,
        # and the score must exclude suppressed findings.
        self.db.record_scan(scan)
        # Pass KLOC so `scan` uses the same density model as `full`. Without it the
        # v2 formula falls back to the v1 linear model, which saturates at 0 on any
        # real codebase - `cli.py scan` reported 0.0 while `full` reported 40.1 for
        # the identical finding set.
        scan.security_score = compute_security_score(scan.findings, self.kloc())
        return scan

    @staticmethod
    def _disambiguate(findings: List[Finding]) -> List[Finding]:
        """Give colliding fingerprints a stable occurrence ordinal.

        Fingerprints deliberately exclude the line number so a finding survives code
        moving up or down the file. The direct consequence is that the SAME normalised
        snippet appearing twice in one file (say two `verify=False` calls) produces
        two findings with one identity - which collides on the finding_state primary
        key and, worse, would silently merge two distinct defects into one.

        Duplicates are ordered by (file, line) and suffixed -2, -3, ... The first
        occurrence keeps the bare fingerprint, so the common single-occurrence case is
        completely unaffected and existing history stays valid. Identity is still
        line-independent: it depends on how many times the pattern occurs and in what
        order, not on where those occurrences sit.
        """
        groups: Dict[str, List[Finding]] = {}
        for f in findings:
            groups.setdefault(f.fingerprint, []).append(f)
        for fp, items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda f: (f.file_path or "", f.line_start or 0))
            for i, f in enumerate(items[1:], start=2):
                f.fingerprint = f"{fp}-{i}"
        return findings

    # ── compliance ───────────────────────────────────────────────────────────

    def map_compliance(self, scan: Scan) -> Dict[str, Any]:
        """Delegate to the compliance mapper if it is built (Phase 1E).

        Returns a structured 'not available' result rather than raising, so the
        pipeline stays runnable while later phases are still being built.
        """
        try:
            from core.compliance import ComplianceMapper       # noqa: WPS433
        except Exception:
            return {
                "available": False,
                "reason": "compliance mapper not installed (Phase 1E)",
                "coverage": {},
                "matrices": {},
            }
        mapper = ComplianceMapper(self.root)
        return mapper.build(scan, self.project)

    # ── gate + reports ───────────────────────────────────────────────────────

    def evaluate_gate(self, scan: Scan,
                      compliance: Optional[Dict[str, Any]] = None,
                      sboms: Optional[Dict[str, str]] = None) -> Scan:
        gate = GateEngine.from_project(self.project, self.root)
        coverage = (compliance or {}).get("coverage") or {}
        result, reasons = gate.evaluate(scan, coverage, sboms or self.sbom_paths())
        scan.gate_result = result
        scan.gate_reasons = reasons
        return scan

    def sbom_paths(self) -> Dict[str, str]:
        """Read the SBOM manifest the CycloneDX plugin writes."""
        p = self.root / "reports" / self.project.name / "sbom-manifest.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def kloc(self) -> float:
        """Codebase size from the last discovery, for score normalisation."""
        p = self.root / "reports" / self.project.name / "discovery.json"
        if not p.exists():
            return 0.0
        try:
            return round(json.loads(p.read_text(encoding="utf-8"))
                         .get("totals", {}).get("loc", 0) / 1000.0, 3)
        except Exception:
            return 0.0

    def generate_reports(self, scan: Scan, compliance: Optional[Dict[str, Any]] = None,
                         formats: Optional[List[str]] = None) -> Dict[str, str]:
        extras = {
            "delta": self.db.delta(self.project.name, scan.scan_id),
            "history": self.db.trend(self.project.name),
            "stats": self.db.stats(self.project.name),
            "compliance": compliance or {},
            "gate_policy": GateEngine.from_project(self.project, self.root).describe(),
        }
        return self.reports.generate_all(scan, self.project, extras, formats)

    # ── full pipeline ────────────────────────────────────────────────────────

    def full(self, categories: Optional[List[Category]] = None,
             formats: Optional[List[str]] = None) -> Dict[str, Any]:
        """discovery -> scan -> compliance -> gate -> reports. Returns a summary."""
        from core.deliverables import (write_dashboard, write_executive_summary,
                                       write_technical_report)

        discovery = self.discover()
        scan = self.scan(categories)

        # Score is density-normalised, so it needs the codebase size that discovery
        # just measured. Recomputed here rather than inside scan().
        kloc = self.kloc()
        scan.security_score = compute_security_score(scan.findings, kloc)

        compliance = self.map_compliance(scan)
        sboms = self.sbom_paths()
        scan = self.evaluate_gate(scan, compliance, sboms)
        # Re-persist so the stored scan row carries the gate verdict and score, which
        # are only known after the scan row was first written.
        self._update_scan_summary(scan)

        written = self.generate_reports(scan, compliance, formats)

        out_dir = self.root / "reports" / self.project.name
        history = self.db.trend(self.project.name)
        try:
            written["executive_summary"] = write_executive_summary(
                out_dir / "executive_summary.md", scan, self.project, compliance,
                kloc, sboms)
            written["technical_report"] = write_technical_report(
                out_dir / "technical_report.md", scan, self.project, compliance, kloc)
            written["dashboard"] = write_dashboard(
                self.root / "dashboard" / "index.html", scan, self.project,
                compliance, kloc, sboms, history)
        except Exception as exc:
            written["deliverables_error"] = f"{type(exc).__name__}: {exc}"

        return {
            "discovery": discovery,
            "scan": scan,
            "compliance": compliance,
            "reports": written,
            "sboms": sboms,
            "kloc": kloc,
            "gate": scan.gate_result.value if scan.gate_result else None,
            "passed": scan.gate_result != GateResult.FAIL,
        }

    def _update_scan_summary(self, scan: Scan) -> None:
        """Write score/gate back onto the persisted scan row."""
        try:
            with self.db._conn() as conn:                      # noqa: SLF001
                conn.execute(
                    "UPDATE scans SET security_score = ?, gate_result = ? WHERE scan_id = ?",
                    (scan.security_score,
                     scan.gate_result.value if scan.gate_result else "", scan.scan_id))
        except Exception:
            pass

    # ── helpers ──────────────────────────────────────────────────────────────

    def _git_info(self) -> tuple[str, str]:
        """Best-effort commit identity of the FIRST enabled target.

        Wrapped in try/except and scoped with -C to the target: the platform tree
        itself is deliberately not a git repository, and must never be treated as one.
        """
        for t in self.project.enabled_targets():
            path = Path(os.path.expandvars(t.path))
            if not path.exists():
                continue
            try:
                ref = subprocess.run(["git", "-C", str(path), "rev-parse",
                                      "--abbrev-ref", "HEAD"],
                                     capture_output=True, text=True, timeout=15)
                sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                                     capture_output=True, text=True, timeout=15)
                if ref.returncode == 0 and sha.returncode == 0:
                    return ref.stdout.strip(), sha.stdout.strip()
            except Exception:
                continue
        return "", ""
