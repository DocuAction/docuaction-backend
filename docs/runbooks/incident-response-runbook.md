# DocuAction Backend — Incident Response Runbook

**Product:** DocuAction AI / DocuAction TEFCA ARC
**Version:** 6.0.0
**Owner:** Alliance Global Tech, Inc.
**Audience:** On-Call / Platform Operations / Security
**Classification:** Internal — Operations
**Contacts:** security@agtbi.com · imran@agtbi.com

---

## 1. Purpose & Scope

This runbook is the **operational companion** to the DocuAction security incident-response
plan. It provides step-by-step actions for detecting, triaging, containing, recovering from,
and closing out operational and security incidents affecting the DocuAction backend on Azure.

For any incident with confirmed or suspected exposure of data (including PHI/PII in the TEFCA
context) or credential compromise, **engage security@agtbi.com immediately** and follow the
formal security incident-response plan in parallel with this runbook.

---

## 2. Detect

Incidents are typically surfaced by one or more of:

- **Microsoft Defender for Cloud (Standard)** alert against the App Service, PostgreSQL server,
  or Key Vault.
- **Health check failure** — `/health` failing on the default host or `api-prod.docuaction.io`.
- **Error spike / latency** — elevated 5xx rate, worker timeouts, or slow responses.
- **Scheduler failure** — APScheduler daily job did not run / self-heal watchdog alert.
- **User or partner report** of an outage or anomaly.

Immediate first check:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://api-prod.docuaction.io/health
az webapp log tail --resource-group rg-docuaction-prod --name Docuaction
```

> A **400** on `/health` from the custom domain typically indicates an `ALLOWED_HOSTS`
> misconfiguration rather than an outage — correct the setting before escalating as an outage.

---

## 3. Triage & Severity

Assign severity to drive response speed and escalation:

| Severity | Definition | Examples |
|----------|-----------|----------|
| **SEV-1 Critical** | Full outage, data loss, or confirmed security breach | API down for all users; database corruption; confirmed data exfiltration or credential compromise |
| **SEV-2 High** | Major degradation or partial outage; security event under investigation | Elevated 5xx, auth failing for many users, suspicious Defender high-severity alert |
| **SEV-3 Medium** | Limited/degraded functionality, workaround exists | Single non-critical job failing; intermittent errors |
| **SEV-4 Low** | Minor issue, no user impact | Cosmetic errors, noisy non-actionable alert |

Record: what, when detected, blast radius, suspected cause, severity, and incident owner.

---

## 4. First Responders & Roles

- **Incident Owner (on-call):** coordinates the response, owns communications and the timeline.
- **Security Lead (security@agtbi.com):** engaged for any SEV-1/SEV-2 with a security dimension;
  owns evidence handling and breach determination.
- **Platform/DB support:** assists with App Service, PostgreSQL, and configuration actions.

For SEV-1, engage the escalation ladder (Section 7) without delay.

---

## 5. Containment Actions

Select actions proportional to the incident. For security incidents, **preserve evidence
before destructive changes** (Section 8).

- **Restart / recycle** the app to clear a wedged state:
  ```bash
  az webapp restart --resource-group rg-docuaction-prod --name Docuaction
  ```
- **Scale** to absorb load or isolate:
  ```bash
  az appservice plan update --resource-group rg-docuaction-prod \
    --name <plan-name> --sku <sku>          # scale up
  az webapp update --resource-group rg-docuaction-prod \
    --name Docuaction --set httpsOnly=true  # ensure HTTPS-only
  ```
- **Rotate compromised secrets** — rotate the affected secret (e.g., `SECRET_KEY`, database
  credential, an API key, an OAuth client secret) at its source and update the App Service
  setting / Key Vault reference (Section 9 quick reference).
- **Disable compromised accounts** — via the admin path; where `REQUIRE_ADMIN_APPROVAL` is in
  force, ensure no unapproved accounts are active. Revoke/rotate JWT signing (`SECRET_KEY`) to
  invalidate outstanding tokens if token theft is suspected.
- **Block hosts / origins** — tighten `ALLOWED_HOSTS` / `ALLOWED_ORIGINS`, or apply App Service
  access restrictions (IP allow/deny) to block malicious sources.
- **Pause scheduled jobs** — set `ENABLE_SCHEDULER=false` if a scheduled job is implicated.
- **Isolate the database** — if data integrity is at risk, restrict access and prepare a PITR
  (see `backup-restore.md`); do not overwrite the source server.

---

## 6. Escalation-Triggering Conditions

Escalate immediately when:

- Any confirmed or suspected **data exposure** (PHI/PII) or **credential compromise**.
- SEV-1 not contained within the first response window.
- Regulatory/contractual notification obligations may be triggered.
- Root cause spans multiple systems or is unknown after initial triage.

---

## 7. Escalation Ladder

1. **On-call engineer** — initial response and triage.
2. **On-call lead / Incident Owner** — coordinates SEV-1/SEV-2.
3. **Security — security@agtbi.com** — mandatory for any security-implicated incident.
4. **Product/Executive sponsor — imran@agtbi.com** — for SEV-1, customer-impacting, or
   notification-triggering events.

Escalate by severity target times (see `on-call-guide.md`). Do not sit on a SEV-1.

---

## 8. Evidence Capture

Capture and preserve before destructive remediation, to the extent it does not prolong an
active breach:

- **Application & platform logs:**
  ```bash
  az webapp log download --resource-group rg-docuaction-prod --name Docuaction --log-file incident-logs.zip
  ```
- **Request IDs / correlation IDs** tied to the anomalous activity.
- **Audit logs** — application audit trail and database audit tables.
- **Defender for Cloud alert** details and timeline.
- **Configuration snapshot** — current app settings (redact secret values):
  ```bash
  az webapp config appsettings list --resource-group rg-docuaction-prod --name Docuaction --output json
  ```
- Record timestamps in UTC and store evidence in the secured incident record.

> **Never** paste secrets, tokens, connection strings, or PHI/PII into tickets, chat, or the
> incident timeline. Reference them by name/ID only.

---

## 9. Quick Command Reference

```bash
# Health check (default host + custom domain)
curl -sS -o /dev/null -w "%{http_code}\n" https://docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net/health
curl -sS -o /dev/null -w "%{http_code}\n" https://api-prod.docuaction.io/health

