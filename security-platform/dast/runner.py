"""DAST orchestration: pick a safe target, run the selected suites, convert evidence
into platform Findings, write the run report.

TARGET SELECTION
    localhost first, then the configured dev URL, and NOTHING else. If neither
    responds, every test is written out as STUB so the report distinguishes "tested
    and passed" from "never executed" - conflating those is how a scan comes to look
    like assurance it never provided.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.models import (Category, ComplianceMapping, Confidence, Finding, Severity)
from dast.api_tester import APISecurityTester
from dast.config import DastConfig, ProductionTargetError, assert_safe_target
from dast.results import Evidence, Outcome, TestRun

PLATFORM_ROOT = Path(__file__).resolve().parent.parent

SUITES = {
    "auth": ("dast.auth_tester", "AuthTester"),
    "authz": ("dast.authz_tester", "AuthzTester"),
    "jwt": ("dast.jwt_tester", "JwtTester"),
    "injection": ("dast.injection_tester", "InjectionTester"),
    "headers": ("dast.header_tester", "HeaderTester"),
    "cors": ("dast.cors_tester", "CorsTester"),
    "rate_limit": ("dast.rate_limit_tester", "RateLimitTester"),
    "upload": ("dast.file_upload_tester", "FileUploadTester"),
}


async def probe_target(url: str, timeout: float = 12.0) -> bool:
    """Cheap liveness check. Guarded like every other request."""
    try:
        assert_safe_target(url)
    except ProductionTargetError:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url.rstrip("/") + "/health")
            return r.status_code < 500
    except Exception:
        return False


async def select_target(cfg: DastConfig) -> Tuple[str, str, List[str]]:
    """Return (url, kind, log). Empty url means no safe live target."""
    log: List[str] = []
    for kind, url in cfg.candidate_targets():
        try:
            assert_safe_target(url, cfg.never_test)
        except ProductionTargetError as exc:
            log.append(f"{url}: REFUSED - {exc}")
            continue
        alive = await probe_target(url, cfg.timeout_seconds)
        log.append(f"{url}: {'reachable' if alive else 'unreachable'}")
        if alive:
            return url, kind, log
    return "", "", log


SEV_MAP = {"critical": Severity.CRITICAL, "high": Severity.HIGH,
           "medium": Severity.MEDIUM, "low": Severity.LOW, "info": Severity.INFO}


def evidence_to_finding(ev: Evidence, target: str) -> Optional[Finding]:
    """Convert a failed/warned test into a platform Finding. Passes are evidence
    only - a passing control is not a finding."""
    if not ev.is_finding:
        return None
    return Finding(
        rule_id=ev.test_id,
        tool="dast",
        title=ev.test_name[:180],
        severity=SEV_MAP.get(ev.severity, Severity.INFO),
        category=Category.DAST,
        confidence=Confidence.coerce(ev.confidence),
        file_path=f"{ev.method} {ev.endpoint}".strip() or target,
        description=(f"{ev.finding}\n\nExpected: {ev.expected}\nObserved: {ev.observed}"
                     + (f"\nNote: {ev.notes}" if ev.notes else "")).strip(),
        remediation=ev.remediation,
        code_snippet=f"[{ev.outcome.value.upper()}] {ev.observed}"[:280],
        compliance=ComplianceMapping(
            cwe=list(ev.cwe), owasp_top10=list(ev.owasp),
            owasp_api_top10=list(ev.owasp_api), owasp_asvs=list(ev.asvs),
            nist_800_53=list(ev.nist), hipaa=list(ev.hipaa),
            cwe_top25=any(c in {"20", "22", "77", "78", "79", "89", "94", "125", "190",
                                "200", "269", "287", "306", "352", "362", "416", "434",
                                "476", "502", "787", "798", "862", "863", "918"}
                          for c in ev.cwe),
        ),
        extra={"engine": "dast", "outcome": ev.outcome.value,
               "evidence_path": ev.evidence_path, "target": target,
               "response_status": ev.response_status},
    )


class DastRunner:
    def __init__(self, cfg: Optional[DastConfig] = None, suites: Optional[List[str]] = None,
                 verbose: bool = False):
        self.cfg = cfg or DastConfig.load()
        self.suites = [s for s in (suites or list(SUITES)) if s in SUITES]
        self.verbose = verbose

    async def run(self) -> Dict[str, Any]:
        # Refuse the whole run if ANY configured target is unsafe, before probing.
        self.cfg.validate()

        target, kind, log = await select_target(self.cfg)
        run = TestRun(run_id=TestRun.new_id(), target=target or "(none)",
                      target_kind=kind or "none")

        if not target:
            run.aborted_reason = ("no safe live target: " + "; ".join(log))
            self._write_stubs(run)
            return self._finish(run, log, [])

        self.cfg.target_kind = kind
        tester = APISecurityTester(target, self.cfg, run=run,
                                  evidence_root=PLATFORM_ROOT / self.cfg.evidence_dir)
        if self.cfg.credentials:
            for role, creds in self.cfg.credentials.items():
                tok = creds.get("token")
                if tok:
                    tester.auth_tokens[role] = tok

        for name in self.suites:
            mod_name, cls_name = SUITES[name]
            try:
                mod = __import__(mod_name, fromlist=[cls_name])
                suite = getattr(mod, cls_name)(tester)
                await suite.run()
                if self.verbose:
                    print(f"  [dast] {name}: done")
            except Exception as exc:
                tester.generate_evidence(
                    f"{name.upper()}-SUITE", name, f"{name} suite execution",
                    outcome=Outcome.ERROR,
                    finding=f"Suite raised {type(exc).__name__}: {exc}",
                    severity="info",
                    notes="The suite failed to complete; its tests are not represented.")
                if self.verbose:
                    print(f"  [dast] {name}: ERROR {exc}")

        await tester.aclose()
        findings = [f for f in (evidence_to_finding(e, target) for e in run.evidence) if f]
        return self._finish(run, log, findings, tester.request_count)

    def _write_stubs(self, run: TestRun) -> None:
        """Register every planned test as STUB when no target is available."""
        from dast.results import EvidenceWriter
        writer = EvidenceWriter(PLATFORM_ROOT / self.cfg.evidence_dir, run.run_id)
        planned = {
            "auth": 20, "authz": 16, "jwt": 13, "injection": 14,
            "headers": 8, "cors": 6, "rate_limit": 5, "upload": 6,
        }
        for suite in self.suites:
            for i in range(1, planned.get(suite, 1) + 1):
                ev = Evidence(
                    test_id=f"{suite.upper()}-STUB-{i:03d}", category=suite,
                    test_name=f"{suite} test {i} (not executed)",
                    outcome=Outcome.STUB,
                    notes="No safe live target was reachable. The test is implemented "
                          "and will execute when localhost:8000 or the dev URL is up.")
                writer.write(ev)
                run.add(ev)

    def _finish(self, run: TestRun, log: List[str], findings: List[Finding],
                requests: int = 0) -> Dict[str, Any]:
        from datetime import datetime, timezone
        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        writer_dir = PLATFORM_ROOT / self.cfg.evidence_dir / run.run_id
        writer_dir.mkdir(parents=True, exist_ok=True)
        (writer_dir / "manifest.json").write_text(
            json.dumps(run.to_dict(), indent=2, default=str), encoding="utf-8")
        return {
            "run": run,
            "findings": findings,
            "target_log": log,
            "requests_sent": requests,
            "evidence_dir": str(writer_dir),
        }


def run_dast(cfg: Optional[DastConfig] = None, suites: Optional[List[str]] = None,
             verbose: bool = False) -> Dict[str, Any]:
    return asyncio.run(DastRunner(cfg, suites, verbose).run())
