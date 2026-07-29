# Azure Architecture Review (Section 2K)

Read-only `az` inspection. Subscription **AGT-DocuAction**.

## Compute
| Item | Value | Assessment |
|---|---|---|
| App Service | `Docuaction` (Linux, PYTHON\|3.12) | ✅ |
| Plan / tier | **P0v3 (Premium0V3), capacity 1** | ⚠ **single instance — no HA/scale-out** |
| Always On | true | ✅ |
| Auto-scale | not configured | ⚠ |
| Deployment slots | none observed | ⚠ (no blue/green) |
| Runtime | gunicorn + uvicorn worker; `pydeps` + PYTHONPATH; `SCM_DO_BUILD_DURING_DEPLOYMENT=false` | works; diverges from `Dockerfile` |

## Database
| Item | Value | Assessment |
|---|---|---|
| Server (active) | `docuaction-db-geo` — **PostgreSQL 16**, Burstable, 32 GB | functional |
| Second server | `docuaction-db` | ⚠ **likely legacy/orphaned — confirm & decommission** |
| Geo-redundant backup | **Enabled**, 14-day retention | ✅ |
| High availability | **Disabled** | ⚠ no zone/standby failover |
| Connection pooling | app-side (SQLAlchemy pool_size=5, max_overflow=10, pre_ping) × 2 engines | adequate for 1 instance; **no PgBouncer** for scale-out |
| Access (public/private) | not fully confirmed this pass | ⚠ verify firewall/private-access in Part 9 |

## Security
| Item | Value | Assessment |
|---|---|---|
| HTTPS only | **true** | ✅ |
| Min TLS | **1.2** | ✅ |
| FTPS | **FtpsOnly** | ✅ |
| Remote debugging | off | ✅ |
| SCM basic auth | **disabled** (AAD only) | ✅ |
| Managed Identity | yes (→ Key Vault) | ✅ |
| Key Vault | `docuaction-kv-prod` with **private endpoint** + private DNS + VNet | ✅ strong |
| **Defender for Cloud (Standard)** | **AppServices, SqlServers, StorageAccounts, KeyVaults, OpenSourceRelationalDatabases, Containers** (+ Discovery, FoundationalCspm) | ✅ **broad coverage** |
| Diagnostic settings / Log Analytics | `docuaction-logs` workspace present | ✅ (per-resource diag routing to verify in Part 9) |
| `DATABASE_URL` | direct credential string app-setting | ⚠ move to KV/passwordless |

## Monitoring
| Item | Value |
|---|---|
| App Insights | `docuaction-appinsights` ✅ |
| Log Analytics | `docuaction-logs` ✅ |
| Metric alerts | availability, 5xx-errors, high-cpu, db-availability ✅ (4) |
| Action group | `docuaction-alerts` + Smart Detection ✅ |
| Health check path | **`/health`** configured on App Service ✅ |

## Networking
- `docuaction-vnet`, **private endpoint** for Key Vault, `privatelink.vaultcore.azure.net` private DNS zone ✅.
- App Service VNet integration / Postgres private access **not confirmed** this pass (Part 9).
- No WAF/Front Door observed in front of the App Service (custom domain terminates directly) — **consider Azure Front Door/WAF** for the internet edge.

## Disaster recovery
| Target | Current |
|---|---|
| Backup | PG geo-redundant, 14-day retention ✅ |
| **RTO** | Undefined — restore-from-backup (hours), single region compute | 
| **RPO** | Undefined — bounded by PG backup cadence (geo) |
| Failover plan | **None** (HA off, no secondary region compute) ⚠ |
| Recovery process | Manual restore + manual redeploy (no runbook) ⚠ |

## Azure Government readiness
| Aspect | Assessment |
|---|---|
| Core services (App Service, Flexible PG, Key Vault, App Insights, SWA) | **Available in Azure Government** — portable |
| **Static Web Apps** | ⚠ historically limited/newer in Gov clouds — **verify availability/parity** |
| **Defender plans / features** | mostly available; verify per-plan parity |
| **AI (Anthropic/OpenAI public endpoints)** | ⚠ **not FedRAMP-High / not in Gov boundary** — AI calls would need an Azure-Gov-approved model (e.g., Azure OpenAI in Gov) or removal from the boundary. **Largest Gov-migration blocker.** |
| External `.gov` APIs (NPPES/LEIE/PECOS/SAM) | reachable from Gov (public gov endpoints) |
| **Estimated migration effort** | **Medium-High** — re-point resources + IaC, replace/relocate SWA if unsupported, and **re-architect the AI dependency** for the Gov boundary; plus IL/FedRAMP paperwork. |

## Top Azure hardening actions (documented only)
1. Move `DATABASE_URL` to KV ref / passwordless MI→Postgres.
2. Add **HA** (PG zone-redundant or standby) + **scale-out**/autoscale or at least a deployment slot before production scale.
3. Decommission the orphaned `docuaction-db`.
4. Consider **Front Door + WAF** at the internet edge.
5. Define **RTO/RPO** and a DR runbook.
