# Installation

Python 3.10+ required. The CORE has no mandatory third-party dependency - it imports
on a bare interpreter so it can always report that a scanner is missing rather than
crash before it gets the chance.

```bash
cd security-platform
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # Linux/macOS
```

## Scanners (all optional, all open source)

| Tool | Install | Notes |
|---|---|---|
| Bandit | `pip install bandit` | Python SAST. Works everywhere. |
| Semgrep | `pip install semgrep` | **Does not work on Windows** - `semgrep-core` has no Windows build and hangs. Runs on Linux/WSL/CI. |
| Gitleaks | download the release binary into `tools/` | Go binary; the Windows build works. |
| pip-audit | `pip install pip-audit` | Python CVEs. |
| npm audit | ships with npm | Needs `package-lock.json`. |
| CycloneDX | `pip install cyclonedx-bom` + `npm install --prefix tools/npm @cyclonedx/cyclonedx-npm` | SBOM. |
| psycopg2 | `pip install psycopg2-binary` | Only for local database integrity checks. Must be in the SAME interpreter that runs the suite. |

Every tool install belongs inside `security-platform/`. Nothing is required globally.

## Verify

```bash
python cli.py discover
```
