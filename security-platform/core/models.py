"""Core data models for the AGT Security Assurance Platform.

Deliberately stdlib-only (dataclasses + enums). The core must import cleanly on a
bare Python 3.10+ with no third-party packages installed, because the CLI has to be
able to *report* that a scanner is missing rather than crash on import. Optional
dependencies (Jinja2, the scanners themselves) are imported lazily by the components
that need them.

PORTABILITY CONTRACT
    Nothing in this module knows about DocuAction. A project is described entirely by
    config/projects/<name>.json. Adding FCC/CMS/NIH/IRS means adding a config file,
    not editing code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    """Ordered severity. Values are lowercase for stable JSON/DB round-tripping."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Higher = worse. Used for sorting and gate thresholds."""
        return {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}[self.value]

    @classmethod
    def coerce(cls, value: Any, default: "Severity" = None) -> "Severity":
        """Map the many severity vocabularies of the underlying tools onto ours.

        Semgrep says ERROR/WARNING/INFO, Bandit says HIGH/MEDIUM/LOW, npm audit says
        critical/high/moderate/low/info, Trivy says CRITICAL/…/UNKNOWN. Normalising
        here is what makes cross-tool severity counts meaningful.
        """
        if isinstance(value, cls):
            return value
        s = str(value or "").strip().lower()
        alias = {
            "error": "high", "warning": "medium", "warn": "medium",
            "moderate": "medium", "note": "low", "unknown": "info",
            "informational": "info", "none": "info", "negligible": "low",
            "blocker": "critical", "severe": "critical",
        }
        s = alias.get(s, s)
        try:
            return cls(s)
        except ValueError:
            return default if default is not None else cls.INFO


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def coerce(cls, value: Any) -> "Confidence":
        s = str(value or "").strip().lower()
        try:
            return cls(s)
        except ValueError:
            return cls.MEDIUM


class Category(str, Enum):
    """Which scanning discipline produced the finding. Drives CLI filters."""
    SAST = "sast"
    SECRETS = "secrets"
    SCA = "sca"              # dependency / software composition
    CONTAINER = "container"
    IAC = "iac"
    LICENSE = "license"
    DAST = "dast"            # Phase 2
    MANUAL = "manual"        # imported from the Phase 0 register


class FindingStatus(str, Enum):
    """Lifecycle across scans, computed by findings_db against the prior scan."""
    NEW = "new"
    EXISTING = "existing"
    RESOLVED = "resolved"
    REOPENED = "reopened"


