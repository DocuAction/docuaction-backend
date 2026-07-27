# DocuAction Backend — Azure Deployment Guide

**Product:** DocuAction AI / DocuAction TEFCA ARC
**Version:** 6.0.0
**Owner:** Alliance Global Tech, Inc.
**Audience:** DevOps / Platform Operations
**Classification:** Internal — Operations
**Contacts:** security@agtbi.com · imran@agtbi.com

---

## 1. Purpose and Scope

This guide provides the authoritative, step-by-step procedure for deploying the DocuAction
backend to **Microsoft Azure App Service (Linux)** with **Azure Database for PostgreSQL
Flexible Server**. It covers prerequisites, packaging of the deployment artifact, App Service
configuration, database migrations, TLS and custom-domain setup, and post-deploy verification.

This procedure applies to both the **dev** and **prod** environments. Environment isolation
requirements (separate App Service apps, separate PostgreSQL servers, separate Entra settings,
no shared secrets) are described in `environment-topology.md` and MUST be respected. Never
deploy dev artifacts against prod configuration or vice versa.

> The DocuAction backend was migrated to Azure from a prior Railway host, which is now retired.
> All references to the previous platform are historical.

---

## 2. Target Architecture Summary

| Component | Detail |
|-----------|--------|
| Cloud subscription | `AGT-DocuAction` (Microsoft Azure) |
| Resource group | `rg-docuaction-prod` (East US 2 region family) |
| Backend compute | Azure App Service (Linux), app name **Docuaction** |
| Default host | `docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net` |
| Custom domain | `api-prod.docuaction.io` |
| Runtime | Python 3.12, gunicorn with uvicorn workers (`python -m gunicorn`) |
| Database | Azure Database for PostgreSQL Flexible Server **docuaction-db**, SSL required, 42-table schema |
| Frontend (context) | Azure Static Web Apps `docuaction-frontend`, `app.docuaction.io` |
| Security monitoring | Microsoft Defender for Cloud (Standard) |
| Identity | JWT + Microsoft Entra ID SSO |

---

## 3. Prerequisites

Before beginning a deployment, confirm the following:

1. **Azure CLI** (`az`) version 2.60 or later installed and authenticated:
   ```bash
   az version
   az login
   az account set --subscription "AGT-DocuAction"
   az account show --output table
   ```
2. **Resource group access** — Contributor (or scoped deployment) rights on `rg-docuaction-prod`.
3. **Python 3.12** toolchain locally (matching the App Service runtime) for building Linux wheels.
4. **Deployment secrets** available from the approved secret store (Key Vault or the operations
   password manager). Never source secrets from source control, tickets, or chat.
5. **Change ticket / approval** for the target environment per the release process.
6. Confirm the ALLOWED_HOSTS and ALLOWED_ORIGINS values for the target environment
   (see `environment-topology.md`). A missing host entry causes **HTTP 400** on all routes,
   including `/health`.

---

## 4. Build the Deployment Artifact (zip)

The backend is deployed as a **zip package** containing the application code plus its
dependencies pre-built as **Linux cp312 wheels**. Dependencies are placed in a `pydeps/`
directory and made importable via `PYTHONPATH`. This avoids relying on Oryx build-time
resolution and produces a reproducible artifact.

> **Important — do not name the dependency directory `antenv`.** The Oryx/App Service startup
> optimizer treats a top-level `antenv` as a managed virtual environment and can skip or
> override it. Use a neutral name such as `pydeps/`.

### 4.1 Resolve Linux cp312 wheels

From the repository root, download platform-specific wheels for the App Service runtime:

```bash
# Build dependencies for Linux / CPython 3.12 into ./pydeps
python -m pip download \
  --requirement requirements.txt \
  --dest ./wheelhouse \
  --only-binary=:all: \
  --platform manylinux2014_x86_64 \
  --python-version 312 \
  --implementation cp \
  --abi cp312

python -m pip install \
  --requirement requirements.txt \
  --target ./pydeps \
  --no-index \
  --find-links ./wheelhouse
```

### 4.2 Assemble the zip

Include the application package, Alembic assets, and `pydeps/`. Exclude local virtual
environments, caches, `.env` files, and test artifacts.

```bash
zip -r deploy.zip \
  app/ \
  alembic/ \
  alembic.ini \
  requirements.txt \
  pydeps/ \
  -x '*/__pycache__/*' '*.pyc' '.env' '.env.*' 'tests/*'
```

