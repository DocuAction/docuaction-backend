# Operational Gaps

> Monitoring, logging, IR, backup/DR, certs, and secret rotation. From ops docs + IaC. Read-only.

## Monitoring & alerting completeness
- **App Insights not code-instrumented** — connection string injected but no SDK in `app/`; only App Service auto-instrumentation. No custom traces/exceptions/dependency spans → limited root-cause visibility.
- **Alert coverage partial** — 4 alerts (availability, 5xx, CPU, DB-alive). **Missing:** memory, response latency/P95, DB storage & connection saturation, Key Vault access anomalies, certificate expiry.
- **Single-recipient, single-channel alerting** — action group emails **one address** (`imran@agtbi.com`); no paging/SMS/webhook, no rotation. **Key-person risk.**
- **No diagnostic settings** modeled → resource logs may not be centrally routed to Log Analytics.

## Log aggregation
- App logs use `logging.basicConfig(level=INFO)` → **stdout only**, captured by App Service log stream; `WEBSITE_HTTPLOGGING_RETENTION_DAYS=3` (**very short**). No structured/JSON logging, no central log shipping beyond the platform default, no long-term audit-log retention strategy for compliance.

## Incident response — strong documentation (a genuine strength)
Present: `docs/security/incident-response-plan.md`, `docs/runbooks/incident-response-runbook.md`, `docs/runbooks/on-call-guide.md` (alert sources, SEV-1..4 ack targets 15min→next-day, escalation ladder, `az` diagnostics, "Do Not" list, handoff checklist), `docs/security/vulnerability-disclosure-policy.md`. **Gap:** escalation is a small set of **email contacts** (`security@agtbi.com`, `imran@agtbi.com`) — no paging tooling; the referenced on-call rotation schedule is **not in-repo**.

## Backup verification & DR — documented targets, thin topology
- `docs/runbooks/backup-restore.md`: PITR + `pg_dump`/`pg_restore`, restore verification (expect 42 tables), **quarterly restore-test** policy, **RPO ≤ 15 min / RTO ≤ 4 hours**.
- **Reality vs targets:** geo-redundant backup **Disabled** in IaC, Postgres **HA Disabled**, single region (eastus2), single instance, no read replica. **Regional DR is aspirational** — under a real regional/instance failure the stated RTO/RPO are unlikely to be met. Backup verification is a **manual/scheduled human** process, not automated.

## Certificate management
- App Service managed cert (SNI) for `api-prod.docuaction.io` provisioned/bound **manually** (`az webapp config ssl create/bind`). SWA cert for `app.docuaction.io` platform-managed, out of band. **Not modeled in IaC; no cert-expiry monitoring/alert** — managed certs auto-renew, but there is **no expiry alarm as a backstop**.

## Secret rotation
- `docs/security/secrets-management.md` documents rotation as **Planned/Target state, not implemented**. **No rotation schedule, no automation, no rotation policy on KV secrets.** `DATABASE_URL` remains a direct app setting (not vaulted) → DB-credential rotation needs a config change + restart. (The doc also *lags reality* — lists KV+MI as "Planned" though IaC implements it.)

## Key-person risk (cross-cutting)
Single CODEOWNER, single alert recipient, single escalation sponsor (`imran-agt` / `imran@agtbi.com`) throughout CI/CD, monitoring, and IR. A **bus-factor of one** across governance, alerting, and approvals.

## Priority operational fixes
| # | Fix | Closes | Effort |
|---|---|---|---|
| 1 | Enable **Postgres HA (zone-redundant)** + geo-redundant backup (at cutover) | DR/RTO/RPO gap | config |
| 2 | Add App Service **autoscale** (min 2 for HA) or at least a second instance | single point of failure | config |
| 3 | **Code-instrument App Insights** (`configure_azure_monitor`) + add latency/memory/DB-saturation/cert-expiry alerts | observability gaps | 2–3d |
| 4 | Add a **second alert channel + recipient** (paging + backup contact) | key-person risk | 0.5d |
| 5 | Put **Postgres & Key Vault behind private endpoints**; add App Service IP restrictions | public exposure | 1–2d (deploy the authored `networking.bicep`) |
| 6 | Increase HTTP log retention + centralize logs; **implement secret rotation** | log/rotation gaps | 1–2d |
