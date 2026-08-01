# Deployment Guide

Written from what actually went wrong, not from what the tooling claims. Every
rule below cost an outage, a wasted hour, or a false report at least once.

---

## The three rules that matter most

### 1. Build the zip with Python, never PowerShell

```python
import os, zipfile
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for root, dirs, files in os.walk(stage):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            full = os.path.join(root, fn)
            arc = os.path.relpath(full, stage).replace(os.sep, "/")   # POSIX
            z.write(full, arc)
```

**Why.** `.NET`'s `ZipFile.CreateFromDirectory` under PowerShell 5.1 writes entry
names with **backslashes**. Linux unzip does not treat those as path separators,
so wwwroot ends up holding a file literally named `app\main.py` and nothing
imports. Combined with `--clean` (which empties wwwroot *before* unpacking) that
is an outage, not a failed no-op.

Always gate the artifact before deploying:

```python
assert not [n for n in z.namelist() if "\\" in n]      # zero backslash entries
assert sorted({n.split("/")[0] for n in z.namelist()}) == ["app", "pydeps", "requirements.txt"]
```

### 2. Status 4 is NOT proof the new code is running

This has caught us on **both** environments. `az webapp deploy` reports
`status: 4, active: true` while the old container keeps serving — for minutes.

```bash
# Necessary but not sufficient:
az webapp log deployment list -n <app> -g <rg> --query "[0].{s:status,a:active}" -o tsv

# The actual proof — an endpoint that exists ONLY in the new build:
curl -o /dev/null -w '%{http_code}' https://<host>/api/tefca/arc/review-rules   # 404 -> old code
```

`/health` answers 200 from the old code the whole time and proves nothing.
**Issue an explicit `az webapp restart` after every deploy** and re-check.

### 3. CLI error ≠ failed deploy

`az webapp deploy` regularly ends with `RemoteDisconnected` or
`UnknownDeploymentError` **while the deployment is succeeding server-side**. The
CLI lost its polling connection; the server keeps going.

**Never retry on a CLI error** — a retry starts a second concurrent build on the
same app and the two collide. Query the server instead (above). Only retry when
the server itself reports status 3 *and* the site is down.

---

## Dev and prod use the same model

Dev was converted off Oryx on 2026-08-01. Both environments now deploy a
self-contained artifact.

| Setting | Value |
|---------|-------|
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `false` |
| `ENABLE_ORYX_BUILD` | `false` |
| `PYTHONPATH` | `/home/site/wwwroot/pydeps` |
| Startup command | `python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind=0.0.0.0:8000 --timeout 600 --forwarded-allow-ips='*'` |

**Why dev left Oryx.** Under Oryx, two of four dev deploys failed and one caused
an outage. The cause was a packaging race visible only in the container log:

```
tar: ./antenv/lib/python3.12/site-packages/pydantic/_internal/_typing_extra.py:
     file changed as we read it
Falling back to gzip compression.
Deployment Failed.
```

Not a code fault — the Oryx build itself logged 0 errors. Removing Oryx removed
the failing component; every deploy since has hit status 4 on the first attempt.

`python -m gunicorn`, not bare `gunicorn`: a flat `pip --target` directory is not
an activatable venv, so the module must be found via `PYTHONPATH`.

---

## Building `pydeps`

The build machine is Windows/py3.13; Azure is Linux/py3.12. **Never ship a local
venv.** Download Linux wheels cross-platform:

```bash
pip install --target pydeps_new --only-binary=:all: \
  --python-version 3.12 --implementation cp \
  --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 \
  --platform manylinux_2_28_x86_64 \
  -r requirements.txt
```

Verify a compiled extension is actually Linux:

```python
# first bytes must be \x7fELF, and byte 18 == 0x3E (x86-64)
```

Strip `__pycache__` and `*.pyc` before zipping — wrong-version bytecode, plus
Windows long-path failures on deep dependency trees.