The resulting `deploy.zip` is the immutable deployment artifact. Record its checksum for the
release record:

```bash
sha256sum deploy.zip
```

---

## 5. Configure App Service Application Settings

All runtime configuration is supplied as **App Service application settings** (environment
variables). The full catalog with purposes and placeholder values is documented in
`environment-topology.md` — that table is authoritative. Populate every required variable for
the target environment.

### 5.1 Required-at-boot variables

`SECRET_KEY` and `DATABASE_URL` are **required at boot**; the app will not start without them.
`ALLOWED_HOSTS` and `ALLOWED_ORIGINS` must be set correctly or requests are rejected.

### 5.2 PYTHONPATH and platform flags

```bash
az webapp config appsettings set \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --settings \
    PYTHONPATH="/home/site/wwwroot/pydeps" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="false" \
    WEBSITES_PORT="8000"
```

### 5.3 Core application settings (example — use placeholders, never real secrets)

```bash
az webapp config appsettings set \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --settings \
    ENVIRONMENT="production" \
    SECRET_KEY="<from-key-vault>" \
    DATABASE_URL="postgresql://<user>:<password>@docuaction-db.postgres.database.azure.com:5432/<db>?sslmode=require" \
    ALLOWED_HOSTS="docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net,api-prod.docuaction.io" \
    ALLOWED_ORIGINS="https://app.docuaction.io" \
    ENABLE_DOCS="false" \
    ENABLE_SCHEDULER="true"
```

> Set the remaining variables (AI providers, Identity/SSO, Email, TEFCA connectors, Bulletin
> flags, URLs) per the environment-topology catalog. Prefer **Key Vault references** for all
> secret-bearing settings so that rotation does not require redeployment.

### 5.4 Verify settings

```bash
az webapp config appsettings list \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --output table
```

> Application-setting changes trigger an App Service restart. Confirm settings are complete
> **before** deploying the code artifact to avoid a boot failure loop.

---

## 6. Configure the Startup Command

The backend starts via `python -m gunicorn` with uvicorn workers. Configure the App Service
startup command:

```bash
az webapp config set \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --startup-file "python -m gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --timeout 120"
```

Adjust `--workers` to the App Service plan sizing. Ensure `--bind` port matches `WEBSITES_PORT`.

---

## 7. Deploy the Artifact

Deploy the zip using the modern one-deploy endpoint:

```bash
az webapp deploy \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --src-path deploy.zip \
  --type zip
```

Monitor the deployment and startup logs:

```bash
az webapp log tail \
  --resource-group rg-docuaction-prod \
  --name Docuaction
```

---

## 8. Run Database Migrations (Alembic)

Schema changes are managed with **Alembic** (`alembic.ini` + `alembic/`). Run migrations after
the app settings (specifically `DATABASE_URL`) are in place and before declaring the release
healthy.

Preferred: run migrations from the App Service SSH console so they execute against the correct
network context and SSL-required database:

```bash
# From the App Service SSH session (Development Tools > SSH)
cd /home/site/wwwroot
export PYTHONPATH=/home/site/wwwroot/pydeps
python -m alembic current
python -m alembic upgrade head
python -m alembic current   # confirm head revision
```

> **Cautions:**
> - Take or confirm a fresh database backup / restore point before applying migrations (see
>   `../runbooks/backup-restore.md`).
> - Migrations against `docuaction-db` require `sslmode=require`.
> - Never run migrations against the wrong environment. Verify `DATABASE_URL` targets the
>   intended server before `upgrade head`.

---

## 9. ALLOWED_HOSTS / ALLOWED_ORIGINS Validation

The backend enforces **TrustedHost middleware** and **strict CORS**:

- `ALLOWED_HOSTS` must list the Azure default host **and** the custom domain
  (`api-prod.docuaction.io`). An unlisted `Host` header returns **HTTP 400** on all routes,
  including `/health`.
- `ALLOWED_ORIGINS` (a.k.a. `CORS_ORIGINS`) must be `https://app.docuaction.io` for prod.

Confirm both are set (Section 5.3) before verification. A 400 on `/health` from the custom
domain almost always indicates a missing `ALLOWED_HOSTS` entry.

---

## 10. Custom Domain and Managed TLS

