# CLI reference

```
python cli.py <command> [-p PROJECT] [-v] [options]
```

| Command | Purpose |
|---|---|
| `discover` | Inventory files, languages, LOC, manifests; list registered plugins |
| `scan` | Run every enabled scanner |
| `scan --sast` | Static analysis only |
| `scan --deps` | Dependency / SCA only |
| `scan --secrets` | Secrets detection only |
| `scan --container` | Container / IaC only |
| `compliance` | Map findings to control frameworks |
| `report [--format ...]` | Regenerate reports for the latest scan |
| `gate [--strict]` | Evaluate the release gate |
| `full` | discovery -> scan -> compliance -> gate -> reports -> dashboard |
| `status` | History, open findings, MTTR |
| `findings list [--status open\|suppressed\|all] [--limit N]` | Finding inventory |
| `findings suppress <fingerprint> --reason "..." [--by NAME]` | Suppress with an audit trail |
| `findings reopen <fingerprint>` | Remove suppression, mark reopened |
| `diff [--format md\|json]` | Delta between the last two scans |

Global: `-p/--project NAME` (default `docuaction`), `-v/--verbose`,
`--format json\|markdown\|csv\|html` (repeatable).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success / gate PASS |
| 1 | Gate FAIL |
| 2 | Gate WARN with `--strict` |
| 3 | Usage or configuration error - **nothing was scanned**, fail CI loudly |

## Phase 2/3 suites

DAST and Azure suites are invoked through their runners rather than `cli.py` flags:

```bash
python -c "from dast.runner import run_dast; run_dast(verbose=True)"
python -c "import asyncio; from dast.phase2_runner import run_phase2; asyncio.run(run_phase2(verbose=True))"
python integrate_phase2.py       # consolidate everything
```

`cli.py scan --azure` and `--dast` flags are **not yet wired** - a known packaging gap.
