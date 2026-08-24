# `docuaction_owner` — production DBA runbook

**Classification:** INTERNAL ENGINEERING — PRODUCTION SENSITIVE · 2026-08-23

> **NO COMMAND IN THIS DOCUMENT HAS BEEN EXECUTED.** It is prepared for a DBA to
> run in a maintenance window, against production, with the Contracting
> Officer's Representative informed. Do not run it from an application account.

## Why

Area-1 tables are currently owned by the application role. Ownership carries
implicit rights that no revocation removes — an owner can always `ALTER` or
`DROP` its own table. Moving ownership to a role that cannot log in makes the
immutability guarantee structural rather than procedural.

The current control is already meaningful: an `UPDATE` against
`rce_source_records` from the application role is refused with
`permission denied`. This runbook removes the residual ownership rights.

## Preconditions

1. Verified backup **that has been restored somewhere and checked**. An untested
   backup is not a precondition, it is a hope.
2. Maintenance window; application quiesced.
3. `alembic current` equals head. **Re-derive head with `alembic heads`; do not
   trust a revision id transcribed into a document.** This precondition
   originally named `20260828_area1_grants`, which was head when this runbook
   was written on 2026-08-23 and stopped being head when Phase 7.5A added
   `20260829_report_artifacts`. A DBA checking against the transcribed value
   would have accepted a database one migration behind head. As of 2026-08-24
   head is `20260829_report_artifacts` and there is exactly one head — but
   verify that, do not assume it.
4. Area-1 content digest recorded before starting.

## Step 1 — create the role (idempotent)

```sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docuaction_owner') THEN
    CREATE ROLE docuaction_owner NOLOGIN;
  END IF;
END $$;
```

`NOLOGIN` is the point. The role owns objects; nothing authenticates as it.

### Step 1b — grant the owner schema rights (found in rehearsal, 2026-08-24)

```sql
GRANT USAGE, CREATE ON SCHEMA public TO docuaction_owner;
```

**Without this, Step 2 fails with `permission denied for schema public`.**
PostgreSQL requires a prospective owner to hold CREATE on the containing
schema before objects can be assigned to it. This grants no reachable
capability — the role cannot authenticate — but the transfer cannot proceed
without it, and the failure message names the schema rather than the missing
grant, which is not obvious at 2am.

### Step 1c — the runtime role, and why `<app_role>` is not `pgadmin`

This runbook's `<app_role>` presumed a non-owning application role existed. As
of 2026-08-24 **it did not**: DEV and PROD both connect as `pgadmin`, which owns
every table. That is why the immutability control was inert — see the measured
result in Step 3 below.

```sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docuaction_app') THEN
    CREATE ROLE docuaction_app LOGIN PASSWORD '<from key vault>';
  END IF;
END $$;
GRANT CONNECT ON DATABASE <db> TO docuaction_app;
GRANT USAGE, CREATE ON SCHEMA public TO docuaction_app;
```

**`docuaction_app` MUST NOT be granted membership in `docuaction_owner`.** A
member of the owning role inherits its rights, which would restore exactly the
capability this runbook removes.

**Partial ownership is deliberate.** `docuaction_app` owns the ordinary
application tables and `docuaction_owner` owns only the four Area-1 tables. The
application performs DDL at runtime — `app/main.py` startup runs
`Base.metadata.create_all()` plus roughly 25
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements against `users`,
`audit_logs`, `documents`, `tefca_reg_entities` and `tefca_import_history`, and
`bulletin_store.py` creates its own tables. `ALTER TABLE` is not a grantable
privilege in PostgreSQL; only an owner may do it. A model where the runtime role
owns nothing would therefore break startup. Verified by repository-wide search:
**no runtime path performs DDL against any Area-1 table**, so the boundary holds
where it matters.

### Step 1d — migrations must name the runtime role

```
DB_APP_ROLE=docuaction_app alembic upgrade head
```

`20260828_area1_grants` derives its privilege target from `DB_APP_ROLE`, falling
back to `current_user`. Run by an administrative identity that owns the tables,
the fallback revokes from the owner — which changes nothing — and the revision
reports success. The revision now **refuses to run** in that situation rather
than enforcing nothing silently. Set the variable explicitly; do not rely on the
fallback.

## Step 2 — transfer ownership and re-grant, in ONE transaction

```sql
BEGIN;

ALTER TABLE rce_source_intakes        OWNER TO docuaction_owner;
ALTER TABLE rce_source_records        OWNER TO docuaction_owner;
ALTER TABLE rce_ingestion_runs        OWNER TO docuaction_owner;
ALTER TABLE rce_rule_execution_history OWNER TO docuaction_owner;

-- Runtime rights the application genuinely needs.
GRANT SELECT ON rce_source_intakes, rce_source_records,
                rce_ingestion_runs, rce_rule_execution_history
  TO <app_role>;

-- Intake must still be able to write a NEW delivery.
GRANT INSERT ON rce_source_intakes, rce_source_records,
                rce_ingestion_runs, rce_rule_execution_history
  TO <app_role>;

-- The ONLY columns promotion may update. Not the row.
GRANT UPDATE (promotion_status, canonical_entity_id)
  ON rce_source_records TO <app_role>;
GRANT UPDATE (status, error) ON rce_source_intakes TO <app_role>;

-- Deliberately NOT granted: UPDATE on raw_line, parsed, record_sha256,
-- line_number; DELETE; TRUNCATE; REFERENCES.
```

