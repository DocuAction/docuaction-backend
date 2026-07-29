# Architecture

```
core/       framework - no knowledge of any specific application
  models.py            Finding, Scan, Project, ComplianceMapping, ToolStatus
  plugin_manager.py    auto-discovery + isolated execution
  findings_db.py       SQLite history: new/existing/resolved/reopened, MTTR, delta
  engine.py            orchestration
  gate_engine.py       release policy + security score
  report_engine.py     JSON / Markdown / CSV / HTML
  deliverables.py      executive summary, technical report, dashboard
  compliance.py        framework matrices (Phase 1E)
  compliance_reports.py  evidence packages (Phase 4)
plugins/    scanners, auto-discovered
dast/       Phase 2 dynamic tests + Phase 3 Azure checks
config/projects/*.json   one file per application
data/findings.db         scan history
reports/, evidence/, compliance/, dashboard/
```

## Design decisions worth knowing

**Fingerprints exclude line numbers.** Identity is `(tool, rule, file, normalised
snippet)`. Including the line would mark half a codebase "new" after a reformat and
destroy trend and MTTR data. Duplicate snippets in one file get a stable occurrence
ordinal.

**Resolution is category-scoped.** A `--secrets` run only resolves secrets findings,
so a partial scan cannot falsely mark everything else fixed.

**A scan that ran nothing cannot PASS.** `require_at_least_one_scanner` exists because
a CI job whose tool install failed would otherwise certify an unscanned build.

**Score is density-normalised** (v2.0): `100 / (1 + (penalty/KLOC)/5)`. The v1 linear
model saturated at 0 and could not distinguish 90 findings from 900.

**The production guard cannot be disabled.** `dast/config.py` is allow-list AND
deny-list, has no override parameter, and is re-checked before every socket.

**Static, dynamic and infrastructure evidence stay distinguishable.** A static PASS
and a live PASS are different kinds of evidence and an audit needs to know which it is
looking at.