# Tail live logs
az webapp log tail --resource-group rg-docuaction-prod --name Docuaction

# Restart the app
az webapp restart --resource-group rg-docuaction-prod --name Docuaction

# Rotate a secret (update the App Service setting; prefer Key Vault reference)
az webapp config appsettings set \
  --resource-group rg-docuaction-prod --name Docuaction \
  --settings SECRET_KEY="<new-value-from-key-vault>"

# Disable scheduled jobs during an incident
az webapp config appsettings set \
  --resource-group rg-docuaction-prod --name Docuaction \
  --settings ENABLE_SCHEDULER="false"

# Apply an IP access restriction (block a source)
az webapp config access-restriction add \
  --resource-group rg-docuaction-prod --name Docuaction \
  --rule-name block-src --action Deny --ip-address <cidr> --priority 100
```

---

## 10. Recovery & Validation

1. Confirm the root cause is addressed (config fix, secret rotation, code rollback, or restore).
2. Restore normal configuration (re-enable scheduler, remove temporary access restrictions).
3. Verify:
   - `/health` succeeds on both hosts.
   - Authentication (JWT + Entra SSO) works end-to-end.
   - Alembic revision is correct; database integrity confirmed (see `backup-restore.md`).
   - Error rate and latency back to baseline.
   - No new high-severity Defender alerts.
4. For rollbacks/restores, follow `../deployment/rollback-procedures.md` and `backup-restore.md`.
5. Declare the incident resolved only after sustained healthy signals.

---

## 11. Post-Incident

- Write a post-incident review (timeline, root cause, impact, RPO/RTO actuals, remediation).
- Track corrective actions to closure.
- Update runbooks, alerts, and the deployment guide with lessons learned.
- For security incidents, security@agtbi.com owns breach determination and any required
  notifications.

---

## 12. Change Record

| Field | Value |
|-------|-------|
| Document owner | Platform Operations / Security, Alliance Global Tech, Inc. |
| Applies to | DocuAction backend v6.0.0 |
| Companion to | DocuAction security incident-response plan |
| Related | `on-call-guide.md`, `backup-restore.md`, `../deployment/rollback-procedures.md` |
| Security contact | security@agtbi.com |
