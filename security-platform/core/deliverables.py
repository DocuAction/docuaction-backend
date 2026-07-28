"""PHASE 1F - executive summary, technical report, and the local dashboard.

Separate from report_engine.py, which owns the raw per-scan formats (JSON/CSV/MD/
HTML). These are the human-facing deliverables: one page for a decision-maker, a
full technical write-up for engineers, and a self-contained dashboard.

The dashboard has NO external dependencies - no CDN, no fonts, no chart library.
Charts are hand-drawn inline SVG. It must open from the filesystem on a machine with
no network, because that is where a security report is often read.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.gate_engine import (DENSITY_HALF_POINT, SCORE_MODEL_VERSION, raw_penalty,
                              score_formula_text)
from core.models import Finding, GateResult, Scan, Severity

SEV_ORDER = ["critical", "high", "medium", "low", "info"]
SEV_COLOUR = {"critical": "#b3121f", "high": "#d9480f", "medium": "#b8860b",
              "low": "#2b6cb0", "info": "#4a5568"}


def _rank(f: Finding) -> tuple:
    return (-f.severity.rank, f.file_path or "", f.line_start or 0)


def top_risks(scan: Scan, n: int = 5) -> List[Dict[str, Any]]:
    """Group by rule so 'the same problem in 72 places' is one risk, not 72."""
    active = [f for f in scan.findings if not f.suppressed]
    groups: Dict[str, List[Finding]] = {}
    for f in active:
        groups.setdefault(f.rule_id, []).append(f)
    ranked = sorted(
        groups.items(),
        key=lambda kv: (-max(x.severity.rank for x in kv[1]), -len(kv[1])))
    out = []
    for rule, items in ranked[:n]:
        worst = max(items, key=lambda x: x.severity.rank)
        out.append({
            "rule_id": rule, "title": worst.title, "severity": worst.severity.value,
            "count": len(items), "tool": worst.tool,
            "example": worst.location, "remediation": worst.remediation,
            "files": len({i.file_path for i in items}),
        })
    return out


def recommendation(scan: Scan, counts: Dict[str, int]) -> tuple[str, str]:
    """(verdict, justification) - Deploy / Remediate / Block."""
    if scan.gate_result == GateResult.FAIL and counts.get("critical", 0):
        return ("BLOCK",
                f"{counts['critical']} Critical finding(s) must be resolved before "
                f"release. Criticals here are hardcoded credentials and unsafe "
                f"deserialization - both are directly exploitable and neither is "
                f"mitigated by configuration.")
    if scan.gate_result == GateResult.FAIL:
        return ("REMEDIATE",
                "The release gate failed on policy thresholds other than Critical "
                "findings. The listed items are actionable and mostly have published "
                "fixes; re-run the gate after addressing them.")
    if scan.gate_result == GateResult.WARN:
        return ("REMEDIATE",
                "The gate passed its blocking rules but raised warnings - most "
                "importantly reduced scanner coverage. Treat the result as "
                "provisional until full coverage is achieved.")
    return ("DEPLOY",
            "No blocking policy rule was violated. Residual findings should still be "
            "tracked and burned down.")


# ── Executive summary ─────────────────────────────────────────────────────────

def write_executive_summary(path: Path, scan: Scan, project,
                            compliance: Dict[str, Any], kloc: float,
                            sboms: Dict[str, str]) -> str:
    counts = scan.counts_by_severity()
    active = len([f for f in scan.findings if not f.suppressed])
    verdict, why = recommendation(scan, counts)
    cov = (compliance or {}).get("coverage") or {}
    risks = top_risks(scan, 5)

    L: List[str] = []
    a = L.append
    a(f"# Security Assessment - Executive Summary")
    a("")
    a(f"**{getattr(project, 'display_name', scan.project_name)}** · "
      f"scan `{scan.scan_id}` · {scan.started_at[:10]}")
    a("")
    a("---")
    a("")
    a(f"## Recommendation: **{verdict}**")
    a("")
    a(why)
    a("")
    a("## Security posture")
    a("")
    a("| Metric | Value |")
    a("|---|---|")
    a(f"| **Security score** | **{scan.security_score}/100** |")
    a(f"| Release gate | **{(scan.gate_result.value.upper() if scan.gate_result else 'N/A')}** |")
    a(f"| Total active findings | {active} |")
    a(f"| Codebase size | {kloc:,.1f} KLOC |")
    a(f"| Finding density | {raw_penalty(scan.findings)/kloc:.2f} penalty points / KLOC |" if kloc else "| Finding density | n/a |")
    a(f"| Scanners run | {len([t for t in scan.tools if t.ran])} of {len(scan.tools)} |")
    a("")
    a("## Findings by severity")
    a("")
    a("| Critical | High | Medium | Low |")
    a("|--:|--:|--:|--:|")
    a(f"| **{counts['critical']}** | {counts['high']} | {counts['medium']} | {counts['low']} |")
    a("")
    a("## Top 5 risks")
    a("")
    a("| # | Severity | Risk | Occurrences | Example |")
    a("|--:|---|---|--:|---|")
    for i, r in enumerate(risks, 1):
        a(f"| {i} | **{r['severity'].title()}** | {r['title'][:80]} | {r['count']} | "
          f"`{r['example']}` |")
    a("")
    a("## Compliance posture")
    a("")
    a("Detection coverage - the share of each framework this platform's ruleset can "
      "detect. It is an assurance statement about the tooling, not a compliance "
      "attestation.")
    a("")
    a("| Framework | Detection coverage |")
    a("|---|--:|")
    for k, label in (("owasp_top10", "OWASP Top 10 (2021)"),
                     ("owasp_api_top10", "OWASP API Top 10 (2023)"),
                     ("nist_800_53", "NIST SP 800-53 Rev. 5"),
                     ("hipaa", "HIPAA Technical Safeguards"),
                     ("cwe_top25", "CWE Top 25")):
        if k in cov:
            a(f"| {label} | {cov[k]:.0f}% |")
    a("")
    m = (compliance or {}).get("matrices") or {}
    if m.get("hipaa"):
        h = m["hipaa"]
        a(f"HIPAA: **{h['safeguards_with_findings']} of {h['safeguards_total']}** "
          f"technical safeguards have findings.")
        a("")
    a("## Supply chain")
    a("")
    for name, p in (sboms or {}).items():
        ok = p and Path(str(p)).exists()
        a(f"- **{name}**: {'SBOM generated' if ok else 'SBOM NOT generated'}"
          f"{' - `' + Path(str(p)).name + '`' if ok else ''}")
    a("")
    skipped = scan.skipped_tools
    if skipped:
        a("## Coverage limitation")
        a("")
        a(f"**{len(skipped)} scanner(s) did not run:** "
          f"{', '.join(t.name for t in skipped)}. Their capability is absent from this "
          f"assessment, so the finding count is a floor, not a total.")
        a("")
    a("---")
    a("")
    a(f"*Score model {SCORE_MODEL_VERSION}. {score_formula_text(kloc)}*")
    a("")
    a("*A control with no findings is not thereby compliant: an automated scan that "
      "finds nothing is not evidence that a control is satisfied.*")
    a("")
    path.write_text("\n".join(L), encoding="utf-8")
    return str(path)


# ── Technical report ──────────────────────────────────────────────────────────

def write_technical_report(path: Path, scan: Scan, project,
                           compliance: Dict[str, Any], kloc: float) -> str:
    active = sorted([f for f in scan.findings if not f.suppressed], key=_rank)
    counts = scan.counts_by_severity()

    L: List[str] = []
    a = L.append
    a("# Security Assessment - Technical Report")
    a("")
    a(f"**Project:** {getattr(project, 'display_name', scan.project_name)}  ")
    a(f"**Scan:** `{scan.scan_id}`  ")
    a(f"**Commit:** `{scan.git_commit[:12] or 'n/a'}` ({scan.git_ref or 'n/a'})  ")
    a(f"**Duration:** {scan.duration_seconds:.1f}s  ")
    a(f"**Score:** {scan.security_score}/100  ")
    a("")
    a("## Scanner coverage")
    a("")
    a("| Scanner | Status | Version | Findings | Duration | Note |")
    a("|---|---|---|--:|--:|---|")
    for t in scan.tools:
        st = "ran" if t.ran else ("ERROR" if t.error else "SKIPPED")
        a(f"| {t.name} | {st} | {t.version or '-'} | {t.findings_count} | "
          f"{t.duration_seconds:.1f}s | {(t.error or t.skipped_reason or '')[:110]} |")
    a("")
    a("## Summary")
    a("")
    a("| Severity | Count |")
    a("|---|--:|")
    for s in SEV_ORDER:
        a(f"| {s.title()} | {counts[s]} |")
    a(f"| **Total** | **{len(active)}** |")
    a("")
    by_cat: Dict[str, int] = {}
    for f in active:
        by_cat[f.category.value] = by_cat.get(f.category.value, 0) + 1
    a("| Category | Count |")
    a("|---|--:|")
    for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        a(f"| {k} | {v} |")
    a("")
    a("## Findings")
    a("")
    a("Grouped by severity, most severe first. Confidence is stated per finding: a "
      "LOW-confidence pattern match warrants review, it is not a proven vulnerability.")
    a("")

    for sev in SEV_ORDER:
        group = [f for f in active if f.severity.value == sev]
        if not group:
            continue
        a(f"### {sev.title()} ({len(group)})")
        a("")
        for f in group:
            a(f"#### `{f.rule_id}` - {f.title}")
            a("")
            a(f"- **Location:** `{f.location}`")
            a(f"- **Tool:** {f.tool} · **Confidence:** {f.confidence.value} · "
              f"**Status:** {f.status.value}")
            if f.package_name:
                a(f"- **Package:** {f.package_name} {f.package_version}"
                  f"{' → fix ' + f.fixed_version if f.fixed_version else ' (no fix published)'}")
            if f.cve:
                a(f"- **Advisory:** {f.cve}")
            refs = []
            c = f.compliance
            if c.cwe:
                refs.append("CWE-" + ", CWE-".join(c.cwe))
            if c.owasp_top10:
                refs.append("OWASP " + ", ".join(c.owasp_top10))
            if c.owasp_api_top10:
                refs.append("API " + ", ".join(c.owasp_api_top10))
            if c.nist_800_53:
                refs.append("NIST " + ", ".join(c.nist_800_53))
            if c.hipaa:
                refs.append("HIPAA " + ", ".join("§" + h for h in c.hipaa))
            if refs:
                a(f"- **Controls:** {' · '.join(refs)}")
            if f.effort:
                a(f"- **Effort:** {f.effort}")
            a("")
            if f.description:
                a(f"{f.description}")
                a("")
            if f.code_snippet and f.category.value != "secrets":
                a("```")
                a(f.code_snippet[:280])
                a("```")
                a("")
            elif f.category.value == "secrets":
                a(f"> Value withheld: {f.code_snippet}")
                a("")
            if f.remediation:
                a(f"**Remediation:** {f.remediation}")
                a("")
    path.write_text("\n".join(L), encoding="utf-8")
    return str(path)


# ── Dashboard ─────────────────────────────────────────────────────────────────

def _donut(counts: Dict[str, int], size: int = 190) -> str:
    """Inline SVG donut. No chart library, no network."""
    total = sum(counts.get(s, 0) for s in SEV_ORDER) or 1
    r, cx, cy, w = size / 2 - 22, size / 2, size / 2, 26
    circ = 2 * 3.141592653589793 * r
    segs, offset = [], 0.0
    for s in SEV_ORDER:
        n = counts.get(s, 0)
        if not n:
            continue
        frac = n / total
        segs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{SEV_COLOUR[s]}" stroke-width="{w}" '
            f'stroke-dasharray="{circ*frac:.2f} {circ*(1-frac):.2f}" '
            f'stroke-dashoffset="{-circ*offset:.2f}" transform="rotate(-90 {cx} {cy})">'
            f'<title>{s.title()}: {n}</title></circle>')
        offset += frac
    return (f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
            f'role="img" aria-label="Findings by severity">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#e2e8f0" '
            f'stroke-width="{w}"/>{"".join(segs)}'
            f'<text x="{cx}" y="{cy-2}" text-anchor="middle" font-size="30" '
            f'font-weight="700" fill="currentColor">{total}</text>'
            f'<text x="{cx}" y="{cy+18}" text-anchor="middle" font-size="11" '
            f'fill="#718096">findings</text></svg>')


def _gauge(score: float, size: int = 200) -> str:
    """Semicircular score gauge."""
    r, cx, cy = size / 2 - 20, size / 2, size / 2 + 8
    frac = max(0.0, min(1.0, score / 100.0))
    semi = 3.141592653589793 * r
    colour = "#2f855a" if score >= 70 else ("#b8860b" if score >= 40 else "#b3121f")
    return (f'<svg viewBox="0 0 {size} {size*0.66:.0f}" width="{size}" '
            f'height="{size*0.66:.0f}" role="img" aria-label="Security score {score}">'
            f'<path d="M {cx-r} {cy} A {r} {r} 0 0 1 {cx+r} {cy}" fill="none" '
            f'stroke="#e2e8f0" stroke-width="17" stroke-linecap="round"/>'
            f'<path d="M {cx-r} {cy} A {r} {r} 0 0 1 {cx+r} {cy}" fill="none" '
            f'stroke="{colour}" stroke-width="17" stroke-linecap="round" '
            f'stroke-dasharray="{semi*frac:.2f} {semi:.2f}"/>'
            f'<text x="{cx}" y="{cy-8}" text-anchor="middle" font-size="38" '
            f'font-weight="700" fill="currentColor">{score:g}</text>'
            f'<text x="{cx}" y="{cy+10}" text-anchor="middle" font-size="11" '
            f'fill="#718096">out of 100</text></svg>')


def _bars(rows: List[tuple], max_w: int = 260) -> str:
    mx = max([n for _, n in rows] or [1]) or 1
    out = []
    for label, n in rows:
        w = int(max_w * n / mx) if n else 0
        out.append(
            f'<div class="bar"><span class="bl">{html.escape(label)}</span>'
            f'<span class="bt"><i style="width:{w}px"></i></span>'
            f'<span class="bn">{n}</span></div>')
    return "".join(out)


# ── Phase 3/4 dashboard panes ─────────────────────────────────────────────────

def _az_group(findings, prefix):
    """Azure findings for one check family, split pass/attention.

    Only FAIL/WARN records become Findings, so a family's 'attention' count is what
    is present here and 'checks' is read from the evidence manifest where available.
    """
    items = [f for f in findings if f.rule_id.startswith(prefix)]
    sev = Counter(f.severity.value for f in items)
    return len(items), sev


def _azure_pane(findings, gov_pct):
    fams = [("AZ-APP", "App Service"), ("AZ-DB", "Database"), ("AZ-KV", "Key Vault"),
            ("AZ-NET", "Networking"), ("AZ-ID", "Identity"), ("AZ-MON", "Monitoring")]
    rows = ""
    total = 0
    for pre, label in fams:
        n, sev = _az_group(findings, pre)
        total += n
        worst = next((s for s in SEV_ORDER if sev.get(s)), None)
        colour = SEV_COLOUR.get(worst, "#2f855a")
        badge = (f'<span class="tag" style="background:{colour}">{n} need attention</span>'
                 if n else '<span class="tag" style="background:#2f855a">no findings</span>')
        detail = " ".join(f'{k[0].upper()}{v}' for k, v in
                          sorted(sev.items(), key=lambda kv: SEV_ORDER.index(kv[0]))) or "-"
        rows += (f"<tr><td><b>{pre}</b></td><td>{html.escape(label)}</td>"
                 f"<td class=n>{n}</td><td>{detail}</td><td>{badge}</td></tr>")
    gov_col = "#2f855a" if gov_pct >= 80 else ("#b8860b" if gov_pct >= 50 else "#b3121f")
    return f"""
