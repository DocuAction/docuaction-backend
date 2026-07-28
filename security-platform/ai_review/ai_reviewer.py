"""Phase 2G - AI-assisted security review engine.

WHAT LEAVES THIS MACHINE
    Only code excerpts, and only after scrubbing. `_scrub()` removes assigned secret
    values, bearer tokens, JWTs, connection strings with credentials, and long
    high-entropy literals before anything is sent. Files matching NEVER_SEND are
    refused outright. This matters more than usual here: the codebase under review
    demonstrably contains live credentials in working-tree files.

WHY EXCERPTS, NOT FILES
    app/api/routes.py alone is thousands of lines. Sending whole files would be
    expensive and would bury the interesting parts. `_excerpt()` selects the regions
    most likely to contain the ten defect classes - route handlers, auth checks,
    query construction, subprocess and AI calls - and caps the total.

HONESTY CONSTRAINTS
    The model is told to report only what the excerpt shows, to mark uncertain
    findings low-confidence, and that an empty list is a valid answer. Every finding
    is tagged tool="ai_review" and carries its confidence, so an LLM opinion is never
    silently promoted to the same standing as a deterministic scanner result.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_review.config import (DEFAULT_TARGETS, MAX_FILES, MAX_SNIPPET_CHARS,
                              MAX_TOKENS, MODEL, NEVER_SEND, REQUESTS_PER_MINUTE,
                              api_key)
from ai_review.prompts import SYSTEM, user_prompt
from core.models import (Category, ComplianceMapping, Confidence, Finding, Severity)

logger = logging.getLogger("agt.ai_review")

BACKEND = Path(r"C:/Imran_Coding projects/DocuAction/backend")

# Values scrubbed before anything is transmitted.
_SCRUB = [
    # \w* after the keyword: the identifier is usually SECRET_KEY / API_KEY_ID /
    # DB_PASSWORD, not a bare 'secret'. Without it the most common real-world shape
    # slipped through - verified by the scrubber self-test.
    (re.compile(r'(?i)(\w*(?:secret|password|passwd|token|api[_-]?key|apikey)\w*'
                r'\s*[:=]\s*)["\']([^"\']{6,})["\']'), r'\1"[REDACTED]"'),
    (re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]{12,}'), 'Bearer [REDACTED]'),
    (re.compile(r'eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*'), '[JWT]'),
    (re.compile(r'(?i)(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s"\']*:'
                r'[^\s"\'@]+@'), r'\1://[REDACTED]@'),
    (re.compile(r'(?i)(sk-|xoxb-|ghp_)[A-Za-z0-9_\-]{12,}'), '[API_KEY]'),
    # Any remaining long opaque literal.
    (re.compile(r'["\'][A-Za-z0-9+/=_\-]{40,}["\']'), '"[LONG_LITERAL]"'),
]

# Lines worth sending: where the ten defect classes actually live.
_INTEREST = re.compile(
    r"(@(?:router|app)\.(?:get|post|put|patch|delete)|"
    r"def\s+\w+|Depends\(|require_role|get_current_user|guard\(|"
    r"text\(|execute\(|subprocess|eval\(|exec\(|pickle|"
    r"messages\.create|anthropic|openai|api\.openai|"
    r"HTTPException|raise\s+\w*Error|"
    r"user_id|role\s*=|is_admin|patient|phi|npi|ssn|mrn)", re.I)


def _scrub(text: str) -> str:
    out = text
    for rx, repl in _SCRUB:
        out = rx.sub(repl, out)
    return out


def _refused(path: Path) -> bool:
    import fnmatch
    n = str(path).replace("\\", "/").lower()
    return any(fnmatch.fnmatch(n, f"*{p.lower()}") for p in NEVER_SEND)


def _excerpt(path: Path) -> str:
    """Interesting regions only, scrubbed and capped."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    keep, i, n = [], 0, len(lines)
    while i < n:
        if _INTEREST.search(lines[i]):
            lo, hi = max(0, i - 2), min(n, i + 12)
            keep.append((lo, hi))
            i = hi
        else:
            i += 1
    if not keep:
        return ""
    merged: List[List[int]] = []
    for lo, hi in keep:
        if merged and lo <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    parts, total = [], 0
    for lo, hi in merged:
        block = "\n".join(f"{k+1}: {lines[k]}" for k in range(lo, hi))
        if total + len(block) > MAX_SNIPPET_CHARS:
            break
        parts.append(block)
        total += len(block)
    return _scrub("\n...\n".join(parts))