**Do not name the directory `antenv`.** Azure's "Express Python Deploy"
optimizer detects that name and re-compresses it into `antenv.tar.gz`, so it
never lands in wwwroot.

---

## `--clean` is mandatory, and it has teeth

`az webapp deploy --type zip` **overlays** onto the existing wwwroot; it does not
replace it. Files from earlier deploys that are absent from the new zip persist
forever — which once left 87 `.dist-info` directories against 75 in the
artifact, so `pip-audit` reported CVE fixes as unapplied.

```bash
az webapp deploy --name <app> --resource-group <rg> \
  --type zip --src-path prod-deploy.zip --clean true --restart true --track-status false
```

**Caution:** `--clean` wipes wwwroot *before* unpacking, so a bad zip takes the
site down rather than merely failing to improve it. Gate the artifact first
(rule 1), and never `--clean` with a zip shape that has not deployed
successfully at least once.

---

## Do not install semgrep into the application environment

`pip install semgrep` moved **11 pinned packages**, including:

* `fastapi` 0.140.13 → 0.115.0 — the exact boundary where auth failures return
  **403 instead of 401**. A full test suite ran against the wrong stack and had
  to be discarded.
* `python-multipart` 0.0.31 → 0.0.22 — reintroducing 5 High CVEs.
* `pyasn1` 0.6.4 → 0.6.3 — undoing a CVE fix.

Run semgrep in CI or a separate virtualenv. Note also that semgrep on Windows
returns **empty output for directory targets** (file targets work), which is why
the platform's probe correctly disables it.

If the local environment drifts, compare against the manifest rather than
assuming — and remember `asyncpg==0.29.0` has no Windows wheel for Python 3.13,
so it cannot be restored locally. The deploy artifact is built from
`requirements.txt` with Linux wheels, so production is unaffected by local drift.

---

## Frontend (Azure Static Web Apps)

`NEXT_PUBLIC_*` is **inlined at build time**, not read at runtime. One artifact
carries one API URL and cannot be correct for both environments — build twice:

```bash
NEXT_PUBLIC_API_URL=https://docuaction-dev.azurewebsites.net npx next build   # dev SWA
NEXT_PUBLIC_API_URL=https://api-prod.docuaction.io          npx next build   # prod SWA
```

Gate each build before deploying:

```bash
grep -rl "api-prod\.docuaction\.io" out/ | wc -l     # must be 0 for the DEV build
grep -rl "docuaction-dev\."          out/ | wc -l    # must be 0 for the PROD build
```

Deploy with the SWA CLI and the deployment token:

```bash
TOKEN=$(az staticwebapp secrets list --name <swa> --resource-group <rg> \
         --query "properties.apiKey" -o tsv)
npx @azure/static-web-apps-cli deploy ./out --deployment-token "$TOKEN" --env production
```

| Environment | SWA | Resource group |
|-------------|-----|----------------|
| dev | `docuaction-frontend-dev` (witty-dune) | `rg-docuaction-dev` |
| prod | `docuaction-frontend` (witty-tree) | `rg-docuaction-prod` |

**Do not run `npm audit fix --force`.** As of 2026-08-01 it proposes
*downgrading* Next 16.2.12 → 14.2.35 — a major downgrade that would break the
App Router build. Plain `npm audit fix` is safe and fixed postcss.

---

## Post-deploy checklist

```bash
curl -s https://<host>/health                    # 200
curl -s https://<host>/api/config                # correct "environment"
curl -o /dev/null -w '%{http_code}' <new-endpoint>   # proves the NEW build
curl -o /dev/null -w '%{http_code}' <guarded>        # 401
curl -o /dev/null -w '%{http_code}' <public>         # 200
```

Never seed production. `POST /api/tefca/registry/dev/seed` is admin-gated **and**
refuses when `ENVIRONMENT=production`: the registry is the population every
sample and report is drawn from, and a contaminated denominator is not
correctable after the fact.
