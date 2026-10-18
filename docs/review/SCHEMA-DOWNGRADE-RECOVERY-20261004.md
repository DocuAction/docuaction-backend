# Schema-downgrade recovery — the three 2026-10-04 migrations

**Status: operator guidance, rehearsed on a disposable cluster only.** Nothing
here was run against DEV, PROD or any shared database. A disposable success
does not establish that a production rollback is safe.

| | |
|---|---|
| Backend revision inspected | `4aae7b09618107b749c8773bb2405d8d2a3ade38` (frontend reviewed alongside: `123544d1d7859c48d0b8735e8c4473f4aceed148`) |
| Rehearsal software | PostgreSQL 18.3 (new `initdb` cluster, 127.0.0.1:5547), Alembic 1.14.1, SQLAlchemy 2.0.35, asyncpg 0.30.0, Python 3.13.11 |
| Rehearsal script | `scripts/schema_downgrade_recovery_rehearsal.py` (synthetic data, disposable-cluster guard) |
| Result | 18 of 18 exercises PASS (section 7) |

## 1. Inventory, read from the migration code and confirmed in the catalog

Chain (each `down_revision` read from the file, and applied in this order):

`20261003_preflight_shadow` → `20261004_preflight_exec_held` → `20261004_stage_event_preflight` → `20261004_recheck_jobs` (head)

| Revision | Change | Downgrade behaviour |
|---|---|---|
| `20261004_preflight_exec_held` | Drops and recreates CHECK `ck_rce_preflight_finding_execution` on `rce_preflight_finding.execution`. Old: `'done','unavailable','insufficient'`. New: adds lowercase `'held'`. No table, column, index, FK or grant change. | Recreates the old CHECK. **No guard.** PostgreSQL validates existing rows, so one `'held'` row fails it with a raw `CheckViolationError`. |
| `20261004_stage_event_preflight` | Drops and recreates CHECK `ck_rce_stage_event_stage` on `rce_delivery_stage_events.stage`. Old: 14 stages. New: adds uppercase `'PREFLIGHT'` (between `PARSING` and `QUALITY`). Nothing else. | Same shape, **no guard**: one `'PREFLIGHT'` row fails it raw. |
| `20261004_recheck_jobs` | Creates `rce_recheck_job` (PK `id`; FK `intake_id` → `rce_source_intakes.id` ON DELETE RESTRICT; unique `uq_rce_recheck_job_idempotency`; CHECKs `ck_rce_recheck_job_trigger`, `ck_rce_recheck_job_state`; indexes `idx_rce_recheck_job_state`, `idx_rce_recheck_job_intake`) and `rce_recheck_item` (PK `id`; FK `job_id` → `rce_recheck_job.id` ON DELETE RESTRICT; unique `uq_rce_recheck_item_job_entity`; CHECK `ck_rce_recheck_item_state`; index `idx_rce_recheck_item_job_state`). Grants `SELECT, INSERT, UPDATE` on both to the role named by `DB_APP_ROLE`; refuses to run if that variable is unset. | **Named refusal**: `RecheckJobsPreconditionError: rce_recheck_job holds N row(s); downgrade refused.` whenever any job row exists, whatever its state. Otherwise drops both tables. |

Owner assumption: objects are created by the role in `DB_MIGRATION_ROLE`
(`docuaction_owner`); `alembic/env.py` sets it in the connection startup packet.

### Protection on the four tables — what kind, and who is bound by it

Checked against `pg_trigger`, `pg_rules`, `pg_event_trigger` and
`has_table_privilege` after applying the **whole** chain, not only these files.

| Table | Runtime role (`docuaction_app`) | Kind of protection |
|---|---|---|
| `rce_preflight_finding` | SELECT, INSERT. No UPDATE, DELETE, TRUNCATE | Append-only **by grant** (`20261003_preflight_shadow`) |
| `rce_delivery_stage_events` | SELECT, INSERT, UPDATE. No DELETE, TRUNCATE | Mutable bookkeeping (an attempt is opened then closed), no runtime DELETE (`20260917_delivery_traceability`) |
| `rce_recheck_job`, `rce_recheck_item` | SELECT, INSERT, UPDATE. No DELETE, TRUNCATE | Mutable bookkeeping, no runtime DELETE (`20261004_recheck_jobs`) |

