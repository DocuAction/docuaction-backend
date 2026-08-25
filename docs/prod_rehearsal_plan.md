# PROD Rehearsal Plan — PLAN ONLY, NOT EXECUTED

Prepared 2026-08-25 from the verified DEV closure state. Nothing in this document
has been run against PROD. PROD was read only.

## Why this document exists

DEV and PROD are not the same shape. The differences below are not cosmetic —
two of them would break the deployment outright, and one would produce a running
site that 503s on every PDF request. They are listed before the sequence because
the sequence is not safe to start until they are settled.

## Measured DEV vs PROD parity (read-only, 2026-08-25)

| | DEV `docuaction-dev` | PROD `Docuaction` |
|---|---|---|
| Runtime | `DOCKER\|acrdocuactiondev.azurecr.io/docuaction-backend:03020d45…` | `PYTHON\|3.12` built-in |
| Startup command | *(empty — image CMD is used)* | `python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 …` |
| Port | `WEBSITES_PORT=8080` | *(unset; app binds 8000 via startup command)* |
| Always On | true | true |
| Health check path | `/health` | `/health` |
| HTTPS only | true | true |
| Managed identity | SystemAssigned | SystemAssigned |
| MI role assignments | Key Vault Secrets User on DEV vault | Key Vault Secrets User on PROD vault; **no AcrPull anywhere** |
| VNet integration | `docuaction-vnet-dev/app-integration` | `docuaction-vnet/app-integration` |
| Route all outbound | true | true |
| FTPS | Disabled | Disabled |
| SCM basic auth | n/a | Disabled (AAD only) |
| PostgreSQL | `docuaction-db-dev`, PG16, B1ms, 7-day PITR, geo off | `docuaction-db-geo`, PG16, B1ms, **14-day** PITR, **geo on** |
| DB public access | Enabled | Enabled — **no private endpoint, no VNet injection**; 32 single-IP `appsvc-*` firewall rules, no human workstation permitted |
| Key Vault | `docuaction-kv-dev`, public disabled, RBAC on, 1 private endpoint | `docuaction-kv-prod` |
| Reporting stack | WeasyPrint 69.0 proven in image | **absent** — no Pango/Cairo/GObject on the built-in runtime |
| Container registry | `acrdocuactiondev` (Basic, admin disabled, DEV RG) | **none exists** |

### App settings present on one side only (names only, no values)

PROD only: `APPINSIGHTS_INSTRUMENTATIONKEY`, `APPLICATIONINSIGHTS_CONNECTION_STRING`,
`PERIGON_API_KEY`, `PYTHONPATH`, `WEBSITES_CONTAINER_START_TIME_LIMIT`.

DEV only: `AZURE_AD_POST_LOGIN_REDIRECT`, `DOCKER_REGISTRY_SERVER_URL`,
`WEBSITES_PORT`, `WEBSITES_ENABLE_APP_SERVICE_STORAGE`, `EMAIL_TEMPLATE_VERSION`.

`AZURE_AD_POST_LOGIN_REDIRECT` is absent from PROD **correctly**: the code default
in `app/api/azure_auth_routes.py` is already the PROD URL. It exists on DEV only
because DEV had to override that default. Do not copy it to PROD.

## Blocking prerequisites — settle before step 1

1. **No container registry reachable by PROD.** The only ACR is `acrdocuactiondev`,
   in the DEV resource group, with the admin user disabled. A PROD container
   cutover needs either a PROD ACR, or `AcrPull` granted to the PROD managed
   identity on the DEV registry. The latter couples PROD to a DEV-owned resource
   and is the worse option; it is recorded here only so the choice is explicit.
2. **The PROD managed identity holds no AcrPull anywhere.** It does hold Key
   Vault Secrets User on `docuaction-kv-prod`, so per-environment secret isolation
   is already correct; what it cannot do today is pull an image.
3. **Key Vault migration is deferred.** PROD secrets (`DATABASE_URL`, `SECRET_KEY`,
   `AZURE_AD_CLIENT_SECRET`, and the API keys) are plaintext app settings. The
   rehearsal does not fix this and must not be described as fixing it.
4. **PROD database contents are unknown.** The read-only inventory could not reach
   inside the database (see below). Rehearsing a migration against a database
   whose table and row counts nobody has measured is how a rehearsal becomes an
   incident.

### Why the PROD database inventory is still missing

CORRECTION (2026-08-25): an earlier revision of this document said the PROD app
reaches its database "over its private endpoint". That was wrong. Measured state:
`docuaction-db-geo` has `delegatedSubnetResourceId: null` and
`privateDnsZoneArmResourceId: null`, public network access **Enabled**, and 32
firewall rules named `appsvc-01`..`appsvc-32`, each a single App Service outbound
IP. There is **no private endpoint and no VNet injection** on the PROD database.
The app reaches it over the PUBLIC endpoint, allowlisted by IP.

