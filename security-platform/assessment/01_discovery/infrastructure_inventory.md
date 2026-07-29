# Infrastructure Inventory — DocuAction (Azure)

**Subscription:** AGT-DocuAction · **Read-only via `az` CLI.**

## Resource groups

### `rg-docuaction-prod`
| Type | Name | Notes |
|---|---|---|
| App Service | **Docuaction** | Linux, `PYTHON\|3.12`, gunicorn+uvicorn worker; custom domain **api-prod.docuaction.io**; SCM basic-auth **disabled** (AAD only) |
| App Service Plan | ASP-rgdocuactionprod-8aa2 | **P0v3 (Premium0V3), capacity 1** — single instance (no scale-out/HA) |
| PostgreSQL Flexible | **docuaction-db-geo** | **PG 16**, Burstable, 32 GB, 14-day backup, **geo-redundant backup ✅**, **HA Disabled** ⚠ |
| PostgreSQL Flexible | docuaction-db | **⚠ second server — likely legacy/unused (Railway→Azure migration remnant). Confirm + decommission for cost.** |
| Key Vault | **docuaction-kv-prod** | **PRIVATE ENDPOINT** (`docuaction-kv-pe`) + `privatelink.vaultcore.azure.net` private DNS + VNet ✅ strong |
| Static Web App | docuaction-frontend | **app.docuaction.io** / witty-tree-0a448a70f |
| VNet | docuaction-vnet | hosts the KV private endpoint |
| App Insights | docuaction-appinsights | APM |
| Log Analytics | docuaction-logs | log workspace |
| Metric alerts | docuaction-availability, docuaction-5xx-errors, docuaction-high-cpu, docuaction-db-availability | ✅ 4 alerts |
| Action group | docuaction-alerts (+ App Insights Smart Detection) | notification target |
| TLS cert | api-prod.docuaction.io-Docuaction | managed cert |

### `rg-docuaction-dev` (parallel lower environment)
| Type | Name |
|---|---|
| App Service | docuaction-dev |
| App Service Plan | asp-docuaction-dev |
| PostgreSQL Flexible | docuaction-db-dev |
| Key Vault | docuaction-kv-dev |
| Static Web App | docuaction-frontend-dev (witty-dune-0dd70870f) |

## Identity & secrets
- **Managed Identity** on the App Service; secrets delivered as **Key Vault references** (confirmed: reading `SECRET_KEY` via `az` returns a KV reference, not the resolved value — the runtime resolves it).
- **22 app settings** on prod. Categorized:
  - **Secrets (should be KV-backed):** `SECRET_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `SENDGRID_API_KEY`, `AZURE_AD_CLIENT_SECRET`, `APPLICATIONINSIGHTS_CONNECTION_STRING`/`APPINSIGHTS_INSTRUMENTATIONKEY`.
  - *Note:* `DATABASE_URL` observed as a **direct** connection string (username/password inline) — a hardening opportunity (move to KV ref / passwordless MI-to-Postgres). Documented only.
  - **Config (non-secret):** `ALLOWED_HOSTS`, `ALLOWED_ORIGINS`, `APP_URL`, `AZURE_AD_CLIENT_ID`, `AZURE_AD_TENANT_ID`, `EMAIL_FROM`, `EMAIL_FROM_NAME`, `BULLETIN_AUTH_ENABLED`, `ENABLE_SCHEDULER`(=true), `PYTHONPATH`(=/home/site/wwwroot/pydeps), `ENABLE_ORYX_BUILD`, `SCM_DO_BUILD_DURING_DEPLOYMENT`(=false), `WEBSITES_CONTAINER_START_TIME_LIMIT`(=300), `WEBSITE_DNS_SERVER`, `WEBSITE_HTTPLOGGING_RETENTION_DAYS`.

## Deployment model (observed)
- **Backend:** files deployed to `/home/site/wwwroot` via **Kudu VFS** (incremental PUT); dependencies staged in `pydeps/` (Linux cp312 wheels) + `PYTHONPATH`; startup `python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker`. Table creation via runtime `Base.metadata.create_all` (not Alembic on prod). `SCM_DO_BUILD_DURING_DEPLOYMENT=false` (no Oryx build on deploy).
- **Frontend:** Next static export (`out/`) deployed to both SWAs via **Azure SWA CLI** (`swa deploy --env production`).
- **Docker:** a `Dockerfile` exists (`python:3.12-slim`, installs `ffmpeg`, `uvicorn` on :8080) but is **not** the prod runtime path (Azure Linux uses Oryx image + gunicorn). Divergence between Dockerfile and actual prod runtime — documentation gap.

## CI/CD — GitHub Actions
| Workflow | Purpose |
|---|---|
| `codeql.yml` | SAST (CodeQL) |
| `dependency-review.yml` | PR dependency review / supply chain |
| `security-scan.yml` | security scanning |

**Gap:** No **deploy** workflow observed — backend/frontend deployment is currently **manual** (Kudu VFS + SWA CLI). No automated tests or gated release pipeline. Rollback is manual (prior file re-PUT / prior build re-deploy).

## Monitoring / backup / DR
- **Monitoring:** App Insights + Log Analytics + 4 metric alerts + action group ✅.
- **Backup:** PG geo-redundant backup enabled, 14-day retention ✅.
- **DR / HA:** PostgreSQL **HA disabled**; App Service Plan **capacity 1** — **no high availability**; single-region compute. DR relies on geo-redundant backups (restore, not failover). ⚠ Documented for Part 9.
- **Network:** Key Vault behind private endpoint; App Service and Postgres reachability/firewall not fully enumerated in this pass (Part 9 to detail).