- **No trigger, rule or event trigger exists on any of the four tables.** The
  only triggers in the chain are `trg_review_event_sod` on
  `review_decision_events` (function `review_event_enforce_sod`,
  `20260825_qa_decision_events`) and `trg_area1_record_mutation`,
  `trg_area1_record_delete` on `rce_source_records` and
  `trg_area1_intake_mutation` on `rce_source_intakes` (function
  `area1_log_mutation`, `20260826_area1_mutation_audit`).
- Those Area 1 triggers matter indirectly: the parents of these rows (intakes,
  source records) log every UPDATE/DELETE. Recovery must not touch the parents.
- The only inbound foreign key is `rce_recheck_item.job_id` → `rce_recheck_job`.
- **The migration owner is not bound by any of this.** As table owner it can
  UPDATE and DELETE these rows. The append-only property is a statement about
  the application role, not about an operator holding the owner role. That is
  why deleting is a governance decision, not a technical obstacle.

## 2. Transaction behaviour — measured, not assumed

`env.py` does not set `transaction_per_migration`; it wraps
`context.run_migrations()` in one `context.begin_transaction()`. None of the
four migrations uses an autocommit block, `COMMIT`, or `CONCURRENTLY`.

| Test | Measured result |
|---|---|
| **A.** One command, `alembic downgrade 20261003_preflight_shadow`, three revisions, the last one fails | **All-or-nothing.** `alembic_version` stayed `20261004_recheck_jobs`, the recheck tables still existed, both CHECKs were still the widened ones, row counts unchanged. The two earlier steps that would have succeeded alone were rolled back with the failure. |
| **B.** Three separate commands, one revision each, the third fails | **Each successful invocation committed.** After the failure the database sat at `20261004_preflight_exec_held`: recheck tables dropped, stage CHECK narrowed, execution CHECK still widened, the `'held'` row intact. Consistent, but two revisions down. |

Consequence: a failed downgrade leaves the database in a consistent state at a
revision that `alembic_version` reports truthfully. Which revision depends on
how the command was issued. Always read it; never assume it. Never use
`alembic stamp` to make the recorded revision match an expectation.

## 3. Two different rollbacks

### A. Code rollback, schema left upgraded (the default)

Redeploy the previous application image; do not touch the schema. The three
migrations only widen two CHECKs and add two tables, and older code does not
write the new values or reference the new tables.

Evidence and its limits are in section 6. Two costs are certain:

- Reverting the code restores the old QA-approval defect (an eligible non-B1
  entity cannot complete independent QA, because the previous `qa_gate.py`
  refuses on `verification_status == 'in_review'` instead of on a genuine open
  finding).
- Recheck jobs and preflight stages stop being processed or displayed; their
  rows remain.

### B. Schema downgrade (exceptional)

Only when an operator has a specific reason the upgraded schema cannot stay.
For a shared environment, when detection (section 4) finds any dependent row:

> **STOP the downgrade. Preserve the evidence. Keep the upgraded schema.**
> Consider an approved feature disable, a roll-forward fix, or a separately
> proven code rollback (path A).

Do not rewrite `'held'` or `'PREFLIGHT'` to some older value to satisfy the
constraint. That changes what a record says happened.

Deleting rows so a downgrade can proceed removes audit history. An archive
(section 5) is a copy for reference. It is not permission to delete, and it is
not a substitute for a tested database backup. That decision belongs to the
program owner and records authority, not to this runbook.

## 4. Detection and inspection SQL

Read-only. Schema is `public` throughout.

