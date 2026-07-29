#!/usr/bin/env python3
"""PHASE 2H - fold DAST/Phase-2 findings into the platform and regenerate everything.

Runs the Phase 1 scanners and the Phase 2 suites, merges both finding sets into the
same SQLite store, re-derives compliance, re-scores, re-evaluates the gate, and
rewrites every report and the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.deliverables import (write_dashboard, write_executive_summary,   # noqa: E402
                               write_technical_report)
from core.engine import SecurityEngine                                     # noqa: E402
from core.gate_engine import compute_security_score                        # noqa: E402
from core.models import Scan                                               # noqa: E402
from dast.config import DastConfig                                         # noqa: E402
from dast.phase2_runner import run_phase2                                  # noqa: E402
from dast.runner import run_dast                                           # noqa: E402


def main(skip_sast: bool = False) -> int:
    project = SecurityEngine.load_project("docuaction", ROOT)
    eng = SecurityEngine(project, ROOT, verbose=True)

    print("=" * 74)
    print("PHASE 2H - consolidated scan (Phase 1 SAST/SCA/secrets + Phase 2 DAST)")
    print("=" * 74)

    discovery = eng.discover()
    kloc = eng.kloc()

    scan: Scan
    if skip_sast:
        sid = eng.db.latest_scan_id(project.name)
        from cli import _rehydrate
        scan = _rehydrate(eng, sid)
        scan.scan_id = Scan.new_id(project.name)
        print(f"  reusing prior static findings from {sid}")
    else:
        scan = eng.scan(None)
        print(f"  phase 1 scanners: {len(scan.findings)} findings")

    # Phase 2A live API suite
    dast_res = run_dast(DastConfig.load(), verbose=False)
    print(f"  phase 2A DAST   : {len(dast_res['findings'])} findings "
          f"({dast_res['run'].to_dict()['totals']['executed']} tests executed)")

    # Phase 2B-2E static / db / live suites
    p2 = asyncio.run(run_phase2(verbose=False))
    print(f"  phase 2B-2E     : {len(p2['findings'])} findings "
          f"({p2['run'].to_dict()['totals']['executed']} tests executed)")

    scan.findings.extend(dast_res["findings"])
    scan.findings.extend(p2["findings"])
    scan.tools.extend([
        type(scan.tools[0])(name="dast_api", available=True, ran=True,
                            findings_count=len(dast_res["findings"]),
                            version="phase2a")
        if scan.tools else None,
    ] if scan.tools else [])
    scan.categories_run = sorted(set(scan.categories_run or []) | {"dast"})

    eng.db.record_scan(scan)
    scan.security_score = compute_security_score(scan.findings, kloc)

    compliance = eng.map_compliance(scan)
    sboms = eng.sbom_paths()
    eng.evaluate_gate(scan, compliance, sboms)
    eng._update_scan_summary(scan)

    written = eng.generate_reports(scan, compliance, None)
    out_dir = ROOT / "reports" / project.name
    history = eng.db.trend(project.name)
    written["executive_summary"] = write_executive_summary(
        out_dir / "executive_summary.md", scan, project, compliance, kloc, sboms)
    written["technical_report"] = write_technical_report(
        out_dir / "technical_report.md", scan, project, compliance, kloc)
    written["dashboard"] = write_dashboard(
        ROOT / "dashboard" / "index.html", scan, project, compliance, kloc, sboms,
        history)

    counts = scan.counts_by_severity()
    print()
    print(f"  TOTAL findings : {len([f for f in scan.findings if not f.suppressed])}")
    print(f"  Critical {counts['critical']}  High {counts['high']}  "
          f"Medium {counts['medium']}  Low {counts['low']}  Info {counts['info']}")
    print(f"  Security score : {scan.security_score}/100  ({kloc:.1f} KLOC)")
    print(f"  Release gate   : {scan.gate_result.value.upper()}")
    for r in scan.gate_reasons:
        print(f"    {r}")
    print("\n  Deliverables:")
    for k, v in written.items():
        print(f"    {k:<20} {v}")
    print(f"\n  Evidence: {dast_res['evidence_dir']}")
    print(f"            {p2['evidence_dir']}")
    return 0 if scan.gate_result.value != "fail" else 1


if __name__ == "__main__":
    sys.exit(main(skip_sast="--skip-sast" in sys.argv))
