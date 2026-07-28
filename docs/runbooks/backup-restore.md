# DocuAction Backend — Database Backup & Restore Runbook

**Product:** DocuAction AI / DocuAction TEFCA ARC
**Version:** 6.0.0
**Owner:** Alliance Global Tech, Inc.
**Audience:** Platform Operations / Database Operations / On-Call
**Classification:** Internal — Operations
**Contacts:** security@agtbi.com · imran@agtbi.com

---

## 1. Purpose

This runbook covers backup and restore operations for the DocuAction production database,
**Azure Database for PostgreSQL Flexible Server "docuaction-db"** (SSL required). It documents
automated backups and retention, point-in-time restore (PITR), manual logical backups
(`pg_dump` / `pg_restore`), restore verification, and RPO/RTO policy targets.

> The DocuAction schema comprises **42 tables**, replicated from the prior Railway PostgreSQL
> during the Azure migration. Any restore must preserve the complete 42-table schema and its
> referential integrity.

---

## 2. Backup Strategy Overview

| Layer | Mechanism | Primary use |
|-------|-----------|-------------|
| Platform automated backups | Flexible Server built-in backups (PITR) | Server-level recovery, accidental change/corruption |
| Logical backups | `pg_dump` / `pg_restore` | Portable snapshots, selective/table-level restore, off-platform copies |

Both layers are maintained. Platform backups provide fast, timestamp-precise recovery; logical
backups provide portability and granular restore.

---

## 3. Automated Backups & Retention

Azure PostgreSQL Flexible Server takes automated backups enabling PITR within the configured
retention window.

Confirm the current retention and backup configuration:

```bash
az postgres flexible-server show \
  --resource-group rg-docuaction-prod \
  --name docuaction-db \
  --query "{retentionDays:backup.backupRetentionDays, geoRedundant:backup.geoRedundantBackup, earliestRestore:backup.earliestRestoreDate}" \
  --output table
```

To adjust retention (change-controlled):

```bash
az postgres flexible-server update \
  --resource-group rg-docuaction-prod \
  --name docuaction-db \
  --backup-retention <days>
```

Operational policy:

- Retain automated backups per the data-retention policy (aligned with `DATA_RETENTION_DAYS`
  and contractual requirements).
- Enable geo-redundant backup for production where the plan permits, to support regional
  recovery.
- Review the **earliest restore time** regularly so the team knows the PITR floor.

---

## 4. Point-in-Time Restore (PITR)

PITR restores the server to a chosen timestamp **into a new server**. The source server is
never modified. Use PITR for corruption, bad migrations, or accidental data loss.

### 4.1 Procedure

```bash
# 1. Determine a safe target time (just BEFORE the incident/change).
#    Confirm it is within the retention window (earliestRestoreDate above).

# 2. Restore into a NEW server.
az postgres flexible-server restore \
  --resource-group rg-docuaction-prod \
  --name docuaction-db-restore \
  --source-server docuaction-db \
  --restore-time "2026-07-18T00:45:00Z"

# 3. Confirm the restored server is available.
az postgres flexible-server show \
  --resource-group rg-docuaction-prod \
  --name docuaction-db-restore \
  --query "state" --output tsv
```

### 4.2 Validate before cutover

- Connect to the restored server (SSL required) and run the verification checks in Section 6.
- Only after validation, perform cutover by repointing the application `DATABASE_URL` to the
  restored server (a deliberate, change-controlled step). See `../deployment/rollback-procedures.md`.
- **Never** delete the original server until the restore is confirmed good and retained per policy.

---

## 5. Manual Logical Backups (pg_dump / pg_restore)

Logical backups are portable and support selective restore. Perform them from a trusted
operator workstation or jump host with network access to the server. Connections require SSL.

### 5.1 Create a logical backup

```bash
# Custom-format dump (recommended: compressed, supports selective restore)
PGSSLMODE=require pg_dump \
  --host=docuaction-db.postgres.database.azure.com \
  --port=5432 \
  --username=<admin-user> \
  --dbname=<database> \
  --format=custom \
  --file=docuaction_$(date +%Y%m%dT%H%M%SZ).dump
```

Store the dump in an approved, access-controlled location (e.g., encrypted storage). Never
place backups in source control or unsecured shares. Record a checksum.

### 5.2 Restore a logical backup

```bash
# Restore into a target database (typically a fresh/empty database or restored server)
PGSSLMODE=require pg_restore \
  --host=<target-host> \
  --port=5432 \
  --username=<admin-user> \
  --dbname=<target-database> \
  --clean --if-exists \
  --no-owner \
  docuaction_<timestamp>.dump
```

> **Caution:** `--clean --if-exists` drops existing objects before recreating them. Only run
> against the intended target. For selective recovery, use `pg_restore --list` and
> `--use-list` to restore specific tables.

---

## 6. Restore Verification

After any restore (PITR or logical), verify before declaring success:

1. **Server/connectivity** — connect over SSL (`sslmode=require`).
2. **Schema completeness** — confirm all **42 tables** are present:
   ```sql
   SELECT count(*) FROM information_schema.tables
   WHERE table_schema = 'public';   -- expect 42
   ```
3. **Row-count spot checks** — compare key tables against expected/pre-incident counts.
4. **Referential integrity** — confirm foreign keys and critical constraints are intact.
5. **Application boot** — point a non-production app instance at the restored database and
   confirm `/health` succeeds and Alembic reports the expected revision:
   ```bash
   python -m alembic current
   ```
6. **Audit trail** — confirm audit/log tables restored consistently.
7. Record results, restore timestamp, and any RPO gap in the incident/change record.

---

## 7. RPO / RTO Policy Targets

These are **policy targets** used for planning and prioritization. They are objectives, **not
guarantees**; actual results depend on data volume, backup state, and incident specifics.

| Objective | Target | Notes |
|-----------|--------|-------|
| **RPO** (max acceptable data loss) | ≤ 15 minutes | Bounded by automated backup/PITR granularity; data written after the restore point is lost on a PITR. |
| **RTO** (max acceptable time to restore service) | ≤ 4 hours | From decision-to-restore to validated cutover, for a standard single-server restore. |

Review targets at least annually and after any material architecture change.

---

## 8. Backup Hygiene & Governance

- Test a restore (PITR and logical) on a scheduled basis (at least quarterly) to prove the
  procedure and validate RTO.
- Encrypt logical backups at rest and restrict access to authorized operators.
- Never store credentials or backups in tickets, chat, or source control.
- Log all restore operations, including who performed them and why.

---

## 9. Change Record

| Field | Value |
|-------|-------|
| Document owner | Database/Platform Operations, Alliance Global Tech, Inc. |
| Applies to | DocuAction backend v6.0.0, docuaction-db |
| Related | `../deployment/rollback-procedures.md`, `incident-response-runbook.md` |
| Review cadence | Quarterly restore test; annual target review |
| Security contact | security@agtbi.com |
