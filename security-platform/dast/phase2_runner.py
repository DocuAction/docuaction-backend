"""Phase 2 orchestration: static + database + live suites in one run.

Three execution modes coexist and are labelled as such in every record:
  LIVE    - HTTP against a guarded non-production target
  STATIC  - source analysis (no target needed)
  LOCAL-DB- read-only queries against a local database

Keeping them distinguishable is the point: a static PASS and a live PASS are
different kinds of evidence, and an audit needs to know which it is looking at.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dast.api_tester import APISecurityTester
from dast.config import DastConfig
from dast.results import EvidenceWriter, Outcome, TestRun
from dast.runner import evidence_to_finding, select_target
from dast.static_base import StaticTester

PLATFORM_ROOT = Path(__file__).resolve().parent.parent

STATIC_SUITES = [
    ("dast.tefca.entity_tester", "EntityTester"),
    ("dast.tefca.qhin_tester", "QhinTester"),
    ("dast.tefca.workflow_tester", "WorkflowTester"),
    ("dast.tefca.verification_tester", "VerificationTester"),
    ("dast.tefca.import_tester", "ImportTester"),
    ("dast.tefca.audit_tester", "AuditTester"),
    ("dast.fhir.identifier_tester", "IdentifierTester"),
    ("dast.fhir.bundle_tester", "BundleTester"),
    ("dast.fhir.resource_tester", "ResourceTester"),
    ("dast.fhir.smart_tester", "SmartTester"),
    ("dast.database.integrity_tester", "IntegrityTester"),
]

LIVE_SUITES = [
    ("dast.bulletin.bulletin_tester", "BulletinTester"),
    ("dast.performance.perf_tester", "PerfTester"),
]


async def run_phase2(cfg: Optional[DastConfig] = None,
                     verbose: bool = False) -> Dict[str, Any]:
    cfg = cfg or DastConfig.load()
    cfg.validate()

    run = TestRun(run_id="phase2_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"),
                  target="(mixed)", target_kind="static+db+live")
    writer = EvidenceWriter(PLATFORM_ROOT / cfg.evidence_dir, run.run_id)

    # ── static + local-db suites (never need a target) ───────────────────────
    st = StaticTester(run, writer)
    for mod_name, cls_name in STATIC_SUITES:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            getattr(mod, cls_name)(st).run()
            if verbose:
                print(f"  [phase2] {cls_name}: done")
        except Exception as exc:
            st.record(f"{cls_name}-SUITE", "suite_error", f"{cls_name} execution",
                      outcome=Outcome.ERROR,
                      finding=f"{type(exc).__name__}: {exc}", severity="info",
                      notes="Suite failed to complete; its tests are not represented.")
            if verbose:
                print(f"  [phase2] {cls_name}: ERROR {exc}")

    # ── live suites ──────────────────────────────────────────────────────────
    target, kind, log = await select_target(cfg)
    if target:
        run.target = target
        run.target_kind = f"static+db+live({kind})"
        tester = APISecurityTester(target, cfg, run=run,
                                   evidence_root=PLATFORM_ROOT / cfg.evidence_dir)
        tester.writer = writer          # one evidence directory for the whole phase
        for mod_name, cls_name in LIVE_SUITES:
            try:
                mod = __import__(mod_name, fromlist=[cls_name])
                await getattr(mod, cls_name)(tester).run()
                if verbose:
                    print(f"  [phase2] {cls_name}: done (live)")
            except Exception as exc:
                tester.generate_evidence(
                    f"{cls_name}-SUITE", "suite_error", f"{cls_name} execution",
                    outcome=Outcome.ERROR, severity="info",
                    finding=f"{type(exc).__name__}: {exc}")
        await tester.aclose()
    else:
        for mod_name, cls_name in LIVE_SUITES:
            st.stub(f"{cls_name}-ALL", "live", f"{cls_name} suite",
                    "no safe live target reachable: " + "; ".join(log))

    run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    writer.write_manifest(run)
    findings = [f for f in (evidence_to_finding(e, run.target) for e in run.evidence) if f]
    return {"run": run, "findings": findings, "target_log": log,
            "evidence_dir": str(writer.dir)}


def main(verbose: bool = True) -> Dict[str, Any]:
    return asyncio.run(run_phase2(verbose=verbose))
