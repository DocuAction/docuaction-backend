# Azure Operations Review

> From `backend/infra/` **Bicep IaC** (`main.bicep` + 7 modules + `parameters.prod.json`) and ops docs. The README frames the Bicep as *reconstructed from live to document/drive drift-review* — so it mirrors prod but is **not** the deployment mechanism (deploys are manual `az`/SWA-CLI). Read-only. Verify `(*)`-flagged items against the live tenant.

## App Service (`modules/appService.bicep`, `parameters.prod.json`)
- Plan **ASP-rgdocuactionprod-8aa2**, SKU **P0v3 (Premium0V3)**, Linux (`reserved:true`), **capacity 1**.
- `alwaysOn:true`, `httpsOnly:true`, `minTlsVersion:1.2`, `ftpsState:FtpsOnly`, `http20Enabled:false`, health check `/health`.
- Startup: `python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000` (deploy guide: `--workers 4 --timeout 120`).
- **No auto-scale** (`Microsoft.Insights/autoscalesettings` absent), **no zone redundancy** → fixed single instance = **single point of failure, no horizontal scaling**.
- Key settings: `SCM_DO_BUILD_DURING_DEPLOYMENT=false`, `ENABLE_ORYX_BUILD=false`, `PYTHONPATH=/home/site/wwwroot/pydeps`, `WEBSITE_HTTPLOGGING_RETENTION_DAYS=3`, `ENABLE_SCHEDULER=true`, `BULLETIN_AUTH_ENABLED=true`.
- System-assigned managed identity; `keyVaultReferenceIdentity:SystemAssigned`; **Key Vault Secrets User** role granted.

## PostgreSQL (`modules/postgresql.bicep`)
- **docuaction-db**, PostgreSQL **v16**, **Standard_B1ms / Burstable**, **32GB** Premium_LRS autogrow, AZ 1.
- **`highAvailability.mode: Disabled`** — **no HA**.
- **`geoRedundantBackup: Disabled`** (create-time-only per memory), backup retention **7 days**.
- **`publicNetworkAccess: Enabled`** + firewall rule `0.0.0.0` (AllowAllAzureServicesAndResourcesWithinAzureIps) → **Postgres is publicly reachable**, gated only by the Azure-services firewall.
- `activeDirectoryAuth: Disabled`, `passwordAuth: Enabled` — **password-only, no Entra DB auth**. SSL `sslmode=require` is app-side (deploy guide), not visible as a server param. No connection-limit tuning.

## Static Web App (`modules/staticWebApp.bicep`, `frontend/public/staticwebapp.config.json`)
- **docuaction-frontend**, **Free** tier, `enterpriseGradeCdnStatus: Disabled`, deployed via **SWA CLI, no linked GitHub repo**. Custom domain `app.docuaction.io` managed **out of band** (not in IaC).
- Config headers: `nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy` — **no CSP, no HSTS** (Part 8 SH-03/04).

## Key Vault (`modules/keyVault.bicep`)
- **docuaction-kv-prod**, standard, **RBAC-authorized** (not access policies), soft-delete + **purge protection**, 90-day retention.
- **`publicNetworkAccess: Enabled` in prod params.** The private-endpoint posture exists only in `networking.bicep`, which is **authored but NOT deployed** (module + README both state this). `(*)` **Contradicts the memory note of a live KV private endpoint — verify against live.**
- Secrets: `SECRET-KEY`, `ANTHROPIC-API-KEY`, `AZURE-AD-CLIENT-SECRET`, `SENDGRID-API-KEY` (4). **`DATABASE_URL` deliberately left as a direct app setting** (embeds DB password) — documented known gap (Part 8 SEC-03). **No rotation policy** on any secret.

## Monitoring (`modules/monitoring.bicep`)
- Log Analytics **docuaction-logs** (PerGB2018, 90d), workspace-based App Insights **docuaction-appinsights** (90d). **No sampling config.**
- Action group **docuaction-alerts** → **single email `imran@agtbi.com`** (no PagerDuty/SMS/webhook — bus factor).
- **4 metric alerts:** availability (HealthCheckStatus <100, sev1), 5xx (>10, sev2), high CPU (>80%, sev2), DB availability (`is_db_alive`<1, sev1).
- **Gaps:** no alerts for **memory, latency/P95, DB storage/connection saturation, Key Vault access anomalies, or cert expiry**. **No `Microsoft.Insights/diagnosticSettings`** resource anywhere — diagnostic-log routing to Log Analytics is not modeled.
- **App Insights is configured but NOT code-instrumented** — connection string injected as an app setting, but **no SDK wiring in `app/`** (no `applicationinsights`/`azure.monitor`/`configure_azure_monitor`). Telemetry depends on the App Service auto-instrumentation agent only; no custom traces/exceptions/dependency tracking.

## Networking (`modules/networking.bicep`)
- VNet `10.0.0.0/16` + `private-endpoints` subnet, **KV** private endpoint, `privatelink.vaultcore.azure.net` zone. **Explicitly not deployed**; even if applied it only builds the **KV** PE — **App Service is not VNet-integrated, Postgres is not behind a private endpoint** (still public).
- **No NSGs, no App Service `ipSecurityRestrictions`** anywhere → App Service is open to the internet (auth-gated at the app layer via TrustedHost/CORS).

## Defender for Cloud (`modules/defender.bicep`)
- Subscription-scoped, **not wired into `main.bicep`** (deploy separately). **6 Standard plans:** SqlServers, AppServices, KeyVaults (PerKeyVault), OpenSourceRelationalDatabases, Discovery, FoundationalCspm. `(*)` (memory listed 4; IaC enables 6 — verify live.) No captured Defender recommendations in-repo.

## IaC posture
Real, well-structured **Bicep** (no Terraform/ARM-JSON) — a genuine strength for documentation and drift-review. **But no pipeline invokes it** (Part 9 CI/CD), so it is not enforced/reconciled → **drift risk**. Custom domains, TLS bindings, and deployment slots are **not modeled** and are managed by manual `az` commands.

## Azure ops verdict
**Strong intent, thin resilience & exposure hardening.** Good: P0v3 + alwaysOn + httpsOnly + minTLS1.2, RBAC Key Vault + MI + purge protection, Defender Standard (6 plans), monitoring workspace + core alerts, real Bicep. **Weak:** single instance (no autoscale/HA), Postgres **Burstable B1ms with HA Disabled** + geo-backup Disabled, **public Key Vault + public Postgres**, no App Service IP restrictions, private endpoints authored-not-deployed, App Insights not code-instrumented, single-recipient email alerting with notable metric gaps. The resilience and network-exposure items are the ones to prioritize for a healthcare production posture.

## NIST mapping
CP-9/CP-10 (backup/DR — 7d, no geo, no HA) ◐, SC-7 (boundary — public KV/PG) ◐, AU-6/SI-4 (monitoring — gaps) ◐, CM-2/CM-3 (baseline/IaC — present but not enforced) ◐.
