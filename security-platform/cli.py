#!/usr/bin/env python3
"""AGT Security Assurance Platform - command line interface.

    python cli.py discover                 inventory the codebase
    python cli.py scan                     run every enabled scanner
    python cli.py scan --sast              SAST only
    python cli.py scan --deps              dependency / SCA only
    python cli.py scan --secrets           secrets only
    python cli.py compliance               map findings to control frameworks
    python cli.py report                   regenerate reports for the latest scan
    python cli.py gate                     evaluate the release gate
    python cli.py full                     everything, end to end
    python cli.py status                   history, open findings, MTTR

Common options:
    -p/--project NAME    project config to use (default: docuaction)
    -v/--verbose         per-plugin progress
    --format FMT         repeatable: json, markdown, csv, html

EXIT CODES  (designed for CI)
    0  success / gate PASS
    1  gate FAIL
    2  gate WARN with --strict
    3  usage or configuration error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parent
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

# A Windows console defaults to cp1252, which cannot encode many characters that
# legitimately appear in scanner output (file paths, rule messages, CVE titles).
# Without this, printing a finding can raise UnicodeEncodeError and abort the run —
# a reporting tool must never fail because of a character it was asked to display.
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.engine import SecurityEngine                       # noqa: E402
from core.gate_engine import GateEngine, score_formula_text  # noqa: E402
from core.models import Category, GateResult                 # noqa: E402

EXIT_OK, EXIT_GATE_FAIL, EXIT_GATE_WARN, EXIT_CONFIG = 0, 1, 2, 3

BAR = "=" * 74


def _hr(title: str = "") -> None:
    print(BAR if not title else f"\n{BAR}\n{title}\n{BAR}")


def _categories(args) -> list:
    """--sast/--deps/--secrets/--container select categories; none means all."""
    chosen = []
    if getattr(args, "sast", False):
        chosen.append(Category.SAST)
    if getattr(args, "deps", False):
        chosen += [Category.SCA, Category.LICENSE]
    if getattr(args, "secrets", False):
        chosen.append(Category.SECRETS)
    if getattr(args, "container", False):
        chosen.append(Category.CONTAINER)
    return chosen or None


def _engine(args) -> SecurityEngine:
    try:
        project = SecurityEngine.load_project(args.project, PLATFORM_ROOT)
    except FileNotFoundError:
        available = SecurityEngine.list_projects(PLATFORM_ROOT)
        print(f"ERROR: no project config named '{args.project}'.", file=sys.stderr)
        print(f"       available: {', '.join(available) or '(none)'}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)
    except Exception as exc:
        print(f"ERROR: could not load project '{args.project}': {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)
    return SecurityEngine(project, PLATFORM_ROOT, verbose=args.verbose)


def _print_severity(counts: dict) -> None:
    print(f"  Critical {counts.get('critical',0):>4}   High {counts.get('high',0):>4}   "
          f"Medium {counts.get('medium',0):>4}   Low {counts.get('low',0):>4}   "
          f"Info {counts.get('info',0):>4}")


def _print_tools(scan) -> None:
    print("\n  Scanner coverage:")
    if not scan.tools:
        print("    (no scanner plugins registered yet)")
        return
    for t in scan.tools:
        if t.ran:
            state = f"ran      {t.findings_count:>4} findings  {t.duration_seconds:>5.1f}s"
        elif t.error:
            state = f"ERROR    {t.error[:60]}"
        else:
            state = f"SKIPPED  {t.skipped_reason[:60]}"
        print(f"    {t.name:<22} {state}")
    skipped = scan.skipped_tools
    if skipped:
        print(f"\n  NOTE: reduced coverage - {len(skipped)} scanner(s) did not run. "
              f"Their capability is absent from this report.")


# ── commands ──────────────────────────────────────────────────────────────────

def _external_suites(args):
    """Run the DAST and/or Azure suites and return (findings, tool_statuses).

    Kept out of the plugin system deliberately: these are not file scanners. DAST
    needs a guarded live target and Azure needs cloud credentials, so neither can be
    auto-discovered and run unconditionally the way a linter can.
    """
    from core.models import ToolStatus
    findings, tools = [], []

    # --all is shorthand for --dast --azure. Kept opt-in: DAST needs a guarded live
    # target and Azure needs cloud credentials, so neither belongs in the default
    # fast path that CI runs on every push.
    run_all = getattr(args, "all", False)

    if getattr(args, "dast", False) or run_all:
        from dast.config import DastConfig
        from dast.runner import run_dast
        res = run_dast(DastConfig.load(), verbose=args.verbose)
        findings += res["findings"]
        run = res["run"]
        tools.append(ToolStatus(
            name="dast_api", available=True, ran=bool(run.executed()),
            findings_count=len(res["findings"]), version="phase2a",
            skipped_reason="" if run.executed() else (run.aborted_reason or
                                                      "no safe live target")))
        print(f"  [dast] target {run.target}: "
              f"{len(run.executed())} executed, {len(res['findings'])} findings")

    # NOT included in --all: it costs money per run. Always explicit.
    if getattr(args, "ai_review", False):
        from ai_review.ai_reviewer import run_ai_review
        res = run_ai_review(getattr(args, "files", None), verbose=args.verbose)
        if not res.get("available"):
            print(f"  [ai_review] SKIPPED: {res.get('reason')}")
            tools.append(ToolStatus(name="ai_review", available=False,
                                    skipped_reason=res.get("reason", "")))
        else:
            findings += res["findings"]
            tools.append(ToolStatus(name="ai_review", available=True, ran=True,
                                    findings_count=len(res["findings"]),
                                    version=res.get("model", "")))
            print(f"  [ai_review] {res['files']} file(s) reviewed, "
                  f"{res['finding_count']} finding(s); skipped {len(res['skipped'])}")

    if getattr(args, "azure", False) or run_all:
        import pathlib as _p
        from dast.azure.azure_scanner import AzureScanner
        from dast.results import EvidenceWriter, Outcome, TestRun
        from dast.runner import evidence_to_finding
        run = TestRun(run_id=TestRun.new_id().replace("dast_", "azure_"),
                      target="Azure subscription", target_kind="azure")
        w = EvidenceWriter(PLATFORM_ROOT / "evidence", run.run_id)
        sc = AzureScanner(run, w)
        only = getattr(args, "check", None)
        if only:
            fn = {"AZ-APP": sc.app_service, "AZ-DB": sc.database, "AZ-KV": sc.key_vault,
                  "AZ-NET": sc.network_identity, "AZ-ID": sc.network_identity,
                  "AZ-MON": sc.monitoring}.get(only.upper())
            if not fn:
                print(f"ERROR: unknown --check {only!r}; expected one of "
                      f"AZ-APP, AZ-DB, AZ-KV, AZ-NET, AZ-ID, AZ-MON", file=sys.stderr)
                raise SystemExit(EXIT_CONFIG)
            fn()
        else:
            sc.run()
        w.write_manifest(run)
        az_f = [f for f in (evidence_to_finding(ev, "azure") for ev in run.evidence) if f]
        findings += az_f
        tools.append(ToolStatus(name="azure_infra", available=True, ran=True,
                                findings_count=len(az_f), version="phase3"))
        print(f"  [azure] {len(run.evidence)} checks, {len(az_f)} findings")

    return findings, tools


def cmd_discover(args) -> int:
    eng = _engine(args)
    _hr(f"DISCOVERY - {eng.project.display_name}")
    d = eng.discover()
    for t in d["targets"]:
        mark = "OK " if t["exists"] else "MISSING"
        print(f"\n  [{mark}] {t['name']}  ->  {t['path']}")
        if not t["exists"]:
            continue
        print(f"      files {t['files_scanned']:>6}   LOC {t['total_loc']:>8}")
        for lang, n in list(t["files_by_language"].items())[:6]:
            print(f"        {lang:<12} {n:>5} files   {t['loc_by_language'].get(lang,0):>8} LOC")
        if t.get("manifest"):
            print(f"      manifest: {Path(t['manifest']).name} "
                  f"({'present' if t['manifest_exists'] else 'MISSING'})")
        if t.get("dockerfile"):
            print("      Dockerfile: present")
    print(f"\n  TOTAL: {d['totals']['files']} files, {d['totals']['loc']} LOC "
          f"across {d['totals']['targets']} target(s)")
    print(f"  Plugins registered: {', '.join(d['plugins_registered']) or '(none yet)'}")
    for w in d.get("warnings", []):
        print(f"  WARNING: {w}")
    for e in d.get("plugin_load_errors", []):
        print(f"  PLUGIN LOAD ERROR: {e}")
    return EXIT_OK


def cmd_scan(args) -> int:
    eng = _engine(args)
    cats = _categories(args)
    label = ", ".join(c.value for c in cats) if cats else "all categories"
    _hr(f"SCAN - {eng.project.display_name} ({label})")
    extra_f, extra_t = _external_suites(args)
    scan = eng.scan(cats, extra_findings=extra_f, extra_tools=extra_t)
    print(f"\n  Scan ID: {scan.scan_id}   duration {scan.duration_seconds:.1f}s")
    if scan.git_commit:
        print(f"  Commit : {scan.git_commit[:12]} ({scan.git_ref})")
    print(f"\n  Findings ({len([f for f in scan.findings if not f.suppressed])} active):")
    _print_severity(scan.counts_by_severity())
    st = scan.counts_by_status()
    print(f"\n  New {st.get('new',0)}   Existing {st.get('existing',0)}   "
          f"Reopened {st.get('reopened',0)}")
    print(f"  Security score: {scan.security_score}/100")
    _print_tools(scan)
    print(f"\n  Stored in {eng.db.db_path}")
    return EXIT_OK


def cmd_compliance(args) -> int:
    eng = _engine(args)
    _hr(f"COMPLIANCE - {eng.project.display_name}")
    scan_id = eng.db.latest_scan_id(eng.project.name)
    if not scan_id:
        print("  No scan on record. Run `python cli.py scan` first.")
        return EXIT_CONFIG
    scan = _rehydrate(eng, scan_id)
    result = eng.map_compliance(scan)
    if not result.get("available", True):
        print(f"  Compliance mapping unavailable: {result.get('reason')}")
        return EXIT_OK
    print(json.dumps(result.get("coverage", {}), indent=2))
    return EXIT_OK


def cmd_report(args) -> int:
    eng = _engine(args)
    _hr(f"REPORTS - {eng.project.display_name}")
    scan_id = eng.db.latest_scan_id(eng.project.name)
    if not scan_id:
        print("  No scan on record. Run `python cli.py scan` first.")
        return EXIT_CONFIG
    scan = _rehydrate(eng, scan_id)
    compliance = eng.map_compliance(scan)
    eng.evaluate_gate(scan, compliance)
    written = eng.generate_reports(scan, compliance, args.format or None)
    for fmt, path in written.items():
        print(f"  {fmt:<9} {path}")
    return EXIT_OK


def cmd_gate(args) -> int:
    eng = _engine(args)
    _hr(f"RELEASE GATE - {eng.project.display_name}")
    scan_id = eng.db.latest_scan_id(eng.project.name)
    if not scan_id:
        print("  No scan on record. Run `python cli.py scan` first.")
        return EXIT_CONFIG
    scan = _rehydrate(eng, scan_id)
    compliance = eng.map_compliance(scan)
    eng.evaluate_gate(scan, compliance)
    print(f"\n  Policy: {json.dumps(GateEngine.from_project(eng.project, PLATFORM_ROOT).describe())}")
    print()
    for r in scan.gate_reasons:
        print(f"  {r}")
    print(f"\n  RESULT: {scan.gate_result.value.upper()}")
    return _gate_exit(scan.gate_result, args)


def cmd_full(args) -> int:
    eng = _engine(args)
    cats = _categories(args)
    _hr(f"FULL PIPELINE - {eng.project.display_name}")
    extra_f, extra_t = _external_suites(args)
    result = eng.full(cats, args.format or None,
                      extra_findings=extra_f, extra_tools=extra_t)
    scan = result["scan"]

    d = result["discovery"]
    print(f"\n  Discovery: {d['totals']['files']} files, {d['totals']['loc']} LOC")
    print(f"\n  Findings ({len([f for f in scan.findings if not f.suppressed])} active):")
    _print_severity(scan.counts_by_severity())
    print(f"\n  Security score: {scan.security_score}/100")
    print(f"  ({score_formula_text(result.get('kloc'))})")
    _print_tools(scan)

    comp = result.get("compliance") or {}
    if comp.get("available"):
        print("\n  Compliance DETECTION coverage (capability of the ruleset,")
        print("  not a compliance attestation):")
        for k, v in (comp.get("coverage") or {}).items():
            print(f"    {k:<22} {v:.0f}%")
    else:
        print(f"\n  Compliance mapping: not available ({comp.get('reason','')})")

    print("\n  Deliverables:")
    for fmt, path in (result.get("reports") or {}).items():
        print(f"    {fmt:<20} {path}")
    for name, p in (result.get("sboms") or {}).items():
        print(f"    {'sbom:' + name:<20} {p}")
    for f in (comp.get("files") or []):
        print(f"    {'compliance':<20} {f}")

    print("\n  Release gate:")
    for r in scan.gate_reasons:
        print(f"    {r}")
    print(f"\n  RESULT: {scan.gate_result.value.upper()}")
    print("\n  Production impact: ZERO (read-only analysis; no target code executed)")
    return _gate_exit(scan.gate_result, args)


def cmd_status(args) -> int:
    eng = _engine(args)
    _hr(f"STATUS - {eng.project.display_name}")
    stats = eng.db.stats(eng.project.name)
    print(f"\n  Scans on record : {stats['total_scans']}")
    print(f"  Open findings   : {stats['open_total']}")
    if stats["open_by_severity"]:
        _print_severity(stats["open_by_severity"])
    print(f"\n  Lifecycle: {json.dumps(stats['findings_by_lifecycle'])}")
    m = stats["mttr"]
    print(f"\n  MTTR (resolved {m['resolved_count']}): "
          f"{m['mttr_days_overall'] if m['mttr_days_overall'] is not None else 'n/a'} days")
    for sev, days in (m["mttr_days_by_severity"] or {}).items():
        print(f"    {sev:<10} {days if days is not None else 'n/a'} days")
    print(f"  {m['note']}")

    hist = eng.db.scan_history(eng.project.name, limit=10)
    if hist:
        print("\n  Recent scans:")
        print(f"    {'scan_id':<34} {'score':>6} {'gate':>6}  C/H/M/L")
        for h in hist:
            c = h["counts"]
            print(f"    {h['scan_id']:<34} {h['security_score']:>6} "
                  f"{(h['gate_result'] or '-'):>6}  "
                  f"{c.get('critical',0)}/{c.get('high',0)}/{c.get('medium',0)}/{c.get('low',0)}")
    else:
        print("\n  No scans yet - run `python cli.py scan`.")
    return EXIT_OK




# ── Phase 5A/5B: finding lifecycle and delta ─────────────────────────────────

def cmd_findings(args) -> int:
    """List, suppress or reopen findings across scans."""
    eng = _engine(args)
    action = getattr(args, "action", "list")

    if action == "suppress":
        if not args.finding_id or not args.reason:
            print("ERROR: suppress needs <fingerprint> and --reason", file=sys.stderr)
            return EXIT_CONFIG
        eng.db.add_suppression(eng.project.name, args.finding_id, args.reason,
                               created_by=args.by or "cli")
        print(f"  suppressed {args.finding_id}: {args.reason}")
        print("  NOTE: suppression hides the finding from the score and the gate. "
              "It is recorded with a reason and an author so the decision stays "
              "auditable - it does not delete anything.")
        return EXIT_OK

    if action == "reopen":
        if not args.finding_id:
            print("ERROR: reopen needs <fingerprint>", file=sys.stderr)
            return EXIT_CONFIG
        with eng.db._conn() as conn:                                  # noqa: SLF001
            n = conn.execute(
                "DELETE FROM suppressions WHERE project_name=? AND fingerprint=?",
                (eng.project.name, args.finding_id)).rowcount
            conn.execute(
                "UPDATE finding_state SET status='reopened', resolved_at='' "
                "WHERE project_name=? AND fingerprint=?",
                (eng.project.name, args.finding_id))
        print(f"  reopened {args.finding_id} (suppressions removed: {n})")
        return EXIT_OK

    # list
    _hr(f"FINDINGS - {eng.project.display_name}")
    rows = eng.db.open_findings(eng.project.name)
    supp = eng.db.load_suppressions(eng.project.name)
    want = (args.status or "open").lower()
    if getattr(args, "severity", None):
        rows = [r for r in rows if (r.get("severity") or "") == args.severity]
    if getattr(args, "engine", None):
        with eng.db._conn() as conn:                                  # noqa: SLF001
            keep = {x[0] for x in conn.execute(
                "SELECT DISTINCT fingerprint FROM findings WHERE project_name=? AND tool=?",
                (eng.project.name, args.engine))}
        rows = [r for r in rows if r["fingerprint"] in keep]
    if want == "suppressed":
        rows = [r for r in rows if r["fingerprint"] in supp]
    elif want != "all":
        rows = [r for r in rows if r["fingerprint"] not in supp]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    rows.sort(key=lambda r: (order.get(r.get("severity"), 9), r.get("first_seen") or ""))
    limit = args.limit or 40
    print(f"\n  {len(rows)} finding(s) [{want}]; showing {min(limit, len(rows))}\n")
    print(f"    {'fingerprint':<18}{'sev':<10}{'status':<11}{'first seen':<21}title")
    for r in rows[:limit]:
        print(f"    {r['fingerprint']:<18}{(r.get('severity') or ''):<10}"
              f"{(r.get('status') or ''):<11}{(r.get('first_seen') or '')[:19]:<21}"
              f"{(r.get('title') or '')[:52]}")
    if supp:
        print(f"\n  {len(supp)} suppression(s) active")
    return EXIT_OK


def cmd_diff(args) -> int:
    """Delta between the two most recent scans."""
    eng = _engine(args)
    cur = eng.db.latest_scan_id(eng.project.name)
    if not cur:
        print("  No scan on record.")
        return EXIT_CONFIG
    prev = eng.db.latest_scan_id(eng.project.name, before=cur)
    d = eng.db.delta(eng.project.name, cur, prev)

    def _detail(fps):
        out = []
        for f in eng.db.findings_for_scan(cur if fps is d["introduced"] else (prev or cur)):
            if f.fingerprint in set(fps):
                out.append(f)
        return out

    if (args.format or "md") == "json":
        import json as _json
        print(_json.dumps(d, indent=2))
        return EXIT_OK

    _hr(f"DELTA - {eng.project.display_name}")
    print(f"\n  current : {d['current_scan_id']}")
    print(f"  previous: {d['previous_scan_id'] or '(none - first scan)'}")
    c = d["counts"]
    print(f"\n  NEW        {c['introduced']}")
    print(f"  RESOLVED   {c['fixed']}")
    print(f"  UNCHANGED  {c['carried_over']}")
    if not d["previous_scan_id"]:
        print("\n  Only one scan on record, so every finding reads as NEW. A delta "
              "needs two scans to mean anything.")
        return EXIT_OK
    for label, fps in (("NEW", d["introduced"]), ("RESOLVED", d["fixed"])):
        if not fps:
            continue
        print(f"\n  --- {label} ---")
        for f in _detail(fps)[:15]:
            print(f"    [{f.severity.value:<8}] {f.rule_id:<22} {f.title[:54]}")
    return EXIT_OK

# ── helpers ───────────────────────────────────────────────────────────────────

def _rehydrate(eng: SecurityEngine, scan_id: str):
    """Rebuild a Scan object from the DB so report/gate can run standalone."""
    from core.models import Scan, ToolStatus
    from core.gate_engine import compute_security_score
    hist = [h for h in eng.db.scan_history(eng.project.name, limit=200)
            if h["scan_id"] == scan_id]
    scan = Scan(scan_id=scan_id, project_name=eng.project.name)
    if hist:
        # Deliberately NOT copying started_at onto a scan that will be re-recorded
        # under a NEW id: doing so made a freshly-consolidated scan sort as older than
        # the one it replaced, so latest_scan_id() kept returning a superseded (and in
        # one case double-counted) result. The original timestamp is preserved in
        # `rehydrated_from` instead.
        scan.git_commit = hist[0].get("git_commit") or ""
        scan.duration_seconds = hist[0].get("duration_seconds") or 0.0
    scan.findings = eng.db.findings_for_scan(scan_id)
    with eng.db._conn() as conn:                                  # noqa: SLF001
        row = conn.execute("SELECT tools_json, categories_run FROM scans WHERE scan_id = ?",
                           (scan_id,)).fetchone()
    if row:
        scan.tools = [ToolStatus(**t) for t in json.loads(row["tools_json"] or "[]")]
        scan.categories_run = json.loads(row["categories_run"] or "[]")
    scan.security_score = compute_security_score(scan.findings, eng.kloc())
    return scan


def _gate_exit(result, args) -> int:
    if result == GateResult.FAIL:
        return EXIT_GATE_FAIL
    if result == GateResult.WARN and getattr(args, "strict", False):
        return EXIT_GATE_WARN
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="AGT Security Assurance Platform - SAST, secrets, SCA, SBOM, "
                    "compliance mapping and release gating.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 ok/PASS, 1 gate FAIL, 2 gate WARN (--strict), 3 config error.")
    p.add_argument("-p", "--project", default="docuaction",
                   help="project config name or path (default: docuaction)")
    p.add_argument("-v", "--verbose", action="store_true", help="per-plugin progress")
    sub = p.add_subparsers(dest="command", required=True)

    def add_cat_flags(sp):
        sp.add_argument("--sast", action="store_true", help="static analysis only")
        sp.add_argument("--deps", action="store_true", help="dependency/SCA only")
        sp.add_argument("--secrets", action="store_true", help="secrets detection only")
        sp.add_argument("--container", action="store_true", help="container/IaC only")
        sp.add_argument("--dast", action="store_true",
                        help="also run the DAST suite (guarded non-prod target only)")
        sp.add_argument("--azure", action="store_true",
                        help="also run read-only Azure infrastructure checks")
        sp.add_argument("--ai-review", dest="ai_review", action="store_true",
                        help="AI-assisted review via Claude (needs ANTHROPIC_API_KEY; "
                             "costs money - never runs automatically)")
        sp.add_argument("--files", action="append",
                        help="limit --ai-review to these files (repeatable)")
        sp.add_argument("--all", action="store_true",
                        help="run EVERYTHING: Phase 1 scanners + DAST + Azure "
                             "(slower; default is Phase 1 only)")
        sp.add_argument("--check", metavar="FAMILY",
                        help="limit --azure to one family: AZ-APP, AZ-DB, AZ-KV, "
                             "AZ-NET, AZ-ID, AZ-MON")

    def add_fmt(sp):
        sp.add_argument("--format", action="append",
                        choices=["json", "markdown", "csv", "html"],
                        help="output format (repeatable; default: all)")

    sub.add_parser("discover", help="inventory the codebase").set_defaults(func=cmd_discover)

    sp = sub.add_parser("scan", help="run scanners")
    add_cat_flags(sp); sp.set_defaults(func=cmd_scan)

    sub.add_parser("compliance", help="map findings to frameworks").set_defaults(
        func=cmd_compliance)

    sp = sub.add_parser("report", help="regenerate reports for the latest scan")
    add_fmt(sp); sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("gate", help="evaluate the release gate")
    sp.add_argument("--strict", action="store_true", help="exit 2 on WARN")
    sp.set_defaults(func=cmd_gate)

    sp = sub.add_parser("full", help="discovery + scan + compliance + gate + reports")
    add_cat_flags(sp); add_fmt(sp)
    sp.add_argument("--strict", action="store_true", help="exit 2 on WARN")
    sp.set_defaults(func=cmd_full)


    sp = sub.add_parser("findings", help="list / suppress / reopen findings")
    sp.add_argument("action", nargs="?", default="list",
                    choices=["list", "suppress", "reopen"])
    sp.add_argument("finding_id", nargs="?", help="fingerprint")
    sp.add_argument("--status", choices=["open", "suppressed", "all"], default="open")
    sp.add_argument("--reason", help="required for suppress")
    sp.add_argument("--by", help="who is suppressing")
    sp.add_argument("--limit", type=int, default=40)
    sp.add_argument("--severity", choices=["critical","high","medium","low","info"])
    sp.add_argument("--engine", help="filter by producing tool (e.g. gitleaks)")
    sp.set_defaults(func=cmd_findings)

    sp = sub.add_parser("diff", help="delta between the last two scans")
    sp.add_argument("--format", choices=["md", "json"], default="md")
    sp.set_defaults(func=cmd_diff)

    sub.add_parser("status", help="history, open findings, MTTR").set_defaults(
        func=cmd_status)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        print("\n  interrupted", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
