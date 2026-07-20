# DocuAction — Infrastructure as Code (Bicep)

Infrastructure-as-Code for the DocuAction Azure environment. The templates were
authored to match the **live** `rg-docuaction-prod` footprint (SKUs, versions,
and API shapes were read directly from the deployed resources), so they can be
used to recreate the environment (dev), document prod, or drive drift review.

> **Region:** `eastus2` · **Subscription:** `6ce81f40-7f0f-4e6d-97e3-2569b4d18611`
> (AGT-DocuAction) · **Prod RG:** `rg-docuaction-prod`

---

## Layout

```
infra/
  main.bicep                  Resource-group-scoped orchestrator (all RG modules)
  parameters.prod.json        Production parameter values
  parameters.dev.json         Dev parameter values (docuaction-dev, rg-docuaction-dev)
  modules/
    appService.bicep          P0v3 Linux plan + Python 3.12 Web App (MI, /health, KV refs)
    postgresql.bicep          PostgreSQL Flexible Server v16, Burstable B1ms, 32 GB
    keyVault.bicep            Key Vault (RBAC, soft-delete + purge-protection, 90-day)
    monitoring.bicep          Log Analytics + App Insights + action group + 4 metric alerts
    networking.bicep          VNet + Key Vault private endpoint + private DNS (additive)
    staticWebApp.bicep        Static Web App (Free)
    defender.bicep            Microsoft Defender plans — SUBSCRIPTION SCOPE, deploy separately
  README.md                   This file
```

---

## What each module creates

| Module | Resources | Notes from the live environment |
|--------|-----------|---------------------------------|
| `appService.bicep` | `Microsoft.Web/serverfarms` (P0v3, Linux, `reserved:true`), `Microsoft.Web/sites` (`PYTHON\|3.12`, system-assigned identity, `/health`, HTTPS-only, TLS 1.2, `FtpsOnly`), Key Vault Secrets User role assignment | App command line: `python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000`. App settings include `@Microsoft.KeyVault(...)` references for `SECRET_KEY`, `ANTHROPIC_API_KEY`, `AZURE_AD_CLIENT_SECRET`, `SENDGRID_API_KEY`. |
| `postgresql.bicep` | `Microsoft.DBforPostgreSQL/flexibleServers` (v16, `Standard_B1ms` Burstable, 32 GB Premium_LRS autogrow, 7-day backup, AZ 1), firewall rule for Azure services | Public network access **Enabled** with firewall; password auth only (Entra auth disabled). |
| `keyVault.bicep` | `Microsoft.KeyVault/vaults` (standard, RBAC, soft-delete, purge-protection, 90-day) + optional secrets | Matches `docuaction-kv-prod`. Secrets are created only when `createKeyVaultSecrets=true`. |
| `monitoring.bicep` | `Microsoft.OperationalInsights/workspaces` (PerGB2018, 90-day), workspace-based `Microsoft.Insights/components`, `actionGroups` (email), 4 `metricAlerts` | Alerts: **availability** (HealthCheckStatus < 100, sev1), **5xx** (Http5xx > 10, sev2), **high CPU** (CpuPercentage > 80 on the plan, sev2), **DB availability** (`is_db_alive` < 1, sev1). |
| `networking.bicep` | `virtualNetworks` (10.0.0.0/16 + `private-endpoints` 10.0.1.0/24), Key Vault `privateEndpoints`, `privateDnsZones` (`privatelink.vaultcore.azure.net`) + VNet link + zone group | **Additive / not currently deployed in prod.** Models the target private-networking posture; applying it does not by itself disable Key Vault public access. Gated by `deployNetworking` (prod `true`, dev `false`). |
| `staticWebApp.bicep` | `Microsoft.Web/staticSites` (Free) | Matches `docuaction-frontend`. Deployed in prod via the SWA CLI, so no linked repo/branch. Custom domain `app.docuaction.io` is managed out of band. |
| `defender.bicep` | `Microsoft.Security/pricings` × 6 (Standard) | **Subscription-scoped — NOT part of `main.bicep`.** See below. |

---

## Prerequisites

1. **Azure CLI + Bicep** — `az bicep upgrade`.
2. **Login & subscription:**
   ```powershell
   az login
   az account set --subscription 6ce81f40-7f0f-4e6d-97e3-2569b4d18611
   ```
   Use **PowerShell** on Windows — git-bash mangles Azure resource IDs.
