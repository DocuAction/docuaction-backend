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

## 13. Change Record

| Field | Value |
|-------|-------|
| Document owner | Platform Operations, Alliance Global Tech, Inc. |
| Applies to | DocuAction backend v6.0.0 |
| Review cadence | Each release or quarterly, whichever is sooner |
| Security contact | security@agtbi.com |