1. **Add the custom hostname** (DNS validation via CNAME/TXT to the App Service default host):
   ```bash
   az webapp config hostname add \
     --resource-group rg-docuaction-prod \
     --webapp-name Docuaction \
     --hostname api-prod.docuaction.io
   ```
2. **Provision an App Service managed certificate** and bind it (SNI SSL):
   ```bash
   az webapp config ssl create \
     --resource-group rg-docuaction-prod \
     --name Docuaction \
     --hostname api-prod.docuaction.io

   # Bind the managed certificate (use the thumbprint returned above)
   az webapp config ssl bind \
     --resource-group rg-docuaction-prod \
     --name Docuaction \
     --certificate-thumbprint <thumbprint> \
     --ssl-type SNI
   ```
3. Confirm `api-prod.docuaction.io` is present in `ALLOWED_HOSTS` (Section 9).
4. Enforce HTTPS-only:
   ```bash
   az webapp update \
     --resource-group rg-docuaction-prod \
     --name Docuaction \
     --set httpsOnly=true
   ```

---

## 11. Post-Deploy Verification

1. **Health endpoint** (default host and custom domain):
   ```bash
   curl -sSf https://docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net/health
   curl -sSf https://api-prod.docuaction.io/health
   ```
   Both must return a successful health response. A 400 indicates an `ALLOWED_HOSTS` gap.
2. **Migration state** — `python -m alembic current` reports the expected head revision.
3. **Scheduler** — if `ENABLE_SCHEDULER=true`, confirm APScheduler daily jobs registered in
   the startup logs (see the scheduler self-heal watchdog notes in the on-call guide).
4. **CORS** — confirm the frontend (`app.docuaction.io`) can call the API without CORS errors.
5. **Docs gating** — confirm Swagger visibility matches `ENABLE_DOCS` for the environment
   (prod should be `false`).
6. **Defender for Cloud** — confirm no new high-severity alerts against the App Service or the
   PostgreSQL server following the change.
7. Record the artifact checksum, head revision, and verification results in the release ticket.

---

## 12. Rollback

If verification fails or a regression is detected, follow `rollback-procedures.md`. Do not
attempt ad-hoc fixes in production; roll back to the last known-good artifact and configuration
and re-attempt in a controlled window.

---

## 13. Frontend Deployment (Azure Static Web Apps)

> **Status of this section.** Added 2026-07-26 to close a documented gap: prior to this,
> no frontend deployment procedure existed in any repository document or CI workflow.
> The resource facts below were read live from Azure. **The deploy commands themselves
> have NOT yet been executed against these resources by the author of this section** —
> treat the first run as a validation run, ideally against dev, and correct this section
> from what actually happens.

### 13.1 Resources

| | Production | Development |
|---|---|---|
| Static Web App | `docuaction-frontend` | `docuaction-frontend-dev` |
| Resource group | `rg-docuaction-prod` | `rg-docuaction-dev` |
| Default hostname | `witty-tree-0a448a70f.7.azurestaticapps.net` | `witty-dune-0dd70870f.7.azurestaticapps.net` |
| Custom domain | **`app.docuaction.io`** (live) | none |
| SKU | **Free** | **Free** |
| Linked repo / branch | **none** | **none** |

Two consequences of that last row, both load-bearing:

- **There is no GitHub Actions CI for the frontend.** `.github/workflows/` contains only
  `codeql.yml`, `dependency-review.yml`, and `security-scan.yml`. Deployment is manual
  via the SWA CLI, and nothing deploys on push.
- **Free SKU keeps no deployment history and offers no environment rollback.** There is
  no "previous version" to promote. Rollback means rebuilding from an earlier commit and
  redeploying — see §13.5.

### 13.2 Build

The app is a Next.js static export (`output: 'export'` in `next.config.mjs`), so the
build produces a plain directory of static assets in `out/`.

```bash
cd "C:/Imran_Coding projects/DocuAction/frontend"
npm ci                 # use ci, not install, for a reproducible tree
npm run build          # -> next build -> static export into ./out
```

Expected: **exit 0**. Three warnings are expected and benign — the `redirects` /
`rewrites` / `headers` notices, which do not apply under `output: 'export'`:

```
⚠ Specified "redirects" will not automatically work with "output: export".   (x2)
⚠ rewrites, redirects, and headers are not applied when exporting your application
```