```sql
-- 4.1 Where is the chain?
SELECT version_num FROM public.alembic_version;

-- 4.2 Blockers. Any non-zero row blocks the corresponding downgrade.
--     If the recheck tables do not exist (already downgraded past them), run
--     only the first two branches.
SELECT 'preflight_held' AS blocker, count(*) AS rows
FROM public.rce_preflight_finding
WHERE execution = 'held'
UNION ALL
SELECT 'stage_preflight', count(*)
FROM public.rce_delivery_stage_events
WHERE stage = 'PREFLIGHT'
UNION ALL
SELECT 'recheck_jobs', count(*)
FROM public.rce_recheck_job
UNION ALL
SELECT 'recheck_items', count(*)
FROM public.rce_recheck_item;

-- 4.3 Do the recheck tables exist?
SELECT to_regclass('public.rce_recheck_job')  IS NOT NULL AS recheck_job_exists,
       to_regclass('public.rce_recheck_item') IS NOT NULL AS recheck_item_exists;

-- 4.4 The actual CHECK definitions (compare with section 1).
SELECT conrelid::regclass AS table_name, conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conname IN ('ck_rce_preflight_finding_execution', 'ck_rce_stage_event_stage');

-- 4.5 Foreign keys pointing AT the four tables (what would block a delete or drop).
SELECT conrelid::regclass AS referencing_table, conname,
       confrelid::regclass AS referenced_table, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE contype = 'f'
  AND confrelid IN ('public.rce_preflight_finding'::regclass,
                    'public.rce_delivery_stage_events'::regclass,
                    to_regclass('public.rce_recheck_job'),
                    to_regclass('public.rce_recheck_item'));

-- 4.6 Triggers, rules and event triggers (expected: none on these four tables).
SELECT c.relname AS table_name, t.tgname, p.proname AS function_name, pg_get_triggerdef(t.oid)
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_proc  p ON p.oid = t.tgfoid
WHERE NOT t.tgisinternal
  AND c.relname IN ('rce_preflight_finding', 'rce_delivery_stage_events',
                    'rce_recheck_job', 'rce_recheck_item');
SELECT tablename, rulename FROM pg_rules
WHERE schemaname = 'public'
  AND tablename IN ('rce_preflight_finding', 'rce_delivery_stage_events',
                    'rce_recheck_job', 'rce_recheck_item');
SELECT evtname, evtevent FROM pg_event_trigger;

-- 4.7 Owner and runtime privileges.
SELECT tablename, tableowner FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('rce_preflight_finding', 'rce_delivery_stage_events',
                    'rce_recheck_job', 'rce_recheck_item');
SELECT t AS table_name, p AS privilege,
       has_table_privilege('docuaction_app', 'public.' || t, p) AS runtime_role_has_it
FROM unnest(ARRAY['rce_preflight_finding', 'rce_delivery_stage_events',
                  'rce_recheck_job', 'rce_recheck_item']) AS t,
     unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS p
ORDER BY 1, 2;

-- 4.8 The blocking rows themselves, for the decision record.
SELECT id, run_id, source_record_id, code, execution, disposition, created_at
FROM public.rce_preflight_finding WHERE execution = 'held' ORDER BY created_at;
SELECT id, job_id, intake_id, stage, attempt, status, started_at, completed_at
FROM public.rce_delivery_stage_events WHERE stage = 'PREFLIGHT' ORDER BY started_at;
SELECT id, intake_id, trigger_kind, source_id, state, requested_by, approved_by, created_at
FROM public.rce_recheck_job ORDER BY created_at;
SELECT job_id, state, count(*) FROM public.rce_recheck_item GROUP BY 1, 2 ORDER BY 1, 2;
```

No other blocker was found for these three revisions. Going further down than
`20261003_preflight_shadow` is a different question: that revision's own
downgrade refuses while any of its seven evidence tables holds a row.

## 5. Archive SQL (a reference copy; not a backup, not a licence to delete)

Run in `psql` as a role holding `docuaction_owner`. The archive schema name is
generated from the UTC clock, and `CREATE SCHEMA` is issued without
`IF NOT EXISTS`, so an existing archive is never reused or overwritten: a
collision fails the whole transaction (measured: `DuplicateSchema`).

`CREATE TABLE ... AS` copies **data and column types only**. The copy has none
of the source table's constraints, indexes, foreign keys, privileges, ownership
policy or default values. It is evidence of content, not a restorable table.

