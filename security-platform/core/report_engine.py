"""Report generation: JSON, Markdown, CSV, HTML.

JINJA2 IS OPTIONAL BY DESIGN
    If templates/ and Jinja2 are present, HTML is rendered from templates so the
    look can be customised per client without touching Python. If Jinja2 is absent,
    a self-contained built-in renderer produces the same content. A missing
    presentation library must never cost you the scan results.
"""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.gate_engine import score_formula_text
from core.models import Finding, GateResult, Scan, Severity

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SEVERITY_COLOUR = {
    "critical": "#b3121f", "high": "#d9480f", "medium": "#b8860b",
    "low": "#2b6cb0", "info": "#4a5568",
}


class ReportEngine:
    """Writes a scan out in every supported format."""

    def __init__(self, output_dir: Path, platform_root: Optional[Path] = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.platform_root = Path(platform_root) if platform_root else \
            Path(__file__).resolve().parent.parent
        self.template_dir = self.platform_root / "templates"

    # ── public API ───────────────────────────────────────────────────────────

    def generate_all(self, scan: Scan, project, extras: Optional[Dict[str, Any]] = None,
                     formats: Optional[List[str]] = None) -> Dict[str, str]:
        """Produce every requested format. Returns {format: path}."""
        extras = extras or {}
        formats = formats or ["json", "markdown", "csv", "html"]
        written: Dict[str, str] = {}
        for fmt in formats:
            try:
                if fmt == "json":
                    written["json"] = self.write_json(scan, project, extras)
                elif fmt == "markdown":
                    written["markdown"] = self.write_markdown(scan, project, extras)
                elif fmt == "csv":
                    written["csv"] = self.write_csv(scan)
                elif fmt == "html":
                    written["html"] = self.write_html(scan, project, extras)
            except Exception as exc:
                written[fmt] = f"ERROR: {type(exc).__name__}: {exc}"
        return written

    # ── formats ──────────────────────────────────────────────────────────────

    def write_json(self, scan: Scan, project, extras: Dict[str, Any]) -> str:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "platform": "AGT Security Assurance Platform",
            "project": project.to_dict() if hasattr(project, "to_dict") else str(project),
            "scan": scan.to_dict(include_findings=True),
            "score_formula": score_formula_text(),
        }
        payload.update(extras)
        path = self.output_dir / f"{scan.scan_id}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        self._write_latest_pointer(path, "latest.json")
        return str(path)

    def write_csv(self, scan: Scan) -> str:
        path = self.output_dir / f"{scan.scan_id}.csv"
        cols = ["fingerprint", "severity", "status", "category", "tool", "rule_id",
                "title", "file_path", "line_start", "package_name", "cve", "cwe",
                "owasp_top10", "nist_800_53", "hipaa", "remediation", "suppressed"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for f in self._sorted(scan.findings):
                w.writerow([
                    f.fingerprint, f.severity.value, f.status.value, f.category.value,
                    f.tool, f.rule_id, f.title, f.file_path, f.line_start or "",
                    f.package_name, f.cve,
                    ";".join(f.compliance.cwe), ";".join(f.compliance.owasp_top10),
                    ";".join(f.compliance.nist_800_53), ";".join(f.compliance.hipaa),
                    (f.remediation or "").replace("\n", " ")[:300],
                    "yes" if f.suppressed else "no",
                ])
        return str(path)

    def write_markdown(self, scan: Scan, project, extras: Dict[str, Any]) -> str:
        counts = scan.counts_by_severity()
        active = [f for f in scan.findings if not f.suppressed]
        lines: List[str] = []
        a = lines.append

        a(f"# Security Scan Report — {getattr(project, 'display_name', scan.project_name)}")
        a("")
        a(f"**Scan ID:** `{scan.scan_id}`  ")
        a(f"**Started:** {scan.started_at}  •  **Duration:** {scan.duration_seconds:.1f}s  ")
        if scan.git_commit:
            a(f"**Commit:** `{scan.git_commit[:12]}` ({scan.git_ref})  ")
        a(f"**Security score:** **{scan.security_score}/100**  ")
        if scan.gate_result:
            a(f"**Release gate:** **{scan.gate_result.value.upper()}**  ")
        a("")
        a(f"> Score formula: {score_formula_text()}")
        a("")

        a("## Findings by severity")
        a("")
        a("| Severity | Count |")
        a("|---|--:|")
        for s in SEVERITY_ORDER:
            a(f"| {s.title()} | {counts.get(s, 0)} |")
        a(f"| **Total (active)** | **{len(active)}** |")
        a("")

        status = scan.counts_by_status()
        a("## Change since last scan")
        a("")
        a(f"- New: **{status.get('new', 0)}**")
        a(f"- Pre-existing: {status.get('existing', 0)}")
        a(f"- Reopened: **{status.get('reopened', 0)}**")
        if extras.get("delta", {}).get("counts", {}).get("fixed") is not None:
            a(f"- Fixed since previous scan: **{extras['delta']['counts']['fixed']}**")
        a("")

        a("## Scanner coverage")
        a("")
        a("| Scanner | Status | Findings | Duration | Note |")
        a("|---|---|--:|--:|---|")
        for t in scan.tools:
            st = "ran" if t.ran else ("ERROR" if t.error else "SKIPPED")
            note = t.error or t.skipped_reason or t.version or ""
            a(f"| {t.name} | {st} | {t.findings_count} | {t.duration_seconds:.1f}s | {note} |")
        a("")
        if scan.skipped_tools:
            a("> **Reduced coverage.** The scanners above marked SKIPPED did not run; "
              "their capability is absent from this report.")
            a("")

        if scan.gate_result:
            a("## Release gate")
            a("")
            for r in scan.gate_reasons:
                a(f"- {r}")
            a("")

        a("## Findings")
        a("")
        if not active:
            a("_No active findings._")
        else:
            a("| Sev | Status | Title | Location | Tool | CWE | OWASP |")
            a("|---|---|---|---|---|---|---|")
            for f in self._sorted(active):
                a(f"| {f.severity.value.title()} | {f.status.value} | "
                  f"{self._md(f.title)[:90]} | `{self._md(f.location)}` | {f.tool} | "
                  f"{','.join(f.compliance.cwe) or '-'} | "
                  f"{','.join(f.compliance.owasp_top10) or '-'} |")
        a("")
        path = self.output_dir / f"{scan.scan_id}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        self._write_latest_pointer(path, "latest.md")
        return str(path)

    def write_html(self, scan: Scan, project, extras: Dict[str, Any]) -> str:
        ctx = self._context(scan, project, extras)
        rendered = self._render_with_jinja("report.html.j2", ctx)
        if rendered is None:
            rendered = self._builtin_html(ctx)
        path = self.output_dir / f"{scan.scan_id}.html"
        path.write_text(rendered, encoding="utf-8")
        self._write_latest_pointer(path, "latest.html")
        return str(path)

    # ── internals ────────────────────────────────────────────────────────────

    def _context(self, scan: Scan, project, extras: Dict[str, Any]) -> Dict[str, Any]:
        active = [f for f in scan.findings if not f.suppressed]
        return {
            "scan": scan,
            "project": project,
            "project_title": getattr(project, "display_name", scan.project_name),
            "counts": scan.counts_by_severity(),
            "status_counts": scan.counts_by_status(),
            "category_counts": scan.counts_by_category(),
            "findings": self._sorted(active),
            "suppressed": [f for f in scan.findings if f.suppressed],
            "tools": scan.tools,
            "skipped_tools": scan.skipped_tools,
            "severity_order": SEVERITY_ORDER,
            "severity_colour": SEVERITY_COLOUR,
            "score_formula": score_formula_text(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "extras": extras,
        }

    def _render_with_jinja(self, template_name: str, ctx: Dict[str, Any]) -> Optional[str]:
        tpl = self.template_dir / template_name
        if not tpl.exists():
            return None
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
        except Exception:
            return None
        try:
            env = Environment(loader=FileSystemLoader(str(self.template_dir)),
                              autoescape=select_autoescape(["html", "xml"]))
            return env.get_template(template_name).render(**ctx)
        except Exception:
            return None

    def _builtin_html(self, ctx: Dict[str, Any]) -> str:
        """Self-contained HTML, no external assets, no CDN, works offline."""
        e = html.escape
        scan: Scan = ctx["scan"]
        counts = ctx["counts"]
        gate = scan.gate_result.value.upper() if scan.gate_result else "N/A"
        gate_col = {"PASS": "#2f855a", "WARN": "#b8860b", "FAIL": "#b3121f"}.get(gate, "#4a5568")

        chips = "".join(
            f'<span class="chip" style="background:{SEVERITY_COLOUR[s]}">'
            f'{s.title()}: {counts.get(s, 0)}</span>' for s in SEVERITY_ORDER)

        tool_rows = "".join(
            f"<tr><td>{e(t.name)}</td>"
            f"<td>{'ran' if t.ran else ('ERROR' if t.error else 'SKIPPED')}</td>"
            f"<td class=n>{t.findings_count}</td><td class=n>{t.duration_seconds:.1f}s</td>"
            f"<td>{e(t.error or t.skipped_reason or t.version or '')}</td></tr>"
            for t in ctx["tools"]) or "<tr><td colspan=5>No scanners registered.</td></tr>"

        rows = "".join(
            f'<tr><td><span class="pill" style="background:{SEVERITY_COLOUR[f.severity.value]}">'
            f'{f.severity.value.title()}</span></td>'
            f"<td>{e(f.status.value)}</td><td>{e(f.title)}</td>"
            f"<td><code>{e(f.location)}</code></td><td>{e(f.tool)}</td>"
            f"<td>{e(','.join(f.compliance.cwe) or '-')}</td>"
            f"<td>{e(','.join(f.compliance.owasp_top10) or '-')}</td></tr>"
            for f in ctx["findings"]) or "<tr><td colspan=7>No active findings.</td></tr>"

        gate_reasons = "".join(f"<li>{e(r)}</li>" for r in scan.gate_reasons)
        skipped_note = ""
        if ctx["skipped_tools"]:
            names = ", ".join(t.name for t in ctx["skipped_tools"])
            skipped_note = (f'<div class="warn"><strong>Reduced coverage.</strong> '
                            f'Scanner(s) did not run: {e(names)}. '
                            f'Their capability is absent from this report.</div>')

        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Security Report — {e(ctx['project_title'])}</title>
<style>
:root{{color-scheme:light dark}}
*{{box-sizing:border-box}}
body{{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
margin:0;padding:32px;background:#f7f8fa;color:#1a202c}}
@media(prefers-color-scheme:dark){{body{{background:#12151a;color:#e6e9ef}}
.card{{background:#1b1f27!important;border-color:#2c3340!important}}
th{{background:#232936!important}} code{{background:#232936!important}}}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:23px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 10px}}
.sub{{color:#718096;font-size:13px;margin-bottom:20px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:18px;margin-bottom:16px}}
.grid{{display:flex;gap:16px;flex-wrap:wrap}}
.score{{font-size:44px;font-weight:700;line-height:1}}
.gate{{display:inline-block;padding:7px 16px;border-radius:6px;color:#fff;
font-weight:700;background:{gate_col}}}
.chip,.pill{{display:inline-block;color:#fff;border-radius:20px;padding:3px 11px;
font-size:12px;font-weight:600;margin-right:6px}}
.pill{{border-radius:5px;padding:2px 8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #e2e8f0;vertical-align:top}}
th{{background:#edf2f7;font-size:12px;text-transform:uppercase;letter-spacing:.03em}}
td.n{{text-align:right}}
code{{background:#edf2f7;padding:1px 5px;border-radius:4px;font-size:12px}}
.scroll{{overflow-x:auto}}
.warn{{background:#fffaf0;border-left:4px solid #b8860b;padding:11px 14px;
border-radius:5px;margin:12px 0;font-size:13px;color:#744210}}
.formula{{font-size:12px;color:#718096;margin-top:8px}}
</style></head><body><div class="wrap">
<h1>Security Scan Report — {e(ctx['project_title'])}</h1>
<div class="sub">Scan <code>{e(scan.scan_id)}</code> • {e(scan.started_at)}
• {scan.duration_seconds:.1f}s{' • commit <code>' + e(scan.git_commit[:12]) + '</code>' if scan.git_commit else ''}</div>

<div class="grid">
  <div class="card" style="flex:1;min-width:210px">
    <div class="sub" style="margin:0">Security score</div>
    <div class="score">{scan.security_score}<span style="font-size:18px;color:#718096">/100</span></div>
    <div class="formula">{e(ctx['score_formula'])}</div>
  </div>
  <div class="card" style="flex:1;min-width:210px">
    <div class="sub" style="margin:0 0 8px">Release gate</div>
    <div class="gate">{gate}</div>
  </div>
  <div class="card" style="flex:2;min-width:280px">
    <div class="sub" style="margin:0 0 10px">Findings by severity</div>
    <div>{chips}</div>
  </div>
</div>

{skipped_note}

<div class="card"><h2 style="margin-top:0">Release gate justification</h2>
<ul>{gate_reasons or '<li>Gate not evaluated.</li>'}</ul></div>

<div class="card"><h2 style="margin-top:0">Scanner coverage</h2><div class="scroll">
<table><thead><tr><th>Scanner</th><th>Status</th><th>Findings</th><th>Duration</th>
<th>Note</th></tr></thead><tbody>{tool_rows}</tbody></table></div></div>

<div class="card"><h2 style="margin-top:0">Findings ({len(ctx['findings'])})</h2>
<div class="scroll"><table><thead><tr><th>Sev</th><th>Status</th><th>Title</th>
<th>Location</th><th>Tool</th><th>CWE</th><th>OWASP</th></tr></thead>
<tbody>{rows}</tbody></table></div></div>

<div class="sub">Generated {e(ctx['generated_at'])} by the AGT Security Assurance
Platform. Read-only analysis — no production system was modified.</div>
</div></body></html>"""

    @staticmethod
    def _sorted(findings: List[Finding]) -> List[Finding]:
        return sorted(findings,
                      key=lambda f: (-f.severity.rank, f.category.value,
                                     f.file_path or "", f.line_start or 0))

    @staticmethod
    def _md(text: str) -> str:
        return (text or "").replace("|", "\\|").replace("\n", " ")

    def _write_latest_pointer(self, path: Path, alias: str) -> None:
        """Copy to a stable filename so CI and the dashboard can hardlink to it."""
        try:
            (self.output_dir / alias).write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