<div class="grid">
  <div class="card" style="flex:2"><h2>Azure infrastructure checks</h2><div class="scroll">
    <table><thead><tr><th>Family</th><th>Area</th><th>Findings</th><th>Severity</th>
    <th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>
    <div class="muted" style="font-size:11px;margin-top:9px">All checks are read-only
      <code>az</code> queries. A family with no findings means nothing was flagged -
      not that every control was verified.</div></div>
  <div class="card center"><h2>Azure Gov readiness</h2>
    <div style="font-size:44px;font-weight:800;color:{gov_col}">{gov_pct:.0f}%</div>
    <div class="muted" style="font-size:12px;text-align:center;margin-top:8px">
      services available in Azure Government.<br>Two blockers are not configuration:
      Static Web Apps is unavailable there, and the AI provider sits outside the
      Gov boundary.</div></div>
</div>"""


def _compliance_pane(compliance):
    m = (compliance or {}).get("matrices") or {}
    cov = (compliance or {}).get("coverage") or {}

    hip = "".join(
        f'<tr><td><b>§{html.escape(r["id"])}</b></td><td>{html.escape(r["name"])}</td>'
        f'<td class=n>{r["finding_count"]}</td>'
        f'<td><span class="tag" style="background:'
        f'{"#b3121f" if r["by_severity"]["critical"] else ("#d9480f" if r["by_severity"]["high"] else ("#b8860b" if r["finding_count"] else "#718096"))}">'
        f'{"findings" if r["finding_count"] else "no findings"}</span></td></tr>'
        for r in (m.get("hipaa") or {}).get("rows", []))

    nist = (m.get("nist_800_53") or {}).get("by_family") or {}
    nist_rows = "".join(
        f'<tr><td>{html.escape(k)}</td><td class=n>{v}</td></tr>'
        for k, v in sorted(nist.items(), key=lambda kv: -kv[1])[:10]) or         "<tr><td colspan=2 class=muted>No NIST mappings.</td></tr>"

    cells = ""
    for r in (m.get("owasp_top10") or {}).get("rows", []):
        n = r["finding_count"]
        bg = "#edf2f7" if not n else (
            "#b3121f" if r["by_severity"]["critical"] else
            "#d9480f" if r["by_severity"]["high"] else
            "#b8860b" if r["by_severity"]["medium"] else "#2b6cb0")
        fg = "#4a5568" if not n else "#fff"
        cells += (f'<div class="cell" style="background:{bg};color:{fg}" '
                  f'title="{html.escape(r["name"])}: {n}"><b>'
                  f'{html.escape(r["id"].split(":")[0])}</b><span>{n}</span></div>')

    packs = ["hipaa_evidence_package", "tefca_evidence_package", "nist_evidence_package",
             "owasp_evidence_package", "fedramp_preparation"]
    root = Path(__file__).resolve().parent.parent / "compliance" / "evidence"
    pack_rows = ""
    for p in packs:
        d = root / p
        cnt = len(list(d.rglob("*"))) if d.exists() else 0
        pack_rows += (f'<tr><td><code>{html.escape(p)}</code></td>'
                      f'<td><span class="tag" style="background:'
                      f'{"#2f855a" if cnt else "#b3121f"}">'
                      f'{"present" if cnt else "missing"}</span></td>'
                      f'<td class=n>{cnt}</td></tr>')

    cov_rows = "".join(
        f'<tr><td>{html.escape(k.replace("_"," ").upper())}</td>'
        f'<td class=n>{v:.0f}%</td></tr>' for k, v in sorted(cov.items()))

    return f"""