**Do not commit yet.** Run Step 3 inside this transaction.

## Rehearsal result — 2026-08-24, restored copy of DEV

This procedure was executed end to end against a PITR-restored throwaway copy of
`docuaction-db-dev`, not reasoned about. What it established:

**Before the ownership transfer, with the migration run as the owning admin
role, the control was inert.** The ACL looked correct —
`{pgadmin=arxt/pgadmin}`, column-level UPDATE narrowed to exactly
`promotion_status` and `canonical_entity_id` — and `TRUNCATE` was genuinely
refused. But `UPDATE rce_source_records SET raw_line` and `DELETE` both
succeeded, because `pgadmin` owned the tables.

**After this runbook, connected as `docuaction_app`, 14 of 14 checks passed:**

| Operation | Result |
| --- | --- |
| Area-1 SELECT | allowed |
| Authorized ingestion INSERT (intake + record) | allowed |
| `UPDATE raw_line` | **InsufficientPrivilegeError** |
| `DELETE` | **InsufficientPrivilegeError** |
| `TRUNCATE` | **InsufficientPrivilegeError** |
| `UPDATE rce_source_intakes.sha256` | **InsufficientPrivilegeError** |
| `UPDATE promotion_status` / `canonical_entity_id` | allowed |
| `ALTER TABLE` / `DROP TABLE` | **InsufficientPrivilegeError** |
| `SET ROLE docuaction_owner` | **InsufficientPrivilegeError** |
| `ALTER TABLE ... OWNER TO docuaction_app` | **InsufficientPrivilegeError** |
| Self-`GRANT UPDATE` | returns without error but **grants nothing** — ACL unchanged, `has_table_privilege` still false, UPDATE still refused |
| Ordinary-table write and `ALTER` | allowed (startup DDL survives) |

The self-GRANT line is worth knowing before someone tests it and misreads the
result: PostgreSQL answers a GRANT from a grantor with nothing grantable with
success and a warning, not an error. Measure the resulting privilege, not the
statement's return.

**Audit remained intact.** A workflow-column UPDATE produced no
`area1_mutation_log` entry (no false tampering signal), while UPDATE and DELETE
performed by an identity that still holds the rights were both recorded. The
design is prevention for the application and auditability for everyone else.

**The FK-lock case the runbook warns about did not bite**, because ownership
moved to `docuaction_owner`: the referential check runs as the referenced
table's owner, which kept its privileges, so ingestion INSERT still works with
no extra grant.

---

## Step 3 — the validation that catches the real failure

```sql
-- 3a. The application must not be able to alter delivered content.
--     EXPECT: ERROR permission denied
UPDATE rce_source_records SET raw_line = 'TAMPER-TEST' WHERE line_number = 2;

-- 3b. DELETE must be refused.  EXPECT: ERROR permission denied
DELETE FROM rce_source_records WHERE line_number = 2;

-- 3c. FOREIGN-KEY LOCK CHECK — THE STEP THAT MATTERS.
--     A referential-integrity check acquires FOR KEY SHARE on the referenced
--     row and runs AS THE OWNER OF THE REFERENCED TABLE. When ownership moved
--     during development, an ordinary INSERT into a child table began failing,
--     because the application role had lost that implicit privilege. This is
--     the failure mode; it is silent until a user hits it.
INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                                 record_status, transformation_version)
VALUES (gen_random_uuid(),
        (SELECT id FROM rce_source_intakes LIMIT 1),
        (SELECT id FROM rce_source_records LIMIT 1),
        'CLEAN', 'runbook-validation');
-- EXPECT: INSERT 0 1.  If this errors, the grant set is wrong.

DELETE FROM rce_curated_records WHERE transformation_version = 'runbook-validation';

-- 3d. Promotion's narrow update must still work.
UPDATE rce_source_records SET promotion_status = promotion_status
 WHERE line_number = 2;
-- EXPECT: UPDATE 1
```

**If 3a or 3b succeeds, or 3c or 3d fails: `ROLLBACK;` and stop.**

```sql
COMMIT;   -- only when 3a and 3b refused, and 3c and 3d succeeded
```

## Step 4 — post-transfer verification

```sql
SELECT tablename, tableowner FROM pg_tables
 WHERE tablename LIKE 'rce_source%';        -- expect docuaction_owner

SELECT md5(string_agg(raw_line, chr(10) ORDER BY line_number))
  FROM rce_source_records;                   -- expect the pre-change digest
```

Then, from the application: start it, confirm it reads Area 1, run the
reconciliation suite (expect **18/18**), and run one promotion end to end.

## Rollback

```sql
BEGIN;
ALTER TABLE rce_source_intakes OWNER TO <app_role>;
ALTER TABLE rce_source_records OWNER TO <app_role>;
ALTER TABLE rce_ingestion_runs OWNER TO <app_role>;
ALTER TABLE rce_rule_execution_history OWNER TO <app_role>;
COMMIT;
```

Ownership reverts immediately; no data is touched at any point in this runbook.

## What this does not do

It does not protect `tefca_dimension_evidence` or `review_decision_events`.
Those are append-only by design and by database check constraint, not by
ownership. Extending ownership transfer to them is a separate decision and is
**not** recommended without analysis: promotion and QA both write to them at
runtime.