This has two consequences. There is no private path to inventory the database.
And no human workstation is permitted today — every rule is an Azure App Service
IP — so the inventory cannot be run by an operator either without a firewall
change.

The intended path was therefore the App Service container itself, which is
allowlisted and already connects. The Kudu command API does authenticate with an ARM bearer token
(basic publishing credentials are disabled) and returns HTTP 200. But the Kudu
container for a Linux built-in-runtime site is a *separate* container from the
application, and it has no `python` on PATH, so it cannot open a database
connection. The application container, which does have the runtime and the
credentials, is not reachable through that API.

No firewall exception was created. This check is stopped, not worked around.

## Rehearsal sequence — DO NOT EXECUTE WITHOUT AUTHORIZATION

1. **Pre-change backup / recovery verification.** Confirm `docuaction-db-geo`
   PITR earliest-restore timestamp and that geo-redundant backup is Enabled.
   Record the exact UTC recovery point. Confirm a restore target name is chosen
   in advance — deciding it during an incident wastes the window.
2. **Capture current PROD configuration.** Full `az webapp config show`,
   `appsettings list` (names and values captured to a secured location, never to
   a transcript), `config container show`, site config, and the ARM template
   export. This is the rollback source of truth.
3. **Data-preservation verification.** Obtain the PROD table/row inventory
   (blocked today — see above). Classify every populated table before touching
   anything. PROD already contains data; the default disposition is PRESERVE.
4. **Schema / migration gate.** Determine the PROD Alembic revision. PROD has
   never run Alembic (DEV had not either until this engagement), so expect
   `alembic_version` to be absent and the schema to have been built by
   `create_all()`. Generate the offline SQL (`alembic upgrade --sql`) and review
   every statement before any of it runs. Do not run migrations as part of the
   container cutover — separate the two changes.
5. **Container deployment strategy.** Build the image from the exact commit that
   is proven on DEV. Do not rebuild from `main` at cutover time; promote the
   tested digest.
6. **AcrPull via managed identity.** Grant `AcrPull` to the PROD system-assigned
   identity on whichever registry is chosen in prerequisite 1. Verify with a pull
   before changing the site runtime.
7. **Startup and port settings.** Clear `appCommandLine` so the image `CMD` is
   used, and set `WEBSITES_PORT=8080` to match `EXPOSE`. These must change
   together: the current PROD command binds :8000 while the image serves :8080,
   and App Service would probe a port nothing is listening on. Use
   `az resource update --set properties.siteConfig.appCommandLine=""` — passing
   `--startup-file ""` does not clear it.
8. **Key Vault requirements.** None for this rehearsal; migration stays deferred.
   Record explicitly that PROD secrets remain plaintext app settings afterwards.
9. **Health verification.** `/api/config` returns 200 with `environment:
   production`. Then `/health`. Note that `/health` fans out to live external
   connectors, so a failure there may indicate a third-party outage rather than a
   deployment fault — read the body, do not just read the status.
10. **Database runtime-role verification.** Confirm the application connects as
    the intended least-privilege role, and that it is not the server admin.
    DEV runs as `docuaction_app`; PROD's role model has not been established and
    that gap must be closed before, not during, cutover.
11. **Auth / SSO / RBAC verification.** Confirm SSO round-trip and that
    `require_role` behaves. Note that `ADMIN_EMAILS` in `app/core/security.py`
    hard-codes three addresses to super-admin in every environment, PROD
    included, independent of the database `users.role` column.
12. **Reporting / PDF verification.** `GET /api/reports/health/engine` must report
    WeasyPrint available with a version. This is the single check that proves the
    container cutover achieved its purpose; on the built-in runtime it fails.
13. **Area-1 verification.** Confirm ownership and that the runtime role has
    SELECT and INSERT but not UPDATE, DELETE or TRUNCATE on the four `rce_*`
    tables, and no membership in the owner role. Append-only INSERT is by design.
14. **Rollback trigger.** Any of: `/api/config` not 200 within the container start
    limit; the reporting engine check failing after cutover; the database runtime
    role not resolving; any Area-1 privilege check failing; any unexpected change
    in a PROD row count.
15. **Rollback procedure.** Restore `linuxFxVersion` to `PYTHON|3.12`, restore the
    captured `appCommandLine`, remove `WEBSITES_PORT`, restart, and re-verify
    `/api/config`. Configuration rollback does not restore data; if any data
    changed, stop and use the step-1 PITR point instead of continuing.