Prerequisite found in rehearsal: the role needs `CREATE` on the database.
Check with `SELECT has_database_privilege('docuaction_owner', current_database(), 'CREATE');`.

```sql
\set ON_ERROR_STOP on
SET ROLE docuaction_owner;

SELECT 'recovery_archive_20261004_'
       || to_char(now() AT TIME ZONE 'utc', 'YYYYMMDD"t"HH24MISS"z"') AS archive_schema \gset
\echo archive schema is :archive_schema

BEGIN;
CREATE SCHEMA :"archive_schema";
REVOKE ALL ON SCHEMA :"archive_schema" FROM PUBLIC;

CREATE TABLE :"archive_schema".rce_preflight_finding_held AS
  SELECT * FROM public.rce_preflight_finding WHERE execution = 'held';
CREATE TABLE :"archive_schema".rce_delivery_stage_events_preflight AS
  SELECT * FROM public.rce_delivery_stage_events WHERE stage = 'PREFLIGHT';
CREATE TABLE :"archive_schema".rce_recheck_job AS
  SELECT * FROM public.rce_recheck_job;
CREATE TABLE :"archive_schema".rce_recheck_item AS
  SELECT * FROM public.rce_recheck_item;
COMMIT;

-- Row counts: source and archive side by side. Every pair must match.
SELECT 'rce_preflight_finding_held' AS set_name,
       (SELECT count(*) FROM public.rce_preflight_finding WHERE execution = 'held') AS source_rows,
       (SELECT count(*) FROM :"archive_schema".rce_preflight_finding_held) AS archive_rows
UNION ALL SELECT 'rce_delivery_stage_events_preflight',
       (SELECT count(*) FROM public.rce_delivery_stage_events WHERE stage = 'PREFLIGHT'),
       (SELECT count(*) FROM :"archive_schema".rce_delivery_stage_events_preflight)
UNION ALL SELECT 'rce_recheck_job',
       (SELECT count(*) FROM public.rce_recheck_job),
       (SELECT count(*) FROM :"archive_schema".rce_recheck_job)
UNION ALL SELECT 'rce_recheck_item',
       (SELECT count(*) FROM public.rce_recheck_item),
       (SELECT count(*) FROM :"archive_schema".rce_recheck_item);

-- Row content, both directions. Every count must be 0.
SELECT 'finding: in source, not in archive' AS check_name, count(*) AS rows FROM (
  SELECT * FROM public.rce_preflight_finding WHERE execution = 'held'
  EXCEPT SELECT * FROM :"archive_schema".rce_preflight_finding_held) d
UNION ALL SELECT 'finding: in archive, not in source', count(*) FROM (
  SELECT * FROM :"archive_schema".rce_preflight_finding_held
  EXCEPT SELECT * FROM public.rce_preflight_finding WHERE execution = 'held') d
UNION ALL SELECT 'stage event: in source, not in archive', count(*) FROM (
  SELECT * FROM public.rce_delivery_stage_events WHERE stage = 'PREFLIGHT'
  EXCEPT SELECT * FROM :"archive_schema".rce_delivery_stage_events_preflight) d
UNION ALL SELECT 'stage event: in archive, not in source', count(*) FROM (
  SELECT * FROM :"archive_schema".rce_delivery_stage_events_preflight
  EXCEPT SELECT * FROM public.rce_delivery_stage_events WHERE stage = 'PREFLIGHT') d
UNION ALL SELECT 'recheck job: in source, not in archive', count(*) FROM (
  SELECT * FROM public.rce_recheck_job EXCEPT SELECT * FROM :"archive_schema".rce_recheck_job) d
UNION ALL SELECT 'recheck job: in archive, not in source', count(*) FROM (
  SELECT * FROM :"archive_schema".rce_recheck_job EXCEPT SELECT * FROM public.rce_recheck_job) d
UNION ALL SELECT 'recheck item: in source, not in archive', count(*) FROM (
  SELECT * FROM public.rce_recheck_item EXCEPT SELECT * FROM :"archive_schema".rce_recheck_item) d
UNION ALL SELECT 'recheck item: in archive, not in source', count(*) FROM (
  SELECT * FROM :"archive_schema".rce_recheck_item EXCEPT SELECT * FROM public.rce_recheck_item) d;

-- Access: the runtime role must not reach the archive.
SELECT has_schema_privilege('docuaction_app', :'archive_schema', 'USAGE') AS runtime_role_can_use;
RESET ROLE;
```