class GateResult(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Compliance ────────────────────────────────────────────────────────────────

@dataclass
class ComplianceMapping:
    """Control references attached to a finding.

    Every field is a list because one finding routinely maps to several controls
    (e.g. hardcoded secret -> CWE-798 + OWASP A02 + NIST IA-5 + HIPAA §164.312(a)(2)(i)).
    Kept as plain strings so a new framework needs no schema change.
    """
    cwe: List[str] = field(default_factory=list)
    owasp_top10: List[str] = field(default_factory=list)        # e.g. "A01:2021"
    owasp_api_top10: List[str] = field(default_factory=list)     # e.g. "API1:2023"
    owasp_asvs: List[str] = field(default_factory=list)          # e.g. "V2.1.1"
    nist_800_53: List[str] = field(default_factory=list)         # e.g. "AC-3"
    hipaa: List[str] = field(default_factory=list)               # e.g. "164.312(a)(1)"
    cwe_top25: bool = False

    def is_empty(self) -> bool:
        return not any([self.cwe, self.owasp_top10, self.owasp_api_top10,
                        self.owasp_asvs, self.nist_800_53, self.hipaa])

    def merge(self, other: "ComplianceMapping") -> "ComplianceMapping":
        """Union of two mappings, order-preserving and de-duplicated."""
        def u(a, b):
            out = list(a)
            for x in b:
                if x not in out:
                    out.append(x)
            return out
        return ComplianceMapping(
            cwe=u(self.cwe, other.cwe),
            owasp_top10=u(self.owasp_top10, other.owasp_top10),
            owasp_api_top10=u(self.owasp_api_top10, other.owasp_api_top10),
            owasp_asvs=u(self.owasp_asvs, other.owasp_asvs),
            nist_800_53=u(self.nist_800_53, other.nist_800_53),
            hipaa=u(self.hipaa, other.hipaa),
            cwe_top25=self.cwe_top25 or other.cwe_top25,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ComplianceMapping":
        d = d or {}
        return cls(
            cwe=list(d.get("cwe") or []),
            owasp_top10=list(d.get("owasp_top10") or []),
            owasp_api_top10=list(d.get("owasp_api_top10") or []),
            owasp_asvs=list(d.get("owasp_asvs") or []),
            nist_800_53=list(d.get("nist_800_53") or []),
            hipaa=list(d.get("hipaa") or []),
            cwe_top25=bool(d.get("cwe_top25", False)),
        )


# ── Finding ───────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """One security finding, tool-agnostic.

    Field set is a superset of the Phase 0 manual register (ID / severity / title /
    file:line / OWASP / CWE / NIST / remediation / effort) so hand-written findings
    and scanner output live in the same table and the same reports.
    """
    # identity / provenance
    rule_id: str
    tool: str
    title: str
    severity: Severity = Severity.INFO
    category: Category = Category.SAST
    confidence: Confidence = Confidence.MEDIUM

    # location
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    code_snippet: str = ""

    # content
    description: str = ""
    remediation: str = ""
    effort: str = ""
    references: List[str] = field(default_factory=list)

    # dependency findings
    package_name: str = ""
    package_version: str = ""
    fixed_version: str = ""
    cve: str = ""

    compliance: ComplianceMapping = field(default_factory=ComplianceMapping)

    # lifecycle (populated by findings_db, not by plugins)
    fingerprint: str = ""
    status: FindingStatus = FindingStatus.NEW
    first_seen: str = ""
    last_seen: str = ""
    resolved_at: str = ""

    # triage
    suppressed: bool = False
    suppression_reason: str = ""

    extra: Dict[str, Any] = field(default_factory=dict)

    # Volatile parts of a snippet that must not change identity between scans.
    _NORMALISE = re.compile(r"\s+")

    def __post_init__(self) -> None:
        self.severity = Severity.coerce(self.severity)
        self.confidence = Confidence.coerce(self.confidence)
        if not isinstance(self.category, Category):
            try:
                self.category = Category(str(self.category).lower())
            except ValueError:
                self.category = Category.SAST
        if not isinstance(self.status, FindingStatus):
            try:
                self.status = FindingStatus(str(self.status).lower())
            except ValueError:
                self.status = FindingStatus.NEW
        if not isinstance(self.compliance, ComplianceMapping):
            self.compliance = ComplianceMapping.from_dict(self.compliance)
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    def compute_fingerprint(self) -> str:
        """Stable identity across scans.

        Deliberately EXCLUDES the line number. Line numbers drift whenever anything
        above the finding is edited; including them would flag half the codebase as
        "new" after a formatting change and destroy the MTTR/trend numbers. Identity
        is (tool, rule, file, normalised snippet) — for dependency findings the
        snippet slot is the package identity instead.
        """
        if self.category in (Category.SCA, Category.CONTAINER, Category.LICENSE):
            body = f"{self.package_name}@{self.package_version}|{self.cve}"
        else:
            body = self._NORMALISE.sub(" ", (self.code_snippet or "").strip())[:200]
        path = (self.file_path or "").replace("\\", "/").lstrip("./")
        raw = f"{self.tool}|{self.rule_id}|{path}|{body}"
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]

    @property
    def location(self) -> str:
        if not self.file_path:
            return self.package_name or "-"
        return f"{self.file_path}:{self.line_start}" if self.line_start else self.file_path

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["category"] = self.category.value
        d["confidence"] = self.confidence.value
        d["status"] = self.status.value
        d["location"] = self.location
        d.pop("_NORMALISE", None)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Finding":
        known = {f for f in cls.__dataclass_fields__ if not f.startswith("_")}
        payload = {k: v for k, v in d.items() if k in known}
        payload["compliance"] = ComplianceMapping.from_dict(d.get("compliance"))
        return cls(**payload)


# ── Scan ──────────────────────────────────────────────────────────────────────

@dataclass
class ToolStatus:
    """Per-plugin outcome. `skipped` is a first-class result, never a failure.

    The licensing policy requires that a missing scanner degrades the run instead of
    breaking it, and that the report says plainly which capability was lost.
    """
    name: str
    available: bool = False
    ran: bool = False
    skipped_reason: str = ""
    error: str = ""
    findings_count: int = 0
    duration_seconds: float = 0.0
    version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Scan:
    """One execution of the platform against one project."""
    scan_id: str
    project_name: str
    started_at: str = field(default_factory=utcnow)
    finished_at: str = ""
    duration_seconds: float = 0.0
    git_ref: str = ""
    git_commit: str = ""
    findings: List[Finding] = field(default_factory=list)
    tools: List[ToolStatus] = field(default_factory=list)
    categories_run: List[str] = field(default_factory=list)
    security_score: float = 0.0
    gate_result: Optional[GateResult] = None
    gate_reasons: List[str] = field(default_factory=list)

    @staticmethod
    def new_id(project_name: str) -> str:
        return f"{project_name}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    def counts_by_severity(self, include_suppressed: bool = False) -> Dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            if f.suppressed and not include_suppressed:
                continue
            out[f.severity.value] += 1
        return out

    def counts_by_status(self) -> Dict[str, int]:
        out = {s.value: 0 for s in FindingStatus}
        for f in self.findings:
            out[f.status.value] += 1
        return out

    def counts_by_category(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.findings:
            if f.suppressed:
                continue
            out[f.category.value] = out.get(f.category.value, 0) + 1
        return out

    @property
    def skipped_tools(self) -> List[ToolStatus]:
        return [t for t in self.tools if not t.ran]

    def to_dict(self, include_findings: bool = True) -> Dict[str, Any]:
        d = {
            "scan_id": self.scan_id,
            "project_name": self.project_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "git_ref": self.git_ref,
            "git_commit": self.git_commit,
            "categories_run": self.categories_run,
            "security_score": self.security_score,
            "gate_result": self.gate_result.value if self.gate_result else None,
            "gate_reasons": self.gate_reasons,
            "counts": {
                "by_severity": self.counts_by_severity(),
                "by_status": self.counts_by_status(),
                "by_category": self.counts_by_category(),
                "total": len([f for f in self.findings if not f.suppressed]),
                "suppressed": len([f for f in self.findings if f.suppressed]),
            },
            "tools": [t.to_dict() for t in self.tools],
        }
        if include_findings:
            d["findings"] = [f.to_dict() for f in self.findings]
        return d


# ── Project ───────────────────────────────────────────────────────────────────

@dataclass
class ScanTarget:
    """One scannable tree inside a project (e.g. backend, frontend)."""
    name: str
    path: str
    language: str = ""
    package_manager: str = ""       # pip | npm | none
    manifest: str = ""              # requirements.txt | package.json
    enabled: bool = True

    def resolved(self, base: Path) -> Path:
        p = Path(os.path.expandvars(self.path))
        return p if p.is_absolute() else (base / p)


@dataclass
class Project:
    """Everything the platform needs to know about one application.

    This is the whole portability story: a new AGT application is a new JSON file.
    """
    name: str
    display_name: str = ""
    description: str = ""
    targets: List[ScanTarget] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    plugins: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gate_policy: Dict[str, Any] = field(default_factory=dict)
    compliance_profiles: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    config_path: str = ""

    DEFAULT_EXCLUDES = [
        "**/node_modules/**", "**/.git/**", "**/__pycache__/**", "**/.venv/**",
        "**/venv/**", "**/dist/**", "**/build/**", "**/.next/**", "**/pydeps/**",
        "**/*.min.js", "**/coverage/**", "**/.pytest_cache/**", "**/site-packages/**",
    ]

    @classmethod
    def load(cls, path: Path) -> "Project":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Project config not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)

        targets = [ScanTarget(**t) for t in raw.get("targets", [])]
        excludes = list(raw.get("exclude_patterns") or [])
        for d in cls.DEFAULT_EXCLUDES:
            if d not in excludes:
                excludes.append(d)

        return cls(
            name=raw["name"],
            display_name=raw.get("display_name", raw["name"]),
            description=raw.get("description", ""),
            targets=targets,
            exclude_patterns=excludes,
            plugins=raw.get("plugins", {}) or {},
            gate_policy=raw.get("gate_policy", {}) or {},
            compliance_profiles=raw.get("compliance_profiles", []) or [],
            metadata=raw.get("metadata", {}) or {},
            config_path=str(path),
        )

    def enabled_targets(self) -> List[ScanTarget]:
        return [t for t in self.targets if t.enabled]

    def plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        return self.plugins.get(plugin_name, {}) or {}

    def plugin_enabled(self, plugin_name: str) -> bool:
        """Unlisted plugins default to enabled, so adding a plugin doesn't require
        touching every existing project config."""
        return bool(self.plugin_config(plugin_name).get("enabled", True))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "targets": [asdict(t) for t in self.targets],
            "exclude_patterns": self.exclude_patterns,
            "plugins": self.plugins,
            "gate_policy": self.gate_policy,
            "compliance_profiles": self.compliance_profiles,
            "metadata": self.metadata,
            "config_path": self.config_path,
        }
