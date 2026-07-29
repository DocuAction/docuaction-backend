# AGT Security Assurance Platform

Reusable secure-code-review tooling: SAST, secrets detection, dependency scanning,
SBOM, compliance mapping, and a release gate. DocuAction is the first project; the
architecture supports any future AGT application (FCC, CMS, NIH, IRS) **by adding a
config file, not by changing code**.

## Status

| Phase | Scope | State |
|---|---|---|
| **1A** | Core framework, plugin system, findings DB, CLI, project config | **Built** |
| 1B | SAST — Semgrep, Bandit, custom rule packs | Not started |
| 1C | Secrets — Gitleaks | Not started |
| 1D | Dependencies — pip-audit, npm audit, Trivy, CycloneDX SBOM | Not started |
| 1E | Compliance mapping — NIST 800-53r5, ASVS, OWASP, HIPAA, CWE Top 25 | Not started |
| 1F | Executive summary, release gate, dashboard | Gate built in 1A; reports/dashboard pending |
| 1G | CLI polish + GitHub Actions | CLI built in 1A; workflow pending |

## Guarantees

- **Zero production impact.** Scanners run as external processes against source
  trees. No target code is imported or executed, nothing is written outside
  `security-platform/`, and no scan touches a running system.
- **Zero commercial licensing cost.** Open-source tools only. A missing tool is
  recorded as SKIPPED, the run continues, and the report states which capability
  was lost.
- **No git operations in this tree.** `security-platform/` is deliberately not a
  repository — the enclosing path resolves to a stray `C:\.git`, so any `git add`
  here would target the whole drive.

## Usage

```bash
python cli.py discover        # inventory the codebase
python cli.py scan            # run every enabled scanner
python cli.py scan --sast     # SAST only  (also --deps, --secrets, --container)
python cli.py compliance      # map findings to control frameworks
python cli.py report          # regenerate reports for the latest scan
python cli.py gate            # evaluate the release gate
python cli.py full            # everything, end to end
python cli.py status          # history, open findings, MTTR
```

Options: `-p/--project NAME` (default `docuaction`), `-v/--verbose`,
`--format json|markdown|csv|html` (repeatable), `--strict` (exit 2 on WARN).

Exit codes: `0` PASS · `1` gate FAIL · `2` gate WARN with `--strict` · `3` config error.

## Layout

```
core/
  models.py          Finding, Scan, Project, ComplianceMapping, ToolStatus
  plugin_manager.py  auto-discovery + isolated execution
  findings_db.py     SQLite history: new/existing/resolved/reopened, MTTR, delta
  engine.py          orchestration
  report_engine.py   JSON / Markdown / CSV / HTML
  gate_engine.py     release policy + security score
plugins/
  base.py            ScannerPlugin contract
  rules/             custom rule packs (Phase 1B)
config/projects/     one JSON per application
data/findings.db     scan history
reports/<project>/   generated artefacts
```

## Adding a project

Copy `config/projects/docuaction.json`, change `name`, `targets`, and
`gate_policy`. Nothing else is required — plugins not listed default to enabled.

## Design notes worth knowing

**Fingerprints exclude line numbers.** Identity is
`(tool, rule, file, normalised snippet)`. Including the line would mark half the
codebase "new" after a reformat and destroy the trend and MTTR data.

**Resolution is category-scoped.** A `--secrets` run only resolves secrets
findings. Without this, any partial scan would falsely mark everything else fixed.

**A scan that ran nothing cannot PASS.** `require_at_least_one_scanner` fails the
gate when no scanner executed — otherwise a CI job whose tool install broke would
report score 100 / PASS over a build nobody scanned.

**The score is deliberately simple**: `100 − (25×critical + 10×high + 3×medium +
0.5×low)`, floored at 0, suppressed findings excluded. The formula is printed
alongside the number so anyone can recompute it by hand.
