# Backup and Restore Procedure

**Contract:** 7571MN26F80064  ·  **Date:** 2026-08-02  ·  **Git SHA:** `706a2f641f3a48f3dc117f57d579ddc82dbd5686`

## Database — PostgreSQL on Azure Flexible Server

Retention values below were read from Azure at the date above, not assumed.

| Server | Resource Group | Retention | Geo-redundant | Tier | Version |
|--------|----------------|-----------|---------------|------|---------|
| `docuaction-db` | rg-docuaction-prod | **14 days** | Disabled | Burstable | 16 |
| `docuaction-db-geo` | rg-docuaction-prod | **14 days** | **Enabled** | Burstable | 16 |
| `docuaction-db-dev` | rg-docuaction-dev | **7 days** | Disabled | Burstable | 16 |

- **Backup:** Azure automated daily backups with continuous transaction log
  capture, enabling point-in-time restore anywhere in the retention window.
- **Restore:** Azure portal → the server → *Restore* → point-in-time. This
  provisions a **new** server; it does not restore in place.

```
az postgres flexible-server restore \
  --name docuaction-db-restored \
  --resource-group rg-docuaction-prod \
  --source-server docuaction-db \
  --restore-time "2026-08-01T12:00:00Z"
```

After a restore, repoint `DATABASE_URL` on the App Service to the restored server
and restart.

### Geo-redundancy caveat

`docuaction-db` (the primary in use) has geo-redundant backup **Disabled**.
Geo-redundancy on Azure Flexible Server is a **create-time-only** setting — it
cannot be enabled on an existing server. `docuaction-db-geo` exists with it
enabled and is the intended destination at cutover. Until cutover, prod backups
are regionally redundant only.

## Application

| Item | Value |
|------|-------|
| Source of truth | GitHub — backend and frontend repositories |
| Deploy artifact | `prod-deploy.zip` (built with Python `zipfile`) |
| Rollback artifact | `prod-deploy.prev.zip` — the previously deployed zip |

Build zips with Python `zipfile`, never PowerShell `Compress-Archive`: PowerShell
writes **backslash** path separators into the archive, which Linux App Service
does not interpret as directories. A `--clean true` deploy of such an archive
removes the working application and replaces it with unusable flat files. This
has happened once and is the reason the rule exists.

## Deployment rollback

1. Identify the issue on production.
2. Deploy the previous artifact:
   ```
   az webapp deploy --name Docuaction --resource-group rg-docuaction-prod \
     --src-path prod-deploy.prev.zip --type zip --clean true --restart true
   ```
3. **Restart explicitly**, then verify:
   ```
   az webapp restart --name Docuaction --resource-group rg-docuaction-prod
   curl -s -o /dev/null -w '%{http_code}' https://api.docuaction.io/health
   curl -s https://api.docuaction.io/api/config
   ```
   `/health` must return 200 and `/api/config` must report
   `environment=production`.
4. Investigate root cause on **dev**, never on prod.

### Two rules learned the hard way

- **A deployment status of "4 / active" is not proof the new code is serving.**
  Every deploy in this programme has required an explicit `az webapp restart`
  before new behaviour appeared. Verify with an endpoint that exists only in the
  new build.
- **An `az webapp deploy` CLI error is not proof the deploy failed.** The command
  frequently prints `RemoteDisconnected` while the server continues building.
  Never retry blindly — query deployment status instead. A blind retry during a
  live build is how partial deploys happen.

## Not covered

- **Restore has not been rehearsed.** No test restore was performed for this
  contract; the procedure above is written from Azure's documented behaviour and
  the platform's own deploy history. A rehearsal is recommended before ATO.
  Recovery time objective: **Not Executed** — unmeasured.
- Key Vault secret backup is not covered here.
