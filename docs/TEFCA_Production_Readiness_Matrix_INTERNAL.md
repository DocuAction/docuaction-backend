# TEFCA ARC — Production Readiness Matrix

**Internal engineering record. Not a Government deliverable. Contains no secrets,
no credentials and no Government row-level values.**
Contract 7571MN26F80064 · Step #18 / #18A / #18B · 31 August 2026

Status vocabulary: **PROVEN READY** (evidence, not existence) · **PARTIAL** ·
**NOT READY** · **N/A** · **GOV DECISION** · **UNVERIFIED**.

A resource existing is never evidence that it is used. Every "PROVEN READY"
below names what was actually observed.

---

| # | Area | Current state | Evidence | Gap | Risk | Remediation | Azure change? | PROD change? | Gov decision? | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Secrets | **DEV: all 7 via Key Vault reference, all `Resolved`.** PROD: 8 still literal | #18B migration; `configreferences/appsettings` | PROD not migrated (out of scope by instruction) | **HIGH** for PROD | repeat the DEV procedure on PROD | **B** | **C** | No | **DEV PROVEN READY / PROD NOT READY** |
| 2 | Managed Identity | System-assigned on both apps | `az webapp identity show` — DEV `f5d1…`, PROD `5a90…` | none | — | — | — | — | No | **PROVEN READY** |
| 3 | Key Vault | Vaults unchanged; **DEV app now consumes the vault via managed identity** | 7 references `Resolved`, identity `SystemAssigned` | PROD unused | MEDIUM | PROD migration | **B** | **C** | No | **DEV PROVEN READY** |
| 4 | Database roles | **Azure DEV verified via Entra token.** `docuaction_owner` owns Area 1; runtime holds INSERT/SELECT only; CREATE revoked | 13/13 permission probes | PROD unverified | **HIGH** for PROD | repeat on PROD | A (read) | **C** | No | **DEV PROVEN READY / PROD UNVERIFIED** |
| 5 | Database ownership | Area 1 owned by `docuaction_owner`; 84 other tables owned by the runtime role (migrations run as it) | #18B ownership query | migrations must move to `docuaction_owner` | MEDIUM | run Alembic as the owner | — | **C** | No | **PARTIAL** |
| 6 | Database network | Both servers `publicNetworkAccess: Enabled`, no private endpoint, no delegated subnet; reachability by IP allow-list | server properties + firewall rules | DB reachable from the public internet subject to credentials | **MEDIUM** (PROD) / **HIGH** (DEV, see #7) | private endpoint or VNet delegation | **B** | **C** | No | **PARTIAL** |
| 7 | DEV DB firewall | **Broad rule removed.** 34 possible outbound IPs individually covered first | #18B: 35 rules, 0 broad; DEV verified healthy after | 35 hand-maintained rules is fragile | LOW | private endpoint | **B** | No | No | **PROVEN READY** |
| 8 | App Service network | VNet-integrated (both), HTTPS-only, TLS 1.2, FTPS disabled, 1 IP restriction | `az webapp show` / `config show` | HTTP/2 off (performance, not security) | LOW | — | — | — | No | **PROVEN READY** |
| 9 | Artifact storage | **DEV Blob account provisioned and the backend implemented.** Shared-key access disabled; private container; managed identity | 21/21 tests against real Azure | PROD has none; DEV image not yet deployed so the setting is still `local` | **HIGH** for PROD | provision PROD storage; deploy the image | **B** | **C** | No | **DEV IMPLEMENTED, NOT YET LIVE** |
| 10 | TLS | HTTPS-only, min TLS 1.2, managed certificate present | `az webapp show`, `Microsoft.Web/certificates` | — | — | — | — | — | No | **PROVEN READY** |
| 11 | Security headers | HSTS, nosniff, `X-Frame-Options: DENY`, CSP `default-src 'self'`, Referrer-Policy, Permissions-Policy — on **every** response including 404 and error paths | live probe through the ASGI app | no global `Cache-Control` | LOW | per-response, already done for controlled downloads | — | — | No | **PROVEN READY** |
| 12 | Authentication | JWT **HS256**, DB-authoritative role check, lockout, reset flow, verification, session invalidation | code + existing suites | **no MFA** | MEDIUM | governance + architecture decision | — | — | **Yes** | **PARTIAL** |
| 13 | Authorization | 8-level hierarchy, DB role never the token role | `test_rbac*`, Step #17C suites | — | — | — | — | — | No | **PROVEN READY** |
| 14 | RBAC (exports) | produce `qalead`; read job `qalead` + ownership; download `viewer` | Step #17C tests, re-run here | — | — | — | — | — | No | **PROVEN READY** |
| 15 | IDOR | export jobs answer **404**, not 403, to a non-owner | `test_another_users_job_is_not_readable` | not swept outside reports/exports | MEDIUM | later security gate | — | — | No | **PARTIAL** |
| 16 | Audit | `audit_logs` with indexed `event_type`/`outcome`/`correlation_id`; export events recorded | Step #17C | no tamper-evidence (hash chain / WORM) | MEDIUM | later | — | — | No | **PARTIAL** |
| 17 | Logging | Structured; global handlers suppress tracebacks, DB errors and paths | `error_handler`, live probe | — | — | — | — | — | No | **PROVEN READY** |
| 18 | Monitoring | **DEV workspace + App Insights created and wired** | `docuaction-logs-dev`, `docuaction-appinsights-dev` | no worker/job telemetry | MEDIUM | log-based alerts | **B** | No | No | **PARTIAL** |
| 19 | Alerting | PROD 4; **DEV 3 added** (availability, 5xx, DB liveness) | `az monitor metrics alert list` | worker/Key Vault/backup alerts need a paging decision | MEDIUM | decide recipients | **B** | No | No | **PARTIAL** |
| 20 | Backup | PROD 14 days, DEV 7, geo server separately geo-redundant | server properties | primary `geoRedundantBackup: Disabled` | MEDIUM | enable, or rely on the geo replica knowingly | **B** | **C** | No | **PARTIAL** |
| 21 | Restore | **PITR PERFORMED on DEV**: restored, validated read-only, deleted. ~7 minutes | #18B restore evidence; digest matched source exactly | PROD unrehearsed | MEDIUM | repeat on PROD | **B** | No | No | **DEV PROVEN READY** |
| 22 | PITR | **Exercised on DEV**, restore point 2026-08-31T17:25:59Z | as above | PROD unrehearsed | MEDIUM | repeat on PROD | **B** | No | No | **DEV PROVEN READY** |
| 23 | DR / continuity | Geo server exists; no documented RTO/RPO, no failover procedure | inventory; no DR doc found | RTO/RPO undefined | **HIGH** | program decision, then a procedure | — | — | **Yes** | **GOV DECISION** |
| 24 | Deployment safety | Digest-pinned PROD image; tag-pinned DEV. **A tag push builds and stops — it cannot reach production.** `deploy-prod` requires an explicit `workflow_dispatch` choosing `production` AND carries `environment: production` | `linuxFxVersion`; `deploy-backend.yml` `on:`/`if:`/`environment:` | whether the GitHub `production` environment actually requires reviewers is a repository setting, **not verified** (and not changed) | LOW | confirm the environment's reviewer requirement before a rehearsal | — | — | No | **PARTIAL — declared gate proven, enforcement unverified** |
| 25 | Migration safety | Single Alembic head `20260831_export_jobs`; **startup DDL fail-closed** and now also on a deployed host with no `ENVIRONMENT` | `alembic heads`; `schema_guard`; 24 tests | — | — | — | — | — | No | **PROVEN READY** |
| 26 | Background workers | **1 App Service instance and gunicorn with no `--workers` flag ⇒ exactly one process** | `numberOfWorkers: 1`; Dockerfile `CMD` has no `--workers`; `appCommandLine` empty on both | scale-out unreviewed | MEDIUM | DB guards already hold; review topology before scaling | — | — | No | **PROVEN READY (at 1 worker)** |
| 27 | Export jobs | Queue, claim, heartbeat, reaper, partial unique index; **one export at a time, tested** | Step #17C + #18A capacity measurement (659 MB = 32% of B1ms) | app baseline unmeasured from this host | MEDIUM | none — no resize recommended on this evidence | — | — | No | **PROVEN READY (single export)** |
| 28 | Health endpoints | `/health` configured as the App Service probe on both | `healthCheckPath` | disclosure not fully assessed | LOW | — | — | — | No | **PARTIAL** |
| 29 | Dependency health | PostgreSQL 16 both; App Insights on PROD | inventory | no dependency telemetry | LOW | — | — | — | No | **PARTIAL** |
| 30 | Rate limiting | Implemented (global, registration, verification, lockout) | existing suites | not re-measured this gate | LOW | — | — | — | No | **PARTIAL** |
| 31 | Incident readiness | Audit trail, session invalidation, account disable, timestamps, correlation ids | code | no rehearsal; notification chain undefined | MEDIUM | tabletop | — | — | **Yes** | **PARTIAL** |
| 32 | Secret rotation | **DEV rotates in the vault** without touching app settings | 7 references resolve by name | PROD still literal | **HIGH** for PROD | follows PROD migration | **B** | **C** | No | **DEV PROVEN READY** |
| 33 | Accessibility engineering | Semantic tables, focus management, no colour-only meaning, 43 guardrails; **shared shell now reflows at 320px** | rendered measurement, this gate | no automated a11y runner | MEDIUM | later | — | — | No | **PARTIAL** |
| 34 | Section 508 | Test-readiness package prepared | `TEFCA_Section_508_Test_Readiness_INTERNAL.md` | manual/Trusted Tester review outstanding | — | Government activity | — | — | **Yes** | **GOV DECISION** |
| 35 | Configuration management | All security-significant flags explicitly set in both environments | `ENVIRONMENT`, `STARTUP_SCHEMA_MUTATION_ENABLED`, `PPEF_BULK_INGEST_ENABLED` read directly | — | — | — | — | — | No | **PROVEN READY** |
| 36 | Production data-state controls | Environment / identity / authorization separated; 10 scenarios certified | `test_classification_matrix.py` | — | — | — | — | — | No | **PROVEN READY** |
| 37 | Government authorization | Marker absent; no code may set it | read-only check; repository assertion test | authority undefined | — | governance | No | **No** | **Yes** | **GOV DECISION** |

---

## The three questions, kept apart

| Question | Answer |
|---|---|
| Technically ready for a **controlled production rehearsal**? | **NO** — see the five blockers below |
| Ready for **Government data ingestion into PROD**? | **NO** |
| Ready for **official Government ARC operations**? | **NO** |

### Blockers before a controlled rehearsal — Step #18B outcome

All five were **closed on DEV**. Each now has a rehearsed procedure rather than
a plan, recorded in `TEFCA_PROD_Execution_Checklist_INTERNAL.md`.

1. ~~PROD secrets plaintext~~ → **DEV migrated, 7/7 references Resolved.** PROD
   remains literal; migrating it is out of scope by instruction, and the
   procedure is now proven rather than theoretical.
2. ~~Artifact storage ephemeral~~ → **Blob account provisioned, backend
   implemented, 21/21 tests against real Azure.** Not yet live on DEV: the image
   carrying the implementation has not been deployed, so the backend setting was
   deliberately reverted to `local` rather than left ahead of the code.
3. ~~Restore never exercised~~ → **PITR performed**, validated, cleaned up.
4. ~~PROD database roles unverified~~ → **Azure DEV verified** and a real defect
   fixed (the runtime role could CREATE tables). PROD still unverified, and the
   probe procedure is now written down.
5. ~~DEV `AllowAllAzureServices`~~ → **removed**, after covering all 34 possible
   outbound addresses and confirming connectivity.

**PROD remains untouched. Every PROD-side item is open.**
