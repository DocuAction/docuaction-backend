"""Test result and forensic evidence models.

EVERY executed test produces an evidence record, pass or fail. A report that only
keeps failures cannot answer "did you actually test this?" six months later during an
audit - which is precisely the question an ATO package has to answer.

Evidence is written as one JSON file per test under evidence/<run_id>/, plus a
manifest. Request and response bodies are TRUNCATED and SCRUBBED: a DAST run
legitimately handles tokens and passwords, and an evidence package that captures them
verbatim is a credential store nobody intended to create.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_BODY_CHARS = 1200

# Field names whose values are replaced wholesale in stored evidence.
_SENSITIVE_KEYS = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|api[_-]?key|client[_-]?secret|"
    r"ssn|dob|date_of_birth|mrn|medical_record)")

# Value-shaped things that must never be stored even under an innocuous key.
_SENSITIVE_VALUES = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"), "[JWT REDACTED]"),
    (re.compile(r"(?i)(sk-|xoxb-|ghp_)[A-Za-z0-9_\-]{12,}"), "[API KEY REDACTED]"),
    (re.compile(r"postgres(?:ql)?://[^\s\"']*:[^\s\"'@]+@"), "postgresql://[REDACTED]@"),
]


class Outcome(str, Enum):
    PASS = "pass"            # the control behaved correctly
    FAIL = "fail"            # a security weakness was demonstrated
    WARN = "warn"            # suspicious but not conclusive
    SKIP = "skip"            # preconditions absent (no credentials, endpoint missing)
    ERROR = "error"          # the test itself failed to execute
    STUB = "stub"            # written but never executed (no target available)


def scrub(value: Any) -> Any:
    """Recursively redact sensitive keys and value shapes."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SENSITIVE_KEYS.search(str(k)) else scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        out = value
        for rx, repl in _SENSITIVE_VALUES:
            out = rx.sub(repl, out)
        return out[:MAX_BODY_CHARS]
    return value


@dataclass
class Evidence:
    """Forensic record of one executed test."""
    test_id: str
    category: str
    test_name: str
    endpoint: str = ""
    method: str = ""
    request_summary: Dict[str, Any] = field(default_factory=dict)
    response_status: Optional[int] = None
    response_summary: Dict[str, Any] = field(default_factory=dict)
    expected: str = ""
    observed: str = ""
    outcome: Outcome = Outcome.SKIP
    finding: str = ""
    severity: str = "info"
    confidence: str = "high"
    owasp: List[str] = field(default_factory=list)
    owasp_api: List[str] = field(default_factory=list)
    cwe: List[str] = field(default_factory=list)
    nist: List[str] = field(default_factory=list)
    hipaa: List[str] = field(default_factory=list)
    asvs: List[str] = field(default_factory=list)
    remediation: str = ""
    duration_ms: float = 0.0
    evidence_path: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d

    @property
    def is_finding(self) -> bool:
        return self.outcome in (Outcome.FAIL, Outcome.WARN)

    def fingerprint(self) -> str:
        raw = f"dast|{self.test_id}|{self.endpoint}|{self.method}"
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class TestRun:
    """A whole DAST execution."""
    run_id: str
    target: str
    target_kind: str
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    finished_at: str = ""
    evidence: List[Evidence] = field(default_factory=list)
    aborted_reason: str = ""

    @staticmethod
    def new_id() -> str:
        return f"dast_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    def add(self, ev: Evidence) -> Evidence:
        self.evidence.append(ev)
        return ev

    def counts(self) -> Dict[str, int]:
        out = {o.value: 0 for o in Outcome}
        for e in self.evidence:
            out[e.outcome.value] += 1
        return out

    def by_category(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for e in self.evidence:
            b = out.setdefault(e.category, {o.value: 0 for o in Outcome})
            b[e.outcome.value] += 1
        return out

    def findings(self) -> List[Evidence]:
        order = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
        return sorted([e for e in self.evidence if e.is_finding],
                      key=lambda e: -order.get(e.severity, 1))

    def executed(self) -> List[Evidence]:
        return [e for e in self.evidence
                if e.outcome not in (Outcome.STUB, Outcome.SKIP)]

    def to_dict(self) -> Dict[str, Any]:
        c = self.counts()
        return {
            "run_id": self.run_id,
            "target": self.target,
            "target_kind": self.target_kind,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "aborted_reason": self.aborted_reason,
            "totals": {
                "written": len(self.evidence),
                "executed": len(self.executed()),
                "passed": c["pass"],
                "failed": c["fail"],
                "warned": c["warn"],
                "skipped": c["skip"],
                "errored": c["error"],
                "stubbed": c["stub"],
            },
            "by_category": self.by_category(),
            "findings": [e.to_dict() for e in self.findings()],
            "evidence": [e.to_dict() for e in self.evidence],
        }


class EvidenceWriter:
    """Writes per-test evidence files plus a run manifest."""

    def __init__(self, root: Path, run_id: str):
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id

    def write(self, ev: Evidence) -> str:
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", ev.test_id)
        path = self.dir / f"{safe_id}.json"
        payload = ev.to_dict()
        payload["request_summary"] = scrub(payload.get("request_summary"))
        payload["response_summary"] = scrub(payload.get("response_summary"))
        payload["_evidence_note"] = (
            "Bodies are truncated and scrubbed: credentials, tokens and PHI-shaped "
            "values are replaced before storage so this package is not itself a "
            "secret store.")
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        ev.evidence_path = str(path)
        return str(path)

    def write_manifest(self, run: TestRun) -> str:
        path = self.dir / "manifest.json"
        path.write_text(json.dumps(run.to_dict(), indent=2, default=str),
                        encoding="utf-8")
        return str(path)
