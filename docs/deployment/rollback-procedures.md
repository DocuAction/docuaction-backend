# DocuAction Backend — Rollback Procedures

**Product:** DocuAction AI / DocuAction TEFCA ARC
**Version:** 6.0.0
**Owner:** Alliance Global Tech, Inc.
**Audience:** Platform Operations / Release Engineering / On-Call
**Classification:** Internal — Operations
**Contacts:** security@agtbi.com · imran@agtbi.com

---

## 1. Purpose

This procedure defines how to safely roll back a DocuAction backend release on **Azure App
Service** and **Azure Database for PostgreSQL Flexible Server**. It covers code rollback,
configuration/secret rollback, Alembic downgrade cautions, database point-in-time restore, and
the decision criteria and communications required for a controlled rollback.

Rollback is a **controlled change**. Do not perform ad-hoc fixes in production; prefer
restoring a known-good state and re-attempting in a planned window.

---

## 2. Decision Criteria (Go / No-Go)

Initiate rollback when one or more of the following holds after a deploy:

- `/health` fails on the default host or `api-prod.docuaction.io` and cannot be resolved by a
  configuration correction within the change window.
- Error rate or latency exceeds agreed thresholds attributable to the release.
- A data-integrity or security regression is confirmed (or a new high-severity Defender alert
  is traced to the change).
- Core authentication (JWT / Entra SSO) is broken for users.
- A migration produced unexpected schema or data behavior.

**Do NOT roll back** (correct forward instead) when:

- The issue is a missing/incorrect **application setting** (e.g., an `ALLOWED_HOSTS` entry
  causing 400) that can be fixed in place — correct the setting and re-verify.
- The failure is external (upstream provider outage) and unrelated to the artifact.

**Go/No-Go owner:** the release manager (or on-call lead outside business hours), in
consultation with security@agtbi.com for any security-implicated event.

---

## 3. Pre-Rollback Checklist

1. Declare an incident if user impact is occurring (see `../runbooks/incident-response-runbook.md`).
2. Identify the **last known-good** artifact (zip checksum) and the **Alembic head revision**
   that corresponded to it, from the release record.
3. Confirm whether the failed release included **database migrations**. This determines whether
   a code-only rollback is safe (see Section 5).
4. Capture current logs and state for post-incident analysis before changing anything:
   ```bash
   az webapp log tail --resource-group rg-docuaction-prod --name Docuaction
   ```
5. Notify stakeholders that a rollback is starting (Section 8).

---

## 4. Code / Artifact Rollback

Choose the mechanism available for the environment.

### 4.1 Deployment slots (preferred if configured)

If a staging slot with the previous build is available, swap back:

```bash
az webapp deployment slot swap \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --slot staging \
  --target-slot production \
  --action swap
```

A slot swap restores both the previous code and its slot-scoped settings near-instantly and is
the lowest-risk code rollback.

### 4.2 Previous zip redeploy

If slots are not in use, redeploy the last known-good artifact:

```bash
az webapp deploy \
  --resource-group rg-docuaction-prod \
  --name Docuaction \
  --src-path deploy-known-good.zip \
  --type zip
```

Confirm the redeployed artifact checksum matches the recorded known-good value.

---

## 5. Alembic Downgrade Cautions (Data-Loss Risk)

**Database downgrades are the highest-risk part of any rollback.** Many migrations are not
cleanly reversible and a `downgrade` can **drop columns/tables and destroy data**.

Guidance:

- If the failed release was **code-only** (no migrations), do **not** touch the schema. Roll
  back code only (Section 4).
- If a migration must be reversed, first take/confirm a restore point (Section 6), then:
  ```bash
  # From the App Service SSH session
  cd /home/site/wwwroot
  export PYTHONPATH=/home/site/wwwroot/pydeps
  python -m alembic current            # note current head
  python -m alembic downgrade -1       # or: downgrade <target_revision>
  python -m alembic current            # confirm target
  ```
- **Prefer forward-fix or database restore over destructive downgrades** when data has been
  written under the new schema. A downgrade that removes a populated column is irreversible
  without a restore.
- Never downgrade against the wrong environment — verify `DATABASE_URL` first.

> The DocuAction schema comprises **42 tables**. Treat any downgrade touching multiple tables
> as a high-risk operation requiring the security/data owner's sign-off.

---

## 6. Database Point-in-Time Restore (PITR) Option

When data corruption or an irreversible migration is involved, use PostgreSQL Flexible Server
PITR rather than a schema downgrade. Full procedure: `../runbooks/backup-restore.md`.

Summary:

```bash
# Restore docuaction-db to a timestamp just before the failed change,
# into a NEW server, then validate before any cutover.
az postgres flexible-server restore \
  --resource-group rg-docuaction-prod \
  --name docuaction-db-restore \
  --source-server docuaction-db \
  --restore-time "2026-07-18T00:45:00Z"
```

PITR restores to a **new server**; cutover (repointing `DATABASE_URL`) is a deliberate,
validated step — never overwrite the live server in place. Coordinate with the DB owner.

---

## 7. Configuration / Secret Rollback

If the failed release changed application settings or secrets:

1. Restore the previous known-good values of any changed settings:
   ```bash
   az webapp config appsettings set \
     --resource-group rg-docuaction-prod \
     --name Docuaction \
     --settings KEY="<previous-value>"
   ```
2. For **secret rotation** events, ensure the value restored (or the newly rotated value) is
   valid and, if from Key Vault, that the reference resolves.
3. Restoring settings triggers a restart — re-verify after it settles.
4. Confirm `ALLOWED_HOSTS`, `ALLOWED_ORIGINS`, `SECRET_KEY`, and `DATABASE_URL` are correct for
   the environment after any config rollback.

---

## 8. Verification After Rollback

1. `/health` returns success on **both** hosts:
   ```bash
   curl -sSf https://docuaction-emffhfgwc0gffgc9.eastus2-01.azurewebsites.net/health
   curl -sSf https://api-prod.docuaction.io/health
   ```
2. `python -m alembic current` reports the **expected** revision for the restored code.
3. Authentication (JWT + Entra SSO) works end-to-end.
4. Frontend (`app.docuaction.io`) operates without CORS errors.
5. Scheduler state matches `ENABLE_SCHEDULER`.
6. No new high-severity Defender for Cloud alerts.
7. Error rate and latency return to baseline.

---

## 9. Communication Steps

- **On start:** notify stakeholders and security@agtbi.com that a rollback is underway,
  including scope and expected impact.
- **On completion:** confirm restored version, verification results, and any data-restore
  actions taken.
- **If data restore occurred:** document the restore timestamp, RPO impact (any data written
  after the restore point that was lost), and affected records.
- Log the rollback in the change/incident record and schedule a post-incident review.

---

## 10. Post-Rollback

- Preserve the failed artifact and logs for root-cause analysis.
- Do not re-attempt the release until the root cause is understood and the fix validated in dev.
- Feed lessons learned back into the deployment guide and test plan.

---

## 11. Change Record

| Field | Value |
|-------|-------|
| Document owner | Platform Operations, Alliance Global Tech, Inc. |
| Applies to | DocuAction backend v6.0.0 |
| Related | `azure-deployment-guide.md`, `../runbooks/backup-restore.md`, `../runbooks/incident-response-runbook.md` |
| Security contact | security@agtbi.com |