The archive holds the same record content as the source tables, so restrict
it the same way: no grant to `PUBLIC` or the runtime role (measured: `false`).
If writers are still active, the source can change after the copy; reconcile
again immediately before any further step.

## 6. Code rollback — what is and is not proven

**The earlier proof does not cover this candidate.** Ledger R8-3 / R9-4 ran
prior backend `a6bf241bed0536533bacddd2adb3712d906762f7` against a schema at
`20261003_preflight_shadow`, built by candidate `23af09f`. That predates all
three migrations. It exercised login, delivery list and dashboard reads, a
report row read, and one full delivery upload. No `'held'` finding, no
`'PREFLIGHT'` stage event and no recheck row existed.

Run for this document, on the disposable cluster, as the runtime role
`docuaction_app`, schema at `20261004_recheck_jobs` holding one `'held'`
finding, one `'PREFLIGHT'` stage event, one recheck job and item. Older code
has none of the three flags, so they are off by construction.

| Exercise | `a6bf241` (main) | `ea92ea5` (PR #110 head) |
|---|---|---|
| ORM load of stage events including the `'PREFLIGHT'` row | PASS | PASS |
| Prior `stage_events.timeline()` returns the `'PREFLIGHT'` entry without error | PASS | PASS |
| Prior code writes a new stage event under the widened CHECK | PASS (`CURATION`) | PASS (`QUALITY`) |
| ORM load of findings including the `'held'` row | not applicable (no model at this revision) | PASS (its own `EXECUTION` tuple lacks `'held'`; nothing validated against it on read) |
| Recheck tables | invisible to the code | invisible to the code |
| Real server start + `/health` | PASS, 200 after 13 s | NOT RUN |
| Authenticated HTTP routes, a full delivery, report generation, the frontend | NOT RUN | NOT RUN |

The `a6bf241` server again logged `DB schema setup FAILED after retries`
(`arc_stale_marks.intake_id` could not find table `rce_source_intakes`), the
same startup error the ledger recorded. It is a model-registration fault inside
that revision; it is still not attributed by a run against that revision's own
native schema.

What this supports: older code can read rows carrying the new values and can
still write stage events. What it does not support: any general statement that
rolling back the code is safe. The old user interface's handling of a
`PREFLIGHT` timeline entry, and every authenticated route, are untested.

## 7. Rehearsal results (disposable cluster, synthetic rows)

| # | Exercise | Result |
|---|---|---|
| 0 | Full chain applied; reference CHECK definitions captured at `20261003_preflight_shadow` | PASS |
| S1 | Recheck downgrade with one job present: `RecheckJobsPreconditionError: rce_recheck_job holds 1 row(s); downgrade refused.`; state unchanged | PASS |
| S2 | Stage CHECK downgrade with one `'PREFLIGHT'` row: `asyncpg.exceptions.CheckViolationError: check constraint "ck_rce_stage_event_stage" of relation "rce_delivery_stage_events" is violated by some row`; revision stayed `20261004_stage_event_preflight` | PASS |
| S3 | Execution CHECK downgrade with one `'held'` row: `CheckViolationError ... "ck_rce_preflight_finding_execution" ... is violated by some row`; revision stayed `20261004_preflight_exec_held` | PASS |
| T-A | Single command across three revisions, last fails: everything rolled back | PASS |
| T-B | One revision per command, third fails: first two committed | PASS |
| G1 | No trigger, rule or event trigger on the four tables | PASS |
| G2 | Owner `docuaction_owner`; runtime privileges as in section 1 | PASS |
| G3 | Runtime role DELETE on each table and UPDATE on a finding: `InsufficientPrivilege` | PASS |
| R1 | Detection: 1 / 1 / 1 / 1 | PASS |
| R2 | Downgrade refused with all blockers present; state unchanged | PASS |
| R2b | `pg_dump -Fc`, `pg_restore --list` (805 entries), then a real `pg_restore` into a **separate** database; blocker counts match 1 / 1 / 1 / 1 | PASS |
| R3 | Archive: counts 1 = 1 for all four sets, `EXCEPT` 0 in both directions, second `CREATE SCHEMA` refused, runtime role has no access | PASS |
| R4 | Deleted exactly the four seeded rows as owner, items before jobs, one row each, one transaction | PASS |
| R5 | Detection: 0 / 0 / 0 / 0 | PASS |
| R6 | Downgrade to `20261003_preflight_shadow`: both CHECKs identical to the captured originals, recheck tables gone | PASS |
| R7 | Archive fingerprints unchanged; control rows (a `'done'` finding, a `'PARSING'` stage event, their intake, source record and job) byte-identical | PASS |
| R8 | Re-upgrade to head: CHECKs widened, recheck tables recreated empty with SELECT/INSERT/UPDATE and no DELETE/TRUNCATE, archive intact | PASS |

Not run: any exercise on DEV or PROD; a restore of a shared-environment
backup; concurrent writers during archive or downgrade; a downgrade below
`20261003_preflight_shadow`.

R4 deleted synthetic fixtures to exercise the mechanics. It is not a
recommended procedure for records in a shared environment.

## 8. If a downgrade has already failed

1. Stop retrying. Stop application writers through the authorised maintenance
   process (no ad-hoc session kills).
2. Keep the full error output with the time and the exact command.
3. Run 4.1 to 4.4. Record the revision, both CHECK definitions, whether the
   recheck tables exist, and the blocker counts.
4. Decide whether the failed command rolled back completely:
   - a multi-revision command: expect the revision it started from (test A);
   - single-revision commands: expect the last revision that succeeded (test B).
   The recorded revision, the CHECK definitions and the table existence must
   agree with section 1 for that revision.
5. If they agree, the database is consistent. Leave it there until the operator
   decision is made. Running the application at an intermediate revision is a
   separate question: candidate code expects head.
6. If they disagree, escalate and use a validated recovery plan. Do not
   `alembic stamp`, do not hand-edit constraints, do not disable grants or
   triggers to force progress.

Backups: `pg_restore --list` succeeding shows the archive file is readable; it
does not show the backup restores. Restore into a **separate** database and
compare before relying on it. Do not run `pg_restore --clean` against a live or
shared database as a routine step. The only restore rehearsed here is R2b, of a
disposable database; restoring a real environment backup is untested.

## 9. Feature settings

`app/core/config.py`, all default `False`:

| Setting | Read by |
|---|---|
| `ENABLE_PREFLIGHT_ENFORCEMENT` | `delivery_runner.py` (adds the PREFLIGHT stage; the only writer of `'PREFLIGHT'` stage events), `iqvia_import.py` (refuses a blocked reference snapshot) |
| `ENABLE_CONTROLLED_RECHECKS` | `preflight_shadow_routes.py` (every recheck route refuses when off), `rechecks.py` (reported state) |
| `ENFORCE_COMPLETE_EXCLUSION_SCREENING` | `prior_risk.py`, used by `arc_pipeline.py` (withholds `verified` when screening is incomplete). Creates no blocking row. |

At this revision no application path inserts `execution = 'held'` into
`rce_preflight_finding`: `reference_preflight.py` uses the value only inside
snapshot metadata. The CHECK still accepts it from any writer.

Flags being off does not prove the tables are clean. Tests, an earlier
activation or another writer can have created rows. Section 4.2 decides.

## 10. Decisions that remain with the operator

- Whether a schema downgrade is ever wanted, given path A exists.
- Whether evidence rows may be removed at all, by whom, under which records
  authority. Default: no.
- Where an archive lives, who may read it, how long it is kept.
- Confirming `docuaction_owner` holds `CREATE` on the database before relying
  on section 5.
- Rehearsing a restore of the real environment backup into a separate database.
- A maintenance procedure for stopping writers.

P1–P6 remain unapproved, `SEED_RULES_V4` inactive, the "1,298" interpretation
unresolved. None is affected by this document.