<div class="grid">
  <div class="card"><h2>HIPAA technical safeguards</h2><div class="scroll"><table>
    <thead><tr><th>§</th><th>Safeguard</th><th>Findings</th><th>Status</th></tr></thead>
    <tbody>{hip or '<tr><td colspan=4 class=muted>Not mapped.</td></tr>'}</tbody>
    </table></div></div>
  <div class="card"><h2>OWASP Top 10 (2021)</h2><div class="heat">{cells}</div>
    <div class="muted" style="font-size:11px;margin-top:10px">Grey = no findings,
      which is not an assertion of compliance.</div></div>
</div>
<div class="grid">
  <div class="card"><h2>NIST 800-53 findings by family</h2><div class="scroll"><table>
    <thead><tr><th>Family</th><th>Findings</th></tr></thead>
    <tbody>{nist_rows}</tbody></table></div></div>
  <div class="card"><h2>Detection coverage</h2><div class="scroll"><table>
    <thead><tr><th>Framework</th><th>Coverage</th></tr></thead>
    <tbody>{cov_rows or '<tr><td colspan=2 class=muted>n/a</td></tr>'}</tbody></table></div>
    <div class="muted" style="font-size:11px;margin-top:9px">Detection coverage is what
      the RULESET can detect - an assurance statement about tooling, not about the
      code.</div></div>
  <div class="card"><h2>Evidence packages</h2><div class="scroll"><table>
    <thead><tr><th>Package</th><th>Status</th><th>Files</th></tr></thead>
    <tbody>{pack_rows}</tbody></table></div></div>