Anything beyond those three is a new warning and should be investigated.

### 13.3 Deployment tokens

Each Static Web App has its own deployment token. Retrieve it at deploy time; **do not
commit it, paste it into a ticket, or echo it into a shell transcript.**

```bash
# Production
az staticwebapp secrets list --name docuaction-frontend \
  --resource-group rg-docuaction-prod --query "properties.apiKey" -o tsv

# Development
az staticwebapp secrets list --name docuaction-frontend-dev \
  --resource-group rg-docuaction-dev --query "properties.apiKey" -o tsv
```

Also available in the Azure Portal under the Static Web App → **Overview → Manage
deployment token**. Prefer piping the token into an environment variable over passing it
as a literal argument, so it does not land in shell history:

```bash
export SWA_DEPLOYMENT_TOKEN=$(az staticwebapp secrets list --name docuaction-frontend \
  --resource-group rg-docuaction-prod --query "properties.apiKey" -o tsv)
```

If a token is ever exposed, rotate it: `az staticwebapp secrets reset-api-key`.

### 13.4 Deploy

Deploy **dev first**, validate, then production.

```bash
# --- DEV ---
npx @azure/static-web-apps-cli deploy ./out \
  --deployment-token "$SWA_DEV_DEPLOYMENT_TOKEN" \
  --env production

# --- PRODUCTION ---
npx @azure/static-web-apps-cli deploy ./out \
  --deployment-token "$SWA_DEPLOYMENT_TOKEN" \
  --env production
```

`--env production` is correct for **both**: it names the environment *within* each Static
Web App, not the deployment tier. On the Free SKU it is the only available environment.

`staticwebapp.config.json` lives in `public/` and is copied into `out/` by the export, so
routing and header rules ship with the artifact — no separate step.

### 13.5 Rollback

There is no deployment history to roll back to on the Free SKU. Rollback is
**rebuild-and-redeploy from a known-good commit**:

```bash
cd "C:/Imran_Coding projects/DocuAction/frontend"
git checkout <last-known-good-tag-or-sha>     # e.g. the previous release tag
npm ci && npm run build
npx @azure/static-web-apps-cli deploy ./out \
  --deployment-token "$SWA_DEPLOYMENT_TOKEN" --env production
git checkout main                              # restore your working branch
```

Alternatively revert the offending commit on `main`, then rebuild and redeploy:

```bash
git revert <sha> && npm ci && npm run build && <deploy command above>
```

**Tag every frontend release** so a known-good build is always addressable by name. Keep
the previous release tag identified in the release ticket *before* deploying.

### 13.6 Verification

1. **Default host** loads: `https://witty-tree-0a448a70f.7.azurestaticapps.net`
2. **Custom domain** loads: `https://app.docuaction.io`
3. **Login** succeeds and stores a token under the `token` localStorage key.
4. **Authenticated API calls carry the bearer token** — open DevTools → Network on
   `/case-management` and confirm `Authorization: Bearer …` is present on requests to
   `/api/v1/case-management/*`, and that they return **200**, not 403.
5. **TEFCA Registry pages** load: `/tefca-registry`, `/tefca-registry/entities`,
   `/tefca-registry/issues`, `/tefca-registry/verification`.
6. **No CORS errors** in the console — the API's `ALLOWED_ORIGINS` must include the host
   being served.
7. **Hard-refresh** (Ctrl-F5) at least one page to confirm you are not validating a
   cached bundle.

### 13.7 Ordering constraint — read before deploying

**Never deploy the frontend ahead of a backend release that the frontend depends on.**
As of Sprint 1 the `/case-management` page sends an `Authorization` header that the
backend auth gate must be in place to accept.

- Backend first, or both together → safe.
- Frontend-only against an older backend → the header is simply ignored; harmless.
- **Backend-only without the frontend → `/case-management` returns 403 for every user.**

---

## 14. Change Record

| Field | Value |
|-------|-------|
| Document owner | Platform Operations, Alliance Global Tech, Inc. |
| Applies to | DocuAction backend v6.0.0 |
| Review cadence | Each release or quarterly, whichever is sooner |
| Security contact | security@agtbi.com |
| 2026-07-26 | Added §13 Frontend Deployment (Azure Static Web Apps) — closes the gap identified in the Sprint 1 pre-merge review. Commands not yet executed against these resources; validate on dev and correct. |