class AIReviewer:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.key = api_key()
        self._last_call = 0.0
        self.last_error = ""

    def available(self) -> tuple[bool, str]:
        if not self.key:
            return False, "ANTHROPIC_API_KEY not available"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "anthropic SDK not installed (pip install anthropic)"
        return True, ""

    def _throttle(self) -> None:
        gap = 60.0 / max(1, REQUESTS_PER_MINUTE)
        wait = gap - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _call(self, path: str, snippet: str) -> Optional[Dict[str, Any]]:
        import anthropic
        client = anthropic.Anthropic(api_key=self.key)
        self._throttle()
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
                messages=[{"role": "user", "content": user_prompt(path, snippet)}],
            )
        except Exception as exc:
            # Carry the message, not just the class: "AuthenticationError" alone gave
            # no way to tell a bad key from a bad model id. Truncated, and the key is
            # never part of an SDK error string.
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning(f"AI review call failed for {path}: {self.last_error}")
            return None
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n|\n```$", "", raw)
        try:
            return json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    return json.loads(m.group())
                except Exception:
                    pass
            logger.warning(f"AI review returned unparseable JSON for {path}")
            return None

    def review(self, files: Optional[List[str]] = None) -> Dict[str, Any]:
        ok, why = self.available()
        if not ok:
            return {"available": False, "reason": why, "findings": [], "files": 0}

        targets = [BACKEND / f for f in (files or DEFAULT_TARGETS)][:MAX_FILES]
        findings: List[Finding] = []
        reviewed, skipped = [], []

        for p in targets:
            if not p.exists():
                skipped.append({"file": str(p), "reason": "not found"})
                continue
            if _refused(p):
                skipped.append({"file": p.name, "reason": "matches NEVER_SEND"})
                continue
            snip = _excerpt(p)
            if not snip:
                skipped.append({"file": p.name, "reason": "no reviewable regions"})
                continue
            rel = str(p.relative_to(BACKEND)).replace("\\", "/")
            if self.verbose:
                print(f"  [ai_review] {rel} ({len(snip)} chars)")
            data = self._call(rel, snip)
            if data is None:
                skipped.append({"file": rel,
                                "reason": self.last_error or "unparseable response"})
                continue
            reviewed.append(rel)
            for f in (data.get("findings") or []):
                findings.append(self._to_finding(f, rel))

        return {"available": True, "model": MODEL, "files": len(reviewed),
                "reviewed": reviewed, "skipped": skipped,
                "findings": findings, "finding_count": len(findings)}

    @staticmethod
    def _to_finding(f: Dict[str, Any], rel: str) -> Finding:
        cwe = [str(c).replace("CWE-", "") for c in (f.get("cwe") or [])]
        desc = (f"{f.get('description','')}\n\n"
                f"Attack scenario: {f.get('attack_scenario','n/a')}\n"
                f"Affected: {f.get('affected','n/a')}").strip()
        rem = f.get("remediation", "")
        if f.get("remediation_code"):
            rem = f"{rem}\n\nSuggested:\n{f['remediation_code']}"
        return Finding(
            rule_id=str(f.get("id") or "AI-SEC-000"),
            tool="ai_review",
            title=str(f.get("title", ""))[:180],
            severity=Severity.coerce(f.get("severity")),
            category=Category.SAST,
            # Confidence is carried through verbatim. An LLM opinion must never be
            # presented with the same standing as a deterministic scanner result.
            confidence=Confidence.coerce(f.get("confidence", "low")),
            file_path=rel,
            description=desc,
            remediation=rem,
            compliance=ComplianceMapping(
                cwe=cwe,
                owasp_top10=[str(x) for x in (f.get("owasp") or [])],
                nist_800_53=[str(x) for x in (f.get("nist") or [])],
                hipaa=[str(x) for x in (f.get("hipaa") or [])],
                cwe_top25=any(c in {"20", "22", "78", "79", "89", "94", "200", "287",
                                    "306", "502", "798", "862", "863", "918"}
                              for c in cwe),
            ),
            extra={"engine": "ai_review", "model": MODEL,
                   "note": "LLM-generated finding - verify before acting on it"},
        )


def run_ai_review(files: Optional[List[str]] = None,
                  verbose: bool = False) -> Dict[str, Any]:
    return AIReviewer(verbose=verbose).review(files)
