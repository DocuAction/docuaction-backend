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
3. `alembic current` equals head (`20260828_area1_grants`).
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