3. **Register resource providers** (once per subscription):
   ```powershell
   az provider register --namespace Microsoft.Web
   az provider register --namespace Microsoft.DBforPostgreSQL
   az provider register --namespace Microsoft.KeyVault
   az provider register --namespace Microsoft.OperationalInsights
   az provider register --namespace Microsoft.Insights
   az provider register --namespace Microsoft.Network
   az provider register --namespace Microsoft.Security
   ```
4. **RBAC** — the deploying principal needs `Owner` (or `Contributor` + `User Access
   Administrator`, because `appService.bicep` creates a Key Vault Secrets User role
   assignment), plus **Key Vault data-plane** rights if `createKeyVaultSecrets=true`.
5. **Replace the `REPLACE_ME` placeholders** — `postgresAdminPassword`, `secretKey`,
   `anthropicApiKey`, `azureAdClientSecret`, `sendGridApiKey` are `@secure()`
   parameters. Never commit real values. Pass them at deploy time (see below) or
   supply them from a secret store / pipeline variable group.

---

## Validate (no changes made)

```powershell
az bicep build --file infra/main.bicep

az deployment group validate `
  --resource-group rg-docuaction-prod `
  --template-file infra/main.bicep `
  --parameters "@infra/parameters.prod.json"
```

Both are expected to succeed. Provide the secure params inline to avoid the
`REPLACE_ME` placeholders when validating against a fresh RG:

```powershell
  ... --parameters "@infra/parameters.prod.json" `
      --parameters postgresAdminPassword=<pwd> secretKey=<key> `
                   anthropicApiKey=<key> azureAdClientSecret=<secret> `
                   sendGridApiKey=<key>
```

---

## Deploy

> Prefer `what-if` before any `create` to review the change set.

```powershell
# Preview
az deployment group what-if `
  --resource-group rg-docuaction-prod `
  --template-file infra/main.bicep `
  --parameters "@infra/parameters.prod.json" `
  --parameters postgresAdminPassword=<pwd> secretKey=<key> anthropicApiKey=<key> `
               azureAdClientSecret=<secret> sendGridApiKey=<key>

# Apply
az deployment group create `
  --resource-group rg-docuaction-prod `
  --template-file infra/main.bicep `
  --parameters "@infra/parameters.prod.json" `
  --parameters postgresAdminPassword=<pwd> secretKey=<key> anthropicApiKey=<key> `
               azureAdClientSecret=<secret> sendGridApiKey=<key>
```

### Dev

Target the dev RG with the dev params file (names `docuaction-dev`,
`docuaction-kv-dev`, etc.; `deployNetworking=false`):

```powershell
az deployment group create `
  --resource-group rg-docuaction-dev `
  --template-file infra/main.bicep `
  --parameters "@infra/parameters.dev.json" `
  --parameters postgresAdminPassword=<pwd> secretKey=<key> anthropicApiKey=<key> `
               azureAdClientSecret=<secret> sendGridApiKey=<key>
```

---

## Microsoft Defender plans (separate, subscription-scoped)

`Microsoft.Security/pricings` is a **subscription-level** resource and cannot be
deployed from a resource-group deployment, so it is deliberately **not** wired
into `main.bicep`. Deploy it on its own:

```powershell
az deployment sub create `
  --location eastus2 `
  --template-file infra/modules/defender.bicep
```

It enables the six Standard plans active on the subscription: `SqlServers`,
`AppServices`, `KeyVaults` (`PerKeyVault`), `OpenSourceRelationalDatabases`,
`Discovery`, `FoundationalCspm`.

---

## Notes, deviations & things not fully modeled

- **`DATABASE_URL`** is intentionally **not** emitted as an app setting (the live
  value embeds the DB password in plaintext). Store the connection string as a
  Key Vault secret and reference it the same way as the other secrets.
- **Networking is additive** — prod currently runs Key Vault with public access
  and no private endpoint. The module represents the intended hardened posture.
- **Custom domains / TLS bindings** (`api-prod.docuaction.io` on the Web App,
  `app.docuaction.io` on the Static Web App) and their certificates are **not**
  modeled — they depend on DNS/cert state managed outside this template.
- **App Insights Smart Detection** action group (auto-created by Azure) is not
  reproduced; only the `docuaction-alerts` email action group is defined.
- Key Vault **purge protection cannot be disabled** once enabled; redeploying
  over the existing vault keeps it on.
- All resource **names, locations, SKUs/tiers, and secret values are
  parameterized** (secrets/passwords via `@secure()`), with defaults supplied in
  the `parameters.*.json` files.