</div>"""


def write_dashboard(path: Path, scan: Scan, project, compliance: Dict[str, Any],
                    kloc: float, sboms: Dict[str, str],
                    history: List[Dict[str, Any]],
                    gov_readiness_pct: float = 70.0) -> str:
    e = html.escape
    counts = scan.counts_by_severity()
    active = [f for f in scan.findings if not f.suppressed]
    gate = scan.gate_result.value.upper() if scan.gate_result else "N/A"
    gate_col = {"PASS": "#2f855a", "WARN": "#b8860b", "FAIL": "#b3121f"}.get(gate, "#4a5568")
    m = (compliance or {}).get("matrices") or {}
    verdict, why = recommendation(scan, counts)

    # OWASP heatmap
    owasp_cells = ""
    for r in (m.get("owasp_top10") or {}).get("rows", []):
        n = r["finding_count"]
        bg = "#edf2f7" if not n else (
            "#b3121f" if r["by_severity"]["critical"] else
            "#d9480f" if r["by_severity"]["high"] else
            "#b8860b" if r["by_severity"]["medium"] else "#2b6cb0")
        fg = "#4a5568" if not n else "#fff"
        owasp_cells += (f'<div class="cell" style="background:{bg};color:{fg}" '
                        f'title="{e(r["name"])}: {n} finding(s)">'
                        f'<b>{e(r["id"].split(":")[0])}</b><span>{n}</span></div>')

    hipaa_rows = "".join(
        f'<tr><td><b>§{e(r["id"])}</b></td><td>{e(r["name"])}</td>'
        f'<td class="n">{r["finding_count"]}</td>'
        f'<td><span class="tag" style="background:'
        f'{"#b3121f" if r["by_severity"]["critical"] else ("#d9480f" if r["by_severity"]["high"] else ("#b8860b" if r["finding_count"] else "#718096"))}">'
        f'{"findings" if r["finding_count"] else "no findings"}</span></td></tr>'
        for r in (m.get("hipaa") or {}).get("rows", []))

    risk_rows = "".join(
        f'<tr><td><span class="pill" style="background:{SEV_COLOUR[r["severity"]]}">'
        f'{r["severity"].title()}</span></td><td>{e(r["title"][:90])}</td>'
        f'<td class="n">{r["count"]}</td><td class="n">{r["files"]}</td>'
        f'<td><code>{e(r["rule_id"])}</code></td></tr>'
        for r in top_risks(scan, 10))

    tool_rows = "".join(
        f'<tr><td>{e(t.name)}</td>'
        f'<td><span class="tag" style="background:'
        f'{"#2f855a" if t.ran else ("#b3121f" if t.error else "#b8860b")}">'
        f'{"ran" if t.ran else ("error" if t.error else "skipped")}</span></td>'
        f'<td class="n">{t.findings_count}</td><td class="n">{t.duration_seconds:.1f}s</td>'
        f'<td class="muted">{e((t.error or t.skipped_reason or t.version or "")[:90])}</td></tr>'
        for t in scan.tools)

    sbom_rows = "".join(
        f'<tr><td>{e(k)}</td><td>{"<span class=\'tag\' style=\'background:#2f855a\'>generated</span>" if (v and Path(str(v)).exists()) else "<span class=\'tag\' style=\'background:#b3121f\'>missing</span>"}</td>'
        f'<td class="muted">{e(Path(str(v)).name if v and Path(str(v)).exists() else str(v)[:70])}</td></tr>'
        for k, v in (sboms or {}).items())

    cat_bars = _bars([(k, v) for k, v in sorted(
        scan.counts_by_category().items(), key=lambda kv: -kv[1])])

    hist_rows = "".join(
        f'<tr><td><code>{e(h["scan_id"][-15:])}</code></td><td>{e(str(h["date"])[:16])}</td>'
        f'<td class="n">{h.get("security_score")}</td>'
        f'<td class="n">{h.get("critical",0)}</td><td class="n">{h.get("high",0)}</td>'
        f'<td class="n">{h.get("medium",0)}</td><td class="n">{h.get("low",0)}</td></tr>'
        for h in (history or [])[-12:])

    skipped_banner = ""
    if scan.skipped_tools:
        names = ", ".join(t.name for t in scan.skipped_tools)
        skipped_banner = (
            f'<div class="warn"><b>Reduced coverage.</b> Scanner(s) did not run: '
            f'{e(names)}. The finding count is a floor, not a total.</div>')

    azure_pane = _azure_pane([f for f in scan.findings if not f.suppressed],
                             gov_readiness_pct)
    compliance_pane = _compliance_pane(compliance)

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Security Dashboard - {e(getattr(project,'display_name',scan.project_name))}</title>
<style>
:root{{--bg:#f4f6f9;--card:#fff;--fg:#1a202c;--mut:#718096;--bd:#e2e8f0;--th:#edf2f7}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f1218;--card:#181c24;--fg:#e6e9ef;
--mut:#94a3b8;--bd:#2b3240;--th:#222836}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:26px;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:1280px;margin:0 auto}}
h1{{font-size:24px;margin:0 0 3px}} h2{{font-size:15px;margin:0 0 14px;
text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:20px}}
.grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));
margin-bottom:16px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:18px}}
.center{{display:flex;align-items:center;justify-content:center;flex-direction:column}}
.gate{{display:inline-block;padding:9px 22px;border-radius:8px;color:#fff;
font-weight:800;font-size:19px;background:{gate_col}}}
.verdict{{font-size:26px;font-weight:800;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--bd)}}
th{{background:var(--th);font-size:11px;text-transform:uppercase;letter-spacing:.04em;
color:var(--mut)}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.muted{{color:var(--mut)}}
code{{background:var(--th);padding:1px 5px;border-radius:4px;font-size:12px}}
.pill,.tag{{display:inline-block;color:#fff;border-radius:5px;padding:2px 8px;
font-size:11px;font-weight:700}}
.heat{{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}}
.cell{{border-radius:8px;padding:11px 6px;text-align:center;font-size:12px}}
.cell b{{display:block;font-size:14px}} .cell span{{font-size:17px;font-weight:700}}
.bar{{display:flex;align-items:center;gap:9px;margin-bottom:7px;font-size:13px}}
.bl{{width:82px;color:var(--mut)}} .bt{{flex:1;background:var(--th);border-radius:4px;
height:11px;overflow:hidden}} .bt i{{display:block;height:100%;background:#2b6cb0}}
.bn{{width:38px;text-align:right;font-variant-numeric:tabular-nums}}
.warn{{background:#fffaf0;border-left:4px solid #b8860b;color:#744210;padding:11px 14px;
border-radius:6px;margin-bottom:16px;font-size:13px}}
@media(prefers-color-scheme:dark){{.warn{{background:#2a2210;color:#f6e05e}}}}
.scroll{{overflow-x:auto}}
.foot{{color:var(--mut);font-size:12px;margin-top:22px;line-height:1.6}}
</style></head><body><div class="wrap">

<h1>Security Dashboard - {e(getattr(project,'display_name',scan.project_name))}</h1>
<div class="sub">Scan <code>{e(scan.scan_id)}</code> · {e(scan.started_at[:19])} ·
{scan.duration_seconds:.0f}s · {kloc:,.1f} KLOC ·
commit <code>{e(scan.git_commit[:12] or 'n/a')}</code></div>

{skipped_banner}

<input type="radio" name="tab" id="t1" checked>
<input type="radio" name="tab" id="t2">
<input type="radio" name="tab" id="t3">
<div class="tabs"><label for="t1">Overview</label><label for="t2">Azure</label><label for="t3">Compliance</label></div>

<div class="pane pane-1">
<div class="grid">
  <div class="card center"><h2>Security score</h2>{_gauge(scan.security_score)}
    <div class="muted" style="font-size:11px;text-align:center;margin-top:8px">
      model {SCORE_MODEL_VERSION} · density-normalised</div></div>
  <div class="card center"><h2>Release gate</h2>
    <div class="gate">{gate}</div>
    <div class="verdict">{e(verdict)}</div>
    <div class="muted" style="font-size:12px;text-align:center;margin-top:8px">
      {e(why[:150])}</div></div>
  <div class="card center"><h2>Findings by severity</h2>{_donut(counts)}
    <div style="margin-top:10px;font-size:12px">
      {"".join(f'<span class="pill" style="background:{SEV_COLOUR[s]};margin:2px">{s.title()} {counts[s]}</span>' for s in SEV_ORDER if counts[s])}
    </div></div>
  <div class="card"><h2>By category</h2>{cat_bars}</div>
</div>

<div class="grid">
  <div class="card"><h2>OWASP Top 10 (2021) heatmap</h2><div class="heat">{owasp_cells}</div>
    <div class="muted" style="font-size:11px;margin-top:10px">Colour = worst severity
      present. Grey = no findings, which is not an assertion of compliance.</div></div>
  <div class="card"><h2>HIPAA technical safeguards</h2><div class="scroll"><table>
    <thead><tr><th>§</th><th>Safeguard</th><th>Findings</th><th>Status</th></tr></thead>
    <tbody>{hipaa_rows}</tbody></table></div></div>
</div>

<div class="card" style="margin-bottom:16px"><h2>Top risks</h2><div class="scroll">
<table><thead><tr><th>Severity</th><th>Risk</th><th>Occurrences</th><th>Files</th>
<th>Rule</th></tr></thead><tbody>{risk_rows}</tbody></table></div></div>

<div class="grid">
  <div class="card"><h2>Scanner coverage</h2><div class="scroll"><table>
    <thead><tr><th>Scanner</th><th>Status</th><th>Findings</th><th>Time</th><th>Note</th></tr></thead>
    <tbody>{tool_rows}</tbody></table></div></div>
  <div class="card"><h2>SBOM (CycloneDX)</h2><div class="scroll"><table>
    <thead><tr><th>Target</th><th>Status</th><th>Artefact</th></tr></thead>
    <tbody>{sbom_rows or '<tr><td colspan=3 class=muted>No SBOM recorded.</td></tr>'}</tbody>
    </table></div></div>
</div>

<div class="card"><h2>Scan history</h2><div class="scroll"><table>
<thead><tr><th>Scan</th><th>Date</th><th>Score</th><th>C</th><th>H</th><th>M</th><th>L</th></tr></thead>
<tbody>{hist_rows or '<tr><td colspan=7 class=muted>Only one scan on record.</td></tr>'}</tbody>
</table></div></div>
</div>

<div class="pane pane-2">{azure_pane}</div>
<div class="pane pane-3">{compliance_pane}</div>

<div class="foot">
{e(score_formula_text(kloc))}<br>
Generated {e(datetime.now(timezone.utc).isoformat(timespec='seconds'))} by the AGT
Security Assurance Platform. Read-only analysis - no production system was modified.
Self-contained: no external scripts, styles, fonts or network calls.
</div>
</div></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return str(path)
