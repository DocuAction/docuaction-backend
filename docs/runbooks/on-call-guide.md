# DocuAction Backend — On-Call Guide

**Product:** DocuAction AI / DocuAction TEFCA ARC
**Version:** 6.0.0
**Owner:** Alliance Global Tech, Inc.
**Audience:** On-Call Engineers / Platform Operations
**Classification:** Internal — Operations
**Contacts:** security@agtbi.com · imran@agtbi.com

---

## 1. Purpose

This guide defines on-call responsibilities, alert sources, first-response actions, response-time
targets, and escalation for the DocuAction backend running on Azure App Service with Azure
PostgreSQL Flexible Server. It complements `incident-response-runbook.md`.

---

## 2. On-Call Responsibilities

The on-call engineer:

- Monitors alert sources (Section 4) and acknowledges alerts within the target window.
- Performs first-response triage and containment per the incident-response runbook.
- Owns the incident timeline and stakeholder communication until handed off or resolved.
- Escalates security-implicated events to security@agtbi.com without delay.
- Records actions taken; hands off cleanly at rotation boundaries with an accurate status.
- Never applies unreviewed changes to production outside an active, documented incident.

---

## 3. Rotation

- On-call follows the published rotation schedule (primary + secondary/backup).
- **Handoff:** at each rotation change, the outgoing engineer briefs the incoming engineer on
  open incidents, recent deploys, known-fragile areas, and any suppressed/again-expected alerts.
- The **secondary** is engaged when the primary does not acknowledge within the target time or
  when a SEV-1 needs additional hands.

---

## 4. Alert Sources

| Source | What it tells you |
|--------|-------------------|
| **Microsoft Defender for Cloud (Standard)** | Security alerts on App Service, PostgreSQL, Key Vault (suspicious auth, exfiltration patterns, misconfig) |
| **Health monitoring** | `/health` failures on the default host or `api-prod.docuaction.io` |
| **Scheduler self-heal watchdog** | APScheduler daily job did not run / recovered; gated by `ENABLE_SCHEDULER` |
| **Error/latency monitoring** | 5xx spikes, worker timeouts, elevated latency |
| **Platform/App Service** | Restarts, unhealthy instances, deployment failures |

---

## 5. Common Alerts & First Actions

| Alert | First action |
|-------|-------------|
| `/health` returns **400** | Check `ALLOWED_HOSTS` includes both the Azure default host and `api-prod.docuaction.io`; correct the app setting and re-verify. Likely config, not outage. |
| `/health` **fails / 5xx** | Tail logs; check recent deploy; restart app; confirm `DATABASE_URL`/`SECRET_KEY` present; verify PostgreSQL reachable (SSL). |
| **Error/latency spike** | Tail logs for the failing route; check DB health and connection saturation; scale if load-driven; consider rollback if tied to a recent deploy. |
| **Scheduler did not run** | Confirm `ENABLE_SCHEDULER=true`; check watchdog/self-heal logs; restart if the scheduler thread is wedged. |
| **Defender high-severity alert** | Treat as a security incident; engage security@agtbi.com; capture evidence before remediation (see incident runbook). |
| **Auth failures (JWT/Entra SSO)** | Check Entra app settings (`AZURE_AD_*`), `SECRET_KEY` integrity, and clock/redirect config; confirm no recent secret rotation broke tokens. |
| **DB connection errors** | Verify server state, SSL requirement, credential validity, and connection limits; check for a maintenance/failover event. |

Always start with:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://api-prod.docuaction.io/health
az webapp log tail --resource-group rg-docuaction-prod --name Docuaction
```

---

## 6. Severity-Based Response Times

Targets from alert acknowledgement (align with contractual SLAs where stricter):

| Severity | Acknowledge | Begin active response | Escalate if unresolved |
|----------|-------------|-----------------------|------------------------|
| **SEV-1 Critical** | ≤ 15 min | Immediately | ≤ 30 min |
| **SEV-2 High** | ≤ 30 min | ≤ 1 hour | ≤ 2 hours |
| **SEV-3 Medium** | ≤ 4 hours | Next business day | Next business day |
| **SEV-4 Low** | Next business day | As scheduled | N/A |

Any security-implicated event escalates to security@agtbi.com immediately, regardless of severity.

---

## 7. Escalation Contacts

1. **Secondary on-call** — when primary can't ack or needs help.
2. **Security — security@agtbi.com** — mandatory for suspected/confirmed security events
   (data exposure, credential compromise).
3. **Product/Executive sponsor — imran@agtbi.com** — SEV-1, customer-impacting, or
   notification-triggering events.

Full ladder and criteria: `incident-response-runbook.md`.

---

## 8. Useful az CLI / Diagnostics

```bash
# Auth / context
az login
az account set --subscription "AGT-DocuAction"

# Health (both hosts)
curl -sS -o /dev/null -w "%{http_code}\n" https://docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net/health
curl -sS -o /dev/null -w "%{http_code}\n" https://api-prod.docuaction.io/health

# Logs
az webapp log tail     --resource-group rg-docuaction-prod --name Docuaction
az webapp log download --resource-group rg-docuaction-prod --name Docuaction --log-file oncall-logs.zip

# App lifecycle
az webapp restart --resource-group rg-docuaction-prod --name Docuaction
az webapp show    --resource-group rg-docuaction-prod --name Docuaction --query "state" -o tsv

# Settings (values are redacted server-side for secrets; still avoid pasting output with secrets)
az webapp config appsettings list --resource-group rg-docuaction-prod --name Docuaction --output table

# Database state
az postgres flexible-server show --resource-group rg-docuaction-prod --name docuaction-db --query "state" -o tsv

# Toggle scheduler during an incident
az webapp config appsettings set --resource-group rg-docuaction-prod --name Docuaction --settings ENABLE_SCHEDULER="false"
```

---

## 9. "Do Not" List

- **Never** paste secrets, tokens, connection strings, API keys, or PHI/PII into tickets, chat,
  logs shared externally, or the incident timeline. Reference by name/ID only.
- **Never** copy secrets between dev and prod — environments are isolated with no shared secrets.
- **Never** run an Alembic `downgrade` or a destructive DB command without a confirmed backup /
  restore point and sign-off (see `backup-restore.md` and `../deployment/rollback-procedures.md`).
- **Never** disable TrustedHost/CORS protections or add broad `ALLOWED_HOSTS`/`ALLOWED_ORIGINS`
  wildcards to "fix" a 400 — correct the specific host/origin instead.
- **Never** deploy or reconfigure production outside an approved change or active incident.
- **Never** operate against the wrong environment — verify `DATABASE_URL` and app name first.
- **Never** delete a source database/server during a restore before validation and retention.

---

## 10. Handoff Checklist

- Open incidents and their status/owner.
- Recent deploys and config/secret changes.
- Active suppressions or expected-noisy alerts.
- Any temporary changes (access restrictions, scheduler disabled) that must be reverted.

---

## 11. Change Record

| Field | Value |
|-------|-------|
| Document owner | Platform Operations, Alliance Global Tech, Inc. |
| Applies to | DocuAction backend v6.0.0 |
| Related | `incident-response-runbook.md`, `backup-restore.md`, `../deployment/rollback-procedures.md` |
| Review cadence | Quarterly or after major incident |
| Security contact | security@agtbi.com |
